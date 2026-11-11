import os
import sys
import time
import json
import queue
import io
import tempfile
import threading
import webbrowser
from flask import Flask, render_template, request, Response, jsonify, send_from_directory
from werkzeug.utils import secure_filename
from PIL import Image
import uiautomator2 as u2

from adb import InstagramReelUploader, DEFAULT_AUDIO_URL, DEFAULT_CAPTION
import db
import vault
import package_scanner
import excel_manager
import login_flow

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
ALLOWED_EXTENSIONS = {"mp4", "mov", "mkv", "avi"}

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

app = Flask(__name__)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024  # 500 MB max

# Global State
device_lock = threading.Lock()
connected_device = None
current_frame_bytes = None
screen_stream_running = True

workflow_thread = None
current_uploader = None
workflow_status = {
    "state": "idle",  # idle, running, success, error, cancelled
    "current_step": 0,
    "reel_url": None,
    "error_message": None
}

log_queue = queue.Queue()


def log_listener(level, message, step=None):
    """Pushes structured log to queue for SSE streaming."""
    ts = time.strftime("%H:%M:%S")
    entry = {
        "timestamp": ts,
        "level": level,
        "message": message,
        "step": step
    }
    log_queue.put(entry)
    if step:
        workflow_status["current_step"] = step


def get_u2_device():
    global connected_device
    with device_lock:
        if connected_device is None:
            try:
                connected_device = u2.connect()
            except Exception as e:
                print(f"[Device Connection Error] {e}")
                return None
        return connected_device


def screen_mirror_loop():
    """Background worker continuously capturing screen frames for smooth MJPEG streaming."""
    global current_frame_bytes, screen_stream_running
    print("[Mirror Engine] Started screen capture thread.")
    
    # Placeholder black frame
    placeholder = Image.new("RGB", (360, 800), color=(15, 23, 42))
    p_buf = io.BytesIO()
    placeholder.save(p_buf, format="JPEG", quality=70)
    current_frame_bytes = p_buf.getvalue()

    while screen_stream_running:
        d = get_u2_device()
        if d is not None:
            try:
                # Capture frame using u2 screenshot
                pil_img = d.screenshot()
                # Downscale for ultra-smooth streaming and low latency
                target_w = 360
                target_h = int(target_w * pil_img.height / pil_img.width)
                resized = pil_img.resize((target_w, target_h), Image.Resampling.BILINEAR)

                buf = io.BytesIO()
                resized.save(buf, format="JPEG", quality=65)
                current_frame_bytes = buf.getvalue()
                time.sleep(0.08)  # ~10-12 FPS
                continue
            except Exception:
                time.sleep(0.5)
        else:
            time.sleep(1.0)


# Start Mirroring Daemon Thread
mirror_thread = threading.Thread(target=screen_mirror_loop, daemon=True)
mirror_thread.start()


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/device")
def get_device_info():
    d = get_u2_device()
    if d is None:
        return jsonify({"connected": False, "name": "Disconnected", "serial": None})
    try:
        info = d.info
        name = info.get("productName", "Android Device")
        serial = d.serial
        w = info.get("displayWidth", 720)
        h = info.get("displayHeight", 1600)
        return jsonify({
            "connected": True,
            "name": name,
            "serial": serial,
            "width": w,
            "height": h
        })
    except Exception as e:
        return jsonify({"connected": False, "name": "Error", "error": str(e)})


@app.route("/api/stream")
def screen_stream():
    """MJPEG stream endpoint for real-time video mirroring."""
    def generate_frames():
        while True:
            if current_frame_bytes:
                yield (b"--frame\r\n"
                       b"Content-Type: image/jpeg\r\n\r\n" + current_frame_bytes + b"\r\n")
            time.sleep(0.08)

    return Response(generate_frames(), mimetype="multipart/x-mixed-replace; boundary=frame")


@app.route("/api/tap", methods=["POST"])
def tap_screen():
    """Allows interacting with the phone by clicking the mirror screen."""
    d = get_u2_device()
    if not d:
        return jsonify({"error": "No device connected"}), 400

    data = request.json or {}
    rx = data.get("ratio_x", 0)
    ry = data.get("ratio_y", 0)

    try:
        w = d.info.get("displayWidth", 720)
        h = d.info.get("displayHeight", 1600)
        x = int(rx * w)
        y = int(ry * h)
        d.click(x, y)
        return jsonify({"status": "ok", "clicked": [x, y]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/key", methods=["POST"])
def press_key():
    d = get_u2_device()
    if not d:
        return jsonify({"error": "No device connected"}), 400

    data = request.json or {}
    key = data.get("key")
    if key in ["home", "back", "app_switch"]:
        d.press(key)
        return jsonify({"status": "ok", "key": key})
    return jsonify({"error": "Invalid key"}), 400


@app.route("/api/upload_video", methods=["POST"])
def upload_video():
    if "video" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files["video"]
    if file.filename == "":
        return jsonify({"error": "No selected file"}), 400

    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        # Avoid collisions
        unique_name = f"{int(time.time())}_{filename}"
        save_path = os.path.join(app.config["UPLOAD_FOLDER"], unique_name)
        file.save(save_path)
        return jsonify({
            "status": "ok",
            "filename": unique_name,
            "url": f"/uploads/{unique_name}",
            "size_mb": round(os.path.getsize(save_path) / (1024 * 1024), 2)
        })

    return jsonify({"error": "File type not supported. Use MP4/MOV."}), 400


@app.route("/uploads/<filename>")
def serve_upload(filename):
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)


def run_workflow_async(audio_url, video_path, caption, hide_likes, hide_shares, package_name=None):
    global current_uploader, workflow_status
    workflow_status["state"] = "running"
    workflow_status["current_step"] = 1
    workflow_status["reel_url"] = None
    workflow_status["error_message"] = None

    try:
        current_uploader = InstagramReelUploader(
            package_name=package_name or "com.instagram.android",
            log_callback=log_listener
        )
        reel_url = current_uploader.execute_workflow(
            audio_url=audio_url,
            video_path=video_path,
            caption=caption,
            hide_likes=hide_likes,
            hide_shares=hide_shares
        )
        workflow_status["state"] = "success"
        workflow_status["reel_url"] = reel_url
    except InterruptedError:
        workflow_status["state"] = "cancelled"
    except Exception as e:
        workflow_status["state"] = "error"
        workflow_status["error_message"] = str(e)


@app.route("/api/start", methods=["POST"])
def start_workflow():
    global workflow_thread
    if workflow_status["state"] == "running":
        return jsonify({"error": "A workflow is already running!"}), 400

    data = request.json or {}
    audio_url = data.get("audio_url", "").strip() or DEFAULT_AUDIO_URL
    caption = data.get("caption", "").strip() or DEFAULT_CAPTION
    video_filename = data.get("video_filename")
    hide_likes = data.get("hide_likes", True)
    hide_shares = data.get("hide_shares", True)
    package_name = data.get("package_name")

    video_path = None
    if video_filename:
        video_path = os.path.join(app.config["UPLOAD_FOLDER"], video_filename)
        if not os.path.exists(video_path):
            return jsonify({"error": "Selected video file not found on server"}), 400

    workflow_thread = threading.Thread(
        target=run_workflow_async,
        args=(audio_url, video_path, caption, hide_likes, hide_shares, package_name),
        daemon=True
    )
    workflow_thread.start()

    return jsonify({"status": "started"})


@app.route("/api/stop", methods=["POST"])
def stop_workflow():
    global current_uploader
    if current_uploader:
        current_uploader.is_cancelled = True
        return jsonify({"status": "cancelled"})
    return jsonify({"status": "not_running"})


@app.route("/api/status")
def get_status():
    return jsonify(workflow_status)


@app.route("/api/logs")
def stream_logs():
    """SSE endpoint for streaming real-time log messages to GUI."""
    def event_stream():
        # First send any initial status
        initial_msg = json.dumps({"timestamp": time.strftime("%H:%M:%S"), "level": "info", "message": "Connected to real-time log stream."})
        yield f"data: {initial_msg}\n\n"
        while True:
            try:
                entry = log_queue.get(timeout=15.0)
                yield f"data: {json.dumps(entry)}\n\n"
            except queue.Empty:
                # Keep-alive heartbeat
                yield ": keepalive\n\n"

    return Response(event_stream(), mimetype="text/event-stream")


# ---------------------------------------------------------------------------
# Account Management Endpoints
# ---------------------------------------------------------------------------
batch_login_manager = login_flow.BatchLoginManager(log_callback=log_listener)


@app.route("/api/accounts", methods=["GET"])
def list_accounts():
    status = request.args.get("status")
    accounts = db.get_accounts(status=status)
    return jsonify({"status": "ok", "accounts": accounts})


@app.route("/api/accounts", methods=["POST"])
def create_account_api():
    data = request.json or {}
    username = data.get("username", "").strip()
    if not username:
        return jsonify({"error": "Username is required."}), 400

    try:
        account_id = db.create_account(
            username=username,
            email=data.get("email"),
            device_serial=data.get("device_serial"),
            package_name=data.get("package_name"),
            proxy_endpoint=data.get("proxy_endpoint"),
            proxy_label=data.get("proxy_label"),
            tag=data.get("tag"),
            notes=data.get("notes"),
            status=data.get("status", "active"),
            password=data.get("password"),
            totp_seed=data.get("totp_seed"),
            proxy_secret=data.get("proxy_secret"),
        )
        return jsonify({"status": "ok", "account_id": account_id}), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Failed to create account: {e}"}), 500


@app.route("/api/accounts/<int:acc_id>", methods=["GET"])
def get_account_api(acc_id):
    account = db.get_account_by_id(acc_id)
    if not account:
        return jsonify({"error": "Account not found."}), 404
    return jsonify({"status": "ok", "account": account})


@app.route("/api/accounts/<int:acc_id>", methods=["PUT"])
def update_account_api(acc_id):
    data = request.json or {}
    try:
        updated = db.update_account(
            account_id=acc_id,
            username=data.get("username"),
            email=data.get("email"),
            device_serial=data.get("device_serial"),
            package_name=data.get("package_name"),
            proxy_endpoint=data.get("proxy_endpoint"),
            proxy_label=data.get("proxy_label"),
            tag=data.get("tag"),
            notes=data.get("notes"),
            status=data.get("status"),
            login_status=data.get("login_status"),
        )
        if not updated:
            return jsonify({"error": "Account not found."}), 404

        # Update secrets if provided
        if any(k in data for k in ("password", "totp_seed", "proxy_secret")):
            db.save_account_secrets(
                account_id=acc_id,
                password=data.get("password"),
                totp_seed=data.get("totp_seed"),
                proxy_secret=data.get("proxy_secret"),
            )

        return jsonify({"status": "ok"})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Update failed: {e}"}), 500


@app.route("/api/accounts/<int:acc_id>", methods=["DELETE"])
def delete_account_api(acc_id):
    deleted = db.delete_account(acc_id)
    if not deleted:
        return jsonify({"error": "Account not found."}), 404
    return jsonify({"status": "ok"})


@app.route("/api/accounts/<int:acc_id>/credentials", methods=["POST"])
def update_credentials_api(acc_id):
    data = request.json or {}
    db.save_account_secrets(
        account_id=acc_id,
        password=data.get("password"),
        totp_seed=data.get("totp_seed"),
        proxy_secret=data.get("proxy_secret"),
    )
    return jsonify({"status": "ok", "message": "Credentials updated in protected vault."})


@app.route("/api/accounts/<int:acc_id>/logs", methods=["GET"])
def get_account_logs_api(acc_id):
    logs = db.get_login_logs(account_id=acc_id)
    return jsonify({"status": "ok", "logs": logs})


# ---------------------------------------------------------------------------
# Clone Package Management Endpoints
# ---------------------------------------------------------------------------
@app.route("/api/clones", methods=["GET"])
def list_clones():
    serial = request.args.get("device_serial")
    clones = db.get_clones(device_serial=serial)
    return jsonify({"status": "ok", "clones": clones})


@app.route("/api/clones/scan", methods=["POST"])
def scan_clones_api():
    data = request.json or {}
    serial = data.get("device_serial")
    try:
        candidates = package_scanner.scan_packages(device_serial=serial)
        return jsonify({"status": "ok", "candidates": candidates})
    except Exception as e:
        return jsonify({"error": f"Scan failed: {e}"}), 500


MAX_APK_SIZE = 250 * 1024 * 1024  # 250 MB max APK size


@app.route("/api/clones/install-apk", methods=["POST"])
def install_apk_api():
    """
    Accepts an operator-provided .apk file from the PC, validates extension and size,
    installs it onto the target device via ADB, immediately rescans package inventory,
    and guarantees cleanup of temporary files.
    """
    if "apk_file" not in request.files:
        return jsonify({"error": "No APK file uploaded."}), 400

    file = request.files["apk_file"]
    device_serial = request.form.get("device_serial") or None

    if not file.filename:
        return jsonify({"error": "No file selected."}), 400

    fn_lower = file.filename.lower()
    if not fn_lower.endswith(".apk"):
        return jsonify({"error": "Invalid file format. Only .apk files are supported."}), 400

    temp_dir = os.path.join(UPLOAD_FOLDER, "temp_apk")
    os.makedirs(temp_dir, exist_ok=True)
    temp_fd, temp_path = tempfile.mkstemp(suffix=".apk", dir=temp_dir)
    os.close(temp_fd)

    try:
        size = 0
        with open(temp_path, "wb") as f:
            while chunk := file.read(65536):
                size += len(chunk)
                if size > MAX_APK_SIZE:
                    return jsonify({"error": f"APK file exceeds maximum allowed size of {MAX_APK_SIZE // (1024*1024)}MB."}), 400
                f.write(chunk)

        install_res = package_scanner.install_apk_on_device(temp_path, device_serial=device_serial)

        candidates = []
        if install_res.get("success"):
            target_serial = install_res.get("device_serial") or device_serial
            candidates = package_scanner.scan_packages(device_serial=target_serial)

        status_code = 200 if install_res.get("success") else 400
        return jsonify({
            "success": bool(install_res.get("success")),
            "status": install_res.get("status"),
            "message": install_res.get("message"),
            "device_serial": install_res.get("device_serial"),
            "package_name": install_res.get("new_package"),
            "result": install_res,
            "candidates": candidates,
        }), status_code

    finally:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass


@app.route("/api/clones/<int:clone_id>/confirm", methods=["POST"])
def confirm_clone_api(clone_id):
    data = request.json or {}
    confirmed = bool(data.get("is_confirmed", True))
    success = db.set_clone_confirmed(clone_id, is_confirmed=confirmed)
    if not success:
        return jsonify({"error": "Clone entry not found."}), 404
    return jsonify({"status": "ok", "is_confirmed": confirmed})


@app.route("/api/clones/<int:clone_id>", methods=["DELETE"])
def delete_clone_api(clone_id):
    success = db.delete_clone(clone_id)
    if not success:
        return jsonify({"error": "Clone entry not found."}), 404
    return jsonify({"status": "ok"})


# ---------------------------------------------------------------------------
# Excel Metadata Import / Export Endpoints
# ---------------------------------------------------------------------------
@app.route("/api/excel/template", methods=["GET"])
def download_excel_template():
    data = excel_manager.generate_sample_template()
    return Response(
        data,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=instoper_accounts_template.xlsx"}
    )


@app.route("/api/excel/export", methods=["GET"])
def export_excel_api():
    data = excel_manager.export_accounts_to_excel()
    return Response(
        data,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=instoper_accounts.xlsx"}
    )


@app.route("/api/excel/preview", methods=["POST"])
def preview_excel_import():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded."}), 400
    file = request.files["file"]
    if not file.filename.endswith((".xlsx", ".xls")):
        return jsonify({"error": "Only .xlsx files are supported."}), 400

    content = file.read()
    result = excel_manager.parse_and_validate_excel(content)
    return jsonify(result)


@app.route("/api/excel/import", methods=["POST"])
def execute_excel_import():
    data = request.json or {}
    to_create = data.get("to_create", [])
    to_update = data.get("to_update", [])
    result = excel_manager.execute_import(to_create, to_update)
    return jsonify({"status": "ok", "result": result})


@app.route("/api/accounts/parse-file", methods=["POST"])
def parse_account_file_api():
    """
    Parses any uploaded spreadsheet (XLSX, CSV, TSV) and returns headers,
    sample rows, and auto-detected column mappings.
    """
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


@app.route("/api/accounts/import-mapped", methods=["POST"])
def import_mapped_accounts_api():
    """
    Imports accounts according to the user's custom column mapping.
    Retrieves rows via ephemeral server-side import_token (or raw_rows for direct API calls).
    Saves metadata to database and securely encrypts credentials via DPAPI.
    """
    data = request.json or {}
    import_token = data.get("import_token")
    raw_rows = data.get("raw_rows")
    mapping = data.get("mapping", {})
    default_device = data.get("default_device")
    default_package = data.get("default_package")

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
    )
    return jsonify(result)


# ---------------------------------------------------------------------------
# Passphrase-Encrypted Secret Backup / Restore
# ---------------------------------------------------------------------------
@app.route("/api/secrets/export", methods=["POST"])
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


@app.route("/api/secrets/import", methods=["POST"])
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


# ---------------------------------------------------------------------------
# Account Login & Batch Execution Endpoints
# ---------------------------------------------------------------------------
@app.route("/api/accounts/<int:acc_id>/login", methods=["POST"])
def single_account_login_api(acc_id):
    if batch_login_manager.state == "running":
        return jsonify({"error": "A batch login operation is currently running."}), 400

    def run_single():
        service = login_flow.InstagramAccountLoginService(log_callback=log_listener)
        service.login_account(acc_id)

    threading.Thread(target=run_single, daemon=True).start()
    return jsonify({"status": "started", "message": f"Login triggered for account #{acc_id}"})


@app.route("/api/batch-login/start", methods=["POST"])
def start_batch_login_api():
    data = request.json or {}
    account_ids = data.get("account_ids")
    device_serial = data.get("device_serial")

    started = batch_login_manager.start(account_ids=account_ids, device_serial=device_serial)
    if not started:
        return jsonify({"error": "Could not start batch. A batch may already be running or no accounts selected."}), 400
    return jsonify({"status": "started"})


@app.route("/api/batch-login/stop", methods=["POST"])
def stop_batch_login_api():
    batch_login_manager.stop()
    return jsonify({"status": "stopped"})


@app.route("/api/batch-login/status", methods=["GET"])
def get_batch_login_status_api():
    return jsonify(batch_login_manager.get_status())


if __name__ == "__main__":
    port = 5000
    print(f"\n=======================================================")
    print(f" Instagram Post Automation GUI")
    print(f" Web Dashboard URL: http://127.0.0.1:{port}")
    print(f"=======================================================\n")
    # Auto-open browser after 1 second
    threading.Timer(1.2, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
