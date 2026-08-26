"""KATS RAG API.

    POST /tickets        upsert one ticket -> chunk -> embed -> store
    POST /tickets/bulk   the same, for a queue backfill
    GET  /tickets        list what is in the store
    GET  /tickets/{id}   the structured record + its rendered summary
    POST /search         hybrid retrieval, no model call
    POST /chat           hybrid retrieval + local LLM, grounded and cited
    GET  /health         what is up, and which models are answering

Run it with ../start.sh rag, or directly:

    uvicorn app.main:app --host 127.0.0.1 --port 8001 --app-dir rag/backend
"""

from __future__ import annotations

import json
import logging
import os
import time
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from app import db, embeddings, llm
from app.config import get_settings
from app.schemas import ChatRequest, SearchRequest, TicketDoc

logging.basicConfig(
    level=os.environ.get("KATS_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)-7s %(name)s  %(message)s",
)
log = logging.getLogger("kats.api")

SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "db", "init.sql")


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    log.info("database  %s", settings.database_url.split("@")[-1])
    db.apply_schema(os.path.abspath(SCHEMA_PATH))

    embeddings.probe()
    db.check_embedding_dim(embeddings.STATE["dim"] or settings.embed_dim)
    llm.probe()
    llm.warm()          # background thread; the API serves immediately

    if embeddings.STATE["mode"] != "ollama":
        log.warning("running with the hash fallback embedder — retrieval is lexical only")
    if not llm.STATE["reachable"]:
        log.warning("no local LLM — /search works, /chat will return an error")

    yield


app = FastAPI(title="KATS RAG API", version="1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in get_settings().cors_origins.split(",")],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

router = APIRouter()


# -----------------------------------------------------------------------------
@router.get("/health")
def health() -> dict:
    settings = get_settings()
    try:
        store = db.counts()
        database_up = True
    except Exception as exc:  # noqa: BLE001
        store = {"error": str(exc)}
        database_up = False

    return {
        "status": "ok" if database_up else "degraded",
        "database": database_up,
        "counts": store,
        "llm_model": llm.STATE.get("model"),
        "ollama": llm.STATE.get("reachable", False),
        "ollama_detail": llm.STATE.get("detail", ""),
        "embed_model": embeddings.STATE.get("model"),
        "embed_mode": embeddings.STATE.get("mode"),
        "embed_detail": embeddings.STATE.get("detail", ""),
        "embed_dim": embeddings.STATE.get("dim"),
        "ollama_url": settings.ollama_url,
    }


# -----------------------------------------------------------------------------
def _ingest(doc: TicketDoc) -> dict:
    payload = doc.model_dump()
    payload["facets"] = doc.facets.model_dump()

    chunks = [c.model_dump() for c in doc.chunks]
    if chunks:
        vectors = embeddings.embed([c["content"] for c in chunks])
        for chunk, vector in zip(chunks, vectors):
            chunk["embedding"] = vector
            chunk["embed_model"] = embeddings.STATE.get("model")

    result = db.upsert_ticket(payload, chunks)
    result["embed_mode"] = embeddings.STATE.get("mode")
    return result


@router.post("/tickets")
def ingest_ticket(doc: TicketDoc) -> dict:
    try:
        return _ingest(doc)
    except Exception as exc:  # noqa: BLE001
        log.exception("ingest failed for %s", doc.ticket_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/tickets/bulk")
def ingest_bulk(docs: list[TicketDoc]) -> dict:
    ok, failed = [], []
    for doc in docs:
        try:
            ok.append(_ingest(doc))
        except Exception as exc:  # noqa: BLE001
            log.warning("bulk ingest failed for %s: %s", doc.ticket_id, exc)
            failed.append({"ticket_id": doc.ticket_id, "error": str(exc)})
    return {"ingested": len(ok), "failed": len(failed), "errors": failed}


@router.get("/tickets")
def get_tickets(
    customer_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
) -> dict:
    return {"tickets": db.list_tickets(customer_id, limit)}


@router.get("/tickets/{ticket_id}")
def get_one_ticket(ticket_id: str) -> dict:
    row = db.get_ticket(ticket_id)
    if not row:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return row


# -----------------------------------------------------------------------------
def _retrieve(request: SearchRequest) -> list[dict]:
    question = (request.question or "").strip()
    if len(question) < 3:
        return []
    vector = embeddings.embed_one(question)
    rows = db.hybrid_search(
        query_text=question,
        query_vec=vector,
        customer_id=request.customer_id,
        doc_types=request.doc_types,
        facets=request.facets or {},
        top_k=request.top_k,
    )
    for row in rows:
        row["score"] = float(row.get("score") or 0)
        row["semantic_score"] = float(row.get("semantic_score") or 0)
        row["lexical_score"] = float(row.get("lexical_score") or 0)
        # Why it matched, in the same spirit as KB.search()'s reasons list —
        # a retrieval hit nobody can explain is a retrieval hit nobody trusts.
        why = []
        if row.get("semantic_rank"):
            why.append(f"semantic #{int(row['semantic_rank'])}")
        if row.get("lexical_rank"):
            why.append(f"keyword #{int(row['lexical_rank'])}")
        row["why"] = " · ".join(why)
    return rows


@router.post("/search")
def search(request: SearchRequest) -> dict:
    rows = _retrieve(request)
    return {
        "question": request.question,
        "customer_id": request.customer_id,
        "count": len(rows),
        "embed_mode": embeddings.STATE.get("mode"),
        "results": rows,
    }


@router.post("/chat/stream")
def chat_stream(request: ChatRequest) -> StreamingResponse:
    """The same answer as /chat, delivered as it is generated.

    NDJSON, one JSON object per line:

        {"type":"evidence","evidence":[...]}   once, before any token
        {"type":"token","t":"..."}             many
        {"type":"done","model":"...","ms":N}   once
        {"type":"error","detail":"..."}        instead of done, on failure

    Evidence goes first on purpose: retrieval finishes in under a second, so
    the reader can see which tickets are being read while the model is still
    writing about them.
    """
    started = time.time()
    evidence = _retrieve(request)

    def generate():
        yield json.dumps({"type": "evidence", "evidence": evidence}) + "\n"

        pieces: list[str] = []
        model = llm.STATE.get("model") or ""
        for kind, value in llm.stream_chat(request.question, evidence, request.history):
            if kind == "token":
                pieces.append(value)
                yield json.dumps({"type": "token", "t": value}) + "\n"
            elif kind == "error":
                yield json.dumps({"type": "error", "detail": value}) + "\n"
                return
            else:
                model = value

        elapsed_ms = int((time.time() - started) * 1000)
        answer = "".join(pieces)
        db.log_chat(
            request.customer_id, request.question, answer, model,
            [{"ticket_id": e["ticket_id"], "section": e["section"], "score": e["score"]}
             for e in evidence],
            elapsed_ms,
        )
        yield json.dumps({
            "type": "done", "model": model, "ms": elapsed_ms,
            "embed_mode": embeddings.STATE.get("mode"),
        }) + "\n"

    return StreamingResponse(
        generate(),
        media_type="application/x-ndjson",
        # Without this, a proxy sitting in front will happily buffer the whole
        # response and undo the entire point of streaming.
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/chat")
def chat(request: ChatRequest) -> dict:
    started = time.time()
    evidence = _retrieve(request)

    result = llm.chat(request.question, evidence, request.history)
    elapsed_ms = int((time.time() - started) * 1000)

    if result["error"]:
        raise HTTPException(
            status_code=503,
            detail=(
                f"Local model unavailable: {result['error']}. "
                f"Check `ollama serve` and `ollama pull {result['model']}`."
            ),
        )

    db.log_chat(
        request.customer_id,
        request.question,
        result["text"],
        result["model"],
        [
            {"ticket_id": e["ticket_id"], "section": e["section"], "score": e["score"]}
            for e in evidence
        ],
        elapsed_ms,
    )

    return {
        "answer": result["text"],
        "model": result["model"],
        "ms": elapsed_ms,
        "embed_mode": embeddings.STATE.get("mode"),
        "evidence": evidence,
    }


app.include_router(router)
