import os
import sys
import time
import logging
from datetime import datetime, timezone

logger = logging.getLogger("instoper.app")
import json
import queue
import io
import csv
import tempfile
import threading
import webbrowser
from flask import Flask, render_template, request, Response, jsonify, send_from_directory, send_file
from werkzeug.utils import secure_filename
from PIL import Image
import uiautomator2 as u2

import adb
from adb import InstagramReelUploader, DEFAULT_AUDIO_URL, DEFAULT_CAPTION
import db
import vault
import package_scanner
import excel_manager
import login_flow
import profile_capture
import screen_recorder
from backend.services import (
    accounts as accounts_service,
    campaigns as campaigns_service,
    tasks as tasks_service,
)
from backend.routes import (
    dashboard_bp,
    devices_bp,
    content_bp,
    accounts_bp,
    automations_bp,
    campaigns_bp,
    tasks_bp,
    imports_exports_bp,
)

import mimetypes
import uuid
from urllib.parse import urlparse
from werkzeug.utils import secure_filename

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
ALLOWED_EXTENSIONS = {"mp4", "mov", "mkv", "avi"}
CONTENT_VIDEO_EXTENSIONS = {"mp4", "mov", "mkv", "avi"}
CONTENT_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}
CONTENT_ALLOWED_EXTENSIONS = CONTENT_VIDEO_EXTENSIONS | CONTENT_IMAGE_EXTENSIONS
_CONTENT_MIME_MAP = {
    "mp4": "video/mp4",
    "mov": "video/quicktime",
    "mkv": "video/x-matroska",
    "avi": "video/x-msvideo",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
}

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(os.path.join(UPLOAD_FOLDER, "content"), exist_ok=True)

_frontend_templates = os.path.join(BASE_DIR, "frontend", "templates")
_root_templates = os.path.join(BASE_DIR, "templates")
_template_folder = _frontend_templates if os.path.isdir(_frontend_templates) else _root_templates

app = Flask(__name__, template_folder=_template_folder)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024  # 500 MB max

for _bp in (
    dashboard_bp,
    devices_bp,
    content_bp,
    accounts_bp,
    automations_bp,
    campaigns_bp,
    tasks_bp,
    imports_exports_bp,
):
    if _bp.name not in app.blueprints:
        app.register_blueprint(_bp)

db.init_db(recover_in_flight=True)
try:
    screen_recorder.cleanup_expired_recordings(older_than_days=30, db_module=db)
except Exception:
    pass


@app.errorhandler(413)
def handle_request_entity_too_large(_err):
    max_mb = max(1, int(app.config.get("MAX_CONTENT_LENGTH", 500 * 1024 * 1024) / (1024 * 1024)))
    if request.path.startswith("/api/"):
        return jsonify({"error": f"Upload exceeds maximum request limit ({max_mb} MB)."}), 413
    return f"Upload exceeds maximum request limit ({max_mb} MB).", 413


def _get_content_upload_dir() -> str:
    base = os.path.abspath(app.config["UPLOAD_FOLDER"])
    content_dir = os.path.join(base, "content")
    os.makedirs(content_dir, exist_ok=True)
    return content_dir


def _resolve_content_filepath(storage_key: str):
    if not storage_key or "/" in storage_key or "\\" in storage_key or ".." in storage_key:
        return None
    content_dir = _get_content_upload_dir()
    candidate = os.path.abspath(os.path.join(content_dir, storage_key))
    try:
        if os.path.commonpath([content_dir, candidate]) != content_dir:
            return None
    except ValueError:
        return None
    if candidate == content_dir:
        return None
    return candidate


def _get_task_artifacts_dir() -> str:
    base = os.path.abspath(app.config["UPLOAD_FOLDER"])
    artifacts_dir = os.path.join(base, "artifacts", "screenshots")
    os.makedirs(artifacts_dir, exist_ok=True)
    return artifacts_dir


def _resolve_artifact_filepath(storage_key: str):
    if not storage_key or "/" in storage_key or "\\" in storage_key or ".." in storage_key:
        return None
    artifacts_dir = _get_task_artifacts_dir()
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


def _get_recordings_artifacts_dir() -> str:
    base = os.path.abspath(app.config["UPLOAD_FOLDER"])
    recordings_dir = os.path.join(base, "artifacts", "recordings")
    os.makedirs(recordings_dir, exist_ok=True)
    return recordings_dir


def _resolve_recording_filepath(task_id: int, filename: Optional[str] = None) -> Optional[str]:
    recordings_base = _get_recordings_artifacts_dir()
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
        try:
            for f in sorted(os.listdir(task_dir)):
                if f.lower().endswith(".mp4"):
                    return os.path.join(task_dir, f)
        except OSError:
            pass
        return None

    if "/" in filename or "\\" in filename or ".." in filename:
        return None
    candidate = os.path.abspath(os.path.join(task_dir, filename))
    try:
        if os.path.commonpath([task_dir, candidate]) != task_dir:
            return None
    except ValueError:
        return None
    if os.path.isfile(candidate):
        return candidate
    return None


def _capture_task_failure_screenshot(
    task_id: int,
    device_serial: str = None,
    uploader: any = None,
):
    """
    Attempts to capture device screen when a task fails before closing the app.
    Returns (relative_filename, captured_at_iso, error_message).
    Never raises an exception; failures in screenshot capture are recorded without masking original error.
    """
    if not device_serial:
        return None, None, "No device serial bound to task."
    try:
        artifacts_dir = _get_task_artifacts_dir()
        filename = f"failure_task_{task_id}_{int(time.time())}.png"
        target_path = os.path.join(artifacts_dir, filename)

        dev = getattr(uploader, "d", None)
        if dev is None:
            if app.testing or app.config.get("TESTING"):
                return None, None, "Testing mode: device u2 not configured on mock uploader."
            try:
                connected_serials = {
                    d.get("serial")
                    for d in package_scanner.list_connected_devices()
                    if d.get("status") == "device"
                }
            except Exception:
                connected_serials = set()
            if device_serial not in connected_serials:
                return None, None, f"Device '{device_serial}' is offline or not connected."
            dev = u2.connect(device_serial)

        dev.screenshot(target_path)
        if os.path.isfile(target_path) and os.path.getsize(target_path) > 0:
            return filename, db._utcnow(), None
        return None, None, "Screenshot file was not created or was empty."
    except Exception as e:
        return None, None, f"Screenshot capture failed: {e}"


def _prepare_content_upload_meta(raw_filename: str):
    """Validates file extension and builds a safe display name, storage key, type, and MIME."""
    display_name = os.path.basename((raw_filename or "").replace("\\", "/")).strip()
    if not display_name or "." not in display_name:
        raise ValueError("File has no valid extension.")
    ext = display_name.rsplit(".", 1)[1].lower()
    if ext not in CONTENT_ALLOWED_EXTENSIONS:
        allowed_str = ", ".join(sorted(CONTENT_ALLOWED_EXTENSIONS))
        raise ValueError(f"Unsupported file type '.{ext}'. Allowed: {allowed_str}.")

    safe = secure_filename(display_name)
    safe_stem = safe.rsplit(".", 1)[0] if "." in safe else safe
    safe_stem = (safe_stem or "media")[:80]
    safe_name = f"{safe_stem}.{ext}"
    storage_key = f"{uuid.uuid4().hex[:12]}_{safe_name}"
    media_type = "video" if ext in CONTENT_VIDEO_EXTENSIONS else "image"
    mime_type = _CONTENT_MIME_MAP.get(ext) or mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
    return display_name, storage_key, media_type, mime_type

# Global State
device_lock = threading.Lock()
connected_device = None
current_frame_bytes = None
screen_stream_running = True

BUILTIN_AUTOMATION_ID = "reel_upload_with_music"
AUTOMATION_CATALOG = [
    {
        "id": BUILTIN_AUTOMATION_ID,
        "name": "Reel Upload with Music",
        "status": "ready",
        "description": "Runs the 11-step Instagram Reel workflow on one onboarded account using selected audio, video, caption, and privacy settings.",
        "steps": 11,
    }
]

workflow_start_lock = threading.Lock()
workflow_cancel_requested = False
workflow_thread = None
current_uploader = None
workflow_status = {
    "state": "idle",  # idle, running, success, error, cancelled
    "current_step": 0,
    "reel_url": None,
    "error_message": None,
    "automation_id": None,
    "automation_name": None,
    "account_id": None,
    "account_username": None,
    "device_serial": None,
    "package_name": None,
}

campaign_cancel_event = threading.Event()
active_campaign_run_id = None
active_campaign_uploader = None
campaign_worker_thread = None

_CAMPAIGN_STEP_LABELS = {
    1: "Open Audio Link",
    2: "Tap Use Audio",
    3: "Open Gallery",
    4: "Select Video",
    5: "Next Screen",
    6: "Write Caption",
    7: "More Options",
    8: "Hide Likes/Shares",
    9: "Click Share",
    10: "Wait for Upload",
    11: "Copy Reel Link",
}


def _is_any_workflow_active() -> "Optional[str]":
    """Returns a human-readable conflict message if a legacy workflow or campaign run is active."""
    if workflow_status.get("state") == "running":
        return "A single-target Reel or Automation workflow is already running."
    active_run = db.get_active_campaign_run()
    if active_run:
        return f"Campaign Run #{active_run['id']} ('{active_run['campaign_name']}') is already {active_run['status']}."
    return None

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


from backend.routes.devices import list_devices_api, get_device_info


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


def _validate_instagram_audio_url(raw_url: str, require_nonempty: bool = False) -> str:
    return db.validate_instagram_audio_url(raw_url, require_nonempty=require_nonempty)


def _resolve_automation_account_target(account_id_raw, explicit_device_serial=None, explicit_package_name=None):
    return accounts_service.resolve_automation_account_target(
        account_id_raw=account_id_raw,
        explicit_device_serial=explicit_device_serial,
        explicit_package_name=explicit_package_name,
        db_module=db,
        scanner_module=package_scanner,
    )


def run_workflow_async(audio_url, video_path, caption, hide_likes, hide_shares, device_serial=None, package_name=None):
    global current_uploader, workflow_status, workflow_cancel_requested
    workflow_status["state"] = "running"
    workflow_status["current_step"] = 1
    workflow_status["reel_url"] = None
    workflow_status["error_message"] = None

    try:
        if workflow_cancel_requested:
            raise InterruptedError("Workflow cancelled before execution.")
        uploader = InstagramReelUploader(
            device_serial=device_serial,
            package_name=package_name or "com.instagram.android",
            log_callback=log_listener,
        )
        current_uploader = uploader
        if workflow_cancel_requested:
            uploader.is_cancelled = True
            raise InterruptedError("Workflow cancelled by user.")
        reel_url = uploader.execute_workflow(
            audio_url=audio_url,
            video_path=video_path,
            caption=caption,
            hide_likes=hide_likes,
            hide_shares=hide_shares,
        )
        workflow_status["state"] = "success"
        workflow_status["reel_url"] = reel_url
    except InterruptedError:
        workflow_status["state"] = "cancelled"
    except Exception as e:
        workflow_status["state"] = "error"
        workflow_status["error_message"] = str(e)
    finally:
        current_uploader = None
        workflow_cancel_requested = False


@app.route("/api/automations", methods=["GET"])
def list_automations_api():
    """Returns built-in runnable automation definitions (no custom script execution)."""
    return jsonify({"automations": AUTOMATION_CATALOG})


@app.route("/api/start", methods=["POST"])
def start_workflow():
    global workflow_thread, workflow_cancel_requested
    if workflow_status["state"] == "running":
        return jsonify({"error": "An automation or Reel workflow is already running."}), 409

    data = request.json or {}
    automation_id = (data.get("automation_id") or "").strip() or None
    if automation_id and automation_id != BUILTIN_AUTOMATION_ID:
        return jsonify({"error": f"Unknown automation '{automation_id}'."}), 400

    raw_account_id = data.get("account_id")
    require_account = automation_id == BUILTIN_AUTOMATION_ID or (
        raw_account_id is not None and str(raw_account_id).strip() != ""
    )

    device_serial = (data.get("device_serial") or "").strip() or None
    package_name = (data.get("package_name") or "").strip() or None
    account_id = None
    account_username = None

    if require_account:
        try:
            target = _resolve_automation_account_target(
                raw_account_id,
                explicit_device_serial=device_serial,
                explicit_package_name=package_name,
            )
        except LookupError as e:
            return jsonify({"error": str(e)}), 404
        except package_scanner.OnboardingValidationError as e:
            return jsonify({"error": e.message, "code": e.code}), e.http_status
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        account_id = target["account_id"]
        account_username = target["account_username"]
        device_serial = target["device_serial"]
        package_name = target["package_name"]

    try:
        audio_url = _validate_instagram_audio_url(
            data.get("audio_url", ""),
            require_nonempty=(automation_id == BUILTIN_AUTOMATION_ID),
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    caption = data.get("caption", "").strip() or DEFAULT_CAPTION
    video_filename = data.get("video_filename")
    content_id = data.get("content_id")
    hide_likes = bool(data.get("hide_likes", True))
    hide_shares = bool(data.get("hide_shares", True))

    has_content_id = content_id is not None and str(content_id).strip() != ""
    if video_filename and has_content_id:
        return jsonify({"error": "Specify either video_filename or content_id, not both."}), 400

    video_path = None
    if has_content_id:
        if isinstance(content_id, bool):
            return jsonify({"error": "Invalid content_id."}), 400
        try:
            parsed_cid = int(content_id)
        except (TypeError, ValueError):
            return jsonify({"error": "Invalid content_id."}), 400
        content_item = db.get_content_item(parsed_cid)
        if not content_item:
            return jsonify({"error": f"Content item #{parsed_cid} not found."}), 404
        if content_item.get("media_type") != "video":
            return jsonify({"error": "Only video items from Content Library can be published as Reels."}), 400
        video_path = _resolve_content_filepath(content_item.get("storage_key", ""))
        if not video_path or not os.path.isfile(video_path):
            return jsonify({"error": "Selected Content Library video file not found on server"}), 400
    elif video_filename:
        video_path = os.path.join(app.config["UPLOAD_FOLDER"], video_filename)
        if not os.path.exists(video_path):
            return jsonify({"error": "Selected video file not found on server"}), 400

    automation_name = "Reel Upload with Music" if automation_id == BUILTIN_AUTOMATION_ID else "Reel Studio"
    with workflow_start_lock:
        conflict_msg = _is_any_workflow_active()
        if conflict_msg:
            return jsonify({"error": conflict_msg, "code": "workflow_locked"}), 409
        workflow_cancel_requested = False
        workflow_status.update({
            "state": "running",
            "current_step": 1,
            "reel_url": None,
            "error_message": None,
            "automation_id": automation_id,
            "automation_name": automation_name,
            "account_id": account_id,
            "account_username": account_username,
            "device_serial": device_serial,
            "package_name": package_name,
        })
        try:
            workflow_thread = threading.Thread(
                target=run_workflow_async,
                args=(audio_url, video_path, caption, hide_likes, hide_shares, device_serial, package_name),
                daemon=True,
            )
            workflow_thread.start()
        except Exception as e:
            workflow_status["state"] = "error"
            workflow_status["error_message"] = str(e)
            return jsonify({"error": f"Failed to launch workflow thread: {e}"}), 500

    target_note = f" on @{account_username} ({device_serial} / {package_name})" if account_username else ""
    log_listener("info", f"Queued {automation_name}{target_note}.")
    return jsonify({"status": "started", "automation_id": automation_id, "device_serial": device_serial, "package_name": package_name})


@app.route("/api/stop", methods=["POST"])
def stop_workflow():
    global current_uploader, workflow_cancel_requested
    workflow_cancel_requested = True
    if current_uploader:
        current_uploader.is_cancelled = True
        return jsonify({"status": "cancelled"})
    if workflow_status["state"] == "running":
        workflow_status["state"] = "cancelled"
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


def _parse_account_taxonomy_payload(data):
    """Extracts and validates optional category_id and tag_ids from account API JSON."""
    return accounts_service.parse_account_taxonomy_payload(data)


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
        posts_count = db._validate_metric_int(data.get("posts_count"), "posts_count")
        followers_count = db._validate_metric_int(data.get("followers_count"), "followers_count")
        category_id, tag_ids = _parse_account_taxonomy_payload(data)
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
            category_id=category_id,
            tag_ids=tag_ids,
        )
        if posts_count is not None or followers_count is not None:
            db.add_profile_snapshot(
                account_id=account_id,
                profile_handle=username.lstrip("@"),
                posts_count=posts_count,
                followers_count=followers_count,
                source_device_serial=(data.get("device_serial") or "").strip() or "manual",
                source_package_name=(data.get("package_name") or "").strip() or "com.instagram.android",
                capture_source="manual",
            )
        account = db.get_account_by_id(account_id)
        return jsonify({"status": "ok", "account_id": account_id, "account": account}), 201
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
        has_snapshot_fields = ("posts_count" in data) or ("followers_count" in data)
        posts_count = db._validate_metric_int(data.get("posts_count"), "posts_count") if has_snapshot_fields else None
        followers_count = db._validate_metric_int(data.get("followers_count"), "followers_count") if has_snapshot_fields else None
        existing = db.get_account_by_id(acc_id) if has_snapshot_fields else None

        category_id, tag_ids = _parse_account_taxonomy_payload(data)
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
            category_id=category_id,
            tag_ids=tag_ids,
        )
        if not updated:
            return jsonify({"error": "Account not found."}), 404

        if has_snapshot_fields and (posts_count is not None or followers_count is not None):
            latest_snap = (existing or {}).get("latest_snapshot") or {}
            prev_posts = latest_snap.get("posts_count")
            prev_followers = latest_snap.get("followers_count")
            if not latest_snap or posts_count != prev_posts or followers_count != prev_followers:
                current_acc = db.get_account_by_id(acc_id)
                db.add_profile_snapshot(
                    account_id=acc_id,
                    profile_handle=(current_acc.get("username") or "").lstrip("@"),
                    posts_count=posts_count,
                    followers_count=followers_count,
                    source_device_serial=(current_acc.get("device_serial") or "").strip() or "manual",
                    source_package_name=(current_acc.get("package_name") or "").strip() or "com.instagram.android",
                    capture_source="manual",
                )

        # Update secrets if provided
        if any(k in data for k in ("password", "totp_seed", "proxy_secret")):
            db.save_account_secrets(
                account_id=acc_id,
                password=data.get("password"),
                totp_seed=data.get("totp_seed"),
                proxy_secret=data.get("proxy_secret"),
            )

        return jsonify({"status": "ok", "account": db.get_account_by_id(acc_id)})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Update failed: {e}"}), 500


@app.route("/api/accounts/<int:acc_id>", methods=["DELETE"])
def delete_account_api(acc_id):
    try:
        deleted = db.delete_account(acc_id)
    except db.ResourceInUseError as e:
        return jsonify({"error": str(e), "code": "resource_in_use"}), 409
    if not deleted:
        return jsonify({"error": "Account not found."}), 404
    return jsonify({"status": "ok"})


# ---------------------------------------------------------------------------
# Scoped Categories & Tags Endpoints (Accounts / Content)
# ---------------------------------------------------------------------------
def _taxonomy_collection_handler(kind: str):
    list_fn = db.list_categories if kind == "category" else db.list_tags
    create_fn = db.create_category if kind == "category" else db.create_tag
    plural = "categories" if kind == "category" else "tags"

    if request.method == "GET":
        scope = request.args.get("scope", "").strip()
        try:
            items = list_fn(scope)
            return jsonify({"status": "ok", plural: items})
        except ValueError as e:
            return jsonify({"error": str(e)}), 400

    data = request.json or {}
    scope = (data.get("scope") or "").strip()
    name = data.get("name")
    try:
        item_id = create_fn(scope, name)
        created = [item for item in list_fn(scope) if item["id"] == item_id][0]
        return jsonify({"status": "ok", kind: created}), 201
    except db.TaxonomyDuplicateError as e:
        return jsonify({"error": str(e)}), 409
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


def _taxonomy_item_handler(kind: str, item_id: int):
    rename_fn = db.rename_category if kind == "category" else db.rename_tag
    delete_fn = db.delete_category if kind == "category" else db.delete_tag

    if request.method == "PUT":
        data = request.json or {}
        try:
            ok = rename_fn(item_id, data.get("name"))
            if not ok:
                return jsonify({"error": f"{kind.capitalize()} not found."}), 404
            return jsonify({"status": "ok"})
        except db.TaxonomyDuplicateError as e:
            return jsonify({"error": str(e)}), 409
        except ValueError as e:
            return jsonify({"error": str(e)}), 400

    try:
        ok = delete_fn(item_id)
        if not ok:
            return jsonify({"error": f"{kind.capitalize()} not found."}), 404
        return jsonify({"status": "ok"})
    except db.TaxonomyInUseError as e:
        return jsonify({"error": str(e)}), 409


@app.route("/api/categories", methods=["GET", "POST"])
def categories_collection_api():
    return _taxonomy_collection_handler("category")


@app.route("/api/categories/<int:cat_id>", methods=["PUT", "DELETE"])
def category_item_api(cat_id: int):
    return _taxonomy_item_handler("category", cat_id)


@app.route("/api/tags", methods=["GET", "POST"])
def tags_collection_api():
    return _taxonomy_collection_handler("tag")


@app.route("/api/tags/<int:tag_id>", methods=["PUT", "DELETE"])
def tag_item_api(tag_id: int):
    return _taxonomy_item_handler("tag", tag_id)


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
# Device & Account Onboarding Endpoints (Module 2)
# ---------------------------------------------------------------------------
@app.route("/api/accounts/onboarding/launch", methods=["POST"])
def onboarding_launch_api():
    data = request.json or {}
    device_serial = data.get("device_serial")
    package_name = data.get("package_name")
    try:
        res = profile_capture.open_profile_app(device_serial=device_serial, package_name=package_name)
        status_code = 504 if res.get("status") == "launch_timeout" else 200
        return jsonify(res), status_code
    except package_scanner.OnboardingValidationError as e:
        return jsonify({"error": e.message, "code": e.code}), e.http_status
    except Exception as e:
        return jsonify({"error": f"Failed to launch selected package: {e}"}), 500


@app.route("/api/accounts/onboarding/capture", methods=["POST"])
def onboarding_capture_api():
    data = request.json or {}
    device_serial = data.get("device_serial")
    package_name = data.get("package_name")
    raw_acc_id = data.get("account_id")

    expected_username = None
    if raw_acc_id not in (None, ""):
        try:
            acc_id = int(raw_acc_id)
        except (TypeError, ValueError):
            return jsonify({"error": "account_id must be an integer."}), 400
        acc = db.get_account_by_id(acc_id)
        if not acc:
            return jsonify({"error": f"Account #{acc_id} not found."}), 404
        expected_username = acc["username"]

    try:
        res = profile_capture.capture_profile(
            device_serial=device_serial,
            package_name=package_name,
            expected_username=expected_username,
        )
        # Keep payload strictly free of raw UI XML, screenshots, or credentials
        safe_payload = {
            "status": res.get("status"),
            "device_serial": res.get("device_serial"),
            "package_name": res.get("package_name"),
            "profile_handle": res.get("profile_handle"),
            "posts_count": res.get("posts_count"),
            "followers_count": res.get("followers_count"),
            "raw_labels": res.get("raw_labels", []),
            "needs_manual_profile_navigation": bool(res.get("needs_manual_profile_navigation", False)),
            "message": res.get("message", ""),
            "screen_state": res.get("screen_state", res.get("status")),
            "verification_steps": res.get("verification_steps", []),
        }
        return jsonify(safe_payload), 200
    except package_scanner.OnboardingValidationError as e:
        return jsonify({"error": e.message, "code": e.code}), e.http_status
    except Exception:
        return jsonify({"error": "Profile capture failed cleanly without saving any data."}), 500


@app.route("/api/accounts/onboarding/close", methods=["POST"])
def onboarding_close_api():
    data = request.json or {}
    device_serial = data.get("device_serial")
    package_name = data.get("package_name")
    try:
        res = profile_capture.close_profile_app(device_serial=device_serial, package_name=package_name)
        return jsonify(res), 200
    except package_scanner.OnboardingValidationError as e:
        return jsonify({"error": e.message, "code": e.code}), e.http_status
    except Exception as e:
        return jsonify({"error": f"Failed to stop package: {e}"}), 500


@app.route("/api/accounts/onboarding/confirm", methods=["POST"])
def onboarding_confirm_api():
    data = request.json or {}
    device_serial = data.get("device_serial")
    package_name = data.get("package_name")
    profile_handle = data.get("profile_handle")
    raw_acc_id = data.get("account_id")

    account_id = None
    if raw_acc_id not in (None, ""):
        try:
            account_id = int(raw_acc_id)
        except (TypeError, ValueError):
            return jsonify({"error": "account_id must be an integer."}), 400

    try:
        # Revalidate online device + installed & confirmed package on exact serial
        package_scanner.validate_confirmed_package(device_serial=device_serial, package_name=package_name)
        category_id, tag_ids = _parse_account_taxonomy_payload(data)

        result = db.confirm_onboarding_account(
            device_serial=device_serial,
            package_name=package_name,
            profile_handle=profile_handle,
            account_id=account_id,
            posts_count=data.get("posts_count"),
            followers_count=data.get("followers_count"),
            capture_source=data.get("capture_source") or "ui",
            category_id=category_id,
            tag_ids=tag_ids,
            notes=data.get("notes"),
        )
        status_code = 201 if result["created"] else 200
        return jsonify({
            "status": "ok",
            "created": result["created"],
            "account": result["account"],
            "snapshot": result["snapshot"],
        }), status_code
    except package_scanner.OnboardingValidationError as e:
        return jsonify({"error": e.message, "code": e.code}), e.http_status
    except db.OnboardingConflictError as e:
        return jsonify({"error": e.message, "code": e.code, "existing_account": e.existing_account}), 409
    except LookupError as e:
        return jsonify({"error": str(e)}), 404
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Failed to confirm onboarding: {e}"}), 500


@app.route("/api/accounts/<int:acc_id>/profile-history", methods=["GET"])
def get_account_profile_history_api(acc_id):
    acc = db.get_account_by_id(acc_id)
    if not acc:
        return jsonify({"error": "Account not found."}), 404

    raw_limit = request.args.get("limit", "20")
    try:
        limit = max(1, min(int(raw_limit), 100))
    except (TypeError, ValueError):
        return jsonify({"error": "limit must be an integer."}), 400

    snapshots = db.get_profile_snapshots(acc_id, limit=limit)
    return jsonify({"status": "ok", "account_id": acc_id, "snapshots": snapshots})


# ---------------------------------------------------------------------------
# Clone Package Management Endpoints
# ---------------------------------------------------------------------------
@app.route("/api/clones", methods=["GET"])
def list_clones():
    serial = request.args.get("device_serial")
    check_installed = request.args.get("check_installed", "").lower() in ("1", "true", "yes")
    clones = db.get_clones(device_serial=serial)
    if serial and check_installed:
        installed = package_scanner.get_all_installed_packages(serial)
        for clone in clones:
            clone["is_installed"] = clone["package_name"] in installed
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
    create_missing = request.form.get("create_missing_taxonomy", "").lower() in ("1", "true", "yes")
    result = excel_manager.parse_and_validate_excel(content, create_missing_taxonomy=create_missing)
    return jsonify(result)


@app.route("/api/excel/import", methods=["POST"])
def execute_excel_import():
    data = request.json or {}
    to_create = data.get("to_create", [])
    to_update = data.get("to_update", [])
    create_missing = bool(data.get("create_missing_taxonomy", False))
    result = excel_manager.execute_import(to_create, to_update, create_missing_taxonomy=create_missing)
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


# ---------------------------------------------------------------------------
# Content Library Endpoints
# ---------------------------------------------------------------------------
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


@app.route("/api/content", methods=["GET", "POST"])
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
        # Fail fast if supplied category/tag doesn't exist or belongs to 'accounts' scope
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


@app.route("/api/content/<int:content_id>/file", methods=["GET"])
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


@app.route("/api/content/<int:content_id>", methods=["PATCH", "DELETE"])
def content_item_api(content_id):
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
        category_id, tag_ids = _parse_account_taxonomy_payload(data)
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


# ---------------------------------------------------------------------------
# Campaigns & Persistent Tasks Endpoints + Sequential Worker
# ---------------------------------------------------------------------------
def _run_campaign_preflight(campaign: dict, target_subset=None, include_lock_check: bool = True) -> dict:
    """Validates audio URL, workflow lock, and every target's device/package/video readiness."""
    return campaigns_service.run_campaign_preflight(
        campaign=campaign,
        target_subset=target_subset,
        include_lock_check=include_lock_check,
        is_locked_fn=_is_any_workflow_active,
        resolve_filepath_fn=_resolve_content_filepath,
        db_module=db,
        scanner_module=package_scanner,
    )


def _finalize_task_recording(
    recorder: Optional[screen_recorder.TaskScreenRecorder],
    task_id: int,
    terminal_state: str,
    cleanup_ok: bool,
    task_start_time: float,
) -> None:
    tasks_service.finalize_task_recording(
        recorder=recorder,
        task_id=task_id,
        terminal_state=terminal_state,
        cleanup_ok=cleanup_ok,
        task_start_time=task_start_time,
        db_module=db,
        logger_instance=logger,
    )


def _execute_campaign_run_worker(run_id: int) -> None:
    """Sequential campaign worker executing one pending task at a time with verified app close."""
    global active_campaign_run_id, active_campaign_uploader
    active_campaign_run_id = run_id
    campaign_cancel_event.clear()

    try:
        run = db.get_campaign_run(run_id)
        if not run:
            return
        if run["status"] in ("queued", "paused"):
            run = db.transition_run_status(run_id, "running", current_task_id=None, error_message=None)

        cooldown_seconds = int(run.get("cooldown_seconds") or 3)
        failure_policy = run.get("failure_policy") or "pause"
        audio_url = run.get("audio_url") or DEFAULT_AUDIO_URL
        hide_likes = bool(run.get("hide_likes", True))
        hide_shares = bool(run.get("hide_shares", True))

        while True:
            if campaign_cancel_event.is_set() or db.is_run_cancel_requested(run_id):
                db.cancel_pending_tasks_for_run(run_id, reason="Run cancelled before task started.")
                db.transition_run_status(run_id, "cancelled", current_task_id=None)
                return

            task = db.get_next_pending_task(run_id)
            if not task:
                break

            task_id = task["id"]
            username = task.get("account_username") or f"account#{task['account_id']}"
            task_start_time = time.time()
            db.transition_run_status(run_id, "running", current_task_id=task_id)
            db.transition_task_state(task_id, "running", current_step=0, current_step_label="Preparing account & video")
            db.append_task_event(
                task_id,
                "info",
                f"Starting task #{task['sequence_number']} for @{username} ({task.get('device_serial')} / {task.get('package_name')}).",
                step=0,
                elapsed_ms=0,
            )

            max_step_seen = 0
            execution_error = None
            cancelled_during_task = False
            reel_url = None
            retry_review = False

            def task_log_cb(level, message, step=None):
                nonlocal max_step_seen
                elapsed_ms = int((time.time() - task_start_time) * 1000)
                if isinstance(step, int) and step > max_step_seen:
                    max_step_seen = step
                    label = _CAMPAIGN_STEP_LABELS.get(step, "")
                    db.update_task_progress(task_id, max_step_seen, label)
                db.append_task_event(
                    task_id,
                    level,
                    str(message),
                    step=step if isinstance(step, int) else None,
                    elapsed_ms=elapsed_ms,
                )
                log_listener(level, f"[Campaign #{run_id} | @{username}] {message}", step=step)

            # Resolve fresh account binding & content path right before execution
            acc = db.get_account_by_id(task["account_id"])
            serial = ((acc or {}).get("device_serial") or task.get("device_serial") or "").strip()
            pkg = ((acc or {}).get("package_name") or task.get("package_name") or "").strip()
            citem = db.get_content_item(task["content_id"])
            video_path = _resolve_content_filepath(citem.get("storage_key", "")) if citem else None

            # Initialize and start per-task screen recorder
            recorder = None
            if serial:
                try:
                    recorder = screen_recorder.TaskScreenRecorder(
                        task_id=task_id,
                        device_serial=serial,
                        storage_base_dir=_get_recordings_artifacts_dir(),
                        log_callback=task_log_cb,
                        testing_mode=bool(app.testing or app.config.get("TESTING")),
                    )
                    rec_ok = recorder.start()
                    db.update_task_recording(
                        task_id,
                        status="recording" if rec_ok else "capture_failed",
                        started_at=datetime.now(timezone.utc).isoformat(),
                        error=recorder._error if not rec_ok else None,
                    )
                except Exception as rec_init_exc:
                    db.update_task_recording(
                        task_id,
                        status="capture_failed",
                        error=str(rec_init_exc),
                    )
                    task_log_cb("warning", f"Could not initialize screen recording: {rec_init_exc}")

            if not acc or not serial or not pkg:
                execution_error = f"Account @{username} lost its device or package binding before launch."
                task_log_cb("error", execution_error)
                if serial:
                    shot_path, shot_at, shot_err = _capture_task_failure_screenshot(task_id, serial)
                    if shot_path:
                        db.update_task_failure_artifact(task_id, screenshot_path=shot_path, captured_at=shot_at)
                    elif shot_err:
                        db.update_task_failure_artifact(task_id, error=shot_err)
            elif not citem or citem.get("media_type") != "video" or not video_path or not os.path.isfile(video_path):
                execution_error = f"Assigned video content #{task['content_id']} is missing or not a video."
                task_log_cb("error", execution_error)
                if serial:
                    shot_path, shot_at, shot_err = _capture_task_failure_screenshot(task_id, serial)
                    if shot_path:
                        db.update_task_failure_artifact(task_id, screenshot_path=shot_path, captured_at=shot_at)
                    elif shot_err:
                        db.update_task_failure_artifact(task_id, error=shot_err)
            else:
                try:
                    uploader = adb.InstagramReelUploader(
                        device_serial=serial,
                        package_name=pkg,
                        log_callback=task_log_cb,
                    )
                    active_campaign_uploader = uploader
                    if campaign_cancel_event.is_set() or db.is_run_cancel_requested(run_id):
                        uploader.is_cancelled = True
                        raise InterruptedError("Workflow cancelled by user.")
                    reel_url = uploader.execute_workflow(
                        audio_url=audio_url,
                        video_path=video_path,
                        caption=task.get("caption") or DEFAULT_CAPTION,
                        hide_likes=hide_likes,
                        hide_shares=hide_shares,
                    )
                except InterruptedError as ce:
                    cancelled_during_task = True
                    execution_error = str(ce)
                    if max_step_seen >= 9:
                        retry_review = True
                    task_log_cb("warning", f"Task cancelled: {ce}")
                except Exception as exc:
                    execution_error = str(exc)
                    if max_step_seen >= 9:
                        retry_review = True
                        task_log_cb(
                            "warning",
                            f"Failure occurred at/after Share (step {max_step_seen}); marked for manual review before retry.",
                            step=max_step_seen,
                        )
                    task_log_cb("error", f"Task execution failed: {exc}")
                    shot_path, shot_at, shot_err = _capture_task_failure_screenshot(
                        task_id, serial, uploader=active_campaign_uploader
                    )
                    if shot_path:
                        db.update_task_failure_artifact(task_id, screenshot_path=shot_path, captured_at=shot_at)
                        task_log_cb("info", f"Captured failure screenshot: {shot_path}")
                    elif shot_err:
                        db.update_task_failure_artifact(task_id, error=shot_err)
                        task_log_cb("warning", f"Could not capture failure screenshot: {shot_err}")
                finally:
                    active_campaign_uploader = None

            # Always close app and verify stopped before starting another account
            cleanup_ok = False
            cleanup_note = ""
            db.transition_task_state(
                task_id,
                "closing_app",
                current_step=max_step_seen,
                current_step_label="Closing app & verifying stop",
                reel_url=reel_url,
                error_message=execution_error,
                retry_requires_review=retry_review,
            )
            if serial and pkg:
                try:
                    db.append_task_event(task_id, "info", f"Closing '{pkg}' on device '{serial}' and verifying stopped...")
                    profile_capture.close_profile_app(device_serial=serial, package_name=pkg)
                    cleanup_ok = True
                    cleanup_note = "closed"
                    db.append_task_event(task_id, "success", f"Verified '{pkg}' stopped on '{serial}'.")
                except Exception as close_exc:
                    cleanup_ok = False
                    cleanup_note = f"failed: {close_exc}"
                    db.append_task_event(task_id, "error", f"Package stop verification failed for '{pkg}' on '{serial}': {close_exc}")
            else:
                cleanup_ok = False
                cleanup_note = "failed: missing device serial or package name for cleanup"
                db.append_task_event(task_id, "error", "Cannot verify package stop: missing device serial or package name.")

            if not cleanup_ok:
                terminal_task_state = "completed" if execution_error is None else ("cancelled" if cancelled_during_task else "failed")
                _finalize_task_recording(recorder, task_id, terminal_task_state, cleanup_ok=False, task_start_time=task_start_time)
                db.transition_task_state(
                    task_id,
                    terminal_task_state,
                    cleanup_status=cleanup_note,
                    error_message=execution_error or f"App cleanup failed: {cleanup_note}",
                    retry_requires_review=retry_review,
                )
                pause_reason = (
                    f"Run paused after Task #{task['sequence_number']} (@{username}) because '{pkg}' could not be stopped cleanly: {cleanup_note}"
                )
                db.transition_run_status(run_id, "paused", current_task_id=None, error_message=pause_reason)
                log_listener("error", f"[Campaign #{run_id}] {pause_reason}")
                return

            # Cleanup succeeded; check cancellation first
            if cancelled_during_task or campaign_cancel_event.is_set() or db.is_run_cancel_requested(run_id):
                terminal_task_state = "completed" if execution_error is None else "cancelled"
                _finalize_task_recording(recorder, task_id, terminal_task_state, cleanup_ok=cleanup_ok, task_start_time=task_start_time)
                db.transition_task_state(
                    task_id,
                    terminal_task_state,
                    cleanup_status=cleanup_note,
                    error_message=execution_error,
                    retry_requires_review=retry_review,
                )
                db.cancel_pending_tasks_for_run(run_id, reason="Run cancelled before task started.")
                db.transition_run_status(run_id, "cancelled", current_task_id=None)
                return

            next_pending = db.get_next_pending_task(run_id)
            should_advance = (execution_error is None) or (failure_policy == "continue")
            final_task_state = "completed" if execution_error is None else "failed"
            _finalize_task_recording(recorder, task_id, final_task_state, cleanup_ok=cleanup_ok, task_start_time=task_start_time)

            if next_pending and should_advance and cooldown_seconds > 0:
                db.transition_task_state(
                    task_id,
                    "cooldown",
                    current_step_label=f"Cooldown ({cooldown_seconds}s)",
                    cleanup_status=cleanup_note,
                    retry_requires_review=retry_review,
                )
                db.append_task_event(
                    task_id,
                    "info",
                    f"Waiting {cooldown_seconds}s cooldown before launching next target (@{next_pending['account_username']}).",
                )
                interrupted_wait = campaign_cancel_event.wait(cooldown_seconds)
                if interrupted_wait or db.is_run_cancel_requested(run_id):
                    db.transition_task_state(
                        task_id,
                        final_task_state,
                        cleanup_status=cleanup_note,
                        retry_requires_review=retry_review,
                    )
                    db.cancel_pending_tasks_for_run(run_id, reason="Run cancelled during cooldown.")
                    db.transition_run_status(run_id, "cancelled", current_task_id=None)
                    return

            db.transition_task_state(
                task_id,
                final_task_state,
                cleanup_status=cleanup_note,
                error_message=execution_error,
                retry_requires_review=retry_review,
            )

            if execution_error is not None and failure_policy == "pause" and next_pending:
                pause_reason = f"Run paused after Task #{task['sequence_number']} (@{username}) failed: {execution_error}"
                db.transition_run_status(run_id, "paused", current_task_id=None, error_message=pause_reason)
                log_listener("warning", f"[Campaign #{run_id}] {pause_reason}")
                return

        # All pending targets finished
        final_run = db.get_campaign_run(run_id)
        if final_run and final_run["status"] == "running":
            counts = final_run.get("task_counts", {})
            if counts.get("failed", 0) > 0 or counts.get("interrupted", 0) > 0:
                db.transition_run_status(
                    run_id,
                    "failed",
                    current_task_id=None,
                    error_message=f"Run finished with {counts.get('failed', 0)} failed task(s).",
                )
            else:
                db.transition_run_status(run_id, "completed", current_task_id=None, error_message=None)
    except Exception as fatal_exc:
        try:
            cur = db.get_campaign_run(run_id)
            if cur and cur["status"] in ("queued", "running"):
                db.transition_run_status(run_id, "failed", current_task_id=None, error_message=str(fatal_exc))
        except Exception:
            pass
    finally:
        active_campaign_uploader = None
        if active_campaign_run_id == run_id:
            active_campaign_run_id = None


def _start_campaign_worker_thread(run_id: int) -> None:
    global campaign_worker_thread
    campaign_worker_thread = threading.Thread(
        target=_execute_campaign_run_worker,
        args=(run_id,),
        daemon=True,
    )
    campaign_worker_thread.start()


@app.route("/api/campaigns", methods=["GET", "POST"])
def campaigns_collection_api():
    if request.method == "GET":
        return jsonify({"status": "ok", "campaigns": db.list_campaigns()})

    data = request.json or {}
    try:
        created = db.create_campaign(
            name=data.get("name", ""),
            automation_id=data.get("automation_id", BUILTIN_AUTOMATION_ID),
            audio_url=data.get("audio_url", ""),
            hide_likes=data.get("hide_likes", True),
            hide_shares=data.get("hide_shares", True),
            cooldown_seconds=data.get("cooldown_seconds", db.CAMPAIGN_COOLDOWN_DEFAULT),
            failure_policy=data.get("failure_policy", "pause"),
            targets=data.get("targets"),
        )
        return jsonify({"status": "ok", "campaign": created}), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/campaigns/<int:campaign_id>", methods=["GET", "PATCH", "DELETE"])
def campaign_item_api(campaign_id):
    if request.method == "GET":
        camp = db.get_campaign(campaign_id)
        if not camp:
            return jsonify({"error": f"Campaign #{campaign_id} not found."}), 404
        return jsonify({"status": "ok", "campaign": camp})

    if request.method == "DELETE":
        try:
            deleted = db.delete_campaign(campaign_id)
        except db.ResourceInUseError as e:
            return jsonify({"error": str(e), "code": "resource_in_use"}), 409
        if not deleted:
            return jsonify({"error": f"Campaign #{campaign_id} not found."}), 404
        return jsonify({"status": "deleted", "id": campaign_id})

    data = request.json or {}
    try:
        updated = db.update_campaign(
            campaign_id=campaign_id,
            name=data["name"] if "name" in data else db._UNSET,
            automation_id=data["automation_id"] if "automation_id" in data else db._UNSET,
            audio_url=data["audio_url"] if "audio_url" in data else db._UNSET,
            hide_likes=data["hide_likes"] if "hide_likes" in data else db._UNSET,
            hide_shares=data["hide_shares"] if "hide_shares" in data else db._UNSET,
            cooldown_seconds=data["cooldown_seconds"] if "cooldown_seconds" in data else db._UNSET,
            failure_policy=data["failure_policy"] if "failure_policy" in data else db._UNSET,
            targets=data["targets"] if "targets" in data else db._UNSET,
        )
    except db.ResourceInUseError as e:
        return jsonify({"error": str(e), "code": "campaign_running"}), 409
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    if not updated:
        return jsonify({"error": f"Campaign #{campaign_id} not found."}), 404
    return jsonify({"status": "ok", "campaign": updated})


@app.route("/api/campaigns/<int:campaign_id>/preflight", methods=["POST"])
def campaign_preflight_api(campaign_id):
    camp = db.get_campaign(campaign_id)
    if not camp:
        return jsonify({"error": f"Campaign #{campaign_id} not found."}), 404
    result = _run_campaign_preflight(camp)
    return jsonify({"status": "ok", **result})


@app.route("/api/campaigns/<int:campaign_id>/runs", methods=["POST"])
def launch_campaign_run_api(campaign_id):
    with workflow_start_lock:
        conflict_msg = _is_any_workflow_active()
        if conflict_msg:
            return jsonify({"error": conflict_msg, "code": "workflow_locked"}), 409

        camp = db.get_campaign(campaign_id)
        if not camp:
            return jsonify({"error": f"Campaign #{campaign_id} not found."}), 404

        preflight = _run_campaign_preflight(camp, include_lock_check=False)
        if not preflight["ready"]:
            first_msg = preflight["blockers"][0]["message"] if preflight["blockers"] else "Preflight checks failed."
            return jsonify({"error": first_msg, "preflight": preflight}), 400

        try:
            run = db.create_campaign_run(campaign_id)
        except db.ResourceInUseError as e:
            return jsonify({"error": str(e), "code": "workflow_locked"}), 409
        except ValueError as e:
            return jsonify({"error": str(e)}), 400

        try:
            _start_campaign_worker_thread(run["id"])
        except Exception as e:
            db.transition_run_status(run["id"], "failed", error_message=f"Failed to launch worker thread: {e}")
            return jsonify({"error": f"Failed to launch campaign worker: {e}"}), 500

    return jsonify({"status": "started", "run": db.get_campaign_run(run["id"]) or run}), 202


@app.route("/api/campaign-runs", methods=["GET"])
def list_campaign_runs_api():
    raw_cid = (request.args.get("campaign_id") or "").strip()
    campaign_id = None
    if raw_cid:
        try:
            campaign_id = int(raw_cid)
        except ValueError:
            return jsonify({"error": "Invalid campaign_id filter."}), 400
    status_filter = (request.args.get("status") or "").strip().lower() or None
    search_filter = (request.args.get("search") or "").strip() or None
    try:
        limit = int(request.args.get("limit", 25))
        offset = int(request.args.get("offset", 0))
    except ValueError:
        return jsonify({"error": "limit and offset must be integers."}), 400

    runs, total = db.list_campaign_runs(
        campaign_id=campaign_id,
        status=status_filter,
        search=search_filter,
        limit=limit,
        offset=offset,
    )
    return jsonify({"status": "ok", "runs": runs, "total": total, "limit": limit, "offset": offset})


@app.route("/api/campaign-runs/<int:run_id>", methods=["GET"])
def get_campaign_run_api(run_id):
    run = db.get_campaign_run(run_id)
    if not run:
        return jsonify({"error": f"Campaign Run #{run_id} not found."}), 404
    return jsonify({"status": "ok", "run": run})


@app.route("/api/campaign-runs/<int:run_id>/export", methods=["GET"])
def export_campaign_run_api(run_id):
    run = db.get_campaign_run(run_id)
    if not run:
        return jsonify({"error": f"Campaign Run #{run_id} not found."}), 404
    export_format = (request.args.get("format") or "json").strip().lower()
    if export_format == "csv":
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "Run ID",
            "Campaign ID",
            "Campaign Name",
            "Sequence",
            "Task ID",
            "Account Username",
            "Device Serial",
            "Package Name",
            "Status",
            "Current Step",
            "Current Step Label",
            "Reel URL",
            "Cleanup Status",
            "Error Message",
            "Has Screenshot",
            "Screenshot URL",
            "Started At",
            "Completed At",
            "Event Count",
        ])
        for t in run.get("tasks", []):
            writer.writerow([
                run["id"],
                run["campaign_id"],
                run["campaign_name"],
                t.get("sequence_number", ""),
                t.get("id", ""),
                t.get("account_username", ""),
                t.get("device_serial", ""),
                t.get("package_name", ""),
                t.get("status", ""),
                t.get("current_step", 0),
                t.get("current_step_label", ""),
                t.get("reel_url", ""),
                t.get("cleanup_status", ""),
                t.get("error_message", ""),
                "yes" if t.get("has_failure_screenshot") else "no",
                t.get("failure_screenshot_url", ""),
                t.get("started_at", ""),
                t.get("completed_at", ""),
                t.get("event_count", 0),
            ])
        csv_data = output.getvalue()
        return Response(
            csv_data,
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment; filename=campaign_run_{run_id}_tasks.csv"},
        )
    return Response(
        json.dumps({"status": "ok", "run": run}, indent=2),
        mimetype="application/json",
        headers={"Content-Disposition": f"attachment; filename=campaign_run_{run_id}_full.json"},
    )


@app.route("/api/campaign-runs/<int:run_id>/cancel", methods=["POST"])
def cancel_campaign_run_api(run_id):
    run = db.get_campaign_run(run_id)
    if not run:
        return jsonify({"error": f"Campaign Run #{run_id} not found."}), 404
    if run["status"] in ("completed", "failed", "cancelled", "interrupted"):
        return jsonify({"error": f"Campaign Run #{run_id} is already '{run['status']}'."}), 409

    db.set_run_cancel_requested(run_id, True)
    if active_campaign_run_id == run_id:
        campaign_cancel_event.set()
        if active_campaign_uploader is not None:
            active_campaign_uploader.is_cancelled = True

    # If paused or no active worker for this run, finalize cancellation cleanly immediately
    if run["status"] == "paused" or (run["status"] == "queued" and active_campaign_run_id != run_id):
        db.cancel_pending_tasks_for_run(run_id, reason="Run cancelled by operator.")
        db.transition_run_status(run_id, "cancelled", current_task_id=None, cancel_requested=True)

    return jsonify({"status": "ok", "run": db.get_campaign_run(run_id)})


@app.route("/api/campaign-runs/<int:run_id>/continue", methods=["POST"])
def continue_campaign_run_api(run_id):
    with workflow_start_lock:
        conflict_msg = _is_any_workflow_active()
        if conflict_msg:
            return jsonify({"error": conflict_msg, "code": "workflow_locked"}), 409

        run = db.get_campaign_run(run_id)
        if not run:
            return jsonify({"error": f"Campaign Run #{run_id} not found."}), 404
        if run["status"] != "paused":
            return jsonify({"error": f"Only paused runs can be continued (Run #{run_id} is '{run['status']}')."}), 400

        pending_tasks = [t for t in run.get("tasks", []) if t["status"] == "pending"]
        if not pending_tasks:
            return jsonify({"error": f"Run #{run_id} has no remaining pending tasks to continue."}), 400

        preflight = _run_campaign_preflight(
            {
                "id": run["campaign_id"],
                "automation_id": run["automation_id"],
                "audio_url": run["audio_url"],
            },
            target_subset=pending_tasks,
            include_lock_check=False,
        )
        if not preflight["ready"]:
            first_msg = preflight["blockers"][0]["message"] if preflight["blockers"] else "Preflight checks failed."
            return jsonify({"error": first_msg, "preflight": preflight}), 400

        db.transition_run_status(run_id, "running", cancel_requested=False, error_message=None)
        try:
            _start_campaign_worker_thread(run_id)
        except Exception as e:
            db.transition_run_status(run_id, "paused", error_message=f"Failed to resume worker thread: {e}")
            return jsonify({"error": f"Failed to resume campaign worker: {e}"}), 500

    return jsonify({"status": "started", "run": db.get_campaign_run(run_id)}), 202


@app.route("/api/tasks", methods=["GET"])
def list_tasks_api():
    def _opt_int(arg_name):
        raw = (request.args.get(arg_name) or "").strip()
        if not raw:
            return None
        return int(raw)

    try:
        run_id = _opt_int("run_id")
        campaign_id = _opt_int("campaign_id")
        account_id = _opt_int("account_id")
        limit = int(request.args.get("limit", 50))
        offset = int(request.args.get("offset", 0))
    except ValueError:
        return jsonify({"error": "Filter/pagination IDs must be integers."}), 400

    status_filter = (request.args.get("status") or "").strip().lower() or None
    search_filter = (request.args.get("search") or "").strip() or None
    tasks, total = db.list_tasks(
        run_id=run_id,
        campaign_id=campaign_id,
        account_id=account_id,
        status=status_filter,
        search=search_filter,
        limit=limit,
        offset=offset,
    )
    return jsonify({"status": "ok", "tasks": tasks, "total": total, "limit": limit, "offset": offset})


@app.route("/api/tasks/<int:task_id>", methods=["GET"])
def get_task_detail_api(task_id):
    task = db.get_task_detail(task_id)
    if not task:
        return jsonify({"error": f"Task #{task_id} not found."}), 404
    return jsonify({"status": "ok", "task": task})


@app.route("/api/tasks/<int:task_id>/export", methods=["GET"])
def export_task_api(task_id):
    task = db.get_task_detail(task_id)
    if not task:
        return jsonify({"error": f"Task #{task_id} not found."}), 404
    export_format = (request.args.get("format") or "json").strip().lower()
    if export_format == "csv":
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Task ID", "Event ID", "Timestamp", "Level", "Step", "Stage", "Message", "Elapsed (s)"])
        for ev in task.get("events", []):
            elapsed_s = round(float(ev.get("elapsed_ms", 0)) / 1000.0, 2) if ev.get("elapsed_ms") is not None else ""
            writer.writerow([
                task["id"],
                ev.get("id", ""),
                ev.get("timestamp", ""),
                ev.get("level", ""),
                ev.get("step", ""),
                ev.get("stage", ""),
                ev.get("message", ""),
                elapsed_s,
            ])
        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment; filename=task_{task_id}_events.csv"},
        )
    return Response(
        json.dumps({"status": "ok", "task": task}, indent=2),
        mimetype="application/json",
        headers={"Content-Disposition": f"attachment; filename=task_{task_id}_detail.json"},
    )


@app.route("/api/tasks/<int:task_id>/failure-screenshot", methods=["GET"])
def serve_task_failure_screenshot_api(task_id):
    task = db.get_task_detail(task_id)
    if not task:
        return jsonify({"error": f"Task #{task_id} not found."}), 404
    path_key = task.get("failure_screenshot_path")
    if not path_key:
        return jsonify({"error": f"Task #{task_id} has no failure screenshot."}), 404
    resolved = _resolve_artifact_filepath(path_key)
    if not resolved:
        return jsonify({"error": f"Screenshot file for Task #{task_id} is missing on server."}), 404
    return send_file(resolved, mimetype="image/png")


@app.route("/api/tasks/<int:task_id>/recording", methods=["GET"])
def get_task_recording_api(task_id: int):
    task = db.get_task_detail(task_id)
    if not task:
        return jsonify({"error": f"Task #{task_id} not found."}), 404

    status = task.get("recording_status") or "unavailable"
    if status == "deleted_success":
        return jsonify({
            "error": "Screen recording was deleted because task and app cleanup completed with 100% success.",
            "status": "deleted_success",
            "task_id": task_id,
        }), 404
    if status == "expired":
        return jsonify({
            "error": "Screen recording has expired and was cleaned up per 30-day retention policy.",
            "status": "expired",
            "task_id": task_id,
        }), 404
    if status != "saved":
        return jsonify({
            "error": f"No saved screen recording for Task #{task_id} (status: {status}).",
            "status": status,
            "task_id": task_id,
        }), 404

    candidate_path = task.get("recording_path")
    if not candidate_path or not os.path.isfile(candidate_path):
        candidate_path = _resolve_recording_filepath(task_id)

    if not candidate_path or not os.path.isfile(candidate_path):
        return jsonify({"error": "Screen recording video file not found on disk.", "status": status}), 404

    as_attachment = request.args.get("download", "false").lower() in ("true", "1")
    download_name = f"task_{task_id}_screenrecord.mp4"
    return send_file(candidate_path, mimetype="video/mp4", as_attachment=as_attachment, download_name=download_name)


@app.route("/api/tasks/<int:task_id>/recording/segments/<int:segment_index>", methods=["GET"])
def get_task_recording_segment_api(task_id: int, segment_index: int):
    task = db.get_task_detail(task_id)
    if not task:
        return jsonify({"error": f"Task #{task_id} not found."}), 404

    segments = task.get("recording_segments") or []
    target_seg = next((s for s in segments if int(s.get("index", -1)) == segment_index), None)
    if not target_seg:
        return jsonify({"error": f"Segment #{segment_index} not found for Task #{task_id}."}), 404

    candidate_path = target_seg.get("path")
    if not candidate_path or not os.path.isfile(candidate_path):
        candidate_path = _resolve_recording_filepath(task_id, target_seg.get("filename"))

    if not candidate_path or not os.path.isfile(candidate_path):
        return jsonify({"error": f"Segment file for segment #{segment_index} not found on disk."}), 404

    as_attachment = request.args.get("download", "false").lower() in ("true", "1")
    return send_file(
        candidate_path,
        mimetype="video/mp4",
        as_attachment=as_attachment,
        download_name=f"task_{task_id}_seg{segment_index}.mp4",
    )


@app.route("/api/recordings/cleanup-expired", methods=["POST"])
def cleanup_expired_recordings_api():
    older_than_days = request.args.get("days", 30)
    try:
        days = int(older_than_days)
    except (TypeError, ValueError):
        days = 30
    cleaned_count = screen_recorder.cleanup_expired_recordings(older_than_days=days, db_module=db)
    return jsonify({"status": "ok", "cleaned_count": cleaned_count, "retention_days": days})


@app.route("/api/tasks/<int:task_id>/retry", methods=["POST"])
def retry_task_api(task_id):
    with workflow_start_lock:
        conflict_msg = _is_any_workflow_active()
        if conflict_msg:
            return jsonify({"error": conflict_msg, "code": "workflow_locked"}), 409

        task = db.get_task_detail(task_id)
        if not task:
            return jsonify({"error": f"Task #{task_id} not found."}), 404
        if task["status"] not in ("failed", "interrupted", "cancelled"):
            return jsonify({"error": f"Task #{task_id} has state '{task['status']}' and cannot be retried."}), 400

        camp = db.get_campaign(task["campaign_id"])
        if not camp:
            return jsonify({"error": f"Parent campaign #{task['campaign_id']} not found."}), 404

        acc = db.get_account_by_id(task["account_id"])
        citem = db.get_content_item(task["content_id"])
        retry_target = {
            "campaign_target_id": task.get("campaign_target_id"),
            "account_id": task["account_id"],
            "account_username": (acc or {}).get("username") or task.get("account_username", ""),
            "device_serial": (acc or {}).get("device_serial") or task.get("device_serial"),
            "package_name": (acc or {}).get("package_name") or task.get("package_name"),
            "content_id": task["content_id"],
            "content_filename": (citem or {}).get("original_filename") or task.get("content_filename", ""),
            "caption": task.get("caption", ""),
        }

        preflight = _run_campaign_preflight(camp, target_subset=[retry_target], include_lock_check=False)
        if not preflight["ready"]:
            first_msg = preflight["blockers"][0]["message"] if preflight["blockers"] else "Preflight checks failed."
            return jsonify({"error": first_msg, "preflight": preflight}), 400

        try:
            retry_run = db.create_campaign_run(camp["id"], target_rows=[retry_target])
        except db.ResourceInUseError as e:
            return jsonify({"error": str(e), "code": "workflow_locked"}), 409
        except ValueError as e:
            return jsonify({"error": str(e)}), 400

        retry_task_id = retry_run["tasks"][0]["id"]
        db.append_task_event(
            retry_task_id,
            "info",
            f"Explicit retry launched from Run #{task['run_id']}, Task #{task_id}.",
        )
        try:
            _start_campaign_worker_thread(retry_run["id"])
        except Exception as e:
            db.transition_run_status(retry_run["id"], "failed", error_message=f"Failed to launch retry thread: {e}")
            return jsonify({"error": f"Failed to launch retry worker: {e}"}), 500

    return jsonify({"status": "started", "run": db.get_campaign_run(retry_run["id"]) or retry_run}), 202


if __name__ == "__main__":
    port = 5000
    print(f"\n=======================================================")
    print(f" Instagram Post Automation GUI")
    print(f" Web Dashboard URL: http://127.0.0.1:{port}")
    print(f"=======================================================\n")
    # Auto-open browser after 1 second
    threading.Timer(1.2, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
