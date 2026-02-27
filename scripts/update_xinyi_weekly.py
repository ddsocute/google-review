import os
import sys
import argparse

from dotenv import load_dotenv


# Ensure project root is on sys.path when running as `python scripts/...`
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


def main() -> None:
    load_dotenv(override=True)

    parser = argparse.ArgumentParser(description="Weekly refresh for Xinyi catalog (re-analyze expired cache only).")
    parser.add_argument("--tag", default="xinyi", help="Catalog tag to update (default: xinyi)")
    parser.add_argument("--mode", default="quick", help="Cache mode (default: quick)")
    parser.add_argument("--max-places", type=int, default=None, help="Limit how many places to refresh")
    parser.add_argument("--force-refresh", action="store_true", help="Force refresh even if cache is still valid")
    parser.add_argument("--sleep-seconds", type=float, default=0.3, help="Sleep between analyses")
    parser.add_argument("--max-reviews", type=int, default=60, help="Max reviews to scrape per place")
    parser.add_argument("--workers", type=int, default=6, help="Parallel workers for Apify+LLM (default 6)")
    args = parser.parse_args()

    from services.cache_store import init_db
    from services.place_store import init_place_db
    from services.review_store import init_review_db
    from services.job_store import init_job_db
    from scripts.build_xinyi_db import analyze_catalog

    init_db()
    init_place_db()
    init_review_db()
    init_job_db()

    stats = analyze_catalog(
        tag=args.tag,
        mode=args.mode,
        max_places=args.max_places,
        force_refresh=bool(args.force_refresh),
        sleep_seconds=float(args.sleep_seconds),
        max_reviews=int(args.max_reviews),
        workers=int(args.workers),
    )
    print("[weekly] summary:", stats)


if __name__ == "__main__":
    main()

