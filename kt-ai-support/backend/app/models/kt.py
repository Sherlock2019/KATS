"""The KT specification: IS / IS NOT, the distinctions between them, and
what changed.

These three tables are the reason this system can retrieve better than
semantic similarity alone. "Cluster A fails, cluster B does not, and the
only difference is the secret version" is a machine-comparable statement;
the same sentence in a notes field is not.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class KTSpecification(Base):
    """One entry, on one side, of one dimension.

    Multiple rows per cell is the normal case — "WHAT IS" is usually a list,
    not a sentence — which is exactly why this is a table rather than eight
    text columns on the ticket.
    """

    __tablename__ = "kt_specifications"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.uuid_generate_v4()
    )
    ticket_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("support_tickets.id", ondelete="CASCADE")
    )

    dimension: Mapped[str] = mapped_column(Text)     # WHAT | WHERE | WHEN | EXTENT
    side: Mapped[str] = mapped_column(Text)          # IS | IS_NOT

    value: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # When both sides of a dimension carry the same structured_key, the pair
    # of values is a distinction the system can derive rather than wait for
    # someone to type. See KTAnalysisService.derive_distinctions().
    structured_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    structured_value: Mapped[str | None] = mapped_column(Text, nullable=True)

    evidence_reference: Mapped[str | None] = mapped_column(Text, nullable=True)

    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    ticket = relationship("SupportTicket", back_populates="specifications")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<KTSpec {self.dimension} {self.side} {self.value!r}>"


class KTDistinction(Base):
    """What is different between an IS and its comparable IS NOT.

    KT's central move: a cause must explain why the broken case fails AND
    why the healthy twin does not. The distinction is where that explanation
    has to land.
    """

    __tablename__ = "kt_distinctions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.uuid_generate_v4()
    )
    ticket_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("support_tickets.id", ondelete="CASCADE")
    )

    dimension: Mapped[str | None] = mapped_column(Text, nullable=True)

    is_reference: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("kt_specifications.id", ondelete="SET NULL"), nullable=True
    )
    is_not_reference: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("kt_specifications.id", ondelete="SET NULL"), nullable=True
    )

    distinction: Mapped[str] = mapped_column(Text)

    attribute_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_not_value: Mapped[str | None] = mapped_column(Text, nullable=True)

    importance_score: Mapped[Decimal | None] = mapped_column(Numeric(3, 2), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    ticket = relationship("SupportTicket", back_populates="distinctions")


class KTChange(Base):
    """Something that changed in or around the distinction.

    Most faults are a change. Linking a change to the distinction it sits
    on is what turns "a deploy happened" into "the deploy happened on the
    side that broke, and not on the side that did not".
    """

    __tablename__ = "kt_changes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.uuid_generate_v4()
    )
    ticket_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("support_tickets.id", ondelete="CASCADE")
    )

    change_type: Mapped[str] = mapped_column(Text)
    component: Mapped[str | None] = mapped_column(Text, nullable=True)

    description: Mapped[str] = mapped_column(Text)

    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)

    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    related_distinction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("kt_distinctions.id", ondelete="SET NULL"), nullable=True
    )

    change_source: Mapped[str | None] = mapped_column(Text, nullable=True)
    change_id: Mapped[str | None] = mapped_column(Text, nullable=True)

    suspected_relevance: Mapped[Decimal | None] = mapped_column(Numeric(3, 2), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    ticket = relationship("SupportTicket", back_populates="changes")
