import datetime
import os
import re


def mask(value: str) -> str:
    v = (value or "").strip()
    if len(v) <= 14:
        return "(too-short)"
    return f"{v[:10]}...{v[-4:]}"


def main() -> None:
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env_path = os.path.join(project_root, ".env")

    print(f"[debug_env] env_path={env_path}")
    if not os.path.exists(env_path):
        print("[debug_env] ERROR: .env not found")
        raise SystemExit(2)

    st = os.stat(env_path)
    mtime = datetime.datetime.fromtimestamp(st.st_mtime)
    print(f"[debug_env] size={st.st_size} mtime={mtime}")

    pat = re.compile(r"^\s*(APIFY_TOKEN|APIFY_API_TOKEN)\s*=\s*(.+?)\s*$")
    found = False
    with open(env_path, "r", encoding="utf-8-sig", errors="replace") as f:
        for i, line in enumerate(f.read().splitlines(), 1):
            m = pat.match(line)
            if not m:
                continue
            found = True
            key = m.group(1)
            raw_val = m.group(2).strip().strip('"').strip("'")
            print(f"[debug_env] line={i} key={key} masked={mask(raw_val)}")

    if not found:
        print("[debug_env] No APIFY_TOKEN / APIFY_API_TOKEN lines found in .env")


if __name__ == "__main__":
    main()

