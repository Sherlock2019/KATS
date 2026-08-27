"""Thread -> KT fields, via the local LLM.

The model's whole job is transcription: find the sentences that carry KT
meaning and put them in labelled slots. It assigns no scores and makes no
judgements — `confidence.py` does that afterwards from checkable structure.

Everything the model returns is validated before it is trusted. A field that
is not a string, an enum value that is not in the vocabulary, a citation
pointing at a message that does not exist — all become null rather than
propagating into the canonical record.
"""

from __future__ import annotations

import logging
from typing import Any

from app.config import get_settings
from app.models.enums import ChangeType
from app.prompts.extraction import EXTRACTION_SYSTEM, build_extraction_prompt
from app.services.legacy.connector import LegacyTicket
from app.services.llm.service import get_llm_service

log = logging.getLogger("kt.legacy.extractor")

_SPEC_KEYS = (
    "what_is", "what_is_not", "where_is", "where_is_not",
    "when_is", "when_is_not", "extent_is", "extent_is_not",
)
_VERDICTS = {"CONFIRMS", "REJECTS", "INCONCLUSIVE"}
_CHANGE_TYPES = {str(c) for c in ChangeType}


def _clean_text(value: Any, limit: int = 4000) -> str | None:
    """A field is a non-empty string or it is null.

    Small models return `"null"`, `"N/A"`, `"unknown"` and `"not specified"`
    as *strings* a fair amount of the time. Those are the model saying it does
    not know, and they must not be stored as if they were content.
    """
    if value is None:
        return None
    if isinstance(value, (list, dict)):
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.lower() in ("null", "none", "n/a", "na", "unknown", "not specified",
                        "not stated", "not mentioned", "-", "unspecified"):
        return None
    return text[:limit]


def _valid_citations(raw: Any, message_count: int) -> list[int]:
    """Keep only citations that point at a message that exists."""
    if raw is None:
        return []
    items = raw if isinstance(raw, list) else [raw]
    out = []
    for item in items:
        try:
            n = int(item)
        except (TypeError, ValueError):
            continue
        if 1 <= n <= message_count:
            out.append(n)
    return out


def validate(raw: dict[str, Any], message_count: int) -> dict[str, Any]:
    """Everything the model returned, checked and normalised.

    Anything that fails becomes null. The pipeline is explicitly biased
    towards an empty field over a wrong one: a null is a gap KARL can report,
    while a fabricated value is a gap it cannot see.
    """
    out: dict[str, Any] = {}

    for field in ("problem", "expected", "actual", "workaround", "prevention"):
        out[field] = _clean_text(raw.get(field))

    spec_in = raw.get("specification") or {}
    spec_out = {}
    for key in _SPEC_KEYS:
        spec_out[key] = _clean_text(spec_in.get(key), 1000) if isinstance(spec_in, dict) else None
    out["specification"] = spec_out

    changes = []
    for item in (raw.get("changes") or [])[:12]:
        if not isinstance(item, dict):
            continue
        description = _clean_text(item.get("description"), 1000)
        if not description:
            continue
        change_type = str(item.get("type") or "unknown").strip().lower()
        changes.append({
            "description": description,
            "type": change_type if change_type in _CHANGE_TYPES else "unknown",
            "when": _clean_text(item.get("when"), 200),
        })
    out["changes"] = changes

    tests = []
    for item in (raw.get("tests") or [])[:12]:
        if not isinstance(item, dict):
            continue
        name = _clean_text(item.get("test"), 500)
        if not name:
            continue
        verdict = str(item.get("verdict") or "").strip().upper()
        tests.append({
            "test": name,
            "result": _clean_text(item.get("result"), 1000),
            "verdict": verdict if verdict in _VERDICTS else "INCONCLUSIVE",
        })
    out["tests"] = tests

    rejected = []
    for item in (raw.get("rejected_causes") or [])[:12]:
        if not isinstance(item, dict):
            continue
        cause = _clean_text(item.get("cause"), 500)
        if not cause:
            continue
        rejected.append({"cause": cause, "why": _clean_text(item.get("why"), 1000)})
    out["rejected_causes"] = rejected

    rc_in = raw.get("root_cause")
    rc_in = rc_in if isinstance(rc_in, dict) else {}
    out["root_cause"] = {
        "cause": _clean_text(rc_in.get("cause"), 2000),
        "mechanism": _clean_text(rc_in.get("mechanism"), 2000),
        "trigger": _clean_text(rc_in.get("trigger"), 1000),
    }

    evidence_in = raw.get("_evidence")
    evidence_in = evidence_in if isinstance(evidence_in, dict) else {}
    out["_evidence"] = {
        k: _valid_citations(v, message_count) for k, v in evidence_in.items()
    }

    return out


def numbered_thread(ticket: LegacyTicket) -> tuple[str, int]:
    """Number the messages so the model can cite them and we can check."""
    lines = []
    n = 0
    for m in ticket.messages:
        body = (m.get("body") or "").strip()
        if not body:
            continue
        n += 1
        who = str(m.get("author_role") or "unknown").upper()
        lines.append(f"[{n}] {who} ({m.get('ts') or '?'}): {body}")
    if ticket.resolution and ticket.resolution.get("resolution"):
        n += 1
        lines.append(f"[{n}] RESOLUTION: {ticket.resolution['resolution']}")
    return "\n".join(lines), n


def extract(ticket: LegacyTicket) -> dict[str, Any] | None:
    """Returns validated KT fields, or None if the model gave nothing usable."""
    settings = get_settings()
    llm = get_llm_service()
    if not llm.reachable:
        log.warning("no local model reachable — cannot extract %s", ticket.source_ref)
        return None

    thread, message_count = numbered_thread(ticket)
    if message_count == 0:
        return None

    prompt = build_extraction_prompt(ticket.header, thread)
    # The extraction schema is large — 8 specification cells, three lists, a
    # nested root cause and an evidence map. At the chat default of ~600-1200
    # tokens the JSON truncates mid-object and every field late in the schema
    # (root_cause among them) comes back empty, which looks exactly like the
    # model failing to find one. This is the single most important number in
    # the file.
    raw = llm.generate_json(prompt, [], system=EXTRACTION_SYSTEM,
                            max_tokens=settings.extraction_max_tokens)
    if not raw:
        log.warning("extraction produced no parseable JSON for %s", ticket.source_ref)
        return None

    result = validate(raw, message_count)
    result["_extractor_version"] = settings.extractor_version
    result["_message_count"] = message_count
    return result
