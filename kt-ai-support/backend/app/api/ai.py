"""The AI diagnostic assistant. §23, §28, §45."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import SupportTicket
from app.schemas import DiagnoseRequest
from app.services.diagnostics.reasoning import DiagnosticReasoningService

router = APIRouter(prefix="/api/ai", tags=["ai"])


def _get(db: Session, ticket_id: uuid.UUID) -> SupportTicket:
    ticket = db.get(SupportTicket, ticket_id)
    if ticket is None:
        raise HTTPException(404, "Ticket not found")
    return ticket


@router.post("/diagnose")
def diagnose(payload: DiagnoseRequest, db: Session = Depends(get_db)) -> dict:
    """Retrieve, ground, reason, cite.

    Never 503s on an unreachable model. Retrieval, the similar cases and the
    diagnostic-value ranking are all computed from the database, so a
    degraded answer is still a real one — and it says in `warnings` exactly
    what it could not do.
    """
    ticket = _get(db, payload.ticket_id)
    return DiagnosticReasoningService.diagnose(db, ticket, payload.question, payload.top_k)


@router.post("/next-action")
def next_action(payload: DiagnoseRequest, db: Session = Depends(get_db)) -> dict:
    return DiagnosticReasoningService.next_action(db, _get(db, payload.ticket_id))


@router.post("/next-question")
def next_question(payload: DiagnoseRequest, db: Session = Depends(get_db)) -> dict:
    return DiagnosticReasoningService.next_question(db, _get(db, payload.ticket_id))
