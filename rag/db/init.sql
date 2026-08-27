-- =============================================================================
-- KATS RAG store — PostgreSQL + pgvector
--
-- Two tables, deliberately:
--
--   ticket        the structured truth. Facets live here as columns so they can
--                 be filtered, counted and displayed. NOTHING here is embedded.
--   ticket_chunk  the retrieval surface. One row per KT section per ticket,
--                 carrying the text that IS embedded plus a tsvector for the
--                 lexical half of hybrid search.
--
-- The split matters. Embedding a whole ticket as one blob buries the two
-- sentences that discriminate ("what is NOT affected", "what changed") under
-- thirty fields of scaffolding, and embedding the facets themselves just adds
-- noise to the vector that a WHERE clause does better.
--
-- Applied automatically by docker-compose on first boot of an empty volume,
-- and re-appliable by hand: everything here is IF NOT EXISTS.
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS vector;

-- -----------------------------------------------------------------------------
-- ticket
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ticket (
    ticket_id            TEXT PRIMARY KEY,
    customer_id          TEXT NOT NULL,
    -- intake     = a customer's report. A question.
    -- resolution = a worked ticket with a cause and a fix. An answer.
    -- kb         = a published KB article. Shared knowledge, not a ticket, and
    --              the only doc_type that is not tenant-scoped (see below).
    doc_type             TEXT NOT NULL DEFAULT 'intake'
                         CHECK (doc_type IN ('intake', 'resolution', 'kb')),
    doc_version          INTEGER NOT NULL DEFAULT 1,
    title                TEXT,
    status               TEXT NOT NULL DEFAULT 'new',
    opened_at            TEXT,
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- facets: filters, never embedded
    site                 TEXT,
    service_component    TEXT,
    category             TEXT,
    environment          TEXT,
    severity             SMALLINT,
    blast_radius         TEXT,
    impact_trend         TEXT,
    quality_score        SMALLINT,

    error_signature_raw  TEXT,
    error_signature_norm TEXT,

    -- Where the record came from. 'new_kt' is the wizard; the legacy_*
    -- values are set by the CORE import. Separate from quality: a
    -- verified legacy ticket can outrank a half-empty new one.
    source_type          TEXT NOT NULL DEFAULT 'new_kt',

    fields               JSONB NOT NULL DEFAULT '{}'::jsonb,
    summary              JSONB NOT NULL DEFAULT '[]'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_ticket_customer   ON ticket (customer_id, status);
CREATE INDEX IF NOT EXISTS idx_ticket_signature  ON ticket (error_signature_norm);
CREATE INDEX IF NOT EXISTS idx_ticket_component  ON ticket (service_component, site);
CREATE INDEX IF NOT EXISTS idx_ticket_doc_type   ON ticket (doc_type);

-- -----------------------------------------------------------------------------
-- ticket_chunk
--
-- customer_id is denormalised on purpose. Tenant isolation is the one
-- invariant this schema must not lose, and a filter that needs a join is a
-- filter someone eventually forgets.
--
-- The single exception is customer_id = '*', which means SHARED KNOWLEDGE:
-- published KB articles, which are scrubbed of customer identity before they
-- are written and are meant to be found by everyone. A tenant-scoped query
-- returns its own rows plus '*', and nothing else. Ticket rows must never be
-- written with '*'.
--
-- The vector dimension is fixed at 768 to match the default embedder
-- (nomic-embed-text). Changing EMBED_MODEL to one with a different dimension
-- means altering this column AND re-embedding every row — the backend refuses
-- to start on a mismatch rather than writing garbage.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ticket_chunk (
    chunk_id     BIGSERIAL PRIMARY KEY,
    ticket_id    TEXT NOT NULL REFERENCES ticket (ticket_id) ON DELETE CASCADE,
    customer_id  TEXT NOT NULL,
    doc_type     TEXT NOT NULL
                 CHECK (doc_type IN ('intake', 'resolution', 'kb')),
    section      TEXT NOT NULL,
    content      TEXT NOT NULL,
    embedding    VECTOR(768),
    embed_model  TEXT,
    source_type  TEXT NOT NULL DEFAULT 'new_kt',
    tsv          TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_chunk_ticket   ON ticket_chunk (ticket_id);
CREATE INDEX IF NOT EXISTS idx_chunk_tenant   ON ticket_chunk (customer_id, doc_type);
CREATE INDEX IF NOT EXISTS idx_chunk_tsv      ON ticket_chunk USING GIN (tsv);

-- HNSW beats ivfflat here: the table is small and grows one ticket at a time,
-- and ivfflat needs a populated table before its lists are meaningful.
CREATE INDEX IF NOT EXISTS idx_chunk_embedding
    ON ticket_chunk USING hnsw (embedding vector_cosine_ops);

-- -----------------------------------------------------------------------------
-- chat_log — what support asked, what came back, and which chunks were used.
-- The evidence ids are the audit trail: "the agent said X" is only reviewable
-- if you can see what it read.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS chat_log (
    id           BIGSERIAL PRIMARY KEY,
    customer_id  TEXT,
    question     TEXT NOT NULL,
    answer       TEXT,
    model        TEXT,
    evidence     JSONB NOT NULL DEFAULT '[]'::jsonb,
    latency_ms   INTEGER,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_chat_log_created ON chat_log (created_at DESC);


-- -----------------------------------------------------------------------------
-- Migrations for databases that already exist.
--
-- `CREATE TABLE IF NOT EXISTS` above does nothing to a table that is already
-- there, so a column added later never appears on a running store. These
-- ALTERs are what make this file genuinely re-appliable rather than only
-- correct on an empty volume.
-- -----------------------------------------------------------------------------
ALTER TABLE ticket
    ADD COLUMN IF NOT EXISTS source_type TEXT NOT NULL DEFAULT 'new_kt';
ALTER TABLE ticket_chunk
    ADD COLUMN IF NOT EXISTS source_type TEXT NOT NULL DEFAULT 'new_kt';

CREATE INDEX IF NOT EXISTS idx_ticket_source ON ticket (source_type);
CREATE INDEX IF NOT EXISTS idx_chunk_source  ON ticket_chunk (source_type);
