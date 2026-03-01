#!/usr/bin/env python3
import sqlite3
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from services.place_store import upsert_catalog_place, record_place_from_analysis

source_db = os.path.abspath("../fkp/data/analysis_cache.db")
target_db = os.path.abspath("data/analysis_cache.db")

print("開始遷移...")
source_conn = sqlite3.connect(source_db)
source_conn.row_factory = sqlite3.Row
target_conn = sqlite3.connect(target_db)

source_cur = source_conn.cursor()
source_cur.execute("SELECT cache_key, mode, canonical_url, display_name, result_json, created_at FROM analysis_cache")
rows = source_cur.fetchall()
print(f"找到 {len(rows)} 筆記錄")

target_cur = target_conn.cursor()
for idx, row in enumerate(rows):
    print(f"[{idx+1}/{len(rows)}] {row['display_name']}")
    try:
        # 遷移 analysis_cache
        target_cur.execute(
            "INSERT INTO analysis_cache (cache_key, mode, canonical_url, display_name, result_json, created_at) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(cache_key, mode) DO UPDATE SET canonical_url=excluded.canonical_url, display_name=excluded.display_name, result_json=excluded.result_json, created_at=excluded.created_at",
            (row['cache_key'], row['mode'], row['canonical_url'], row['display_name'], row['result_json'], row['created_at'])
        )
        target_conn.commit()  # 先提交，避免鎖定
        
        # 解析並記錄到 places 和 place_catalog
        analysis_obj = json.loads(row['result_json'])
        record_place_from_analysis(row['canonical_url'], row['display_name'], analysis_obj, db_path=target_db)
        
        place_info = {
            'name': analysis_obj.get('restaurant_name') or analysis_obj.get('name') or row['display_name'],
            'address': analysis_obj.get('address') or analysis_obj.get('google_address'),
            'google_rating': analysis_obj.get('google_rating'),
            'user_ratings_total': analysis_obj.get('google_reviews_count') or analysis_obj.get('total_reviews_analyzed'),
        }
        maps_url = row['canonical_url'] if row['canonical_url'].startswith('http') else None
        upsert_catalog_place(
            tag='migrated',
            canonical_url=row['canonical_url'],
            maps_url=maps_url,
            name=place_info.get('name'),
            address=place_info.get('address'),
            google_rating=place_info.get('google_rating'),
            user_ratings_total=place_info.get('user_ratings_total'),
            last_analyzed_at=row['created_at'],
            last_analyze_status='done',
            db_path=target_db
        )
        print(f"  OK")
    except Exception as e:
        print(f"  ERROR: {e}")
        import traceback
        traceback.print_exc()

target_conn.commit()
source_conn.close()
target_conn.close()

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

print(f"\n遷移完成!")
print(f"  analysis_cache: {ac_count} 筆")
print(f"  place_catalog: {pc_count} 筆")
print(f"  places: {pl_count} 筆")
