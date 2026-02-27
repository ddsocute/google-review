import argparse
import os
import sqlite3
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from dotenv import load_dotenv


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


def _get_postgres_url() -> str:
    return (
        os.getenv("POSTGRES_URL")
        or os.getenv("POSTGRES_URL_NON_POOLING")
        or os.getenv("DATABASE_URL")
        or ""
    ).strip()


def _pg_connect():
    import psycopg

    url = _get_postgres_url()
    if not url:
        raise RuntimeError("POSTGRES_URL is not set (put it in your local .env first)")
    return psycopg.connect(url)


def _sqlite_connect(sqlite_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(sqlite_path)
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists_sqlite(conn: sqlite3.Connection, table: str) -> bool:
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,))
    return cur.fetchone() is not None


def _parse_iso_dt(s: Any) -> Optional[datetime]:
    if s is None:
        return None
    if isinstance(s, datetime):
        dt = s
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    txt = str(s).strip()
    if not txt:
        return None
    try:
        dt = datetime.fromisoformat(txt)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _migrate_analysis_cache(sqlite_conn: sqlite3.Connection, pg_conn, *, dry_run: bool) -> int:
    if not _table_exists_sqlite(sqlite_conn, "analysis_cache"):
        print("[skip] sqlite has no analysis_cache")
        return 0
    cur = sqlite_conn.cursor()
    cur.execute(
        "SELECT cache_key, mode, canonical_url, display_name, result_json, created_at FROM analysis_cache"
    )
    rows = cur.fetchall() or []
    if not rows:
        print("[ok] analysis_cache empty")
        return 0

    inserted = 0
    with pg_conn.cursor() as pg:
        for r in rows:
            created_at = _parse_iso_dt(r["created_at"]) or datetime.now(timezone.utc)
            if dry_run:
                inserted += 1
                continue
            pg.execute(
                """
                INSERT INTO analysis_cache (
                    cache_key, mode, canonical_url, display_name, result_json, created_at
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (cache_key, mode) DO UPDATE SET
                    canonical_url = EXCLUDED.canonical_url,
                    display_name = EXCLUDED.display_name,
                    result_json = EXCLUDED.result_json,
                    created_at = EXCLUDED.created_at
                """,
                (
                    r["cache_key"],
                    r["mode"],
                    r["canonical_url"],
                    r["display_name"],
                    r["result_json"],
                    created_at,
                ),
            )
            inserted += 1
    if not dry_run:
        pg_conn.commit()
    print(f"[ok] migrated analysis_cache rows: {inserted}")
    return inserted


def _migrate_places(sqlite_conn: sqlite3.Connection, pg_conn, *, dry_run: bool) -> int:
    if not _table_exists_sqlite(sqlite_conn, "places"):
        print("[skip] sqlite has no places")
        return 0
    cur = sqlite_conn.cursor()
    cur.execute(
        """
        SELECT
            canonical_url,
            display_name,
            address,
            google_rating,
            user_ratings_total,
            last_overall_score,
            total_reviews_analyzed,
            last_analyzed_at
        FROM places
        """
    )
    rows = cur.fetchall() or []
    if not rows:
        print("[ok] places empty")
        return 0

    inserted = 0
    with pg_conn.cursor() as pg:
        for r in rows:
            last_analyzed_at = _parse_iso_dt(r["last_analyzed_at"]) or datetime.now(timezone.utc)
            if dry_run:
                inserted += 1
                continue
            pg.execute(
                """
                INSERT INTO places (
                    canonical_url,
                    display_name,
                    address,
                    google_rating,
                    user_ratings_total,
                    last_overall_score,
                    total_reviews_analyzed,
                    last_analyzed_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (canonical_url) DO UPDATE SET
                    display_name = EXCLUDED.display_name,
                    address = COALESCE(EXCLUDED.address, places.address),
                    google_rating = EXCLUDED.google_rating,
                    user_ratings_total = EXCLUDED.user_ratings_total,
                    last_overall_score = EXCLUDED.last_overall_score,
                    total_reviews_analyzed = EXCLUDED.total_reviews_analyzed,
                    last_analyzed_at = EXCLUDED.last_analyzed_at
                """,
                (
                    r["canonical_url"],
                    r["display_name"],
                    r["address"],
                    r["google_rating"],
                    r["user_ratings_total"],
                    r["last_overall_score"],
                    r["total_reviews_analyzed"],
                    last_analyzed_at,
                ),
            )
            inserted += 1
    if not dry_run:
        pg_conn.commit()
    print(f"[ok] migrated places rows: {inserted}")
    return inserted


def _migrate_place_catalog(sqlite_conn: sqlite3.Connection, pg_conn, *, dry_run: bool) -> int:
    if not _table_exists_sqlite(sqlite_conn, "place_catalog"):
        print("[skip] sqlite has no place_catalog")
        return 0
    cur = sqlite_conn.cursor()
    cur.execute(
        """
        SELECT
            tag,
            canonical_url,
            maps_url,
            place_id,
            name,
            address,
            lat,
            lng,
            google_rating,
            user_ratings_total,
            source_query,
            discovered_at,
            last_seen_at,
            last_analyzed_at,
            last_analyze_status,
            last_error
        FROM place_catalog
        """
    )
    rows = cur.fetchall() or []
    if not rows:
        print("[ok] place_catalog empty")
        return 0

    inserted = 0
    with pg_conn.cursor() as pg:
        for r in rows:
            discovered_at = _parse_iso_dt(r["discovered_at"]) or datetime.now(timezone.utc)
            last_seen_at = _parse_iso_dt(r["last_seen_at"]) or discovered_at
            last_analyzed_at = _parse_iso_dt(r["last_analyzed_at"])
            if dry_run:
                inserted += 1
                continue
            pg.execute(
                """
                INSERT INTO place_catalog (
                    tag,
                    canonical_url,
                    maps_url,
                    place_id,
                    name,
                    address,
                    lat,
                    lng,
                    google_rating,
                    user_ratings_total,
                    source_query,
                    discovered_at,
                    last_seen_at,
                    last_analyzed_at,
                    last_analyze_status,
                    last_error
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (tag, canonical_url) DO UPDATE SET
                    maps_url = COALESCE(EXCLUDED.maps_url, place_catalog.maps_url),
                    place_id = COALESCE(EXCLUDED.place_id, place_catalog.place_id),
                    name = COALESCE(EXCLUDED.name, place_catalog.name),
                    address = COALESCE(EXCLUDED.address, place_catalog.address),
                    lat = COALESCE(EXCLUDED.lat, place_catalog.lat),
                    lng = COALESCE(EXCLUDED.lng, place_catalog.lng),
                    google_rating = COALESCE(EXCLUDED.google_rating, place_catalog.google_rating),
                    user_ratings_total = COALESCE(EXCLUDED.user_ratings_total, place_catalog.user_ratings_total),
                    source_query = COALESCE(EXCLUDED.source_query, place_catalog.source_query),
                    discovered_at = LEAST(place_catalog.discovered_at, EXCLUDED.discovered_at),
                    last_seen_at = GREATEST(place_catalog.last_seen_at, EXCLUDED.last_seen_at),
                    last_analyzed_at = COALESCE(EXCLUDED.last_analyzed_at, place_catalog.last_analyzed_at),
                    last_analyze_status = COALESCE(EXCLUDED.last_analyze_status, place_catalog.last_analyze_status),
                    last_error = COALESCE(EXCLUDED.last_error, place_catalog.last_error)
                """,
                (
                    r["tag"],
                    r["canonical_url"],
                    r["maps_url"],
                    r["place_id"],
                    r["name"],
                    r["address"],
                    r["lat"],
                    r["lng"],
                    r["google_rating"],
                    r["user_ratings_total"],
                    r["source_query"],
                    discovered_at,
                    last_seen_at,
                    last_analyzed_at,
                    r["last_analyze_status"],
                    r["last_error"],
                ),
            )
            inserted += 1
    if not dry_run:
        pg_conn.commit()
    print(f"[ok] migrated place_catalog rows: {inserted}")
    return inserted


def main() -> None:
    load_dotenv(override=True)

    parser = argparse.ArgumentParser(description="Migrate local SQLite (data/analysis_cache.db) to Postgres.")
    parser.add_argument(
        "--sqlite",
        default=os.path.join(PROJECT_ROOT, "data", "analysis_cache.db"),
        help="Path to SQLite DB (default: data/analysis_cache.db)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Count rows without writing to Postgres")
    args = parser.parse_args()

    sqlite_path = os.path.abspath(args.sqlite)
    if not os.path.exists(sqlite_path):
        raise SystemExit(f"SQLite file not found: {sqlite_path}")

    if not _get_postgres_url():
        raise SystemExit("POSTGRES_URL is not set. Put it into your local .env then re-run.")

    # Ensure Postgres tables exist using app services (they will pick Postgres when POSTGRES_URL is set).
    from services.cache_store import init_db as init_cache_db
    from services.place_store import init_place_db as init_places_db

    init_cache_db()
    init_places_db()

    sqlite_conn = _sqlite_connect(sqlite_path)
    pg_conn = _pg_connect()
    try:
        print(f"[info] sqlite: {sqlite_path}")
        print("[info] migrating -> Postgres (POSTGRES_URL)")
        total = 0
        total += _migrate_analysis_cache(sqlite_conn, pg_conn, dry_run=bool(args.dry_run))
        total += _migrate_places(sqlite_conn, pg_conn, dry_run=bool(args.dry_run))
        total += _migrate_place_catalog(sqlite_conn, pg_conn, dry_run=bool(args.dry_run))
        print(f"[done] total rows processed: {total}")
    finally:
        try:
            sqlite_conn.close()
        except Exception:
            pass
        try:
            pg_conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()

