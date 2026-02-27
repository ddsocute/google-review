import os
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .cache_store import _get_connection


def _get_postgres_url() -> str:
    return (
        os.getenv("POSTGRES_URL")
        or os.getenv("POSTGRES_URL_NON_POOLING")
        or os.getenv("DATABASE_URL")
        or ""
    ).strip()


def _use_postgres_places() -> bool:
    return bool(_get_postgres_url())


def _pg_connect():
    # Lazy import so local dev without psycopg still works if Postgres is not enabled.
    import psycopg
    from psycopg.rows import dict_row

    url = _get_postgres_url()
    if not url:
        raise RuntimeError("POSTGRES_URL is not set")
    return psycopg.connect(url, row_factory=dict_row)


def init_place_db(db_path: Optional[str] = None) -> None:
    """
    Ensure the local Places table exists in the same SQLite DB as analysis_cache.

    Schema:
      - id INTEGER PRIMARY KEY AUTOINCREMENT
      - canonical_url TEXT UNIQUE
      - display_name TEXT
      - address TEXT
      - google_rating REAL
      - user_ratings_total INTEGER
      - last_overall_score REAL
      - total_reviews_analyzed INTEGER
      - last_analyzed_at TEXT (ISO8601 UTC)
    """
    if _use_postgres_places():
        conn = _pg_connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS places (
                        id BIGSERIAL PRIMARY KEY,
                        canonical_url TEXT NOT NULL UNIQUE,
                        display_name TEXT NOT NULL,
                        address TEXT,
                        google_rating DOUBLE PRECISION,
                        user_ratings_total BIGINT,
                        last_overall_score DOUBLE PRECISION,
                        total_reviews_analyzed BIGINT,
                        last_analyzed_at TIMESTAMPTZ NOT NULL
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS place_catalog (
                        id BIGSERIAL PRIMARY KEY,
                        tag TEXT NOT NULL,
                        canonical_url TEXT NOT NULL,
                        maps_url TEXT,
                        place_id TEXT,
                        name TEXT,
                        address TEXT,
                        lat DOUBLE PRECISION,
                        lng DOUBLE PRECISION,
                        google_rating DOUBLE PRECISION,
                        user_ratings_total BIGINT,
                        source_query TEXT,
                        discovered_at TIMESTAMPTZ NOT NULL,
                        last_seen_at TIMESTAMPTZ NOT NULL,
                        last_analyzed_at TIMESTAMPTZ,
                        last_analyze_status TEXT,
                        last_error TEXT,
                        UNIQUE (tag, canonical_url)
                    )
                    """
                )
                cur.execute("CREATE INDEX IF NOT EXISTS idx_place_catalog_tag ON place_catalog(tag)")
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_place_catalog_last_seen ON place_catalog(last_seen_at)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_place_catalog_last_analyzed ON place_catalog(last_analyzed_at)"
                )
            conn.commit()
        finally:
            conn.close()
        return

    conn = _get_connection(db_path)
    try:
        cur = conn.cursor()
        # Core analysed places table
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS places (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                canonical_url TEXT NOT NULL UNIQUE,
                display_name TEXT NOT NULL,
                address TEXT,
                google_rating REAL,
                user_ratings_total INTEGER,
                last_overall_score REAL,
                total_reviews_analyzed INTEGER,
                last_analyzed_at TEXT NOT NULL
            )
            """
        )

        # Catalog table: discovered places (e.g. prebuilt district lists) before analysis.
        # IMPORTANT:
        #   - We allow the same canonical_url to appear under multiple tags (e.g. a chain
        #     restaurant that logically belongs to several districts / themes).
        #   - Therefore the natural uniqueness key is (tag, canonical_url) instead of just
        #     canonical_url.
        #   - This keeps each catalog "view" independent while still using a shared
        #     underlying analysis cache / places table keyed only by canonical_url.
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS place_catalog (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tag TEXT NOT NULL,
                canonical_url TEXT NOT NULL,
                maps_url TEXT,
                place_id TEXT,
                name TEXT,
                address TEXT,
                lat REAL,
                lng REAL,
                google_rating REAL,
                user_ratings_total INTEGER,
                source_query TEXT,
                discovered_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                last_analyzed_at TEXT,
                last_analyze_status TEXT,
                last_error TEXT,
                UNIQUE (tag, canonical_url)
            )
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_place_catalog_tag ON place_catalog(tag)")
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_place_catalog_last_seen ON place_catalog(last_seen_at)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_place_catalog_last_analyzed ON place_catalog(last_analyzed_at)"
        )
        conn.commit()
    finally:
        conn.close()


def record_place_from_analysis(
    canonical_url: str,
    display_name: str,
    analysis: Dict[str, Any],
    *,
    address: Optional[str] = None,
    google_rating: Optional[float] = None,
    user_ratings_total: Optional[int] = None,
    db_path: Optional[str] = None,
) -> None:
    """
    Upsert a place row whenever an analysis successfully completes.

    This builds a lightweight "map database" of analysed restaurants so that
    we know *what* has been analysed and can later present them like a map/list
    without calling Google Maps again.
    """
    if not canonical_url:
        return

    # Prefer explicit values passed from caller; otherwise, try to infer from analysis.
    if google_rating is None:
        gr = analysis.get("google_rating")
        try:
            google_rating = float(gr) if gr is not None else None
        except (TypeError, ValueError):
            google_rating = None

    if user_ratings_total is None:
        ur = analysis.get("google_reviews_count") or analysis.get("total_reviews_analyzed")
        try:
            user_ratings_total = int(ur) if ur is not None else None
        except (TypeError, ValueError):
            user_ratings_total = None

    overall_score_val = None
    os_val = analysis.get("overall_score")
    try:
        overall_score_val = float(os_val) if os_val is not None else None
    except (TypeError, ValueError):
        overall_score_val = None

    total_reviews = analysis.get("total_reviews_analyzed")
    try:
        total_reviews_int = int(total_reviews) if total_reviews is not None else None
    except (TypeError, ValueError):
        total_reviews_int = None

    now_dt = datetime.now(timezone.utc)

    if _use_postgres_places():
        conn = _pg_connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
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
                        canonical_url,
                        display_name,
                        address,
                        google_rating,
                        user_ratings_total,
                        overall_score_val,
                        total_reviews_int,
                        now_dt,
                    ),
                )
            conn.commit()
        finally:
            conn.close()
        return

    now = now_dt.isoformat()

    conn = _get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
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
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(canonical_url) DO UPDATE SET
                display_name = excluded.display_name,
                google_rating = excluded.google_rating,
                user_ratings_total = excluded.user_ratings_total,
                last_overall_score = excluded.last_overall_score,
                total_reviews_analyzed = excluded.total_reviews_analyzed,
                last_analyzed_at = excluded.last_analyzed_at
            """,
            (
                canonical_url,
                display_name,
                address,
                google_rating,
                user_ratings_total,
                overall_score_val,
                total_reviews_int,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def list_places(
    limit: int = 100,
    db_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    List recently analysed places from local DB, newest first.
    """
    if _use_postgres_places():
        conn = _pg_connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        id,
                        canonical_url,
                        display_name,
                        address,
                        google_rating,
                        user_ratings_total,
                        last_overall_score,
                        total_reviews_analyzed,
                        last_analyzed_at
                    FROM places
                    ORDER BY last_analyzed_at DESC
                    LIMIT %s
                    """,
                    (int(limit),),
                )
                rows = cur.fetchall() or []
                items: List[Dict[str, Any]] = []
                for row in rows:
                    d = dict(row)
                    la = d.get("last_analyzed_at")
                    if isinstance(la, datetime):
                        d["last_analyzed_at"] = la.astimezone(timezone.utc).isoformat()
                    items.append(d)
                return items
        finally:
            conn.close()

    conn = _get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                id,
                canonical_url,
                display_name,
                address,
                google_rating,
                user_ratings_total,
                last_overall_score,
                total_reviews_analyzed,
                last_analyzed_at
            FROM places
            ORDER BY datetime(last_analyzed_at) DESC
            LIMIT ?
            """,
            (int(limit),),
        )
        rows = cur.fetchall()
        items: List[Dict[str, Any]] = []
        for row in rows:
            # sqlite3.Row behaves like a mapping
            items.append(
                {
                    "id": row["id"],
                    "canonical_url": row["canonical_url"],
                    "display_name": row["display_name"],
                    "address": row["address"],
                    "google_rating": row["google_rating"],
                    "user_ratings_total": row["user_ratings_total"],
                    "last_overall_score": row["last_overall_score"],
                    "total_reviews_analyzed": row["total_reviews_analyzed"],
                    "last_analyzed_at": row["last_analyzed_at"],
                }
            )
        return items
    finally:
        conn.close()


__all__ = ["init_place_db", "record_place_from_analysis", "list_places"]


def upsert_catalog_place(
    *,
    tag: str,
    canonical_url: str,
    maps_url: Optional[str] = None,
    place_id: Optional[str] = None,
    name: Optional[str] = None,
    address: Optional[str] = None,
    lat: Optional[float] = None,
    lng: Optional[float] = None,
    google_rating: Optional[float] = None,
    user_ratings_total: Optional[int] = None,
    source_query: Optional[str] = None,
    last_analyzed_at: Optional[str] = None,
    last_analyze_status: Optional[str] = None,
    last_error: Optional[str] = None,
    db_path: Optional[str] = None,
) -> None:
    if not tag or not canonical_url:
        return

    now_dt = datetime.now(timezone.utc)

    if _use_postgres_places():
        conn = _pg_connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
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
                        last_seen_at = EXCLUDED.last_seen_at,
                        last_analyzed_at = COALESCE(EXCLUDED.last_analyzed_at, place_catalog.last_analyzed_at),
                        last_analyze_status = COALESCE(EXCLUDED.last_analyze_status, place_catalog.last_analyze_status),
                        last_error = COALESCE(EXCLUDED.last_error, place_catalog.last_error)
                    """,
                    (
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
                        now_dt,
                        now_dt,
                        last_analyzed_at,
                        last_analyze_status,
                        last_error,
                    ),
                )
            conn.commit()
        finally:
            conn.close()
        return

    now = now_dt.isoformat()
    conn = _get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
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
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(tag, canonical_url) DO UPDATE SET
                maps_url = COALESCE(excluded.maps_url, place_catalog.maps_url),
                place_id = COALESCE(excluded.place_id, place_catalog.place_id),
                name = COALESCE(excluded.name, place_catalog.name),
                address = COALESCE(excluded.address, place_catalog.address),
                lat = COALESCE(excluded.lat, place_catalog.lat),
                lng = COALESCE(excluded.lng, place_catalog.lng),
                google_rating = COALESCE(excluded.google_rating, place_catalog.google_rating),
                user_ratings_total = COALESCE(excluded.user_ratings_total, place_catalog.user_ratings_total),
                source_query = COALESCE(excluded.source_query, place_catalog.source_query),
                last_seen_at = excluded.last_seen_at,
                last_analyzed_at = COALESCE(excluded.last_analyzed_at, place_catalog.last_analyzed_at),
                last_analyze_status = COALESCE(excluded.last_analyze_status, place_catalog.last_analyze_status),
                last_error = COALESCE(excluded.last_error, place_catalog.last_error)
            """,
            (
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
                now,
                now,
                last_analyzed_at,
                last_analyze_status,
                last_error,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def list_catalog_places(
    *,
    tag: str,
    limit: int = 1000,
    db_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    if not tag:
        return []
    limit = max(1, min(int(limit), 50000))

    if _use_postgres_places():
        conn = _pg_connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        id,
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
                    WHERE tag = %s
                    ORDER BY last_seen_at DESC
                    LIMIT %s
                    """,
                    (tag, limit),
                )
                rows = cur.fetchall() or []
                items: List[Dict[str, Any]] = []
                for row in rows:
                    d = dict(row)
                    for k in ("discovered_at", "last_seen_at", "last_analyzed_at"):
                        v = d.get(k)
                        if isinstance(v, datetime):
                            d[k] = v.astimezone(timezone.utc).isoformat()
                    items.append(d)
                return items
        finally:
            conn.close()

    conn = _get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                id,
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
            WHERE tag = ?
            ORDER BY datetime(last_seen_at) DESC
            LIMIT ?
            """,
            (tag, limit),
        )
        rows = cur.fetchall()
        items: List[Dict[str, Any]] = []
        for row in rows:
            items.append({k: row[k] for k in row.keys()})
        return items
    finally:
        conn.close()


def list_catalog_with_analysis(
    *,
    tag: str,
    limit: int = 200,
    only_analyzed: bool = False,
    db_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Return catalog rows for a tag, joined with lightweight analysis metadata from `places`.

    This is designed for "prebuilt district lists" (e.g. xinyi) where we want to:
    - show the discovered list quickly
    - indicate whether an item already has analysis cached (places row exists)
    - optionally filter to only analyzed items
    """
    if not tag:
        return []
    limit = max(1, min(int(limit), 1000))

    if _use_postgres_places():
        conn = _pg_connect()
        try:
            with conn.cursor() as cur:
                where_extra = "AND p.canonical_url IS NOT NULL" if only_analyzed else ""
                cur.execute(
                    f"""
                    SELECT
                        c.id,
                        c.tag,
                        c.canonical_url,
                        c.maps_url,
                        c.place_id,
                        c.name,
                        c.address,
                        c.lat,
                        c.lng,
                        c.google_rating,
                        c.user_ratings_total,
                        c.source_query,
                        c.discovered_at,
                        c.last_seen_at,
                        c.last_analyzed_at AS catalog_last_analyzed_at,
                        c.last_analyze_status,
                        c.last_error,
                        p.display_name AS analyzed_display_name,
                        p.last_overall_score,
                        p.total_reviews_analyzed,
                        p.last_analyzed_at AS analyzed_last_analyzed_at
                    FROM place_catalog c
                    LEFT JOIN places p
                        ON p.canonical_url = c.canonical_url
                    WHERE c.tag = %s
                      {where_extra}
                    ORDER BY c.last_seen_at DESC
                    LIMIT %s
                    """,
                    (tag, limit),
                )
                rows = cur.fetchall() or []
                items: List[Dict[str, Any]] = []
                for row in rows:
                    d = dict(row)
                    for k in (
                        "discovered_at",
                        "last_seen_at",
                        "catalog_last_analyzed_at",
                        "analyzed_last_analyzed_at",
                    ):
                        v = d.get(k)
                        if isinstance(v, datetime):
                            d[k] = v.astimezone(timezone.utc).isoformat()
                    d["analysis_available"] = bool(d.get("analyzed_last_analyzed_at"))
                    items.append(d)
                return items
        finally:
            conn.close()

    conn = _get_connection(db_path)
    try:
        cur = conn.cursor()
        where_extra = "AND p.canonical_url IS NOT NULL" if only_analyzed else ""
        cur.execute(
            f"""
            SELECT
                c.id,
                c.tag,
                c.canonical_url,
                c.maps_url,
                c.place_id,
                c.name,
                c.address,
                c.lat,
                c.lng,
                c.google_rating,
                c.user_ratings_total,
                c.source_query,
                c.discovered_at,
                c.last_seen_at,
                c.last_analyzed_at AS catalog_last_analyzed_at,
                c.last_analyze_status,
                c.last_error,
                p.display_name AS analyzed_display_name,
                p.last_overall_score,
                p.total_reviews_analyzed,
                p.last_analyzed_at AS analyzed_last_analyzed_at
            FROM place_catalog c
            LEFT JOIN places p
                ON p.canonical_url = c.canonical_url
            WHERE c.tag = ?
              {where_extra}
            ORDER BY datetime(c.last_seen_at) DESC
            LIMIT ?
            """,
            (tag, limit),
        )
        rows = cur.fetchall()
        items: List[Dict[str, Any]] = []
        for row in rows:
            d = {k: row[k] for k in row.keys()}
            d["analysis_available"] = bool(d.get("analyzed_last_analyzed_at"))
            items.append(d)
        return items
    finally:
        conn.close()


def update_catalog_analyze_status(
    *,
    tag: str,
    canonical_url: str,
    status: str,
    error: Optional[str] = None,
    db_path: Optional[str] = None,
) -> None:
    if not tag or not canonical_url:
        return
    now_dt = datetime.now(timezone.utc)

    if _use_postgres_places():
        conn = _pg_connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE place_catalog
                    SET last_analyzed_at = %s,
                        last_analyze_status = %s,
                        last_error = %s
                    WHERE tag = %s AND canonical_url = %s
                    """,
                    (now_dt, status, error, tag, canonical_url),
                )
            conn.commit()
        finally:
            conn.close()
        return

    now = now_dt.isoformat()
    conn = _get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE place_catalog
            SET last_analyzed_at = ?,
                last_analyze_status = ?,
                last_error = ?
            WHERE tag = ? AND canonical_url = ?
            """,
            (now, status, error, tag, canonical_url),
        )
        conn.commit()
    finally:
        conn.close()


__all__ = [
    "init_place_db",
    "record_place_from_analysis",
    "list_places",
    "upsert_catalog_place",
    "list_catalog_places",
    "list_catalog_with_analysis",
    "update_catalog_analyze_status",
]

