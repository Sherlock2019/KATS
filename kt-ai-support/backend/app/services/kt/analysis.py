"""KTAnalysisService — the KT grid, derived distinctions, and IS/IS NOT matching.

This module is the reason the system can retrieve better than semantic
similarity alone.

Consider two historical cases against a new incident whose WHAT IS is "new
tokens fail" and whose WHAT IS NOT is "existing tokens work":

    Ticket A   new tokens fail, existing tokens work
    Ticket B   all authentication fails

Ticket B may be lexically and semantically closer — it is longer, it says
"authentication" more often. It is also the wrong case: whatever broke it
would also have broken existing tokens, so it cannot explain the IS NOT.
`kt_similarity()` is what lets Ticket A win anyway.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.models.enums import Dimension, Side

DIMENSIONS: tuple[str, ...] = (Dimension.WHAT, Dimension.WHERE, Dimension.WHEN, Dimension.EXTENT)

_STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "was", "are", "not",
    "but", "has", "have", "had", "can", "when", "what", "where", "all", "any",
    "its", "via", "per", "due", "new", "old", "only", "some", "does", "did",
}


def _tokens(text: str | None) -> set[str]:
    if not text:
        return set()
    raw = re.split(r"[^a-z0-9_.\-/]+", str(text).lower())
    return {t for t in raw if len(t) > 2 and t not in _STOPWORDS}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


@dataclass
class KTSide:
    """Everything said on one side of one dimension."""

    values: list[str] = field(default_factory=list)
    structured: dict[str, set[str]] = field(default_factory=dict)

    @property
    def tokens(self) -> set[str]:
        out: set[str] = set()
        for v in self.values:
            out |= _tokens(v)
        return out

    @property
    def stated(self) -> bool:
        return bool(self.values)


@dataclass
class KTProfile:
    """A ticket's whole specification, in the shape comparison needs.

    Built once per ticket and compared many times, so the token sets are
    computed here rather than inside the scoring loop.
    """

    dimensions: dict[str, dict[str, KTSide]] = field(default_factory=dict)

    def side(self, dimension: str, side: str) -> KTSide:
        return self.dimensions.get(dimension, {}).get(side, KTSide())

    @property
    def stated_cells(self) -> int:
        return sum(
            1
            for d in DIMENSIONS
            for s in (Side.IS, Side.IS_NOT)
            if self.side(d, s).stated
        )

    @property
    def has_contrast(self) -> bool:
        """At least one dimension with BOTH sides stated.

        Without a contrast there is no KT specification — only a description
        of what is broken, which is what every ticket system already has.
        """
        return any(
            self.side(d, Side.IS).stated and self.side(d, Side.IS_NOT).stated
            for d in DIMENSIONS
        )


class KTAnalysisService:
    """Stateless. Every method takes rows and returns values."""

    # -- profile ------------------------------------------------------------
    @staticmethod
    def build_profile(specifications) -> KTProfile:
        profile = KTProfile()
        for spec in specifications:
            dim = profile.dimensions.setdefault(
                str(spec.dimension), {Side.IS: KTSide(), Side.IS_NOT: KTSide()}
            )
            side = dim.setdefault(str(spec.side), KTSide())
            if spec.value:
                side.values.append(spec.value)
            if spec.structured_key:
                side.structured.setdefault(spec.structured_key, set()).add(
                    str(spec.structured_value or "")
                )
        return profile

    # -- the §5 grid --------------------------------------------------------
    @staticmethod
    def build_grid(specifications) -> dict:
        by_cell: dict[tuple[str, str], list] = {}
        for spec in specifications:
            by_cell.setdefault((str(spec.dimension), str(spec.side)), []).append(spec)

        rows = []
        filled = 0
        for dimension in DIMENSIONS:
            is_entries = sorted(
                by_cell.get((dimension, Side.IS), []), key=lambda s: (s.sort_order, s.created_at)
            )
            isnot_entries = sorted(
                by_cell.get((dimension, Side.IS_NOT), []), key=lambda s: (s.sort_order, s.created_at)
            )
            filled += bool(is_entries) + bool(isnot_entries)
            rows.append(
                {
                    "dimension": dimension,
                    "is": {"entries": is_entries},
                    "is_not": {"entries": isnot_entries},
                }
            )
        return {"rows": rows, "filled_cells": filled, "total_cells": 8}

    # -- derived distinctions ----------------------------------------------
    @staticmethod
    def derive_distinctions(specifications) -> list[dict]:
        """Where an IS and an IS NOT share a structured_key and differ, that
        difference IS a distinction — no one needs to type it.

        This is the payoff for asking people to tag `cluster: cluster-a`
        instead of writing a sentence: KT's hardest analytical step falls out
        of the data model.
        """
        profile = KTAnalysisService.build_profile(specifications)
        derived: list[dict] = []

        for dimension in DIMENSIONS:
            is_side = profile.side(dimension, Side.IS)
            not_side = profile.side(dimension, Side.IS_NOT)

            for key, is_values in is_side.structured.items():
                not_values = not_side.structured.get(key)
                if not not_values:
                    continue
                differing_is = sorted(v for v in is_values if v and v not in not_values)
                differing_not = sorted(v for v in not_values if v and v not in is_values)
                if not differing_is or not differing_not:
                    continue

                derived.append(
                    {
                        "dimension": dimension,
                        "attribute_name": key,
                        "is_value": ", ".join(differing_is),
                        "is_not_value": ", ".join(differing_not),
                        "distinction": (
                            f"{dimension}: the affected side has {key} = "
                            f"{', '.join(differing_is)} while the unaffected side has "
                            f"{key} = {', '.join(differing_not)}."
                        ),
                        # Derived facts start below hand-written ones: a person
                        # who typed a distinction was making a judgement, and
                        # this is only pattern-matching on tags.
                        "importance_score": 0.6,
                        "derived": True,
                    }
                )
        return derived

    # -- §22 IS / IS NOT similarity ----------------------------------------
    @staticmethod
    def kt_similarity(query: KTProfile, candidate: KTProfile) -> tuple[float, list[str]]:
        """How well two specifications agree, per dimension, on BOTH sides.

        Scoring, per dimension:

            both sides stated in both profiles, both agree   1.00
            IS agrees, IS NOT stated in both but disagrees   0.35
            only IS agrees (one side has no IS NOT)          0.50
            IS NOT agrees but IS does not                    0.30

        The middle row is the one that matters. A candidate whose IS matches
        but whose IS NOT contradicts is actively the wrong case: it means the
        two incidents divide the world differently, and the historical cause
        cannot explain the new boundary. Scoring that BELOW a candidate with
        no IS NOT at all is deliberate — a wrong contrast is worse evidence
        than a missing one.
        """
        if not query.stated_cells or not candidate.stated_cells:
            return 0.0, []

        total = 0.0
        weighted = 0.0
        reasons: list[str] = []

        # WHAT carries the most diagnostic weight: it names the failing thing.
        dimension_weight = {
            Dimension.WHAT: 1.4,
            Dimension.WHERE: 1.1,
            Dimension.WHEN: 1.0,
            Dimension.EXTENT: 0.9,
        }

        for dimension in DIMENSIONS:
            q_is, q_not = query.side(dimension, Side.IS), query.side(dimension, Side.IS_NOT)
            c_is, c_not = candidate.side(dimension, Side.IS), candidate.side(dimension, Side.IS_NOT)

            if not q_is.stated and not q_not.stated:
                continue          # the query says nothing here; do not penalise
            if not c_is.stated and not c_not.stated:
                continue

            weight = dimension_weight[dimension]
            total += weight

            is_sim = _jaccard(q_is.tokens, c_is.tokens) if q_is.stated and c_is.stated else None
            not_sim = _jaccard(q_not.tokens, c_not.tokens) if q_not.stated and c_not.stated else None

            if is_sim is not None and not_sim is not None:
                score = 0.55 * is_sim + 0.45 * not_sim
                if is_sim >= 0.3 and not_sim >= 0.3:
                    score = min(1.0, score * 1.35)      # both sides agree — the strong case
                    reasons.append(
                        f"{dimension}: IS and IS NOT both match "
                        f"({is_sim:.0%} / {not_sim:.0%})"
                    )
                elif is_sim >= 0.3 and not_sim < 0.15:
                    score *= 0.45
                    reasons.append(
                        f"{dimension}: IS matches but IS NOT contradicts — "
                        f"the two incidents draw the boundary differently"
                    )
            elif is_sim is not None:
                score = is_sim * 0.7
                if is_sim >= 0.3:
                    reasons.append(f"{dimension} IS matches ({is_sim:.0%}), no IS NOT to compare")
            elif not_sim is not None:
                score = not_sim * 0.5
            else:
                score = 0.0

            weighted += weight * score

        if total == 0:
            return 0.0, []
        return round(min(1.0, weighted / total), 4), reasons

    # -- §44 / §45 gaps -----------------------------------------------------
    @staticmethod
    def gaps(profile: KTProfile) -> list[dict]:
        """Unanswered cells, most valuable first.

        A missing IS NOT outranks a missing IS: without the healthy twin
        there is nothing to eliminate against, and elimination is the whole
        method. This ordering is what /api/ai/next-question walks.
        """
        prompts = {
            (Dimension.WHAT, Side.IS_NOT):
                "What comparable thing could have failed the same way but did not?",
            (Dimension.WHERE, Side.IS_NOT):
                "Where could this have happened but did not — another cluster, region or host?",
            (Dimension.WHEN, Side.IS_NOT):
                "When does the problem NOT occur? What was the last known good time?",
            (Dimension.EXTENT, Side.IS_NOT):
                "What portion is unaffected — which users, requests or nodes are fine?",
            (Dimension.WHAT, Side.IS): "Exactly which object or component is failing?",
            (Dimension.WHERE, Side.IS): "Where is it failing — cluster, region, host, environment?",
            (Dimension.WHEN, Side.IS): "When did it start, and how often does it occur?",
            (Dimension.EXTENT, Side.IS): "How much is affected — how many users, what percentage?",
        }
        # IS NOT first, and WHAT before the rest.
        order = [
            (Dimension.WHAT, Side.IS_NOT), (Dimension.WHERE, Side.IS_NOT),
            (Dimension.WHEN, Side.IS_NOT), (Dimension.EXTENT, Side.IS_NOT),
            (Dimension.WHAT, Side.IS), (Dimension.WHERE, Side.IS),
            (Dimension.WHEN, Side.IS), (Dimension.EXTENT, Side.IS),
        ]

        out = []
        for dimension, side in order:
            if profile.side(dimension, side).stated:
                continue
            paired = profile.side(dimension, Side.IS if side == Side.IS_NOT else Side.IS_NOT)
            out.append(
                {
                    "dimension": str(dimension),
                    "side": str(side),
                    "question": prompts[(dimension, side)],
                    # A missing IS NOT next to a stated IS is the highest-value
                    # question on the board: the contrast is one answer away.
                    "priority": 1.0 if (side == Side.IS_NOT and paired.stated) else 0.6,
                }
            )
        return out

    @staticmethod
    def to_text(profile: KTProfile) -> str:
        """The KT_SPECIFICATION chunk, and the query text for retrieval."""
        lines: list[str] = []
        for dimension in DIMENSIONS:
            is_side = profile.side(dimension, Side.IS)
            not_side = profile.side(dimension, Side.IS_NOT)
            if not is_side.stated and not not_side.stated:
                continue
            if is_side.stated:
                lines.append(f"{dimension} IS: " + "; ".join(is_side.values))
            if not_side.stated:
                lines.append(f"{dimension} IS NOT: " + "; ".join(not_side.values))
        return "\n".join(lines)
