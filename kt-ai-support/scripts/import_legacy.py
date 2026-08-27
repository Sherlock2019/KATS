"""Import legacy CORE tickets into the canonical KT database.

    python -m scripts.import_legacy --probe            check the connection
    python -m scripts.import_legacy --rows-only        all tickets, no LLM
    python -m scripts.import_legacy --limit 200        + extraction, small batch
    python -m scripts.import_legacy --incremental      only what changed
    python -m scripts.import_legacy --force            re-process everything

Safe to interrupt and safe to re-run: every stage keys off a content hash,
and `legacy_raw.status` records how far each ticket got.
"""

from __future__ import annotations

import argparse
import pathlib
import sys
import time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "backend"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.db import SessionLocal                                  # noqa: E402
from app.services.legacy.connector import LegacyConnector        # noqa: E402
from app.services.legacy.pipeline import LegacyPipeline          # noqa: E402

UTC = timezone.utc


def probe(connector: LegacyConnector) -> int:
    result = connector.probe()
    print(f"\n  legacy database  {result['url']}")
    if not result.get("reachable"):
        print(f"  UNREACHABLE — {result.get('error')}\n")
        return 1
    print("  reachable\n")
    print(f"  {'view':<24}{'status':<10}rows")
    ok = True
    for kind, info in result["views"].items():
        if info["ok"]:
            print(f"  {info['view']:<24}{'ok':<10}{info['rows']}")
        else:
            ok = False
            print(f"  {info['view']:<24}{'MISSING':<10}{info.get('error','')[:60]}")
    print()
    if not ok:
        print("  Missing views are the contract this pipeline reads. Ask the CORE\n"
              "  DBA to create them, or override the names with LEGACY_VIEWS.\n")
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Import legacy CORE tickets")
    parser.add_argument("--probe", action="store_true", help="test the connection only")
    parser.add_argument("--rows-only", action="store_true",
                        help="import every ticket as a row; no LLM extraction")
    parser.add_argument("--limit", type=int, help="stop after N tickets")
    parser.add_argument("--since", help="only tickets updated after YYYY-MM-DD")
    parser.add_argument("--incremental", action="store_true",
                        help="use the stored watermark as --since")
    parser.add_argument("--force", action="store_true",
                        help="re-process even when the hash is unchanged")
    args = parser.parse_args()

    connector = LegacyConnector()
    if not connector.configured:
        print("\n  LEGACY_DATABASE_URL is not set.\n"
              "  To try the whole pipeline without production access:\n\n"
              "    python -m scripts.make_fake_core --url sqlite:///fake_core.db\n"
              "    LEGACY_DATABASE_URL=sqlite:///fake_core.db \\\n"
              "      python -m scripts.import_legacy --rows-only\n")
        return 1

    if args.probe:
        return probe(connector)

    db = SessionLocal()
    try:
        pipeline = LegacyPipeline(db, connector)

        since = None
        if args.since:
            since = datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=UTC)
        elif args.incremental:
            since = pipeline.last_sync("ticket")
            if since:
                # A minute of overlap, because a legacy `updated_at` written
                # mid-transaction can land just before the watermark.
                since = since - timedelta(minutes=1)
                print(f"  incremental since {since:%Y-%m-%d %H:%M}")
            else:
                print("  no watermark yet — full pass")

        mode = "rows only (no LLM)" if args.rows_only else "rows + KT extraction"
        print(f"\n  Importing legacy tickets — {mode}")
        if args.limit:
            print(f"  limit {args.limit}")
        print()

        started = time.time()
        last_line = [0.0]

        def progress(stats):
            # Rewrite one line rather than scrolling thousands.
            if time.time() - last_line[0] < 0.5:
                return
            last_line[0] = time.time()
            sys.stdout.write(
                f"\r  {stats.fetched} seen · {stats.mapped} rows · "
                f"{stats.extracted} extracted · {stats.unchanged} unchanged · "
                f"{stats.failed} failed   ")
            sys.stdout.flush()

        stats = pipeline.run(
            since=since, limit=args.limit,
            do_extract=not args.rows_only, force=args.force,
            progress=progress,
        )
        elapsed = time.time() - started
        print("\n")

        result = stats.as_dict()
        width = max(len(k) for k in result if k != "skip_reasons")
        for key, value in result.items():
            if key == "skip_reasons":
                continue
            print(f"  {key.replace('_', ' '):<{width + 2}}{value}")
        print(f"  {'elapsed':<{width + 2}}{elapsed:.0f}s")

        if result["skip_reasons"]:
            print("\n  not extracted (still imported as rows):")
            for reason, count in sorted(result["skip_reasons"].items(),
                                        key=lambda kv: -kv[1]):
                print(f"    {count:>5}  {reason}")

        if stats.extracted:
            print(f"\n  Next: check retrieval did not degrade —")
            print(f"    python -m scripts.evaluate_retrieval\n")
        else:
            print()
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
