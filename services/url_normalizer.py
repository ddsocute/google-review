import re
import hashlib
import urllib.parse
from typing import Optional, Dict, Any


GOOGLE_DOMAINS = (
    "google.com",
    "google.com.tw",
    "google.com.hk",
    "google.com.jp",
    "google.co",
)

# Query 參數白名單：其餘會被當作追蹤參數移除
ALLOWED_QUERY_KEYS = {
    "cid",
    "ftid",
    "q",
    "query",
    "query_place_id",
    "hl",
    "gl",
}


def extract_first_url(text: str) -> Optional[str]:
    """從任意文字中提取第一個 http/https 開頭的 URL。"""
    if not text:
        return None
    # 粗略擷取，避免吃到結尾標點符號
    match = re.search(r"(https?://[^\s<>\"'）)]+)", text)
    if not match:
        return None
    url = match.group(1).strip()
    # 去掉尾端常見標點
    url = url.rstrip(")。).,，；;")
    return url or None


def _is_google_maps_domain(netloc: str) -> bool:
    netloc = netloc.lower()
    return (
        "google.com" in netloc
        or "google.com.tw" in netloc
        or "google.com.hk" in netloc
        or "google.co" in netloc
        or netloc.startswith("maps.google.")
    )


def clean_tracking_params(url: str) -> str:
    """移除常見追蹤參數 (utm_*, g_st, fbclid, etc.)，保留必要 Maps 參數。"""
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return url

    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=False)
    cleaned = {}
    for key, values in query.items():
        k_lower = key.lower()
        # 移除追蹤 / 廣告相關參數
        if k_lower.startswith("utm_"):
            continue
        if k_lower in {"g_st", "fbclid", "gclid", "mc_id", "mc_eid"}:
            continue
        if key not in ALLOWED_QUERY_KEYS:
            continue
        cleaned[key] = values

    new_query = urllib.parse.urlencode(cleaned, doseq=True)
    parsed = parsed._replace(query=new_query)
    return urllib.parse.urlunparse(parsed)


def parse_maps_components(url: str) -> Dict[str, Optional[str]]:
    """從 Google Maps URL 中盡量抽出 place_id、cid 與餐廳名稱。"""
    result: Dict[str, Optional[str]] = {
        "place_id": None,
        "cid": None,
        "display_name": None,
    }

    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return result

    netloc = parsed.netloc.lower()
    path = parsed.path or ""
    query = urllib.parse.parse_qs(parsed.query)

    # 只處理 Google Maps 網域
    if not _is_google_maps_domain(netloc) and not netloc.startswith("maps.app.goo.gl"):
        return result

    # 1) 從 query 直接找 cid / place_id
    cid = query.get("cid", [None])[0]
    if cid:
        result["cid"] = cid

    # 例如 /maps/place/?q=place_id:ChIJ...
    q_param = query.get("q", [None])[0]
    if q_param and isinstance(q_param, str) and q_param.startswith("place_id:"):
        result["place_id"] = q_param.split("place_id:", 1)[1] or None

    # 或 query_place_id
    qp = query.get("query_place_id", [None])[0]
    if qp:
        result["place_id"] = qp

    if not result["place_id"]:
        # 嘗試從 path 解析 place_id（少見，但保守處理）
        # /maps/place/...?q=place_id:XXX 已在上面處理
        pass

    # 2) 從 path 抽 display_name
    segments = [s for s in path.split("/") if s]
    # 典型：/maps/place/<NAME>/...
    try:
        if len(segments) >= 3 and segments[0] == "maps" and segments[1] == "place":
            encoded_name = segments[2]
            display_name = urllib.parse.unquote(encoded_name)
            if display_name:
                result["display_name"] = display_name
    except Exception:
        pass

    return result


def canonicalize(url: str) -> Dict[str, Any]:
    """將任意 Google Maps URL 正規化成穩定的 canonical_url + 基本資訊。"""
    cleaned_url = clean_tracking_params(url)
    components = parse_maps_components(cleaned_url)

    place_id = components.get("place_id")
    cid = components.get("cid")
    display_name = components.get("display_name") or ""

    try:
        parsed = urllib.parse.urlparse(cleaned_url)
    except Exception:
        parsed = urllib.parse.urlparse(url)

    # 對於 Google Maps 網域，統一成 google.com 以提高快取命中率；
    # 對於 maps.app.goo.gl 等短網址，則保留原本網域，讓下游自行跟隨轉址。
    if _is_google_maps_domain(parsed.netloc):
        base_netloc = "www.google.com"
    else:
        base_netloc = parsed.netloc
    path = parsed.path or "/maps"

    # 建立 canonical_url
    if place_id:
        canonical_url = f"https://{base_netloc}/maps/place/?q=place_id:{place_id}"
    elif cid:
        canonical_url = f"https://maps.google.com/?cid={cid}&t=m"
    else:
        # 沒有 place_id / cid，就用清理後的 /maps... URL
        # 仍然移除多餘 query，只保留 ALLOWED_QUERY_KEYS
        safe_url = clean_tracking_params(cleaned_url)
        parsed2 = urllib.parse.urlparse(safe_url)
        # 若是 Google Maps 網域且不是 /maps 路徑，就強制補上 /maps；
        # 對 maps.app.goo.gl 等短網址則維持原始路徑，避免丟失短網址代碼。
        if _is_google_maps_domain(parsed2.netloc):
            if not parsed2.path.startswith("/maps"):
                parsed2 = parsed2._replace(path="/maps")
        canonical_url = urllib.parse.urlunparse(
            ("https", base_netloc, parsed2.path, "", parsed2.query, "")
        )

    # 建立快取 key：place_id > cid > url_hash
    if place_id:
        cache_key = f"place_id:{place_id}"
    elif cid:
        cache_key = f"cid:{cid}"
    else:
        h = hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()[:16]
        cache_key = f"url:{h}"

    return {
        "canonical_url": canonical_url,
        "cache_key": cache_key,
        "display_name": display_name,
        "place_id": place_id,
        "cid": cid,
    }


def normalize_input_to_canonical(raw_text: str) -> Dict[str, Any]:
    """接受「任意文字或 URL 或店名」，輸出統一的正規化結構。

    回傳格式：
    {
      "canonical_url": "...",
      "cache_key": "...",
      "display_name": "...",
      "input_type": "url|search",
      "resolved_from": "short_url|long_url|search_text",
      "place_id": "... 或 None",
      "cid": "... 或 None"
    }
    """
    text = (raw_text or "").strip()

    # 1) 先嘗試從文字中抽出 URL
    url = extract_first_url(text)
    if url:
        # 判斷是否短網址（實際 HTTP 解析會在別的 service 裡進行）
        parsed = urllib.parse.urlparse(url)
        netloc = parsed.netloc.lower()
        is_short = netloc.startswith("maps.app.goo.gl") or netloc.startswith("goo.gl")
        norm = canonicalize(url)
        norm.update(
            {
                "input_type": "url",
                "resolved_from": "short_url" if is_short else "long_url",
            }
        )
        if not norm.get("display_name"):
            # 若 URL 中沒有餐廳名稱，就用原始文字作為 display_name 提示
            norm["display_name"] = text
        return norm

    # 2) 沒有 URL，視為使用者輸入關鍵字 / 店名，先組成 search URL
    search_query = text
    encoded = urllib.parse.quote(search_query)
    search_url = f"https://www.google.com/maps/search/{encoded}"
    h = hashlib.sha256(search_url.encode("utf-8")).hexdigest()[:16]
    cache_key = f"search_url:{h}"

    return {
        "canonical_url": search_url,
        "cache_key": cache_key,
        "display_name": search_query,
        "input_type": "search",
        "resolved_from": "search_text",
        "place_id": None,
        "cid": None,
    }


__all__ = [
    "extract_first_url",
    "clean_tracking_params",
    "parse_maps_components",
    "canonicalize",
    "normalize_input_to_canonical",
]

