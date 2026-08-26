"""Retrieval and AI-assistant payloads."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import ChunkType


class ChunkRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    ticket_id: uuid.UUID
    chunk_type: ChunkType
    title: str | None = None
    content: str
    metadata: dict = Field(default_factory=dict)
    quality_score: float = 0.0
    confidence_score: float = 0.0
    embedded: bool = False
    updated_at: datetime | None = None


class ScoreBreakdown(BaseModel):
    """§31 — every term that produced the ranking, kept separately.

    A single fused number is undebuggable: when a wrong case ranks first you
    need to know whether it won on vector, keyword, metadata or KT match.
    """

    semantic: float = 0.0
    keyword: float = 0.0
    metadata: float = 0.0
    error_signature: float = 0.0
    knowledge_quality: float = 0.0
    root_cause_confidence: float = 0.0
    kt_match: float = 0.0
    final: float = 0.0


class SearchResult(BaseModel):
    chunk_id: uuid.UUID
    ticket_id: uuid.UUID
    ticket_number: str
    title: str | None = None
    chunk_type: ChunkType
    content: str

    product: str | None = None
    component: str | None = None
    environment: str | None = None
    error_code: str | None = None
    severity: str | None = None
    status: str | None = None
    root_cause_status: str | None = None

    scores: ScoreBreakdown
    why: list[str] = Field(default_factory=list)


class SearchRequest(BaseModel):
    query: str = Field(min_length=1)

    # When set, the query is built from that ticket's KT specification rather
    # than from free text — which is what makes IS/IS NOT matching possible.
    ticket_id: uuid.UUID | None = None

    filters: dict = Field(default_factory=dict)
    chunk_types: list[ChunkType] | None = None
    top_k: int = Field(default=8, ge=1, le=50)

    # Exclude the ticket being worked, or it retrieves itself and reports a
    # perfect match with its own text.
    exclude_ticket_ids: list[uuid.UUID] = Field(default_factory=list)

    # Per-request weight override, for the inspector and the eval suite.
    weights: dict[str, float] | None = None


class SearchResponse(BaseModel):
    query: str
    detected_metadata: dict = Field(default_factory=dict)
    applied_filters: dict = Field(default_factory=dict)
    weights: dict[str, float] = Field(default_factory=dict)
    embedding_model: str | None = None
    count: int
    results: list[SearchResult] = Field(default_factory=list)
    latency_ms: int = 0


class SimilarCase(BaseModel):
    """§32 — a historical case, with why it matched and what it turned out to be."""

    ticket_id: uuid.UUID
    ticket_number: str
    title: str
    similarity: float
    match_reasons: list[str] = Field(default_factory=list)

    root_cause: str | None = None
    root_cause_status: str | None = None
    resolution_summary: str | None = None
    workaround: str | None = None
    rejected_causes: list[str] = Field(default_factory=list)
    successful_tests: list[str] = Field(default_factory=list)

    knowledge_quality_score: float = 0.0


# -----------------------------------------------------------------------------
# /api/ai/diagnose — §24
# -----------------------------------------------------------------------------
class LikelyCause(BaseModel):
    cause: str
    confidence: float = Field(ge=0, le=1)
    reason: str = ""
    supporting_evidence: list[str] = Field(default_factory=list)
    contradicting_evidence: list[str] = Field(default_factory=list)
    source_tickets: list[str] = Field(default_factory=list)


class RecommendedTest(BaseModel):
    test: str
    purpose: str = ""
    expected_if_true: str = ""
    expected_if_false: str = ""
    risk: str = "low"
    # Why this test and not another. §28 ranks by uncertainty removed per
    # unit of cost and risk, not by which cause looks likeliest.
    discriminates_between: list[str] = Field(default_factory=list)
    diagnostic_value: float | None = None


class DiagnoseRequest(BaseModel):
    ticket_id: uuid.UUID
    question: str = "What should I test next?"
    top_k: int = Field(default=8, ge=1, le=25)


class DiagnoseResponse(BaseModel):
    problem_understanding: str = ""
    missing_information: list[str] = Field(default_factory=list)
    similar_cases: list[SimilarCase] = Field(default_factory=list)
    likely_causes: list[LikelyCause] = Field(default_factory=list)
    recommended_tests: list[RecommendedTest] = Field(default_factory=list)
    possible_workaround: str | None = None

    # Null unless THIS ticket's own evidence confirms it. A historical fix is
    # evidence, never proof about the incident in front of you.
    confirmed_root_cause: str | None = None

    model: str | None = None
    latency_ms: int = 0
    grounded_in: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class NextActionResponse(BaseModel):
    recommended_action: str
    rationale: str = ""
    candidates: list[RecommendedTest] = Field(default_factory=list)
    model: str | None = None
    latency_ms: int = 0


class NextQuestionResponse(BaseModel):
    """§45 — the missing KT answer that would remove the most uncertainty."""

    question: str
    dimension: str | None = None
    side: str | None = None
    why_it_matters: str = ""
    current_gaps: list[str] = Field(default_factory=list)
    model: str | None = None
    latency_ms: int = 0


class InspectorRow(BaseModel):
    """One row of the §31 debugging table."""

    rank: int
    ticket_number: str
    chunk_type: str
    vector: float
    keyword: float
    metadata: float
    kt: float
    quality: float
    final: float
    title: str | None = None
