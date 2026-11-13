"""
profile_capture.py - Explicit-device Profile Inspection & Onboarding Capture for Instoper.
Connects to an explicitly validated ADB device serial, launches a confirmed package,
navigates only to the signed-in owner's Profile tab when unambiguous, and extracts
handle, posts count, and followers count from accessible UI labels without storing raw XML.
"""

from decimal import Decimal, InvalidOperation
import re
import threading
import time
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional

import uiautomator2 as u2

import package_scanner

_NAV_WAIT_SEC = 1.2
_LAUNCH_READY_TIMEOUT_SEC = 90.0
_CAPTURE_READY_TIMEOUT_SEC = 90.0
_PROFILE_SETTLE_TIMEOUT_SEC = 15.0
_POLL_INTERVAL_SEC = 0.7
_MAX_RAW_LABELS = 25
_MAX_LABEL_LEN = 80

_LOCKS_GUARD = threading.Lock()
_DEVICE_LOCKS: Dict[str, threading.Lock] = {}

_HANDLE_STOP_WORDS = {
    "instagram",
    "profile",
    "home",
    "reels",
    "reel",
    "search",
    "explore",
    "notifications",
    "activity",
    "settings",
    "edit_profile",
    "edit.profile",
    "share_profile",
    "posts",
    "post",
    "followers",
    "follower",
    "following",
    "message",
    "follow",
    "dashboard",
    "professional",
    "highlights",
    "threads",
    "create",
    "menu",
    "options",
}

_HANDLE_REGEX = re.compile(r"^@?([A-Za-z0-9._]{1,30})$")
_PURE_INT_RE = re.compile(r"^\d+$")
_COMMA_INT_RE = re.compile(r"^\d{1,3}(?:,\d{3})+$")
_ABBREV_RE = re.compile(r"^(\d+(?:\.\d{1,2})?)\s*([kmbKMB])$")
_METRIC_UNIT_RE = re.compile(r"^(.*?)\s+(?:posts?|followers?|following)\s*$", re.IGNORECASE)
_POSTS_PAIRED_RE = re.compile(r"^\s*([^\s].*?)\s*posts?\s*$", re.IGNORECASE)
_FOLLOWERS_PAIRED_RE = re.compile(r"^\s*([^\s].*?)\s*followers?\s*$", re.IGNORECASE)

_MULTIPLIERS = {
    "K": Decimal("1000"),
    "M": Decimal("1000000"),
    "B": Decimal("1000000000"),
}


def get_device_lock(device_serial: str) -> threading.Lock:
    """Returns a per-serial mutex so operations targeting one phone never overlap."""
    with _LOCKS_GUARD:
        lock = _DEVICE_LOCKS.get(device_serial)
        if lock is None:
            lock = threading.Lock()
            _DEVICE_LOCKS[device_serial] = lock
        return lock


def parse_metric_count(raw_text: Optional[str]) -> Optional[int]:
    """
    Normalizes profile metric strings ('123', '1,234', '1.2K', '2M') into integers.
    Returns None for empty, negative, localized/ambiguous strings ('1,2 mil', '1,2 тыс.',
    '1,2K', '1.234', '5m ago'), or unrelated text rather than guessing.
    """
    if raw_text is None:
        return None
    text = str(raw_text).strip()
    if not text:
        return None

    # Strip an explicit trailing English metric noun ('1.2K followers' -> '1.2K')
    unit_match = _METRIC_UNIT_RE.match(text)
    if unit_match:
        text = unit_match.group(1).strip()

    if not text:
        return None

    if _PURE_INT_RE.match(text):
        return int(text)

    if _COMMA_INT_RE.match(text):
        return int(text.replace(",", ""))

    abbrev_match = _ABBREV_RE.match(text)
    if abbrev_match:
        num_str, suffix = abbrev_match.groups()
        try:
            value = Decimal(num_str) * _MULTIPLIERS[suffix.upper()]
            return int(value)
        except (InvalidOperation, ValueError):
            return None

    return None


def _parse_hierarchy_nodes(xml_str: Optional[str]) -> List[Dict[str, str]]:
    if not xml_str or not str(xml_str).strip():
        return []
    root = ET.fromstring(xml_str)
    nodes: List[Dict[str, str]] = []
    for elem in root.iter("node"):
        nodes.append({
            "package": (elem.attrib.get("package") or "").strip(),
            "text": (elem.attrib.get("text") or "").strip(),
            "content_desc": (elem.attrib.get("content-desc") or "").strip(),
            "resource_id": (elem.attrib.get("resource-id") or "").strip(),
            "clickable": (elem.attrib.get("clickable") or "").lower(),
            "selected": (elem.attrib.get("selected") or "").lower(),
        })
    return nodes


def _collect_bounded_labels(nodes: List[Dict[str, str]], package_name: Optional[str] = None) -> List[str]:
    labels: List[str] = []
    seen = set()
    for node in nodes:
        if "systemui" in node["resource_id"] or "systemui" in node.get("package", ""):
            continue
        if package_name and node.get("package") and node["package"] != package_name:
            continue
        for candidate in (node["text"], node["content_desc"]):
            if not candidate:
                continue
            clipped = candidate[:_MAX_LABEL_LEN]
            key = clipped.casefold()
            if key in seen:
                continue
            seen.add(key)
            labels.append(clipped)
            if len(labels) >= _MAX_RAW_LABELS:
                return labels
    return labels


_KEYGUARD_HINTS = (
    "keyguard",
    "lock_icon",
    "lockscreen",
    "bouncer",
    "pinentry",
    "passwordentry",
    "notification_stack_scroller",
    "qs_panel",
    "quick_settings",
)

_SAFE_STARTUP_DISMISS_LABELS = {
    "not now",
    "skip",
    "later",
    "no thanks",
    "no, thanks",
    "dismiss",
}

_STARTUP_DISMISS_DENY_TERMS = (
    "login_username",
    "login_password",
    ":id/password",
    "log in",
    "forgot password",
    "create new account",
    "confirm it's you",
    "security code",
    "two-factor",
    "authenticator app",
    "captcha",
    "i'm not a robot",
    "suspended",
    "disabled",
)


def _safe_app_current(d: Any) -> Dict[str, Any]:
    try:
        return d.app_current() or {}
    except Exception:
        return {}


def _safe_dump_nodes(d: Any) -> List[Dict[str, str]]:
    try:
        return _parse_hierarchy_nodes(d.dump_hierarchy())
    except Exception:
        return []


def _record_step(steps: List[str], step: str) -> None:
    if step and (not steps or steps[-1] != step):
        steps.append(step)


def _is_transient_launcher_pkg(package_name: Optional[str]) -> bool:
    if not package_name:
        return False
    low = package_name.lower()
    return "launcher" in low or "quickstep" in low


def _has_keyguard_or_shade_overlay(nodes: List[Dict[str, str]], curr_pkg: Optional[str]) -> bool:
    if curr_pkg == "com.android.systemui":
        return True
    for node in nodes:
        pkg_l = (node.get("package") or "").lower()
        rid_l = (node.get("resource_id") or "").lower()
        if "systemui" in pkg_l or "systemui" in rid_l:
            if any(hint in rid_l for hint in _KEYGUARD_HINTS):
                return True
    return False


def _is_covered_by_systemui(nodes: List[Dict[str, str]], package_name: str) -> bool:
    packages = {n.get("package", "") for n in nodes if n.get("package")}
    if not packages:
        return False
    return package_name not in packages and any("systemui" in p for p in packages)


def _find_safe_startup_dismiss_node(nodes: List[Dict[str, str]], package_name: str) -> Optional[Dict[str, str]]:
    """
    Finds an innocuous dismiss button ('Not now', 'Skip', 'Dismiss') on benign
    startup overlays such as 'Save your login info?' or notification prompts.
    Never matches when login fields or security/challenge keywords exist on screen.
    """
    app_nodes = [n for n in nodes if not n.get("package") or n["package"] == package_name]
    if not app_nodes:
        return None
    joined_lower = " | ".join(
        f"{n['text']} {n['content_desc']} {n['resource_id']}".strip().lower()
        for n in app_nodes
        if "systemui" not in n["resource_id"]
    )
    if any(term in joined_lower for term in _STARTUP_DISMISS_DENY_TERMS):
        return None

    for require_clickable in (True, False):
        for node in app_nodes:
            if require_clickable and node.get("clickable") != "true":
                continue
            text_l = node["text"].strip().casefold()
            desc_l = node["content_desc"].strip().casefold()
            if text_l in _SAFE_STARTUP_DISMISS_LABELS or desc_l in _SAFE_STARTUP_DISMISS_LABELS:
                return node
    return None


def _click_hierarchy_node(d: Any, node: Dict[str, str]) -> bool:
    rid = node.get("resource_id") or ""
    text = node.get("text") or ""
    desc = node.get("content_desc") or ""
    for selector_kwargs in (
        {"resourceId": rid} if rid else None,
        {"text": text} if text else None,
        {"description": desc} if desc else None,
    ):
        if not selector_kwargs:
            continue
        try:
            sel = d(**selector_kwargs)
            if sel.exists:
                sel.click()
                return True
        except Exception:
            continue
    return False


def _classify_screen_state(
    nodes: List[Dict[str, str]],
    package_name: str,
    curr_pkg: Optional[str],
) -> tuple[str, Dict[str, Any]]:
    """
    Classifies the current Android UI screen into a deterministic state token so
    launch & capture wait reliably until real app UI renders or a genuine blocker occurs.
    """
    app_nodes = [n for n in nodes if n.get("package") == package_name]
    packages = {n.get("package", "") for n in nodes if n.get("package")}
    has_app_nodes = len(app_nodes) > 0
    in_foreground = has_app_nodes or curr_pkg == package_name

    # Explicit foreground mismatch when another non-launcher, non-systemui app owns screen
    if (
        curr_pkg
        and curr_pkg != package_name
        and curr_pkg != "com.android.systemui"
        and not _is_transient_launcher_pkg(curr_pkg)
        and not has_app_nodes
    ):
        return "wrong_package_foreground", {"curr_pkg": curr_pkg}

    confirmed_lockscreen = _has_keyguard_or_shade_overlay(nodes, curr_pkg)
    if confirmed_lockscreen and not has_app_nodes:
        return "systemui_locked", {"confirmed_lockscreen": True}

    if _is_covered_by_systemui(nodes, package_name):
        # Status/nav bar alone while target package is actively launching is splash loading, not lockscreen
        if curr_pkg == package_name or _is_transient_launcher_pkg(curr_pkg):
            return "splash_or_loading", {"covered_by_systemui": True, "confirmed_lockscreen": False}
        return "systemui_locked", {"confirmed_lockscreen": confirmed_lockscreen}

    if not in_foreground:
        return "splash_or_loading", {"curr_pkg": curr_pkg}

    auth_issue = _detect_auth_or_challenge_state(nodes, package_name)
    if auth_issue:
        return auth_issue, {}

    if _is_on_profile_screen(nodes, package_name):
        return "profile_screen", {}

    if _has_auth_tabs(nodes, package_name):
        return "home_or_tab_screen", {}

    dismiss_node = _find_safe_startup_dismiss_node(nodes, package_name)
    if dismiss_node is not None:
        return "startup_prompt", {"dismiss_node": dismiss_node}

    # Empty or minimal container tree during cold start splash animation
    if packages and len(app_nodes) <= 8:
        return "splash_or_loading", {"node_count": len(app_nodes)}
    if not nodes:
        return "splash_or_loading", {"node_count": 0}

    return "unknown_in_app", {"node_count": len(app_nodes)}


def _has_auth_tabs(nodes: List[Dict[str, str]], package_name: str) -> bool:
    for node in nodes:
        rid = node["resource_id"]
        desc_l = node["content_desc"].casefold()
        if rid in (
            f"{package_name}:id/feed_tab",
            f"{package_name}:id/profile_tab",
            f"{package_name}:id/clips_tab",
            f"{package_name}:id/search_tab",
        ) or rid.endswith((":id/feed_tab", ":id/profile_tab")):
            return True
        if desc_l in ("home", "profile") and node["clickable"] == "true":
            return True
    return False


def _detect_auth_or_challenge_state(nodes: List[Dict[str, str]], package_name: str) -> Optional[str]:
    joined_lower = " | ".join(
        f"{n['text']} {n['content_desc']}".strip().lower()
        for n in nodes
        if "systemui" not in n["resource_id"]
    )
    has_tabs = _has_auth_tabs(nodes, package_name)

    # Check challenge banners/screens even if overlays appear
    challenge_terms = (
        "confirm it's you",
        "confirm you own this account",
        "help us confirm",
        "enter confirmation code",
        "security code",
        "two-factor",
        "authenticator app",
        "we suspended",
        "your account has been disabled",
        "verify your account",
        "unusual login",
        "check your email",
        "i'm not a robot",
        "captcha",
    )
    if any(term in joined_lower for term in challenge_terms):
        return "challenge_required"

    # Explicit login fields mean signed out
    for node in nodes:
        rid = node["resource_id"].lower()
        if any(k in rid for k in ("login_username", "login_password", ":id/password")):
            return "signed_out"

    if not has_tabs:
        signed_out_terms = (
            "phone number, username, or email",
            "forgot password",
            "create new account",
            "use another profile",
            "log in to another account",
        )
        if any(term in joined_lower for term in signed_out_terms):
            return "signed_out"
        for node in nodes:
            if node["text"].strip().lower() == "log in" and node["clickable"] == "true":
                return "signed_out"

    return None


def _is_on_profile_screen(nodes: List[Dict[str, str]], package_name: str) -> bool:
    for node in nodes:
        rid = node["resource_id"]
        text_l = node["text"].casefold()
        desc_l = node["content_desc"].casefold()
        if (rid == f"{package_name}:id/profile_tab" or rid.endswith(":id/profile_tab")) and node["selected"] == "true":
            return True
        if desc_l == "profile" and node["selected"] == "true":
            return True
        if any(
            token in rid
            for token in (
                "row_profile_header_textview_post_count",
                "row_profile_header_textview_followers_count",
                "profile_header_familiar_post_count_value",
                "profile_header_familiar_followers_count_value",
                "profile_header_container",
            )
        ):
            return True
        if text_l in ("edit profile", "share profile"):
            return True
    return False


def _find_unambiguous_profile_tabs(nodes: List[Dict[str, str]], package_name: str) -> List[Dict[str, str]]:
    candidates: List[Dict[str, str]] = []
    seen_keys = set()
    for node in nodes:
        rid = node["resource_id"]
        desc_l = node["content_desc"].casefold()
        is_profile_rid = rid == f"{package_name}:id/profile_tab" or rid.endswith(":id/profile_tab")
        is_profile_desc = desc_l == "profile" and node["clickable"] == "true"
        if is_profile_rid or is_profile_desc:
            dedup_key = (rid, desc_l) if rid else ("", desc_l, len(candidates))
            if rid and dedup_key in seen_keys:
                continue
            seen_keys.add(dedup_key)
            candidates.append(node)
    return candidates


def _is_valid_handle_candidate(raw: str) -> Optional[str]:
    if not raw:
        return None
    m = _HANDLE_REGEX.match(raw.strip())
    if not m:
        return None
    handle = m.group(1)
    if handle.isdigit():
        return None
    if handle.casefold() in _HANDLE_STOP_WORDS:
        return None
    return handle


def _extract_profile_handle(nodes: List[Dict[str, str]], package_name: str) -> Optional[str]:
    # 1. Explicit header username resource IDs
    priority_rids = (
        f"{package_name}:id/action_bar_title",
        f"{package_name}:id/action_bar_large_title_auto_size",
        f"{package_name}:id/action_bar_title_chevron",
        f"{package_name}:id/profile_header_username",
    )
    for target_rid in priority_rids:
        for node in nodes:
            if node["resource_id"] == target_rid:
                handle = _is_valid_handle_candidate(node["text"]) or _is_valid_handle_candidate(node["content_desc"])
                if handle:
                    return handle

    # 2. Suffix matches for cloned builds whose prefix changed
    suffixes = (
        ":id/action_bar_title",
        ":id/action_bar_large_title_auto_size",
        ":id/action_bar_title_chevron",
        ":id/profile_header_username",
        ":id/username",
    )
    for node in nodes:
        rid = node["resource_id"]
        if "systemui" in rid:
            continue
        if any(rid.endswith(sfx) for sfx in suffixes):
            handle = _is_valid_handle_candidate(node["text"]) or _is_valid_handle_candidate(node["content_desc"])
            if handle:
                return handle

    return None


def _extract_profile_counts(nodes: List[Dict[str, str]]) -> Dict[str, Optional[int]]:
    posts_count: Optional[int] = None
    followers_count: Optional[int] = None

    post_rids = (
        "row_profile_header_textview_post_count",
        "profile_header_familiar_post_count_value",
    )
    follower_rids = (
        "row_profile_header_textview_followers_count",
        "profile_header_familiar_followers_count_value",
    )

    # 1. Direct Instagram profile metric resource IDs
    for node in nodes:
        rid = node["resource_id"]
        if any(k in rid for k in post_rids) and posts_count is None:
            posts_count = parse_metric_count(node["text"] or node["content_desc"])
        if any(k in rid for k in follower_rids) and followers_count is None:
            followers_count = parse_metric_count(node["text"] or node["content_desc"])

    # 2. Combined labels such as '123 posts' / '1.2K followers'
    for node in nodes:
        for val in (node["text"], node["content_desc"]):
            if not val:
                continue
            if posts_count is None:
                m_post = _POSTS_PAIRED_RE.match(val)
                if m_post:
                    posts_count = parse_metric_count(m_post.group(1))
            if followers_count is None:
                m_fol = _FOLLOWERS_PAIRED_RE.match(val)
                if m_fol:
                    followers_count = parse_metric_count(m_fol.group(1))

    # 3. Adjacent stats-row pairing ('123' followed immediately by 'posts')
    for idx, node in enumerate(nodes):
        label = (node["text"] or node["content_desc"]).strip().casefold()
        if label in ("posts", "post") and posts_count is None and idx > 0:
            prev_val = nodes[idx - 1]["text"] or nodes[idx - 1]["content_desc"]
            posts_count = parse_metric_count(prev_val)
        elif label in ("followers", "follower") and followers_count is None and idx > 0:
            prev_val = nodes[idx - 1]["text"] or nodes[idx - 1]["content_desc"]
            followers_count = parse_metric_count(prev_val)

    return {"posts_count": posts_count, "followers_count": followers_count}


def _build_capture_response(
    *,
    status: str,
    serial: str,
    pkg: str,
    profile_handle: Optional[str] = None,
    posts_count: Optional[int] = None,
    followers_count: Optional[int] = None,
    raw_labels: Optional[List[str]] = None,
    needs_manual_profile_navigation: bool = False,
    message: str = "",
    screen_state: Optional[str] = None,
    verification_steps: Optional[List[str]] = None,
) -> Dict[str, Any]:
    return {
        "status": status,
        "device_serial": serial,
        "package_name": pkg,
        "profile_handle": profile_handle,
        "posts_count": posts_count,
        "followers_count": followers_count,
        "raw_labels": raw_labels if raw_labels is not None else [],
        "needs_manual_profile_navigation": needs_manual_profile_navigation,
        "message": message,
        "screen_state": screen_state or status,
        "verification_steps": list(verification_steps or []),
    }


def open_profile_app(
    device_serial: str,
    package_name: str,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Validates the selected device is online and the package is installed & confirmed
    for that exact serial, launches the package via uiautomator2, and waits until
    app UI leaves splash/launcher or hits a verified blocker state.
    """
    validated = package_scanner.validate_confirmed_package(
        device_serial=device_serial,
        package_name=package_name,
        db_path=db_path,
    )
    serial = validated["device_serial"]
    pkg = validated["package_name"]

    with get_device_lock(serial):
        d = u2.connect(serial)
        d.app_start(pkg, stop=False)
        steps: List[str] = ["app_start_requested"]
        last_state = "splash_or_loading"

        polling = _NAV_WAIT_SEC > 0
        if polling:
            time.sleep(min(_NAV_WAIT_SEC, 1.0))
            deadline = time.monotonic() + _LAUNCH_READY_TIMEOUT_SEC
            retried_start = False
            lock_hits = 0
            start_ts = time.monotonic()

            while True:
                curr_pkg = _safe_app_current(d).get("package")
                nodes = _safe_dump_nodes(d)
                state, ctx = _classify_screen_state(nodes, pkg, curr_pkg)
                last_state = state
                _record_step(steps, f"screen:{state}")

                if state in (
                    "profile_screen",
                    "home_or_tab_screen",
                    "startup_prompt",
                    "signed_out",
                    "challenge_required",
                ):
                    break

                if state == "systemui_locked" and ctx.get("confirmed_lockscreen"):
                    lock_hits += 1
                    if lock_hits >= 2:
                        break
                else:
                    lock_hits = 0

                elapsed = time.monotonic() - start_ts
                if not retried_start and elapsed >= 3.5 and state in ("wrong_package_foreground", "splash_or_loading"):
                    app_nodes = [n for n in nodes if n.get("package") == pkg]
                    if not app_nodes and curr_pkg != pkg:
                        d.app_start(pkg, stop=False)
                        retried_start = True
                        _record_step(steps, "retried_app_start")

                if time.monotonic() >= deadline:
                    break
                time.sleep(_POLL_INTERVAL_SEC)

            if last_state in ("splash_or_loading", "wrong_package_foreground"):
                try:
                    d.app_stop(pkg)
                except Exception:
                    pass
                return {
                    "status": "launch_timeout",
                    "device_serial": serial,
                    "package_name": pkg,
                    "app_name": validated["app_name"],
                    "screen_state": last_state,
                    "verification_steps": steps,
                    "message": f"'{pkg}' did not finish opening within {_LAUNCH_READY_TIMEOUT_SEC:.0f} seconds. It was stopped; check the phone and retry.",
                }

        return {
            "status": "launched",
            "device_serial": serial,
            "package_name": pkg,
            "app_name": validated["app_name"],
            "screen_state": last_state,
            "verification_steps": steps,
            "message": f"Launched '{pkg}' on device '{serial}' (verified screen: {last_state}).",
        }


def capture_profile(
    device_serial: str,
    package_name: str,
    expected_username: Optional[str] = None,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Performs screen-by-screen verification of a validated device/package via uiautomator2.
    Waits through cold-start splash animations, safely dismisses benign non-auth
    overlays ('Not now'/'Dismiss'), navigates to Profile only via one unambiguous
    selector, and settles before returning captured handle/counts or a verified blocker.
    """
    validated = package_scanner.validate_confirmed_package(
        device_serial=device_serial,
        package_name=package_name,
        db_path=db_path,
    )
    serial = validated["device_serial"]
    pkg = validated["package_name"]

    with get_device_lock(serial):
        d = u2.connect(serial)
        polling = _NAV_WAIT_SEC > 0
        steps: List[str] = []
        dismiss_attempts = 0
        lock_hits = 0
        start_ts = time.monotonic()
        deadline = start_ts + (_CAPTURE_READY_TIMEOUT_SEC if polling else 0.0)

        nodes: List[Dict[str, str]] = []
        raw_labels: List[str] = []
        state = "splash_or_loading"
        ctx: Dict[str, Any] = {}

        # Phase 1: Verify foreground app & wait until splash / benign prompts resolve
        while True:
            curr_pkg = _safe_app_current(d).get("package")
            nodes = _safe_dump_nodes(d)
            state, ctx = _classify_screen_state(nodes, pkg, curr_pkg)
            _record_step(steps, f"screen:{state}")

            if state == "wrong_package_foreground":
                if polling and (time.monotonic() - start_ts) < 4.0 and time.monotonic() < deadline:
                    time.sleep(_POLL_INTERVAL_SEC)
                    continue
                wrong_pkg = ctx.get("curr_pkg") or curr_pkg
                return _build_capture_response(
                    status="wrong_package_foreground",
                    serial=serial,
                    pkg=pkg,
                    raw_labels=[],
                    needs_manual_profile_navigation=False,
                    screen_state="wrong_package_foreground",
                    verification_steps=steps,
                    message=f"Foreground app is '{wrong_pkg}', expected '{pkg}'. Launch or switch to '{pkg}' on '{serial}' and retry.",
                )

            if state == "systemui_locked":
                if polling and not ctx.get("confirmed_lockscreen") and time.monotonic() < deadline:
                    time.sleep(_POLL_INTERVAL_SEC)
                    continue
                if polling and ctx.get("confirmed_lockscreen") and lock_hits < 1 and time.monotonic() < deadline:
                    lock_hits += 1
                    time.sleep(_POLL_INTERVAL_SEC)
                    continue
                return _build_capture_response(
                    status="needs_manual_profile_navigation",
                    serial=serial,
                    pkg=pkg,
                    raw_labels=[],
                    needs_manual_profile_navigation=True,
                    screen_state="systemui_locked",
                    verification_steps=steps,
                    message="Device screen appears locked or covered by system UI (lock screen/notifications). Unlock the phone, open your Profile tab in the selected app, and tap Capture again.",
                )

            raw_labels = _collect_bounded_labels(nodes, pkg)

            if state in ("signed_out", "challenge_required"):
                msg = (
                    "Selected app appears signed out. Sign in manually on the phone, open your Profile tab, and retry capture."
                    if state == "signed_out"
                    else "Security checkpoint or challenge detected on screen. Resolve it manually on the phone, then retry capture."
                )
                return _build_capture_response(
                    status=state,
                    serial=serial,
                    pkg=pkg,
                    raw_labels=raw_labels,
                    needs_manual_profile_navigation=False,
                    screen_state=state,
                    verification_steps=steps,
                    message=msg,
                )

            if state == "startup_prompt" and dismiss_attempts < 2:
                dismiss_node = ctx.get("dismiss_node")
                if dismiss_node and _click_hierarchy_node(d, dismiss_node):
                    dismiss_attempts += 1
                    _record_step(steps, "dismissed_startup_prompt")
                    if polling:
                        time.sleep(min(_NAV_WAIT_SEC, 1.0))
                    continue

            if state in ("profile_screen", "home_or_tab_screen"):
                break

            if polling and time.monotonic() < deadline:
                time.sleep(_POLL_INTERVAL_SEC)
                continue
            break

        # Finalize timeout states from Phase 1 cleanly
        if state == "splash_or_loading":
            if ctx.get("covered_by_systemui"):
                return _build_capture_response(
                    status="needs_manual_profile_navigation",
                    serial=serial,
                    pkg=pkg,
                    raw_labels=[],
                    needs_manual_profile_navigation=True,
                    screen_state="systemui_locked",
                    verification_steps=steps,
                    message="Device screen appears locked or covered by system UI (lock screen/notifications). Unlock the phone, open your Profile tab in the selected app, and tap Capture again.",
                )
            return _build_capture_response(
                status="needs_manual_profile_navigation",
                serial=serial,
                pkg=pkg,
                raw_labels=raw_labels,
                needs_manual_profile_navigation=True,
                screen_state="splash_timeout",
                verification_steps=steps,
                message=f"'{pkg}' remained on its splash or loading screen. Wait for it to finish loading, open your Profile tab, and tap Capture again.",
            )

        # Phase 2: Navigate to Profile tab if not already viewing own Profile
        if not _is_on_profile_screen(nodes, pkg):
            tab_candidates = _find_unambiguous_profile_tabs(nodes, pkg)
            if len(tab_candidates) != 1:
                return _build_capture_response(
                    status="needs_manual_profile_navigation",
                    serial=serial,
                    pkg=pkg,
                    raw_labels=raw_labels,
                    needs_manual_profile_navigation=True,
                    screen_state=state,
                    verification_steps=steps,
                    message="Could not uniquely locate the Profile tab. Open your own Profile tab manually on the phone and tap Capture again, or enter your handle manually.",
                )

            cand = tab_candidates[0]
            selector = d(resourceId=cand["resource_id"]) if cand["resource_id"] else d(description=cand["content_desc"])
            if not selector.exists:
                return _build_capture_response(
                    status="needs_manual_profile_navigation",
                    serial=serial,
                    pkg=pkg,
                    raw_labels=raw_labels,
                    needs_manual_profile_navigation=True,
                    screen_state="profile_tab_missing",
                    verification_steps=steps,
                    message="Profile tab selector could not be activated. Open your own Profile tab manually on the phone and retry capture.",
                )

            selector.click()
            _record_step(steps, "clicked_profile_tab")
            if polling:
                time.sleep(min(_NAV_WAIT_SEC, 1.0))

            settle_deadline = time.monotonic() + (_PROFILE_SETTLE_TIMEOUT_SEC if polling else 0.0)
            handle_seen_at: Optional[float] = None
            while True:
                nodes = _safe_dump_nodes(d)
                raw_labels = _collect_bounded_labels(nodes, pkg)

                auth_issue = _detect_auth_or_challenge_state(nodes, pkg)
                if auth_issue:
                    _record_step(steps, f"screen:{auth_issue}")
                    return _build_capture_response(
                        status=auth_issue,
                        serial=serial,
                        pkg=pkg,
                        raw_labels=raw_labels,
                        needs_manual_profile_navigation=False,
                        screen_state=auth_issue,
                        verification_steps=steps,
                        message="App paused on login or challenge screen. Resolve manually on the phone and retry capture.",
                    )

                if dismiss_attempts < 2:
                    prompt_node = _find_safe_startup_dismiss_node(nodes, pkg)
                    if prompt_node and _click_hierarchy_node(d, prompt_node):
                        dismiss_attempts += 1
                        _record_step(steps, "dismissed_profile_prompt")
                        if polling:
                            time.sleep(_POLL_INTERVAL_SEC)
                        continue

                on_prof = _is_on_profile_screen(nodes, pkg)
                cur_handle = _extract_profile_handle(nodes, pkg)
                cur_counts = _extract_profile_counts(nodes)
                has_both_counts = (
                    cur_counts["posts_count"] is not None and cur_counts["followers_count"] is not None
                )
                if cur_handle and handle_seen_at is None:
                    handle_seen_at = time.monotonic()

                if on_prof and cur_handle and (has_both_counts or not polling):
                    _record_step(steps, "verified_profile_screen")
                    break
                if not polling or time.monotonic() >= settle_deadline:
                    break
                time.sleep(0.6)
        else:
            # Already on Profile screen: allow brief settle if header/counts are mid-render
            if polling:
                settle_deadline = time.monotonic() + _PROFILE_SETTLE_TIMEOUT_SEC
                while time.monotonic() < settle_deadline:
                    observed_try = _extract_profile_handle(nodes, pkg)
                    counts_try = _extract_profile_counts(nodes)
                    if observed_try and counts_try["posts_count"] is not None and counts_try["followers_count"] is not None:
                        break
                    time.sleep(0.6)
                    nodes = _safe_dump_nodes(d)
                    raw_labels = _collect_bounded_labels(nodes, pkg)
            _record_step(steps, "verified_profile_screen")

        observed_handle = _extract_profile_handle(nodes, pkg)
        if not observed_handle:
            return _build_capture_response(
                status="needs_manual_profile_navigation",
                serial=serial,
                pkg=pkg,
                raw_labels=raw_labels,
                needs_manual_profile_navigation=True,
                screen_state="profile_handle_missing",
                verification_steps=steps,
                message="Signed-in profile handle was not detected on screen. Navigate to your Profile tab manually and retry capture, or enter the handle below.",
            )

        counts = _extract_profile_counts(nodes)

        if expected_username:
            clean_expected = expected_username.strip().lstrip("@").casefold()
            if observed_handle.lstrip("@").casefold() != clean_expected:
                _record_step(steps, "handle_mismatch")
                return _build_capture_response(
                    status="handle_mismatch",
                    serial=serial,
                    pkg=pkg,
                    profile_handle=observed_handle,
                    posts_count=counts["posts_count"],
                    followers_count=counts["followers_count"],
                    raw_labels=raw_labels,
                    needs_manual_profile_navigation=False,
                    screen_state="handle_mismatch",
                    verification_steps=steps,
                    message=f"Visible profile '@{observed_handle}' does not match selected account '@{expected_username.strip().lstrip('@')}'. Switch accounts on the phone before confirming.",
                )

        status = "captured"
        partial_note = ""
        if counts["posts_count"] is None or counts["followers_count"] is None:
            status = "profile_metrics_incomplete"
            partial_note = " Some profile counts could not be parsed cleanly and remain blank for manual review."

        _record_step(steps, status)
        return _build_capture_response(
            status=status,
            serial=serial,
            pkg=pkg,
            profile_handle=observed_handle,
            posts_count=counts["posts_count"],
            followers_count=counts["followers_count"],
            raw_labels=raw_labels,
            needs_manual_profile_navigation=False,
            screen_state="profile_screen",
            verification_steps=steps,
            message=f"Captured profile '@{observed_handle}' from '{pkg}'. Review values before saving.{partial_note}",
        )


def close_profile_app(
    device_serial: str,
    package_name: str,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Force-stop only the confirmed clone selected on the explicit device."""
    validated = package_scanner.validate_confirmed_package(
        device_serial=device_serial,
        package_name=package_name,
        db_path=db_path,
    )
    serial = validated["device_serial"]
    pkg = validated["package_name"]

    with get_device_lock(serial):
        device = u2.connect(serial)
        device.app_stop(pkg)

    return {
        "status": "closed",
        "device_serial": serial,
        "package_name": pkg,
        "message": f"Closed '{pkg}' on device '{serial}'.",
    }


def capture_profile_until_terminal(
    device_serial: str,
    package_name: str,
    expected_username: Optional[str] = None,
    db_path: Optional[str] = None,
    timeout_sec: float = 20.0,
    poll_interval_sec: float = 2.0,
) -> Dict[str, Any]:
    """
    Polls capture_profile while transient loading states ('loading', 'launching')
    are reported, and returns immediately once a profile capture or terminal blocker
    (signed_out, challenge_required, handle_mismatch, wrong_package_foreground,
    needs_manual_profile_navigation) is reached or timeout expires.
    """
    deadline = time.monotonic() + max(0.0, float(timeout_sec))
    wait_step = max(0.1, float(poll_interval_sec))
    while True:
        res = capture_profile(
            device_serial=device_serial,
            package_name=package_name,
            expected_username=expected_username,
            db_path=db_path,
        )
        if res.get("status") not in ("loading", "launching"):
            return res
        if time.monotonic() >= deadline:
            return res
        time.sleep(wait_step)


def close_profile_app(
    device_serial: str,
    package_name: str,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Validates that package_name is installed and confirmed on device_serial,
    then cleanly stops only that exact package before moving to the next clone.
    """
    validated = package_scanner.validate_confirmed_package(
        device_serial=device_serial,
        package_name=package_name,
        db_path=db_path,
    )
    serial = validated["device_serial"]
    pkg = validated["package_name"]

    with get_device_lock(serial):
        d = u2.connect(serial)
        d.app_stop(pkg)
        return {
            "status": "closed",
            "device_serial": serial,
            "package_name": pkg,
            "app_name": validated["app_name"],
            "message": f"Stopped '{pkg}' on device '{serial}'.",
        }
