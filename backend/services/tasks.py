"""
backend/services/tasks.py - Task recording, screenshot, and artifact lifecycle logic.
"""
from typing import Optional, Dict, Any, Tuple
import os
import time
import logging
from backend.storage import db
from automation.observability import screen_recorder

logger = logging.getLogger("instoper.services.tasks")


def get_task_artifacts_dir(upload_folder: str) -> str:
    base = os.path.abspath(upload_folder)
    artifacts_dir = os.path.join(base, "artifacts", "screenshots")
    os.makedirs(artifacts_dir, exist_ok=True)
    return artifacts_dir


def resolve_artifact_filepath(storage_key: str, upload_folder: str) -> Optional[str]:
    if not storage_key or "/" in storage_key or "\\" in storage_key or ".." in storage_key:
        return None
    artifacts_dir = get_task_artifacts_dir(upload_folder)
    candidate = os.path.abspath(os.path.join(artifacts_dir, storage_key))
    try:
        if os.path.commonpath([artifacts_dir, candidate]) != artifacts_dir:
            return None
    except ValueError:
        return None
    if candidate == artifacts_dir:
        return None
    if not os.path.isfile(candidate):
        return None
    return candidate


def get_recordings_artifacts_dir(upload_folder: str) -> str:
    base = os.path.abspath(upload_folder)
    recordings_dir = os.path.join(base, "artifacts", "recordings")
    os.makedirs(recordings_dir, exist_ok=True)
    return recordings_dir


def resolve_recording_filepath(task_id: int, upload_folder: str, filename: Optional[str] = None) -> Optional[str]:
    recordings_base = get_recordings_artifacts_dir(upload_folder)
    task_dir = os.path.abspath(os.path.join(recordings_base, f"task_{task_id}"))
    if not os.path.isdir(task_dir):
        return None

    if not filename:
        candidates = [
            f"task_{task_id}_joined.mp4",
            f"task_{task_id}_recording.mp4",
            f"task_{task_id}_simulated.mp4",
            "seg_0.mp4",
        ]
        for c in candidates:
            p = os.path.join(task_dir, c)
            if os.path.isfile(p):
                return p
        return None

    if "/" in filename or "\\" in filename or ".." in filename:
        return None
    cand = os.path.abspath(os.path.join(task_dir, filename))
    try:
        if os.path.commonpath([task_dir, cand]) != task_dir:
            return None
    except ValueError:
        return None
    return cand if os.path.isfile(cand) else None


def finalize_task_recording(
    recorder: Optional[screen_recorder.TaskScreenRecorder],
    task_id: int,
    terminal_state: str,
    cleanup_ok: bool,
    task_start_time: float,
    db_module=None,
    logger_instance=None,
) -> None:
    """Finalizes task screen recording, updates database, and logs user-visible events."""
    if not recorder:
        return
    _db = db_module or db
    _log = logger_instance or logger
    try:
        rec_res = recorder.stop(terminal_task_state=terminal_state, cleanup_ok=cleanup_ok)
        _db.update_task_recording(
            task_id,
            status=rec_res.get("status", "unavailable"),
            recording_path=rec_res.get("recording_path"),
            duration_seconds=rec_res.get("duration_seconds"),
            segments=rec_res.get("segments"),
            started_at=rec_res.get("started_at"),
            completed_at=rec_res.get("completed_at"),
            error=rec_res.get("error"),
        )
        elapsed_ms = int((time.time() - task_start_time) * 1000)
        st = rec_res.get("status")
        if st == "deleted_success":
            _db.append_task_event(
                task_id,
                "info",
                "Cleaned up task screen recording video after 100% verified success and verified app stop.",
                elapsed_ms=elapsed_ms,
            )
        elif st == "saved":
            dur = rec_res.get("duration_seconds") or 0.0
            _db.append_task_event(
                task_id,
                "info",
                f"Retained device screen recording artifact ({dur}s) for inspection due to task outcome: {terminal_state}.",
                elapsed_ms=elapsed_ms,
            )
        elif st == "capture_failed":
            _db.append_task_event(
                task_id,
                "warning",
                f"Device screen recording was not captured: {rec_res.get('error')}",
                elapsed_ms=elapsed_ms,
            )
        elif st == "delete_failed":
            _db.append_task_event(
                task_id,
                "error",
                f"Failed to delete screen recording file after 100% verified success: {rec_res.get('error')}",
                elapsed_ms=elapsed_ms,
            )
    except Exception as rec_exc:
        _log.warning(f"Failed to finalize recording for task #{task_id}: {rec_exc}")
