-- =============================================================================
-- KT AI Support — initial schema
--
-- The governing principle, and the reason this schema has twelve tables
-- instead of one with a notes column:
--
--     PostgreSQL is the knowledge model. pgvector is one retrieval
--     mechanism layered on top of it.
--
-- Everything an engineer can be wrong about is stored as its own kind of
-- record, and the kinds are never collapsed into each other:
--
--     FACT        ticket columns, ticket_timeline
--     SPECIFICATION  kt_specifications  (IS / IS NOT, per dimension)
--     DISTINCTION    kt_distinctions
--     CHANGE         kt_changes
--     HYPOTHESIS     kt_hypotheses      (including REJECTED ones — kept)
--     EVIDENCE       ticket_evidence    (FOR / AGAINST / NEUTRAL)
--     TEST + RESULT  diagnostic_tests
--     ROOT CAUSE     root_causes        (with a confidence ladder)
--     ACTION         ticket_actions
--
-- An LLM that cannot tell a hypothesis from a confirmed root cause will
-- state guesses as fact. That distinction cannot be recovered downstream if
-- the database threw it away, which is why it is enforced here.
--
-- ${EMBEDDING_DIM} is substituted by migrations/run.py from the configured
-- embedding model. Do not hardcode a dimension here — changing the embedder
-- must be a config change plus a re-embed, not a schema edit.
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;      -- fuzzy keyword matching
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- -----------------------------------------------------------------------------
-- Human-readable ticket numbers: INC-000001, INC-000002, …
-- A UUID is the key; the number is what people say out loud.
-- -----------------------------------------------------------------------------
CREATE SEQUENCE IF NOT EXISTS ticket_number_seq START 1;

CREATE OR REPLACE FUNCTION next_ticket_number() RETURNS TEXT AS $$
    SELECT 'INC-' || LPAD(nextval('ticket_number_seq')::TEXT, 6, '0');
$$ LANGUAGE SQL;

CREATE OR REPLACE FUNCTION touch_updated_at() RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;


-- =============================================================================
-- support_tickets — the incident itself
-- =============================================================================
CREATE TABLE IF NOT EXISTS support_tickets (
    id                      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    ticket_number           TEXT UNIQUE NOT NULL DEFAULT next_ticket_number(),
    title                   TEXT NOT NULL,

    status                  TEXT NOT NULL DEFAULT 'NEW'
                            CHECK (status IN ('NEW','TRIAGE','INVESTIGATING','TESTING',
                                              'IDENTIFIED','MITIGATED','RESOLVED','CLOSED')),
    priority                TEXT CHECK (priority IN ('P1','P2','P3','P4')),
    severity                TEXT CHECK (severity IN ('S1','S2','S3','S4')),

    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at             TIMESTAMPTZ,

    customer_name           TEXT,
    organization            TEXT,

    product                 TEXT,
    product_version         TEXT,

    service                 TEXT,
    component               TEXT,
    subcomponent            TEXT,

    environment             TEXT,
    environment_type        TEXT CHECK (environment_type IN
                                ('production','staging','test','development','lab')),

    operating_system        TEXT,
    cloud_provider          TEXT,
    region                  TEXT,
    datacenter              TEXT,
    cluster                 TEXT,
    node                    TEXT,

    business_impact         TEXT,
    technical_impact        TEXT,

    users_affected          INTEGER,
    percentage_affected     NUMERIC(5,2)
                            CHECK (percentage_affected IS NULL
                                   OR (percentage_affected >= 0 AND percentage_affected <= 100)),

    problem_summary         TEXT,

    -- The deviation, stated as two halves. One field holding "it's broken"
    -- is not a deviation; a deviation is the gap between these two.
    expected_behavior       TEXT,
    actual_behavior         TEXT,

    first_seen_at           TIMESTAMPTZ,
    last_known_good_at      TIMESTAMPTZ,

    error_code              TEXT,
    error_message           TEXT,
    -- Volatile tokens stripped (timestamps, UUIDs, request ids, host IPs) so
    -- the same fault matches itself across two tickets. Written by the
    -- application, not by a trigger, because the rules are domain knowledge.
    error_signature_norm    TEXT,

    root_cause_status       TEXT NOT NULL DEFAULT 'UNKNOWN'
                            CHECK (root_cause_status IN
                                ('UNKNOWN','SUSPECTED','PROBABLE','HIGH_CONFIDENCE','CONFIRMED')),
    root_cause              TEXT,
    root_cause_confidence   NUMERIC(3,2)
                            CHECK (root_cause_confidence IS NULL
                                   OR (root_cause_confidence >= 0 AND root_cause_confidence <= 1)),

    workaround              TEXT,
    resolution_summary      TEXT,
    prevention_summary      TEXT,

    -- 0.0 unusable → 1.0 gold-standard. Recomputed by KnowledgeQualityService
    -- whenever the ticket changes; boosts retrieval ranking.
    knowledge_quality_score NUMERIC(3,2) NOT NULL DEFAULT 0
                            CHECK (knowledge_quality_score >= 0 AND knowledge_quality_score <= 1),

    created_by              TEXT,
    assigned_to             TEXT,

    -- Free-form extras that do not deserve a column. Indexed with GIN.
    metadata                JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TRIGGER trg_support_tickets_updated
    BEFORE UPDATE ON support_tickets
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

CREATE INDEX IF NOT EXISTS idx_tickets_number       ON support_tickets (ticket_number);
CREATE INDEX IF NOT EXISTS idx_tickets_product      ON support_tickets (product, product_version);
CREATE INDEX IF NOT EXISTS idx_tickets_component    ON support_tickets (component, subcomponent);
CREATE INDEX IF NOT EXISTS idx_tickets_service      ON support_tickets (service);
CREATE INDEX IF NOT EXISTS idx_tickets_environment  ON support_tickets (environment, environment_type);
CREATE INDEX IF NOT EXISTS idx_tickets_error_code   ON support_tickets (error_code);
CREATE INDEX IF NOT EXISTS idx_tickets_signature    ON support_tickets (error_signature_norm);
CREATE INDEX IF NOT EXISTS idx_tickets_severity     ON support_tickets (severity);
CREATE INDEX IF NOT EXISTS idx_tickets_priority     ON support_tickets (priority);
CREATE INDEX IF NOT EXISTS idx_tickets_status       ON support_tickets (status);
CREATE INDEX IF NOT EXISTS idx_tickets_created_at   ON support_tickets (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tickets_resolved_at  ON support_tickets (resolved_at DESC);
CREATE INDEX IF NOT EXISTS idx_tickets_rc_status    ON support_tickets (root_cause_status);
CREATE INDEX IF NOT EXISTS idx_tickets_cloud        ON support_tickets (cloud_provider, region);
CREATE INDEX IF NOT EXISTS idx_tickets_metadata     ON support_tickets USING GIN (metadata);

-- Full-text over the narrative fields. Vector search does not replace this:
-- an exact error string is a keyword problem, not a semantic one.
CREATE INDEX IF NOT EXISTS idx_tickets_fts ON support_tickets USING GIN (
    to_tsvector('english',
        COALESCE(title,'') || ' ' || COALESCE(problem_summary,'') || ' ' ||
        COALESCE(expected_behavior,'') || ' ' || COALESCE(actual_behavior,'') || ' ' ||
        COALESCE(error_message,'') || ' ' || COALESCE(root_cause,'') || ' ' ||
        COALESCE(resolution_summary,''))
);

-- Trigram index for fuzzy error-message matching ("Could not find token"
-- vs "could not find the token").
CREATE INDEX IF NOT EXISTS idx_tickets_error_trgm
    ON support_tickets USING GIN (error_message gin_trgm_ops);


-- =============================================================================
-- kt_specifications — IS / IS NOT, one row per entry per dimension
--
-- Deliberately NOT one big text field. "WHERE IS cluster-a / WHERE IS NOT
-- cluster-b" is a comparison a machine can make; the same thing written as
-- a paragraph is not. structured_key/value is what makes that comparison
-- exact rather than lexical.
-- =============================================================================
CREATE TABLE IF NOT EXISTS kt_specifications (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    ticket_id           UUID NOT NULL REFERENCES support_tickets(id) ON DELETE CASCADE,

    dimension           TEXT NOT NULL CHECK (dimension IN ('WHAT','WHERE','WHEN','EXTENT')),
    side                TEXT NOT NULL CHECK (side IN ('IS','IS_NOT')),

    value               TEXT NOT NULL,
    description         TEXT,

    -- Optional structured form, e.g. ('cluster', 'cluster-a'). When both
    -- sides carry the same structured_key, the difference between their
    -- values IS a distinction — see kt_distinctions.
    structured_key      TEXT,
    structured_value    TEXT,

    evidence_reference  TEXT,

    sort_order          INTEGER NOT NULL DEFAULT 0,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ktspec_ticket    ON kt_specifications (ticket_id, dimension, side);
CREATE INDEX IF NOT EXISTS idx_ktspec_key       ON kt_specifications (structured_key, structured_value);
CREATE INDEX IF NOT EXISTS idx_ktspec_value_trgm
    ON kt_specifications USING GIN (value gin_trgm_ops);


-- =============================================================================
-- kt_distinctions — what is different between the IS and the IS NOT
-- =============================================================================
CREATE TABLE IF NOT EXISTS kt_distinctions (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    ticket_id           UUID NOT NULL REFERENCES support_tickets(id) ON DELETE CASCADE,

    dimension           TEXT CHECK (dimension IN ('WHAT','WHERE','WHEN','EXTENT')),

    is_reference        UUID REFERENCES kt_specifications(id) ON DELETE SET NULL,
    is_not_reference    UUID REFERENCES kt_specifications(id) ON DELETE SET NULL,

    distinction         TEXT NOT NULL,

    attribute_name      TEXT,
    is_value            TEXT,
    is_not_value        TEXT,

    importance_score    NUMERIC(3,2)
                        CHECK (importance_score IS NULL
                               OR (importance_score >= 0 AND importance_score <= 1)),

    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_distinctions_ticket ON kt_distinctions (ticket_id);
CREATE INDEX IF NOT EXISTS idx_distinctions_attr   ON kt_distinctions (attribute_name);


-- =============================================================================
-- kt_changes — what changed, in or around the distinction
-- =============================================================================
CREATE TABLE IF NOT EXISTS kt_changes (
    id                      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    ticket_id               UUID NOT NULL REFERENCES support_tickets(id) ON DELETE CASCADE,

    change_type             TEXT NOT NULL CHECK (change_type IN
                                ('deployment','configuration','network','credential',
                                 'certificate','os_patch','software_upgrade','hardware',
                                 'database','policy','security','dependency','traffic',
                                 'user_behavior','unknown')),
    component               TEXT,

    description             TEXT NOT NULL,

    old_value               TEXT,
    new_value               TEXT,

    occurred_at             TIMESTAMPTZ,

    related_distinction_id  UUID REFERENCES kt_distinctions(id) ON DELETE SET NULL,

    change_source           TEXT,      -- e.g. 'ServiceNow', 'ArgoCD', 'manual'
    change_id               TEXT,      -- the external change record

    suspected_relevance     NUMERIC(3,2)
                            CHECK (suspected_relevance IS NULL
                                   OR (suspected_relevance >= 0 AND suspected_relevance <= 1)),

    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_changes_ticket   ON kt_changes (ticket_id);
CREATE INDEX IF NOT EXISTS idx_changes_type     ON kt_changes (change_type);
CREATE INDEX IF NOT EXISTS idx_changes_occurred ON kt_changes (occurred_at DESC);


-- =============================================================================
-- kt_hypotheses — possible causes
--
-- REJECTED rows are never deleted or overwritten. A refuted candidate is a
-- search-space reduction someone already paid for, and it is the single most
-- useful thing to hand the next engineer: it stops them re-running the test.
-- =============================================================================
CREATE TABLE IF NOT EXISTS kt_hypotheses (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    ticket_id           UUID NOT NULL REFERENCES support_tickets(id) ON DELETE CASCADE,

    cause               TEXT NOT NULL,
    description         TEXT,

    status              TEXT NOT NULL DEFAULT 'PROPOSED'
                        CHECK (status IN ('PROPOSED','TESTING','SUPPORTED','REJECTED','CONFIRMED')),

    probability_score   NUMERIC(3,2)
                        CHECK (probability_score IS NULL
                               OR (probability_score >= 0 AND probability_score <= 1)),
    confidence_score    NUMERIC(3,2)
                        CHECK (confidence_score IS NULL
                               OR (confidence_score >= 0 AND confidence_score <= 1)),

    rank                INTEGER,
    reasoning           TEXT,

    -- Set when a hypothesis was proposed by the assistant rather than a
    -- human, so its own suggestions are never mistaken for observations.
    proposed_by         TEXT,

    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TRIGGER trg_hypotheses_updated
    BEFORE UPDATE ON kt_hypotheses
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

CREATE INDEX IF NOT EXISTS idx_hypotheses_ticket ON kt_hypotheses (ticket_id, status);
CREATE INDEX IF NOT EXISTS idx_hypotheses_rank   ON kt_hypotheses (ticket_id, rank);


-- =============================================================================
-- ticket_evidence — what was observed, and which way it cuts
--
-- `direction` is the whole point of this table. An assistant that cannot see
-- that an observation argues AGAINST a hypothesis will keep recommending it.
-- =============================================================================
CREATE TABLE IF NOT EXISTS ticket_evidence (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    ticket_id           UUID NOT NULL REFERENCES support_tickets(id) ON DELETE CASCADE,
    hypothesis_id       UUID REFERENCES kt_hypotheses(id) ON DELETE SET NULL,

    evidence_type       TEXT NOT NULL CHECK (evidence_type IN
                            ('log','metric','trace','screenshot','configuration',
                             'command_output','test_result','user_observation',
                             'monitoring_alert','document','other')),

    direction           TEXT NOT NULL DEFAULT 'NEUTRAL'
                        CHECK (direction IN ('FOR','AGAINST','NEUTRAL')),

    title               TEXT,
    content             TEXT NOT NULL,

    source              TEXT,
    source_reference    TEXT,

    observed_at         TIMESTAMPTZ,

    reliability_score   NUMERIC(3,2)
                        CHECK (reliability_score IS NULL
                               OR (reliability_score >= 0 AND reliability_score <= 1)),

    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_evidence_ticket     ON ticket_evidence (ticket_id);
CREATE INDEX IF NOT EXISTS idx_evidence_hypothesis ON ticket_evidence (hypothesis_id, direction);
CREATE INDEX IF NOT EXISTS idx_evidence_type       ON ticket_evidence (evidence_type);


-- =============================================================================
-- diagnostic_tests — the controlled experiment, and what it actually showed
-- =============================================================================
CREATE TABLE IF NOT EXISTS diagnostic_tests (
    id                          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    ticket_id                   UUID NOT NULL REFERENCES support_tickets(id) ON DELETE CASCADE,
    hypothesis_id               UUID REFERENCES kt_hypotheses(id) ON DELETE SET NULL,

    test_name                   TEXT NOT NULL,
    objective                   TEXT,
    procedure                   TEXT,

    -- Both branches are recorded BEFORE the test runs. A test whose failing
    -- branch was never written down is not a test, it is a hope.
    expected_result_if_true     TEXT,
    expected_result_if_false    TEXT,

    actual_result               TEXT,

    result_status               TEXT NOT NULL DEFAULT 'NOT_RUN'
                                CHECK (result_status IN
                                    ('NOT_RUN','INCONCLUSIVE','SUPPORTS','REJECTS','CONFIRMS')),

    started_at                  TIMESTAMPTZ,
    completed_at                TIMESTAMPTZ,
    performed_by                TEXT,

    risk_level                  TEXT CHECK (risk_level IN ('low','medium','high')),
    reversible                  BOOLEAN NOT NULL DEFAULT TRUE,
    rollback_procedure          TEXT,

    estimated_minutes           INTEGER,

    evidence_id                 UUID REFERENCES ticket_evidence(id) ON DELETE SET NULL,

    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tests_ticket     ON diagnostic_tests (ticket_id, result_status);
CREATE INDEX IF NOT EXISTS idx_tests_hypothesis ON diagnostic_tests (hypothesis_id);


-- =============================================================================
-- ticket_actions — what was done to the system, and what it achieved
-- =============================================================================
CREATE TABLE IF NOT EXISTS ticket_actions (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    ticket_id       UUID NOT NULL REFERENCES support_tickets(id) ON DELETE CASCADE,

    action_type     TEXT NOT NULL CHECK (action_type IN
                        ('STABILIZATION','WORKAROUND','DIAGNOSTIC',
                         'CORRECTIVE','PREVENTIVE','ROLLBACK')),

    description     TEXT NOT NULL,
    procedure       TEXT,

    status          TEXT NOT NULL DEFAULT 'PLANNED'
                    CHECK (status IN ('PLANNED','IN_PROGRESS','DONE','FAILED','ROLLED_BACK')),

    performed_at    TIMESTAMPTZ,
    result          TEXT,

    -- Free-form so any metric fits: error_rate, p99_latency, availability,
    -- cpu, memory, users_affected. Compared before/after for verification.
    before_metric   JSONB NOT NULL DEFAULT '{}'::jsonb,
    after_metric    JSONB NOT NULL DEFAULT '{}'::jsonb,

    owner           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_actions_ticket ON ticket_actions (ticket_id, action_type);
CREATE INDEX IF NOT EXISTS idx_actions_before ON ticket_actions USING GIN (before_metric);
CREATE INDEX IF NOT EXISTS idx_actions_after  ON ticket_actions USING GIN (after_metric);


-- =============================================================================
-- root_causes — the conclusion, with the confidence it has actually earned
--
-- Typing a sentence into a form does not make a cause confirmed. Only
-- CONFIRMED rows get the top retrieval boost; everything below it is a
-- lead, and the assistant is told to say so.
-- =============================================================================
CREATE TABLE IF NOT EXISTS root_causes (
    id                      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    ticket_id               UUID NOT NULL REFERENCES support_tickets(id) ON DELETE CASCADE,

    cause                   TEXT NOT NULL,
    cause_category          TEXT,
    component               TEXT,

    -- Mechanism and trigger are separate on purpose: "the token was not
    -- persisted" is the mechanism, "a rotation ran at 21:58" is the trigger.
    -- A fix that addresses one and not the other is a recurrence waiting.
    mechanism               TEXT,
    trigger                 TEXT,

    verification_method     TEXT,
    verification_result     TEXT,

    confidence              TEXT NOT NULL DEFAULT 'SUSPECTED'
                            CHECK (confidence IN
                                ('SUSPECTED','PROBABLE','HIGH_CONFIDENCE','CONFIRMED')),

    confirmed_at            TIMESTAMPTZ,
    confirmed_by            TEXT,

    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_root_causes_ticket   ON root_causes (ticket_id);
CREATE INDEX IF NOT EXISTS idx_root_causes_conf     ON root_causes (confidence);
CREATE INDEX IF NOT EXISTS idx_root_causes_category ON root_causes (cause_category);


-- =============================================================================
-- ticket_timeline — when things happened, in order
-- =============================================================================
CREATE TABLE IF NOT EXISTS ticket_timeline (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    ticket_id       UUID NOT NULL REFERENCES support_tickets(id) ON DELETE CASCADE,

    event_type      TEXT NOT NULL,
    occurred_at     TIMESTAMPTZ NOT NULL,

    component       TEXT,
    description     TEXT NOT NULL,
    source          TEXT,

    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_timeline_ticket ON ticket_timeline (ticket_id, occurred_at);


-- =============================================================================
-- rag_chunks — the retrieval surface
--
-- One vector per ticket would bury the two sentences that discriminate under
-- thirty fields of scaffolding. One chunk per meaning, instead.
--
-- content_hash is what makes re-indexing cheap: on every ticket change the
-- builder regenerates all chunks, but only chunks whose hash moved are
-- re-embedded. Embedding is the slow step; hashing is free.
-- =============================================================================
CREATE TABLE IF NOT EXISTS rag_chunks (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    ticket_id           UUID NOT NULL REFERENCES support_tickets(id) ON DELETE CASCADE,

    chunk_type          TEXT NOT NULL CHECK (chunk_type IN
                            ('PROBLEM','SYMPTOM','CONTEXT','KT_SPECIFICATION','DISTINCTIONS',
                             'CHANGES','HYPOTHESIS','REJECTED_HYPOTHESIS','EVIDENCE',
                             'DIAGNOSTIC_TEST','ROOT_CAUSE','WORKAROUND','RESOLUTION',
                             'PREVENTION','TIMELINE','FULL_CASE_SUMMARY')),

    title               TEXT,
    content             TEXT NOT NULL,
    content_hash        TEXT NOT NULL,

    embedding           VECTOR(${EMBEDDING_DIM}),
    embedding_model     TEXT,

    metadata            JSONB NOT NULL DEFAULT '{}'::jsonb,

    quality_score       NUMERIC(3,2) NOT NULL DEFAULT 0,
    confidence_score    NUMERIC(3,2) NOT NULL DEFAULT 0,

    tsv                 TSVECTOR GENERATED ALWAYS AS (
                            to_tsvector('english', COALESCE(title,'') || ' ' || content)
                        ) STORED,

    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- One chunk of each type per ticket. Regeneration is an upsert on this.
    UNIQUE (ticket_id, chunk_type, title)
);

CREATE TRIGGER trg_rag_chunks_updated
    BEFORE UPDATE ON rag_chunks
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

CREATE INDEX IF NOT EXISTS idx_chunks_ticket    ON rag_chunks (ticket_id);
CREATE INDEX IF NOT EXISTS idx_chunks_type      ON rag_chunks (chunk_type);
CREATE INDEX IF NOT EXISTS idx_chunks_hash      ON rag_chunks (content_hash);
CREATE INDEX IF NOT EXISTS idx_chunks_metadata  ON rag_chunks USING GIN (metadata);
CREATE INDEX IF NOT EXISTS idx_chunks_tsv       ON rag_chunks USING GIN (tsv);

-- HNSW over ivfflat: the table grows a ticket at a time, and ivfflat's lists
-- are meaningless until it is already populated.
CREATE INDEX IF NOT EXISTS idx_chunks_embedding
    ON rag_chunks USING hnsw (embedding vector_cosine_ops);


-- =============================================================================
-- rag_queries — what was asked, what came back, and what it scored
--
-- This is what /rag-inspector reads. Retrieval quality is not debuggable
-- from the answer alone; you need the scores that produced the ranking.
-- =============================================================================
CREATE TABLE IF NOT EXISTS rag_queries (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    ticket_id           UUID REFERENCES support_tickets(id) ON DELETE SET NULL,

    query_text          TEXT NOT NULL,
    detected_metadata   JSONB NOT NULL DEFAULT '{}'::jsonb,
    applied_filters     JSONB NOT NULL DEFAULT '{}'::jsonb,
    weights             JSONB NOT NULL DEFAULT '{}'::jsonb,

    -- Per-chunk score breakdown: vector, keyword, metadata, kt, quality, final.
    results             JSONB NOT NULL DEFAULT '[]'::jsonb,

    embedding_model     TEXT,
    llm_model           TEXT,
    answer              TEXT,

    latency_ms          INTEGER,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_rag_queries_created ON rag_queries (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_rag_queries_ticket  ON rag_queries (ticket_id);


-- =============================================================================
-- schema_migrations — applied by migrations/run.py
-- =============================================================================
CREATE TABLE IF NOT EXISTS schema_migrations (
    version         TEXT PRIMARY KEY,
    embedding_dim   INTEGER,
    applied_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
