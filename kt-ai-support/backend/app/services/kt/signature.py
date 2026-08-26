"""Error-signature normalisation.

Retrieval fails if two records of the same incident differ only by a UUID or
a timestamp. This strips volatile tokens but PRESERVES well-known constants
(the link-local metadata address, loopback, standard ports), which carry real
signal — "connection refused to 127.0.0.1" and "connection refused to
10.4.2.9" are different faults.
"""

from __future__ import annotations

import re

# Protected before scrubbing and restored after. These are not noise: an
# error naming 169.254.169.254 is specifically about metadata.
PRESERVE_LITERALS = ("169.254.169.254", "127.0.0.1", "0.0.0.0", "255.255.255.255")

_RULES: list[tuple[re.Pattern[str], str]] = [
    # ISO and common timestamps first — they embed the numbers later rules eat
    (re.compile(r"\d{4}-\d{2}-\d{2}[t ]\d{2}:\d{2}(:\d{2})?(\.\d+)?(z|[+-]\d{2}:?\d{2})?"), "<ts>"),
    (re.compile(r"\b\d{2}:\d{2}:\d{2}(\.\d+)?\b"), "<ts>"),
    # request ids before UUIDs: req-<uuid> would otherwise split in two
    (re.compile(r"\breq-[0-9a-f-]+\b"), "<reqid>"),
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"), "<uuid>"),
    (re.compile(r"\b[0-9a-f]{16,}\b"), "<hex>"),
    (re.compile(r"\b\d{1,3}(\.\d{1,3}){3}(/\d{1,2})?(:\d{1,5})?\b"), "<ip>"),
    (re.compile(r"\b([0-9a-f]{2}:){5}[0-9a-f]{2}\b"), "<mac>"),
    (re.compile(r"(/[\w.-]+){3,}"), "<path>"),
    # Quantities with a unit: "timeout after 30s" and "timeout after 45s" are
    # one fault. The bare-number rule below cannot catch these because there
    # is no word boundary between the digits and the unit.
    (re.compile(r"\b\d+(?:\.\d+)?\s*(?:ms|s|m|h|d|us|ns|kb|mb|gb|tb|kib|mib|gib|%)\b"), "<qty>"),
    (re.compile(r"\b\d+\b"), "<n>"),
    (re.compile(r"[\"'`\[\](){}]"), " "),
    (re.compile(r"\s+"), " "),
]


def normalize_error_signature(text: str | None) -> str:
    if not text:
        return ""

    value = str(text)
    for index, literal in enumerate(PRESERVE_LITERALS):
        value = value.replace(literal, f" KEEP{index} ")

    value = value.lower()
    for pattern, replacement in _RULES:
        value = pattern.sub(replacement, value)

    for index, literal in enumerate(PRESERVE_LITERALS):
        value = value.replace(f" keep{index} ", literal).replace(f" KEEP{index} ", literal)

    return value.strip()


def signature_similarity(left: str | None, right: str | None) -> float:
    """0.0-1.0 over normalised signatures.

    Exact match is the case that matters and is checked first; the token
    overlap below it is what catches the same fault reported with a slightly
    different message.
    """
    a, b = normalize_error_signature(left), normalize_error_signature(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:
        return 0.85

    ta = {t for t in a.split() if len(t) > 2}
    tb = {t for t in b.split() if len(t) > 2}
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)
