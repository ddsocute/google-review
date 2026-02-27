import os
import threading
import time
from typing import Any, Dict, List, Optional, Sequence

import requests


MAX_SCRAPE_REVIEWS_DEFAULT = int(os.getenv("MAX_SCRAPE_REVIEWS", "90"))

# Global concurrency limiter for Apify actor runs.
# This prevents accidentally exceeding Apify account memory quotas when running many threads.
# Default: 2 (fits common 8GB total quota when each run requests 4GB).
_APIFY_MAX_CONCURRENT_RUNS = max(1, int(os.getenv("APIFY_MAX_CONCURRENT_RUNS", "2") or "2"))
_APIFY_RUN_SEM = threading.Semaphore(_APIFY_MAX_CONCURRENT_RUNS)

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


def _parse_place_item(item: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize a raw Apify place item into a stable shape.

    Note: different actors/versions may use different keys, so we try several.
    """
    place_id = item.get("placeId") or item.get("googlePlaceId") or item.get("id") or item.get("place_id")
    name = item.get("name") or item.get("title") or ""
    address = item.get("address") or item.get("formattedAddress") or item.get("fullAddress") or ""
    rating = item.get("rating") or item.get("totalScore") or item.get("stars")
    user_ratings_total = item.get("userRatingsTotal") or item.get("reviewsCount") or item.get("reviews")
    maps_url = (
        item.get("url")
        or item.get("mapsUrl")
        or (f"https://www.google.com/maps/place/?q=place_id:{place_id}" if place_id else None)
    )

    # Optional: lightweight photo previews for place list
    photo_urls: List[str] = []
    try:
        candidates = None
        for key in ("photoUrls", "imageUrls", "photos", "images", "gallery"):
            value = item.get(key)
            if isinstance(value, list) and value:
                candidates = value
                break
        if candidates:
            photo_urls = [str(u) for u in candidates if isinstance(u, str) and u.startswith("http")][:6]
    except Exception:
        photo_urls = []

    # optional geo fields for map view
    lat: Optional[float] = None
    lng: Optional[float] = None
    for lat_key in ("locationLat", "lat", "latitude"):
        if isinstance(item.get(lat_key), (int, float)):
            lat = float(item[lat_key])
            break
    for lng_key in ("locationLng", "lng", "lon", "longitude"):
        if isinstance(item.get(lng_key), (int, float)):
            lng = float(item[lng_key])
            break
    if (lat is None or lng is None) and isinstance(item.get("location"), dict):
        loc = item.get("location") or {}
        if lat is None and isinstance(loc.get("lat"), (int, float)):
            lat = float(loc["lat"])
        if lng is None and isinstance(loc.get("lng"), (int, float)):
            lng = float(loc["lng"])

    # some actors include which search string produced the row
    source_query = item.get("searchString") or item.get("searchQuery") or item.get("query")

    return {
        "place_id": place_id,
        "name": name,
        "address": address,
        "rating": rating,
        "user_ratings_total": user_ratings_total,
        "maps_url": maps_url,
        "lat": lat,
        "lng": lng,
        "photos": photo_urls,
        "source_query": source_query,
        "_raw": item,
    }


def _get_apify_token() -> str:
    """
    取得目前的 Apify Token。

    注意：
    - 不能依賴「模組 import 當下」讀到的環境變數，因為 `.env` 可能在稍後才被載入，
      或是作業系統環境變數在不同執行方式下會改變。
      因此必須在每次呼叫時都直接讀取 `os.getenv(...)`。
    - 部分主機可能使用 `APIFY_API_TOKEN` 這個名稱，所以也一併支援。
    """
    token = os.getenv("APIFY_TOKEN", "") or os.getenv("APIFY_API_TOKEN", "")
    # Guard against accidental whitespace (e.g. copy/paste with trailing newline),
    # which Apify treats as "token not provided".
    return (token or "").strip()


def _apify_run_actor(
    actor_id: str,
    payload: Dict[str, Any],
    timeout: int = 300,
    *,
    heartbeat_every: float = 5.0,
    heartbeat_prefix: Optional[str] = None,
):
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
    # run-sync-get-dataset-items 會「同步等待 actor 跑完」才回傳。
    # 為了避免使用者誤以為卡住，這裡可以加心跳訊息（預設每 5 秒）回報等待中。
    # 在平行模式下，建議 caller 傳入 heartbeat_prefix 以利辨識是哪個 batch。
    stop_evt = threading.Event()

    prefix = heartbeat_prefix or actor_id

    def _heartbeat() -> None:
        t0 = time.time()
        # 先稍微等一下，避免很快就回傳時噴太多訊息
        time.sleep(max(0.0, float(heartbeat_every)))
        while not stop_evt.is_set():
            elapsed = time.time() - t0
            print(
                f"[apify_client] waiting ({prefix}) ... elapsed={elapsed:.0f}s (timeout={timeout}s)",
                flush=True,
            )
            # 用小步睡眠讓 stop 更即時
            stop_evt.wait(max(0.1, float(heartbeat_every)))

    # Acquire a global concurrency slot first, so we don't start heartbeats for calls that
    # are just waiting in a local queue.
    acquired = False
    t_acquire0 = time.time()
    while not acquired:
        acquired = _APIFY_RUN_SEM.acquire(timeout=1.0)
        if not acquired:
            # Print a low-frequency hint (re-using heartbeat_every as cadence) so users see progress.
            # When heartbeat is disabled, still print every ~10s to avoid "silent hang".
            every = float(heartbeat_every) if heartbeat_every and float(heartbeat_every) > 0 else 10.0
            if (time.time() - t_acquire0) >= every:
                waited = time.time() - t_acquire0
                print(
                    f"[apify_client] waiting for concurrency slot ({prefix}) ... waited={waited:.0f}s "
                    f"(limit={_APIFY_MAX_CONCURRENT_RUNS})",
                    flush=True,
                )
                t_acquire0 = time.time()

    th = None
    if heartbeat_every and float(heartbeat_every) > 0:
        th = threading.Thread(target=_heartbeat, name="apify-heartbeat", daemon=True)
        th.start()
    try:
        resp = requests.post(
            url,
            params={"token": token},
            json=payload,
            timeout=timeout,
        )
    finally:
        stop_evt.set()
        try:
            _APIFY_RUN_SEM.release()
        except Exception:
            pass

    # 提供對 Apify 402/429 等常見錯誤更清楚的訊息，方便排查方案 / 額度問題。
    try:
        resp.raise_for_status()
    except requests.HTTPError as e:
        detail: str
        try:
            # 嘗試從 JSON 取出錯誤訊息，若失敗則退回純文字
            data = resp.json()
            if isinstance(data, dict):
                detail = str(data.get("message") or data.get("error") or data)
            else:
                detail = str(data)
        except Exception:
            detail = resp.text[:500]

        # Provide a more actionable hint for common Apify plan/quota errors.
        hint = ""
        if int(resp.status_code) == 402:
            hint = (
                " Hint: This is an Apify account quota/plan limit (NOT your local PC RAM/CPU). "
                "If you run multiple actor calls in parallel, reduce workers (e.g. 1-2), "
                "stop other running Actor jobs in Apify Console, or upgrade your Apify plan."
            )

        raise RuntimeError(
            f"Apify actor call failed (status={resp.status_code}, actor={actor_id}): {detail}{hint}"
        ) from e

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
    *,
    timeout: int = 300,
    heartbeat_every: float = 5.0,
    heartbeat_prefix: Optional[str] = None,
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

    return _apify_run_actor(
        APIFY_REVIEWS_ACTOR_ID,
        payload,
        timeout=int(timeout),
        heartbeat_every=float(heartbeat_every),
        heartbeat_prefix=heartbeat_prefix,
    )


def search_places_by_text(
    query: str,
    limit: int = 6,
    language: str = "zh-TW",
    *,
    with_location: bool = False,
    location_lat: Optional[float] = None,
    location_lng: Optional[float] = None,
    timeout: int = 300,
    heartbeat_every: float = 5.0,
    heartbeat_prefix: Optional[str] = None,
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

    raw_items = _apify_run_actor(
        APIFY_PLACES_ACTOR_ID,
        payload,
        timeout=int(timeout),
        heartbeat_every=float(heartbeat_every),
        heartbeat_prefix=heartbeat_prefix,
    )

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
        if isinstance(item, dict):
            results.append(_parse_place_item(item))

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


def search_places_bulk(
    queries: Sequence[str],
    *,
    limit_per_query: int = 200,
    language: str = "zh-TW",
    timeout: int = 300,
    heartbeat_every: float = 5.0,
    heartbeat_prefix: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Bulk place discovery by running one Apify actor call with multiple search strings.

    This is mainly for offline scripts to approximate "full coverage" in a district by:
    - sending many queries in one call
    - setting a high maxCrawledPlacesPerSearch
    - deduping afterwards
    """
    qs = [q.strip() for q in (queries or []) if isinstance(q, str) and q.strip()]
    if not qs:
        return []
    if not _get_apify_token():
        raise RuntimeError("APIFY_TOKEN not set")

    limit_per_query = max(1, min(int(limit_per_query), 2000))
    payload = {
        "searchStringsArray": list(qs),
        "maxCrawledPlacesPerSearch": limit_per_query,
        "language": language,
    }
    raw_items = _apify_run_actor(
        APIFY_PLACES_ACTOR_ID,
        payload,
        timeout=int(timeout),
        heartbeat_every=float(heartbeat_every),
        heartbeat_prefix=heartbeat_prefix,
    )

    results: List[Dict[str, Any]] = []
    for item in raw_items:
        if isinstance(item, dict):
            results.append(_parse_place_item(item))
    return results


__all__ = ["scrape_reviews", "search_places_by_text", "search_places_bulk"]
