"""KT AI Support API.

    uvicorn app.main:app --host 127.0.0.1 --port 8100 --app-dir kt-ai-support/backend

Run migrations first:  python -m migrations.run
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api import ai, problems, rag, tickets
from app.config import get_settings
from app.db import engine
from app.services.embeddings.service import get_embedding_service
from app.services.llm.service import get_llm_service

settings = get_settings()
logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)-7s %(name)-18s %(message)s",
)
log = logging.getLogger("kt.api")


@asynccontextmanager
async def lifespan(_: FastAPI):
    log.info("database  %s", settings.database_url.split("@")[-1])

    embeddings = get_embedding_service()
    embeddings.probe()

    # Refuse to serve on a dimension mismatch rather than writing vectors
    # nobody can compare. Everything else degrades; this one cannot.
    if not embeddings.healthy:
        raise RuntimeError(embeddings.detail)

    if not embeddings.status()["semantic"]:
        log.warning("embeddings: %s", embeddings.detail)

    llm = get_llm_service()
    llm.probe()
    if not llm.reachable:
        log.warning("llm: %s — /api/ai still answers from the database alone",
                    llm.status().get("detail"))

    yield


app = FastAPI(title="KT AI Support", version="1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_list,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(tickets.router)
app.include_router(rag.router)
app.include_router(ai.router)
app.include_router(problems.router)


@app.get("/health")
def health() -> dict:
    embeddings = get_embedding_service()
    llm = get_llm_service()

    counts: dict = {}
    database_up = True
    try:
        with engine.connect() as conn:
            row = conn.execute(text("""
                SELECT
                  (SELECT COUNT(*) FROM support_tickets)                         AS tickets,
                  (SELECT COUNT(*) FROM rag_chunks)                              AS chunks,
                  (SELECT COUNT(*) FROM rag_chunks WHERE embedding IS NOT NULL)  AS embedded,
                  (SELECT COUNT(*) FROM kt_specifications)                       AS kt_specifications,
                  (SELECT COUNT(*) FROM kt_hypotheses)                           AS hypotheses,
                  (SELECT COUNT(*) FROM root_causes WHERE confidence='CONFIRMED') AS confirmed_causes,
                  (SELECT COUNT(*) FROM problem_records)                          AS problems,
                  (SELECT COUNT(*) FROM problem_records WHERE is_emerging)        AS emerging
            """)).one()
            counts = dict(row._mapping)
    except Exception as exc:  # noqa: BLE001
        database_up = False
        counts = {"error": str(exc)}

    return {
        "status": "ok" if database_up and embeddings.healthy else "degraded",
        "database": database_up,
        "counts": counts,
        "embeddings": embeddings.status(),
        "llm": llm.status(),
        "retrieval_weights": settings.weights.normalised(),
    }
