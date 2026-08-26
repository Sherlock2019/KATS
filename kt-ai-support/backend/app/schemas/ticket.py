"""Ticket request/response models."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import RootCauseStatus, TicketStatus


class TicketBase(BaseModel):
    title: str = Field(min_length=1, max_length=500)

    status: TicketStatus = TicketStatus.NEW
    priority: str | None = Field(default=None, pattern=r"^P[1-4]$")
    severity: str | None = Field(default=None, pattern=r"^S[1-4]$")

    customer_name: str | None = None
    organization: str | None = None

    product: str | None = None
    product_version: str | None = None
    service: str | None = None
    component: str | None = None
    subcomponent: str | None = None

    environment: str | None = None
    environment_type: str | None = None

    operating_system: str | None = None
    cloud_provider: str | None = None
    region: str | None = None
    datacenter: str | None = None
    cluster: str | None = None
    node: str | None = None

    business_impact: str | None = None
    technical_impact: str | None = None
    users_affected: int | None = Field(default=None, ge=0)
    percentage_affected: Decimal | None = Field(default=None, ge=0, le=100)

    problem_summary: str | None = None
    expected_behavior: str | None = None
    actual_behavior: str | None = None

    first_seen_at: datetime | None = None
    last_known_good_at: datetime | None = None

    error_code: str | None = None
    error_message: str | None = None

    workaround: str | None = None
    resolution_summary: str | None = None
    prevention_summary: str | None = None

    created_by: str | None = None
    assigned_to: str | None = None

    metadata: dict = Field(default_factory=dict)


class TicketCreate(TicketBase):
    """§44 — only a title is required.

    Refusing to open a ticket until every field is filled produces tickets
    nobody opens, and the missing fields are precisely what the assistant is
    supposed to help chase.
    """


class TicketUpdate(BaseModel):
    """Everything optional. PATCH semantics: unset means unchanged.

    Deliberately excludes root_cause_status and knowledge_quality_score —
    both are computed from the evidence, not asserted by a client.
    """

    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    status: TicketStatus | None = None
    priority: str | None = Field(default=None, pattern=r"^P[1-4]$")
    severity: str | None = Field(default=None, pattern=r"^S[1-4]$")

    customer_name: str | None = None
    organization: str | None = None
    product: str | None = None
    product_version: str | None = None
    service: str | None = None
    component: str | None = None
    subcomponent: str | None = None
    environment: str | None = None
    environment_type: str | None = None
    operating_system: str | None = None
    cloud_provider: str | None = None
    region: str | None = None
    datacenter: str | None = None
    cluster: str | None = None
    node: str | None = None
    business_impact: str | None = None
    technical_impact: str | None = None
    users_affected: int | None = None
    percentage_affected: Decimal | None = None
    problem_summary: str | None = None
    expected_behavior: str | None = None
    actual_behavior: str | None = None
    first_seen_at: datetime | None = None
    last_known_good_at: datetime | None = None
    error_code: str | None = None
    error_message: str | None = None
    workaround: str | None = None
    resolution_summary: str | None = None
    prevention_summary: str | None = None
    assigned_to: str | None = None
    resolved_at: datetime | None = None
    metadata: dict | None = None


class TicketRead(TicketBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    ticket_number: str

    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None = None

    error_signature_norm: str | None = None

    root_cause_status: RootCauseStatus = RootCauseStatus.UNKNOWN
    root_cause: str | None = None
    root_cause_confidence: Decimal | None = None

    knowledge_quality_score: Decimal = Decimal("0")


class TicketSummary(BaseModel):
    """The list row. Everything a queue view needs, nothing it does not."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    ticket_number: str
    title: str
    status: str
    priority: str | None = None
    severity: str | None = None
    product: str | None = None
    component: str | None = None
    environment: str | None = None
    error_code: str | None = None
    root_cause_status: str
    knowledge_quality_score: Decimal
    created_at: datetime
    resolved_at: datetime | None = None


class TimelineCreate(BaseModel):
    event_type: str
    occurred_at: datetime
    description: str
    component: str | None = None
    source: str | None = None


class TimelineRead(TimelineCreate):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    ticket_id: uuid.UUID
    created_at: datetime


class CompletenessSection(BaseModel):
    key: str
    label: str
    percent: int
    present: int
    expected: int
    # What to ask next to move this section. §44/§45 — the point of measuring
    # completeness is to generate the question, not to display a number.
    missing_prompt: str | None = None


class Completeness(BaseModel):
    """§44 — progressive data collection, per section and overall."""

    overall: int
    sections: list[CompletenessSection]
    knowledge_quality_score: float


class TicketDetail(TicketRead):
    """The whole case. What §43's screen and the chunk builder both read."""

    specifications: list = Field(default_factory=list)
    distinctions: list = Field(default_factory=list)
    changes: list = Field(default_factory=list)
    hypotheses: list = Field(default_factory=list)
    evidence: list = Field(default_factory=list)
    tests: list = Field(default_factory=list)
    actions: list = Field(default_factory=list)
    root_causes: list = Field(default_factory=list)
    timeline: list = Field(default_factory=list)
    completeness: Completeness | None = None
