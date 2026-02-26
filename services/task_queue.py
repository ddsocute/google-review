import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Any, Optional

from services.url_normalizer import normalize_input_to_canonical, canonicalize
from services.apify_client import scrape_reviews
from services.cache_store import get_cached_analysis, set_cached_analysis

# 全域 ThreadPoolExecutor（最多 3 個 worker）
_EXECUTOR = ThreadPoolExecutor(max_workers=3)

# 任務保留時間（秒）：2 小時
TASK_TTL_SECONDS = 2 * 60 * 60

# 內部狀態
_tasks_by_id: Dict[str, Dict[str, Any]] = {}
_running_by_dedupe_key: Dict[str, str] = {}  # dedupe_key -> task_id
_lock = threading.Lock()


def _now_ts() -> float:
    return time.time()


def _cleanup_expired() -> None:
    """移除已超過 TTL 的任務，同時清掉對應 dedupe mapping。"""
    now = _now_ts()
    with _lock:
        expired_task_ids = [
            tid
            for tid, task in _tasks_by_id.items()
            if now - float(task.get("created_at", now)) > TASK_TTL_SECONDS
        ]

        for tid in expired_task_ids:
            # 先移除 dedupe 映射
            keys_to_delete = [
                dk for dk, mapped_tid in _running_by_dedupe_key.items() if mapped_tid == tid
            ]
            for dk in keys_to_delete:
                _running_by_dedupe_key.pop(dk, None)

            _tasks_by_id.pop(tid, None)


def _make_task_dict(
    task_id: str,
    mode: str,
    input_raw: str,
    canonical_url: str,
    display_name: str,
    cache_key: str,
) -> Dict[str, Any]:
    now = _now_ts()
    return {
        "task_id": task_id,
        "status": "pending",
        "progress": 0,
        "message": "任務已建立，等待執行",
        "mode": mode,
        "input_raw": input_raw,
        "canonical_url": canonical_url,
        "display_name": display_name,
        "cache_key": cache_key,
        "final_cache_key": cache_key,
        "created_at": now,
        "updated_at": now,
        "error": None,
    }


def _update_task(task_id: str, **fields: Any) -> None:
    with _lock:
        task = _tasks_by_id.get(task_id)
        if not task:
            return
        task.update(fields)
        task["updated_at"] = _now_ts()


def _get_task_copy(task_id: str) -> Optional[Dict[str, Any]]:
    with _lock:
        task = _tasks_by_id.get(task_id)
        if not task:
            return None
        return dict(task)


def submit_task(input_raw: str, mode: str = "quick") -> Dict[str, Any]:
    """
    提交一個分析任務。

    去重規則：
      dedupe_key = f"{cache_key}:{mode}"
      若相同 dedupe_key 的任務仍在 pending/running 且未過期，直接回傳原任務。
    """
    _cleanup_expired()

    # 先做一次正規化，取得初始 canonical_url / cache_key 作為 dedupe 基礎
    info = normalize_input_to_canonical(input_raw)
    canonical_url = info.get("canonical_url", "")
    display_name = info.get("display_name") or input_raw
    cache_key = info.get("cache_key", "")

    dedupe_key = f"{cache_key}:{mode}"
    now = _now_ts()

    with _lock:
        # 若已有同 dedupe_key 的任務且仍有效，直接回傳
        existing_task_id = _running_by_dedupe_key.get(dedupe_key)
        if existing_task_id:
            existing = _tasks_by_id.get(existing_task_id)
            if existing:
                if now - float(existing.get("created_at", now)) <= TASK_TTL_SECONDS and existing[
                    "status"
                ] in {"pending", "running"}:
                    return dict(existing)
                # 否則視為過期 / 結束，移除映射
                _running_by_dedupe_key.pop(dedupe_key, None)

        task_id = str(uuid.uuid4())
        task = _make_task_dict(
            task_id=task_id,
            mode=mode,
            input_raw=input_raw,
            canonical_url=canonical_url,
            display_name=display_name,
            cache_key=cache_key,
        )
        _tasks_by_id[task_id] = task
        _running_by_dedupe_key[dedupe_key] = task_id

    # 排程執行 worker
    _EXECUTOR.submit(_run_worker, task_id)
    return dict(task)


def get_task_status(task_id: str) -> Optional[Dict[str, Any]]:
    """取得任務最新狀態。"""
    _cleanup_expired()
    return _get_task_copy(task_id)


def get_task_result(task_id: str) -> Optional[Dict[str, Any]]:
    """
    僅在任務 status == "done" 時回傳結果物件。
    結果來源：cache_store 中 final_cache_key（若無則 cache_key） 對應的快取。
    """
    _cleanup_expired()
    task = _get_task_copy(task_id)
    if not task or task.get("status") != "done":
        return None

    final_cache_key = task.get("final_cache_key") or task.get("cache_key")
    if not final_cache_key:
        return None

    entry = get_cached_analysis(final_cache_key, task.get("mode", "quick"))
    if not entry:
        # 依規格：若 cache miss 就回傳 None
        return None
    return entry.as_result_object()


def _run_worker(task_id: str) -> None:
    """實際執行分析流程的 worker。"""
    task = _get_task_copy(task_id)
    if not task:
        return

    input_raw = task["input_raw"]
    mode = task["mode"]

    _update_task(task_id, status="running", progress=5, message="解析輸入中")

    try:
        # 1) 重新正規化輸入，確保使用最新規則
        info = normalize_input_to_canonical(input_raw)
        canonical_url = info.get("canonical_url", task.get("canonical_url", ""))
        display_name = info.get("display_name") or task.get("display_name", "")
        cache_key = info.get("cache_key", task.get("cache_key", ""))

        _update_task(
            task_id,
            canonical_url=canonical_url,
            display_name=display_name,
            cache_key=cache_key,
            final_cache_key=cache_key,
            progress=10,
            message="檢查快取中",
        )

        # 2) 先查一次 cache（初始 cache_key）
        entry = get_cached_analysis(cache_key, mode)
        if entry:
            _update_task(
                task_id,
                status="done",
                progress=100,
                message="分析完成（來自初始快取）",
                error=None,
            )
            return

        # 3) 呼叫 Apify 抓評論
        _update_task(task_id, progress=20, message="抓取評論中")
        # 方便驗證快取是否生效：只有實際呼叫 scrape_reviews 時才會印出這行
        print(f"[task_queue] scrape_reviews called for url={canonical_url!r}, mode={mode!r}")
        if mode == "deep":
            max_reviews = 90
        else:
            max_reviews = 30

        reviews = scrape_reviews(canonical_url, max_reviews=max_reviews, language="zh-TW")

        # 4) 嘗試從評論升級 cache_key
        new_cache_key = cache_key
        new_canonical_url = canonical_url
        new_display_name = display_name

        for r in reviews:
            url = r.get("url") or r.get("placeUrl")
            if url:
                try:
                    norm = canonicalize(url)
                    if norm:
                        if norm.get("cache_key"):
                            new_cache_key = norm["cache_key"]
                        if norm.get("canonical_url"):
                            new_canonical_url = norm["canonical_url"]
                        if norm.get("display_name"):
                            new_display_name = norm["display_name"]
                        break
                except Exception:
                    pass

            place_id = r.get("placeId") or r.get("place_id")
            if place_id:
                new_cache_key = f"place_id:{place_id}"
                break

            cid = r.get("cid")
            if cid:
                new_cache_key = f"cid:{cid}"
                break

        final_cache_key = new_cache_key
        _update_task(
            task_id,
            final_cache_key=final_cache_key,
            canonical_url=new_canonical_url,
            display_name=new_display_name,
            progress=40,
            message="檢查升級後快取中",
        )

        # 5) 若升級後 key 不同，再查一次 cache
        if final_cache_key and final_cache_key != cache_key:
            upgraded_entry = get_cached_analysis(final_cache_key, mode)
            if upgraded_entry:
                _update_task(
                    task_id,
                    status="done",
                    progress=100,
                    message="分析完成（來自升級後快取）",
                    error=None,
                )
                return

        # 6) mock LLM 分析
        _update_task(task_id, progress=60, message="分析評論中（mock LLM）")
        time.sleep(2)

        sample_reviews = [
            (r.get("text") or r.get("reviewText") or "")[:80] for r in reviews[:3]
        ]

        result_obj = {
            "mock": True,
            "mode": mode,
            "place": new_display_name,
            "review_count": len(reviews),
            "sample_reviews": sample_reviews,
        }

        _update_task(task_id, progress=80, message="寫入快取中")
        set_cached_analysis(
            final_cache_key or cache_key,
            mode,
            new_canonical_url,
            new_display_name,
            result_obj,
        )

        # 7) 完成
        _update_task(
            task_id,
            status="done",
            progress=100,
            message="分析完成",
            error=None,
        )

    except Exception as e:
        _update_task(
            task_id,
            status="error",
            message="分析過程中發生錯誤",
            error=str(e),
        )

    finally:
        # 任務結束（無論成功或失敗）都要解除 dedupe mapping
        with _lock:
            keys_to_delete = [
                dk for dk, tid in _running_by_dedupe_key.items() if tid == task_id
            ]
            for dk in keys_to_delete:
                _running_by_dedupe_key.pop(dk, None)


__all__ = [
    "TASK_TTL_SECONDS",
    "submit_task",
    "get_task_status",
    "get_task_result",
]

