-- =============================================================================
-- Legacy ingestion.
--
-- Adds three things:
--
--   1. Provenance on every record and every chunk. `source_type` says where a
--      fact came from and `source_trust` says how much to weight it. These are
--      SEPARATE from knowledge_quality_score, which measures the content: a
--      legacy ticket with a verified cause and a test is better evidence than
--      a KT ticket someone abandoned half-filled, and the ranking has to be
--      able to express that.
--
--   2. A Bronze layer. `legacy_raw` is append-only and never edited. It is
--      what makes the migration re-runnable: change detection is a hash
--      comparison, and re-extraction with a better model reads from here
--      rather than going back to a production system we do not own.
--
--   3. The KB as its own object. A KB article is not a ticket and must not be
--      converted into a fake one - different shape, different chunking,
--      different trust. `rag_chunks.object_id` therefore replaces `ticket_id`,
--      because a chunk can now belong to either.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- provenance on incidents
-- -----------------------------------------------------------------------------
ALTER TABLE support_tickets
    ADD COLUMN IF NOT EXISTS source_type       TEXT NOT NULL DEFAULT 'new_kt'
        CHECK (source_type IN ('new_kt', 'legacy_verified', 'legacy_extracted',
                               'legacy_raw_only')),
    -- The id this record has in the legacy system. NULL for KT-native tickets.
    ADD COLUMN IF NOT EXISTS source_ref        TEXT,
    ADD COLUMN IF NOT EXISTS source_hash       TEXT,
    -- Set the moment a person edits an imported record. Re-extraction must
    -- never overwrite a human correction, however much better the model gets.
    ADD COLUMN IF NOT EXISTS human_reviewed    BOOLEAN NOT NULL DEFAULT FALSE,
    -- Which extractor produced the KT fields. Lets a later pass re-process
    -- only what an improved extractor would actually change.
    ADD COLUMN IF NOT EXISTS extractor_version TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_tickets_source_ref
    ON support_tickets (source_ref) WHERE source_ref IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_tickets_source_type ON support_tickets (source_type);
CREATE INDEX IF NOT EXISTS idx_tickets_reviewed    ON support_tickets (human_reviewed);


-- -----------------------------------------------------------------------------
-- kb_articles - curated knowledge, NOT incidents
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS kb_articles (
    id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    kb_ref        TEXT UNIQUE NOT NULL,          -- the legacy KB id
    title         TEXT NOT NULL,

    product       TEXT,
    component     TEXT,
    version       TEXT,
    environment   TEXT,

    -- Sectioned rather than one body: each becomes its own chunk, so
    -- "what are the symptoms" and "what is the procedure" retrieve separately.
    summary       TEXT,
    symptoms      TEXT,
    diagnostics   TEXT,
    procedure     TEXT,
    resolution    TEXT,
    prevention    TEXT,
    body          TEXT,                          -- the original, unsplit

    status        TEXT NOT NULL DEFAULT 'published'
                  CHECK (status IN ('published', 'draft', 'archived')),
    source_type   TEXT NOT NULL DEFAULT 'legacy_kb',
    source_hash   TEXT,

    error_signature_norm TEXT,
    metadata      JSONB NOT NULL DEFAULT '{}'::jsonb,

    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TRIGGER trg_kb_articles_updated
    BEFORE UPDATE ON kb_articles
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

CREATE INDEX IF NOT EXISTS idx_kb_product   ON kb_articles (product, component);
CREATE INDEX IF NOT EXISTS idx_kb_status    ON kb_articles (status);
CREATE INDEX IF NOT EXISTS idx_kb_signature ON kb_articles (error_signature_norm);
CREATE INDEX IF NOT EXISTS idx_kb_metadata  ON kb_articles USING GIN (metadata);


-- -----------------------------------------------------------------------------
-- incident <-> KB links
--
-- Imported straight from the legacy system's own ticket/KB associations. This
-- is the highest-value table in the whole migration: human-curated, no
-- extraction, no confidence scoring, and it gives problem clusters on day one.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS incident_kb_link (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    ticket_id   UUID REFERENCES support_tickets(id) ON DELETE CASCADE,
    kb_id       UUID REFERENCES kb_articles(id)     ON DELETE CASCADE,
    relation    TEXT NOT NULL DEFAULT 'resolved_by'
                CHECK (relation IN ('resolved_by', 'related_to', 'caused_by')),
    source_type TEXT NOT NULL DEFAULT 'legacy_kb',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (ticket_id, kb_id, relation)
);

CREATE INDEX IF NOT EXISTS idx_link_ticket ON incident_kb_link (ticket_id);
CREATE INDEX IF NOT EXISTS idx_link_kb     ON incident_kb_link (kb_id);


-- -----------------------------------------------------------------------------
-- Bronze - raw legacy payloads, append-only
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS legacy_raw (
    source_ref   TEXT NOT NULL,
    source_kind  TEXT NOT NULL CHECK (source_kind IN ('ticket', 'kb')),
    source_hash  TEXT NOT NULL,
    payload      JSONB NOT NULL,

    -- Where the pipeline got to with this record, so a crash at row 1,400
    -- resumes at 1,400 rather than at 1.
    status       TEXT NOT NULL DEFAULT 'fetched'
                 CHECK (status IN ('fetched', 'mapped', 'extracted', 'skipped', 'failed')),
    status_detail TEXT,

    fetched_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processed_at TIMESTAMPTZ,
    PRIMARY KEY (source_kind, source_ref)
);

CREATE INDEX IF NOT EXISTS idx_legacy_raw_status ON legacy_raw (source_kind, status);
CREATE INDEX IF NOT EXISTS idx_legacy_raw_hash   ON legacy_raw (source_hash);


-- -----------------------------------------------------------------------------
-- sync watermarks - "what changed since I last looked"
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS legacy_sync_state (
    source_kind    TEXT PRIMARY KEY,
    last_synced_at TIMESTAMPTZ,
    last_run_at    TIMESTAMPTZ,
    rows_seen      INTEGER NOT NULL DEFAULT 0,
    rows_changed   INTEGER NOT NULL DEFAULT 0
);


-- -----------------------------------------------------------------------------
-- rag_chunks: a chunk can now belong to a ticket OR a KB article
-- -----------------------------------------------------------------------------
ALTER TABLE rag_chunks
    ADD COLUMN IF NOT EXISTS object_type  TEXT NOT NULL DEFAULT 'incident'
        CHECK (object_type IN ('incident', 'kb_article')),
    ADD COLUMN IF NOT EXISTS object_id    UUID,
    ADD COLUMN IF NOT EXISTS source_type  TEXT NOT NULL DEFAULT 'new_kt',
    -- How much to trust the SOURCE. quality_score already measures the
    -- CONTENT; keeping them apart is what lets a verified legacy ticket
    -- outrank a half-empty new one.
    ADD COLUMN IF NOT EXISTS source_trust NUMERIC(3,2) NOT NULL DEFAULT 1.00;

-- Backfill object_id from the existing ticket_id, then make it the real key.
UPDATE rag_chunks SET object_id = ticket_id WHERE object_id IS NULL;

ALTER TABLE rag_chunks ALTER COLUMN ticket_id DROP NOT NULL;

CREATE INDEX IF NOT EXISTS idx_chunks_object ON rag_chunks (object_type, object_id);
CREATE INDEX IF NOT EXISTS idx_chunks_source ON rag_chunks (source_type);

-- The old uniqueness was (ticket_id, chunk_type, title); it has to move to
-- object_id so KB chunks are covered by the same upsert path.
ALTER TABLE rag_chunks DROP CONSTRAINT IF EXISTS rag_chunks_ticket_id_chunk_type_title_key;
CREATE UNIQUE INDEX IF NOT EXISTS idx_chunks_object_unique
    ON rag_chunks (object_type, object_id, chunk_type, title);


-- -----------------------------------------------------------------------------
-- KB chunk types
-- -----------------------------------------------------------------------------
ALTER TABLE rag_chunks DROP CONSTRAINT IF EXISTS rag_chunks_chunk_type_check;
ALTER TABLE rag_chunks ADD CONSTRAINT rag_chunks_chunk_type_check
    CHECK (chunk_type IN (
        'PROBLEM','SYMPTOM','CONTEXT','KT_SPECIFICATION','DISTINCTIONS',
        'CHANGES','HYPOTHESIS','REJECTED_HYPOTHESIS','EVIDENCE',
        'DIAGNOSTIC_TEST','ROOT_CAUSE','WORKAROUND','RESOLUTION',
        'PREVENTION','TIMELINE','FULL_CASE_SUMMARY',
        'KB_SUMMARY','KB_PROCEDURE','KB_DIAGNOSTICS'
    ));
