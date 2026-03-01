#!/usr/bin/env python3
"""
將原本的 analysis_cache 資料庫遷移到新的三層資料庫結構：
- place_catalog: 店家目錄
- place_reviews: 評論（原本可能沒有，暫時跳過）
- analysis_cache: 分析快取（直接遷移）
"""

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Optional

# 添加專案根目錄到路徑
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.cache_store import _get_connection, init_db
from services.place_store import init_place_db, upsert_catalog_place, record_place_from_analysis
from services.review_store import init_review_db


def _parse_iso_dt(s: Any) -> Optional[datetime]:
    """解析 ISO8601 時間字串"""
    if not s:
        return None
    if isinstance(s, datetime):
        return s
    try:
        s_str = str(s).strip()
        if not s_str:
            return None
        # 嘗試解析 ISO8601 格式
        if "T" in s_str or "+" in s_str or s_str.endswith("Z"):
            # ISO8601 格式
            if s_str.endswith("Z"):
                s_str = s_str[:-1] + "+00:00"
            return datetime.fromisoformat(s_str.replace("Z", "+00:00"))
        # 嘗試其他常見格式
        for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"]:
            try:
                return datetime.strptime(s_str, fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        return None
    except Exception:
        return None


def _extract_place_info_from_analysis(result_json: str, canonical_url: str) -> Dict[str, Any]:
    """從分析結果中提取店家資訊"""
    try:
        analysis = json.loads(result_json)
        if not isinstance(analysis, dict):
            return {}
        
        # 提取可能的店家資訊
        info = {
            "name": analysis.get("restaurant_name") or analysis.get("name"),
            "address": analysis.get("address") or analysis.get("google_address"),
            "google_rating": analysis.get("google_rating"),
            "user_ratings_total": analysis.get("google_reviews_count") or analysis.get("total_reviews_analyzed"),
        }
        
        # 嘗試從 canonical_url 提取 place_id（如果有的話）
        # 例如：https://www.google.com/maps/place/?q=place_id:ChIJ...
        if "place_id:" in canonical_url:
            try:
                place_id_part = canonical_url.split("place_id:")[1].split("&")[0].split("/")[0]
                info["place_id"] = place_id_part
            except Exception:
                pass
        
        return {k: v for k, v in info.items() if v is not None}
    except Exception:
        return {}


def migrate_analysis_cache(
    source_db_path: str,
    target_db_path: str,
    *,
    tag: str = "migrated",
    dry_run: bool = False,
) -> Dict[str, int]:
    """
    遷移 analysis_cache 資料到新的三層資料庫
    
    Args:
        source_db_path: 來源資料庫路徑
        target_db_path: 目標資料庫路徑
        tag: 用於 place_catalog 的 tag（預設 "migrated"）
        dry_run: 是否為試運行（不實際寫入）
    
    Returns:
        遷移統計資訊
    """
    stats = {
        "analysis_cache_migrated": 0,
        "place_catalog_inserted": 0,
        "places_recorded": 0,
        "errors": 0,
    }
    
    # 檢查來源資料庫是否存在
    if not os.path.exists(source_db_path):
        print(f"[錯誤] 來源資料庫不存在: {source_db_path}", flush=True)
        return stats
    
    # 初始化目標資料庫結構
    print(f"[初始化] 初始化目標資料庫結構: {target_db_path}", flush=True)
    if not dry_run:
        init_db(target_db_path)
        init_place_db(target_db_path)
        init_review_db(target_db_path)
    
    # 連接來源資料庫
    source_conn = sqlite3.connect(source_db_path)
    source_conn.row_factory = sqlite3.Row
    
    # 連接目標資料庫
    target_conn = _get_connection(target_db_path)
    
    try:
        # 讀取來源資料庫的 analysis_cache
        source_cur = source_conn.cursor()
        source_cur.execute(
            "SELECT cache_key, mode, canonical_url, display_name, result_json, created_at FROM analysis_cache"
        )
        rows = source_cur.fetchall()
        
        print(f"[讀取] 從來源資料庫讀取 {len(rows)} 筆 analysis_cache 記錄", flush=True)
        
        target_cur = target_conn.cursor()
        
        for idx, row in enumerate(rows):
            try:
                cache_key = row["cache_key"]
                mode = row["mode"]
                canonical_url = row["canonical_url"]
                display_name = row["display_name"]
                result_json = row["result_json"]
                created_at_str = row["created_at"]
                
                # 解析時間
                created_at = _parse_iso_dt(created_at_str) or datetime.now(timezone.utc)
                created_at_iso = created_at.isoformat()
                
                if not dry_run:
                    # 1. 遷移 analysis_cache
                    target_cur.execute(
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
                        (cache_key, mode, canonical_url, display_name, result_json, created_at_iso),
                    )
                    stats["analysis_cache_migrated"] += 1
                    
                    # 2. 嘗試從 result_json 提取資訊並填充 place_catalog
                    place_info = _extract_place_info_from_analysis(result_json, canonical_url)
                    
                    # 構建 maps_url（如果 canonical_url 看起來像 Google Maps URL）
                    maps_url = canonical_url if canonical_url.startswith("http") else None
                    
                    # 嘗試解析分析結果以記錄到 places 表
                    try:
                        analysis_obj = json.loads(result_json)
                        if isinstance(analysis_obj, dict):
                            record_place_from_analysis(
                                canonical_url=canonical_url,
                                display_name=display_name,
                                analysis=analysis_obj,
                                address=place_info.get("address"),
                                google_rating=place_info.get("google_rating"),
                                user_ratings_total=place_info.get("user_ratings_total"),
                                db_path=target_db_path,
                            )
                            stats["places_recorded"] += 1
                    except Exception as e:
                        print(f"[警告] 記錄 place 失敗 (canonical_url={canonical_url[:50]}...): {e}", flush=True)
                    
                    # 填充 place_catalog
                    upsert_catalog_place(
                        tag=tag,
                        canonical_url=canonical_url,
                        maps_url=maps_url,
                        place_id=place_info.get("place_id"),
                        name=place_info.get("name") or display_name,
                        address=place_info.get("address"),
                        google_rating=place_info.get("google_rating"),
                        user_ratings_total=place_info.get("user_ratings_total"),
                        last_analyzed_at=created_at_iso,
                        last_analyze_status="done",
                        db_path=target_db_path,
                    )
                    stats["place_catalog_inserted"] += 1
                else:
                    # dry_run 模式：只計數
                    stats["analysis_cache_migrated"] += 1
                    stats["place_catalog_inserted"] += 1
                
                if (idx + 1) % 100 == 0:
                    print(f"[進度] 已處理 {idx + 1}/{len(rows)} 筆記錄...", flush=True)
                    
            except Exception as e:
                stats["errors"] += 1
                print(f"[錯誤] 處理記錄失敗 (idx={idx}): {e}", flush=True)
                import traceback
                traceback.print_exc()
        
        if not dry_run:
            target_conn.commit()
        
        print(f"[完成] 遷移完成:", flush=True)
        print(f"  - analysis_cache: {stats['analysis_cache_migrated']} 筆", flush=True)
        print(f"  - place_catalog: {stats['place_catalog_inserted']} 筆", flush=True)
        print(f"  - places: {stats['places_recorded']} 筆", flush=True)
        print(f"  - 錯誤: {stats['errors']} 筆", flush=True)
        
    finally:
        source_conn.close()
        target_conn.close()
    
    return stats


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="遷移資料庫到三層結構")
    parser.add_argument(
        "--source",
        required=True,
        help="來源資料庫路徑（例如: ../fkp/data/analysis_cache.db）",
    )
    parser.add_argument(
        "--target",
        default="data/analysis_cache.db",
        help="目標資料庫路徑（預設: data/analysis_cache.db）",
    )
    parser.add_argument(
        "--tag",
        default="migrated",
        help="place_catalog 的 tag（預設: migrated）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="試運行模式（不實際寫入）",
    )
    
    try:
        args = parser.parse_args()
        
        # 轉換為絕對路徑
        source_path = os.path.abspath(args.source)
        target_path = os.path.abspath(args.target)
        
        print(f"[開始] 資料庫遷移", flush=True)
        print(f"  來源: {source_path}", flush=True)
        print(f"  目標: {target_path}", flush=True)
        print(f"  Tag: {args.tag}", flush=True)
        print(f"  模式: {'試運行' if args.dry_run else '實際遷移'}", flush=True)
        print(flush=True)
        
        stats = migrate_analysis_cache(
            source_db_path=source_path,
            target_db_path=target_path,
            tag=args.tag,
            dry_run=args.dry_run,
        )
        
        if args.dry_run:
            print("\n[提示] 這是試運行模式，沒有實際寫入資料。移除 --dry-run 參數以執行實際遷移。", flush=True)
    except Exception as e:
        print(f"[錯誤] 執行失敗: {e}", flush=True)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
