"""Problem clustering — grouping incidents into the fault behind them.

No model is involved and none is needed. Two incidents belong together when
their normalised error signatures agree, and `normalize_error_signature`
already strips the timestamps and UUIDs that would otherwise make one fault
look like fifty. That makes clustering deterministic, explainable and
instant — three properties an LLM would take away.

The rule that keeps this honest: **every statistic is reported against two
denominators.** "19 of 23 were DNS" is only true if all 23 were extracted
correctly, and on AI-extracted legacy threads they were not. So a cluster
carries `member_count` AND `verified_count`, and the caller is expected to
show both. Laundering extraction error into a confident statistic is worse
than not producing the statistic.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger("kt.problems")
UTC = timezone.utc

# A signature shorter than this carries no information — "error", "failed",
# "<n>". Clustering on it would put half the corpus in one bucket.
MIN_SIGNATURE_TOKENS = 3
MIN_CLUSTER_SIZE = 2


@dataclass
class Cluster:
    cluster_key: str
    title: str
    signature_norm: str | None = None
    product: str | None = None
    component: str | None = None
    matched_on: str = "signature"

    members: list[dict[str, Any]] = field(default_factory=list)

    @property
    def customers(self) -> set[str]:
        return {m["organization"] for m in self.members if m.get("organization")}

    @property
    def verified(self) -> list[dict[str, Any]]:
        return [m for m in self.members if m.get("root_cause_status") == "CONFIRMED"]

    def dominant_cause(self) -> tuple[str | None, int]:
        """The most common CONFIRMED cause, and how many members share it.

        Counted over verified members only. An AI-extracted cause repeated
        twenty times is twenty guesses, not evidence, and letting it win here
        is exactly how a cluster grows a confident wrong headline.
        """
        causes = Counter(
            m["root_cause"].strip().lower()[:200]
            for m in self.verified if m.get("root_cause")
        )
        if not causes:
            return None, 0
        cause, count = causes.most_common(1)[0]
        # Return the original casing from whichever member carries it.
        for m in self.verified:
            if m.get("root_cause") and m["root_cause"].strip().lower()[:200] == cause:
                return m["root_cause"], count
        return cause, count

    def category(self) -> str | None:
        cats = Counter(m["cause_category"] for m in self.members if m.get("cause_category"))
        return cats.most_common(1)[0][0] if cats else None


def signature_tokens(signature: str | None) -> list[str]:
    if not signature:
        return []
    # Placeholders from normalisation carry no discriminating power.
    return [
        t for t in re.split(r"\s+", signature)
        if len(t) > 2 and not (t.startswith("<") and t.endswith(">"))
    ]


def cluster_key_for(signature: str | None, component: str | None,
                    category: str | None) -> tuple[str, str]:
    """A stable key, so a human-set status survives re-clustering.

    Hashed rather than the raw signature because signatures can be long and
    the key is a unique index.
    """
    if signature and len(signature_tokens(signature)) >= MIN_SIGNATURE_TOKENS:
        return "sig:" + hashlib.sha256(signature.encode()).hexdigest()[:24], "signature"
    if component and category:
        return f"cc:{component}:{category}".lower()[:120], "component+cause"
    return "", ""


class ClusteringService:

    @staticmethod
    def _load(db: Session) -> list[dict[str, Any]]:
        rows = db.execute(text("""
            SELECT t.id, t.ticket_number, t.title, t.organization, t.customer_name,
                   t.product, t.component, t.environment, t.severity, t.status,
                   t.error_signature_norm, t.root_cause, t.root_cause_status,
                   t.source_type, t.first_seen_at, t.created_at, t.resolved_at,
                   (SELECT rc.cause_category FROM root_causes rc
                     WHERE rc.ticket_id = t.id ORDER BY rc.created_at LIMIT 1) AS cause_category
            FROM support_tickets t
        """)).fetchall()
        return [dict(r._mapping) for r in rows]

    # ------------------------------------------------------------------
    @classmethod
    def build_clusters(cls, db: Session) -> list[Cluster]:
        """Group every ticket. Exact signature first, then component+cause.

        Deliberately not fuzzy-matching signatures against each other: an
        O(n²) similarity pass over 50,000 tickets is slow, and the
        normalisation has already done the work that fuzzy matching would be
        compensating for.
        """
        tickets = cls._load(db)
        buckets: dict[str, Cluster] = {}

        for t in tickets:
            key, matched_on = cluster_key_for(
                t.get("error_signature_norm"), t.get("component"), t.get("cause_category")
            )
            if not key:
                continue                       # nothing to cluster it on

            cluster = buckets.get(key)
            if cluster is None:
                cluster = Cluster(
                    cluster_key=key,
                    title=(t.get("title") or "Recurring issue")[:300],
                    signature_norm=t.get("error_signature_norm"),
                    product=t.get("product"),
                    component=t.get("component"),
                    matched_on=matched_on,
                )
                buckets[key] = cluster
            cluster.members.append(t)

        # A cluster of one is an incident, not a problem.
        clusters = [c for c in buckets.values() if len(c.members) >= MIN_CLUSTER_SIZE]

        # Prefer a verified member's title: it usually names the fault rather
        # than the symptom the first reporter happened to describe.
        for c in clusters:
            verified = c.verified
            if verified and verified[0].get("title"):
                c.title = verified[0]["title"][:300]

        clusters.sort(key=lambda c: len(c.members), reverse=True)
        return clusters

    # ------------------------------------------------------------------
    @staticmethod
    def _rate(members: list[dict], since: datetime, until: datetime) -> int:
        def when(m):
            return m.get("first_seen_at") or m.get("created_at")
        return sum(1 for m in members if when(m) and since <= when(m) < until)

    @classmethod
    def detect_emerging(cls, cluster: Cluster, window_days: int,
                        baseline_days: int, now: datetime) -> dict[str, Any]:
        """Is this happening faster than it used to?

        Compares the rate inside the recent window against the rate over the
        baseline period before it. A brand-new signature with no history is
        emerging by definition — that is the major-incident case, and the one
        worth waking someone for.
        """
        window_start = now - timedelta(days=window_days)
        baseline_start = window_start - timedelta(days=baseline_days)

        recent = cls._rate(cluster.members, window_start, now)
        baseline = cls._rate(cluster.members, baseline_start, window_start)

        baseline_rate = (baseline / baseline_days) * window_days if baseline_days else 0.0

        if recent == 0:
            ratio = 0.0
        elif baseline_rate <= 0:
            # No history at all. Treat it as a surge, but only once it clears
            # the minimum size — otherwise every new one-off is an alert.
            ratio = float(recent) if recent >= MIN_CLUSTER_SIZE else 0.0
        else:
            ratio = recent / baseline_rate

        # A ratio alone is not enough at these counts. A cluster running at a
        # steady 1/week produces 2 in some weeks purely by chance — that is a
        # 2.1x "spike" and an alert nobody should get. Incident arrivals are
        # roughly Poisson, so the noise on an expected count of λ is √λ;
        # requiring the recent count to clear λ + 3√λ filters that out while
        # leaving a genuine jump untouched.
        #
        #   λ=0.9  →  threshold 3.8   2 observed  no alert   (noise)
        #   λ=0.3  →  threshold 2.0   8 observed  ALERT      (real)
        poisson_floor = baseline_rate + 3.0 * (baseline_rate ** 0.5)

        multi_customer = len({
            m["organization"] for m in cluster.members
            if m.get("organization")
            and (m.get("first_seen_at") or m.get("created_at"))
            and (m.get("first_seen_at") or m.get("created_at")) >= window_start
        }) >= 2

        # Two independent triggers.
        #
        #   rate   — clears both the ratio and the Poisson floor
        #   spread — the same fault hitting two or more tenants inside one
        #            window is a platform problem however slowly it arrives,
        #            so it alerts without needing an acceleration
        big_enough = recent >= MIN_CLUSTER_SIZE
        rate_spike = big_enough and ratio >= 2.0 and recent > poisson_floor
        spread = big_enough and multi_customer

        return {
            "recent_count": recent,
            "baseline_rate": round(baseline_rate, 3),
            "surge_ratio": round(ratio, 3),
            "poisson_floor": round(poisson_floor, 3),
            "is_emerging": rate_spike or spread,
            "trigger": "rate" if rate_spike else ("spread" if spread else None),
            "multi_customer": multi_customer,
        }

    # ------------------------------------------------------------------
    @classmethod
    def rebuild(cls, db: Session, *, window_days: int = 7,
                baseline_days: int = 90, now: datetime | None = None) -> dict[str, Any]:
        """Recompute every problem record from the tickets.

        Wholesale rather than incremental: clustering the entire corpus is a
        single query plus a dictionary, and a rebuild that cannot drift is
        worth more than one that is marginally faster.
        """
        started = time.time()
        now = now or datetime.now(UTC)

        clusters = cls.build_clusters(db)
        emerging_count = 0

        # Membership is derived; wipe and rewrite. The human-owned columns on
        # problem_records are preserved by the ON CONFLICT below.
        db.execute(text("DELETE FROM problem_members"))

        seen_keys = []
        for c in clusters:
            surge = cls.detect_emerging(c, window_days, baseline_days, now)
            if surge["is_emerging"]:
                emerging_count += 1

            cause, cause_count = c.dominant_cause()
            times = [m.get("first_seen_at") or m.get("created_at") for m in c.members]
            times = [t for t in times if t]

            row = db.execute(text("""
                INSERT INTO problem_records (
                    cluster_key, title, signature_norm, product, component,
                    member_count, customers_affected, verified_count,
                    dominant_cause, dominant_cause_count, cause_category,
                    first_seen_at, last_seen_at,
                    recent_count, baseline_rate, surge_ratio, is_emerging, metadata
                ) VALUES (
                    :key, :title, :sig, :product, :component,
                    :members, :customers, :verified,
                    :cause, :cause_count, :category,
                    :first_seen, :last_seen,
                    :recent, :baseline, :ratio, :emerging, CAST(:meta AS jsonb)
                )
                ON CONFLICT (cluster_key) DO UPDATE SET
                    title                = EXCLUDED.title,
                    signature_norm       = EXCLUDED.signature_norm,
                    product              = EXCLUDED.product,
                    component            = EXCLUDED.component,
                    member_count         = EXCLUDED.member_count,
                    customers_affected   = EXCLUDED.customers_affected,
                    verified_count       = EXCLUDED.verified_count,
                    dominant_cause       = EXCLUDED.dominant_cause,
                    dominant_cause_count = EXCLUDED.dominant_cause_count,
                    cause_category       = EXCLUDED.cause_category,
                    first_seen_at        = EXCLUDED.first_seen_at,
                    last_seen_at         = EXCLUDED.last_seen_at,
                    recent_count         = EXCLUDED.recent_count,
                    baseline_rate        = EXCLUDED.baseline_rate,
                    surge_ratio          = EXCLUDED.surge_ratio,
                    is_emerging          = EXCLUDED.is_emerging,
                    metadata             = EXCLUDED.metadata,
                    updated_at           = NOW()
                    -- status, permanent_fix, owner, notes are NOT touched:
                    -- they are the only human-owned fields here.
                RETURNING id
            """), {
                "key": c.cluster_key, "title": c.title, "sig": c.signature_norm,
                "product": c.product, "component": c.component,
                "members": len(c.members), "customers": len(c.customers),
                "verified": len(c.verified),
                "cause": cause, "cause_count": cause_count, "category": c.category(),
                "first_seen": min(times) if times else None,
                "last_seen": max(times) if times else None,
                "recent": surge["recent_count"], "baseline": surge["baseline_rate"],
                "ratio": surge["surge_ratio"], "emerging": surge["is_emerging"],
                "meta": __import__("json").dumps({
                    "matched_on": c.matched_on,
                    "multi_customer": surge["multi_customer"],
                    # Why it alerted, and the bar it had to clear. Without
                    # these an on-call engineer sees "1.0x surge, ALERT" and
                    # reasonably concludes the detector is broken.
                    "trigger": surge["trigger"],
                    "poisson_floor": surge["poisson_floor"],
                    "source_mix": dict(Counter(m.get("source_type") for m in c.members)),
                }),
            }).fetchone()

            problem_id = row[0]
            seen_keys.append(c.cluster_key)

            for m in c.members:
                db.execute(text("""
                    INSERT INTO problem_members (problem_id, ticket_id, matched_on)
                    VALUES (:p, :t, :m)
                    ON CONFLICT (problem_id, ticket_id) DO NOTHING
                """), {"p": problem_id, "t": m["id"], "m": c.matched_on})

        # Clusters that no longer exist — a ticket was deleted or re-extracted
        # into a different signature. Drop them unless a human has taken
        # ownership, in which case the record is theirs, not the job's.
        if seen_keys:
            db.execute(text("""
                DELETE FROM problem_records
                WHERE cluster_key <> ALL(:keys)
                  AND status = 'OPEN' AND permanent_fix IS NULL AND owner IS NULL
            """), {"keys": seen_keys})

        elapsed = int((time.time() - started) * 1000)
        db.execute(text("""
            INSERT INTO problem_detection_runs
                (window_days, baseline_days, tickets_scanned, clusters_found,
                 emerging_found, duration_ms)
            VALUES (:w, :b, :scanned, :found, :emerging, :ms)
        """), {
            "w": window_days, "b": baseline_days,
            "scanned": sum(len(c.members) for c in clusters),
            "found": len(clusters), "emerging": emerging_count, "ms": elapsed,
        })
        db.commit()

        log.info("clustering: %d problems, %d emerging, %dms",
                 len(clusters), emerging_count, elapsed)

        return {
            "clusters": len(clusters),
            "emerging": emerging_count,
            "tickets_clustered": sum(len(c.members) for c in clusters),
            "window_days": window_days,
            "baseline_days": baseline_days,
            "duration_ms": elapsed,
        }
