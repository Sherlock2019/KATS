"""IndexingService — keeps rag_chunks in step with the relational record.

The whole design goal is that re-indexing is cheap enough to do on every
ticket write. Regenerating chunks is pure CPU and takes milliseconds;
embedding is a network round trip per batch and takes seconds. So: always
regenerate, compare hashes, embed only what moved.

A ticket edited twenty times during an incident therefore costs twenty
cheap rebuilds and a handful of embeddings, not twenty full re-embeds.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import RagChunk, SupportTicket
from app.services.embeddings.service import get_embedding_service
from app.services.kt.quality import KnowledgeQualityService
from app.services.rag.knowledge_builder import KnowledgeBuilderService

log = logging.getLogger("kt.indexing")


@dataclass
class IndexResult:
    ticket_number: str
    built: int = 0
    unchanged: int = 0
    embedded: int = 0
    deleted: int = 0
    embed_mode: str = ""

    def as_dict(self) -> dict:
        return {
            "ticket_number": self.ticket_number,
            "chunks_built": self.built,
            "chunks_unchanged": self.unchanged,
            "chunks_embedded": self.embedded,
            "chunks_deleted": self.deleted,
            "embed_mode": self.embed_mode,
        }


class IndexingService:
    @staticmethod
    def reindex_ticket(db: Session, ticket: SupportTicket, force: bool = False) -> IndexResult:
        embeddings = get_embedding_service()
        result = IndexResult(ticket_number=ticket.ticket_number)

        # The quality score is an input to retrieval ranking and is stamped
        # onto every chunk, so it has to be current before chunks are written.
        score, _ = KnowledgeQualityService.evaluate(ticket)
        ticket.knowledge_quality_score = round(score, 2)

        built = KnowledgeBuilderService.build(ticket)
        result.built = len(built)

        existing = {
            (c.chunk_type, c.title): c
            for c in db.scalars(select(RagChunk).where(RagChunk.ticket_id == ticket.id))
        }

        keep: set[tuple[str, str]] = set()
        to_embed: list[tuple[RagChunk, str]] = []

        for chunk in built:
            key = (chunk.chunk_type, chunk.title)
            keep.add(key)
            digest = chunk.content_hash
            row = existing.get(key)

            if row is None:
                row = RagChunk(
                    ticket_id=ticket.id, chunk_type=chunk.chunk_type, title=chunk.title,
                    content=chunk.content, content_hash=digest, metadata_=chunk.metadata,
                    quality_score=round(chunk.quality_score, 2),
                    confidence_score=round(chunk.confidence_score, 2),
                )
                db.add(row)
                to_embed.append((row, chunk.content))
                continue

            # Metadata and scores are refreshed unconditionally — they change
            # when the ticket around the chunk changes even if the text does
            # not, and they are filter/ranking inputs, not embedded content.
            row.metadata_ = chunk.metadata
            row.quality_score = round(chunk.quality_score, 2)
            row.confidence_score = round(chunk.confidence_score, 2)

            if row.content_hash == digest and row.embedding is not None and not force:
                result.unchanged += 1
                continue

            row.content = chunk.content
            row.content_hash = digest
            to_embed.append((row, chunk.content))

        # Chunks whose source disappeared — a hypothesis deleted, a resolution
        # cleared. Leaving them behind means retrieving a fix that no longer
        # exists on the ticket.
        for key, row in existing.items():
            if key not in keep:
                db.delete(row)
                result.deleted += 1

        if to_embed:
            vectors = embeddings.embed_documents([text for _, text in to_embed])
            for (row, _), vector in zip(to_embed, vectors):
                row.embedding = vector
                row.embedding_model = embeddings.model
            result.embedded = len(to_embed)

        # Read after embedding, not before: the service probes lazily, so
        # before the first call its mode is still 'unknown'.
        result.embed_mode = embeddings.mode

        db.flush()
        log.info(
            "reindexed %s: %d built, %d unchanged, %d embedded, %d deleted",
            ticket.ticket_number, result.built, result.unchanged,
            result.embedded, result.deleted,
        )
        return result

    @staticmethod
    def reindex_all(db: Session, force: bool = False) -> list[IndexResult]:
        results = []
        for ticket in db.scalars(select(SupportTicket).order_by(SupportTicket.created_at)):
            results.append(IndexingService.reindex_ticket(db, ticket, force=force))
        db.commit()
        return results
