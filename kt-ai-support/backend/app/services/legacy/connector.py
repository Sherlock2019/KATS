"""Read-only connector to the legacy CORE database.

Two decisions shape this file.

**The vendor is a URL, not a code path.** CORE could be MySQL, PostgreSQL,
Oracle or SQL Server — nobody currently knows. SQLAlchemy speaks all four, so
the vendor becomes `LEGACY_DATABASE_URL` and this module never branches on it:

    mysql+pymysql://ro@core/core
    postgresql+psycopg://ro@core/core
    oracle+oracledb://ro@core:1521/?service_name=CORE
    mssql+pyodbc://ro@core/core?driver=ODBC+Driver+18+for+SQL+Server

**Six views are the entire contract.** Every piece of schema archaeology lives
on the legacy side, in SQL their DBA owns and can validate. We read those six
shapes and nothing else, so a CORE schema change breaks a view definition
rather than this pipeline.

This module only ever reads. There is no write path, and the engine is opened
read-only where the driver supports it.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app.config import get_settings

log = logging.getLogger("kt.legacy.connector")

# The contract. Names are configurable because a DBA may not be able to create
# objects called exactly this in a shared schema.
DEFAULT_VIEWS = {
    "ticket": "v_ticket",
    "message": "v_ticket_message",
    "resolution": "v_ticket_resolution",
    "attachment": "v_ticket_attachment",
    "kb": "v_kb_article",
    "kb_link": "v_kb_link",
}


@dataclass
class LegacyTicket:
    """One legacy ticket, with its whole thread, as fetched."""

    source_ref: str
    header: dict[str, Any]
    messages: list[dict[str, Any]] = field(default_factory=list)
    resolution: dict[str, Any] | None = None
    attachments: list[dict[str, Any]] = field(default_factory=list)

    def payload(self) -> dict[str, Any]:
        return {
            "header": self.header,
            "messages": self.messages,
            "resolution": self.resolution,
            "attachments": self.attachments,
        }

    def content_hash(self) -> str:
        """Hash of everything that would change the extraction.

        Deliberately not the whole row: a legacy system that touches
        `last_viewed_at` on every read would otherwise invalidate every ticket
        nightly and re-run extraction on all of them.
        """
        material = json.dumps(self.payload(), sort_keys=True, default=str)
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    @property
    def thread_text(self) -> str:
        """The free text, and only the free text.

        This is what goes to the LLM. Everything the header already answers —
        customer, product, severity, dates — is read from columns instead,
        which is the single biggest cost saving in the pipeline.
        """
        lines = []
        for m in self.messages:
            who = (m.get("author_role") or "unknown").upper()
            when = m.get("ts") or ""
            body = (m.get("body") or "").strip()
            if body:
                lines.append(f"[{when}] {who}: {body}")
        if self.resolution and self.resolution.get("resolution"):
            lines.append(f"[RESOLUTION] {self.resolution['resolution']}")
        return "\n".join(lines)

    @property
    def substantive(self) -> bool:
        """Is there enough free text to be worth a model call at all?

        A three-line "please reboot" ticket has no KT content to extract. It
        still becomes a row — it counts — but sending it to an LLM buys
        nothing and costs 30 seconds.
        """
        return len(self.thread_text) >= 400 and len(self.messages) >= 3


@dataclass
class LegacyKB:
    source_ref: str
    row: dict[str, Any]

    def content_hash(self) -> str:
        material = json.dumps(self.row, sort_keys=True, default=str)
        return hashlib.sha256(material.encode("utf-8")).hexdigest()


class LegacyConnector:
    def __init__(self, url: str | None = None, views: dict[str, str] | None = None):
        settings = get_settings()
        self.url = url or settings.legacy_database_url
        self.views = {**DEFAULT_VIEWS, **(views or settings.legacy_views)}
        self._engine: Engine | None = None

    # ------------------------------------------------------------------
    @property
    def configured(self) -> bool:
        return bool(self.url)

    @property
    def engine(self) -> Engine:
        if self._engine is None:
            if not self.url:
                raise RuntimeError(
                    "LEGACY_DATABASE_URL is not set. Point it at a read-only "
                    "CORE account, or run scripts/make_fake_core.py to test "
                    "the pipeline without one."
                )
            # pool_pre_ping: a legacy DB behind a firewall will drop idle
            # connections during a long backfill, and a stale one fails the
            # whole run rather than one batch.
            self._engine = create_engine(self.url, pool_pre_ping=True, pool_size=4)
        return self._engine

    def probe(self) -> dict[str, Any]:
        """Check the connection and that each view exists and is readable."""
        result: dict[str, Any] = {"url": self._redacted_url(), "views": {}}
        try:
            with self.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
                result["reachable"] = True
        except Exception as exc:  # noqa: BLE001
            result["reachable"] = False
            result["error"] = f"{type(exc).__name__}: {exc}"
            return result

        for kind, view in self.views.items():
            try:
                with self.engine.connect() as conn:
                    n = conn.execute(text(f"SELECT COUNT(*) FROM {view}")).scalar()
                result["views"][kind] = {"view": view, "ok": True, "rows": int(n or 0)}
            except Exception as exc:  # noqa: BLE001
                result["views"][kind] = {"view": view, "ok": False,
                                         "error": f"{type(exc).__name__}: {exc}"}
        return result

    def _redacted_url(self) -> str:
        if not self.url:
            return "<unset>"
        if "@" in self.url:
            scheme, rest = self.url.split("://", 1)
            return f"{scheme}://***@{rest.split('@', 1)[1]}"
        return self.url

    # ------------------------------------------------------------------
    # fetch
    # ------------------------------------------------------------------
    def _rows(self, sql: str, params: dict) -> list[dict[str, Any]]:
        with self.engine.connect() as conn:
            return [dict(r._mapping) for r in conn.execute(text(sql), params)]

    def ticket_ids(self, since: datetime | None = None,
                   limit: int | None = None) -> list[str]:
        """Candidate ticket ids, newest first.

        Newest-first matters: the last two years carry most of the retrieval
        value, so a backfill that is interrupted half way through has still
        imported the half that counts.
        """
        where = "WHERE updated_at > :since" if since else ""
        sql = (f"SELECT id FROM {self.views['ticket']} {where} "
               f"ORDER BY COALESCE(updated_at, opened) DESC")
        if limit:
            sql += f" LIMIT {int(limit)}"
        return [str(r["id"]) for r in self._rows(sql, {"since": since} if since else {})]

    def fetch_tickets(self, ids: list[str], batch: int = 200) -> Iterator[LegacyTicket]:
        """Fetch whole tickets, batched.

        One query per table per batch rather than per ticket: 200 tickets is
        4 round trips, not 800.
        """
        for start in range(0, len(ids), batch):
            chunk = ids[start:start + batch]
            placeholders = ", ".join(f":id{i}" for i in range(len(chunk)))
            params = {f"id{i}": v for i, v in enumerate(chunk)}

            headers = {
                str(r["id"]): r
                for r in self._rows(
                    f"SELECT * FROM {self.views['ticket']} WHERE id IN ({placeholders})",
                    params)
            }

            messages: dict[str, list] = {}
            for r in self._rows(
                f"SELECT * FROM {self.views['message']} "
                f"WHERE ticket_id IN ({placeholders}) ORDER BY ticket_id, seq", params
            ):
                messages.setdefault(str(r["ticket_id"]), []).append(r)

            resolutions = {
                str(r["ticket_id"]): r
                for r in self._rows(
                    f"SELECT * FROM {self.views['resolution']} "
                    f"WHERE ticket_id IN ({placeholders})", params)
            }

            attachments: dict[str, list] = {}
            try:
                for r in self._rows(
                    f"SELECT * FROM {self.views['attachment']} "
                    f"WHERE ticket_id IN ({placeholders})", params
                ):
                    attachments.setdefault(str(r["ticket_id"]), []).append(r)
            except Exception:  # noqa: BLE001
                # Attachments are optional; a missing view must not stop a
                # backfill that is otherwise working.
                log.debug("attachment view unavailable, continuing without it")

            for tid in chunk:
                header = headers.get(tid)
                if not header:
                    continue
                yield LegacyTicket(
                    source_ref=tid,
                    header=header,
                    messages=messages.get(tid, []),
                    resolution=resolutions.get(tid),
                    attachments=attachments.get(tid, []),
                )

    # ------------------------------------------------------------------
    def fetch_kb(self, since: datetime | None = None,
                 limit: int | None = None) -> Iterator[LegacyKB]:
        where = "WHERE updated_at > :since" if since else ""
        sql = f"SELECT * FROM {self.views['kb']} {where} ORDER BY updated_at DESC"
        if limit:
            sql += f" LIMIT {int(limit)}"
        for r in self._rows(sql, {"since": since} if since else {}):
            yield LegacyKB(source_ref=str(r.get("id")), row=r)

    def fetch_kb_links(self) -> list[dict[str, Any]]:
        """The curated ticket <-> KB associations.

        Quietly the most valuable thing in the legacy estate: human-made, no
        extraction, no confidence scoring, and it yields problem clusters
        without a single model call.
        """
        try:
            return self._rows(f"SELECT * FROM {self.views['kb_link']}", {})
        except Exception as exc:  # noqa: BLE001
            log.warning("kb_link view unavailable: %s", exc)
            return []
