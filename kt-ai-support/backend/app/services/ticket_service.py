"""TicketService — writes that must not happen in a route handler.

Three invariants live here, each of which is a way the record could quietly
start lying:

1. `error_signature_norm` is derived, never supplied. If a client could set
   it, two records of one fault would stop matching.

2. `root_cause_status` on the ticket is a projection of the strongest row in
   `root_causes`, recomputed on every change. A PATCH cannot assert it.

3. Every write re-indexes. Chunks that lag the record are worse than no
   chunks: retrieval returns a fix the ticket no longer claims.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models import RootCause, SupportTicket
from app.models.enums import RootCauseConfidence, TicketStatus
from app.services.kt.quality import KnowledgeQualityService
from app.services.kt.signature import normalize_error_signature
from app.services.rag.indexing import IndexingService

log = logging.getLogger("kt.tickets")

_LADDER = [
    RootCauseConfidence.CONFIRMED,
    RootCauseConfidence.HIGH_CONFIDENCE,
    RootCauseConfidence.PROBABLE,
    RootCauseConfidence.SUSPECTED,
]
_NUMERIC = {
    RootCauseConfidence.CONFIRMED: 0.95,
    RootCauseConfidence.HIGH_CONFIDENCE: 0.75,
    RootCauseConfidence.PROBABLE: 0.5,
    RootCauseConfidence.SUSPECTED: 0.25,
}


class TicketService:

    @staticmethod
    def apply_derived(ticket: SupportTicket) -> None:
        ticket.error_signature_norm = normalize_error_signature(
            ticket.error_message or ticket.title
        )

        causes = list(ticket.root_causes)
        if causes:
            best = min(causes, key=lambda rc: _LADDER.index(
                RootCauseConfidence(rc.confidence)
                if rc.confidence in _LADDER else RootCauseConfidence.SUSPECTED
            ))
            ticket.root_cause_status = str(best.confidence)
            ticket.root_cause = best.cause
            ticket.root_cause_confidence = _NUMERIC.get(
                RootCauseConfidence(best.confidence), 0.25
            )
        else:
            ticket.root_cause_status = "UNKNOWN"
            ticket.root_cause_confidence = None

        # Closing a ticket that never reached a cause is allowed — plenty of
        # incidents self-resolve — but it must not be recorded as if it had.
        if ticket.status in (TicketStatus.RESOLVED, TicketStatus.CLOSED) and not ticket.resolved_at:
            ticket.resolved_at = datetime.now(UTC)

        score, _ = KnowledgeQualityService.evaluate(ticket)
        ticket.knowledge_quality_score = round(score, 2)

    @classmethod
    def save(cls, db: Session, ticket: SupportTicket, reindex: bool = True) -> dict | None:
        """Flush, recompute the derived fields, re-index, commit."""
        db.flush()
        db.refresh(ticket)
        cls.apply_derived(ticket)

        result = None
        if reindex:
            try:
                result = IndexingService.reindex_ticket(db, ticket).as_dict()
            except Exception as exc:  # noqa: BLE001
                # A failed embed must not lose the engineer's typing. The
                # chunks are rebuildable; the ticket edit is not.
                log.error("reindex failed for %s: %s", ticket.ticket_number, exc)

        db.commit()
        db.refresh(ticket)
        return result

    @staticmethod
    def confirm_root_cause(root_cause: RootCause, ticket: SupportTicket) -> list[str]:
        """Guard the top of the ladder.

        CONFIRMED is what earns the highest retrieval boost, so it has to mean
        something. A verification method and result, or a test on this ticket
        that CONFIRMS, are the evidence; without either, the claim is demoted
        and the caller is told why.
        """
        warnings: list[str] = []
        if root_cause.confidence != RootCauseConfidence.CONFIRMED:
            return warnings

        has_verification = bool(root_cause.verification_method and root_cause.verification_result)
        has_confirming_test = any(t.result_status == "CONFIRMS" for t in ticket.tests)

        if not (has_verification or has_confirming_test):
            root_cause.confidence = RootCauseConfidence.HIGH_CONFIDENCE
            warnings.append(
                "Downgraded CONFIRMED to HIGH_CONFIDENCE: a confirmed root cause needs a "
                "verification method and result, or a diagnostic test on this ticket with "
                "result CONFIRMS. Record one and set it again."
            )
        elif not root_cause.confirmed_at:
            root_cause.confirmed_at = datetime.now(UTC)

        return warnings
