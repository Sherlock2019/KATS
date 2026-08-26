"""Load the demo corpus.

    python -m scripts.seed_demo_cases            insert and index
    python -m scripts.seed_demo_cases --reset    delete existing first
    python -m scripts.seed_demo_cases --no-index insert without embedding

Run from kt-ai-support/backend with the venv active.
"""

from __future__ import annotations

import argparse
import pathlib
import sys
from datetime import timedelta

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "backend"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, select                              # noqa: E402

from app.db import SessionLocal                                    # noqa: E402
from app.models import (                                           # noqa: E402
    DiagnosticTest,
    KTChange,
    KTHypothesis,
    KTSpecification,
    RootCause,
    SupportTicket,
    TicketEvidence,
    TicketTimeline,
)
from app.services.rag.indexing import IndexingService              # noqa: E402
from app.services.ticket_service import TicketService              # noqa: E402
from scripts.demo_cases import DEMO_CASES                          # noqa: E402

TICKET_FIELDS = {c.name for c in SupportTicket.__table__.columns} - {
    "id", "ticket_number", "created_at", "updated_at", "error_signature_norm",
    "root_cause_status", "root_cause", "root_cause_confidence", "knowledge_quality_score",
    "metadata",
}


def build(db, case: dict) -> SupportTicket:
    ticket = SupportTicket(**{k: v for k, v in case.items() if k in TICKET_FIELDS})
    db.add(ticket)
    db.flush()

    for dimension, side, value, key, structured in case.get("kt", []):
        db.add(KTSpecification(
            ticket_id=ticket.id, dimension=dimension, side=side, value=value,
            structured_key=key, structured_value=structured,
        ))

    hypotheses = []
    for cause, status, probability, reasoning in case.get("hyp", []):
        row = KTHypothesis(
            ticket_id=ticket.id, cause=cause, status=status,
            probability_score=probability, reasoning=reasoning,
            rank=len(hypotheses) + 1,
        )
        db.add(row)
        hypotheses.append(row)
    db.flush()

    evidence_rows = []
    for evidence_type, direction, content, hyp_index in case.get("ev", []):
        row = TicketEvidence(
            ticket_id=ticket.id, evidence_type=evidence_type, direction=direction,
            content=content,
            hypothesis_id=hypotheses[hyp_index].id if hyp_index is not None
            and hyp_index < len(hypotheses) else None,
            observed_at=case.get("first_seen_at"),
        )
        db.add(row)
        evidence_rows.append(row)

    for entry in case.get("test", []):
        name, hyp_index, if_true, if_false, actual, status, risk, reversible, minutes = entry
        db.add(DiagnosticTest(
            ticket_id=ticket.id, test_name=name,
            hypothesis_id=hypotheses[hyp_index].id if hyp_index is not None
            and hyp_index < len(hypotheses) else None,
            expected_result_if_true=if_true, expected_result_if_false=if_false,
            actual_result=actual, result_status=status,
            risk_level=risk, reversible=reversible, estimated_minutes=minutes,
            completed_at=case.get("first_seen_at") if status != "NOT_RUN" else None,
        ))

    for change_type, description, old, new, offset_hours in case.get("change", []):
        occurred = None
        if case.get("first_seen_at"):
            occurred = case["first_seen_at"] + timedelta(hours=offset_hours)
        db.add(KTChange(
            ticket_id=ticket.id, change_type=change_type, description=description,
            old_value=old, new_value=new, occurred_at=occurred,
            suspected_relevance=0.8,
        ))

    if case.get("rc"):
        cause, category, component, mechanism, trigger, method, result, confidence = case["rc"]
        db.add(RootCause(
            ticket_id=ticket.id, cause=cause, cause_category=category, component=component,
            mechanism=mechanism, trigger=trigger,
            verification_method=method, verification_result=result, confidence=confidence,
            confirmed_at=case.get("first_seen_at") if confidence == "CONFIRMED" else None,
        ))

    for event_type, description, offset_minutes in case.get("tl", []):
        if not case.get("first_seen_at"):
            continue
        db.add(TicketTimeline(
            ticket_id=ticket.id, event_type=event_type, description=description,
            occurred_at=case["first_seen_at"] + timedelta(minutes=offset_minutes),
        ))

    db.flush()
    db.refresh(ticket)
    return ticket


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true", help="delete existing tickets first")
    parser.add_argument("--no-index", action="store_true", help="skip embedding")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        if args.reset:
            count = db.scalar(select(SupportTicket).with_only_columns(
                SupportTicket.id).limit(1))
            db.execute(delete(SupportTicket))
            db.commit()
            print("  cleared existing tickets" if count else "  nothing to clear")

        print(f"\n  Inserting {len(DEMO_CASES)} demo cases…")
        created = []
        for case in DEMO_CASES:
            ticket = build(db, case)
            TicketService.apply_derived(ticket)
            created.append(ticket)
        db.commit()

        for ticket in created:
            db.refresh(ticket)
            print(f"    {ticket.ticket_number}  {ticket.title[:66]:<66} "
                  f"q={float(ticket.knowledge_quality_score):.2f} {ticket.root_cause_status}")

        if args.no_index:
            print("\n  --no-index: chunks not built.\n")
            return 0

        print("\n  Building chunks and embedding…")
        results = IndexingService.reindex_all(db)
        print(f"    {len(results)} tickets, "
              f"{sum(r.built for r in results)} chunks, "
              f"{sum(r.embedded for r in results)} embedded "
              f"({results[0].embed_mode if results else 'n/a'})\n")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
