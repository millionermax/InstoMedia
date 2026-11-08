"""
package_scanner.py - ADB Package Discovery & Clone Inventory Engine for Instoper.
Discovers installed Instagram packages and clone candidates on Android devices,
tracks operator confirmation status, and verifies package availability.
"""

import re
import subprocess
from typing import List, Dict, Any, Optional

import db

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


def list_connected_devices() -> List[Dict[str, str]]:
    """Returns a list of connected ADB devices with serial and status."""
    output = run_adb_command(["devices"])
    devices = []
    lines = output.splitlines()
    for line in lines[1:]:
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) >= 2:
            devices.append({
                "serial": parts[0],
                "status": parts[1]
            })
    return devices


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


def scan_packages(device_serial: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Scans device for all third-party and clone-candidate packages.
    Persists newly discovered packages into the database inventory and returns candidates.
    """
    # If no serial provided, pick first connected device if available
    serial = device_serial
    if not serial:
        devices = list_connected_devices()
        if devices:
            serial = devices[0]["serial"]
        else:
            serial = "default_device"

    # Query third-party packages and all packages matching clone patterns
    raw_output = run_adb_command(["shell", "pm", "list", "packages", "-3"], device_serial=device_serial)
    all_packages = set()

    for line in raw_output.splitlines():
        line = line.strip()
        if line.startswith("package:"):
            all_packages.add(line.replace("package:", "").strip())

    # Also query specifically for instagram packages across all users/spaces
    insta_output = run_adb_command(["shell", "pm", "list", "packages", "instagram"], device_serial=device_serial)
    for line in insta_output.splitlines():
        if line.startswith("package:"):
            all_packages.add(line.replace("package:", "").strip())

    # Always ensure official package is evaluated
    all_packages.add(OFFICIAL_INSTAGRAM_PKG)

    foreground_pkg = get_foreground_package(device_serial=device_serial)

    # Load existing clones from DB to preserve confirmation state
    existing_clones = {c["package_name"]: c for c in db.get_clones(device_serial=serial)}

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
                is_confirmed=is_confirmed
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
