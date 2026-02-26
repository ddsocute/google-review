import os
from typing import Any, Dict, List

import requests


APIFY_TOKEN = os.getenv("APIFY_TOKEN", "")
MAX_SCRAPE_REVIEWS_DEFAULT = int(os.getenv("MAX_SCRAPE_REVIEWS", "90"))


def scrape_reviews(
    google_maps_url: str,
    max_reviews: int = MAX_SCRAPE_REVIEWS_DEFAULT,
    language: str = "zh-TW",
) -> List[Dict[str, Any]]:
    """
    呼叫 Apify 的 Google Maps Reviews Scraper 抓取評論。

    若未設定 APIFY_TOKEN，為了讓整體服務仍可啟動，這裡會回傳空陣列，
    上層會把「沒有評論」當成正常情況處理。
    """
    if not APIFY_TOKEN:
        # 不直接 raise，避免整個 Flask app 無法啟動
        print("[apify_client] Warning: APIFY_TOKEN not set, returning empty reviews list.")
        return []

    payload = {
        "startUrls": [{"url": google_maps_url}],
        "maxReviews": max_reviews,
        "reviewsSort": "newest",
        "language": language,
        "personalData": False,
    }

    resp = requests.post(
        "https://api.apify.com/v2/acts/compass~Google-Maps-Reviews-Scraper/run-sync-get-dataset-items",
        params={"token": APIFY_TOKEN},
        json=payload,
        timeout=300,
    )
    resp.raise_for_status()

    data = resp.json()
    # Apify 這個 endpoint 理論上會直接回傳 list
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "items" in data:
        return list(data.get("items") or [])
    return []


__all__ = ["scrape_reviews"]
