from __future__ import annotations

import os
import sys
import json

from dotenv import load_dotenv


def main() -> None:
    # Ensure project root is on sys.path when running as `python scripts/...`
    PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if PROJECT_ROOT not in sys.path:
        sys.path.insert(0, PROJECT_ROOT)

    load_dotenv(".env", override=True)

    from services.apify_client import search_places_by_text

    tests = [
        "鼎泰豐 信義",
        "鼎泰豐 信義店",
        "鼎泰豐 101",
        "鼎泰豐 台北101店",
        "Din Tai Fung Taipei 101",
        "Din Tai Fung Xinyi",
        "鼎泰豐 市政府",
        "鼎泰豐 微風南山",
        "Din Tai Fung Taipei 101 Branch",
        "Din Tai Fung Taipei 101店",
    ]

    for q in tests:
        try:
            r = search_places_by_text(q, limit=5, language="zh-TW", heartbeat_every=0.0, heartbeat_prefix="probe")
            print(f"{q} -> {len(r)}")
            if r:
                print("  top:", r[0].get("name"), "|", r[0].get("address"), "|", r[0].get("maps_url"))
        except Exception as e:
            print(f"{q} -> ERROR: {e}")

    # Dump last results of a likely query for deeper inspection
    q = "鼎泰豐 信義"
    r = search_places_by_text(q, limit=5, language="zh-TW", heartbeat_every=0.0, heartbeat_prefix="probe")
    print("\nSample JSON:")
    print(json.dumps(r, ensure_ascii=False, indent=2)[:4000])


if __name__ == "__main__":
    main()

