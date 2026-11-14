"""
backend/routes/automations.py - Automation catalog, execution controls, status, and SSE logs.
"""
from flask import Blueprint, jsonify, request, Response
from backend.config import AUTOMATION_CATALOG

automations_bp = Blueprint("automations_bp", __name__)


@automations_bp.route("/api/automations", methods=["GET"])
def list_automations_api():
    """Returns built-in runnable automation definitions (no custom script execution)."""
    return jsonify({"automations": AUTOMATION_CATALOG})


@automations_bp.route("/api/start", methods=["POST"])
def start_workflow():
    import app
    return app.start_workflow()


@automations_bp.route("/api/stop", methods=["POST"])
def stop_workflow():
    import app
    return app.stop_workflow()


@automations_bp.route("/api/status")
def get_status():
    import app
    return app.get_status()


@automations_bp.route("/api/logs")
def stream_logs():
    import app
    return app.stream_logs()
