"""
backend/routes/campaigns.py - Campaign CRUD, preflight validation, run execution, and export endpoints.
"""
from flask import Blueprint

campaigns_bp = Blueprint("campaigns_bp", __name__)


@campaigns_bp.route("/api/campaigns", methods=["GET", "POST"])
def campaigns_collection_api():
    import app
    return app.campaigns_collection_api()


@campaigns_bp.route("/api/campaigns/<int:campaign_id>", methods=["GET", "PATCH", "DELETE"])
def campaign_item_api(campaign_id):
    import app
    return app.campaign_item_api(campaign_id)


@campaigns_bp.route("/api/campaigns/<int:campaign_id>/preflight", methods=["POST"])
def campaign_preflight_api(campaign_id):
    import app
    return app.campaign_preflight_api(campaign_id)


@campaigns_bp.route("/api/campaigns/<int:campaign_id>/runs", methods=["POST"])
def launch_campaign_run_api(campaign_id):
    import app
    return app.launch_campaign_run_api(campaign_id)


@campaigns_bp.route("/api/campaign-runs", methods=["GET"])
def list_campaign_runs_api():
    import app
    return app.list_campaign_runs_api()


@campaigns_bp.route("/api/campaign-runs/<int:run_id>", methods=["GET"])
def get_campaign_run_api(run_id):
    import app
    return app.get_campaign_run_api(run_id)


@campaigns_bp.route("/api/campaign-runs/<int:run_id>/export", methods=["GET"])
def export_campaign_run_api(run_id):
    import app
    return app.export_campaign_run_api(run_id)


@campaigns_bp.route("/api/campaign-runs/<int:run_id>/cancel", methods=["POST"])
def cancel_campaign_run_api(run_id):
    import app
    return app.cancel_campaign_run_api(run_id)


@campaigns_bp.route("/api/campaign-runs/<int:run_id>/continue", methods=["POST"])
def continue_campaign_run_api(run_id):
    import app
    return app.continue_campaign_run_api(run_id)
