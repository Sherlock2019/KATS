"""Hypotheses, evidence, tests, actions and root causes.

These five tables exist separately because collapsing any two of them
destroys information the assistant needs:

    hypothesis vs root cause     a guess is not a conclusion
    evidence FOR vs AGAINST      an observation that kills a candidate is
                                 worth more than one that supports it
    test vs result               a test with no recorded failing branch
                                 cannot discriminate between anything
    action vs test               changing the system to fix it and changing
                                 it to learn from it are different intents
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class KTHypothesis(Base):
    """A possible cause.

    REJECTED rows are never deleted. A refuted candidate is a reduction of
    the search space that someone already paid for with a test, and telling
    the next engineer "we ruled this out, here is how" is often more useful
    than the answer itself.
    """

    __tablename__ = "kt_hypotheses"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.uuid_generate_v4()
    )
    ticket_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("support_tickets.id", ondelete="CASCADE")
    )

    cause: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(Text, default="PROPOSED", server_default="PROPOSED")

    probability_score: Mapped[Decimal | None] = mapped_column(Numeric(3, 2), nullable=True)
    confidence_score: Mapped[Decimal | None] = mapped_column(Numeric(3, 2), nullable=True)

    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 'assistant' when the model proposed it, so its own suggestions are
    # never later mistaken for something a human observed.
    proposed_by: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    ticket = relationship("SupportTicket", back_populates="hypotheses")
    evidence = relationship("TicketEvidence", back_populates="hypothesis")
    tests = relationship("DiagnosticTest", back_populates="hypothesis")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Hypothesis {self.status} {self.cause!r}>"


class TicketEvidence(Base):
    __tablename__ = "ticket_evidence"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.uuid_generate_v4()
    )
    ticket_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("support_tickets.id", ondelete="CASCADE")
    )
    hypothesis_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("kt_hypotheses.id", ondelete="SET NULL"), nullable=True
    )

    evidence_type: Mapped[str] = mapped_column(Text)
    direction: Mapped[str] = mapped_column(Text, default="NEUTRAL", server_default="NEUTRAL")

    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    content: Mapped[str] = mapped_column(Text)

    source: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_reference: Mapped[str | None] = mapped_column(Text, nullable=True)

    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reliability_score: Mapped[Decimal | None] = mapped_column(Numeric(3, 2), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    ticket = relationship("SupportTicket", back_populates="evidence")
    hypothesis = relationship("KTHypothesis", back_populates="evidence")


class DiagnosticTest(Base):
    """A controlled experiment against one hypothesis.

    Both expected branches are recorded before it runs. A test whose failing
    branch was never written down cannot discriminate — whatever happens,
    someone will read it as confirmation.
    """

    __tablename__ = "diagnostic_tests"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.uuid_generate_v4()
    )
    ticket_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("support_tickets.id", ondelete="CASCADE")
    )
    hypothesis_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("kt_hypotheses.id", ondelete="SET NULL"), nullable=True
    )

    test_name: Mapped[str] = mapped_column(Text)
    objective: Mapped[str | None] = mapped_column(Text, nullable=True)
    procedure: Mapped[str | None] = mapped_column(Text, nullable=True)

    expected_result_if_true: Mapped[str | None] = mapped_column(Text, nullable=True)
    expected_result_if_false: Mapped[str | None] = mapped_column(Text, nullable=True)

    actual_result: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_status: Mapped[str] = mapped_column(Text, default="NOT_RUN", server_default="NOT_RUN")

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    performed_by: Mapped[str | None] = mapped_column(Text, nullable=True)

    risk_level: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Feeds the next-best-action ranking: a reversible 20% test beats an
    # irreversible 90% one, every time.
    reversible: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    rollback_procedure: Mapped[str | None] = mapped_column(Text, nullable=True)

    estimated_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)

    evidence_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ticket_evidence.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    ticket = relationship("SupportTicket", back_populates="tests")
    hypothesis = relationship("KTHypothesis", back_populates="tests")


class TicketAction(Base):
    __tablename__ = "ticket_actions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.uuid_generate_v4()
    )
    ticket_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("support_tickets.id", ondelete="CASCADE")
    )

    action_type: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text)
    procedure: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(Text, default="PLANNED", server_default="PLANNED")

    performed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Free-form so any metric fits. §29: before/after is what lets the
    # assistant later verify that a fix actually fixed anything.
    before_metric: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    after_metric: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")

    owner: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    ticket = relationship("SupportTicket", back_populates="actions")


class RootCause(Base):
    """The conclusion, at the confidence it has actually earned.

    `mechanism` and `trigger` are separate deliberately: "the token was not
    persisted" is the mechanism, "a rotation ran at 21:58" is the trigger.
    A fix that addresses only one of them is a recurrence waiting to happen.
    """

    __tablename__ = "root_causes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.uuid_generate_v4()
    )
    ticket_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("support_tickets.id", ondelete="CASCADE")
    )

    cause: Mapped[str] = mapped_column(Text)
    cause_category: Mapped[str | None] = mapped_column(Text, nullable=True)
    component: Mapped[str | None] = mapped_column(Text, nullable=True)

    mechanism: Mapped[str | None] = mapped_column(Text, nullable=True)
    trigger: Mapped[str | None] = mapped_column(Text, nullable=True)

    verification_method: Mapped[str | None] = mapped_column(Text, nullable=True)
    verification_result: Mapped[str | None] = mapped_column(Text, nullable=True)

    confidence: Mapped[str] = mapped_column(Text, default="SUSPECTED", server_default="SUSPECTED")

    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_by: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    ticket = relationship("SupportTicket", back_populates="root_causes")
