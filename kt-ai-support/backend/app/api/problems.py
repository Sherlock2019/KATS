"""The problem register: recurrence, clusters and emerging incidents.

Every response that carries a statistic carries its denominators too. The
UI is expected to show them — "19 of 23 members, 6 with a verified cause" is
a usable sentence; "19 of 23 were caused by DNS" is a claim the data does not
support once AI-extracted legacy records are in the corpus.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_db
from app.services.problems.clustering import ClusteringService

router = APIRouter(prefix="/api/problems", tags=["problems"])


class ProblemUpdate(BaseModel):
    """The only fields a human owns. A rebuild never touches these."""

    status: str | None = Field(default=None, pattern="^(OPEN|KNOWN_ERROR|FIX_IN_PROGRESS|RESOLVED|IGNORED)$")
    permanent_fix: str | None = None
    owner: str | None = None
    notes: str | None = None
    kb_ref: str | None = None


def _confidence_line(row: dict) -> str:
    """One sentence stating what the numbers do and do not support.

    Generated here rather than left to each caller, because the failure mode
    is a dashboard that shows `dominant_cause` next to `member_count` and
    implies the whole cluster shares it.
    """
    members = row["member_count"]
    verified = row["verified_count"]
    if verified == 0:
        return (f"{members} incidents share this signature. None has a verified "
                f"cause, so the grouping is a pattern, not a finding.")
    if row.get("dominant_cause"):
        return (f"{members} incidents share this signature. {verified} have a "
                f"verified cause, and {row['dominant_cause_count']} of those "
                f"{verified} point at the same thing.")
    return f"{members} incidents share this signature; {verified} have a verified cause."


@router.get("")
def list_problems(
    db: Session = Depends(get_db),
    emerging_only: bool = Query(default=False),
    status: str | None = None,
    min_members: int = Query(default=2, ge=1),
    limit: int = Query(default=50, ge=1, le=500),
) -> dict:
    where = ["member_count >= :min_members"]
    params: dict = {"min_members": min_members, "limit": limit}
    if emerging_only:
        where.append("is_emerging = TRUE")
    if status:
        where.append("status = :status")
        params["status"] = status

    rows = db.execute(text(f"""
        SELECT * FROM problem_records
        WHERE {' AND '.join(where)}
        ORDER BY is_emerging DESC, surge_ratio DESC, member_count DESC
        LIMIT :limit
    """), params).fetchall()

    problems = []
    for r in rows:
        m = dict(r._mapping)
        problems.append({
            "id": m["id"], "title": m["title"],
            "product": m["product"], "component": m["component"],
            "signature": (m["signature_norm"] or "")[:160],
            "members": m["member_count"],
            "verified": m["verified_count"],
            "customers_affected": m["customers_affected"],
            "dominant_cause": m["dominant_cause"],
            "dominant_cause_count": m["dominant_cause_count"],
            "cause_category": m["cause_category"],
            "first_seen": m["first_seen_at"], "last_seen": m["last_seen_at"],
            "recent_count": m["recent_count"],
            "surge_ratio": float(m["surge_ratio"] or 0),
            "is_emerging": m["is_emerging"],
            "status": m["status"], "owner": m["owner"],
            "permanent_fix": m["permanent_fix"],
            "confidence": _confidence_line(m),
            "source_mix": (m["metadata"] or {}).get("source_mix", {}),
        })

    return {"count": len(problems), "problems": problems}


@router.get("/emerging")
def emerging(db: Session = Depends(get_db),
             limit: int = Query(default=20, ge=1, le=100)) -> dict:
    """What is happening right now that was not happening before.

    This is the endpoint that turns the system from reactive to proactive:
    nobody has to ask a question for it to have an answer.
    """
    rows = db.execute(text("""
        SELECT * FROM problem_records
        WHERE is_emerging = TRUE
        ORDER BY surge_ratio DESC, recent_count DESC
        LIMIT :limit
    """), {"limit": limit}).fetchall()

    alerts = []
    for r in rows:
        m = dict(r._mapping)
        meta = m["metadata"] or {}
        multi = meta.get("multi_customer")

        # State the trigger that actually fired. A spread alert explained as
        # a rate ("1.0x") reads as a broken detector and gets ignored.
        if meta.get("trigger") == "spread":
            why = (f"{m['recent_count']} in the last window across "
                   f"{m['customers_affected']} customers — the same fault on more "
                   f"than one tenant, so it is a platform problem rather than a "
                   f"configuration one")
        elif m["baseline_rate"] and float(m["baseline_rate"]) > 0:
            why = (f"{m['recent_count']} in the last window against a baseline of "
                   f"{float(m['baseline_rate']):.1f} — {float(m['surge_ratio']):.1f}x, "
                   f"clearing a noise floor of {float(meta.get('poisson_floor') or 0):.1f}")
            if multi:
                why += f", across {m['customers_affected']} customers"
        else:
            why = f"{m['recent_count']} incidents and no prior history — new signature"
            if multi:
                why += f", across {m['customers_affected']} customers"

        alerts.append({
            "id": m["id"], "title": m["title"],
            "component": m["component"], "product": m["product"],
            "recent_count": m["recent_count"],
            "surge_ratio": float(m["surge_ratio"] or 0),
            "customers_affected": m["customers_affected"],
            "multi_customer": bool(multi),
            "why": why,
            "known_fix": m["permanent_fix"] or m["dominant_cause"],
            "status": m["status"],
            "confidence": _confidence_line(m),
        })

    last = db.execute(text(
        "SELECT ran_at, window_days FROM problem_detection_runs "
        "ORDER BY ran_at DESC LIMIT 1")).fetchone()

    return {
        "count": len(alerts),
        "alerts": alerts,
        "last_run": last[0] if last else None,
        "window_days": last[1] if last else None,
    }


@router.get("/{problem_id}")
def get_problem(problem_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    row = db.execute(text("SELECT * FROM problem_records WHERE id = :id"),
                     {"id": problem_id}).fetchone()
    if row is None:
        raise HTTPException(404, "Problem not found")
    m = dict(row._mapping)

    members = db.execute(text("""
        SELECT t.id, t.ticket_number, t.title, t.organization, t.severity,
               t.status, t.root_cause, t.root_cause_status, t.source_type,
               t.first_seen_at, t.resolved_at, pm.matched_on
        FROM problem_members pm
        JOIN support_tickets t ON t.id = pm.ticket_id
        WHERE pm.problem_id = :id
        ORDER BY t.first_seen_at DESC NULLS LAST
    """), {"id": problem_id}).fetchall()

    return {
        **{k: m[k] for k in (
            "id", "title", "signature_norm", "product", "component",
            "member_count", "verified_count", "customers_affected",
            "dominant_cause", "dominant_cause_count", "cause_category",
            "first_seen_at", "last_seen_at", "recent_count", "surge_ratio",
            "is_emerging", "status", "permanent_fix", "owner", "notes", "kb_ref")},
        "confidence": _confidence_line(m),
        "metadata": m["metadata"],
        "members": [dict(r._mapping) for r in members],
    }


@router.patch("/{problem_id}")
def update_problem(problem_id: uuid.UUID, payload: ProblemUpdate,
                   db: Session = Depends(get_db)) -> dict:
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(422, "Nothing to update")

    sets = ", ".join(f"{k} = :{k}" for k in changes)
    row = db.execute(
        text(f"UPDATE problem_records SET {sets}, updated_at = NOW() "
             f"WHERE id = :id RETURNING id, status, owner, permanent_fix"),
        {**changes, "id": problem_id},
    ).fetchone()
    if row is None:
        raise HTTPException(404, "Problem not found")
    db.commit()
    return dict(row._mapping)


@router.post("/recluster")
def recluster(db: Session = Depends(get_db),
              window_days: int = Query(default=7, ge=1, le=90),
              baseline_days: int = Query(default=90, ge=7, le=730)) -> dict:
    """Rebuild every problem from the tickets.

    Cheap enough to run on a schedule: one query plus a dictionary. Human-set
    status, owner and permanent_fix survive.
    """
    return ClusteringService.rebuild(db, window_days=window_days,
                                     baseline_days=baseline_days)
