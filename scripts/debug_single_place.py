import os
import sys
import argparse
from typing import Optional

from dotenv import load_dotenv


# Ensure project root is on sys.path when running as `python scripts/...`
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


def _load_env() -> None:
    """
    Load project .env just like the main app so APIFY_TOKEN / OPENAI_API_KEY 等設定都一致。
    """
    env_path = os.path.join(PROJECT_ROOT, ".env")
    if os.path.exists(env_path):
        load_dotenv(env_path, override=True)


def normalize_input_to_canonical(raw_input: str) -> str:
    """
    接受：
    - Google Maps 各種 URL（包含 maps.app.goo.gl 短網址）
    - 任意文字關鍵字（會交給 canonicalize 幫你查）

    回傳 canonical_url（跟 catalog / analyze_catalog 用的是同一個欄位）。
    """
    from services.url_normalizer import canonicalize

    raw_input = (raw_input or "").strip()
    if not raw_input:
        raise ValueError("input 不能是空字串")

    norm = canonicalize(raw_input)
    canonical_url = norm.get("canonical_url") or ""
    if not canonical_url:
        raise RuntimeError(f"canonicalize 失敗，拿不到 canonical_url，input={raw_input!r}")
    return canonical_url


def upsert_single_catalog_place(
    *,
    tag: str,
    canonical_url: str,
    display_name: Optional[str] = None,
) -> None:
    """
    在 catalog 裡確保有一筆這家店（用 upsert_catalog_place），
    其他欄位先留空，之後 Apify / 分析會補。
    """
    from services.place_store import upsert_catalog_place

    upsert_catalog_place(
        tag=tag,
        canonical_url=canonical_url,
        maps_url=canonical_url,
        place_id=None,
        name=display_name,
        address=None,
        lat=None,
        lng=None,
        google_rating=None,
        user_ratings_total=None,
        source_query="debug_single_place",
    )


def run_analyze_catalog_for_single(
    *,
    tag: str,
    max_reviews: int,
    workers: int,
    sleep_seconds: float,
    mode: str,
) -> None:
    """
    直接重用 scripts.build_xinyi_db 裡的 analyze_catalog，
    讓整條 pipeline（Apify 抓評論 + LLM + record_place_from_analysis）跟正式批次一模一樣。
    """
    from scripts.build_xinyi_db import analyze_catalog

    stats = analyze_catalog(
        tag=tag,
        mode=mode,
        max_places=1,  # 這個 tag 底下目前就你這一間
        force_refresh=True,
        sleep_seconds=sleep_seconds,
        max_reviews=max_reviews,
        workers=workers,
        progress_every=5.0,
    )
    print(f"[debug_single_place] analyze_catalog stats: {stats}")


def main() -> None:
    _load_env()

    parser = argparse.ArgumentParser(
        description="針對單一 Google Maps 店家跑完整 analyze_catalog pipeline（跟信義大批量同一套程式）",
    )
    parser.add_argument(
        "--input",
        required=True,
        help="可以是 Google Maps URL（含 maps.app.goo.gl 短網址）或關鍵字",
    )
    parser.add_argument(
        "--tag",
        default="xinyi_debug",
        help="寫入 catalog 用的 tag，預設 xinyi_debug；你也可以改成 xinyi 跟正式資料混一起",
    )
    parser.add_argument(
        "--max-reviews",
        type=int,
        default=60,
        help="最多抓多少則評論丟給 LLM（預設 60）",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="analyze_catalog 用幾個 worker（IO bound，1~2 就很夠）",
    )
    parser.add_argument(
        "--sleep-seconds",
        type=float,
        default=0.3,
        help="每家店分析完之後 sleep 幾秒，避免打太兇（預設 0.3）",
    )
    parser.add_argument(
        "--mode",
        default="quick",
        help="沿用 analyze_catalog 的 mode 參數（目前 app 端只有 quick，在這裡也用 quick 就好）",
    )

    args = parser.parse_args()

    raw_input = args.input
    tag = args.tag

    print(f"[debug_single_place] input={raw_input!r}, tag={tag!r}")

    canonical_url = normalize_input_to_canonical(raw_input)
    print(f"[debug_single_place] canonical_url={canonical_url}")

    upsert_single_catalog_place(tag=tag, canonical_url=canonical_url, display_name=None)
    print("[debug_single_place] 已寫入/更新 catalog，準備跑 analyze_catalog")

    run_analyze_catalog_for_single(
        tag=tag,
        max_reviews=int(args.max_reviews),
        workers=int(args.workers),
        sleep_seconds=float(args.sleep_seconds),
        mode=str(args.mode),
    )

    print("[debug_single_place] DONE")


if __name__ == "__main__":
    main()

