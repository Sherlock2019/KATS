"""KnowledgeBuilderService — §38, §39.

Turns the relational record into retrievable chunks. Two rules govern it:

1.  One chunk per meaning, not one per ticket. A ticket's WHEN block and its
    root cause answer different questions; fused into one vector they
    retrieve badly for both.

2.  Only re-embed what changed. Every chunk carries a content hash; the
    builder regenerates all of them on every ticket change but hands the
    embedder only the ones whose hash moved. Embedding is the slow step,
    hashing is free.

The chunks are derived data. If this table and `support_tickets` ever
disagree, the ticket is right and the chunks get rebuilt.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from app.models.enums import (
    ChunkType,
    Direction,
    HypothesisStatus,
    RootCauseConfidence,
    TestResult,
)
from app.services.kt.analysis import KTAnalysisService
from app.services.kt.quality import KnowledgeQualityService


@dataclass
class BuiltChunk:
    chunk_type: str
    title: str
    content: str
    metadata: dict = field(default_factory=dict)
    quality_score: float = 0.0
    confidence_score: float = 0.0

    @property
    def content_hash(self) -> str:
        payload = f"{self.chunk_type}\x00{self.title}\x00{self.content}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# Confidence a chunk carries into retrieval, by the confidence of the thing
# it describes. A REJECTED_HYPOTHESIS is deliberately high: knowing something
# was ruled out, and how, is reliable knowledge even though the hypothesis
# was wrong.
_CONFIDENCE = {
    RootCauseConfidence.CONFIRMED: 1.0,
    RootCauseConfidence.HIGH_CONFIDENCE: 0.75,
    RootCauseConfidence.PROBABLE: 0.5,
    RootCauseConfidence.SUSPECTED: 0.25,
}


class KnowledgeBuilderService:
    @staticmethod
    def build_metadata(ticket, chunk_type: str, quality: float) -> dict:
        """§16 — denormalised onto every chunk.

        Filters must not need a join, and a chunk exported on its own should
        still be interpretable.
        """
        return {
            "ticket_number": ticket.ticket_number,
            "product": ticket.product,
            "product_version": ticket.product_version,
            "service": ticket.service,
            "component": ticket.component,
            "subcomponent": ticket.subcomponent,
            "environment": ticket.environment,
            "environment_type": ticket.environment_type,
            "cloud": ticket.cloud_provider,
            "region": ticket.region,
            "cluster": ticket.cluster,
            "severity": ticket.severity,
            "priority": ticket.priority,
            "status": ticket.status,
            "root_cause_status": ticket.root_cause_status,
            "chunk_type": chunk_type,
            "error_code": ticket.error_code,
            "error_signature": ticket.error_signature_norm,
            "quality_score": quality,
        }

    @classmethod
    def build(cls, ticket) -> list[BuiltChunk]:
        quality, _ = KnowledgeQualityService.evaluate(ticket)
        meta = lambda ct: cls.build_metadata(ticket, ct, quality)  # noqa: E731

        chunks: list[BuiltChunk] = []

        def add(chunk_type, title, lines, confidence=0.5):
            body = "\n".join(str(x) for x in lines if x and str(x).strip())
            # Below this a chunk is a label with no content — it retrieves on
            # its own title and tells the reader nothing.
            if len(body.strip()) < 25:
                return
            chunks.append(
                BuiltChunk(
                    chunk_type=str(chunk_type), title=title, content=body,
                    metadata=meta(str(chunk_type)),
                    quality_score=quality, confidence_score=confidence,
                )
            )

        # --- PROBLEM ---------------------------------------------------------
        add(ChunkType.PROBLEM, ticket.title, [
            f"Problem: {ticket.title}",
            f"Summary: {ticket.problem_summary}" if ticket.problem_summary else "",
            f"Expected: {ticket.expected_behavior}" if ticket.expected_behavior else "",
            f"Actual: {ticket.actual_behavior}" if ticket.actual_behavior else "",
            f"Error code: {ticket.error_code}" if ticket.error_code else "",
        ], 0.6)

        # --- SYMPTOM ---------------------------------------------------------
        # Separate from PROBLEM: the raw error text is what an engineer pastes
        # in, and it should be matchable without the narrative around it.
        add(ChunkType.SYMPTOM, "Observed symptom", [
            f"Error code: {ticket.error_code}" if ticket.error_code else "",
            f"Error message: {ticket.error_message}" if ticket.error_message else "",
            f"Normalised signature: {ticket.error_signature_norm}"
            if ticket.error_signature_norm else "",
            f"Technical impact: {ticket.technical_impact}" if ticket.technical_impact else "",
        ], 0.6)

        # --- CONTEXT ---------------------------------------------------------
        add(ChunkType.CONTEXT, "Environment and context", [
            f"Product: {ticket.product} {ticket.product_version or ''}".strip()
            if ticket.product else "",
            f"Service: {ticket.service}" if ticket.service else "",
            f"Component: {ticket.component}"
            + (f" / {ticket.subcomponent}" if ticket.subcomponent else "")
            if ticket.component else "",
            f"Environment: {ticket.environment or ''} ({ticket.environment_type or 'unspecified'})",
            f"Cloud: {ticket.cloud_provider} {ticket.region or ''}".strip()
            if ticket.cloud_provider else "",
            f"Cluster: {ticket.cluster}" if ticket.cluster else "",
            f"Node: {ticket.node}" if ticket.node else "",
            f"Operating system: {ticket.operating_system}" if ticket.operating_system else "",
            f"Business impact: {ticket.business_impact}" if ticket.business_impact else "",
            f"Users affected: {ticket.users_affected}" if ticket.users_affected else "",
        ], 0.5)

        # --- KT_SPECIFICATION -------------------------------------------------
        # The highest-signal chunk in the store. Retrieval compares these
        # structurally as well as semantically — see RetrievalService.
        profile = KTAnalysisService.build_profile(ticket.specifications)
        kt_text = KTAnalysisService.to_text(profile)
        if kt_text:
            add(ChunkType.KT_SPECIFICATION, "KT specification (IS / IS NOT)", [
                kt_text,
                "" if profile.has_contrast else
                "(No dimension has both sides stated — nothing to eliminate against.)",
            ], 0.8 if profile.has_contrast else 0.4)

        # --- DISTINCTIONS ------------------------------------------------------
        if ticket.distinctions:
            add(ChunkType.DISTINCTIONS, "Distinctions", [
                f"- {d.distinction}"
                + (f" [{d.attribute_name}: {d.is_value} vs {d.is_not_value}]"
                   if d.attribute_name else "")
                for d in ticket.distinctions
            ], 0.7)

        # --- CHANGES -----------------------------------------------------------
        if ticket.changes:
            add(ChunkType.CHANGES, "Changes before the failure", [
                f"- [{c.change_type}] {c.description}"
                + (f" ({c.old_value} -> {c.new_value})" if c.old_value or c.new_value else "")
                + (f" at {c.occurred_at:%Y-%m-%d %H:%M}" if c.occurred_at else "")
                for c in ticket.changes
            ], 0.7)

        # --- HYPOTHESES --------------------------------------------------------
        # Live and rejected are split into different chunk types so a query can
        # ask for one without the other. "What did we rule out?" and "what do we
        # suspect?" are different questions.
        live = [h for h in ticket.hypotheses if h.status != HypothesisStatus.REJECTED]
        rejected = [h for h in ticket.hypotheses if h.status == HypothesisStatus.REJECTED]

        for hypothesis in live:
            ev_for = [e for e in ticket.evidence
                      if e.hypothesis_id == hypothesis.id and e.direction == Direction.FOR]
            ev_against = [e for e in ticket.evidence
                          if e.hypothesis_id == hypothesis.id and e.direction == Direction.AGAINST]
            add(ChunkType.HYPOTHESIS, f"Hypothesis: {hypothesis.cause[:120]}", [
                f"Candidate cause: {hypothesis.cause}",
                f"Status: {hypothesis.status}",
                f"Reasoning: {hypothesis.reasoning}" if hypothesis.reasoning else "",
                ("Evidence for: " + "; ".join(e.content[:200] for e in ev_for)) if ev_for else "",
                ("Evidence against: " + "; ".join(e.content[:200] for e in ev_against))
                if ev_against else "",
            ], 0.6 if hypothesis.status == HypothesisStatus.SUPPORTED else 0.4)

        for hypothesis in rejected:
            ev_against = [e for e in ticket.evidence
                          if e.hypothesis_id == hypothesis.id and e.direction == Direction.AGAINST]
            tests = [t for t in ticket.tests
                     if t.hypothesis_id == hypothesis.id and t.result_status == TestResult.REJECTS]
            add(ChunkType.REJECTED_HYPOTHESIS, f"Ruled out: {hypothesis.cause[:120]}", [
                f"Cause ruled out: {hypothesis.cause}",
                f"Why it was rejected: {hypothesis.reasoning}" if hypothesis.reasoning else "",
                ("Contradicting evidence: " + "; ".join(e.content[:200] for e in ev_against))
                if ev_against else "",
                ("Eliminated by test: " + "; ".join(
                    f"{t.test_name} -> {t.actual_result or 'rejected'}" for t in tests))
                if tests else "",
            ], 0.85)  # high: an elimination someone paid for with a test

        # --- EVIDENCE ----------------------------------------------------------
        unattached = [e for e in ticket.evidence if not e.hypothesis_id]
        if unattached:
            add(ChunkType.EVIDENCE, "Observations", [
                f"[{e.evidence_type}/{e.direction}] {e.title or ''} {e.content[:400]}".strip()
                for e in unattached
            ], 0.5)

        # --- DIAGNOSTIC_TEST ----------------------------------------------------
        for test in ticket.tests:
            if test.result_status == TestResult.NOT_RUN:
                continue
            add(ChunkType.DIAGNOSTIC_TEST, f"Test: {test.test_name[:120]}", [
                f"Test: {test.test_name}",
                f"Objective: {test.objective}" if test.objective else "",
                f"Procedure: {test.procedure}" if test.procedure else "",
                f"Expected if true: {test.expected_result_if_true}"
                if test.expected_result_if_true else "",
                f"Expected if false: {test.expected_result_if_false}"
                if test.expected_result_if_false else "",
                f"Actual: {test.actual_result}" if test.actual_result else "",
                f"Verdict: {test.result_status}",
                f"Risk: {test.risk_level or 'unspecified'}, "
                f"{'reversible' if test.reversible else 'IRREVERSIBLE'}",
            ], 0.8 if test.result_status in (TestResult.CONFIRMS, TestResult.REJECTS) else 0.5)

        # --- ROOT_CAUSE ----------------------------------------------------------
        for rc in ticket.root_causes:
            add(ChunkType.ROOT_CAUSE, f"Root cause ({rc.confidence}): {rc.cause[:100]}", [
                f"Cause: {rc.cause}",
                f"Confidence: {rc.confidence}",
                f"Category: {rc.cause_category}" if rc.cause_category else "",
                f"Component: {rc.component}" if rc.component else "",
                f"Mechanism: {rc.mechanism}" if rc.mechanism else "",
                f"Trigger: {rc.trigger}" if rc.trigger else "",
                f"Verified by: {rc.verification_method}" if rc.verification_method else "",
                f"Verification result: {rc.verification_result}" if rc.verification_result else "",
                "" if rc.confidence == RootCauseConfidence.CONFIRMED else
                "(NOT confirmed — this is a lead, not a conclusion.)",
            ], _CONFIDENCE.get(rc.confidence, 0.3))

        # --- WORKAROUND / RESOLUTION / PREVENTION ----------------------------------
        workarounds = [a for a in ticket.actions if a.action_type == "WORKAROUND"]
        add(ChunkType.WORKAROUND, "Workaround", [
            ticket.workaround or "",
            *[f"- {a.description}" + (f" -> {a.result}" if a.result else "") for a in workarounds],
        ], 0.6)

        corrective = [a for a in ticket.actions if a.action_type == "CORRECTIVE"]
        add(ChunkType.RESOLUTION, "Resolution", [
            ticket.resolution_summary or "",
            *[f"- {a.description}" + (f" -> {a.result}" if a.result else "") for a in corrective],
            *[f"  before: {a.before_metric} after: {a.after_metric}"
              for a in corrective if a.before_metric or a.after_metric],
        ], 0.9 if ticket.root_cause_status == "CONFIRMED" else 0.6)

        preventive = [a for a in ticket.actions if a.action_type == "PREVENTIVE"]
        add(ChunkType.PREVENTION, "Prevention", [
            ticket.prevention_summary or "",
            *[f"- {a.description}" for a in preventive],
        ], 0.7)

        # --- TIMELINE ---------------------------------------------------------------
        if ticket.timeline:
            add(ChunkType.TIMELINE, "Timeline", [
                f"{e.occurred_at:%Y-%m-%d %H:%M}  [{e.event_type}] {e.description}"
                for e in sorted(ticket.timeline, key=lambda x: x.occurred_at)
            ], 0.6)

        # --- FULL_CASE_SUMMARY -------------------------------------------------------
        # §39. The smaller chunks are still kept: this one is for "tell me about
        # INC-234", the others for "who else saw this error".
        if ticket.status in ("RESOLVED", "CLOSED") or ticket.root_causes:
            confirmed = [rc for rc in ticket.root_causes
                         if rc.confidence == RootCauseConfidence.CONFIRMED]
            add(ChunkType.FULL_CASE_SUMMARY, f"Case summary: {ticket.ticket_number}", [
                f"CASE SUMMARY {ticket.ticket_number}: {ticket.title}",
                "",
                f"Problem: {ticket.problem_summary or ticket.actual_behavior or ''}",
                f"Expected: {ticket.expected_behavior}" if ticket.expected_behavior else "",
                f"Environment: {ticket.product or ''} {ticket.product_version or ''} "
                f"{ticket.component or ''} {ticket.environment or ''}".strip(),
                f"Impact: {ticket.business_impact or ''}",
                "",
                "KT specification:", kt_text or "(not specified)",
                "",
                "Distinctions: " + ("; ".join(d.distinction for d in ticket.distinctions)
                                    or "(none recorded)"),
                "Changes: " + ("; ".join(c.description for c in ticket.changes)
                               or "(none recorded)"),
                "",
                "Hypotheses considered: " + ("; ".join(h.cause for h in ticket.hypotheses)
                                             or "(none)"),
                "Ruled out: " + ("; ".join(
                    f"{h.cause} ({h.reasoning or 'no reason recorded'})" for h in rejected)
                    or "(none)"),
                "",
                "Confirmed root cause: " + (confirmed[0].cause if confirmed
                                            else "NOT CONFIRMED"),
                f"Verification: {confirmed[0].verification_result}"
                if confirmed and confirmed[0].verification_result else "",
                f"Workaround: {ticket.workaround}" if ticket.workaround else "",
                f"Resolution: {ticket.resolution_summary}" if ticket.resolution_summary else "",
                f"Prevention: {ticket.prevention_summary}" if ticket.prevention_summary else "",
            ], 1.0 if confirmed else 0.5)

        return chunks
