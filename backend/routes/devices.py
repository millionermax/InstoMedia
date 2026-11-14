"""
backend/routes/devices.py - Connected Android devices and status endpoints.
"""
from flask import Blueprint, jsonify
from automation.android import package_scanner

devices_bp = Blueprint("devices_bp", __name__)


@devices_bp.route("/api/devices", methods=["GET"])
def list_devices_api():
    """Returns connected ADB devices with serial, status, product_name, and selectability."""
    devices = package_scanner.list_connected_devices()
    return jsonify({"status": "ok", "devices": devices})


@devices_bp.route("/api/device")
def get_device_info():
    """Returns details for currently selected / primary connected device."""
    import app
    d = app.get_u2_device()
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
            "height": h,
        })
    except Exception as e:
        return jsonify({"connected": False, "name": "Error", "error": str(e)})
