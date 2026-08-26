"""KT specification, distinctions and changes."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import ChangeType, Dimension, Side


class SpecificationCreate(BaseModel):
    dimension: Dimension
    side: Side
    value: str = Field(min_length=1)
    description: str | None = None

    # The optional structured form. When an IS and an IS NOT in the same
    # dimension share a structured_key, their differing values ARE a
    # distinction — KTAnalysisService derives it instead of waiting for
    # someone to type it out.
    structured_key: str | None = None
    structured_value: str | None = None

    evidence_reference: str | None = None
    sort_order: int = 0


class SpecificationRead(SpecificationCreate):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    ticket_id: uuid.UUID
    created_at: datetime


class KTGridCell(BaseModel):
    """One cell of the §5 table: every entry on one side of one dimension."""

    entries: list[SpecificationRead] = Field(default_factory=list)


class KTGridRow(BaseModel):
    dimension: Dimension
    is_: KTGridCell = Field(default_factory=KTGridCell, alias="is")
    is_not: KTGridCell = Field(default_factory=KTGridCell)

    model_config = ConfigDict(populate_by_name=True)


class KTGrid(BaseModel):
    """The four-row IS / IS NOT table, ready to render.

    Returned in dimension order (WHAT, WHERE, WHEN, EXTENT) even when a row
    is empty, because an empty IS NOT is the most informative gap on the
    screen — it is the question the assistant will ask next.
    """

    rows: list[KTGridRow]
    filled_cells: int
    total_cells: int = 8


class DistinctionCreate(BaseModel):
    distinction: str = Field(min_length=1)
    dimension: Dimension | None = None

    is_reference: uuid.UUID | None = None
    is_not_reference: uuid.UUID | None = None

    attribute_name: str | None = None
    is_value: str | None = None
    is_not_value: str | None = None

    importance_score: Decimal | None = Field(default=None, ge=0, le=1)


class DistinctionRead(DistinctionCreate):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    ticket_id: uuid.UUID
    created_at: datetime
    # True when derived from a matching structured_key pair rather than typed.
    derived: bool = False


class ChangeCreate(BaseModel):
    change_type: ChangeType = ChangeType.UNKNOWN
    description: str = Field(min_length=1)

    component: str | None = None
    old_value: str | None = None
    new_value: str | None = None
    occurred_at: datetime | None = None

    related_distinction_id: uuid.UUID | None = None

    change_source: str | None = None
    change_id: str | None = None
    suspected_relevance: Decimal | None = Field(default=None, ge=0, le=1)


class ChangeRead(ChangeCreate):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    ticket_id: uuid.UUID
    created_at: datetime
