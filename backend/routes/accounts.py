"""
backend/routes/accounts.py - Account management, onboarding, taxonomy, and login endpoints.
"""
from flask import Blueprint, request, jsonify

accounts_bp = Blueprint("accounts_bp", __name__)


@accounts_bp.route("/api/accounts", methods=["GET"])
def list_accounts():
    import app
    return app.list_accounts()


@accounts_bp.route("/api/accounts", methods=["POST"])
def create_account_api():
    import app
    return app.create_account_api()


@accounts_bp.route("/api/accounts/<int:acc_id>", methods=["GET"])
def get_account_api(acc_id):
    import app
    return app.get_account_api(acc_id)


@accounts_bp.route("/api/accounts/<int:acc_id>", methods=["PUT"])
def update_account_api(acc_id):
    import app
    return app.update_account_api(acc_id)


@accounts_bp.route("/api/accounts/<int:acc_id>", methods=["DELETE"])
def delete_account_api(acc_id):
    import app
    return app.delete_account_api(acc_id)


@accounts_bp.route("/api/categories", methods=["GET", "POST"])
def categories_collection_api():
    import app
    return app.categories_collection_api()


@accounts_bp.route("/api/categories/<int:cat_id>", methods=["PUT", "DELETE"])
def category_item_api(cat_id):
    import app
    return app.category_item_api(cat_id)


@accounts_bp.route("/api/tags", methods=["GET", "POST"])
def tags_collection_api():
    import app
    return app.tags_collection_api()


@accounts_bp.route("/api/tags/<int:tag_id>", methods=["PUT", "DELETE"])
def tag_item_api(tag_id):
    import app
    return app.tag_item_api(tag_id)


@accounts_bp.route("/api/accounts/<int:acc_id>/credentials", methods=["POST"])
def update_credentials_api(acc_id):
    import app
    return app.update_credentials_api(acc_id)


@accounts_bp.route("/api/accounts/<int:acc_id>/logs", methods=["GET"])
def get_account_logs_api(acc_id):
    import app
    return app.get_account_logs_api(acc_id)


@accounts_bp.route("/api/accounts/onboarding/launch", methods=["POST"])
def onboarding_launch_api():
    import app
    return app.onboarding_launch_api()


@accounts_bp.route("/api/accounts/onboarding/capture", methods=["POST"])
def onboarding_capture_api():
    import app
    return app.onboarding_capture_api()


@accounts_bp.route("/api/accounts/onboarding/close", methods=["POST"])
def onboarding_close_api():
    import app
    return app.onboarding_close_api()


@accounts_bp.route("/api/accounts/onboarding/confirm", methods=["POST"])
def onboarding_confirm_api():
    import app
    return app.onboarding_confirm_api()


@accounts_bp.route("/api/accounts/<int:acc_id>/profile-history", methods=["GET"])
def get_account_profile_history_api(acc_id):
    import app
    return app.get_account_profile_history_api(acc_id)


@accounts_bp.route("/api/accounts/<int:acc_id>/login", methods=["POST"])
def single_account_login_api(acc_id):
    import app
    return app.single_account_login_api(acc_id)


@accounts_bp.route("/api/batch-login/start", methods=["POST"])
def start_batch_login_api():
    import app
    return app.start_batch_login_api()


@accounts_bp.route("/api/batch-login/stop", methods=["POST"])
def stop_batch_login_api():
    import app
    return app.stop_batch_login_api()


@accounts_bp.route("/api/batch-login/status", methods=["GET"])
def get_batch_login_status_api():
    import app
    return app.get_batch_login_status_api()
