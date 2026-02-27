import os
from typing import Any, Dict, List, Optional

import requests


APIFY_TOKEN = os.getenv("APIFY_TOKEN", "")
MAX_SCRAPE_REVIEWS_DEFAULT = int(os.getenv("MAX_SCRAPE_REVIEWS", "90"))

# 可以透過環境變數覆寫 Apify Actor ID，預設使用官方 Compass Reviews Scraper 與
# 「Google Maps 店家名單採集工具」(futurizerush/google-maps-scraper-zh-tw)。
APIFY_REVIEWS_ACTOR_ID = os.getenv(
    "APIFY_REVIEWS_ACTOR_ID",
    "compass~Google-Maps-Reviews-Scraper",
)
APIFY_PLACES_ACTOR_ID = os.getenv(
    "APIFY_PLACES_ACTOR_ID",
    "futurizerush~google-maps-scraper-zh-tw",
)


def _get_apify_token() -> str:
    """
    取得目前的 Apify Token。

    注意：`app.py` 會在載入本模組 *之後* 才呼叫 `load_dotenv()`，
    因此這裡不能只依賴模組載入時的 `APIFY_TOKEN` 常數，
    必須在每次呼叫時再從環境變數補抓一次，避免永遠是空字串。
    """
    return APIFY_TOKEN or os.getenv("APIFY_TOKEN", "")


def _apify_run_actor(actor_id: str, payload: Dict[str, Any], timeout: int = 300):
    """共用的 Apify actor 呼叫 helper。

    會：
    - 自動帶入 APIFY_TOKEN
    - 處理 list / {items: [...]} 兩種回傳格式
    """
    token = _get_apify_token()
    if not token:
        # 統一在呼叫端處理「沒有 token」的情況，因此這裡直接 raise 讓上層捕捉。
        raise RuntimeError("APIFY_TOKEN not set")

    url = f"https://api.apify.com/v2/acts/{actor_id}/run-sync-get-dataset-items"
    resp = requests.post(
        url,
        params={"token": token},
        json=payload,
        timeout=timeout,
    )
    resp.raise_for_status()

    data = resp.json()
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "items" in data:
        return list(data.get("items") or [])
    return []


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
    if not _get_apify_token():
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

    return _apify_run_actor(APIFY_REVIEWS_ACTOR_ID, payload, timeout=300)


def search_places_by_text(
    query: str,
    limit: int = 6,
    language: str = "zh-TW",
    *,
    with_location: bool = False,
) -> List[Dict[str, Any]]:
    """
    使用 Apify 的 Google Maps 商家爬蟲依「店名 / 關鍵字」搜尋店家清單。

    回傳格式會被整理成：
    [
      {
        "place_id": str | None,
        "name": str,
        "address": str,
        "rating": float | None,
        "user_ratings_total": int | None,
        "maps_url": str | None,
      },
      ...
    ]
    """
    if not _get_apify_token():
        raise RuntimeError("APIFY_TOKEN not set")

    # Apify 官方文件使用的欄位名稱為 searchQueries，這裡僅搜尋單一關鍵字。
    payload = {
        "searchQueries": [query],
        "maxResults": max(limit, 1),
        "language": language,
        # 我們只需要店家清單，不需要額外撈 email/網站，以節省成本與時間
        "scrapeReviews": False,
        "scrapeEmails": False,
    }

    raw_items = _apify_run_actor(APIFY_PLACES_ACTOR_ID, payload, timeout=300)

    results: List[Dict[str, Any]] = []
    for item in raw_items[:limit]:
        if not isinstance(item, dict):
            continue

        place_id = (
            item.get("placeId")
            or item.get("googlePlaceId")
            or item.get("id")
        )
        name = item.get("name") or item.get("title") or ""
        address = (
            item.get("address")
            or item.get("formattedAddress")
            or item.get("fullAddress")
            or ""
        )
        rating = (
            item.get("rating")
            or item.get("totalScore")
            or item.get("stars")
        )
        user_ratings_total = (
            item.get("userRatingsTotal")
            or item.get("reviewsCount")
            or item.get("reviews")
        )
        maps_url = (
            item.get("url")
            or item.get("mapsUrl")
            or (f"https://www.google.com/maps/place/?q=place_id:{place_id}" if place_id else None)
        )

        # optional geo fields for map view
        lat: Optional[float] = None
        lng: Optional[float] = None
        # Apify actors may use different keys for coordinates; try several.
        for lat_key in ("locationLat", "lat", "latitude"):
            if isinstance(item.get(lat_key), (int, float)):
                lat = float(item[lat_key])
                break
        for lng_key in ("locationLng", "lng", "lon", "longitude"):
            if isinstance(item.get(lng_key), (int, float)):
                lng = float(item[lng_key])
                break

        results.append(
            {
                "place_id": place_id,
                "name": name,
                "address": address,
                "rating": rating,
                "user_ratings_total": user_ratings_total,
                "maps_url": maps_url,
            }
        )

    return results

__all__ = ["scrape_reviews", "search_places_by_text"]
