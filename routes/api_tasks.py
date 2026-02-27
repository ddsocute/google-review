from flask import Blueprint, request, jsonify

from services.task_queue import submit_task, get_task_status, get_task_result


bp = Blueprint("api_tasks", __name__, url_prefix="/api")


def _extract_task_public_fields(task: dict) -> dict:
    """Pick the core public fields we want to expose via API."""
    keys = [
        "task_id",
        "status",
        "progress",
        "message",
        "mode",
        "display_name",
        "canonical_url",
        "cache_key",
        "final_cache_key",
        "error",
    ]
    return {k: task.get(k) for k in keys}


@bp.post("/submit")
def submit():
    """Submit a new analysis task."""
    data = request.get_json(silent=True) or {}
    input_raw = (data.get("input") or "").strip()
    mode = (data.get("mode") or "quick").strip().lower()

    # Validate input
    if not input_raw:
        return jsonify({"error": "input 為必填欄位"}), 400
    if len(input_raw) > 2000:
        return jsonify({"error": "input 長度不可超過 2000 字元"}), 400
    if mode not in {"quick", "deep"}:
        return jsonify({"error": "mode 只允許 quick 或 deep"}), 400

    task = submit_task(input_raw=input_raw, mode=mode)
    return jsonify(_extract_task_public_fields(task)), 200


@bp.post("/submit/refresh")
def submit_refresh():
    """
    Submit a new analysis task that forces re-analysis (bypassing cache).

    用途：
      - 後台或管理者想立刻刷新某間店的分析結果時使用。
    """
    data = request.get_json(silent=True) or {}
    input_raw = (data.get("input") or "").strip()
    mode = (data.get("mode") or "quick").strip().lower()

    # Validate input
    if not input_raw:
        return jsonify({"error": "input 為必填欄位"}), 400
    if len(input_raw) > 2000:
        return jsonify({"error": "input 長度不可超過 2000 字元"}), 400
    if mode not in {"quick", "deep"}:
        return jsonify({"error": "mode 只允許 quick 或 deep"}), 400

    task = submit_task(input_raw=input_raw, mode=mode, force_refresh=True)
    return jsonify(_extract_task_public_fields(task)), 200


@bp.get("/task/<task_id>")
def get_task(task_id: str):
    """Get latest status of a task."""
    st = get_task_status(task_id)
    if st is None:
        return jsonify({"error": "任務不存在或已過期"}), 404
    return jsonify(st), 200


@bp.get("/task/<task_id>/result")
def get_task_result_route(task_id: str):
    """Get final analysis result of a completed task."""
    st = get_task_status(task_id)
    if st is None:
        return jsonify({"error": "任務不存在或已過期"}), 404

    status = st.get("status")
    if status != "done":
        return (
            jsonify(
                {
                    "error": "任務尚未完成",
                    "status": status,
                    "progress": st.get("progress"),
                    "message": st.get("message"),
                }
            ),
            409,
        )

    res = get_task_result(task_id)
    if res is None:
        # By this point the task is marked as done but cache has no result;
        # treat as server-side inconsistency.
        return (
            jsonify(
                {
                    "error": "任務已標記為完成，但找不到結果快取，請稍後重試或重新提交任務",
                    "task_id": task_id,
                }
            ),
            500,
        )

    # Directly return the result dict (no extra wrapping)
    return jsonify(res), 200

