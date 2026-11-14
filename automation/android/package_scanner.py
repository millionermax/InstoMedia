"""
package_scanner.py - ADB Package Discovery & Clone Inventory Engine for Instoper.
Discovers installed Instagram packages and clone candidates on Android devices,
tracks operator confirmation status, and verifies package availability.
"""

import os
import sys

# Ensure repository root is on sys.path if invoked or imported independently
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import re
import subprocess
from typing import List, Dict, Any, Optional, Set

from backend.storage import db

OFFICIAL_INSTAGRAM_PKG = "com.instagram.android"

# Known package patterns for Instagram and Android app cloning utilities
CLONE_KEYWORDS = [
    "instagram",
    "insta",
    "appcloner",
    "cloner",
    "clone",
    "dualspace",
    "dual",
    "parallel",
    "multiaccount",
    "multiapp",
    "island",
    "shelter",
    "superclone",
    "2face",
]


class InstallResultStatus:
    SUCCESS = "success"
    ALREADY_EXISTS = "already_exists"
    SIGNATURE_MISMATCH = "signature_mismatch"
    DOWNGRADE_ERROR = "downgrade_error"
    STORAGE_ERROR = "insufficient_storage"
    INVALID_APK = "invalid_apk"
    CONFIRMATION_REQUIRED = "confirmation_required"
    DEVICE_OFFLINE = "device_offline"
    ERROR = "error"



def run_adb_command(cmd: List[str], device_serial: Optional[str] = None, timeout: int = 8) -> str:
    """Executes an adb command with timeout and returns stripped stdout."""
    prefix = ["adb"]
    if device_serial:
        prefix.extend(["-s", device_serial])
    full_cmd = prefix + cmd
    try:
        proc = subprocess.run(
            full_cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False
        )
        return proc.stdout.strip()
    except Exception as e:
        return ""


def list_connected_devices() -> List[Dict[str, Any]]:
    """
    Returns connected ADB devices with explicit serial, status, product_name (when online
    and available), and selectable flag (True only when status == 'device').
    Does not fall back to or select invalid/unauthorized/offline devices.
    """
    output = run_adb_command(["devices", "-l"])
    devices: List[Dict[str, Any]] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("*") or line.lower().startswith("list of devices"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        serial = parts[0]
        status = parts[1]
        props: Dict[str, str] = {}
        for token in parts[2:]:
            if ":" in token:
                k, v = token.split(":", 1)
                props[k] = v

        product_name: Optional[str] = None
        if status == "device":
            raw_model = props.get("model") or props.get("product") or props.get("device")
            if raw_model:
                product_name = raw_model.replace("_", " ").strip()

        devices.append({
            "serial": serial,
            "status": status,
            "product_name": product_name,
            "selectable": (status == "device"),
        })
    return devices


class OnboardingValidationError(ValueError):
    """Raised when device/package validation fails during explicit onboarding."""

    def __init__(self, message: str, code: str = "validation_error", http_status: int = 400):
        super().__init__(message)
        self.message = message
        self.code = code
        self.http_status = http_status


def require_online_device(device_serial: Optional[str]) -> Dict[str, Any]:
    """
    Requires an explicit device serial currently connected in ADB 'device' state.
    Raises OnboardingValidationError if missing, unknown, offline, or unauthorized.
    Never falls back to the first connected device.
    """
    serial = str(device_serial or "").strip()
    if not serial:
        raise OnboardingValidationError(
            "An explicit Android device serial is required.",
            code="missing_device_serial",
            http_status=400,
        )

    devices = list_connected_devices()
    matched = next((d for d in devices if d["serial"] == serial), None)
    if not matched:
        raise OnboardingValidationError(
            f"Device '{serial}' is not connected. Refresh devices and select an online phone.",
            code="device_not_online",
            http_status=400,
        )
    if matched.get("status") != "device":
        raise OnboardingValidationError(
            f"Device '{serial}' has ADB status '{matched.get('status')}' and cannot be used until authorized and online.",
            code="device_not_online",
            http_status=400,
        )
    return matched


def validate_confirmed_package(
    device_serial: Optional[str],
    package_name: Optional[str],
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Verifies that the selected device is online, the exact package is actively
    installed on that device serial, and the package is confirmed in the clone
    inventory for that same device serial. Does not launch the app.
    """
    device = require_online_device(device_serial)
    serial = device["serial"]

    pkg = str(package_name or "").strip()
    if not pkg:
        raise OnboardingValidationError(
            "An explicit package name is required.",
            code="missing_package_name",
            http_status=400,
        )

    if not is_package_installed(pkg, device_serial=serial):
        raise OnboardingValidationError(
            f"Package '{pkg}' is not currently installed on device '{serial}'.",
            code="package_not_installed",
            http_status=400,
        )

    clones = db.get_clones(device_serial=serial, confirmed_only=False, db_path=db_path)
    clone_row = next((c for c in clones if c["package_name"] == pkg), None)
    if not clone_row or not bool(clone_row.get("is_confirmed")):
        raise OnboardingValidationError(
            f"Package '{pkg}' is not confirmed for device '{serial}'. Scan and confirm it first.",
            code="package_not_confirmed",
            http_status=400,
        )

    return {
        "valid": True,
        "device_serial": serial,
        "package_name": pkg,
        "clone_id": clone_row["id"],
        "app_name": clone_row.get("app_name") or pkg,
        "device": device,
    }


def get_foreground_package(device_serial: Optional[str] = None) -> Optional[str]:
    """Finds current foreground package using dumpsys window."""
    output = run_adb_command(["shell", "dumpsys", "window"], device_serial=device_serial, timeout=4)
    for line in output.splitlines():
        if "mCurrentFocus" in line or "mFocusedApp" in line:
            match = re.search(r"([a-zA-Z0-9_]+(?:\.[a-zA-Z0-9_]+)+)/", line)
            if match:
                return match.group(1)
    return None


def is_package_installed(package_name: str, device_serial: Optional[str] = None) -> bool:
    """Checks whether a package is actively installed on the device via pm path."""
    if not package_name:
        return False
    output = run_adb_command(["shell", "pm", "path", package_name], device_serial=device_serial, timeout=4)
    return "package:" in output


def get_third_party_packages(device_serial: Optional[str] = None) -> Set[str]:
    """Helper to query current third-party packages installed on the target device."""
    output = run_adb_command(["shell", "pm", "list", "packages", "-3"], device_serial=device_serial)
    pkgs = set()
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("package:"):
            pkgs.add(line.replace("package:", "").strip())
    return pkgs


def get_all_installed_packages(device_serial: str) -> Set[str]:
    """Queries all installed package names on the specified explicit device serial."""
    output = run_adb_command(["shell", "pm", "list", "packages"], device_serial=device_serial)
    pkgs = set()
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("package:"):
            pkgs.add(line.replace("package:", "").strip())
    return pkgs


def install_apk_on_device(
    apk_path: str,
    device_serial: Optional[str] = None,
    timeout: int = 90
) -> Dict[str, Any]:
    """
    Installs an operator-provided APK onto the target Android device via ADB.
    Resolves the target serial once, tracks before/after installed packages,
    and returns structured outcomes for success, offline device, storage error,
    signature mismatch, version downgrade, or device confirmation prompt.
    """
    if not os.path.exists(apk_path):
        return {
            "success": False,
            "status": InstallResultStatus.INVALID_APK,
            "message": f"APK file not found at path: {apk_path}",
            "device_serial": device_serial,
            "new_package": None,
        }

    # Resolve target device serial once
    serial = device_serial
    if not serial:
        devices = list_connected_devices()
        if devices:
            serial = devices[0]["serial"]
        else:
            return {
                "success": False,
                "status": InstallResultStatus.DEVICE_OFFLINE,
                "message": "No connected ADB devices found.",
                "device_serial": None,
                "new_package": None,
            }

    # Snapshot third-party packages before install to pinpoint newly installed package
    pkgs_before = get_third_party_packages(device_serial=serial)

    # Execute ADB install (-r allows reinstalling/updating existing package)
    cmd = ["adb"]
    if serial:
        cmd.extend(["-s", serial])
    cmd.extend(["install", "-r", apk_path])

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False
        )
        stdout = (proc.stdout or "").strip()
        stderr = (proc.stderr or "").strip()
        output = f"{stdout}\n{stderr}".strip()
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "status": InstallResultStatus.CONFIRMATION_REQUIRED,
            "message": "Installation timed out. Please check if your Android device is prompting 'Install via USB?' or requires on-screen confirmation.",
            "device_serial": serial,
            "new_package": None,
        }
    except Exception as e:
        return {
            "success": False,
            "status": InstallResultStatus.ERROR,
            "message": f"Failed to execute ADB install: {e}",
            "device_serial": serial,
            "new_package": None,
        }

    # Analyze ADB output
    out_lower = output.lower()
    if "success" in out_lower:
        # Detect newly installed package
        pkgs_after = get_third_party_packages(device_serial=serial)
        diff = pkgs_after - pkgs_before
        new_pkg = sorted(diff)[0] if diff else None

        return {
            "success": True,
            "status": InstallResultStatus.SUCCESS,
            "message": f"APK successfully installed on device '{serial}'.",
            "device_serial": serial,
            "new_package": new_pkg,
            "raw_output": output,
        }

    # Error classification
    if "install_failed_update_incompatible" in out_lower or "signatures do not match" in out_lower:
        return {
            "success": False,
            "status": InstallResultStatus.SIGNATURE_MISMATCH,
            "message": "Signature mismatch (INSTALL_FAILED_UPDATE_INCOMPATIBLE). The device already has this package installed with a different signature key.",
            "device_serial": serial,
            "new_package": None,
            "raw_output": output,
        }

    if "install_failed_version_downgrade" in out_lower:
        return {
            "success": False,
            "status": InstallResultStatus.DOWNGRADE_ERROR,
            "message": "Version downgrade rejected (INSTALL_FAILED_VERSION_DOWNGRADE). A higher version of this app is already installed.",
            "device_serial": serial,
            "new_package": None,
            "raw_output": output,
        }

    if "install_failed_insufficient_storage" in out_lower:
        return {
            "success": False,
            "status": InstallResultStatus.STORAGE_ERROR,
            "message": "Device storage full (INSTALL_FAILED_INSUFFICIENT_STORAGE). Free up space on device.",
            "device_serial": serial,
            "new_package": None,
            "raw_output": output,
        }

    if "user_restricted" in out_lower or "canceled_by_user" in out_lower or "blocked" in out_lower:
        return {
            "success": False,
            "status": InstallResultStatus.CONFIRMATION_REQUIRED,
            "message": "Installation blocked by user or device security settings. Please allow 'Install via USB' in Developer Options on device.",
            "device_serial": serial,
            "new_package": None,
            "raw_output": output,
        }

    if "device offline" in out_lower or "device not found" in out_lower or "no devices/emulators found" in out_lower:
        return {
            "success": False,
            "status": InstallResultStatus.DEVICE_OFFLINE,
            "message": f"Device '{serial}' went offline or was disconnected.",
            "device_serial": serial,
            "new_package": None,
            "raw_output": output,
        }

    return {
        "success": False,
        "status": InstallResultStatus.ERROR,
        "message": f"Installation failed: {output}",
        "device_serial": serial,
        "new_package": None,
        "raw_output": output,
    }


def scan_packages(device_serial: Optional[str] = None, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Scans device for all third-party and clone-candidate packages.
    Resolves the target device serial once and ensures the exact same serial is
    passed to every ADB command and database write.
    Persists newly discovered packages into the database inventory and returns candidates.
    """
    # Resolve serial once upfront
    serial = device_serial
    if not serial:
        devices = list_connected_devices()
        if devices:
            serial = devices[0]["serial"]
        else:
            serial = "default_device"

    # Query third-party packages and all packages matching clone patterns targeting the resolved serial
    raw_output = run_adb_command(["shell", "pm", "list", "packages", "-3"], device_serial=serial)
    all_packages = set()

    for line in raw_output.splitlines():
        line = line.strip()
        if line.startswith("package:"):
            all_packages.add(line.replace("package:", "").strip())

    # Also query specifically for instagram packages across all users/spaces on the resolved serial
    insta_output = run_adb_command(["shell", "pm", "list", "packages", "instagram"], device_serial=serial)
    for line in insta_output.splitlines():
        if line.startswith("package:"):
            all_packages.add(line.replace("package:", "").strip())

    # Note: official com.instagram.android is included above via `pm list packages instagram`
    # only when actually installed on this target device.
    foreground_pkg = get_foreground_package(device_serial=serial)

    # Load existing clones from DB to preserve confirmation state for THIS device serial
    existing_clones = {c["package_name"]: c for c in db.get_clones(device_serial=serial, db_path=db_path)}

    candidates = []

    for pkg in sorted(all_packages):
        is_official = (pkg == OFFICIAL_INSTAGRAM_PKG)
        is_clone_candidate = False

        pkg_lower = pkg.lower()
        for kw in CLONE_KEYWORDS:
            if kw in pkg_lower:
                is_clone_candidate = True
                break

        # Only present official Instagram or clone-candidate packages
        if not is_official and not is_clone_candidate:
            continue

        # Friendly App Name resolution
        if is_official:
            app_name = "Instagram (Official)"
            default_confirm = True
        elif "appcloner" in pkg_lower:
            app_name = f"AppCloner: {pkg.split('.')[-1]}"
            default_confirm = False
        elif "dual" in pkg_lower:
            app_name = f"DualSpace: {pkg.split('.')[-1]}"
            default_confirm = False
        elif "parallel" in pkg_lower:
            app_name = f"ParallelSpace: {pkg.split('.')[-1]}"
            default_confirm = False
        else:
            app_name = f"Instagram Clone ({pkg})"
            default_confirm = False

        # Check existing DB confirmation status
        if pkg in existing_clones:
            is_confirmed = bool(existing_clones[pkg]["is_confirmed"])
            app_name = existing_clones[pkg]["app_name"] or app_name
            clone_id = existing_clones[pkg]["id"]
        else:
            # Auto-confirm official, require confirmation for clones
            is_confirmed = default_confirm
            clone_id = db.upsert_clone(
                device_serial=serial,
                package_name=pkg,
                app_name=app_name,
                is_confirmed=is_confirmed,
                db_path=db_path,
            )

        candidates.append({
            "id": clone_id,
            "device_serial": serial,
            "package_name": pkg,
            "app_name": app_name,
            "is_official": is_official,
            "is_confirmed": is_confirmed,
            "is_foreground": (pkg == foreground_pkg),
        })

    return candidates
