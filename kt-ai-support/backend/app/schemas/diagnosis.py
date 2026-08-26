"""Hypotheses, evidence, tests, actions, root causes."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import (
    ActionStatus,
    ActionType,
    Direction,
    EvidenceType,
    HypothesisStatus,
    RiskLevel,
    RootCauseConfidence,
    TestResult,
)


# -----------------------------------------------------------------------------
# hypotheses
# -----------------------------------------------------------------------------
class HypothesisCreate(BaseModel):
    cause: str = Field(min_length=1)
    description: str | None = None
    status: HypothesisStatus = HypothesisStatus.PROPOSED
    probability_score: Decimal | None = Field(default=None, ge=0, le=1)
    confidence_score: Decimal | None = Field(default=None, ge=0, le=1)
    rank: int | None = None
    reasoning: str | None = None
    proposed_by: str | None = None


class HypothesisUpdate(BaseModel):
    """A rejected hypothesis is updated in place — status and reasoning move,
    the row and its history stay. Nothing here deletes."""

    model_config = ConfigDict(extra="forbid")

    cause: str | None = None
    description: str | None = None
    status: HypothesisStatus | None = None
    probability_score: Decimal | None = Field(default=None, ge=0, le=1)
    confidence_score: Decimal | None = Field(default=None, ge=0, le=1)
    rank: int | None = None
    reasoning: str | None = None


class HypothesisRead(HypothesisCreate):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    ticket_id: uuid.UUID
    created_at: datetime
    updated_at: datetime

    # Counted from ticket_evidence. §27's funnel needs these per hypothesis,
    # and an assistant that cannot see 3-for/5-against will keep pushing a
    # candidate the evidence already killed.
    evidence_for: int = 0
    evidence_against: int = 0
    tests_run: int = 0


# -----------------------------------------------------------------------------
# evidence
# -----------------------------------------------------------------------------
class EvidenceCreate(BaseModel):
    evidence_type: EvidenceType = EvidenceType.OTHER
    direction: Direction = Direction.NEUTRAL
    content: str = Field(min_length=1)

    hypothesis_id: uuid.UUID | None = None
    title: str | None = None
    source: str | None = None
    source_reference: str | None = None
    observed_at: datetime | None = None
    reliability_score: Decimal | None = Field(default=None, ge=0, le=1)


class EvidenceRead(EvidenceCreate):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    ticket_id: uuid.UUID
    created_at: datetime


# -----------------------------------------------------------------------------
# diagnostic tests
# -----------------------------------------------------------------------------
class DiagnosticTestCreate(BaseModel):
    test_name: str = Field(min_length=1)
    hypothesis_id: uuid.UUID | None = None

    objective: str | None = None
    procedure: str | None = None

    # Both branches, before the run. A test with no recorded failing branch
    # cannot discriminate — whatever happens, someone reads it as a confirm.
    expected_result_if_true: str | None = None
    expected_result_if_false: str | None = None

    risk_level: RiskLevel | None = None
    reversible: bool = True
    rollback_procedure: str | None = None
    estimated_minutes: int | None = Field(default=None, ge=0)


class DiagnosticTestUpdate(BaseModel):
    """Recording the outcome."""

    model_config = ConfigDict(extra="forbid")

    actual_result: str | None = None
    result_status: TestResult | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    performed_by: str | None = None
    evidence_id: uuid.UUID | None = None


class DiagnosticTestRead(DiagnosticTestCreate):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    ticket_id: uuid.UUID
    actual_result: str | None = None
    result_status: TestResult = TestResult.NOT_RUN
    started_at: datetime | None = None
    completed_at: datetime | None = None
    performed_by: str | None = None
    evidence_id: uuid.UUID | None = None
    created_at: datetime


# -----------------------------------------------------------------------------
# actions
# -----------------------------------------------------------------------------
class ActionCreate(BaseModel):
    action_type: ActionType
    description: str = Field(min_length=1)
    procedure: str | None = None
    status: ActionStatus = ActionStatus.PLANNED
    performed_at: datetime | None = None
    result: str | None = None
    before_metric: dict = Field(default_factory=dict)
    after_metric: dict = Field(default_factory=dict)
    owner: str | None = None


class ActionRead(ActionCreate):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    ticket_id: uuid.UUID
    created_at: datetime


# -----------------------------------------------------------------------------
# root cause
# -----------------------------------------------------------------------------
class RootCauseCreate(BaseModel):
    cause: str = Field(min_length=1)
    cause_category: str | None = None
    component: str | None = None

    mechanism: str | None = None
    trigger: str | None = None

    verification_method: str | None = None
    verification_result: str | None = None

    # Defaults to SUSPECTED on purpose. Claiming CONFIRMED is checked against
    # the evidence by RootCauseService — a technician's certainty is not a
    # verification, and the retrieval ranking depends on this being honest.
    confidence: RootCauseConfidence = RootCauseConfidence.SUSPECTED
    confirmed_by: str | None = None


class RootCauseRead(RootCauseCreate):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    ticket_id: uuid.UUID
    confirmed_at: datetime | None = None
    created_at: datetime
