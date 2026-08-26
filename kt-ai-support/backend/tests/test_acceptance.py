"""§48 — the acceptance scenario, end to end through the HTTP API.

A technician opens a new incident describing the OpenCenter token failure
in their own words and fills in the KT grid. The system must find INC-000001
specifically — not one of the four near-twins that share its symptom — and
must refuse to call anything a confirmed root cause.

Requires a migrated, seeded database:

    python -m migrations.run
    python -m scripts.seed_demo_cases
    pytest tests/ -v
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from app.main import app

pytestmark = pytest.mark.skipif(
    os.environ.get("SKIP_DB_TESTS") == "1", reason="database not available"
)


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module", autouse=True)
def cleanup(client):
    """Delete every ticket these tests create.

    Without this each run leaves a near-duplicate of the §48 scenario in the
    corpus, and the next run's retrieval assertions start competing against
    the previous run's leftovers.
    """
    created: list[str] = []

    original_post = client.post

    def tracking_post(url, *args, **kwargs):
        response = original_post(url, *args, **kwargs)
        if url == "/api/tickets" and response.status_code == 201:
            created.append(response.json()["id"])
        return response

    client.post = tracking_post
    yield
    client.post = original_post

    from app.db import SessionLocal
    from app.models import SupportTicket

    db = SessionLocal()
    try:
        for ticket_id in created:
            row = db.get(SupportTicket, ticket_id)
            if row is not None:
                db.delete(row)
        db.commit()
    finally:
        db.close()


@pytest.fixture(scope="module")
def new_incident(client) -> str:
    """The §48 ticket, created through the public API."""
    response = client.post("/api/tickets", json={
        "title": "OpenCenter CLI returns 404 Could not find token",
        "product": "OpenCenter",
        "product_version": "2.4",
        "component": "authentication",
        "environment_type": "production",
        "error_code": "404",
        "error_message": "HTTP 404: Could not find token",
        "expected_behavior": "Newly generated token authenticates normally.",
        "actual_behavior": "New token produces HTTP 404.",
        "priority": "P1",
    })
    assert response.status_code == 201, response.text
    ticket = response.json()
    ticket_id = ticket["id"]

    # The KT specification, exactly as §48 states it.
    for dimension, side, value, key, structured in [
        ("WHAT", "IS", "Newly generated token", "token_age", "new"),
        ("WHAT", "IS_NOT", "Existing tokens", "token_age", "existing"),
        ("WHERE", "IS", "CLI authentication", "channel", "cli"),
        ("WHERE", "IS_NOT", "Existing web sessions", "channel", "browser"),
        ("WHEN", "IS", "After token rotation", None, None),
        ("WHEN", "IS_NOT", "Before rotation", None, None),
    ]:
        r = client.post(f"/api/tickets/{ticket_id}/kt-specifications", json={
            "dimension": dimension, "side": side, "value": value,
            "structured_key": key, "structured_value": structured,
        })
        assert r.status_code == 201, r.text

    return ticket_id


# -----------------------------------------------------------------------------
def test_ticket_number_is_human_readable(client, new_incident):
    ticket = client.get(f"/api/tickets/{new_incident}").json()
    assert ticket["ticket_number"].startswith("INC-")
    assert len(ticket["ticket_number"]) == 10


def test_error_signature_is_derived_not_supplied(client, new_incident):
    """The client never sets it; two records of one fault must normalise alike."""
    ticket = client.get(f"/api/tickets/{new_incident}").json()
    assert ticket["error_signature_norm"]
    assert "could not find token" in ticket["error_signature_norm"]


def test_kt_grid_has_four_rows_and_counts_gaps(client, new_incident):
    grid = client.get(f"/api/tickets/{new_incident}/kt-specifications").json()
    assert [row["dimension"] for row in grid["rows"]] == ["WHAT", "WHERE", "WHEN", "EXTENT"]
    assert grid["filled_cells"] == 6          # EXTENT left empty on purpose
    # And the empty EXTENT row is reported as the next thing to ask.
    assert any(g["dimension"] == "EXTENT" for g in grid["gaps"])


def test_distinctions_are_derived_from_structured_pairs(client, new_incident):
    """Both sides tagged token_age, so the distinction falls out of the data."""
    ticket = client.get(f"/api/tickets/{new_incident}").json()
    derived = ticket["derived_distinctions"]
    attributes = {d["attribute_name"] for d in derived}
    assert "token_age" in attributes
    token_age = next(d for d in derived if d["attribute_name"] == "token_age")
    assert token_age["is_value"] == "new"
    assert token_age["is_not_value"] == "existing"


# -----------------------------------------------------------------------------
def test_retrieval_finds_the_right_twin_not_just_a_similar_one(client, new_incident):
    """The §48 acceptance criterion, and the hardest thing in the system.

    Five historical cases share this symptom. The correct one matches on
    IS *and* IS NOT — new tokens fail, existing tokens work.
    """
    response = client.post("/api/rag/search", json={
        "query": "Why is authentication failing?",
        "ticket_id": new_incident,
        "top_k": 5,
    })
    assert response.status_code == 200, response.text
    results = response.json()["results"]
    assert results, "retrieval returned nothing"

    top = results[0]
    assert "newly generated" in top["content"].lower() or \
           "newly generated" in (top["title"] or "").lower(), \
           f"expected the new-token case first, got {top['ticket_number']}: {top['title']}"

    # And it must be won on KT, not by accident.
    assert top["scores"]["kt_match"] > 0.3, top["scores"]


def test_similar_cases_carry_the_confirmed_cause_and_rejected_ones(client, new_incident):
    similar = client.get(f"/api/tickets/{new_incident}/similar?top_k=5").json()["similar_cases"]
    assert similar

    # Assert on the case we expect, not on whatever happens to rank first.
    # Earlier runs leave tickets behind, and a test that depends on position 0
    # fails for reasons that have nothing to do with what it is checking.
    match = next(
        (c for c in similar if "newly generated" in (c["title"] or "").lower()), None
    )
    assert match, f"the new-token case was not retrieved: {[c['title'] for c in similar]}"

    assert match["root_cause_status"] == "CONFIRMED"
    assert "persist" in (match["root_cause"] or "").lower()
    # Rejected hypotheses travel with it — what not to re-test.
    assert match["rejected_causes"]


# -----------------------------------------------------------------------------
def test_diagnose_never_confirms_from_a_historical_match(client, new_incident):
    """The guardrail that matters most.

    INC-000001 has a CONFIRMED cause. This ticket does not. A historical
    confirmation must never leak into the new incident's verdict.
    """
    response = client.post("/api/ai/diagnose", json={
        "ticket_id": new_incident,
        "question": "Have we seen this before and what should I test next?",
    })
    assert response.status_code == 200, response.text
    answer = response.json()

    assert answer["confirmed_root_cause"] is None, \
        "a historical fix was reported as a confirmed cause for a new incident"

    assert answer["similar_cases"], "no evidence retrieved"
    assert answer["grounded_in"], "answer cites nothing"

    # Every citation must be a ticket that was actually retrieved.
    cited = {c["ticket_number"] for c in answer["similar_cases"]}
    for cause in answer["likely_causes"]:
        for source in cause["source_tickets"]:
            assert source in cited, f"invented citation {source}"


def test_next_question_asks_for_the_missing_contrast(client, new_incident):
    """EXTENT has neither side filled; that is the gap worth closing."""
    response = client.post("/api/ai/next-question", json={"ticket_id": new_incident})
    assert response.status_code == 200, response.text
    answer = response.json()
    assert answer["question"]
    assert answer["current_gaps"]


def test_next_action_prefers_a_question_over_a_test_when_there_is_no_contrast(client):
    """A ticket with an IS but no IS NOT has nothing to eliminate against."""
    created = client.post("/api/tickets", json={
        "title": "Service returns 500 intermittently",
        "actual_behavior": "Some requests fail with 500.",
    }).json()
    client.post(f"/api/tickets/{created['id']}/kt-specifications", json={
        "dimension": "WHAT", "side": "IS", "value": "The checkout service",
    })

    answer = client.post("/api/ai/next-action", json={"ticket_id": created["id"]}).json()
    assert "Answer this first" in answer["recommended_action"], answer
    assert "eliminate against" in answer["rationale"]


# -----------------------------------------------------------------------------
def test_rejecting_a_hypothesis_requires_a_reason(client, new_incident):
    """A rejection with no reason cannot stop anyone re-testing it."""
    hypothesis = client.post(f"/api/tickets/{new_incident}/hypotheses", json={
        "cause": "DNS failure",
    }).json()

    refused = client.patch(f"/api/hypotheses/{hypothesis['id']}", json={"status": "REJECTED"})
    assert refused.status_code == 422
    assert "reasoning" in refused.text

    accepted = client.patch(f"/api/hypotheses/{hypothesis['id']}", json={
        "status": "REJECTED",
        "reasoning": "DNS resolves correctly from the same host at the same time.",
    })
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "REJECTED"


def test_confirmed_root_cause_is_downgraded_without_verification(client, new_incident):
    """CONFIRMED earns the top retrieval boost, so it has to be earned."""
    response = client.post(f"/api/tickets/{new_incident}/root-cause", json={
        "cause": "Token persistence failure",
        "confidence": "CONFIRMED",
    })
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["confidence"] == "HIGH_CONFIDENCE"
    assert body["_warnings"], "silently accepted an unverified confirmation"
    assert "verification" in body["_warnings"][0].lower()


def test_a_test_result_propagates_to_its_hypothesis(client, new_incident):
    hypothesis = client.post(f"/api/tickets/{new_incident}/hypotheses", json={
        "cause": "Token generated but not persisted",
        "probability_score": 0.7,
    }).json()

    test = client.post(f"/api/tickets/{new_incident}/tests", json={
        "test_name": "Check whether the new token exists in the token store",
        "hypothesis_id": hypothesis["id"],
        "expected_result_if_true": "Token absent",
        "expected_result_if_false": "Token present",
        "risk_level": "low",
    }).json()
    assert test["_warning"] is None      # both branches recorded

    client.patch(f"/api/tests/{test['id']}", json={
        "result_status": "CONFIRMS",
        "actual_result": "Token absent from the store 200ms after creation.",
    })

    ticket = client.get(f"/api/tickets/{new_incident}").json()
    updated = next(h for h in ticket["hypotheses"] if h["id"] == hypothesis["id"])
    assert updated["status"] == "CONFIRMED"
    assert "CONFIRMS" in updated["reasoning"]


def test_one_sided_test_is_flagged(client, new_incident):
    """A test with no failing branch reads as confirmation whatever happens."""
    test = client.post(f"/api/tickets/{new_incident}/tests", json={
        "test_name": "Restart the service and see",
        "expected_result_if_true": "It works",
    }).json()
    assert test["_warning"] is not None
    assert "eliminate" in test["_warning"]


# -----------------------------------------------------------------------------
def test_reindexing_only_re_embeds_what_changed(client, new_incident):
    first = client.post(f"/api/tickets/{new_incident}/reindex").json()
    second = client.post(f"/api/tickets/{new_incident}/reindex").json()

    assert second["chunks_unchanged"] > 0
    assert second["chunks_embedded"] < first["chunks_built"], \
        "re-embedded unchanged chunks; the content hash is not working"


def test_inspector_exposes_every_score_term(client, new_incident):
    response = client.post("/api/rag/inspect", json={
        "query": "authentication 404 token",
        "ticket_id": new_incident,
        "top_k": 5,
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["rows"]
    row = body["rows"][0]
    for term in ("vector", "keyword", "metadata", "signature", "kt", "quality", "final"):
        assert term in row, f"inspector is missing the {term} column"
    assert set(body["weights"]) >= {"semantic", "keyword", "kt_match"}


def test_health_reports_models_and_counts(client):
    body = client.get("/health").json()
    assert body["database"] is True
    assert body["counts"]["tickets"] >= 20
    assert body["counts"]["embedded"] == body["counts"]["chunks"]
    assert body["embeddings"]["dim"] > 0
