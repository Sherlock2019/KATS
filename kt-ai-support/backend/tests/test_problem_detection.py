"""Emerging-incident detection, unit-tested with no database.

The interesting cases are the ones that must NOT alert. A detector that fires
on a steady 1-per-week cluster because it happened to see 2 this week trains
people to ignore it, which is worse than not having it.
"""
import pathlib, sys
from datetime import datetime, timedelta, timezone
sys.path.insert(0, str(pathlib.Path("/home/dzoan/ktsupport/ktsupport/kt-ai-support/backend")))

from app.services.problems.clustering import Cluster, ClusteringService  # noqa: E402

UTC = timezone.utc
NOW = datetime(2026, 8, 27, tzinfo=UTC)

def check(name, cond, detail=""):
    assert cond, f"{name}: {detail}"

def members(days_ago_list, orgs=None):
    orgs = orgs or ["acme"] * len(days_ago_list)
    return [{"id": i, "organization": orgs[i],
             "first_seen_at": NOW - timedelta(days=d),
             "created_at": NOW - timedelta(days=d),
             "root_cause_status": "UNKNOWN"}
            for i, d in enumerate(days_ago_list)]

def surge(days, orgs=None, window=7, baseline=90):
    c = Cluster(cluster_key="k", title="t")
    c.members = members(days, orgs)
    return ClusteringService.detect_emerging(c, window, baseline, NOW)

def test_emerging_detection():
    print("\n=== a genuine spike ===")
    # 8 in the last week, 4 spread over the 90 before it
    s = surge([1,1,2,2,3,4,5,6] + [20,45,70,88])
    print(f"  recent={s['recent_count']} baseline_rate={s['baseline_rate']} "
          f"ratio={s['surge_ratio']}")
    check("fires on a spike", s["is_emerging"], str(s))
    check("ratio well above 1", s["surge_ratio"] > 2, str(s["surge_ratio"]))

    print("\n=== steady background rate — must NOT fire ===")
    # ~1 per week for a year, and 1 this week: business as usual
    s = surge([3] + [7*i for i in range(1, 14)])
    print(f"  recent={s['recent_count']} baseline_rate={s['baseline_rate']} "
          f"ratio={s['surge_ratio']}")
    check("stays quiet on a steady rate", not s["is_emerging"], str(s))

    print("\n=== brand-new signature, no history ===")
    s = surge([1, 2, 3])
    check("fires on a new signature", s["is_emerging"], str(s))
    check("no baseline to divide by", s["baseline_rate"] == 0, str(s["baseline_rate"]))

    print("\n=== a single new incident is NOT an alert ===")
    s = surge([1])
    check("one incident stays quiet", not s["is_emerging"], str(s))

    print("\n=== multi-customer fires without a rate spike ===")
    # 3 recent, 3 in the baseline — flat rate, but three different tenants
    s = surge([1, 2, 3, 30, 50, 80],
              orgs=["acme", "helios", "vantage", "acme", "acme", "acme"])
    print(f"  recent={s['recent_count']} ratio={s['surge_ratio']} "
          f"multi_customer={s['multi_customer']}")
    check("multi-customer is its own trigger", s["is_emerging"], str(s))
    check("detected 3 tenants", s["multi_customer"], str(s))

    print("\n=== 3 this week vs 3 in the previous 90 days, one customer ===")
    # Corrected expectation: this IS an acceleration — 3 in 7 days against 3 in
    # 90 is a genuine 12x rate increase, not noise. It should fire on rate.
    s = surge([1, 2, 3, 30, 50, 80])
    check("fires on a real acceleration", s["is_emerging"], str(s))
    check("triggered by rate, not spread", s["trigger"] == "rate", str(s["trigger"]))

    print("\n=== Poisson floor blocks small-number noise ===")
    # λ ≈ 0.93/week. Seeing 2 in a week is ordinary variation.
    s = surge([3] + [7*i for i in range(1, 14)])
    check("2 observed under a floor of ~3.8", s["recent_count"] < s["poisson_floor"],
          f"recent={s['recent_count']} floor={s['poisson_floor']}")

    print("\n=== nothing recent at all ===")
    s = surge([200, 250, 300])
    check("old-only cluster stays quiet", not s["is_emerging"], str(s))
    check("ratio is zero", s["surge_ratio"] == 0, str(s["surge_ratio"]))


