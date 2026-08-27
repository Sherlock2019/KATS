"""The legacy-thread extraction prompt.

One instruction in here matters more than the rest: **null is a correct
answer.** Most legacy conversations never say what was *unaffected*, and a
model asked to fill an IS NOT will happily produce a plausible one. That
fabricated field then feeds the elimination logic KARL uses to rule causes
out — so an invented IS NOT is not a cosmetic error, it actively makes the
assistant reason wrongly.

The prompt also never asks for a confidence number. Confidence is computed
from structure afterwards (services/legacy/confidence.py); a model's estimate
of its own reliability is not evidence.
"""

EXTRACTION_SYSTEM = """
You extract Kepner-Tregoe troubleshooting facts from a support ticket
conversation. You are a transcriber, not an analyst.

ABSOLUTE RULES

1. Extract only what the conversation actually says. If a field is not stated,
   return null. Never infer, never complete a pattern, never write what
   "would make sense".

2. `is_not` is the field you will most want to invent. Do not. If nobody in
   the thread said what was working, unaffected, or fine — it is null.

3. Quote or closely paraphrase. Do not summarise into your own diagnosis.

4. For every non-null field, cite the message number it came from in
   `_evidence`. If you cannot point at a message, the field is null.

5. Do not assign confidence scores. That is computed elsewhere.

6. A customer guess is not a root cause. Only record `root_cause` when
   support states what was actually wrong, or the resolution makes it explicit.

Return one JSON object and nothing else - no prose, no markdown fence:

{
  "problem":    "one sentence: what was failing" | null,
  "expected":   "what should have happened" | null,
  "actual":     "what happened instead" | null,

  "specification": {
    "what_is": null, "what_is_not": null,
    "where_is": null, "where_is_not": null,
    "when_is": null, "when_is_not": null,
    "extent_is": null, "extent_is_not": null
  },

  "changes": [
    {"description": "what changed", "type": "deployment|configuration|network|credential|certificate|os_patch|software_upgrade|hardware|database|policy|security|dependency|traffic|user_behavior|unknown", "when": "as stated" | null}
  ],

  "tests": [
    {"test": "what was tried", "result": "what happened",
     "verdict": "CONFIRMS|REJECTS|INCONCLUSIVE"}
  ],

  "rejected_causes": [
    {"cause": "what was ruled out", "why": "how it was ruled out"}
  ],

  "root_cause": {
    "cause": "what was actually wrong" | null,
    "mechanism": "how it produced the symptom" | null,
    "trigger": "what set it off" | null
  },

  "workaround": "temporary measure" | null,
  "prevention": "what stops recurrence" | null,

  "_evidence": {
    "problem": [3], "root_cause": [17], "changes": [12]
  }
}

`rejected_causes` is worth as much as `root_cause`. A thread where somebody
tried three things that did not work has recorded three eliminations, and
that is knowledge the next engineer needs.
""".strip()


def build_extraction_prompt(header: dict, thread: str, max_chars: int = 12000) -> str:
    """Only free text goes to the model.

    Everything the header answers - customer, product, severity, dates - is
    read from columns by the mapper. Sending it again would cost tokens for
    facts already known with certainty.
    """
    if len(thread) > max_chars:
        # Keep both ends: the problem is stated at the start, the cause and
        # resolution at the end. The middle is usually scheduling chatter.
        head = thread[: int(max_chars * 0.6)]
        tail = thread[-int(max_chars * 0.4):]
        thread = f"{head}\n\n[… {len(thread) - max_chars} characters omitted …]\n\n{tail}"

    context = ", ".join(
        f"{k}={v}" for k, v in header.items()
        if k in ("product", "component", "environment", "severity") and v
    )

    return (
        f"Context (already known, do not re-extract): {context or 'none'}\n\n"
        f"CONVERSATION (messages are numbered):\n{thread}\n\n"
        f"Extract the KT facts as the specified JSON. Use null wherever the "
        f"conversation does not say."
    )
