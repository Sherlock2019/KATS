"""The legacy ingestion pipeline: fetch -> Bronze -> map -> extract -> chunk.

Three properties this file exists to guarantee:

**Idempotent.** Every stage keys off a hash. Re-running costs a hash compare
per unchanged record, so a nightly job over 50,000 tickets is seconds, and an
interrupted backfill resumes rather than restarts.

**Resumable.** `legacy_raw.status` records how far each record got. A crash at
ticket 1,400 of 3,000 does not send you back to 1.

**Never destructive.** `human_reviewed` blocks re-extraction from overwriting
a correction someone made by hand — however much better the model gets later.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    KTChange,
    KTHypothesis,
    KTSpecification,
    RootCause,
    SupportTicket,
    TicketTimeline,
)
from app.services.legacy import confidence as scoring
from app.services.legacy.connector import LegacyConnector, LegacyTicket
from app.services.legacy.extractor import extract
from app.services.legacy.mapper import map_ticket, should_extract
from app.services.rag.indexing import IndexingService

log = logging.getLogger("kt.legacy.pipeline")
UTC = timezone.utc

_SPEC_MAP = {
    "what_is": ("WHAT", "IS"), "what_is_not": ("WHAT", "IS_NOT"),
    "where_is": ("WHERE", "IS"), "where_is_not": ("WHERE", "IS_NOT"),
    "when_is": ("WHEN", "IS"), "when_is_not": ("WHEN", "IS_NOT"),
    "extent_is": ("EXTENT", "IS"), "extent_is_not": ("EXTENT", "IS_NOT"),
}


@dataclass
class RunStats:
    fetched: int = 0
    unchanged: int = 0
    mapped: int = 0
    extracted: int = 0
    skipped_extract: int = 0
    chunked: int = 0
    failed: int = 0
    protected: int = 0
    skip_reasons: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "fetched": self.fetched, "unchanged_skipped": self.unchanged,
            "rows_written": self.mapped, "extracted": self.extracted,
            "extract_skipped": self.skipped_extract, "chunked": self.chunked,
            "failed": self.failed, "human_reviewed_protected": self.protected,
            "skip_reasons": self.skip_reasons,
        }


class LegacyPipeline:
    def __init__(self, db: Session, connector: LegacyConnector | None = None):
        self.db = db
        self.settings = get_settings()
        self.connector = connector or LegacyConnector()

    # ------------------------------------------------------------------
    # Bronze
    # ------------------------------------------------------------------
    def _known_hash(self, source_ref: str) -> str | None:
        """The hash of the last SUCCESSFUL pass over this ticket.

        Deliberately ignores rows left in 'failed'. Landing a hash on failure
        and then treating it as "seen" means a ticket that errored once is
        never retried — a silent, permanent hole in the corpus that nothing
        downstream would ever surface.
        """
        row = self.db.execute(
            text("SELECT source_hash FROM legacy_raw "
                 "WHERE source_kind='ticket' AND source_ref=:r "
                 "AND status IN ('mapped', 'extracted', 'skipped')"),
            {"r": source_ref},
        ).fetchone()
        return row[0] if row else None

    def _land(self, ticket: LegacyTicket, status: str, detail: str = "") -> None:
        self.db.execute(
            text("""
                INSERT INTO legacy_raw
                    (source_kind, source_ref, source_hash, payload, status, status_detail,
                     processed_at)
                VALUES ('ticket', :ref, :hash, CAST(:payload AS jsonb), :status, :detail, NOW())
                ON CONFLICT (source_kind, source_ref) DO UPDATE SET
                    source_hash   = EXCLUDED.source_hash,
                    payload       = EXCLUDED.payload,
                    status        = EXCLUDED.status,
                    status_detail = EXCLUDED.status_detail,
                    processed_at  = NOW()
            """),
            {"ref": ticket.source_ref, "hash": ticket.content_hash(),
             "payload": json.dumps(ticket.payload(), default=str),
             "status": status, "detail": detail[:500]},
        )

    # ------------------------------------------------------------------
    # Silver
    # ------------------------------------------------------------------
    def _upsert_ticket(self, mapped: dict[str, Any]) -> tuple[SupportTicket, bool]:
        """Returns (ticket, is_protected). Protected means a human edited it."""
        existing = self.db.scalar(
            select(SupportTicket).where(SupportTicket.source_ref == mapped["source_ref"])
        )

        if existing and existing.human_reviewed:
            # Somebody corrected this by hand. Refresh nothing; a better model
            # is still not better than a person who read the ticket.
            return existing, True

        target = existing or SupportTicket()
        for key, value in mapped.items():
            setattr(target, key, value)
        if existing is None:
            self.db.add(target)
        self.db.flush()
        return target, False

    def _write_extracted(self, ticket: SupportTicket, extracted: dict[str, Any],
                         scored: scoring.Scored) -> None:
        """Persist KT fields. Replaces this extractor's previous output only."""
        # Clear what a previous extraction wrote, so re-running does not
        # accumulate duplicates. Human-reviewed records never reach here.
        for model in (KTSpecification, KTChange, KTHypothesis, RootCause):
            self.db.query(model).filter(model.ticket_id == ticket.id).delete()

        if extracted.get("problem"):
            ticket.problem_summary = extracted["problem"]
        if extracted.get("expected"):
            ticket.expected_behavior = extracted["expected"]
        if extracted.get("actual"):
            ticket.actual_behavior = extracted["actual"]
        if extracted.get("workaround"):
            ticket.workaround = extracted["workaround"]
        if extracted.get("prevention"):
            ticket.prevention_summary = extracted["prevention"]

        # IS / IS NOT — only the cells the thread actually stated.
        for key, value in (extracted.get("specification") or {}).items():
            if not value or key not in _SPEC_MAP:
                continue
            dimension, side = _SPEC_MAP[key]
            self.db.add(KTSpecification(
                ticket_id=ticket.id, dimension=dimension, side=side, value=value,
            ))

        for change in extracted.get("changes") or []:
            self.db.add(KTChange(
                ticket_id=ticket.id,
                change_type=change["type"],
                description=change["description"],
                suspected_relevance=0.5,
            ))

        # Rejected causes are recorded as REJECTED hypotheses — the most
        # under-used knowledge in any legacy ticket, and the thing that stops
        # the next engineer re-running a test somebody already ran.
        for rejected in extracted.get("rejected_causes") or []:
            self.db.add(KTHypothesis(
                ticket_id=ticket.id,
                cause=rejected["cause"],
                status="REJECTED",
                reasoning=rejected.get("why") or "ruled out in the original ticket",
                proposed_by="legacy_extraction",
            ))

        rc = extracted.get("root_cause") or {}
        if rc.get("cause") and scored.root_cause_confidence:
            self.db.add(RootCause(
                ticket_id=ticket.id,
                cause=rc["cause"],
                mechanism=rc.get("mechanism"),
                trigger=rc.get("trigger"),
                confidence=scored.root_cause_confidence,
                verification_method="extracted from the original ticket thread",
                verification_result="; ".join(scored.notes)[:2000],
            ))

        ticket.source_type = scored.source_type
        ticket.extractor_version = extracted.get("_extractor_version")

        meta = dict(ticket.metadata_ or {})
        meta["extraction"] = {
            "field_confidence": scored.field_confidence,
            "notes": scored.notes,
            "evidence": extracted.get("_evidence", {}),
        }
        ticket.metadata_ = meta

        self.db.flush()

    # ------------------------------------------------------------------
    # run
    # ------------------------------------------------------------------
    def run(self, *, since: datetime | None = None, limit: int | None = None,
            do_extract: bool = True, force: bool = False,
            progress=None) -> RunStats:
        stats = RunStats()

        ids = self.connector.ticket_ids(since=since, limit=limit)
        log.info("legacy sync: %d candidate ticket(s)", len(ids))

        for ticket in self.connector.fetch_tickets(ids, self.settings.legacy_batch_size):
            stats.fetched += 1
            try:
                # Unchanged? One hash compare and we are done. This is what
                # makes a nightly run over the whole corpus cost seconds.
                if not force and self._known_hash(ticket.source_ref) == ticket.content_hash():
                    stats.unchanged += 1
                    if progress:
                        progress(stats)
                    continue

                mapped = map_ticket(ticket)
                row, protected = self._upsert_ticket(mapped)

                if protected:
                    stats.protected += 1
                    self._land(ticket, "skipped", "human_reviewed — left untouched")
                    self.db.commit()
                    if progress:
                        progress(stats)
                    continue

                stats.mapped += 1

                wanted, reason = should_extract(
                    ticket, mapped,
                    require_resolution=self.settings.legacy_extract_require_resolution,
                    min_chars=self.settings.legacy_extract_min_chars,
                )

                if do_extract and wanted:
                    extracted = extract(ticket)
                    if extracted:
                        message_ids = {str(i) for i in range(1, extracted["_message_count"] + 1)}
                        scored = scoring.score(mapped, extracted, message_ids)
                        self._write_extracted(row, extracted, scored)
                        stats.extracted += 1

                        # Only extracted tickets become chunks. Everything else
                        # is a row: it counts, but it cannot dilute retrieval.
                        result = IndexingService.reindex_ticket(self.db, row)
                        stats.chunked += result.embedded
                        self._land(ticket, "extracted")
                    else:
                        scored = scoring.score(mapped, None)
                        row.source_type = scored.source_type
                        self._land(ticket, "failed", "extraction returned nothing usable")
                        stats.failed += 1
                else:
                    scored = scoring.score(mapped, None)
                    row.source_type = scored.source_type
                    stats.skipped_extract += 1
                    key = reason or "extraction disabled"
                    stats.skip_reasons[key] = stats.skip_reasons.get(key, 0) + 1
                    self._land(ticket, "mapped", key)

                self.db.commit()

            except Exception as exc:  # noqa: BLE001
                # One bad ticket must not end a 3,000-ticket backfill.
                self.db.rollback()
                stats.failed += 1
                log.warning("legacy ticket %s failed: %s", ticket.source_ref, exc)
                try:
                    self._land(ticket, "failed", f"{type(exc).__name__}: {exc}")
                    self.db.commit()
                except Exception:  # noqa: BLE001
                    self.db.rollback()

            if progress:
                progress(stats)

        self._save_watermark("ticket", stats)
        return stats

    def _save_watermark(self, kind: str, stats: RunStats) -> None:
        self.db.execute(
            text("""
                INSERT INTO legacy_sync_state
                    (source_kind, last_synced_at, last_run_at, rows_seen, rows_changed)
                VALUES (:k, NOW(), NOW(), :seen, :changed)
                ON CONFLICT (source_kind) DO UPDATE SET
                    last_synced_at = NOW(), last_run_at = NOW(),
                    rows_seen = EXCLUDED.rows_seen, rows_changed = EXCLUDED.rows_changed
            """),
            {"k": kind, "seen": stats.fetched, "changed": stats.mapped},
        )
        self.db.commit()

    def last_sync(self, kind: str = "ticket") -> datetime | None:
        row = self.db.execute(
            text("SELECT last_synced_at FROM legacy_sync_state WHERE source_kind=:k"),
            {"k": kind},
        ).fetchone()
        return row[0] if row else None
