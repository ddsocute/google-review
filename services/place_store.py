import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .cache_store import _get_connection


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
    conn = _get_connection(db_path)
    try:
        cur = conn.cursor()
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

    now = datetime.now(timezone.utc).isoformat()

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

