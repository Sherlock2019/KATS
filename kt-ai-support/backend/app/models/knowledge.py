"""The retrieval layer: chunks and the query log behind the RAG inspector.

Note what these models are NOT: they are not the knowledge. Every chunk is
derived from the relational tables and can be thrown away and rebuilt. If
this table and `support_tickets` ever disagree, the ticket is right.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.config import get_settings
from app.db import Base

_EMBEDDING_DIM = get_settings().embedding_dim


class RagChunk(Base):
    """One retrievable unit of meaning from one ticket.

    `content_hash` is what keeps re-indexing cheap. On every ticket change
    the builder regenerates all chunks, compares hashes, and re-embeds only
    what moved. Embedding is the slow step; hashing is free.
    """

    __tablename__ = "rag_chunks"
    __table_args__ = (
        UniqueConstraint("ticket_id", "chunk_type", "title", name="rag_chunks_ticket_id_chunk_type_title_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.uuid_generate_v4()
    )
    ticket_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("support_tickets.id", ondelete="CASCADE")
    )

    chunk_type: Mapped[str] = mapped_column(Text)

    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(Text)

    embedding: Mapped[list[float] | None] = mapped_column(Vector(_EMBEDDING_DIM), nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(Text, nullable=True)

    # §16 — the filter surface. Denormalised from the ticket on purpose:
    # retrieval filters must not need a join, and the chunk should stay
    # interpretable if it is ever exported on its own.
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, server_default="{}")

    quality_score: Mapped[Decimal] = mapped_column(Numeric(3, 2), default=0, server_default="0")
    confidence_score: Mapped[Decimal] = mapped_column(Numeric(3, 2), default=0, server_default="0")

    # Provenance. `object_type` lets a chunk belong to a KB article rather
    # than a ticket; `source_trust` is how much to trust where it came from,
    # kept separate from quality_score, which measures the content itself.
    object_type: Mapped[str] = mapped_column(
        Text, default="incident", server_default="incident")
    object_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    source_type: Mapped[str] = mapped_column(Text, default="new_kt", server_default="new_kt")
    source_trust: Mapped[Decimal] = mapped_column(
        Numeric(3, 2), default=1, server_default="1.00")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    ticket = relationship("SupportTicket", back_populates="chunks")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<RagChunk {self.chunk_type} {self.title!r}>"


class RagQuery(Base):
    """What was asked, what came back, and every score that produced the order.

    This is what /rag-inspector reads. Retrieval quality is not debuggable
    from the answer alone — when a wrong case ranks first you need to see
    whether it won on vector, keyword, metadata or KT match.
    """

    __tablename__ = "rag_queries"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.uuid_generate_v4()
    )
    ticket_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("support_tickets.id", ondelete="SET NULL"), nullable=True
    )

    query_text: Mapped[str] = mapped_column(Text)
    detected_metadata: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    applied_filters: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    weights: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")

    results: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")

    embedding_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    llm_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)

    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
