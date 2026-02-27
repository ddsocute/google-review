import json

import requests


def main():
    """Simple local test script for /api/search_places and /api/map_search."""
    base = "http://127.0.0.1:5000"

    print("=== POST /api/search_places ===")
    try:
        body = {"query": "鼎泰豐", "limit": 3}
        resp = requests.post(f"{base}/api/search_places", json=body, timeout=30)
        print("status:", resp.status_code)
        try:
            print("json:", json.dumps(resp.json(), ensure_ascii=False, indent=2))
        except Exception:
            print("text:", resp.text[:500])
    except Exception as e:
        print("ERROR calling /api/search_places:", repr(e))

    print("\n=== POST /api/map_search ===")
    try:
        body = {"query": "台北市 餐廳", "limit": 5}
        resp = requests.post(f"{base}/api/map_search", json=body, timeout=30)
        print("status:", resp.status_code)
        try:
            print("json:", json.dumps(resp.json(), ensure_ascii=False, indent=2))
        except Exception:
            print("text:", resp.text[:500])
    except Exception as e:
        print("ERROR calling /api/map_search:", repr(e))


if __name__ == "__main__":
    main()

