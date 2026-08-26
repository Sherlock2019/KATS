"""Retrieval and the RAG inspector. §33, §31."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import RagChunk, RagQuery, SupportTicket
from app.schemas import SearchRequest
from app.services.embeddings.service import get_embedding_service
from app.services.rag.indexing import IndexingService
from app.services.rag.retrieval import RetrievalService

router = APIRouter(prefix="/api/rag", tags=["rag"])


def _result_dict(candidate) -> dict:
    return {
        "chunk_id": candidate.chunk_id,
        "ticket_id": candidate.ticket_id,
        "ticket_number": candidate.ticket_number,
        "title": candidate.title,
        "chunk_type": candidate.chunk_type,
        "content": candidate.content,
        "product": candidate.product,
        "component": candidate.component,
        "environment": candidate.environment,
        "error_code": candidate.error_code,
        "severity": candidate.severity,
        "status": candidate.status,
        "root_cause_status": candidate.root_cause_status,
        "scores": {
            "semantic": round(candidate.semantic, 4),
            "keyword": round(candidate.keyword, 4),
            "metadata": round(candidate.metadata_score, 4),
            "error_signature": round(candidate.signature, 4),
            "knowledge_quality": round(candidate.quality, 4),
            "root_cause_confidence": round(candidate.confidence, 4),
            "kt_match": round(candidate.kt, 4),
            "final": round(candidate.final, 6),
        },
        "why": candidate.why,
    }


@router.post("/search")
def search(payload: SearchRequest, db: Session = Depends(get_db)) -> dict:
    if payload.ticket_id:
        ticket = db.get(SupportTicket, payload.ticket_id)
        if ticket is None:
            raise HTTPException(404, "Ticket not found")
        # Built from the ticket's structure, which is what makes the KT
        # IS/IS-NOT term possible — free text has no specification to compare.
        ctx = RetrievalService.context_from_ticket(ticket, payload.query)
    else:
        ctx = RetrievalService.context_from_text(payload.query, payload.filters)

    ctx.exclude |= set(payload.exclude_ticket_ids)

    found = RetrievalService.search(
        db, ctx,
        top_k=payload.top_k,
        chunk_types=[str(c) for c in payload.chunk_types] if payload.chunk_types else None,
        filters=payload.filters,
        weight_override=payload.weights,
    )

    results = [_result_dict(c) for c in found["results"]]

    # Logged for the inspector. Retrieval quality is not debuggable after the
    # fact unless the score breakdown was kept at the time.
    db.add(RagQuery(
        ticket_id=payload.ticket_id,
        query_text=payload.query,
        detected_metadata=found["detected_metadata"],
        applied_filters=found["applied_filters"],
        weights=found["weights"],
        results=[
            {"ticket_number": r["ticket_number"], "chunk_type": r["chunk_type"],
             "scores": r["scores"]}
            for r in results
        ],
        embedding_model=found["embedding_model"],
        latency_ms=found["latency_ms"],
    ))
    db.commit()

    return {
        "query": payload.query,
        "detected_metadata": found["detected_metadata"],
        "applied_filters": found["applied_filters"],
        "weights": found["weights"],
        "embedding_model": found["embedding_model"],
        "embed_mode": found["embed_mode"],
        "candidates_considered": found["candidates_considered"],
        "count": len(results),
        "results": results,
        "latency_ms": found["latency_ms"],
    }


@router.post("/inspect")
def inspect(payload: SearchRequest, db: Session = Depends(get_db)) -> dict:
    """§31 — the same search, shaped as the debugging table.

    The point of this endpoint is answering "why did the wrong case win?".
    A fused final score cannot answer it; the per-term columns can.
    """
    found = search(payload, db)
    rows = [
        {
            "rank": index,
            "ticket_number": r["ticket_number"],
            "chunk_type": r["chunk_type"],
            "title": r["title"],
            "vector": r["scores"]["semantic"],
            "keyword": r["scores"]["keyword"],
            "metadata": r["scores"]["metadata"],
            "signature": r["scores"]["error_signature"],
            "kt": r["scores"]["kt_match"],
            "quality": r["scores"]["knowledge_quality"],
            "final": r["scores"]["final"],
            "why": r["why"],
        }
        for index, r in enumerate(found["results"], start=1)
    ]

    embeddings = get_embedding_service()
    return {
        "query": found["query"],
        "detected_metadata": found["detected_metadata"],
        "applied_filters": found["applied_filters"],
        "weights": found["weights"],
        "embedding": {
            "model": embeddings.model,
            "mode": embeddings.mode,
            "dimensions": embeddings.configured_dim,
            "semantic": embeddings.status()["semantic"],
            "detail": embeddings.detail,
        },
        "candidates_considered": found["candidates_considered"],
        "latency_ms": found["latency_ms"],
        "rows": rows,
    }


@router.get("/queries")
def recent_queries(limit: int = Query(default=25, ge=1, le=200),
                   db: Session = Depends(get_db)) -> dict:
    rows = db.scalars(
        select(RagQuery).order_by(RagQuery.created_at.desc()).limit(limit)
    ).all()
    return {
        "queries": [
            {
                "id": r.id, "query_text": r.query_text,
                "detected_metadata": r.detected_metadata,
                "weights": r.weights, "results": r.results,
                "embedding_model": r.embedding_model,
                "latency_ms": r.latency_ms, "created_at": r.created_at,
            }
            for r in rows
        ]
    }


@router.get("/chunks/{ticket_id}")
def ticket_chunks(ticket_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    rows = db.scalars(
        select(RagChunk).where(RagChunk.ticket_id == ticket_id).order_by(RagChunk.chunk_type)
    ).all()
    return {
        "count": len(rows),
        "chunks": [
            {
                "id": c.id, "chunk_type": c.chunk_type, "title": c.title,
                "content": c.content, "metadata": c.metadata_,
                "quality_score": float(c.quality_score or 0),
                "confidence_score": float(c.confidence_score or 0),
                "embedded": c.embedding is not None,
                "embedding_model": c.embedding_model,
                "content_hash": c.content_hash[:12],
                "updated_at": c.updated_at,
            }
            for c in rows
        ],
    }


@router.post("/reindex-all")
def reindex_all(force: bool = False, db: Session = Depends(get_db)) -> dict:
    results = IndexingService.reindex_all(db, force=force)
    return {
        "tickets": len(results),
        "chunks_built": sum(r.built for r in results),
        "chunks_embedded": sum(r.embedded for r in results),
        "chunks_unchanged": sum(r.unchanged for r in results),
        "chunks_deleted": sum(r.deleted for r in results),
        "embed_mode": results[0].embed_mode if results else None,
    }
