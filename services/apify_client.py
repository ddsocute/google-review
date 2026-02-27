import os
from typing import Any, Dict, List, Optional

import requests


APIFY_TOKEN = (os.getenv("APIFY_TOKEN") or os.getenv("APIFY_API_TOKEN") or "").strip()
MAX_SCRAPE_REVIEWS_DEFAULT = int(os.getenv("MAX_SCRAPE_REVIEWS", "90"))

# 可以透過環境變數覆寫 Apify Actor ID，預設使用官方 Compass Reviews Scraper 與
# 「Google Maps 店家名單採集工具」(futurizerush/google-maps-scraper-zh-tw)。
APIFY_REVIEWS_ACTOR_ID = os.getenv(
    "APIFY_REVIEWS_ACTOR_ID",
    "compass~Google-Maps-Reviews-Scraper",
)
APIFY_PLACES_ACTOR_ID = os.getenv(
    "APIFY_PLACES_ACTOR_ID",
    "compass~crawler-google-places",
)


def _get_apify_token() -> str:
    """
    取得目前的 Apify Token。

    注意：
    - `app.py` 會在載入本模組 *之後* 才呼叫 `load_dotenv()`，
      因此這裡不能只依賴模組載入時的 `APIFY_TOKEN` 常數，
      必須在每次呼叫時再從環境變數補抓一次，避免永遠是空字串。
    - 部分主機可能使用 `APIFY_API_TOKEN` 這個名稱，所以也一併支援。
    """
    token = (
        APIFY_TOKEN
        or os.getenv("APIFY_TOKEN", "")
        or os.getenv("APIFY_API_TOKEN", "")
    )
    # Guard against accidental whitespace (e.g. copy/paste with trailing newline),
    # which Apify treats as "token not provided".
    return (token or "").strip()


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
    location_lat: Optional[float] = None,
    location_lng: Optional[float] = None,
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

    # 使用 Compass `crawler-google-places` 的輸入格式：
    # - searchStringsArray: 搜尋字串陣列
    # - maxCrawledPlacesPerSearch: 每個搜尋字串最多回傳幾筆店家
    # - language: 結果語系
    payload = {
        "searchStringsArray": [query],
        "maxCrawledPlacesPerSearch": max(limit, 1),
        "language": language,
    }

    raw_items = _apify_run_actor(APIFY_PLACES_ACTOR_ID, payload, timeout=300)

    def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        # Minimal dependency: compute distance for sorting nearby branches when user shares location.
        from math import asin, cos, radians, sin, sqrt

        r = 6371.0
        dlat = radians(lat2 - lat1)
        dlon = radians(lon2 - lon1)
        a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
        return 2 * r * asin(sqrt(a))

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

        # Optional: lightweight photo previews for place list（用在清單內小縮圖，不做額外 API 呼叫）
        photo_urls: List[str] = []
        try:
            # 嘗試從幾種常見欄位抓圖片：不同 Apify actor / 版本欄位名稱可能不一樣
            candidates = None
            for key in ("photoUrls", "imageUrls", "photos", "images", "gallery"):
                value = item.get(key)
                if isinstance(value, list) and value:
                    candidates = value
                    break
            if candidates:
                photo_urls = [
                    str(u)
                    for u in candidates
                    if isinstance(u, str) and u.startswith("http")
                ][:6]
        except Exception:
            # 圖片失敗不影響主要功能
            photo_urls = []

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
        # Common schema: nested {"location": {"lat": ..., "lng": ...}}
        if (lat is None or lng is None) and isinstance(item.get("location"), dict):
            loc = item.get("location") or {}
            if lat is None and isinstance(loc.get("lat"), (int, float)):
                lat = float(loc["lat"])
            if lng is None and isinstance(loc.get("lng"), (int, float)):
                lng = float(loc["lng"])

        results.append(
            {
                "place_id": place_id,
                "name": name,
                "address": address,
                "rating": rating,
                "user_ratings_total": user_ratings_total,
                "maps_url": maps_url,
                "lat": lat,
                "lng": lng,
                # 前端清單內店家預覽圖（最多數張、主要用作「環境一瞥」）
                "photos": photo_urls,
            }
        )

    # If caller provided user location, sort by distance (best-effort).
    # We DO NOT rely on actor-specific geo-input schema here; we only use location to sort results
    # when the actor happened to return coordinates.
    if with_location and location_lat is not None and location_lng is not None:
        try:
            clat = float(location_lat)
            clng = float(location_lng)
            results.sort(
                key=lambda r: _haversine_km(clat, clng, r["lat"], r["lng"])
                if isinstance(r.get("lat"), (int, float)) and isinstance(r.get("lng"), (int, float))
                else 10**9
            )
        except Exception:
            pass

    return results

__all__ = ["scrape_reviews", "search_places_by_text"]
