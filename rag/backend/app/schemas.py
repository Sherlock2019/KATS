"""Request/response models.

The ingest shape is whatever `Record.toRagDoc()` in kt_record.js emits — that
function is the contract, this file is its mirror. Extra keys are tolerated so
a browser running an older bundle can still post.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class Facets(BaseModel):
    site: str | None = None
    service_component: str | None = None
    category: str | None = None
    environment: str | None = None
    severity: int | None = None
    blast_radius: str | None = None
    impact_trend: str | None = None
    quality_score: int | None = None


class Chunk(BaseModel):
    section: str
    content: str


class TicketDoc(BaseModel):
    schema_version: str = "1.0"
    ticket_id: str
    customer_id: str
    doc_type: str = "intake"
    opened_at: str | None = None
    status: str = "new"
    title: str | None = None
    facets: Facets = Field(default_factory=Facets)
    error_signature_raw: str | None = None
    error_signature_norm: str | None = None
    fields: dict[str, Any] = Field(default_factory=dict)
    summary: list[dict[str, Any]] = Field(default_factory=list)
    chunks: list[Chunk] = Field(default_factory=list)


class SearchRequest(BaseModel):
    question: str
    customer_id: str | None = None
    doc_types: list[str] = Field(default_factory=lambda: ["intake", "resolution", "kb"])
    facets: dict[str, Any] = Field(default_factory=dict)
    top_k: int = Field(default=8, ge=1, le=25)


class ChatRequest(SearchRequest):
    history: list[dict[str, Any]] = Field(default_factory=list)
