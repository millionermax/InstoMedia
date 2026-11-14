"""
backend/routes/imports_exports.py - Excel, clone APK installation, and secret backup/restore endpoints.
"""
from flask import Blueprint, request, jsonify, Response
from backend.storage import db
from backend.storage import vault
from backend.services import excel_manager

imports_exports_bp = Blueprint("imports_exports_bp", __name__)


@imports_exports_bp.route("/api/clones", methods=["GET"])
def list_clones():
    import app
    return app.list_clones()


@imports_exports_bp.route("/api/clones/scan", methods=["POST"])
def scan_clones_api():
    import app
    return app.scan_clones_api()


@imports_exports_bp.route("/api/clones/install-apk", methods=["POST"])
def install_apk_api():
    import app
    return app.install_apk_api()


@imports_exports_bp.route("/api/clones/<int:clone_id>/confirm", methods=["POST"])
def confirm_clone_api(clone_id):
    import app
    return app.confirm_clone_api(clone_id)


@imports_exports_bp.route("/api/clones/<int:clone_id>", methods=["DELETE"])
def delete_clone_api(clone_id):
    import app
    return app.delete_clone_api(clone_id)


@imports_exports_bp.route("/api/excel/template", methods=["GET"])
def download_excel_template():
    data = excel_manager.generate_sample_template()
    return Response(
        data,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=instoper_accounts_template.xlsx"}
    )


@imports_exports_bp.route("/api/excel/export", methods=["GET"])
def export_excel_api():
    data = excel_manager.export_accounts_to_excel()
    return Response(
        data,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=instoper_accounts.xlsx"}
    )


@imports_exports_bp.route("/api/excel/preview", methods=["POST"])
def preview_excel_import():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded."}), 400
    file = request.files["file"]
    if not file.filename.endswith((".xlsx", ".xls")):
        return jsonify({"error": "Only .xlsx files are supported."}), 400

    content = file.read()
    create_missing = request.form.get("create_missing_taxonomy", "").lower() in ("1", "true", "yes")
    result = excel_manager.parse_and_validate_excel(content, create_missing_taxonomy=create_missing)
    return jsonify(result)


@imports_exports_bp.route("/api/excel/import", methods=["POST"])
def execute_excel_import():
    data = request.json or {}
    to_create = data.get("to_create", [])
    to_update = data.get("to_update", [])
    create_missing = bool(data.get("create_missing_taxonomy", False))
    result = excel_manager.execute_import(to_create, to_update, create_missing_taxonomy=create_missing)
    return jsonify({"status": "ok", "result": result})


@imports_exports_bp.route("/api/accounts/parse-file", methods=["POST"])
def parse_account_file_api():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded."}), 400

    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "No file selected."}), 400

    content = file.read()
    result = excel_manager.parse_file_headers_and_rows(content, file.filename)
    if not result.get("success"):
        return jsonify(result), 400
    return jsonify(result)


@imports_exports_bp.route("/api/accounts/import-mapped", methods=["POST"])
def import_mapped_accounts_api():
    data = request.json or {}
    import_token = data.get("import_token")
    raw_rows = data.get("raw_rows")
    mapping = data.get("mapping", {})
    default_device = data.get("default_device") or data.get("default_device_serial")
    default_package = data.get("default_package") or data.get("default_package_name")
    create_missing = bool(data.get("create_missing_taxonomy", False))

    if import_token:
        session = excel_manager.pop_import_session(import_token)
        if not session:
            return jsonify({"error": "Import session has expired or is invalid. Please re-upload your spreadsheet."}), 400
        raw_rows = session.get("raw_rows", [])

    if not raw_rows:
        return jsonify({"error": "No data rows to import."}), 400
    if not mapping.get("username"):
        return jsonify({"error": "Username column must be mapped."}), 400

    result = excel_manager.import_accounts_with_mapping(
        raw_rows=raw_rows,
        column_mapping=mapping,
        default_device=default_device,
        default_package=default_package,
        create_missing_taxonomy=create_missing,
    )
    return jsonify(result)


@imports_exports_bp.route("/api/secrets/export", methods=["POST"])
def export_secrets_api():
    data = request.json or {}
    passphrase = data.get("passphrase", "").strip()
    if not passphrase or len(passphrase) < 4:
        return jsonify({"error": "Passphrase must be at least 4 characters long."}), 400

    accounts = db.get_accounts()
    backup_data = {}
    for acc in accounts:
        sec = db.get_account_secrets(acc["id"])
        if any(sec.values()):
            backup_data[acc["username"]] = {
                "account_id": acc["id"],
                "username": acc["username"],
                "password": sec.get("password"),
                "totp_seed": sec.get("totp_seed"),
                "proxy_secret": sec.get("proxy_secret"),
            }

    try:
        package_bytes = vault.export_secrets_package(backup_data, passphrase)
        return Response(
            package_bytes,
            mimetype="application/json",
            headers={"Content-Disposition": "attachment; filename=instoper_secrets_backup.enc.json"}
        )
    except Exception as e:
        return jsonify({"error": f"Failed to export secrets: {e}"}), 500


@imports_exports_bp.route("/api/secrets/import", methods=["POST"])
def import_secrets_api():
    if "file" not in request.files:
        return jsonify({"error": "No encrypted backup file uploaded."}), 400
    passphrase = request.form.get("passphrase", "").strip()
    if not passphrase:
        return jsonify({"error": "Passphrase is required to decrypt backup."}), 400

    file = request.files["file"]
    file_bytes = file.read()

    try:
        secrets_dict = vault.import_secrets_package(file_bytes, passphrase)
        restored_count = 0
        for username, creds in secrets_dict.items():
            acc = db.get_account_by_username(username)
            if not acc and "account_id" in creds:
                acc = db.get_account_by_id(creds["account_id"])

            if acc:
                db.save_account_secrets(
                    account_id=acc["id"],
                    password=creds.get("password"),
                    totp_seed=creds.get("totp_seed"),
                    proxy_secret=creds.get("proxy_secret"),
                )
                restored_count += 1

        return jsonify({
            "status": "ok",
            "message": f"Successfully restored secrets for {restored_count} account(s)."
        })
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Import failed: {e}"}), 500
