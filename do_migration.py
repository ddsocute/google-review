#!/usr/bin/env python3
"""執行資料庫遷移 - 遷移 place_catalog 和 analysis_cache"""
import sqlite3
import json
import os
import sys

# 添加專案路徑
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from services.place_store import upsert_catalog_place, record_place_from_analysis
from services.cache_store import init_db
from services.place_store import init_place_db

# 路徑設定
source_db = os.path.abspath("../fkp/data/analysis_cache.db")
target_db = os.path.abspath("data/analysis_cache.db")

print("=" * 60)
print("資料庫遷移腳本")
print("=" * 60)
print(f"來源: {source_db}")
print(f"目標: {target_db}")
print()

# 檢查來源資料庫
if not os.path.exists(source_db):
    print(f"錯誤: 來源資料庫不存在: {source_db}")
    sys.exit(1)

# 初始化目標資料庫結構
print("初始化目標資料庫結構...")
init_db(target_db)
init_place_db(target_db)
print("[OK] 資料庫結構初始化完成")
print()

# 連接資料庫
source_conn = sqlite3.connect(source_db)
source_conn.row_factory = sqlite3.Row
target_conn = sqlite3.connect(target_db)
target_conn.row_factory = sqlite3.Row

stats = {"analysis_cache": 0, "place_catalog": 0, "places": 0, "errors": 0}

# ===== 1. 遷移 place_catalog =====
print("=" * 60)
print("步驟 1: 遷移 place_catalog")
print("=" * 60)
source_cur = source_conn.cursor()
source_cur.execute("""
    SELECT tag, canonical_url, maps_url, place_id, name, address, lat, lng,
           google_rating, user_ratings_total, source_query,
           discovered_at, last_seen_at, last_analyzed_at,
           last_analyze_status, last_error
    FROM place_catalog
""")
catalog_rows = source_cur.fetchall()

print(f"找到 {len(catalog_rows)} 筆 place_catalog 記錄")
print()

target_cur = target_conn.cursor()

for idx, row in enumerate(catalog_rows):
    try:
        target_cur.execute("""
            INSERT INTO place_catalog (
                tag, canonical_url, maps_url, place_id, name, address, lat, lng,
                google_rating, user_ratings_total, source_query,
                discovered_at, last_seen_at, last_analyzed_at,
                last_analyze_status, last_error
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(tag, canonical_url) DO UPDATE SET
                maps_url = COALESCE(excluded.maps_url, place_catalog.maps_url),
                place_id = COALESCE(excluded.place_id, place_catalog.place_id),
                name = COALESCE(excluded.name, place_catalog.name),
                address = COALESCE(excluded.address, place_catalog.address),
                lat = COALESCE(excluded.lat, place_catalog.lat),
                lng = COALESCE(excluded.lng, place_catalog.lng),
                google_rating = COALESCE(excluded.google_rating, place_catalog.google_rating),
                user_ratings_total = COALESCE(excluded.user_ratings_total, place_catalog.user_ratings_total),
                source_query = COALESCE(excluded.source_query, place_catalog.source_query),
                last_seen_at = excluded.last_seen_at,
                last_analyzed_at = COALESCE(excluded.last_analyzed_at, place_catalog.last_analyzed_at),
                last_analyze_status = COALESCE(excluded.last_analyze_status, place_catalog.last_analyze_status),
                last_error = COALESCE(excluded.last_error, place_catalog.last_error)
        """, (
            row["tag"], row["canonical_url"], row["maps_url"], row["place_id"],
            row["name"], row["address"], row["lat"], row["lng"],
            row["google_rating"], row["user_ratings_total"], row["source_query"],
            row["discovered_at"], row["last_seen_at"], row["last_analyzed_at"],
            row["last_analyze_status"], row["last_error"]
        ))
        stats["place_catalog"] += 1
        
        if (idx + 1) % 500 == 0:
            print(f"  已處理 {idx + 1}/{len(catalog_rows)} 筆...")
            target_conn.commit()
    except Exception as e:
        stats["errors"] += 1
        print(f"  ✗ 處理 place_catalog 記錄失敗 (idx={idx}): {e}")
        import traceback
        traceback.print_exc()

target_conn.commit()
print(f"[OK] place_catalog 遷移完成: {stats['place_catalog']} 筆")
print()

# ===== 2. 遷移 analysis_cache =====
print("=" * 60)
print("步驟 2: 遷移 analysis_cache")
print("=" * 60)
source_cur.execute("SELECT cache_key, mode, canonical_url, display_name, result_json, created_at FROM analysis_cache")
analysis_rows = source_cur.fetchall()

print(f"找到 {len(analysis_rows)} 筆 analysis_cache 記錄")
print()

for idx, row in enumerate(analysis_rows):
    try:
        target_cur.execute(
            """
            INSERT INTO analysis_cache (cache_key, mode, canonical_url, display_name, result_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(cache_key, mode) DO UPDATE SET
                canonical_url = excluded.canonical_url,
                display_name = excluded.display_name,
                result_json = excluded.result_json,
                created_at = excluded.created_at
            """,
            (row["cache_key"], row["mode"], row["canonical_url"], row["display_name"], 
             row["result_json"], row["created_at"])
        )
        stats["analysis_cache"] += 1
        
        # 嘗試解析 result_json 並記錄到 places
        try:
            analysis_obj = json.loads(row["result_json"])
            if isinstance(analysis_obj, dict):
                record_place_from_analysis(
                    canonical_url=row["canonical_url"],
                    display_name=row["display_name"],
                    analysis=analysis_obj,
                    db_path=target_db
                )
                stats["places"] += 1
        except Exception as e:
            pass  # 忽略解析錯誤
        
    except Exception as e:
        stats["errors"] += 1
        print(f"  ✗ 處理 analysis_cache 記錄失敗 (idx={idx}): {e}")

target_conn.commit()
print(f"[OK] analysis_cache 遷移完成: {stats['analysis_cache']} 筆")
print(f"[OK] places 記錄完成: {stats['places']} 筆")
print()

# 關閉連接
source_conn.close()
target_conn.close()

# 顯示統計
print("=" * 60)
print("遷移完成!")
print("=" * 60)
print(f"  analysis_cache: {stats['analysis_cache']} 筆")
print(f"  place_catalog: {stats['place_catalog']} 筆")
print(f"  places: {stats['places']} 筆")
print(f"  錯誤: {stats['errors']} 筆")
print()

# 驗證
target_conn = sqlite3.connect(target_db)
target_cur = target_conn.cursor()
target_cur.execute("SELECT COUNT(*) FROM analysis_cache")
ac_count = target_cur.fetchone()[0]
target_cur.execute("SELECT COUNT(*) FROM place_catalog")
pc_count = target_cur.fetchone()[0]
target_cur.execute("SELECT COUNT(*) FROM places")
pl_count = target_cur.fetchone()[0]
target_conn.close()

print("驗證結果:")
print(f"  analysis_cache: {ac_count} 筆")
print(f"  place_catalog: {pc_count} 筆")
print(f"  places: {pl_count} 筆")
