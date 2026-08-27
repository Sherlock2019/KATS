"""Deterministic legacy -> canonical mapping. No model involved.

This runs on EVERY legacy ticket, including the ones that will never be
extracted or chunked, because a row is what makes "how many issues since day
one" a true answer. It is cheap: no network, no tokens, milliseconds.

The division of labour with the extractor is the whole cost story of the
migration. Anything a column already answers is copied here; only free text
goes to a model.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from app.services.kt.signature import normalize_error_signature
from app.services.legacy.connector import LegacyTicket

UTC = timezone.utc

# Legacy severity vocabularies vary; map generously and fall back to None
# rather than guessing a severity that will later be counted in a report.
_SEVERITY = {
    "1": "S1", "p1": "S1", "critical": "S1", "urgent": "S1", "sev1": "S1",
    "2": "S2", "p2": "S2", "high": "S2", "sev2": "S2",
    "3": "S3", "p3": "S3", "normal": "S3", "medium": "S3", "sev3": "S3",
    "4": "S4", "p4": "S4", "low": "S4", "minor": "S4", "sev4": "S4",
}
_PRIORITY = {"S1": "P1", "S2": "P2", "S3": "P3", "S4": "P4"}

_STATUS = {
    "closed": "CLOSED", "resolved": "RESOLVED", "complete": "CLOSED",
    "completed": "CLOSED", "done": "CLOSED", "cancelled": "CLOSED",
    "open": "INVESTIGATING", "new": "NEW", "pending": "INVESTIGATING",
    "in progress": "INVESTIGATING", "assigned": "INVESTIGATING",
    "waiting": "INVESTIGATING", "escalated": "INVESTIGATING",
}

# Closure codes that mean somebody actually established the cause, as opposed
# to the ticket merely stopping. This is what separates legacy_verified (0.90)
# from legacy_extracted (0.65), and it is a lookup, not a judgement call.
_VERIFIED_CLOSURE = {
    "resolved-verified", "root_cause_found", "permanent_fix", "fixed",
    "rca_complete", "solved", "resolved_permanent", "confirmed",
}

_ENVIRONMENT = {
    "prod": "production", "production": "production", "prd": "production",
    "stage": "staging", "staging": "staging", "stg": "staging",
    "test": "test", "qa": "test", "uat": "test",
    "dev": "development", "development": "development",
    "lab": "lab",
}

# Error-ish lines in a thread. Deliberately conservative: a false positive
# here pollutes error_signature_norm, which is a retrieval key.
_ERROR_PATTERNS = [
    re.compile(r"^\s*(?:ERROR|FATAL|CRITICAL)[: ]\s*(.{10,300})", re.M | re.I),
    re.compile(r"\b(HTTP\s*[45]\d{2}[^\n]{0,200})", re.I),
    re.compile(r"\b((?:Exception|Traceback|Errno|failed to|unable to|"
               r"cannot|could not|no such|refused|timed out)[^\n]{10,200})", re.I),
]


def _first(row: dict[str, Any], *names: str) -> Any:
    """Legacy column names are unpredictable — try several, take the first."""
    for n in names:
        if n in row and row[n] not in (None, ""):
            return row[n]
    return None


def _dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d",
                "%Y-%m-%dT%H:%M:%SZ", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.strptime(str(value)[:19], fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def _lookup(table: dict[str, str], value: Any) -> str | None:
    if value in (None, ""):
        return None
    return table.get(str(value).strip().lower())


def extract_error_text(thread: str) -> str | None:
    """Pull the most likely error line out of a thread, by regex not by model.

    Preferring the longest match is a cheap heuristic that works because a
    real stack line or HTTP error carries more detail than the sentence
    someone wrote about it.
    """
    best = None
    for pattern in _ERROR_PATTERNS:
        for match in pattern.finditer(thread):
            candidate = match.group(1).strip()
            if best is None or len(candidate) > len(best):
                best = candidate
    return best[:2000] if best else None


def map_ticket(ticket: LegacyTicket) -> dict[str, Any]:
    """Legacy ticket -> support_tickets column values.

    Returns only what the columns can answer. Everything KT — IS/IS NOT,
    changes, tests, cause — is the extractor's job and is absent here.
    """
    h = ticket.header
    thread = ticket.thread_text

    severity = _lookup(_SEVERITY, _first(h, "severity", "sev", "priority", "impact"))
    status = _lookup(_STATUS, _first(h, "status", "state")) or "CLOSED"

    resolution_text = None
    closure_code = None
    if ticket.resolution:
        resolution_text = _first(ticket.resolution, "resolution", "resolution_notes",
                                 "close_notes", "solution")
        closure_code = _first(ticket.resolution, "closure_code", "close_code",
                              "resolution_code", "disposition")

    error_text = _first(h, "error", "error_message") or extract_error_text(thread)

    opened = _dt(_first(h, "opened", "created_at", "created", "open_date"))
    closed = _dt(_first(h, "closed", "closed_at", "resolved_at", "close_date"))

    # The first customer message is the closest thing a legacy ticket has to a
    # problem statement. Not the deviation — that needs the model — but enough
    # for the record to be readable without one.
    first_customer = next(
        (m.get("body") for m in ticket.messages
         if str(m.get("author_role", "")).lower().startswith("cust") and m.get("body")),
        None,
    )

    return {
        "source_type": "legacy_raw_only",          # upgraded later by the scorer
        "source_ref": ticket.source_ref,
        "source_hash": ticket.content_hash(),

        "title": (_first(h, "title", "subject", "summary", "short_description")
                  or f"Legacy ticket {ticket.source_ref}")[:500],
        "status": status,
        "severity": severity,
        "priority": _PRIORITY.get(severity or "", None),

        "customer_name": _first(h, "customer", "customer_name", "account", "client"),
        "organization": _first(h, "organization", "org", "account_id", "tenant"),

        "product": _first(h, "product", "product_name", "offering"),
        "product_version": _first(h, "version", "product_version", "release"),
        "service": _first(h, "service", "queue"),
        "component": _first(h, "component", "category", "subcategory", "ci"),
        "subcomponent": _first(h, "subcomponent", "sub_category"),

        "environment": _first(h, "environment", "env"),
        "environment_type": _lookup(_ENVIRONMENT, _first(h, "environment", "env")),
        "region": _first(h, "region", "datacenter", "site", "location"),
        "datacenter": _first(h, "datacenter", "dc"),
        "cluster": _first(h, "cluster"),
        "node": _first(h, "node", "host", "hostname", "server"),

        "problem_summary": (first_customer or "")[:4000] or None,
        "error_message": error_text,
        "error_signature_norm": normalize_error_signature(error_text) if error_text else None,

        "first_seen_at": opened,
        "resolved_at": closed,
        "created_by": _first(h, "created_by", "reporter", "requester"),
        "assigned_to": _first(h, "assignee", "assigned_to", "owner"),

        "resolution_summary": (resolution_text or "")[:8000] or None,

        "metadata_": {
            "legacy": {
                "closure_code": closure_code,
                "queue": _first(h, "queue", "group"),
                "message_count": len(ticket.messages),
                "attachment_count": len(ticket.attachments),
                "verified_closure": bool(
                    closure_code and str(closure_code).strip().lower() in _VERIFIED_CLOSURE
                ),
            }
        },
    }


def should_extract(ticket: LegacyTicket, mapped: dict[str, Any],
                   require_resolution: bool = True,
                   min_chars: int = 400) -> tuple[bool, str]:
    """Is this ticket worth a model call?

    The filter that turns 17 days of extraction into one weekend. A ticket
    that fails it is still imported as a row — it counts in every total — it
    simply never becomes a chunk and so can never dilute retrieval.
    """
    # Reasons are bucketed, not exact: they are aggregated into a summary at
    # the end of a run, and "thread too short (251 chars)" as its own row
    # among forty others tells you nothing.
    if len(ticket.thread_text) < min_chars:
        return False, "thread too short to extract"
    if len(ticket.messages) < 3:
        return False, "fewer than 3 messages"
    if require_resolution and not mapped.get("resolution_summary"):
        return False, "no resolution recorded"
    if mapped.get("status") not in ("CLOSED", "RESOLVED"):
        return False, f"not closed (status {mapped.get('status')})"
    return True, ""
