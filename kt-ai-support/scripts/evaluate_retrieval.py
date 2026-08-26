"""Retrieval evaluation. §41.

Compares three configurations over the same query set:

    vector      semantic similarity only
    hybrid      + keyword, metadata, error signature, quality, confidence
    hybrid+kt   + IS / IS NOT structural matching

Metrics: Recall@5, MRR, root-cause retrieval accuracy, source-ticket
precision, KT match precision.

The five near-twins in the demo corpus are the point of the exercise. They
all present as "HTTP 404 could not find token" with five different causes, so
vector search should struggle to separate them and the KT term should not.
If hybrid+kt does NOT beat vector here, either the weights are wrong or §22
is not doing what it claims — and this script is how you find out rather
than assume.

    python -m scripts.evaluate_retrieval
    python -m scripts.evaluate_retrieval --verbose
"""

from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "backend"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from sqlalchemy import select                                      # noqa: E402

from app.db import SessionLocal                                    # noqa: E402
from app.models import SupportTicket                               # noqa: E402
from app.services.rag.retrieval import RetrievalService            # noqa: E402

# The five near-twin probes deliberately share an IDENTICAL query string.
#
# That is the entire experiment. A support engineer opening a new incident
# writes the symptom, not the answer — "authentication is returning 404" —
# and every one of the five historical cases matches that equally well. The
# prose cannot separate them, so semantic and keyword similarity produce the
# same ranking for all five probes and can be right at most once by luck.
#
# Only the IS / IS NOT structure differs. If the KT term works, hybrid+kt
# finds the right case for all five; if it does not, this table says so.
_TWIN_QUERY = (
    "Authentication is failing with HTTP 404 'Could not find token'. "
    "Users cannot authenticate against the OpenCenter API. "
    "This started today and we have not changed anything we are aware of."
)

EVAL_SET = [
    {
        "name": "twin: new tokens fail / existing work",
        "query": _TWIN_QUERY,
        "kt": [("WHAT", "IS", "Newly generated API tokens", "token_age", "new"),
               ("WHAT", "IS_NOT", "Existing tokens issued earlier", "token_age", "existing"),
               ("WHEN", "IS", "Immediately after generating a token", None, None),
               ("WHEN", "IS_NOT", "Before the rotation", None, None)],
        "expect_title_contains": "newly generated tokens",
        "expect_cause_contains": "persist",
    },
    {
        "name": "twin: cluster-a fails / cluster-b works",
        "query": _TWIN_QUERY,
        "kt": [("WHAT", "IS", "All authentication, new and old tokens alike", "token_age", "all"),
               ("WHAT", "IS_NOT", "Requests that never reach the failing cluster", None, None),
               ("WHERE", "IS", "One cluster", "cluster", "cluster-a"),
               ("WHERE", "IS_NOT", "The other cluster, healthy", "cluster", "cluster-b")],
        "expect_title_contains": "cluster-a after secret rotation",
        "expect_cause_contains": "secret",
    },
    {
        "name": "twin: one node fails / other nodes work",
        "query": _TWIN_QUERY,
        "kt": [("WHAT", "IS", "Requests reaching one particular host", "node", "node-07"),
               ("WHAT", "IS_NOT", "Requests reaching any other host", "node", "other"),
               ("WHEN", "IS", "Intermittently, a fraction of requests", None, None),
               ("WHEN", "IS_NOT", "Consistently — retries usually succeed", None, None)],
        "expect_title_contains": "drifting clock",
        "expect_cause_contains": "clock",
    },
    {
        "name": "twin: aged tokens fail / fresh work",
        "query": _TWIN_QUERY,
        "kt": [("WHAT", "IS", "Tokens that have been alive a while", "token_age", "aged"),
               ("WHAT", "IS_NOT", "Tokens used right after issue", "token_age", "fresh"),
               ("WHEN", "IS", "Some minutes after the token is issued", None, None),
               ("WHEN", "IS_NOT", "Immediately after issue, when it works", None, None)],
        "expect_title_contains": "five minutes after issue",
        "expect_cause_contains": "cache",
    },
    {
        "name": "twin: via LB fails / direct works",
        "query": _TWIN_QUERY,
        "kt": [("WHAT", "IS", "Requests arriving through the load balancer", "path", "lb"),
               ("WHAT", "IS_NOT", "Requests sent straight to a pod", "path", "direct"),
               ("EXTENT", "IS", "All externally routed requests", None, None),
               ("EXTENT", "IS_NOT", "In-cluster traffic, unaffected", None, None)],
        "expect_title_contains": "load balancer",
        "expect_cause_contains": "header",
    },
    {
        "name": "arch mismatch after node upgrade",
        "query": "Pods crash-loop with exec format error after upgrading the node pool.",
        "kt": [("WHAT", "IS", "amd64-only container images", "arch", "amd64"),
               ("WHAT", "IS_NOT", "multi-arch images", "arch", "multi")],
        "expect_title_contains": "CrashLoopBackOff",
        "expect_cause_contains": "arm64",
    },
    {
        "name": "pool exhaustion in a nightly window",
        "query": "Too many clients already, connection refused during the nightly batch.",
        "kt": [("WHEN", "IS", "During the 02:00 batch window", None, None),
               ("WHEN", "IS_NOT", "Any other time of day", None, None)],
        "expect_title_contains": "connection pool exhausted",
        "expect_cause_contains": "parallel",
    },
    {
        "name": "tls chain incomplete after renewal",
        "query": "unable to get local issuer certificate after a certificate renewal. "
                 "Browsers work, API clients fail.",
        "kt": [("WHAT", "IS", "Strict-verifying API clients", "client", "strict"),
               ("WHAT", "IS_NOT", "Browsers with cached intermediates", "client", "browser")],
        "expect_title_contains": "TLS handshake",
        "expect_cause_contains": "intermediate",
    },
]

CONFIGS = {
    "vector": {
        "semantic": 1.0, "keyword": 0.0, "metadata": 0.0, "error_signature": 0.0,
        "knowledge_quality": 0.0, "root_cause_confidence": 0.0, "kt_match": 0.0,
    },
    "hybrid": {
        "semantic": 0.40, "keyword": 0.20, "metadata": 0.15, "error_signature": 0.10,
        "knowledge_quality": 0.10, "root_cause_confidence": 0.05, "kt_match": 0.0,
    },
    "hybrid+kt": {
        "semantic": 0.40, "keyword": 0.20, "metadata": 0.15, "error_signature": 0.10,
        "knowledge_quality": 0.10, "root_cause_confidence": 0.05, "kt_match": 0.30,
    },
}


class FakeSpec:
    def __init__(self, dimension, side, value, key, structured):
        self.dimension, self.side, self.value = dimension, side, value
        self.structured_key, self.structured_value = key, structured
        self.sort_order, self.created_at = 0, None


def evaluate(db, config_name: str, weights: dict, verbose: bool) -> dict:
    from app.services.kt.analysis import KTAnalysisService

    recall_at_5 = 0
    reciprocal_ranks = []
    cause_hits = 0
    kt_precision = []

    for probe in EVAL_SET:
        ctx = RetrievalService.context_from_text(probe["query"])
        ctx.kt_profile = KTAnalysisService.build_profile(
            [FakeSpec(*s) for s in probe["kt"]]
        )

        found = RetrievalService.search(db, ctx, top_k=5, weight_override=weights)

        # Rank of the first chunk belonging to the expected ticket.
        rank = None
        expected_lower = probe["expect_title_contains"].lower()
        seen_tickets = []
        for index, candidate in enumerate(found["results"], start=1):
            ticket = db.get(SupportTicket, candidate.ticket_id)
            if ticket.ticket_number not in seen_tickets:
                seen_tickets.append(ticket.ticket_number)
            if expected_lower in (ticket.title or "").lower() and rank is None:
                rank = index
                if probe["expect_cause_contains"].lower() in (ticket.root_cause or "").lower():
                    cause_hits += 1
                kt_precision.append(candidate.kt)

        if rank is not None:
            recall_at_5 += 1
            reciprocal_ranks.append(1.0 / rank)
        else:
            reciprocal_ranks.append(0.0)

        if verbose:
            mark = f"#{rank}" if rank else "MISS"
            top = found["results"][0] if found["results"] else None
            top_ticket = db.get(SupportTicket, top.ticket_id).title[:52] if top else "-"
            print(f"    {mark:<5} {probe['name'][:44]:<44} top: {top_ticket}")

    n = len(EVAL_SET)
    return {
        "config": config_name,
        "recall_at_5": recall_at_5 / n,
        "mrr": sum(reciprocal_ranks) / n,
        "root_cause_accuracy": cause_hits / n,
        "kt_match_precision": (sum(kt_precision) / len(kt_precision)) if kt_precision else 0.0,
        "found": recall_at_5,
        "total": n,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        total = db.scalar(select(SupportTicket).with_only_columns(SupportTicket.id).limit(1))
        if not total:
            print("\n  No tickets. Run: python -m scripts.seed_demo_cases\n")
            return 1

        print(f"\n  Evaluating {len(EVAL_SET)} probes against the demo corpus\n")
        rows = []
        for name, weights in CONFIGS.items():
            if args.verbose:
                print(f"  {name}")
            rows.append(evaluate(db, name, weights, args.verbose))
            if args.verbose:
                print()

        print(f"  {'config':<12}{'Recall@5':>10}{'MRR':>8}{'RootCause':>11}{'KTmatch':>9}")
        print("  " + "-" * 50)
        for row in rows:
            print(f"  {row['config']:<12}{row['recall_at_5']:>9.0%}{row['mrr']:>8.3f}"
                  f"{row['root_cause_accuracy']:>11.0%}{row['kt_match_precision']:>9.3f}")

        best = max(rows, key=lambda r: (r["mrr"], r["recall_at_5"]))
        print(f"\n  Best: {best['config']} (MRR {best['mrr']:.3f})")

        vector = next(r for r in rows if r["config"] == "vector")
        kt = next(r for r in rows if r["config"] == "hybrid+kt")
        delta = kt["mrr"] - vector["mrr"]
        print(f"  hybrid+kt vs vector-only: MRR {delta:+.3f}, "
              f"Recall@5 {kt['recall_at_5'] - vector['recall_at_5']:+.0%}\n")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
