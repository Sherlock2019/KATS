"""KnowledgeQualityService — §30.

Retrieval boosts high-quality cases, so this score decides which historical
incidents the assistant leans on. It is computed from what is actually in the
database, never asserted by a client: a ticket does not become gold-standard
because someone ticked a box.

    0.0  unusable
    0.5  partial
    0.8  strong
    1.0  gold-standard
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models.enums import Direction, HypothesisStatus, RootCauseConfidence, TestResult
from app.services.kt.analysis import KTAnalysisService


@dataclass(frozen=True)
class QualityComponent:
    key: str
    label: str
    weight: float
    earned: float
    detail: str

    @property
    def points(self) -> float:
        return self.weight * self.earned


class KnowledgeQualityService:
    """Weights sum to 1.0. They encode what makes a case reusable, which is
    not the same as what makes it complete — a ticket with a verified root
    cause and no prevention plan is worth far more to the next engineer than
    one with every narrative field filled and no conclusion."""

    WEIGHTS = {
        "problem": 0.15,
        "kt_specification": 0.20,
        "evidence": 0.15,
        "tests": 0.15,
        "root_cause": 0.20,
        "resolution": 0.10,
        "prevention": 0.05,
    }

    @classmethod
    def evaluate(cls, ticket) -> tuple[float, list[QualityComponent]]:
        components: list[QualityComponent] = []

        # --- problem clearly defined ---------------------------------------
        parts = [ticket.problem_summary, ticket.expected_behavior, ticket.actual_behavior]
        present = sum(1 for p in parts if p and p.strip())
        # The deviation is the pair. Naming only what broke is half a problem
        # statement, so expected+actual together are worth more than either.
        earned = present / 3
        if ticket.expected_behavior and ticket.actual_behavior:
            earned = max(earned, 0.85)
        components.append(
            QualityComponent(
                "problem", "Problem clearly defined", cls.WEIGHTS["problem"], earned,
                f"{present}/3 of summary, expected, actual",
            )
        )

        # --- KT specification ----------------------------------------------
        profile = KTAnalysisService.build_profile(ticket.specifications)
        cells = profile.stated_cells
        earned = cells / 8
        if profile.has_contrast:
            # A single real contrast is worth more than six one-sided cells:
            # it is the only thing you can eliminate against.
            earned = max(earned, 0.6)
        components.append(
            QualityComponent(
                "kt_specification", "IS / IS NOT completed", cls.WEIGHTS["kt_specification"],
                earned, f"{cells}/8 cells" + (", has contrast" if profile.has_contrast else
                                              ", NO contrast — nothing to eliminate against"),
            )
        )

        # --- evidence -------------------------------------------------------
        evidence = list(ticket.evidence)
        against = sum(1 for e in evidence if e.direction == Direction.AGAINST)
        earned = min(1.0, len(evidence) / 4)
        if against:
            # Evidence that kills a candidate is the expensive kind, and the
            # kind people forget to record. Reward it explicitly.
            earned = min(1.0, earned + 0.2)
        components.append(
            QualityComponent(
                "evidence", "Evidence attached", cls.WEIGHTS["evidence"], earned,
                f"{len(evidence)} item(s), {against} contradicting",
            )
        )

        # --- diagnostic tests -----------------------------------------------
        tests = list(ticket.tests)
        run = [t for t in tests if t.result_status != TestResult.NOT_RUN]
        discriminating = [
            t for t in tests if t.expected_result_if_true and t.expected_result_if_false
        ]
        earned = min(1.0, len(run) / 2) * (0.6 + 0.4 * bool(discriminating))
        components.append(
            QualityComponent(
                "tests", "Diagnostic tests documented", cls.WEIGHTS["tests"], earned,
                f"{len(run)} run, {len(discriminating)} with both branches recorded",
            )
        )

        # --- root cause verified ---------------------------------------------
        ladder = {
            RootCauseConfidence.CONFIRMED: 1.0,
            RootCauseConfidence.HIGH_CONFIDENCE: 0.7,
            RootCauseConfidence.PROBABLE: 0.45,
            RootCauseConfidence.SUSPECTED: 0.2,
        }
        best = max((ladder.get(rc.confidence, 0.0) for rc in ticket.root_causes), default=0.0)
        confirmed = [rc for rc in ticket.root_causes if rc.confidence == RootCauseConfidence.CONFIRMED]
        if confirmed and not any(rc.verification_result for rc in confirmed):
            # Confirmed with nothing recorded to back it is a claim, not a
            # verification. Cap it below a genuinely verified case.
            best = min(best, 0.7)
        components.append(
            QualityComponent(
                "root_cause", "Root cause verified", cls.WEIGHTS["root_cause"], best,
                f"{len(ticket.root_causes)} recorded, best confidence "
                f"{max((rc.confidence for rc in ticket.root_causes), default='none')}",
            )
        )

        # --- resolution verified ---------------------------------------------
        has_resolution = bool(ticket.resolution_summary and ticket.resolution_summary.strip())
        verified_action = any(
            a.after_metric for a in ticket.actions if a.action_type == "CORRECTIVE"
        )
        earned = (0.6 if has_resolution else 0.0) + (0.4 if verified_action else 0.0)
        components.append(
            QualityComponent(
                "resolution", "Resolution verified", cls.WEIGHTS["resolution"], earned,
                ("resolution written" if has_resolution else "no resolution")
                + (", before/after captured" if verified_action else ""),
            )
        )

        # --- prevention -------------------------------------------------------
        has_prevention = bool(ticket.prevention_summary and ticket.prevention_summary.strip())
        components.append(
            QualityComponent(
                "prevention", "Prevention documented", cls.WEIGHTS["prevention"],
                1.0 if has_prevention else 0.0,
                "documented" if has_prevention else "not documented",
            )
        )

        score = round(sum(c.points for c in components), 2)
        return min(1.0, max(0.0, score)), components

    @classmethod
    def completeness(cls, ticket) -> dict:
        """§44 — the same measurement, shaped for the progress panel."""
        score, components = cls.evaluate(ticket)
        profile = KTAnalysisService.build_profile(ticket.specifications)
        gaps = KTAnalysisService.gaps(profile)
        gap_by_key = {f"{g['dimension']}/{g['side']}": g["question"] for g in gaps}

        sections = []
        for component in components:
            prompt = None
            if component.key == "kt_specification" and gaps:
                prompt = gaps[0]["question"]
            elif component.earned < 0.5:
                prompt = {
                    "problem": "State what SHOULD happen and what actually happens.",
                    "evidence": "Attach a log, metric or command output — and note which way it cuts.",
                    "tests": "Write the test's expected result for BOTH outcomes before running it.",
                    "root_cause": "What mechanism explains the IS and the IS NOT?",
                    "resolution": "What corrective action was taken, and what did the metrics do?",
                    "prevention": "What stops this recurring?",
                }.get(component.key)

            sections.append(
                {
                    "key": component.key,
                    "label": component.label,
                    "percent": int(round(component.earned * 100)),
                    "present": int(round(component.earned * 100)),
                    "expected": 100,
                    "missing_prompt": prompt,
                }
            )

        return {
            "overall": int(round(score * 100)),
            "sections": sections,
            "knowledge_quality_score": score,
            "_gap_questions": gap_by_key,
        }
