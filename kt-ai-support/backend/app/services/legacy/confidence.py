"""Confidence and trust, computed from structure — never self-reported.

An LLM saying `"confidence": 0.97` about its own extraction is not evidence.
It is the model's impression of its own fluency, it is not calibrated, and it
is uniformly high whether the thread contained a root cause or the model
invented one.

So the model here only extracts *text*. Every number is computed from things
that can be checked afterwards by a human reading the same thread:

    was there a closure code, and was it a verifying one?
    did the extractor point at a message that actually exists?
    is there a question/answer pair that reads as a test?
    did the thread state what was UNaffected?

That makes "confidence 0.9" a claim someone can audit, which is the only kind
worth putting in front of a support engineer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Where a record lands, and how much retrieval should trust it.
TRUST = {
    "new_kt": 1.00,             # a human filled the KT form
    "legacy_kb": 1.00,          # curated, published, no extraction
    "legacy_verified": 0.90,    # closure code says the cause was established
    "legacy_extracted": 0.65,   # a model read a thread and found a cause
    "legacy_raw_only": 0.30,    # imported for counting; never chunked
}


@dataclass
class Scored:
    source_type: str
    source_trust: float
    root_cause_confidence: str | None      # SUSPECTED..CONFIRMED
    field_confidence: dict[str, float]
    notes: list[str]


def _evidence_backed(extracted: dict[str, Any], field: str,
                     message_ids: set[str]) -> bool:
    """Did the extractor cite a message that genuinely exists?

    The cheapest and most effective hallucination check available: a model
    inventing a root cause usually also invents the message id it came from.
    """
    entry = (extracted.get("_evidence") or {}).get(field)
    if not entry:
        return False
    refs = entry if isinstance(entry, list) else [entry]
    return any(str(r) in message_ids for r in refs)


def score(mapped: dict[str, Any], extracted: dict[str, Any] | None,
          message_ids: set[str] | None = None) -> Scored:
    notes: list[str] = []
    field_confidence: dict[str, float] = {}
    message_ids = message_ids or set()

    legacy = (mapped.get("metadata_") or {}).get("legacy", {})
    verified_closure = bool(legacy.get("verified_closure"))

    # --- nothing extracted: a row, and only a row --------------------------
    if not extracted:
        return Scored(
            source_type="legacy_raw_only",
            source_trust=TRUST["legacy_raw_only"],
            root_cause_confidence=None,
            field_confidence={},
            notes=["not extracted — counted in totals, excluded from retrieval"],
        )

    has_cause = bool((extracted.get("root_cause") or {}).get("cause"))
    has_resolution = bool(mapped.get("resolution_summary"))
    tests = extracted.get("tests") or []
    conclusive_test = any(
        str(t.get("result", "")).upper() in ("CONFIRMS", "REJECTS") for t in tests
    )

    # --- root-cause confidence: a ladder climbed on evidence ---------------
    if has_cause and verified_closure and conclusive_test:
        rc = "CONFIRMED"
        notes.append("verified closure code and a conclusive test in the thread")
    elif has_cause and verified_closure:
        rc = "HIGH_CONFIDENCE"
        notes.append("closure code indicates the cause was established")
    elif has_cause and (conclusive_test or has_resolution):
        rc = "PROBABLE"
        notes.append("a cause was stated and something was done about it")
    elif has_cause:
        rc = "SUSPECTED"
        notes.append("a cause appears in the thread but nothing confirms it")
    else:
        rc = None
        notes.append("no root cause found in the thread")

    # A CONFIRMED cause that cites no real message is a model being fluent.
    if rc == "CONFIRMED" and message_ids and not _evidence_backed(
            extracted, "root_cause", message_ids):
        rc = "HIGH_CONFIDENCE"
        notes.append("downgraded: the cited evidence does not match any message id")

    # --- per-field confidence ----------------------------------------------
    for field in ("problem", "expected", "actual", "root_cause", "resolution"):
        value = extracted.get(field)
        if not value or (isinstance(value, dict) and not value.get("value")):
            field_confidence[field] = 0.0
            continue
        base = 0.5
        if _evidence_backed(extracted, field, message_ids):
            base += 0.3
        if field in ("root_cause", "resolution") and verified_closure:
            base += 0.2
        field_confidence[field] = round(min(1.0, base), 2)

    # IS / IS NOT is scored separately: it is the field the model is most
    # tempted to invent, and the one retrieval leans on hardest.
    spec = extracted.get("specification") or {}
    stated = sum(1 for v in spec.values() if v)
    field_confidence["specification"] = round(min(1.0, stated / 8), 2)
    if stated == 0:
        notes.append("no IS / IS NOT in the thread — left null rather than inferred")

    source_type = "legacy_verified" if (verified_closure and has_cause) else "legacy_extracted"

    return Scored(
        source_type=source_type,
        source_trust=TRUST[source_type],
        root_cause_confidence=rc,
        field_confidence=field_confidence,
        notes=notes,
    )
