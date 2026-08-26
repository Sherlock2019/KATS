"""DiagnosticReasoningService — §23-§25, §28, §45.

The pipeline is deliberately mostly deterministic. Retrieval, grouping,
ranking, extracting confirmed causes and rejected hypotheses, and computing
diagnostic value are all done in Python against the relational record; the
model is asked to reason over an evidence pack that is already correct.

That ordering matters. A small local model asked to both find and reason
will do neither well, and every number it invents is one nobody can check.
Here the citations, the similar cases and the diagnostic-value ranking are
computed facts — if the model is unavailable or returns nothing parseable,
the endpoint still answers, from those facts alone.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    DiagnosticTest,
    KTHypothesis,
    RootCause,
    SupportTicket,
    TicketEvidence,
)
from app.models.enums import Direction, HypothesisStatus, RootCauseConfidence, TestResult
from app.prompts.diagnostic import (
    DIAGNOSE_SYSTEM,
    NEXT_ACTION_SYSTEM,
    NEXT_QUESTION_SYSTEM,
)
from app.services.kt.analysis import KTAnalysisService
from app.services.kt.quality import KnowledgeQualityService
from app.services.llm.service import get_llm_service
from app.services.rag.retrieval import Candidate, RetrievalService

log = logging.getLogger("kt.diagnostics")

_RISK_COST = {"low": 1.0, "medium": 2.0, "high": 4.0, None: 1.5}


@dataclass
class EvidencePack:
    """Everything handed to the model, and everything cited back."""

    ticket: SupportTicket
    results: list[Candidate]
    similar_cases: list[dict]
    context_blocks: list[str]
    ticket_numbers: list[str]
    retrieval_meta: dict


class DiagnosticReasoningService:

    # ------------------------------------------------------------------
    # evidence assembly — steps 1-8 of §23
    # ------------------------------------------------------------------
    @classmethod
    def assemble(cls, db: Session, ticket: SupportTicket, question: str,
                 top_k: int = 8) -> EvidencePack:
        ctx = RetrievalService.context_from_ticket(ticket, question)
        found = RetrievalService.search(db, ctx, top_k=top_k * 3)
        results: list[Candidate] = found["results"]

        # Group by ticket (§23 step 4) — several chunks of one case are one
        # piece of evidence, not five, and ranking chunks would let a verbose
        # ticket crowd out three relevant ones.
        by_ticket: dict[uuid.UUID, list[Candidate]] = {}
        for candidate in results:
            by_ticket.setdefault(candidate.ticket_id, []).append(candidate)

        ranked_tickets = sorted(
            by_ticket.items(),
            key=lambda kv: max(c.final for c in kv[1]),
            reverse=True,
        )[:top_k]

        similar_cases = []
        context_blocks = []
        cited: list[str] = []

        for ticket_id, chunks in ranked_tickets:
            source = db.get(SupportTicket, ticket_id)
            if source is None:
                continue
            best = max(chunks, key=lambda c: c.final)
            cited.append(source.ticket_number)

            confirmed = [rc for rc in source.root_causes
                         if rc.confidence == RootCauseConfidence.CONFIRMED]
            rejected = [h for h in source.hypotheses if h.status == HypothesisStatus.REJECTED]
            good_tests = [t for t in source.tests
                          if t.result_status in (TestResult.CONFIRMS, TestResult.REJECTS)]

            similar_cases.append({
                "ticket_id": source.id,
                "ticket_number": source.ticket_number,
                "title": source.title,
                "similarity": round(best.final, 4),
                "match_reasons": best.why,
                "root_cause": confirmed[0].cause if confirmed else (
                    source.root_causes[0].cause if source.root_causes else None),
                "root_cause_status": source.root_cause_status,
                "resolution_summary": source.resolution_summary,
                "workaround": source.workaround,
                # The most under-used knowledge in any ticket system: what
                # somebody already ruled out, and how.
                "rejected_causes": [
                    f"{h.cause} — {h.reasoning or 'no reason recorded'}" for h in rejected
                ],
                "successful_tests": [
                    f"{t.test_name}: {t.actual_result or t.result_status}" for t in good_tests
                ],
                "knowledge_quality_score": float(source.knowledge_quality_score or 0),
            })

            body = "\n\n".join(
                f"[{c.chunk_type}] {c.title or ''}\n{c.content[:900]}" for c in chunks[:4]
            )
            context_blocks.append(
                f"=== {source.ticket_number}: {source.title} "
                f"(root cause status: {source.root_cause_status}, "
                f"quality {float(source.knowledge_quality_score or 0):.2f}) ===\n{body}"
            )

        return EvidencePack(
            ticket=ticket, results=results, similar_cases=similar_cases,
            context_blocks=context_blocks, ticket_numbers=cited,
            retrieval_meta={
                "weights": found["weights"],
                "detected_metadata": found["detected_metadata"],
                "embedding_model": found["embedding_model"],
                "embed_mode": found["embed_mode"],
                "candidates_considered": found["candidates_considered"],
            },
        )

    # ------------------------------------------------------------------
    @staticmethod
    def describe_current_ticket(ticket: SupportTicket) -> str:
        profile = KTAnalysisService.build_profile(ticket.specifications)
        live = [h for h in ticket.hypotheses if h.status != HypothesisStatus.REJECTED]
        rejected = [h for h in ticket.hypotheses if h.status == HypothesisStatus.REJECTED]

        lines = [
            f"CURRENT INCIDENT {ticket.ticket_number}: {ticket.title}",
            f"Product: {ticket.product or '?'} {ticket.product_version or ''}".rstrip(),
            f"Component: {ticket.component or '?'}   Environment: {ticket.environment or '?'}",
            f"Error: {ticket.error_code or ''} {ticket.error_message or ''}".strip(),
            f"Expected: {ticket.expected_behavior or '(not stated)'}",
            f"Actual: {ticket.actual_behavior or '(not stated)'}",
            "",
            "KT specification:",
            KTAnalysisService.to_text(profile) or "(nothing specified yet)",
        ]
        if ticket.distinctions:
            lines += ["", "Distinctions:"] + [f"- {d.distinction}" for d in ticket.distinctions]
        if ticket.changes:
            lines += ["", "Changes:"] + [
                f"- [{c.change_type}] {c.description}" for c in ticket.changes
            ]
        if live:
            lines += ["", "Open hypotheses:"] + [f"- {h.cause} ({h.status})" for h in live]
        if rejected:
            lines += ["", "Already ruled out on THIS ticket:"] + [
                f"- {h.cause} — {h.reasoning or 'no reason recorded'}" for h in rejected
            ]

        evidence_for = [e for e in ticket.evidence if e.direction == Direction.FOR]
        evidence_against = [e for e in ticket.evidence if e.direction == Direction.AGAINST]
        if evidence_for or evidence_against:
            lines += ["", "Evidence on this ticket:"]
            lines += [f"- FOR: {e.content[:200]}" for e in evidence_for]
            lines += [f"- AGAINST: {e.content[:200]}" for e in evidence_against]

        done = [t for t in ticket.tests if t.result_status != TestResult.NOT_RUN]
        if done:
            lines += ["", "Tests already run on THIS ticket:"] + [
                f"- {t.test_name} -> {t.result_status} ({t.actual_result or 'no detail'})"
                for t in done
            ]
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # §28 diagnostic value
    # ------------------------------------------------------------------
    @staticmethod
    def diagnostic_value(probability: float, discriminates: int, risk: str | None,
                         minutes: int | None, reversible: bool) -> float:
        """probability x information gain x discrimination / (cost + risk + time)

        Information gain peaks at p=0.5: a test on a candidate you are already
        95% sure about teaches you almost nothing either way. That is why the
        highest-probability hypothesis is often not the one to test next.
        """
        information_gain = 1.0 - abs(probability - 0.5) * 2      # 1.0 at p=0.5, 0 at 0 or 1
        discrimination = min(1.0, 0.4 + 0.3 * max(0, discriminates - 1))
        cost = _RISK_COST.get(risk, 1.5) + (minutes or 15) / 30.0
        if not reversible:
            # Not a tie-breaker: a reversible test should win against an
            # irreversible one even when the irreversible one is far more
            # likely to settle it.
            cost *= 3.0
        return round(max(probability, 0.05) * information_gain * discrimination / cost, 4)

    @classmethod
    def rank_pending_tests(cls, ticket: SupportTicket) -> list[dict]:
        hypotheses = {h.id: h for h in ticket.hypotheses}
        out = []
        for test in ticket.tests:
            if test.result_status != TestResult.NOT_RUN:
                continue
            hypothesis = hypotheses.get(test.hypothesis_id)
            probability = float(hypothesis.probability_score or 0.5) if hypothesis else 0.4
            discriminates = sum(
                1 for h in ticket.hypotheses if h.status in
                (HypothesisStatus.PROPOSED, HypothesisStatus.TESTING, HypothesisStatus.SUPPORTED)
            )
            out.append({
                "test": test.test_name,
                "purpose": test.objective or "",
                "expected_if_true": test.expected_result_if_true or "",
                "expected_if_false": test.expected_result_if_false or "",
                "risk": test.risk_level or "low",
                "discriminates_between": [hypothesis.cause] if hypothesis else [],
                "diagnostic_value": cls.diagnostic_value(
                    probability, discriminates, test.risk_level,
                    test.estimated_minutes, test.reversible,
                ),
            })
        return sorted(out, key=lambda t: t["diagnostic_value"] or 0, reverse=True)

    # ------------------------------------------------------------------
    # §23 / §24 — diagnose
    # ------------------------------------------------------------------
    @classmethod
    def diagnose(cls, db: Session, ticket: SupportTicket, question: str,
                 top_k: int = 8) -> dict:
        started = time.time()
        pack = cls.assemble(db, ticket, question, top_k)
        llm = get_llm_service()

        completeness = KnowledgeQualityService.completeness(ticket)
        gaps = [s["missing_prompt"] for s in completeness["sections"] if s["missing_prompt"]]

        warnings: list[str] = []
        if pack.retrieval_meta.get("embed_mode") == "hash-fallback":
            warnings.append(
                "Retrieval ran on the lexical hash fallback — no embedding model was "
                "reachable, so semantically similar cases may be missing."
            )
        if not pack.similar_cases:
            warnings.append("No historical case matched. Everything below comes from this ticket alone.")

        # Deterministic answer first. If the model is unreachable or unparseable
        # this is still a correct, useful response rather than an error page.
        deterministic = cls._fallback_answer(ticket, pack, gaps)

        if not llm.reachable:
            deterministic["warnings"] = warnings + [
                f"No local model reachable ({llm.status().get('detail') or 'unknown'}) — "
                f"returning retrieval results without model reasoning."
            ]
            deterministic["latency_ms"] = int((time.time() - started) * 1000)
            return deterministic

        prompt = (
            f"{cls.describe_current_ticket(ticket)}\n\n"
            f"Support engineer's question: {question}\n\n"
            f"Answer as the JSON object specified. Ground every claim in the retrieved "
            f"evidence and cite ticket ids from it."
        )
        parsed = llm.generate_json(prompt, pack.context_blocks, system=DIAGNOSE_SYSTEM)

        if not parsed:
            deterministic["warnings"] = warnings + [
                "The local model did not return parseable JSON; showing retrieval results only."
            ]
            deterministic["latency_ms"] = int((time.time() - started) * 1000)
            return deterministic

        # §24 — the model does not get to declare a confirmation. Only this
        # ticket's own CONFIRMED root cause can populate that field, whatever
        # the model wrote.
        confirmed = next(
            (rc.cause for rc in ticket.root_causes
             if rc.confidence == RootCauseConfidence.CONFIRMED), None
        )
        if parsed.get("confirmed_root_cause") and not confirmed:
            warnings.append(
                "The model proposed a confirmed root cause; overridden to null because "
                "no confirmed cause is recorded on this ticket."
            )

        # Strip invented citations. A plausible-looking INC number that does
        # not exist is worse than none.
        allowed = set(pack.ticket_numbers)
        causes = []
        for raw in parsed.get("likely_causes") or []:
            if not isinstance(raw, dict) or not raw.get("cause"):
                continue
            sources = [s for s in (raw.get("source_tickets") or []) if s in allowed]
            invented = [s for s in (raw.get("source_tickets") or []) if s not in allowed]
            if invented:
                warnings.append(f"Dropped citation(s) not in the evidence: {', '.join(invented)}")
            try:
                confidence = float(raw.get("confidence", 0.4))
            except (TypeError, ValueError):
                confidence = 0.4
            causes.append({
                "cause": str(raw["cause"]),
                "confidence": max(0.0, min(1.0, confidence)),
                "reason": str(raw.get("reason") or ""),
                "supporting_evidence": [str(x) for x in (raw.get("supporting_evidence") or [])],
                "contradicting_evidence": [str(x) for x in (raw.get("contradicting_evidence") or [])],
                "source_tickets": sources,
            })

        tests = []
        for raw in parsed.get("recommended_tests") or []:
            if not isinstance(raw, dict) or not raw.get("test"):
                continue
            tests.append({
                "test": str(raw["test"]),
                "purpose": str(raw.get("purpose") or ""),
                "expected_if_true": str(raw.get("expected_if_true") or ""),
                "expected_if_false": str(raw.get("expected_if_false") or ""),
                "risk": str(raw.get("risk") or "low"),
                "discriminates_between": [str(x) for x in (raw.get("discriminates_between") or [])],
                "diagnostic_value": None,
            })
        # Tests already written on the ticket are ranked by computed value and
        # come first — they are real, and their value is arithmetic not opinion.
        tests = cls.rank_pending_tests(ticket) + tests

        return {
            "problem_understanding": str(parsed.get("problem_understanding") or ""),
            "missing_information": [str(x) for x in (parsed.get("missing_information") or [])] or gaps,
            "similar_cases": pack.similar_cases,
            "likely_causes": causes or deterministic["likely_causes"],
            "recommended_tests": tests or deterministic["recommended_tests"],
            "possible_workaround": parsed.get("possible_workaround") or deterministic.get("possible_workaround"),
            "confirmed_root_cause": confirmed,
            "model": llm.model,
            "latency_ms": int((time.time() - started) * 1000),
            "grounded_in": pack.ticket_numbers,
            "warnings": warnings,
        }

    @staticmethod
    def _fallback_answer(ticket: SupportTicket, pack: EvidencePack, gaps: list[str]) -> dict:
        """A real answer built only from the database.

        Used when no model is reachable and as the floor under every model
        response. Everything here is a fact someone recorded.
        """
        causes = []
        for case in pack.similar_cases[:3]:
            if not case["root_cause"]:
                continue
            causes.append({
                "cause": case["root_cause"],
                # Capped: a historical cause is a lead about this incident, and
                # nothing in this path has tested it here.
                "confidence": round(min(0.6, case["similarity"] * 2), 2),
                "reason": (
                    f"{case['ticket_number']} matched on: "
                    + ("; ".join(case["match_reasons"]) or "similar text")
                    + f". Its cause was {case['root_cause_status']}."
                ),
                "supporting_evidence": case["match_reasons"],
                "contradicting_evidence": [],
                "source_tickets": [case["ticket_number"]],
            })

        workaround = next(
            (c["workaround"] for c in pack.similar_cases if c.get("workaround")), None
        )
        confirmed = next(
            (rc.cause for rc in ticket.root_causes
             if rc.confidence == RootCauseConfidence.CONFIRMED), None
        )

        return {
            "problem_understanding": (
                f"{ticket.title}. Expected: {ticket.expected_behavior or 'not stated'}. "
                f"Actual: {ticket.actual_behavior or 'not stated'}."
            ),
            "missing_information": gaps,
            "similar_cases": pack.similar_cases,
            "likely_causes": causes,
            "recommended_tests": DiagnosticReasoningService.rank_pending_tests(ticket),
            "possible_workaround": workaround,
            "confirmed_root_cause": confirmed,
            "model": None,
            "latency_ms": 0,
            "grounded_in": pack.ticket_numbers,
            "warnings": [],
        }

    # ------------------------------------------------------------------
    # §45 — the next question
    # ------------------------------------------------------------------
    @classmethod
    def next_question(cls, db: Session, ticket: SupportTicket) -> dict:
        started = time.time()
        profile = KTAnalysisService.build_profile(ticket.specifications)
        gaps = KTAnalysisService.gaps(profile)

        if not gaps:
            return {
                "question": "The KT specification is complete. Which candidate cause do you "
                            "want to test first?",
                "dimension": None, "side": None,
                "why_it_matters": "Nothing is missing from the specification.",
                "current_gaps": [], "model": None,
                "latency_ms": int((time.time() - started) * 1000),
            }

        # The deterministic pick is already good: highest-priority gap, which
        # is a missing IS NOT next to a stated IS. The model only sharpens the
        # wording using the specifics of this ticket.
        best = gaps[0]
        llm = get_llm_service()
        gap_labels = [f"{g['dimension']} {g['side']} unanswered" for g in gaps]

        if not llm.reachable:
            return {
                "question": best["question"], "dimension": best["dimension"],
                "side": best["side"],
                "why_it_matters": (
                    "Without this there is no comparison to eliminate against — "
                    "a cause has to explain the IS and the IS NOT."
                ),
                "current_gaps": gap_labels, "model": None,
                "latency_ms": int((time.time() - started) * 1000),
            }

        prompt = (
            f"{cls.describe_current_ticket(ticket)}\n\n"
            f"Unanswered parts of the specification, most valuable first:\n"
            + "\n".join(f"- {g['dimension']} {g['side']}: {g['question']}" for g in gaps[:4])
            + "\n\nAsk the single most useful question, phrased for this specific incident "
              "(name the actual cluster, product or component involved)."
        )
        parsed = llm.generate_json(prompt, [], system=NEXT_QUESTION_SYSTEM) or {}

        return {
            "question": str(parsed.get("question") or best["question"]),
            "dimension": parsed.get("dimension") or best["dimension"],
            "side": parsed.get("side") or best["side"],
            "why_it_matters": str(
                parsed.get("why_it_matters")
                or "It establishes the comparison the analysis eliminates against."
            ),
            "current_gaps": gap_labels,
            "model": llm.model,
            "latency_ms": int((time.time() - started) * 1000),
        }

    # ------------------------------------------------------------------
    # §28 — the next action
    # ------------------------------------------------------------------
    @classmethod
    def next_action(cls, db: Session, ticket: SupportTicket) -> dict:
        started = time.time()
        ranked = cls.rank_pending_tests(ticket)
        llm = get_llm_service()

        profile = KTAnalysisService.build_profile(ticket.specifications)
        gaps = KTAnalysisService.gaps(profile)

        # Shortcut before any model call, and before any test: if the
        # specification has no contrast, the cheapest next action is not a
        # test at all — it is one question. The cheapest test is the one you
        # never have to run.
        if not profile.has_contrast and gaps:
            return {
                "recommended_action": f"Answer this first: {gaps[0]['question']}",
                "rationale": (
                    "No dimension has both an IS and an IS NOT, so there is nothing to "
                    "eliminate against yet. Establishing one comparison will rule out more "
                    "candidates than any test you could run right now."
                ),
                "candidates": ranked,
                "model": None,
                "latency_ms": int((time.time() - started) * 1000),
            }

        if ranked and not llm.reachable:
            best = ranked[0]
            return {
                "recommended_action": best["test"],
                "rationale": (
                    f"Highest diagnostic value ({best['diagnostic_value']}): "
                    f"{best['risk']} risk, and it discriminates between the open candidates."
                ),
                "candidates": ranked, "model": None,
                "latency_ms": int((time.time() - started) * 1000),
            }

        if not llm.reachable:
            return {
                "recommended_action": "Propose a candidate cause and a test that could refute it.",
                "rationale": "No pending tests are recorded and no local model is reachable.",
                "candidates": [], "model": None,
                "latency_ms": int((time.time() - started) * 1000),
            }

        pack = cls.assemble(db, ticket, "What is the safest next diagnostic action?", top_k=5)
        prompt = (
            f"{cls.describe_current_ticket(ticket)}\n\n"
            + (
                "Pending tests already recorded, with their computed diagnostic value:\n"
                + "\n".join(
                    f"- {t['test']} (value {t['diagnostic_value']}, risk {t['risk']})"
                    for t in ranked
                )
                if ranked else "No tests have been recorded yet."
            )
            + "\n\nRecommend the safest next action that removes the most uncertainty."
        )
        parsed = llm.generate_json(prompt, pack.context_blocks, system=NEXT_ACTION_SYSTEM) or {}

        candidates = ranked + [
            {
                "test": str(c.get("test") or ""),
                "purpose": str(c.get("purpose") or ""),
                "expected_if_true": str(c.get("expected_if_true") or ""),
                "expected_if_false": str(c.get("expected_if_false") or ""),
                "risk": str(c.get("risk") or "low"),
                "discriminates_between": [str(x) for x in (c.get("discriminates_between") or [])],
                "diagnostic_value": None,
            }
            for c in (parsed.get("candidates") or [])
            if isinstance(c, dict) and c.get("test")
        ]

        return {
            "recommended_action": str(
                parsed.get("recommended_action")
                or (ranked[0]["test"] if ranked else "Establish a comparable unaffected case.")
            ),
            "rationale": str(parsed.get("rationale") or ""),
            "candidates": candidates,
            "model": llm.model,
            "latency_ms": int((time.time() - started) * 1000),
        }
