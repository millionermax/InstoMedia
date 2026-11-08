import os
import sys
import time
import json
import queue
import io
import threading
import webbrowser
from flask import Flask, render_template, request, Response, jsonify, send_from_directory
from werkzeug.utils import secure_filename
from PIL import Image
import uiautomator2 as u2

from adb import InstagramReelUploader, DEFAULT_AUDIO_URL, DEFAULT_CAPTION

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


def run_workflow_async(audio_url, video_path, caption, hide_likes, hide_shares):
    global current_uploader, workflow_status
    workflow_status["state"] = "running"
    workflow_status["current_step"] = 1
    workflow_status["reel_url"] = None
    workflow_status["error_message"] = None

    try:
        current_uploader = InstagramReelUploader(log_callback=log_listener)
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

    video_path = None
    if video_filename:
        video_path = os.path.join(app.config["UPLOAD_FOLDER"], video_filename)
        if not os.path.exists(video_path):
            return jsonify({"error": "Selected video file not found on server"}), 400

    workflow_thread = threading.Thread(
        target=run_workflow_async,
        args=(audio_url, video_path, caption, hide_likes, hide_shares),
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


if __name__ == "__main__":
    port = 5000
    print(f"\n=======================================================")
    print(f" Instagram Post Automation GUI")
    print(f" Web Dashboard URL: http://127.0.0.1:{port}")
    print(f"=======================================================\n")
    # Auto-open browser after 1 second
    threading.Timer(1.2, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
