import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .cache_store import _get_connection


def init_job_db(db_path: Optional[str] = None) -> None:
    conn = _get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                tag TEXT,
                status TEXT NOT NULL,
                total INTEGER,
                done INTEGER NOT NULL DEFAULT 0,
                failed INTEGER NOT NULL DEFAULT 0,
                skipped INTEGER NOT NULL DEFAULT 0,
                message TEXT,
                started_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_jobs_updated ON jobs(updated_at)")
        conn.commit()
    finally:
        conn.close()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_job(
    *,
    kind: str,
    tag: Optional[str] = None,
    total: Optional[int] = None,
    status: str = "running",
    message: Optional[str] = None,
    db_path: Optional[str] = None,
) -> str:
    init_job_db(db_path)
    job_id = secrets.token_hex(8)
    now = _now_iso()
    conn = _get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO jobs (
                job_id, kind, tag, status, total, done, failed, skipped, message, started_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, 0, 0, 0, ?, ?, ?)
            """,
            (job_id, kind, tag, status, int(total) if total is not None else None, message, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return job_id


def update_job(
    *,
    job_id: str,
    status: Optional[str] = None,
    total: Optional[int] = None,
    done: Optional[int] = None,
    failed: Optional[int] = None,
    skipped: Optional[int] = None,
    message: Optional[str] = None,
    db_path: Optional[str] = None,
) -> None:
    if not job_id:
        return
    now = _now_iso()
    sets: List[str] = ["updated_at = ?"]
    vals: List[Any] = [now]
    if status is not None:
        sets.append("status = ?")
        vals.append(status)
    if total is not None:
        sets.append("total = ?")
        vals.append(int(total))
    if done is not None:
        sets.append("done = ?")
        vals.append(int(done))
    if failed is not None:
        sets.append("failed = ?")
        vals.append(int(failed))
    if skipped is not None:
        sets.append("skipped = ?")
        vals.append(int(skipped))
    if message is not None:
        sets.append("message = ?")
        vals.append(str(message)[:500])

    vals.append(job_id)

    conn = _get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE job_id = ?", tuple(vals))
        conn.commit()
    finally:
        conn.close()


def get_job(job_id: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    if not job_id:
        return None
    conn = _get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,))
        row = cur.fetchone()
        if not row:
            return None
        return {k: row[k] for k in row.keys()}
    finally:
        conn.close()


def list_jobs(limit: int = 20, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    limit = max(1, min(int(limit), 200))
    conn = _get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT * FROM jobs
            ORDER BY datetime(updated_at) DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = cur.fetchall()
        return [{k: row[k] for k in row.keys()} for row in rows]
    finally:
        conn.close()


__all__ = ["init_job_db", "create_job", "update_job", "get_job", "list_jobs"]

