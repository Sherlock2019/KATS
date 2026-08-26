"""Connection pool + the two queries that matter.

Raw psycopg3. The data layer is small enough that an ORM would add a mapping
layer between you and the one query worth reading — the hybrid search.
"""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from typing import Any, Iterable

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.config import get_settings

log = logging.getLogger("kats.db")

_pool: ConnectionPool | None = None


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            get_settings().database_url,
            min_size=1,
            max_size=8,
            kwargs={"row_factory": dict_row},
            open=True,
        )
    return _pool


@contextmanager
def cursor():
    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            yield cur


def vector_literal(values: Iterable[float]) -> str:
    """pgvector accepts '[0.1,0.2,...]'::vector — no adapter package needed."""
    return "[" + ",".join(f"{float(v):.7g}" for v in values) + "]"


# -----------------------------------------------------------------------------
# schema
# -----------------------------------------------------------------------------
def apply_schema(sql_path: str) -> None:
    """Idempotent — every statement in init.sql is IF NOT EXISTS.

    Applied on every boot rather than only on an empty volume, so adding a
    column does not mean asking the user to wipe their data.
    """
    with open(sql_path, "r", encoding="utf-8") as handle:
        sql = handle.read()
    with get_pool().connection() as conn:
        conn.execute(sql)
    log.info("schema applied from %s", sql_path)


def check_embedding_dim(expected: int) -> None:
    """Refuse to run against a column that cannot hold this embedder's output.

    Writing 1024-dim vectors into a vector(768) column fails per row, at
    ingest time, with an error nobody reads. Better to stop at boot.
    """
    with cursor() as cur:
        cur.execute(
            """
            SELECT a.atttypmod AS dim
            FROM pg_attribute a
            JOIN pg_class c ON c.oid = a.attrelid
            WHERE c.relname = 'ticket_chunk' AND a.attname = 'embedding'
            """
        )
        row = cur.fetchone()
    if not row or row["dim"] in (None, -1):
        return
    actual = int(row["dim"])
    if actual != expected:
        raise RuntimeError(
            f"ticket_chunk.embedding is vector({actual}) but the embedder produces "
            f"{expected} dimensions.\n"
            f"Either set KATS_EMBED_MODEL back to a {actual}-dim model, or change "
            f"the column in rag/db/init.sql and re-embed every row:\n"
            f"  ALTER TABLE ticket_chunk ALTER COLUMN embedding TYPE vector({expected}) "
            f"USING NULL;"
        )


# -----------------------------------------------------------------------------
# ingest
# -----------------------------------------------------------------------------
def upsert_ticket(doc: dict[str, Any], chunks: list[dict[str, Any]]) -> dict[str, Any]:
    """Replace a ticket and all of its chunks in one transaction.

    Delete-then-insert rather than diffing: a ticket has under ten chunks, and
    a partial update that leaves a stale 'resolution' chunk behind is a bug
    that only shows up as a wrong answer three weeks later.
    """
    facets = doc.get("facets") or {}

    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO ticket (
                    ticket_id, customer_id, doc_type, title, status, opened_at,
                    updated_at, site, service_component, category, environment,
                    severity, blast_radius, impact_trend, quality_score,
                    error_signature_raw, error_signature_norm, fields, summary
                ) VALUES (
                    %(ticket_id)s, %(customer_id)s, %(doc_type)s, %(title)s,
                    %(status)s, %(opened_at)s, NOW(), %(site)s,
                    %(service_component)s, %(category)s, %(environment)s,
                    %(severity)s, %(blast_radius)s, %(impact_trend)s,
                    %(quality_score)s, %(error_signature_raw)s,
                    %(error_signature_norm)s, %(fields)s, %(summary)s
                )
                ON CONFLICT (ticket_id) DO UPDATE SET
                    customer_id          = EXCLUDED.customer_id,
                    doc_type             = EXCLUDED.doc_type,
                    doc_version          = ticket.doc_version + 1,
                    title                = EXCLUDED.title,
                    status               = EXCLUDED.status,
                    opened_at            = COALESCE(ticket.opened_at, EXCLUDED.opened_at),
                    updated_at           = NOW(),
                    site                 = EXCLUDED.site,
                    service_component    = EXCLUDED.service_component,
                    category             = EXCLUDED.category,
                    environment          = EXCLUDED.environment,
                    severity             = EXCLUDED.severity,
                    blast_radius         = EXCLUDED.blast_radius,
                    impact_trend         = EXCLUDED.impact_trend,
                    quality_score        = EXCLUDED.quality_score,
                    error_signature_raw  = EXCLUDED.error_signature_raw,
                    error_signature_norm = EXCLUDED.error_signature_norm,
                    fields               = EXCLUDED.fields,
                    summary              = EXCLUDED.summary
                RETURNING doc_version
                """,
                {
                    "ticket_id": doc["ticket_id"],
                    "customer_id": doc["customer_id"],
                    "doc_type": doc.get("doc_type") or "intake",
                    "title": doc.get("title"),
                    "status": doc.get("status") or "new",
                    "opened_at": doc.get("opened_at"),
                    "site": facets.get("site"),
                    "service_component": facets.get("service_component"),
                    "category": facets.get("category"),
                    "environment": facets.get("environment"),
                    "severity": facets.get("severity"),
                    "blast_radius": facets.get("blast_radius"),
                    "impact_trend": facets.get("impact_trend"),
                    "quality_score": facets.get("quality_score"),
                    "error_signature_raw": doc.get("error_signature_raw"),
                    "error_signature_norm": doc.get("error_signature_norm"),
                    "fields": json.dumps(doc.get("fields") or {}),
                    "summary": json.dumps(doc.get("summary") or []),
                },
            )
            version = cur.fetchone()["doc_version"]

            cur.execute("DELETE FROM ticket_chunk WHERE ticket_id = %s", (doc["ticket_id"],))

            for chunk in chunks:
                cur.execute(
                    """
                    INSERT INTO ticket_chunk
                        (ticket_id, customer_id, doc_type, section, content,
                         embedding, embed_model)
                    VALUES (%s, %s, %s, %s, %s, %s::vector, %s)
                    """,
                    (
                        doc["ticket_id"],
                        doc["customer_id"],
                        doc.get("doc_type") or "intake",
                        chunk["section"],
                        chunk["content"],
                        vector_literal(chunk["embedding"]) if chunk.get("embedding") else None,
                        chunk.get("embed_model"),
                    ),
                )

    return {"ticket_id": doc["ticket_id"], "doc_version": version, "chunks": len(chunks)}


# -----------------------------------------------------------------------------
# retrieval
# -----------------------------------------------------------------------------
HYBRID_SQL = """
WITH filtered AS (
    SELECT c.chunk_id, c.ticket_id, c.customer_id, c.doc_type, c.section,
           c.content, c.embedding, c.tsv
    FROM ticket_chunk c
    JOIN ticket t USING (ticket_id)
    -- Every optional filter is cast explicitly. Without the ::text Postgres
    -- cannot infer a type for a bare `$1 IS NULL` and rejects the statement
    -- with AmbiguousParameter before it ever runs.
    -- '*' is shared knowledge (published KB articles), readable by every
    -- tenant. Everything else is strictly the asking tenant's own.
    WHERE (%(customer_id)s::text IS NULL
           OR c.customer_id = %(customer_id)s::text
           OR c.customer_id = '*')
      AND c.doc_type = ANY(%(doc_types)s::text[])
      AND (%(site)s::text              IS NULL OR t.site              = %(site)s::text)
      AND (%(service_component)s::text IS NULL OR t.service_component = %(service_component)s::text)
      AND (%(environment)s::text       IS NULL OR t.environment       = %(environment)s::text)
      AND (%(category)s::text          IS NULL OR t.category          = %(category)s::text)
),
semantic AS (
    SELECT chunk_id,
           ROW_NUMBER() OVER (ORDER BY embedding <=> %(query_vec)s::vector) AS rank
    FROM filtered
    WHERE embedding IS NOT NULL AND %(query_vec)s::text IS NOT NULL
    ORDER BY embedding <=> %(query_vec)s::vector
    LIMIT %(pool)s
),
lexical AS (
    SELECT chunk_id,
           ROW_NUMBER() OVER (
               ORDER BY ts_rank(tsv, websearch_to_tsquery('english', %(query_text)s)) DESC
           ) AS rank
    FROM filtered
    WHERE tsv @@ websearch_to_tsquery('english', %(query_text)s)
    LIMIT %(pool)s
)
SELECT f.chunk_id, f.ticket_id, f.doc_type, f.section, f.content,
       t.title, t.customer_id, t.site, t.service_component, t.environment,
       t.severity, t.status, t.opened_at, t.error_signature_raw,
       COALESCE(1.0 / (60 + s.rank), 0) AS semantic_score,
       COALESCE(1.0 / (60 + l.rank), 0) AS lexical_score,
       COALESCE(1.0 / (60 + s.rank), 0) + COALESCE(1.0 / (60 + l.rank), 0) AS score,
       s.rank AS semantic_rank,
       l.rank AS lexical_rank
FROM filtered f
JOIN ticket t ON t.ticket_id = f.ticket_id
LEFT JOIN semantic s ON s.chunk_id = f.chunk_id
LEFT JOIN lexical  l ON l.chunk_id = f.chunk_id
WHERE s.chunk_id IS NOT NULL OR l.chunk_id IS NOT NULL
ORDER BY score DESC
LIMIT %(top_k)s
"""


def hybrid_search(
    query_text: str,
    query_vec: list[float] | None,
    customer_id: str | None,
    doc_types: list[str],
    facets: dict[str, Any],
    top_k: int,
) -> list[dict[str, Any]]:
    """Reciprocal-rank fusion over pgvector + full-text.

    Vector-only retrieval loses on exact error strings, which is most of what
    support pastes in; lexical-only loses on "VM won't boot" vs "instance
    fails to spawn". RRF needs no score normalisation between the two, which
    is why it beats a weighted sum you would have to keep re-tuning.
    """
    params = {
        "query_text": query_text or "",
        "query_vec": vector_literal(query_vec) if query_vec else None,
        "customer_id": customer_id or None,
        "doc_types": list(doc_types or ["intake", "resolution", "kb"]),
        "site": facets.get("site") or None,
        "service_component": facets.get("service_component") or None,
        "environment": facets.get("environment") or None,
        "category": facets.get("category") or None,
        "pool": max(top_k * 6, 50),
        "top_k": top_k,
    }
    with cursor() as cur:
        cur.execute(HYBRID_SQL, params)
        return [dict(row) for row in cur.fetchall()]


def get_ticket(ticket_id: str) -> dict[str, Any] | None:
    with cursor() as cur:
        cur.execute("SELECT * FROM ticket WHERE ticket_id = %s", (ticket_id,))
        row = cur.fetchone()
    return dict(row) if row else None


def list_tickets(customer_id: str | None, limit: int) -> list[dict[str, Any]]:
    with cursor() as cur:
        cur.execute(
            """
            SELECT ticket_id, customer_id, doc_type, title, status, opened_at,
                   site, service_component, severity, updated_at
            FROM ticket
            WHERE (%s::text IS NULL OR customer_id = %s::text)
            ORDER BY updated_at DESC
            LIMIT %s
            """,
            (customer_id, customer_id, limit),
        )
        return [dict(row) for row in cur.fetchall()]


def counts() -> dict[str, int]:
    with cursor() as cur:
        cur.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM ticket)                                  AS tickets,
              (SELECT COUNT(*) FROM ticket_chunk)                            AS chunks,
              (SELECT COUNT(*) FROM ticket_chunk WHERE embedding IS NOT NULL) AS embedded
            """
        )
        return {k: int(v) for k, v in cur.fetchone().items()}


def log_chat(
    customer_id: str | None,
    question: str,
    answer: str,
    model: str,
    evidence: list[dict[str, Any]],
    latency_ms: int,
) -> None:
    try:
        with cursor() as cur:
            cur.execute(
                """
                INSERT INTO chat_log
                    (customer_id, question, answer, model, evidence, latency_ms)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (customer_id, question, answer, model, json.dumps(evidence), latency_ms),
            )
    except psycopg.Error:  # an audit row is not worth failing the answer over
        log.warning("could not write chat_log row", exc_info=True)
