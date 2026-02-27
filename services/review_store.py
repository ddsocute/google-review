import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from .cache_store import _get_connection


def init_review_db(db_path: Optional[str] = None) -> None:
    """
    Store raw Google Maps reviews (from Apify) for incremental updates.

    Key idea:
    - Keep ALL historical reviews
    - On each refresh, only insert new ones (UNIQUE by canonical_url + review_id)
    """
    conn = _get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS place_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                canonical_url TEXT NOT NULL,
                review_id TEXT NOT NULL,
                published_at TEXT,
                stars REAL,
                text TEXT,
                reviewer_name TEXT,
                has_photo INTEGER NOT NULL DEFAULT 0,
                photo_urls_json TEXT,
                raw_json TEXT NOT NULL,
                scraped_at TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                UNIQUE (canonical_url, review_id)
            )
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_place_reviews_url ON place_reviews(canonical_url)")
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_place_reviews_published ON place_reviews(canonical_url, published_at)"
        )
        conn.commit()
    finally:
        conn.close()


def _to_iso(dt: Optional[str]) -> Optional[str]:
    # Apify returns ISO8601 strings already; store as-is (best-effort).
    if not dt:
        return None
    s = str(dt).strip()
    return s or None


def upsert_place_reviews(
    *,
    canonical_url: str,
    reviews: List[Dict[str, Any]],
    db_path: Optional[str] = None,
) -> Tuple[int, int]:
    """
    Insert new reviews and keep old ones.

    Returns:
      (inserted_new, total_processed)
    """
    if not canonical_url or not reviews:
        return (0, 0)

    # Correctly compute inserted_new by using INSERT OR IGNORE (rowcount reflects only new rows),
    # then UPDATE last_seen/scraped for both new+existing.
    return _insert_ignore_then_touch_seen(canonical_url=canonical_url, reviews=reviews, db_path=db_path)


def _insert_ignore_then_touch_seen(
    *, canonical_url: str, reviews: List[Dict[str, Any]], db_path: Optional[str] = None
) -> Tuple[int, int]:
    """
    Correctly compute inserted_new by using INSERT OR IGNORE (so rowcount reflects only new rows),
    then UPDATE last_seen/scraped for both new+existing.
    """
    if not canonical_url or not reviews:
        return (0, 0)

    now = datetime.now(timezone.utc).isoformat()
    inserted = 0
    processed = 0
    conn = _get_connection(db_path)
    try:
        cur = conn.cursor()
        for r in reviews:
            if not isinstance(r, dict):
                continue
            processed += 1
            review_id = (r.get("reviewId") or r.get("review_id") or r.get("id") or "").strip()
            if not review_id:
                fallback = (r.get("reviewUrl") or "") or (
                    f"{r.get('publishedAtDate') or r.get('publishAt')}-{(r.get('text') or '')[:80]}"
                )
                review_id = f"fallback:{abs(hash(str(fallback)))}"

            published_at = _to_iso(r.get("publishedAtDate") or r.get("publishAt") or r.get("reviewDate"))
            stars = r.get("stars") or r.get("rating") or r.get("reviewRating")
            try:
                stars_val = float(stars) if stars is not None else None
            except Exception:
                stars_val = None
            text = r.get("text") or r.get("reviewText") or ""
            reviewer_name = r.get("name") or r.get("reviewerName") or None
            photo_urls = r.get("reviewImageUrls") or r.get("photos") or []
            if not isinstance(photo_urls, list):
                photo_urls = []
            photo_urls = [p for p in photo_urls if isinstance(p, str) and p.startswith("http")][:8]
            has_photo = 1 if len(photo_urls) > 0 else 0
            try:
                raw_json = json.dumps(r, ensure_ascii=False)
            except Exception:
                raw_json = json.dumps(str(r), ensure_ascii=False)

            cur.execute(
                """
                INSERT OR IGNORE INTO place_reviews (
                    canonical_url,
                    review_id,
                    published_at,
                    stars,
                    text,
                    reviewer_name,
                    has_photo,
                    photo_urls_json,
                    raw_json,
                    scraped_at,
                    first_seen_at,
                    last_seen_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    canonical_url,
                    review_id,
                    published_at,
                    stars_val,
                    str(text) if text is not None else "",
                    reviewer_name,
                    has_photo,
                    json.dumps(photo_urls, ensure_ascii=False) if photo_urls else None,
                    raw_json,
                    now,
                    now,
                    now,
                ),
            )
            if (cur.rowcount or 0) > 0:
                inserted += 1

            # Touch seen markers for both new/existing
            cur.execute(
                """
                UPDATE place_reviews
                SET last_seen_at = ?, scraped_at = ?, raw_json = ?
                WHERE canonical_url = ? AND review_id = ?
                """,
                (now, now, raw_json, canonical_url, review_id),
            )

        conn.commit()
    finally:
        conn.close()
    return inserted, processed


def list_recent_reviews(
    *,
    canonical_url: str,
    limit: int = 60,
    db_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    if not canonical_url:
        return []
    limit = max(1, min(int(limit), 500))
    conn = _get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                review_id,
                published_at,
                stars,
                text,
                reviewer_name,
                has_photo,
                photo_urls_json,
                raw_json
            FROM place_reviews
            WHERE canonical_url = ?
            ORDER BY datetime(published_at) DESC, id DESC
            LIMIT ?
            """,
            (canonical_url, limit),
        )
        rows = cur.fetchall()
        out: List[Dict[str, Any]] = []
        for row in rows:
            try:
                raw = json.loads(row["raw_json"])
                if isinstance(raw, dict):
                    out.append(raw)
                    continue
            except Exception:
                pass
            out.append(
                {
                    "reviewId": row["review_id"],
                    "publishedAtDate": row["published_at"],
                    "stars": row["stars"],
                    "text": row["text"],
                    "name": row["reviewer_name"],
                }
            )
        return out
    finally:
        conn.close()


def get_reviews_summary(
    *,
    canonical_url: str,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Quick metadata for progress/debug: how many reviews stored + newest published_at.
    """
    if not canonical_url:
        return {"count": 0, "newest_published_at": None}
    conn = _get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT COUNT(*) AS c, MAX(published_at) AS newest
            FROM place_reviews
            WHERE canonical_url = ?
            """,
            (canonical_url,),
        )
        row = cur.fetchone()
        if not row:
            return {"count": 0, "newest_published_at": None}
        return {"count": int(row["c"] or 0), "newest_published_at": row["newest"]}
    finally:
        conn.close()


__all__ = ["init_review_db", "upsert_place_reviews", "list_recent_reviews", "get_reviews_summary"]

