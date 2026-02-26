import os
import json
import re
import hashlib
import requests
import urllib.parse
from flask import Flask, request, jsonify, render_template, send_from_directory
from flask_cors import CORS
from dotenv import load_dotenv
from services.cache_store import init_db, get_cached_analysis, set_cached_analysis
from services.url_normalizer import canonicalize
from services.apify_client import scrape_reviews as apify_scrape_reviews
from routes.api_tasks import bp as api_tasks_bp

load_dotenv()

app = Flask(__name__, static_folder="static", template_folder="templates")
CORS(app)
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

# Initialize local cache DB and task-based API routes
init_db()
app.register_blueprint(api_tasks_bp)

APIFY_TOKEN = os.getenv("APIFY_TOKEN", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.viviai.cc/v1")
GOOGLE_PLACES_API_KEY = os.getenv("GOOGLE_PLACES_API_KEY", "")

MAX_SCRAPE_REVIEWS = int(os.getenv("MAX_SCRAPE_REVIEWS", "90"))
MAX_REVIEWS_FOR_AI = int(os.getenv("MAX_REVIEWS_FOR_AI", "60"))
MAX_REVIEW_TEXT_CHARS = int(os.getenv("MAX_REVIEW_TEXT_CHARS", "220"))
MAX_REVIEWS_BLOCK_CHARS = int(os.getenv("MAX_REVIEWS_BLOCK_CHARS", "24000"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def normalize_maps_url(url):
    """Convert google.com/maps?q=...&ftid=... to /maps/place/ format for Apify."""
    if '/maps/place/' in url:
        return url
    parsed = urllib.parse.urlparse(url)
    params = urllib.parse.parse_qs(parsed.query)
    q = params.get('q', [''])[0]
    ftid = params.get('ftid', [''])[0]
    if q and ftid:
        place_url = f"https://www.google.com/maps/place/{urllib.parse.quote(q)}/?ftid={ftid}"
        return place_url
    if q:
        return f"https://www.google.com/maps/place/{urllib.parse.quote(q)}/"
    return url


def resolve_short_url(url):
    """Resolve goo.gl / maps.app.goo.gl short links to full Google Maps URL."""
    # Strip tracking query params that interfere with resolution
    clean_url = re.sub(r'[?&](g_st|utm_\w+)=[^&]*', '', url).rstrip('?&')
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    for attempt_url in [clean_url, url]:
        try:
            resp = requests.get(attempt_url, headers=headers, allow_redirects=True, timeout=15, stream=True)
            resp.close()
            final = resp.url
            # Normalize the resolved URL to /maps/place/ format
            return normalize_maps_url(final)
        except Exception:
            continue
    # Fallback: try HEAD
    try:
        resp = requests.head(url, allow_redirects=True, timeout=15)
        return normalize_maps_url(resp.url)
    except Exception:
        return url


def validate_google_maps_url(url):
    """Return True if url looks like a valid Google Maps place link."""
    patterns = [
        r"https?://(www\.)?google\.(com|com\.\w{2})/maps/place/",
        r"https?://(www\.)?google\.(com|com\.\w{2})/maps/search/",
        r"https?://(www\.)?google\.(com|com\.\w{2})/maps\?.*ftid=",
        r"https?://(www\.)?google\.(com|com\.\w{2})/maps/?",
        r"https?://(maps\.)?google\.(com|com\.\w{2})/maps",
        r"https?://maps\.app\.goo\.gl/",
        r"https?://goo\.gl/maps/",
    ]
    return any(re.search(p, url) for p in patterns)


def compact_text(text, max_chars=220):
    if not isinstance(text, str):
        return ""
    clean = re.sub(r"\s+", " ", text).strip()
    if len(clean) <= max_chars:
        return clean
    return clean[:max_chars].rstrip() + "…"


def parse_json_from_model_content(content):
    if isinstance(content, dict):
        return content
    if not isinstance(content, str):
        raise json.JSONDecodeError("Model content is not JSON string", "", 0)

    text = content.strip()

    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
        text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(text[start:end + 1])
        raise


# ---------------------------------------------------------------------------
# OpenAI: analyse reviews
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """你是一位頂尖的美食評論數據分析師，擅長從大量顧客評論中挖掘有價值的資訊。
請針對以下 Google Maps 餐廳評論進行全面深度分析。

*重要*：每則評論都有編號（如 [Review #1]），且標註了是否有照片（has_photo: true/false）。
當你推薦或不推薦菜品時，必須在 review_indices 欄位中填入「提到該菜品的評論編號」。
這樣我們才能正確將評論中的照片對應到菜品。

## 分析項目

### A. 餐廳完整介紹 (restaurant_intro)
撰寫一段 200-350 字的完整餐廳介紹，涵蓋：
- 料理風格與菜系定位（如：台式居酒屋、義法創意料理、道地港式茶餐廳等）
- 地點環境與氛圍描述
- 最具特色的招牌料理
- 適合什麼類型的聚餐（約會、朋友聚會、家庭聚餐、商務宴請）
- 建議到訪時段與注意事項（如：需訂位、尖峰時段要等、低消限制等）
- 整體餐廳體驗的核心賣點

### B. 推薦菜色（最多 8 道）
- 僅列出「評論中被具體提及菜名且獲得正面評價」的菜品
- reason 必須 120-200 字，包含：口感描述、特色之處、多位顧客的共同評價
- keywords 至少 3 個具體形容詞
- review_indices：列出提到此菜品的評論編號（整數陣列）

### C. 不推薦菜色（最多 5 道）
- 同上格式，reason 說明為何不推薦，包含具體問題描述
- review_indices：列出提到此菜品的評論編號

### D. 四大維度分析
每個維度都要：
- score：1-10 分（可含小數點如 7.5）
- summary：120-200 字的具體描述，引用實際評論內容佐證
- positive_keywords：3-6 個正面關鍵詞
- negative_keywords：0-4 個負面關鍵詞（沒有就空陣列）

1. 口味 (taste)
2. 服務 (service)
3. 環境 (environment)  
4. CP值 (value_for_money) — 額外提供 price_range

### E. 灌水評論偵測 (非常重要！)
深入分析評論中的異常模式：
- 辨識「打卡送XX」「五星好評送甜點」等誘導性促銷
- 偵測短時間內湧入的雷同、空洞五星評論
- 識別一次性評論帳號（只評過這間店）
- **activity_period 必填**：分析灌水行為的時間軸
  - start_date：灌水/促銷開始的大約時間
  - end_date：結束的大約時間（若仍在進行中則寫 "至今"）
  - is_ongoing：布林值，目前是否仍在進行
  - description：50-150 字說明這段期間發生了什麼，活動是否已結束

### F. 適合場景推薦 (scene_recommendations)
根據評論內容和餐廳特性，判斷此餐廳最適合哪些用餐場景。
必須包含以下 6 種場景，每個都給出 suitable（布林值）和 description（20-40字說明）：
- 約會、家庭聚餐、朋友聚會、商務宴客、一個人用餐、觀光打卡

### G. 最佳造訪時段建議 (best_visit_time)
從評論中萃取時間相關資訊，分析不同時段的人潮狀況：
- summary：50-100字整體建議
- recommendations：4個時段（平日中午、平日晚餐、假日午餐、假日晚餐），每個包含：
  - time：時段名稱
  - crowding：人潮程度（低/中/高）
  - wait_time：預估等候時間
  - description：20-40字建議

### H. 評分趨勢分析 (rating_trend)
根據評論的日期和評分，分析此餐廳的評價變化趨勢：
- trend：improving / declining / stable
- trend_label：中文標籤（持續進步 / 逐漸下滑 / 穩定維持）
- summary：50-100字趨勢描述
- periods：將評論分成 4 個時間區間（近1個月、1-3個月前、3-6個月前、6個月以上），每個包含：
  - period：區間名稱
  - avg_score：該區間平均星等（1-5，保留一位小數）
  - review_count：該區間評論數

## 輸出格式（必須是合法 JSON，不可包含任何 JSON 以外文字）

{
  "restaurant_name": "餐廳名稱",
  "restaurant_intro": "200-350字完整餐廳介紹...",
  "overall_score": 7.5,
  "total_reviews_analyzed": 120,
  "recommended_dishes": [
    {
      "name": "菜名",
      "mentions": 15,
      "reason": "120-200字詳細推薦原因",
      "keywords": ["形容詞1", "形容詞2", "形容詞3"],
      "review_indices": [1, 5, 12, 23]
    }
  ],
  "not_recommended_dishes": [
    {
      "name": "菜名",
      "mentions": 3,
      "reason": "120-200字詳細不推薦原因",
      "keywords": ["問題1", "問題2"],
      "review_indices": [7, 19]
    }
  ],
  "taste": {
    "score": 8.0,
    "summary": "120-200字口味分析...",
    "positive_keywords": ["鮮甜", "道地", "火候恰到好處"],
    "negative_keywords": ["偏鹹"]
  },
  "service": {
    "score": 7.0,
    "summary": "120-200字服務分析...",
    "positive_keywords": ["親切", "主動介紹菜色"],
    "negative_keywords": ["等待時間長"]
  },
  "environment": {
    "score": 6.5,
    "summary": "120-200字環境分析...",
    "positive_keywords": ["裝潢有質感", "氣氛好"],
    "negative_keywords": ["座位偏擠"]
  },
  "value_for_money": {
    "score": 7.0,
    "summary": "120-200字CP值分析...",
    "positive_keywords": ["份量足", "商業午餐划算"],
    "negative_keywords": ["單點偏貴"],
    "price_range": "每人約 $300-500"
  },
  "fake_review_detection": {
    "suspected_count": 12,
    "total_reviews": 120,
    "percentage": 10.0,
    "reasons": ["打卡送紅茶", "Google評論五星送小菜"],
    "warning_level": "中度注意",
    "details": "詳細分析說明...",
    "activity_period": {
      "start_date": "2024年3月",
      "end_date": "2024年6月",
      "is_ongoing": false,
      "description": "該餐廳在2024年3-6月期間透過打卡送飲品活動大量收集五星評論，此活動已結束，近期評論品質已恢復正常。"
    }
  },
  "scene_recommendations": [
    {"scene": "約會", "suitable": true, "description": "環境浪漫燈光柔和，適合情侶用餐"},
    {"scene": "家庭聚餐", "suitable": true, "description": "菜色多元，適合各年齡層"},
    {"scene": "朋友聚會", "suitable": true, "description": "氣氛熱鬧，適合多人聚餐"},
    {"scene": "商務宴客", "suitable": false, "description": "環境較吵雜，不適合商務洽談"},
    {"scene": "一個人用餐", "suitable": true, "description": "有吧台座位，一人用餐不尷尬"},
    {"scene": "觀光打卡", "suitable": true, "description": "裝潢有特色，適合拍照打卡"}
  ],
  "best_visit_time": {
    "summary": "建議平日中午前往最佳，假日需提前訂位或現場候位30分鐘以上",
    "recommendations": [
      {"time": "平日中午", "crowding": "低", "wait_time": "無需等候", "description": "人潮最少，適合悠閒用餐"},
      {"time": "平日晚餐", "crowding": "中", "wait_time": "約10-15分鐘", "description": "建議提早到場"},
      {"time": "假日午餐", "crowding": "高", "wait_time": "約30-60分鐘", "description": "建議11點前到場"},
      {"time": "假日晚餐", "crowding": "高", "wait_time": "約30-60分鐘", "description": "尖峰時段建議訂位"}
    ]
  },
  "rating_trend": {
    "trend": "stable",
    "trend_label": "穩定維持",
    "summary": "近半年評價穩定維持在4星以上，品質一致獲得好評",
    "periods": [
      {"period": "近1個月", "avg_score": 4.2, "review_count": 15},
      {"period": "1-3個月前", "avg_score": 4.0, "review_count": 25},
      {"period": "3-6個月前", "avg_score": 3.8, "review_count": 20},
      {"period": "6個月以上", "avg_score": 3.5, "review_count": 30}
    ]
  }
}"""


def analyse_reviews(reviews_data, model="gemini-3-flash-preview"):
    """Send scraped reviews to OpenAI for analysis."""
    # Build a condensed text block from raw review items
    # Number each review and note which have photos
    lines = []
    review_photo_map = {}  # idx -> list of photo URLs
    selected_reviews = reviews_data[:MAX_REVIEWS_FOR_AI]
    for idx, item in enumerate(selected_reviews):
        text = compact_text(item.get("text") or item.get("reviewText") or "", MAX_REVIEW_TEXT_CHARS)
        stars = item.get("stars") or item.get("reviewRating") or ""
        name = item.get("name") or item.get("reviewerName") or f"User{idx}"
        date = item.get("publishedAtDate") or item.get("reviewDate") or ""
        review_photos = item.get("reviewImageUrls") or item.get("photos") or []
        owner_reply = item.get("ownerResponse") or item.get("responseFromOwnerText") or ""
        reviewer_reviews = item.get("reviewerNumberOfReviews") or item.get("totalScore") or ""

        # Store photos for this review index
        valid_photos = [p for p in review_photos[:5] if isinstance(p, str) and p.startswith("http")]
        if valid_photos:
            review_photo_map[idx + 1] = valid_photos  # 1-indexed

        has_photo = "true" if valid_photos else "false"
        line = f"[Review #{idx + 1}] [{stars} stars] (has_photo: {has_photo}) {name}"
        if reviewer_reviews:
            line += f" (reviewer total reviews: {reviewer_reviews})"
        if date:
            line += f" ({date})"
        line += f": {text}"
        if owner_reply:
            line += f" | owner reply: {owner_reply}"
        lines.append(line)

    reviews_block = "\n".join(lines)
    if len(reviews_block) > MAX_REVIEWS_BLOCK_CHARS:
        reviews_block = reviews_block[:MAX_REVIEWS_BLOCK_CHARS].rsplit("\n", 1)[0]

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {OPENAI_API_KEY}",
    }
    # Validate model choice
    allowed_models = {"gemini-3-flash-preview", "gemini-3-pro-preview"}
    if model not in allowed_models:
        model = "gemini-3-flash-preview"

    payload = {
        "model": model,
        "temperature": 0.15,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"以下是 {len(selected_reviews)} 則 Google Maps 評論，請分析。只輸出合法 JSON：\n\n{reviews_block}"},
        ],
        "response_format": {"type": "json_object"},
    }

    last_error = None
    analysis = None
    for attempt in range(2):
        try:
            api_timeout = 300 if model == "gemini-3-pro-preview" else 180
            resp = requests.post(
                f"{OPENAI_BASE_URL}/chat/completions",
                headers=headers,
                json=payload,
                timeout=api_timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            analysis = parse_json_from_model_content(content)
            break
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            last_error = e
            payload["messages"][-1]["content"] += "\n\n請再重試一次：只能輸出可被 json.loads 解析的單一 JSON 物件，不要 markdown、不要註解。"
        except requests.exceptions.Timeout:
            raise  # let caller handle timeout directly
        except Exception as e:
            last_error = e
            break

    if analysis is None:
        if isinstance(last_error, json.JSONDecodeError):
            raise last_error
        raise Exception(f"Model response parse failed: {last_error}")

    # Normalize field names - AI may use old names
    if "restaurant_intro" not in analysis and "dining_tips" in analysis:
        analysis["restaurant_intro"] = analysis["dining_tips"]
    if "dining_tips" not in analysis and "restaurant_intro" in analysis:
        analysis["dining_tips"] = analysis["restaurant_intro"]

    # Ensure activity_period exists in fake_review_detection
    fd = analysis.get("fake_review_detection", {})
    if fd and "activity_period" not in fd:
        fd["activity_period"] = {
            "start_date": "無法判斷",
            "end_date": "無法判斷",
            "is_ongoing": False,
            "description": fd.get("details", "無足夠資訊判斷灌水活動的確切時間範圍。")
        }

    # Attach the photo map so enrich_photos can use it
    analysis["_review_photo_map"] = review_photo_map
    analysis["total_reviews_analyzed"] = len(selected_reviews)
    return analysis


# ---------------------------------------------------------------------------
# Vision API: identify food in photos
# ---------------------------------------------------------------------------

def identify_photo_with_vision(photo_url):
    """Use Vision model to identify what food is in a review photo.
    
    Returns the dish name string, or None if not identifiable.
    """
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {OPENAI_API_KEY}",
    }
    payload = {
        "model": "gemini-3-flash-preview",
        "temperature": 0.1,
        "max_tokens": 60,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "請辨識這張照片中的食物名稱。只回傳菜名,不要其他文字。如果有多道菜,用頓號分隔。如果無法辨識或不是食物照片,回傳「無法辨識」。"
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": photo_url}
                    }
                ]
            }
        ],
    }

    try:
        resp = requests.post(
            f"{OPENAI_BASE_URL}/chat/completions",
            headers=headers,
            json=payload,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        result = data["choices"][0]["message"]["content"].strip()
        if "無法辨識" in result or len(result) > 50:
            return None
        return result
    except Exception as e:
        print(f"[Vision] Error identifying photo: {e}")
        return None


def fuzzy_match_dish(identified_name, dish_names):
    """Check if the identified food name matches any dish name.
    
    Returns the matched dish name or None.
    Uses substring matching for Chinese dish names.
    """
    if not identified_name:
        return None
    
    identified_lower = identified_name.lower().strip()
    # The Vision API may return multiple names separated by 、
    identified_parts = [p.strip() for p in identified_lower.replace(",", "、").split("、") if p.strip()]
    
    for dish_name in dish_names:
        dish_lower = dish_name.lower().strip()
        for part in identified_parts:
            # Check both directions: dish name in identified, or identified in dish name
            if part in dish_lower or dish_lower in part:
                return dish_name
            # Check partial match (at least 2 Chinese chars overlap)
            common = set(part) & set(dish_lower)
            # Remove common punctuation/spaces from the overlap count
            common = {c for c in common if c.strip() and ord(c) > 127}
            if len(common) >= 2 and len(common) >= len(dish_lower) * 0.5:
                return dish_name
    return None


def enrich_photos(analysis, reviews_data, restaurant_name=""):
    """Collect up to 10 food photos from positive (4-5 star) reviews.
    
    Returns a flat list of photo URLs in analysis["food_photos"].
    No longer matches photos to individual dishes.
    """
    review_photo_map = analysis.pop("_review_photo_map", {})

    # Clean up review_indices from dishes
    for dish in analysis.get("recommended_dishes", []):
        dish.pop("review_indices", None)
        dish.pop("photo_url", None)
    for dish in analysis.get("not_recommended_dishes", []):
        dish.pop("review_indices", None)
        dish.pop("photo_url", None)

    # Collect photo URLs from positive reviews (4-5 stars) first, then others
    positive_photos = []
    other_photos = []
    seen_urls = set()

    def collect_photo_urls(item):
        candidates = []

        possible_fields = [
            item.get("reviewImageUrls"),
            item.get("photos"),
            item.get("reviewImages"),
            item.get("reviewPhotos"),
            item.get("images"),
        ]

        for field in possible_fields:
            if isinstance(field, list):
                for photo in field:
                    if isinstance(photo, str):
                        candidates.append(photo)
                    elif isinstance(photo, dict):
                        maybe_url = photo.get("url") or photo.get("photoUrl") or photo.get("imageUrl")
                        if isinstance(maybe_url, str):
                            candidates.append(maybe_url)

        valid_urls = []
        for url in candidates:
            if isinstance(url, str) and url.startswith("http") and url not in seen_urls:
                seen_urls.add(url)
                valid_urls.append(url)
        return valid_urls
    for idx, item in enumerate(reviews_data):
        stars = item.get("stars") or item.get("reviewRating") or 0
        try:
            stars = float(stars)
        except (ValueError, TypeError):
            stars = 0

        valid = collect_photo_urls(item)[:5]

        # Fallback from prebuilt photo map if this review has no parsed URLs
        if not valid:
            mapped = review_photo_map.get(idx + 1, [])
            valid = [u for u in mapped if isinstance(u, str) and u.startswith("http") and u not in seen_urls]
            for u in valid:
                seen_urls.add(u)

        if stars >= 4:
            positive_photos.extend(valid)
        else:
            other_photos.extend(valid)

    # Prefer positive review photos, fill up to 10
    food_photos = positive_photos[:10]
    if len(food_photos) < 10:
        food_photos.extend(other_photos[:10 - len(food_photos)])

    analysis["food_photos"] = food_photos[:10]
    print(f"[Photos] Collected {len(analysis['food_photos'])} food photos")
    return analysis


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.after_request
def add_no_cache_headers(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@app.route("/sw.js")
def service_worker():
    # PWA is currently removed.
    # Keep /sw.js as a CLEANUP service worker to remove any previously registered SW/caches
    # that might still control "/" and serve stale assets.
    #
    # IMPORTANT: allow "/" so any old root-scoped registration can update and run cleanup.
    resp = send_from_directory("static", "sw.js", mimetype="application/javascript")
    resp.headers["Service-Worker-Allowed"] = "/"
    return resp


@app.route("/api/analyze", methods=["POST"])
def api_analyze():
    """Main analysis endpoint."""
    body = request.get_json(force=True)
    url = (body.get("url") or "").strip()
    model = body.get("model", "gemini-3-flash-preview")
    mode = "deep" if model == "gemini-3-pro-preview" else "quick"

    if not url:
        return jsonify({"error": "請提供 Google Maps 餐廳連結"}), 400

    # Validate URL format
    if not validate_google_maps_url(url):
        return jsonify({"error": "網址格式不正確，請貼上 Google Maps 餐廳連結（支援短網址）"}), 400

    # Resolve short link
    if "goo.gl" in url:
        try:
            url = resolve_short_url(url)
        except Exception:
            return jsonify({"error": "短網址解析失敗，請改用完整的 Google Maps 連結"}), 400
        if not validate_google_maps_url(url):
            return jsonify({"error": "短網址解析後非有效的 Google Maps 連結，請確認連結是否正確"}), 400

    # Normalize URL to canonical form + cache key
    try:
        norm = canonicalize(url)
    except Exception:
        norm = {}

    canonical_url = norm.get("canonical_url") or url
    cache_key = norm.get("cache_key")
    if not cache_key:
        h = hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()[:16]
        cache_key = f"url:{h}"
    display_name = norm.get("display_name") or url

    # --- Step 0: Check cache first ---
    try:
        entry = get_cached_analysis(cache_key, mode)
    except Exception:
        entry = None

    if entry is not None:
        result = entry.as_result_object()
        return jsonify(result)

    # --- Step 1: Scrape reviews ---
    try:
        # quick 模式少抓一點評論以提高速度，deep 模式抓比較多評論
        if mode == "deep":
            max_reviews = MAX_SCRAPE_REVIEWS
        else:
            max_reviews = min(MAX_SCRAPE_REVIEWS, 60)

        reviews_data = apify_scrape_reviews(
            canonical_url,
            max_reviews=max_reviews,
            language="zh-TW",
        )
    except requests.exceptions.Timeout:
        return jsonify({"error": "抓取評論逾時，可能是餐廳評論過多，請稍後再試"}), 504
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response else 0
        if status == 401:
            return jsonify({"error": "Apify API Token 已失效，請聯繫管理員"}), 502
        if status == 429:
            return jsonify({"error": "本月 API 額度已用完，請下個月再試或聯繫管理員"}), 429
        return jsonify({"error": f"抓取評論時發生錯誤 (HTTP {status})，請稍後再試"}), 502
    except Exception as e:
        return jsonify({"error": f"抓取評論失敗：{str(e)[:100]}"}), 500

    if not reviews_data:
        return jsonify({"error": "此餐廳沒有找到任何評論，請確認連結是否正確"}), 404

    if len(reviews_data) < 3:
        # Still proceed but warn
        pass

    # --- Step 2: AI analysis ---
    try:
        analysis = analyse_reviews(reviews_data, model=model)
    except requests.exceptions.Timeout:
        if model == "gemini-3-pro-preview":
            return jsonify({"error": "完整模式 AI 分析逾時（Pro 模型回應較慢），請改用「快速模式」或稍後再試"}), 504
        return jsonify({"error": "AI 分析逾時，請稍後再試"}), 504
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response else 0
        if status == 429:
            return jsonify({"error": "AI API 額度不足，請稍後再試"}), 429
        return jsonify({"error": f"AI 分析服務錯誤 (HTTP {status})，請稍後重試"}), 502
    except json.JSONDecodeError:
        return jsonify({"error": "AI 回傳格式異常，請重新嘗試（建議切換模式）"}), 500
    except Exception as e:
        return jsonify({"error": f"AI 分析失敗：{str(e)[:100]}"}), 500

    # --- Step 3: Enrich with photos ---
    try:
        restaurant_name = analysis.get("restaurant_name", "")
        analysis = enrich_photos(analysis, reviews_data, restaurant_name)
    except Exception:
        pass  # photos are optional, don't fail the whole request

    # --- Step 4: Write to cache (best-effort) ---
    try:
        set_cached_analysis(
            cache_key=cache_key,
            mode=mode,
            canonical_url=canonical_url,
            display_name=display_name,
            result_obj=analysis,
        )
    except Exception:
        # 快取失敗不應影響主要回應
        pass

    return jsonify(analysis)


@app.route("/api/search_places", methods=["POST"])
def api_search_places():
    """Search Google Maps places by text and return candidate list for user to choose.

    This is used by the "用店名找餐廳" mode on the frontend to first confirm
    the exact place before running the full review analysis.
    """
    body = request.get_json(force=True) or {}
    query = (body.get("query") or "").strip()
    try:
        limit = int(body.get("limit") or 6)
    except (ValueError, TypeError):
        limit = 6
    limit = max(1, min(limit, 10))

    if not query:
        return jsonify({"error": "請輸入餐廳名稱或關鍵字"}), 400

    if not GOOGLE_PLACES_API_KEY:
        return (
            jsonify(
                {
                    "error": "伺服器尚未設定 Google Places API Key，暫時無法使用「用店名找餐廳」功能。",
                }
            ),
            500,
        )

    params = {
        "query": query,
        "language": "zh-TW",
        "key": GOOGLE_PLACES_API_KEY,
        # 偏向餐飲相關地點，但仍保留彈性
        "type": "restaurant",
    }

    try:
        resp = requests.get(
            "https://maps.googleapis.com/maps/api/place/textsearch/json",
            params=params,
            timeout=10,
        )
        resp.raise_for_status()
    except requests.exceptions.Timeout:
        return jsonify({"error": "向 Google 搜尋餐廳逾時，請稍後再試"}), 504
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response else 0
        return (
            jsonify(
                {
                    "error": f"向 Google 搜尋餐廳時發生錯誤 (HTTP {status})，請稍後再試",
                }
            ),
            502,
        )
    except Exception:
        return jsonify({"error": "向 Google 搜尋餐廳時發生未知錯誤，請稍後再試"}), 502

    data = resp.json() or {}
    api_status = data.get("status")
    if api_status not in ("OK", "ZERO_RESULTS"):
        # 常見狀態：REQUEST_DENIED / OVER_QUERY_LIMIT / INVALID_REQUEST ...
        message = data.get("error_message") or f"Google Places API 狀態：{api_status}"
        return jsonify({"error": message}), 502

    results = []
    for item in (data.get("results") or [])[:limit]:
        place_id = item.get("place_id")
        name = item.get("name") or ""
        address = item.get("formatted_address") or item.get("vicinity") or ""
        rating = item.get("rating")
        total_ratings = item.get("user_ratings_total")

        if place_id:
            maps_url = f"https://www.google.com/maps/place/?q=place_id:{place_id}"
        else:
            # 後備：沒有 place_id 就嘗試使用 geometry/location + query，但仍提供原始搜尋文字
            maps_url = None

        results.append(
            {
                "place_id": place_id,
                "name": name,
                "address": address,
                "rating": rating,
                "user_ratings_total": total_ratings,
                "maps_url": maps_url,
            }
        )

    return jsonify({"query": query, "results": results})


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    debug = os.getenv("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)
