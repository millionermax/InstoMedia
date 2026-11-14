"""
backend/routes/dashboard.py - Dashboard view, MJPEG screen mirror stream, and remote device touch/keys.
"""
import time
from flask import Blueprint, render_template, Response, request, jsonify

dashboard_bp = Blueprint("dashboard_bp", __name__)


@dashboard_bp.route("/")
def index():
    return render_template("index.html")


@dashboard_bp.route("/api/stream")
def screen_stream():
    """MJPEG stream endpoint for real-time video mirroring."""
    import app

    def generate_frames():
        while True:
            if app.current_frame_bytes:
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n"
                    + app.current_frame_bytes
                    + b"\r\n"
                )
            time.sleep(0.08)

    return Response(generate_frames(), mimetype="multipart/x-mixed-replace; boundary=frame")


@dashboard_bp.route("/api/tap", methods=["POST"])
def tap_screen():
    """Allows interacting with the phone by clicking the mirror screen."""
    import app

    d = app.get_u2_device()
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


@dashboard_bp.route("/api/key", methods=["POST"])
def press_key():
    import app

    d = app.get_u2_device()
    if not d:
        return jsonify({"error": "No device connected"}), 400

    data = request.json or {}
    key = data.get("key")
    if key in ["home", "back", "app_switch"]:
        d.press(key)
        return jsonify({"status": "ok", "key": key})
    return jsonify({"error": "Invalid key"}), 400
