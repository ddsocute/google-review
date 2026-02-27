import os
import sys
import argparse
from typing import Optional


# Ensure project root is on sys.path when running as `python scripts/...`
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Best-effort UTF-8 output (helps Windows terminals display Chinese correctly).
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass


def peek_catalog(tag: str, limit: int) -> None:
    """
    Quick, local-only peek into place_catalog for a given tag.

    - No network / Apify calls.
    - Reads the same SQLite DB used by the main app.
    """
    from services.cache_store import DEFAULT_DB_PATH, _get_connection  # type: ignore[attr-defined]

    limit = max(1, int(limit))

    conn = _get_connection(None)
    try:
        cur = conn.cursor()

        # Count total items for this tag
        cur.execute("SELECT COUNT(*) AS c FROM place_catalog WHERE tag = ?", (tag,))
        row = cur.fetchone()
        total = int(row["c"] if row and "c" in row.keys() else row[0] if row else 0)

        # Fetch a small preview (newest seen first)
        cur.execute(
            """
            SELECT
                id,
                name,
                address,
                google_rating,
                user_ratings_total,
                last_analyze_status,
                last_error,
                last_seen_at,
                last_analyzed_at
            FROM place_catalog
            WHERE tag = ?
            ORDER BY datetime(last_seen_at) DESC
            LIMIT ?
            """,
            (tag, limit),
        )
        rows = cur.fetchall()

    finally:
        conn.close()

    print(f"DB path: {DEFAULT_DB_PATH}")
    print(f"Tag: {tag}")
    print(f"Total places in place_catalog: {total}")
    print(f"Showing first {min(limit, len(rows))} rows (ordered by last_seen_at DESC):")
    print("-" * 80)

    if not rows:
        print("(no rows for this tag yet)")
        return

    for idx, r in enumerate(rows, start=1):
        name = (r["name"] or "").strip() if r["name"] is not None else ""
        address = (r["address"] or "").strip() if r["address"] is not None else ""
        rating = r["google_rating"]
        count = r["user_ratings_total"]
        status = r["last_analyze_status"]
        error = r["last_error"]
        last_seen = r["last_seen_at"]
        last_analyzed = r["last_analyzed_at"]

        # Keep lines short-ish for quick human scanning
        addr_short = (address[:80] + "…") if address and len(address) > 80 else address
        err_short = (error[:100] + "…") if error and len(error) > 100 else error

        print(f"[{idx}] id={r['id']}")
        print(f"    name       : {name or '(no name)'}")
        print(f"    address    : {addr_short or '(no address)'}")
        print(f"    rating     : {rating} ({count} reviews)" if rating is not None else f"    rating     : (none)")
        print(f"    status     : {status or '(none)'}")
        if err_short:
            print(f"    last_error : {err_short}")
        print(f"    last_seen  : {last_seen}")
        print(f"    analyzed_at: {last_analyzed or '(never)'}")
        print("-" * 80)


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        description="Peek into local place_catalog (信義區店家數量 + 前幾筆資料 preview，純本地查詢，秒出結果)."
    )
    parser.add_argument("--tag", default="xinyi", help="Catalog tag to inspect (default: xinyi)")
    parser.add_argument("--limit", type=int, default=10, help="How many rows to show in the preview (default: 10)")

    args = parser.parse_args(argv)
    peek_catalog(tag=args.tag, limit=args.limit)


if __name__ == "__main__":
    main()

