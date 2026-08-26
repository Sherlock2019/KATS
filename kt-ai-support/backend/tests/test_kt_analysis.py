"""Unit tests for the KT analysis — no database, no models, no network.

These cover the one piece of logic the whole system's retrieval advantage
rests on, so they are worth being able to run in a second.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.services.kt.analysis import KTAnalysisService
from app.services.kt.signature import normalize_error_signature, signature_similarity


def spec(dimension, side, value, key=None, structured=None):
    return SimpleNamespace(
        dimension=dimension, side=side, value=value,
        structured_key=key, structured_value=structured,
        sort_order=0, created_at=None,
    )


# -----------------------------------------------------------------------------
# signature normalisation
# -----------------------------------------------------------------------------
def test_volatile_tokens_are_stripped():
    a = normalize_error_signature(
        "2026-06-01T09:12:44Z req-a91c3f2e-1111-2222-3333-444455556666 timeout after 30s")
    b = normalize_error_signature(
        "2026-08-14T22:01:09Z req-ffffffff-9999-8888-7777-666655554444 timeout after 45s")
    assert a == b, "two records of one fault must normalise identically"


def test_meaningful_constants_survive():
    """169.254.169.254 is not noise — it means 'metadata service'."""
    result = normalize_error_signature(
        "No port found in network None with IP address 169.254.169.254")
    assert "169.254.169.254" in result


def test_different_ips_do_not_collapse_into_a_match():
    a = normalize_error_signature("connection refused to 127.0.0.1")
    b = normalize_error_signature("connection refused to 10.4.2.9")
    assert a != b


def test_signature_similarity_ranks_exact_above_partial():
    exact = signature_similarity("Could not find token", "Could not find token")
    partial = signature_similarity("Could not find token", "Could not find the token quickly")
    unrelated = signature_similarity("Could not find token", "disk quota exceeded")
    assert exact == 1.0
    assert exact > partial > unrelated


# -----------------------------------------------------------------------------
# the grid
# -----------------------------------------------------------------------------
def test_grid_always_has_four_rows_even_when_empty():
    grid = KTAnalysisService.build_grid([spec("WHAT", "IS", "the API")])
    assert [r["dimension"] for r in grid["rows"]] == ["WHAT", "WHERE", "WHEN", "EXTENT"]
    assert grid["filled_cells"] == 1
    assert grid["total_cells"] == 8


def test_multiple_entries_per_cell():
    grid = KTAnalysisService.build_grid([
        spec("WHAT", "IS", "new tokens"),
        spec("WHAT", "IS", "rotated credentials"),
    ])
    assert len(grid["rows"][0]["is"]["entries"]) == 2


# -----------------------------------------------------------------------------
# derived distinctions
# -----------------------------------------------------------------------------
def test_matching_structured_keys_produce_a_distinction():
    derived = KTAnalysisService.derive_distinctions([
        spec("WHERE", "IS", "cluster A", "cluster", "cluster-a"),
        spec("WHERE", "IS_NOT", "cluster B", "cluster", "cluster-b"),
    ])
    assert len(derived) == 1
    assert derived[0]["attribute_name"] == "cluster"
    assert derived[0]["is_value"] == "cluster-a"
    assert derived[0]["is_not_value"] == "cluster-b"
    assert derived[0]["derived"] is True


def test_no_distinction_when_only_one_side_is_tagged():
    derived = KTAnalysisService.derive_distinctions([
        spec("WHERE", "IS", "cluster A", "cluster", "cluster-a"),
        spec("WHERE", "IS_NOT", "cluster B"),
    ])
    assert derived == []


def test_no_distinction_when_both_sides_share_the_value():
    derived = KTAnalysisService.derive_distinctions([
        spec("WHERE", "IS", "eu-west-1", "region", "eu-west-1"),
        spec("WHERE", "IS_NOT", "eu-west-1", "region", "eu-west-1"),
    ])
    assert derived == []


# -----------------------------------------------------------------------------
# §22 — the reason this system beats vector search
# -----------------------------------------------------------------------------
def _profile(pairs):
    return KTAnalysisService.build_profile([spec(*p) for p in pairs])


def test_both_sides_matching_beats_is_only_matching():
    query = _profile([
        ("WHAT", "IS", "newly generated tokens fail"),
        ("WHAT", "IS_NOT", "existing tokens work"),
    ])
    both = _profile([
        ("WHAT", "IS", "newly generated tokens fail"),
        ("WHAT", "IS_NOT", "existing tokens work normally"),
    ])
    is_only = _profile([
        ("WHAT", "IS", "newly generated tokens fail"),
    ])

    both_score, reasons = KTAnalysisService.kt_similarity(query, both)
    is_only_score, _ = KTAnalysisService.kt_similarity(query, is_only)

    assert both_score > is_only_score
    assert any("IS and IS NOT both match" in r for r in reasons)


def test_contradicting_is_not_scores_below_a_missing_one():
    """The case §22 exists for.

    Ticket A: new tokens fail, existing tokens work.
    Ticket B: ALL authentication fails.

    B's IS overlaps heavily — it is about authentication failing too — but
    its IS NOT contradicts: whatever broke B would also have broken the
    existing tokens that still work here. A wrong contrast is worse evidence
    than no contrast, and must score below it.
    """
    query = _profile([
        ("WHAT", "IS", "newly generated tokens fail authentication"),
        ("WHAT", "IS_NOT", "existing tokens authenticate normally"),
    ])
    contradicting = _profile([
        ("WHAT", "IS", "newly generated tokens fail authentication"),
        ("WHAT", "IS_NOT", "requests that never reach the cluster"),
    ])
    silent = _profile([
        ("WHAT", "IS", "newly generated tokens fail authentication"),
    ])

    contradicting_score, reasons = KTAnalysisService.kt_similarity(query, contradicting)
    silent_score, _ = KTAnalysisService.kt_similarity(query, silent)

    assert contradicting_score < silent_score, (
        f"a contradicting IS NOT ({contradicting_score}) must score below a missing "
        f"one ({silent_score})"
    )
    assert any("boundary differently" in r for r in reasons)


def test_unstated_query_dimensions_are_not_penalised():
    """A query that says nothing about WHEN should not punish a case that does."""
    query = _profile([("WHAT", "IS", "the checkout service")])
    rich = _profile([
        ("WHAT", "IS", "the checkout service"),
        ("WHEN", "IS", "every Tuesday at 03:00"),
        ("WHEN", "IS_NOT", "the rest of the week"),
    ])
    score, _ = KTAnalysisService.kt_similarity(query, rich)
    assert score > 0.5


def test_empty_profiles_score_zero():
    assert KTAnalysisService.kt_similarity(_profile([]), _profile([]))[0] == 0.0


def test_what_is_weighted_above_extent():
    """WHAT names the failing thing; EXTENT only sizes it."""
    query = _profile([
        ("WHAT", "IS", "token validation"), ("WHAT", "IS_NOT", "session validation"),
        ("EXTENT", "IS", "forty percent"), ("EXTENT", "IS_NOT", "the remainder"),
    ])
    what_match = _profile([
        ("WHAT", "IS", "token validation"), ("WHAT", "IS_NOT", "session validation"),
        ("EXTENT", "IS", "one node"), ("EXTENT", "IS_NOT", "all other nodes"),
    ])
    extent_match = _profile([
        ("WHAT", "IS", "disk throughput"), ("WHAT", "IS_NOT", "network throughput"),
        ("EXTENT", "IS", "forty percent"), ("EXTENT", "IS_NOT", "the remainder"),
    ])

    assert (KTAnalysisService.kt_similarity(query, what_match)[0]
            > KTAnalysisService.kt_similarity(query, extent_match)[0])


# -----------------------------------------------------------------------------
# gaps
# -----------------------------------------------------------------------------
def test_missing_is_not_beside_a_stated_is_is_the_top_gap():
    profile = _profile([("WHAT", "IS", "the checkout service")])
    gaps = KTAnalysisService.gaps(profile)
    assert gaps[0]["dimension"] == "WHAT"
    assert gaps[0]["side"] == "IS_NOT"
    assert gaps[0]["priority"] == 1.0


def test_has_contrast_requires_both_sides_of_one_dimension():
    assert not _profile([
        ("WHAT", "IS", "a"), ("WHERE", "IS_NOT", "b"),
    ]).has_contrast
    assert _profile([
        ("WHAT", "IS", "a"), ("WHAT", "IS_NOT", "b"),
    ]).has_contrast
