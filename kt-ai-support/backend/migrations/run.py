"""Migration runner.

Deliberately tiny — this is not Alembic, and it does not need to be yet.
Its one non-obvious job is substituting ${EMBEDDING_DIM} into the schema
from application config, so the embedding model can be changed without
editing SQL.

    python -m migrations.run              apply pending migrations
    python -m migrations.run --status     show what is applied
    python -m migrations.run --reset      DROP and recreate (destructive)

Changing the embedding model to one with a different dimension is a real
migration, not a config tweak: every stored vector becomes meaningless.
The runner refuses to run against a mismatched column and tells you what
to do instead, rather than writing vectors nobody can compare.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

import psycopg

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402

MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parent


def _files() -> list[pathlib.Path]:
    return sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9]_*.sql"))


def _applied(conn: psycopg.Connection) -> dict[str, int | None]:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version       TEXT PRIMARY KEY,
            embedding_dim INTEGER,
            applied_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    rows = conn.execute("SELECT version, embedding_dim FROM schema_migrations").fetchall()
    return {r[0]: r[1] for r in rows}


def _current_vector_dim(conn: psycopg.Connection) -> int | None:
    row = conn.execute(
        """
        SELECT a.atttypmod
        FROM pg_attribute a
        JOIN pg_class c ON c.oid = a.attrelid
        WHERE c.relname = 'rag_chunks' AND a.attname = 'embedding'
        """
    ).fetchone()
    if not row or row[0] in (None, -1):
        return None
    return int(row[0])


def status(settings) -> int:
    with psycopg.connect(settings.database_url, autocommit=True) as conn:
        applied = _applied(conn)
        dim = _current_vector_dim(conn)
        print(f"\n  database      {settings.database_url.split('@')[-1]}")
        print(f"  configured    EMBEDDING_DIM={settings.embedding_dim} "
              f"({settings.embedding_model})")
        print(f"  in database   rag_chunks.embedding = vector({dim})" if dim
              else "  in database   rag_chunks not created yet")
        print()
        for path in _files():
            mark = "applied" if path.stem in applied else "PENDING"
            print(f"  [{mark:>7}]  {path.name}")
        print()
        if dim is not None and dim != settings.embedding_dim:
            print(f"  MISMATCH: the column holds vector({dim}) but the configured "
                  f"model produces {settings.embedding_dim}.\n"
                  f"  Fix by setting EMBEDDING_DIM={dim}, or re-embed everything:\n"
                  f"    python -m migrations.run --reset && python -m scripts.rebuild_embeddings\n")
            return 1
    return 0


def migrate(settings, reset: bool = False) -> int:
    with psycopg.connect(settings.database_url, autocommit=True) as conn:
        if reset:
            print("  dropping every table (--reset)")
            conn.execute(
                """
                DO $$ DECLARE r RECORD; BEGIN
                  FOR r IN (SELECT tablename FROM pg_tables WHERE schemaname='public') LOOP
                    EXECUTE 'DROP TABLE IF EXISTS public.' || quote_ident(r.tablename) || ' CASCADE';
                  END LOOP;
                  DROP SEQUENCE IF EXISTS ticket_number_seq;
                END $$;
                """
            )

        applied = _applied(conn)

        dim = _current_vector_dim(conn)
        if dim is not None and dim != settings.embedding_dim:
            print(f"\n  REFUSING TO MIGRATE\n"
                  f"  rag_chunks.embedding is vector({dim}); "
                  f"{settings.embedding_model} produces {settings.embedding_dim}.\n"
                  f"  Vectors of different dimensions cannot be compared, so this would\n"
                  f"  silently corrupt retrieval. Either set EMBEDDING_DIM={dim}, or\n"
                  f"  re-embed from scratch:  python -m migrations.run --reset\n")
            return 1

        pending = [p for p in _files() if p.stem not in applied]
        if not pending:
            print("  nothing to do — schema is current")
            return 0

        for path in pending:
            sql = path.read_text(encoding="utf-8")
            sql = re.sub(r"\$\{EMBEDDING_DIM\}", str(settings.embedding_dim), sql)
            print(f"  applying {path.name}  (EMBEDDING_DIM={settings.embedding_dim})")
            conn.execute(sql)
            conn.execute(
                "INSERT INTO schema_migrations (version, embedding_dim) VALUES (%s, %s) "
                "ON CONFLICT (version) DO NOTHING",
                (path.stem, settings.embedding_dim),
            )
        print(f"  applied {len(pending)} migration(s)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="KT AI Support migrations")
    parser.add_argument("--status", action="store_true", help="show applied/pending")
    parser.add_argument("--reset", action="store_true", help="DROP everything, then migrate")
    args = parser.parse_args()

    settings = get_settings()
    if args.status:
        return status(settings)
    return migrate(settings, reset=args.reset)


if __name__ == "__main__":
    raise SystemExit(main())
