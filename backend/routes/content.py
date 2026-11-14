"""
backend/routes/content.py - Content Library management, file uploads, and media streaming endpoints.
"""
import os
import json
import time
from flask import Blueprint, request, jsonify, send_from_directory, current_app
from werkzeug.utils import secure_filename
from backend.storage import db

content_bp = Blueprint("content_bp", __name__)


def _get_content_upload_dir() -> str:
    import app
    return app._get_content_upload_dir()


def _resolve_content_filepath(storage_key: str):
    import app
    return app._resolve_content_filepath(storage_key)


def _prepare_content_upload_meta(raw_filename: str):
    import app
    return app._prepare_content_upload_meta(raw_filename)


def _serialize_content_item(item: dict) -> dict:
    serialized = dict(item)
    serialized["file_url"] = f"/api/content/{item['id']}/file"
    return serialized


def _parse_upload_content_taxonomy():
    raw_cat = request.form.get("category_id")
    category_id = None
    if raw_cat not in (None, ""):
        try:
            category_id = int(raw_cat)
        except (TypeError, ValueError):
            raise ValueError("Invalid category_id.")

    raw_tag_list = request.form.getlist("tag_ids")
    tag_ids = []
    for entry in raw_tag_list:
        s = (entry or "").strip()
        if not s:
            continue
        if s.startswith("["):
            parsed = json.loads(s)
            if not isinstance(parsed, list):
                raise ValueError("tag_ids must be a list.")
            for val in parsed:
                if isinstance(val, bool):
                    raise ValueError("Invalid tag ID in tag_ids.")
                tag_ids.append(int(val))
        else:
            for part in s.split(","):
                part = part.strip()
                if part:
                    tag_ids.append(int(part))
    return category_id, list(dict.fromkeys(tag_ids))


@content_bp.route("/api/upload_video", methods=["POST"])
def upload_video():
    import app
    if "video" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files["video"]
    if file.filename == "":
        return jsonify({"error": "No selected file"}), 400

    if file and app.allowed_file(file.filename):
        filename = secure_filename(file.filename)
        unique_name = f"{int(time.time())}_{filename}"
        save_path = os.path.join(current_app.config["UPLOAD_FOLDER"], unique_name)
        file.save(save_path)
        return jsonify({
            "status": "ok",
            "filename": unique_name,
            "url": f"/uploads/{unique_name}",
            "size_mb": round(os.path.getsize(save_path) / (1024 * 1024), 2),
        })

    return jsonify({"error": "File type not supported. Use MP4/MOV."}), 400


@content_bp.route("/uploads/<filename>")
def serve_upload(filename):
    return send_from_directory(current_app.config["UPLOAD_FOLDER"], filename)


@content_bp.route("/api/content", methods=["GET", "POST"])
def content_collection_api():
    if request.method == "GET":
        search = (request.args.get("search") or "").strip() or None
        media_type = (request.args.get("media_type") or "").strip().lower() or None
        if media_type == "all":
            media_type = None
        if media_type and media_type not in ("video", "image"):
            return jsonify({"error": "media_type must be 'video' or 'image'."}), 400

        raw_cat = (request.args.get("category_id") or "").strip()
        raw_tag = (request.args.get("tag_id") or "").strip()
        try:
            category_id = int(raw_cat) if raw_cat else None
            tag_id = int(raw_tag) if raw_tag else None
            page = int(request.args.get("page", 1))
            per_page = int(request.args.get("per_page", 50))
        except (TypeError, ValueError):
            return jsonify({"error": "Invalid filter or pagination parameter."}), 400

        result = db.list_content_items(
            search=search,
            media_type=media_type,
            category_id=category_id,
            tag_id=tag_id,
            page=page,
            per_page=per_page,
        )
        result["items"] = [_serialize_content_item(it) for it in result["items"]]
        return jsonify(result)

    # POST: Single or multi-file multipart upload
    uploaded_files = request.files.getlist("files") or request.files.getlist("file")
    uploaded_files = [f for f in uploaded_files if f and (f.filename or "").strip()]
    if not uploaded_files:
        return jsonify({"error": "No files provided for upload."}), 400

    try:
        category_id, tag_ids = _parse_upload_content_taxonomy()
        with db.get_connection() as conn:
            db._validate_and_resolve_taxonomy_tx(
                conn.cursor(), "content", category_id, tag_ids
            )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    content_dir = _get_content_upload_dir()
    uploaded_items = []
    errors = []
    results = []

    for f in uploaded_files:
        raw_name = f.filename or ""
        try:
            display_name, storage_key, media_type, mime_type = _prepare_content_upload_meta(raw_name)
        except ValueError as e:
            err_entry = {"filename": os.path.basename(raw_name) or raw_name, "status": "error", "error": str(e)}
            errors.append(err_entry)
            results.append(err_entry)
            continue

        dest_path = os.path.abspath(os.path.join(content_dir, storage_key))
        if os.path.commonpath([content_dir, dest_path]) != content_dir:
            err_entry = {"filename": display_name, "status": "error", "error": "Invalid destination path."}
            errors.append(err_entry)
            results.append(err_entry)
            continue

        try:
            f.save(dest_path)
            size_bytes = os.path.getsize(dest_path)
            if size_bytes <= 0:
                raise ValueError("Uploaded file is empty.")

            cid = db.create_content_item(
                original_filename=display_name,
                storage_key=storage_key,
                media_type=media_type,
                mime_type=mime_type,
                size_bytes=size_bytes,
                category_id=category_id,
                tag_ids=tag_ids,
            )
            created_item = db.get_content_item(cid)
            serialized = _serialize_content_item(created_item)
            uploaded_items.append(serialized)
            results.append({"filename": display_name, "status": "ok", "item": serialized})
        except Exception as e:
            if os.path.exists(dest_path):
                try:
                    os.remove(dest_path)
                except OSError:
                    pass
            err_entry = {"filename": display_name, "status": "error", "error": str(e)}
            errors.append(err_entry)
            results.append(err_entry)

    payload = {
        "items": uploaded_items,
        "errors": errors,
        "results": results,
        "uploaded_count": len(uploaded_items),
        "failed_count": len(errors),
    }
    if not uploaded_items:
        payload["error"] = errors[0]["error"] if errors else "Upload failed."
        return jsonify(payload), 400
    status_code = 207 if errors else 201
    return jsonify(payload), status_code


@content_bp.route("/api/content/<int:content_id>/file", methods=["GET"])
def serve_content_file_api(content_id):
    item = db.get_content_item(content_id)
    if not item:
        return jsonify({"error": f"Content item #{content_id} not found."}), 404

    filepath = _resolve_content_filepath(item.get("storage_key", ""))
    if not filepath or not os.path.isfile(filepath):
        return jsonify({"error": "Content media file not found."}), 404

    response = send_from_directory(
        _get_content_upload_dir(),
        item["storage_key"],
        mimetype=item.get("mime_type") or "application/octet-stream",
        as_attachment=False,
        conditional=True,
    )
    response.headers["Cache-Control"] = "private, max-age=3600"
    return response


@content_bp.route("/api/content/<int:content_id>", methods=["PATCH", "DELETE"])
def content_item_api(content_id):
    import app
    if request.method == "DELETE":
        try:
            deleted = db.delete_content_item(content_id)
        except db.ResourceInUseError as e:
            return jsonify({"error": str(e), "code": "resource_in_use"}), 409
        if not deleted:
            return jsonify({"error": f"Content item #{content_id} not found."}), 404
        filepath = _resolve_content_filepath(deleted.get("storage_key", ""))
        if filepath and os.path.exists(filepath):
            try:
                os.remove(filepath)
            except OSError:
                pass
        return jsonify({"status": "deleted", "id": content_id})

    data = request.json or {}
    try:
        category_id, tag_ids = app._parse_account_taxonomy_payload(data)
        if category_id is db._UNSET and tag_ids is db._UNSET:
            return jsonify({"error": "No valid taxonomy fields supplied."}), 400
        updated = db.update_content_item_taxonomy(
            content_id,
            category_id=category_id,
            tag_ids=tag_ids,
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    if not updated:
        return jsonify({"error": f"Content item #{content_id} not found."}), 404
    return jsonify(_serialize_content_item(updated))
