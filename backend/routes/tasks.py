"""
backend/routes/tasks.py - Persistent task inspection, recording streaming, artifacts, and retry endpoints.
"""
from flask import Blueprint

tasks_bp = Blueprint("tasks_bp", __name__)


@tasks_bp.route("/api/tasks", methods=["GET"])
def list_tasks_api():
    import app
    return app.list_tasks_api()


@tasks_bp.route("/api/tasks/<int:task_id>", methods=["GET"])
def get_task_detail_api(task_id):
    import app
    return app.get_task_detail_api(task_id)


@tasks_bp.route("/api/tasks/<int:task_id>/export", methods=["GET"])
def export_task_api(task_id):
    import app
    return app.export_task_api(task_id)


@tasks_bp.route("/api/tasks/<int:task_id>/failure-screenshot", methods=["GET"])
def serve_task_failure_screenshot_api(task_id):
    import app
    return app.serve_task_failure_screenshot_api(task_id)


@tasks_bp.route("/api/tasks/<int:task_id>/recording", methods=["GET"])
def get_task_recording_api(task_id):
    import app
    return app.get_task_recording_api(task_id)


@tasks_bp.route("/api/tasks/<int:task_id>/recording/segments/<int:segment_index>", methods=["GET"])
def get_task_recording_segment_api(task_id, segment_index):
    import app
    return app.get_task_recording_segment_api(task_id, segment_index)


@tasks_bp.route("/api/recordings/cleanup-expired", methods=["POST"])
def cleanup_expired_recordings_api():
    import app
    return app.cleanup_expired_recordings_api()


@tasks_bp.route("/api/tasks/<int:task_id>/retry", methods=["POST"])
def retry_task_api(task_id):
    import app
    return app.retry_task_api(task_id)
