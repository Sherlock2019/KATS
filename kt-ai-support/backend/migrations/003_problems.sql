-- =============================================================================
-- Problem records — recurrence, clustering and emerging-incident detection.
--
-- The distinction ITIL draws and most ticket systems lose: an INCIDENT is one
-- customer's outage, a PROBLEM is the underlying fault that keeps producing
-- them. Incidents close; problems outlive them, and a problem's recurrence
-- count is the argument that funds a permanent fix.
--
-- Two things here are deliberate and easy to get wrong:
--
--   1. Statistics are split by verification. "19 of 23 were caused by DNS" is
--      only true if all 23 were extracted correctly - and on AI-extracted
--      legacy threads they were not. Every count is therefore stored twice:
--      how many members, and how many have a CONFIRMED cause. A cluster
--      reports both, so nobody quotes an extraction artefact as a finding.
--
--   2. Membership is derived, never hand-edited. Problems are rebuilt from
--      support_tickets on every run, so they cannot become a second source of
--      truth. The one thing a human CAN set - status, and a permanent-fix
--      note - is kept in columns the rebuild does not touch.
-- =============================================================================

CREATE TABLE IF NOT EXISTS problem_records (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    -- Stable across rebuilds: the same signature always yields the same key,
    -- so a human-set status survives re-clustering.
    cluster_key         TEXT UNIQUE NOT NULL,

    title               TEXT NOT NULL,
    signature_norm      TEXT,
    product             TEXT,
    component           TEXT,

    -- Derived on every rebuild -------------------------------------------
    member_count        INTEGER NOT NULL DEFAULT 0,
    customers_affected  INTEGER NOT NULL DEFAULT 0,
    -- Members whose root cause is CONFIRMED. The denominator that keeps the
    -- headline honest.
    verified_count      INTEGER NOT NULL DEFAULT 0,
    dominant_cause      TEXT,
    dominant_cause_count INTEGER NOT NULL DEFAULT 0,
    cause_category      TEXT,

    first_seen_at       TIMESTAMPTZ,
    last_seen_at        TIMESTAMPTZ,
    total_resolve_mins  INTEGER,

    -- Rate over the detection window vs the baseline before it. > 1 means
    -- this is happening faster than it used to.
    recent_count        INTEGER NOT NULL DEFAULT 0,
    baseline_rate       NUMERIC(8,3),
    surge_ratio         NUMERIC(8,3),
    is_emerging         BOOLEAN NOT NULL DEFAULT FALSE,

    -- Human-owned. Never overwritten by a rebuild. -----------------------
    status              TEXT NOT NULL DEFAULT 'OPEN'
                        CHECK (status IN ('OPEN', 'KNOWN_ERROR', 'FIX_IN_PROGRESS',
                                          'RESOLVED', 'IGNORED')),
    permanent_fix       TEXT,
    owner               TEXT,
    notes               TEXT,

    kb_ref              TEXT,
    metadata            JSONB NOT NULL DEFAULT '{}'::jsonb,

    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TRIGGER trg_problem_records_updated
    BEFORE UPDATE ON problem_records
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

CREATE INDEX IF NOT EXISTS idx_problem_signature ON problem_records (signature_norm);
CREATE INDEX IF NOT EXISTS idx_problem_component ON problem_records (product, component);
CREATE INDEX IF NOT EXISTS idx_problem_emerging  ON problem_records (is_emerging, surge_ratio DESC);
CREATE INDEX IF NOT EXISTS idx_problem_size      ON problem_records (member_count DESC);
CREATE INDEX IF NOT EXISTS idx_problem_status    ON problem_records (status);


-- -----------------------------------------------------------------------------
-- Membership. Rebuilt wholesale on every clustering run.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS problem_members (
    problem_id  UUID NOT NULL REFERENCES problem_records(id) ON DELETE CASCADE,
    ticket_id   UUID NOT NULL REFERENCES support_tickets(id) ON DELETE CASCADE,
    -- How the ticket joined: an exact normalised signature, a fuzzy one, or
    -- a shared component + coded cause. Shown in the UI so a surprising
    -- member can be explained rather than argued about.
    matched_on  TEXT NOT NULL DEFAULT 'signature',
    similarity  NUMERIC(4,3),
    added_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (problem_id, ticket_id)
);

CREATE INDEX IF NOT EXISTS idx_problem_members_ticket ON problem_members (ticket_id);


-- -----------------------------------------------------------------------------
-- Detection runs, so a surge alert can be traced back to the window that
-- produced it rather than being an unexplained number in a dashboard.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS problem_detection_runs (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    ran_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    window_days     INTEGER NOT NULL,
    baseline_days   INTEGER NOT NULL,
    tickets_scanned INTEGER NOT NULL DEFAULT 0,
    clusters_found  INTEGER NOT NULL DEFAULT 0,
    emerging_found  INTEGER NOT NULL DEFAULT 0,
    duration_ms     INTEGER
);

CREATE INDEX IF NOT EXISTS idx_detection_runs_ran ON problem_detection_runs (ran_at DESC);
