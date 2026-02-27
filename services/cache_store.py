import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

# SQLite DB 位置：
# - 一般本機 / 傳統伺服器：專案根目錄下 data/analysis_cache.db
# - Vercel 等 Serverless 平台：必須使用可寫入的暫存目錄（例如 /tmp），
#   否則會因唯讀檔案系統導致 Serverless Function 啟動時就直接 500。
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 在 Vercel 環境（或任何設定了 VERCEL / VERCEL_ENV 的環境）時改用 /tmp
if os.getenv("VERCEL") or os.getenv("VERCEL_ENV"):
    DEFAULT_DB_PATH = os.path.join("/tmp", "analysis_cache.db")
else:
    DEFAULT_DB_PATH = os.path.join(BASE_DIR, "data", "analysis_cache.db")

# 7 天 TTL（秒），可透過環境變數覆寫：
# - CACHE_TTL_SECONDS：整體預設 TTL（秒）
DEFAULT_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60
try:
    CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", DEFAULT_CACHE_TTL_SECONDS))
except (TypeError, ValueError):
    CACHE_TTL_SECONDS = DEFAULT_CACHE_TTL_SECONDS


@dataclass
class CacheEntry:
    cache_key: str
    mode: str
    canonical_url: str
    display_name: str
    result_json: str
    created_at: datetime

    def as_result_object(self) -> Any:
        """將 result_json 還原成 Python 物件。解析失敗時回傳原始字串。"""
        try:
            return json.loads(self.result_json)
        except Exception:
            return self.result_json


def _get_db_path(db_path: Optional[str] = None) -> str:
    return db_path or DEFAULT_DB_PATH


def _get_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    path = _get_db_path(db_path)
    # 確保資料夾存在
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # timeout + busy_timeout：避免多個 worker 併發寫入時過早拋出 "database is locked"
    conn = sqlite3.connect(path, detect_types=sqlite3.PARSE_DECLTYPES, timeout=5.0)
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Optional[str] = None) -> None:
    """
    確保資料庫與 analysis_cache 資料表存在。

    表結構：
      - cache_key (TEXT)
      - mode (TEXT)
      - canonical_url (TEXT)
      - display_name (TEXT)
      - result_json (TEXT)
      - created_at (TIMESTAMP, ISO8601 UTC)
      - PRIMARY KEY (cache_key, mode)
    """
    conn = _get_connection(db_path)
    try:
        cur = conn.cursor()
        # 啟用 WAL 以改善多讀少寫情境下的併發能力
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS analysis_cache (
                cache_key TEXT NOT NULL,
                mode TEXT NOT NULL,
                canonical_url TEXT NOT NULL,
                display_name TEXT NOT NULL,
                result_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (cache_key, mode)
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def _row_to_entry(row: sqlite3.Row) -> CacheEntry:
    created_at_str = row["created_at"]
    try:
        created_at = datetime.fromisoformat(created_at_str)
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
    except Exception:
        created_at = datetime.now(timezone.utc)

    return CacheEntry(
        cache_key=row["cache_key"],
        mode=row["mode"],
        canonical_url=row["canonical_url"],
        display_name=row["display_name"],
        result_json=row["result_json"],
        created_at=created_at,
    )


def get_cached_analysis(
    cache_key: str,
    mode: str,
    *,
    allow_stale: bool = False,
    db_path: Optional[str] = None,
) -> Optional[CacheEntry]:
    """
    取得未過期的快取內容。
    若資料不存在回傳 None。

    預設會套用 TTL：超過 TTL 視同 miss 回傳 None。
    若 allow_stale=True，則即使超過 TTL 仍會回傳資料（用於「舊資料仍可讀」的情境）。
    """
    now = datetime.now(timezone.utc)
    conn = _get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT cache_key, mode, canonical_url, display_name, result_json, created_at
            FROM analysis_cache
            WHERE cache_key = ? AND mode = ?
            """,
            (cache_key, mode),
        )
        row = cur.fetchone()
        if not row:
            return None

        entry = _row_to_entry(row)
        if now - entry.created_at > timedelta(seconds=CACHE_TTL_SECONDS):
            # 視同 miss（不在這裡刪除，交給 purge_expired 或後台處理）
            if not allow_stale:
                return None
        return entry
    finally:
        conn.close()


def set_cached_analysis(
    cache_key: str,
    mode: str,
    canonical_url: str,
    display_name: str,
    result_obj: Any,
    db_path: Optional[str] = None,
) -> None:
    """
    寫入或覆寫快取紀錄。
    result_obj 會被序列化成 JSON 字串存入 result_json。
    """
    created_at = datetime.now(timezone.utc).isoformat()
    try:
        result_json = json.dumps(result_obj, ensure_ascii=False)
    except TypeError:
        # 若無法序列化，就轉成字串儲存
        result_json = json.dumps(str(result_obj), ensure_ascii=False)

    conn = _get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO analysis_cache (
                cache_key, mode, canonical_url, display_name, result_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(cache_key, mode) DO UPDATE SET
                canonical_url = excluded.canonical_url,
                display_name = excluded.display_name,
                result_json = excluded.result_json,
                created_at = excluded.created_at
            """,
            (cache_key, mode, canonical_url, display_name, result_json, created_at),
        )
        conn.commit()
    finally:
        conn.close()


def delete_cache_entry(
    cache_key: str,
    mode: str,
    db_path: Optional[str] = None,
) -> None:
    """刪除指定快取紀錄。"""
    conn = _get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM analysis_cache WHERE cache_key = ? AND mode = ?",
            (cache_key, mode),
        )
        conn.commit()
    finally:
        conn.close()


def purge_expired(
    db_path: Optional[str] = None,
) -> int:
    """
    刪除所有已超過 TTL 的快取紀錄。

    回傳實際刪除的筆數。
    """
    threshold = datetime.now(timezone.utc) - timedelta(seconds=CACHE_TTL_SECONDS)
    threshold_str = threshold.isoformat()

    conn = _get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM analysis_cache WHERE created_at < ?",
            (threshold_str,),
        )
        deleted = cur.rowcount or 0
        conn.commit()
        return deleted
    finally:
        conn.close()


__all__ = [
    "CacheEntry",
    "CACHE_TTL_SECONDS",
    "DEFAULT_DB_PATH",
    "init_db",
    "get_cached_analysis",
    "set_cached_analysis",
    "delete_cache_entry",
    "purge_expired",
]

