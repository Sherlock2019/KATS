"""Ticket CRUD and every sub-resource. §33."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import (
    DiagnosticTest,
    KTChange,
    KTDistinction,
    KTHypothesis,
    KTSpecification,
    RootCause,
    SupportTicket,
    TicketAction,
    TicketEvidence,
    TicketTimeline,
)
from app.schemas import (
    ActionCreate,
    ChangeCreate,
    DiagnosticTestCreate,
    DiagnosticTestUpdate,
    DistinctionCreate,
    EvidenceCreate,
    HypothesisCreate,
    HypothesisUpdate,
    RootCauseCreate,
    SpecificationCreate,
    TicketCreate,
    TicketUpdate,
    TimelineCreate,
)
from app.services.diagnostics.reasoning import DiagnosticReasoningService
from app.services.kt.analysis import KTAnalysisService
from app.services.kt.quality import KnowledgeQualityService
from app.services.rag.indexing import IndexingService
from app.services.ticket_service import TicketService

router = APIRouter(prefix="/api", tags=["tickets"])


def _get(db: Session, ticket_id: uuid.UUID) -> SupportTicket:
    ticket = db.get(SupportTicket, ticket_id)
    if ticket is None:
        raise HTTPException(404, "Ticket not found")
    return ticket


def _serialise(obj) -> dict:
    """ORM row -> plain dict, with `metadata_` renamed back to `metadata`."""
    out = {}
    for column in obj.__table__.columns:
        key = "metadata_" if column.name == "metadata" else column.name
        out[column.name] = getattr(obj, key, None)
    return out


# -----------------------------------------------------------------------------
# tickets
# -----------------------------------------------------------------------------
@router.post("/tickets", status_code=201)
def create_ticket(payload: TicketCreate, db: Session = Depends(get_db)) -> dict:
    data = payload.model_dump()
    data["metadata_"] = data.pop("metadata", {})
    ticket = SupportTicket(**data)
    db.add(ticket)
    index = TicketService.save(db, ticket)
    return {**_serialise(ticket), "_index": index}


@router.get("/tickets")
def list_tickets(
    db: Session = Depends(get_db),
    q: str | None = Query(default=None, description="free text over title and problem"),
    status: str | None = None,
    product: str | None = None,
    component: str | None = None,
    environment_type: str | None = None,
    error_code: str | None = None,
    root_cause_status: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict:
    stmt = select(SupportTicket)
    for column, value in [
        (SupportTicket.status, status),
        (SupportTicket.product, product),
        (SupportTicket.component, component),
        (SupportTicket.environment_type, environment_type),
        (SupportTicket.error_code, error_code),
        (SupportTicket.root_cause_status, root_cause_status),
    ]:
        if value:
            stmt = stmt.where(column == value)

    if q:
        pattern = f"%{q}%"
        stmt = stmt.where(
            or_(
                SupportTicket.title.ilike(pattern),
                SupportTicket.problem_summary.ilike(pattern),
                SupportTicket.error_message.ilike(pattern),
                SupportTicket.ticket_number.ilike(pattern),
            )
        )

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(SupportTicket.created_at.desc()).limit(limit).offset(offset)
    ).all()

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "tickets": [
            {
                "id": t.id, "ticket_number": t.ticket_number, "title": t.title,
                "status": t.status, "priority": t.priority, "severity": t.severity,
                "product": t.product, "component": t.component,
                "environment": t.environment, "error_code": t.error_code,
                "root_cause_status": t.root_cause_status,
                "knowledge_quality_score": float(t.knowledge_quality_score or 0),
                "created_at": t.created_at, "resolved_at": t.resolved_at,
            }
            for t in rows
        ],
    }


@router.get("/tickets/{ticket_id}")
def get_ticket(ticket_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    ticket = _get(db, ticket_id)
    profile = KTAnalysisService.build_profile(ticket.specifications)

    # build_grid returns ORM rows in its cells; they have to be flattened
    # before this leaves the process or FastAPI cannot serialise them.
    grid = KTAnalysisService.build_grid(ticket.specifications)
    grid = {
        **grid,
        "rows": [
            {
                "dimension": row["dimension"],
                "is": [_serialise(s) for s in row["is"]["entries"]],
                "is_not": [_serialise(s) for s in row["is_not"]["entries"]],
            }
            for row in grid["rows"]
        ],
    }

    return {
        **_serialise(ticket),
        "kt_grid": grid,
        "specifications": [_serialise(s) for s in ticket.specifications],
        "distinctions": [_serialise(d) for d in ticket.distinctions],
        # Derived from matching structured_key pairs — shown alongside the
        # typed ones so the engineer can promote them rather than retype them.
        "derived_distinctions": KTAnalysisService.derive_distinctions(ticket.specifications),
        "changes": [_serialise(c) for c in ticket.changes],
        "hypotheses": [
            {
                **_serialise(h),
                "evidence_for": sum(
                    1 for e in ticket.evidence
                    if e.hypothesis_id == h.id and e.direction == "FOR"
                ),
                "evidence_against": sum(
                    1 for e in ticket.evidence
                    if e.hypothesis_id == h.id and e.direction == "AGAINST"
                ),
                "tests_run": sum(
                    1 for t in ticket.tests
                    if t.hypothesis_id == h.id and t.result_status != "NOT_RUN"
                ),
            }
            for h in ticket.hypotheses
        ],
        "evidence": [_serialise(e) for e in ticket.evidence],
        "tests": [_serialise(t) for t in ticket.tests],
        "actions": [_serialise(a) for a in ticket.actions],
        "root_causes": [_serialise(rc) for rc in ticket.root_causes],
        "timeline": [_serialise(e) for e in ticket.timeline],
        "completeness": KnowledgeQualityService.completeness(ticket),
        "hypothesis_funnel": _funnel(ticket),
        "kt_has_contrast": profile.has_contrast,
    }


def _funnel(ticket) -> dict:
    """§27 — the narrowing funnel, as counts.

    The number that matters is not how many causes were proposed but how many
    survive: progress in a KT investigation is measured in candidates
    eliminated, not stages passed.
    """
    hypotheses = list(ticket.hypotheses)
    with_evidence = {e.hypothesis_id for e in ticket.evidence if e.hypothesis_id}
    tested = {t.hypothesis_id for t in ticket.tests if t.result_status != "NOT_RUN"}
    return {
        "possible_causes": len(hypotheses),
        "evidence_checked": sum(1 for h in hypotheses if h.id in with_evidence),
        "tests_run": sum(1 for h in hypotheses if h.id in tested),
        "probable_causes": sum(1 for h in hypotheses if h.status in ("SUPPORTED", "TESTING")),
        "rejected": sum(1 for h in hypotheses if h.status == "REJECTED"),
        "verified_root_cause": sum(
            1 for rc in ticket.root_causes if rc.confidence == "CONFIRMED"
        ),
    }


@router.patch("/tickets/{ticket_id}")
def update_ticket(ticket_id: uuid.UUID, payload: TicketUpdate,
                  db: Session = Depends(get_db)) -> dict:
    ticket = _get(db, ticket_id)
    changes = payload.model_dump(exclude_unset=True)
    if "metadata" in changes:
        changes["metadata_"] = changes.pop("metadata")
    for key, value in changes.items():
        setattr(ticket, key, value)
    index = TicketService.save(db, ticket)
    return {**_serialise(ticket), "_index": index}


@router.get("/tickets/{ticket_id}/completeness")
def completeness(ticket_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    return KnowledgeQualityService.completeness(_get(db, ticket_id))


@router.post("/tickets/{ticket_id}/reindex")
def reindex(ticket_id: uuid.UUID, force: bool = False, db: Session = Depends(get_db)) -> dict:
    ticket = _get(db, ticket_id)
    result = IndexingService.reindex_ticket(db, ticket, force=force)
    db.commit()
    return result.as_dict()


@router.get("/tickets/{ticket_id}/similar")
def similar(ticket_id: uuid.UUID, top_k: int = Query(default=5, ge=1, le=20),
            db: Session = Depends(get_db)) -> dict:
    ticket = _get(db, ticket_id)
    pack = DiagnosticReasoningService.assemble(db, ticket, "", top_k)
    return {"ticket_number": ticket.ticket_number,
            "similar_cases": pack.similar_cases,
            "retrieval": pack.retrieval_meta}


# -----------------------------------------------------------------------------
# KT sub-resources
# -----------------------------------------------------------------------------
def _add_child(db: Session, ticket: SupportTicket, model, payload, **extra):
    row = model(ticket_id=ticket.id, **payload.model_dump(), **extra)
    db.add(row)
    TicketService.save(db, ticket)
    db.refresh(row)
    return _serialise(row)


@router.post("/tickets/{ticket_id}/kt-specifications", status_code=201)
def add_specification(ticket_id: uuid.UUID, payload: SpecificationCreate,
                      db: Session = Depends(get_db)) -> dict:
    ticket = _get(db, ticket_id)
    created = _add_child(db, ticket, KTSpecification, payload)
    db.refresh(ticket)
    return {
        **created,
        # Surface immediately: a new IS that pairs with an existing IS NOT on
        # the same structured_key has just produced a distinction for free.
        "derived_distinctions": KTAnalysisService.derive_distinctions(ticket.specifications),
    }


@router.get("/tickets/{ticket_id}/kt-specifications")
def get_grid(ticket_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    ticket = _get(db, ticket_id)
    grid = KTAnalysisService.build_grid(ticket.specifications)
    return {
        "rows": [
            {
                "dimension": row["dimension"],
                "is": [_serialise(s) for s in row["is"]["entries"]],
                "is_not": [_serialise(s) for s in row["is_not"]["entries"]],
            }
            for row in grid["rows"]
        ],
        "filled_cells": grid["filled_cells"],
        "total_cells": grid["total_cells"],
        "gaps": KTAnalysisService.gaps(
            KTAnalysisService.build_profile(ticket.specifications)
        ),
    }


@router.delete("/kt-specifications/{spec_id}", status_code=204, response_class=Response)
def delete_specification(spec_id: uuid.UUID, db: Session = Depends(get_db)) -> Response:
    row = db.get(KTSpecification, spec_id)
    if row is None:
        raise HTTPException(404, "Specification not found")
    ticket = db.get(SupportTicket, row.ticket_id)
    db.delete(row)
    TicketService.save(db, ticket)
    return Response(status_code=204)


@router.post("/tickets/{ticket_id}/distinctions", status_code=201)
def add_distinction(ticket_id: uuid.UUID, payload: DistinctionCreate,
                    db: Session = Depends(get_db)) -> dict:
    return _add_child(db, _get(db, ticket_id), KTDistinction, payload)


@router.post("/tickets/{ticket_id}/changes", status_code=201)
def add_change(ticket_id: uuid.UUID, payload: ChangeCreate,
               db: Session = Depends(get_db)) -> dict:
    return _add_child(db, _get(db, ticket_id), KTChange, payload)


# -----------------------------------------------------------------------------
# hypotheses, evidence, tests
# -----------------------------------------------------------------------------
@router.post("/tickets/{ticket_id}/hypotheses", status_code=201)
def add_hypothesis(ticket_id: uuid.UUID, payload: HypothesisCreate,
                   db: Session = Depends(get_db)) -> dict:
    return _add_child(db, _get(db, ticket_id), KTHypothesis, payload)


@router.patch("/hypotheses/{hypothesis_id}")
def update_hypothesis(hypothesis_id: uuid.UUID, payload: HypothesisUpdate,
                      db: Session = Depends(get_db)) -> dict:
    row = db.get(KTHypothesis, hypothesis_id)
    if row is None:
        raise HTTPException(404, "Hypothesis not found")

    changes = payload.model_dump(exclude_unset=True)

    # §8 — rejected hypotheses are knowledge. Rejecting without saying why
    # throws away the only part that helps the next engineer.
    if changes.get("status") == "REJECTED":
        reasoning = changes.get("reasoning") or row.reasoning
        if not reasoning:
            raise HTTPException(
                422,
                "Rejecting a hypothesis requires `reasoning` — what evidence or test "
                "ruled it out. A rejection with no reason cannot stop anyone re-testing it.",
            )

    for key, value in changes.items():
        setattr(row, key, value)

    ticket = db.get(SupportTicket, row.ticket_id)
    TicketService.save(db, ticket)
    db.refresh(row)
    return _serialise(row)


@router.post("/tickets/{ticket_id}/evidence", status_code=201)
def add_evidence(ticket_id: uuid.UUID, payload: EvidenceCreate,
                 db: Session = Depends(get_db)) -> dict:
    return _add_child(db, _get(db, ticket_id), TicketEvidence, payload)


@router.post("/tickets/{ticket_id}/tests", status_code=201)
def add_test(ticket_id: uuid.UUID, payload: DiagnosticTestCreate,
             db: Session = Depends(get_db)) -> dict:
    ticket = _get(db, ticket_id)
    created = _add_child(db, ticket, DiagnosticTest, payload)
    warning = None
    if not (payload.expected_result_if_true and payload.expected_result_if_false):
        # Not an error — people record tests mid-incident — but a test with
        # one branch cannot discriminate, and its diagnostic value reflects that.
        warning = (
            "This test records only one expected outcome. A test that does not say what "
            "a NEGATIVE result would look like cannot eliminate anything — whatever "
            "happens, it reads as confirmation."
        )
    return {**created, "_warning": warning}


@router.patch("/tests/{test_id}")
def update_test(test_id: uuid.UUID, payload: DiagnosticTestUpdate,
                db: Session = Depends(get_db)) -> dict:
    row = db.get(DiagnosticTest, test_id)
    if row is None:
        raise HTTPException(404, "Test not found")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(row, key, value)

    ticket = db.get(SupportTicket, row.ticket_id)

    # A test result is the strongest signal there is about a hypothesis, so it
    # propagates. REJECTS without reasoning would violate the rule above, so
    # the reason is written from the test itself.
    if row.hypothesis_id and row.result_status in ("REJECTS", "CONFIRMS", "SUPPORTS"):
        hypothesis = db.get(KTHypothesis, row.hypothesis_id)
        if hypothesis:
            hypothesis.status = {
                "REJECTS": "REJECTED", "CONFIRMS": "CONFIRMED", "SUPPORTS": "SUPPORTED",
            }[row.result_status]
            note = (f"Test '{row.test_name}' -> {row.result_status}: "
                    f"{row.actual_result or 'no detail recorded'}")
            hypothesis.reasoning = f"{hypothesis.reasoning}\n{note}".strip() \
                if hypothesis.reasoning else note

    TicketService.save(db, ticket)
    db.refresh(row)
    return _serialise(row)


# -----------------------------------------------------------------------------
# actions, root cause, timeline
# -----------------------------------------------------------------------------
@router.post("/tickets/{ticket_id}/actions", status_code=201)
def add_action(ticket_id: uuid.UUID, payload: ActionCreate,
               db: Session = Depends(get_db)) -> dict:
    return _add_child(db, _get(db, ticket_id), TicketAction, payload)


@router.post("/tickets/{ticket_id}/root-cause", status_code=201)
def add_root_cause(ticket_id: uuid.UUID, payload: RootCauseCreate,
                   db: Session = Depends(get_db)) -> dict:
    ticket = _get(db, ticket_id)
    row = RootCause(ticket_id=ticket.id, **payload.model_dump())
    db.add(row)
    db.flush()
    db.refresh(ticket)

    warnings = TicketService.confirm_root_cause(row, ticket)
    TicketService.save(db, ticket)
    db.refresh(row)
    return {**_serialise(row), "_warnings": warnings}


@router.post("/tickets/{ticket_id}/timeline", status_code=201)
def add_timeline(ticket_id: uuid.UUID, payload: TimelineCreate,
                 db: Session = Depends(get_db)) -> dict:
    return _add_child(db, _get(db, ticket_id), TicketTimeline, payload)
