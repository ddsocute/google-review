import os
import sys
from typing import Any, Dict, Optional

import requests
from dotenv import load_dotenv


# Ensure project root is on sys.path when running as `python scripts/...`
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


def _mask_token(token: str) -> str:
    token = (token or "").strip()
    if not token:
        return "(empty)"
    if len(token) <= 14:
        return token[:4] + "..."
    return token[:10] + "..." + token[-4:]


def _get_token() -> str:
    return (os.getenv("APIFY_TOKEN") or os.getenv("APIFY_API_TOKEN") or "").strip()

def _read_env_file_token(env_path: str) -> Dict[str, str]:
    """
    Best-effort read `.env` file and extract APIFY_TOKEN / APIFY_API_TOKEN for debugging.
    Returns masked values only (never raw).
    """
    out: Dict[str, str] = {"path": env_path, "exists": "False", "APIFY_TOKEN": "(missing)", "APIFY_API_TOKEN": "(missing)"}
    try:
        if not os.path.exists(env_path):
            return out
        out["exists"] = "True"
        with open(env_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
        for line in lines:
            s = (line or "").strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            k, v = s.split("=", 1)
            k = k.strip()
            v = v.strip().strip("'").strip('"')
            if k in ("APIFY_TOKEN", "APIFY_API_TOKEN"):
                out[k] = _mask_token(v)
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
    return out


def whoami(token: str) -> Dict[str, Any]:
    resp = requests.get(
        "https://api.apify.com/v2/users/me",
        params={"token": token},
        timeout=20,
    )
    data: Any
    try:
        data = resp.json()
    except Exception:
        data = {"_raw": resp.text[:800]}
    return {"status_code": resp.status_code, "json": data}


def _extract_username_plan(payload: Dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
    j = payload.get("json")
    if not isinstance(j, dict):
        return None, None
    d = j.get("data")
    if not isinstance(d, dict):
        return None, None
    username = d.get("username")
    plan = d.get("plan")
    return (str(username) if username else None), (str(plan) if plan else None)


def main() -> None:
    # Load token from project root .env
    # `override=True` so `.env` wins over any stale OS-level APIFY_TOKEN.
    env_path = os.path.join(PROJECT_ROOT, ".env")
    load_dotenv(env_path, override=True)

    # Debug: show whether `.env` exists and what it contains (masked only).
    env_file_info = _read_env_file_token(env_path)
    print(f"[whoami] .env path: {env_file_info.get('path')} exists={env_file_info.get('exists')}")
    print(f"[whoami] .env APIFY_TOKEN (masked): {env_file_info.get('APIFY_TOKEN')}")
    print(f"[whoami] .env APIFY_API_TOKEN (masked): {env_file_info.get('APIFY_API_TOKEN')}")

    token = _get_token()
    print(f"[whoami] APIFY_TOKEN (masked): {_mask_token(token)}")

    if not token:
        print("[whoami] ERROR: APIFY_TOKEN / APIFY_API_TOKEN is empty.")
        print("[whoami] Tip: create .env in project root with `APIFY_TOKEN=...` and restart your shell/app.")
        raise SystemExit(2)

    payload = whoami(token)
    username, plan = _extract_username_plan(payload)
    if username or plan:
        print(f"[whoami] user={username or '(unknown)'} plan={plan or '(unknown)'}")
    else:
        print(f"[whoami] status={payload.get('status_code')} (failed to parse user/plan)")
    # Print minimal error details only when not 200
    if int(payload.get("status_code") or 0) != 200:
        print("[whoami] response snippet:", str(payload.get("json"))[:800])


if __name__ == "__main__":
    main()

