import argparse
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from urllib.parse import urlparse, urlunparse
import uiautomator2 as u2

from human_behavior import HumanBehaviorEngine

# Default Configuration
DEFAULT_AUDIO_URL = "https://www.instagram.com/reels/audio/1656648626250751/"
DEFAULT_CAPTION = "Automated Reel upload! #automation #tech"
PACKAGE_NAME = "com.instagram.android"


class InstagramReelUploader:
    def __init__(self, device_serial=None, package_name=PACKAGE_NAME, log_callback=None):
        self.package_name = (package_name or PACKAGE_NAME).strip() or PACKAGE_NAME
        self.log_callback = log_callback or self._default_log
        self.is_cancelled = False
        if device_serial is not None and not str(device_serial).strip():
            raise ValueError("Explicit device_serial cannot be empty.")
        self.device_serial = str(device_serial).strip() if device_serial else None
        self.username = None
        self._staged_video_path = None
        self.evidence = {
            "target_package": self.package_name,
            "foreground_package": None,
            "foreground_activity": None,
            "intent_result": None,
            "gallery_selector": None,
            "selected_video": None,
        }

        target_desc = f"device {self.device_serial} ({self.package_name})" if self.device_serial else f"default device ({self.package_name})"
        self.log("info", f"Connecting to {target_desc} via ADB...")
        try:
            self.d = u2.connect(self.device_serial) if self.device_serial else u2.connect()
            self.device_name = self.d.info.get("productName", "Unknown")
            # Initialize Human Biometrics & Anti-Bot Engine
            self.human = HumanBehaviorEngine(self.d)
            self.log("success", f"Connected: {self.device_name} (Serial: {self.d.serial}) | Anti-Bot Human Engine: ACTIVE")
            self._ensure_device_permissions()
            self._detect_username()
        except Exception as e:
            self.log("error", f"Failed to connect to ADB device: {e}")
            raise

    def _ensure_device_permissions(self):
        """Pre-emptively grants standard runtime permissions to target clone via ADB shell pm grant to avoid dialog stalls."""
        if not self.package_name or not hasattr(self, "d") or not self.d:
            return
        perms = [
            "android.permission.CAMERA",
            "android.permission.READ_EXTERNAL_STORAGE",
            "android.permission.WRITE_EXTERNAL_STORAGE",
            "android.permission.RECORD_AUDIO",
            "android.permission.READ_MEDIA_IMAGES",
            "android.permission.READ_MEDIA_VIDEO",
        ]
        for p in perms:
            try:
                self.d.shell(f"pm grant {self.package_name} {p}")
            except Exception:
                pass

    @staticmethod
    def normalize_audio_url(audio_url):
        """Validates Instagram audio URL and upgrades plain http:// links to https://."""
        cleaned = str(audio_url or "").strip()
        if not cleaned:
            raise ValueError("Instagram audio URL is required.")
        parsed = urlparse(cleaned)
        scheme = (parsed.scheme or "").lower()
        host = (parsed.hostname or "").lower()
        path = parsed.path or ""
        if scheme not in ("http", "https") or not (host == "instagram.com" or host.endswith(".instagram.com")):
            raise ValueError("Audio URL must be a valid https://www.instagram.com/reels/audio/... link.")
        if not any(
            path.startswith(pfx) and len(path.rstrip("/")) >= len(pfx.rstrip("/")) + 1 and len(path) > len(pfx)
            for pfx in ("/reels/audio/", "/audio/")
        ):
            raise ValueError("Audio URL must point to an Instagram audio track path (/reels/audio/<id>/).")
        if scheme == "http":
            return urlunparse(parsed._replace(scheme="https"))
        return cleaned

    @staticmethod
    def _parse_shell_result(res):
        """Normalizes uiautomator2 ShellResponse, tuple, or string results into (exit_code, output)."""
        if res is None:
            return None, ""
        if isinstance(res, tuple):
            output = str(res[0] if len(res) > 0 and res[0] is not None else "").strip()
            exit_code = res[1] if len(res) > 1 else None
            return exit_code, output
        if isinstance(res, dict):
            return res.get("exit_code"), str(res.get("output") or "").strip()
        if hasattr(res, "output") or hasattr(res, "exit_code"):
            return getattr(res, "exit_code", None), str(getattr(res, "output", "") or "").strip()
        return None, str(res).strip()

    @staticmethod
    def _selector_exists(selector):
        """Safely reads selector existence across real uiautomator2 objects and test mocks."""
        try:
            exists_attr = getattr(selector, "exists", False)
            return bool(exists_attr() if callable(exists_attr) else exists_attr)
        except Exception:
            return False

    def _id_candidates(self, suffix):
        ids = [f"{self.package_name}:id/{suffix}"]
        if self.package_name != PACKAGE_NAME:
            ids.append(f"{PACKAGE_NAME}:id/{suffix}")
        return ids

    def _any_id_exists(self, suffix):
        return any(self._selector_exists(self.d(resourceId=rid)) for rid in self._id_candidates(suffix))

    def _id_selector(self, suffix):
        """Returns matching resourceId UiObject for target package first, else default Instagram ID."""
        for rid in self._id_candidates(suffix):
            sel = self.d(resourceId=rid)
            if self._selector_exists(sel):
                return sel
        return self.d(resourceId=f"{self.package_name}:id/{suffix}")

    def _find_first_selector(self, candidates):
        """Returns (selector_description, ui_object) for the first matching selector, else (None, None)."""
        for label, sel in candidates:
            if self._selector_exists(sel):
                return label, sel
        return None, None

    def _get_foreground_app(self):
        """Queries current foreground package and activity and records them into self.evidence."""
        try:
            app_info = self.d.app_current() or {}
        except Exception:
            app_info = {}
        curr_pkg = app_info.get("package", "Unknown") or "Unknown"
        curr_activity = app_info.get("activity", "Unknown") or "Unknown"
        self.evidence["foreground_package"] = curr_pkg
        self.evidence["foreground_activity"] = curr_activity
        return curr_pkg, curr_activity

    def _is_target_in_foreground(self):
        curr_pkg, _ = self._get_foreground_app()
        return curr_pkg == self.package_name

    def _format_evidence(self):
        """Formats structured failure evidence across target package, foreground, intent, gallery selector, and video."""
        curr_pkg = self.evidence.get("foreground_package")
        curr_act = self.evidence.get("foreground_activity")
        if not curr_pkg or not curr_act:
            curr_pkg, curr_act = self._get_foreground_app()
        return (
            f"  Target package: {self.evidence.get('target_package') or self.package_name}\n"
            f"  Foreground: {curr_pkg}/{curr_act}\n"
            f"  Intent result: {self.evidence.get('intent_result') or 'N/A'}\n"
            f"  Gallery selector: {self.evidence.get('gallery_selector') or 'N/A'}\n"
            f"  Selected video: {self.evidence.get('selected_video') or 'N/A'}"
        )

    def log(self, level, message, step=None):
        """Unified logging for both CLI and GUI."""
        if self.log_callback:
            try:
                self.log_callback(level, message, step)
            except Exception:
                pass

    def _default_log(self, level, message, step=None):
        prefix = f"[{level.upper()}]"
        if step:
            prefix += f" [Step {step}]"
        print(f"{prefix} {message}")

    def check_cancelled(self):
        if self.is_cancelled:
            self.log("warning", "Workflow was cancelled by user.")
            raise InterruptedError("Workflow cancelled by user.")

    def handle_unexpected_popups(self):
        """Auto-detects and dismisses system/Instagram dialogs that could block automation."""
        curr_pkg, _ = self._get_foreground_app()
        if curr_pkg == "com.android.vending":
            self.log("info", "Self-healing: Dismissed Play Store promo overlay via Back.")
            self.d.press("back")
            self.human.sleep(0.8)
            return True

        # Handle system permission dialogs (GrantPermissionsActivity)
        if "permissioncontroller" in (curr_pkg or "").lower() or "packageinstaller" in (curr_pkg or "").lower():
            for full_id in [
                "com.android.permissioncontroller:id/permission_allow_button",
                "com.android.permissioncontroller:id/permission_allow_foreground_only_button",
                "com.android.permissioncontroller:id/permission_allow_one_time_button",
                "com.android.packageinstaller:id/permission_allow_button",
            ]:
                perm_btn = self.d(resourceId=full_id)
                if self._selector_exists(perm_btn):
                    self.log("info", f"Self-healing: Auto-granted system permission via {full_id}.")
                    self.human.click(perm_btn)
                    self.human.sleep(0.6)
                    return True
            perm_text_sel = self.d(textMatches="(?i)^.*(while using the app|allow only while using the app|allow|allow all).*$")
            if self._selector_exists(perm_text_sel):
                self.log("info", "Self-healing: Auto-granted system permission via text match.")
                self.human.click(perm_text_sel)
                self.human.sleep(0.6)
                return True

        popups = [
            ("Not now", self.d(textMatches="(?i)^not now$")),
            ("Dismiss", self.d(textMatches="(?i)^dismiss$")),
            ("Got it", self.d(textMatches="(?i)^got it$")),
            ("OK", self.d(textMatches="(?i)^ok$")),
            ("Cancel", self.d(textMatches="(?i)^cancel$")),
            ("Allow", self.d(textMatches="(?i)^.*(allow|while using the app).*$")),
            ("Close button", self.d(descriptionMatches="(?i)^(close|dismiss)$")),
            ("Try Meta One Dismiss", self.d(textMatches="(?i)^(not now|cancel|close)$")),
        ]
        for name, selector in popups:
            try:
                if self._selector_exists(selector):
                    # Don't click Cancel if it's on the main video editor preview
                    if name == "Cancel" and self._any_id_exists("clips_right_action_button"):
                        continue
                    self.log("info", f"Self-healing: Auto-dismissed popup '{name}'.")
                    self.human.click(selector)
                    self.human.sleep(0.6)
                    return True
            except Exception:
                pass
        return False

    def wait_and_verify_screen(self, state_name, verify_fn, timeout=15, auto_dismiss=True, require_target_foreground=False):
        """
        Polls until verify_fn() returns True.
        Automatically dismisses popups, handles delays, fails fast on wrong foreground/launcher bounce,
        and provides detailed diagnostics on failure.
        """
        self.check_cancelled()
        self.log("info", f"Verifying screen state: [{state_name}]...")
        start_time = time.time()
        wrong_fg_streak = 0

        while time.time() - start_time < timeout:
            self.check_cancelled()
            try:
                if verify_fn():
                    self.log("success", f"Verified state: [{state_name}]")
                    self.human.sleep(0.3, jitter_ratio=0.2)
                    return True
            except Exception:
                pass

            if auto_dismiss and self.handle_unexpected_popups():
                wrong_fg_streak = 0
                continue

            if require_target_foreground:
                curr_pkg, curr_act = self._get_foreground_app()
                if curr_pkg and curr_pkg not in ("Unknown", self.package_name):
                    # Allow brief transition/permission installer/cold-start, fail fast on sustained wrong app or launcher bounce
                    if "permissioncontroller" not in curr_pkg.lower() and "packageinstaller" not in curr_pkg.lower():
                        wrong_fg_streak += 1
                        if wrong_fg_streak >= 7:
                            err_msg = (
                                f"Clone launch/routing failed while verifying [{state_name}]: "
                                f"target '{self.package_name}' is not in foreground (found '{curr_pkg}/{curr_act}').\n"
                                f"{self._format_evidence()}"
                            )
                            self.log("error", err_msg)
                            raise RuntimeError(err_msg)
                else:
                    wrong_fg_streak = 0

            time.sleep(0.4)

        # DIAGNOSTIC REPORT ON FAILURE
        self._get_foreground_app()

        visible_summary = []
        try:
            root = ET.fromstring(self.d.dump_hierarchy())
            for n in root.iter("node"):
                t = (n.attrib.get("text") or n.attrib.get("content-desc") or "").strip()
                res = n.attrib.get("resource-id", "")
                if t and len(t) < 45 and "systemui" not in res and t not in visible_summary:
                    visible_summary.append(t)
                if len(visible_summary) >= 6:
                    break
        except Exception:
            pass

        is_keyboard_open = self._selector_exists(self.d(resourceId="com.android.systemui:id/ime_switcher"))

        diag_msg = (
            f"Screen verification timed out for [{state_name}]!\n"
            f"{self._format_evidence()}\n"
            f"  Visible elements: {', '.join(visible_summary) if visible_summary else 'None detected'}\n"
            f"  Keyboard active: {'Yes (blocking elements)' if is_keyboard_open else 'No'}"
        )
        self.log("error", diag_msg)
        raise TimeoutError(diag_msg)

    def _detect_username(self):
        """Detects current logged-in Instagram username."""
        try:
            story = self.d(descriptionMatches=r"(?i).*'s story.*")
            if story.exists:
                m = re.match(r"^(.*?)'s story", story.info.get("contentDescription", ""))
                if m:
                    self.username = m.group(1).strip()
                    self.log("info", f"Detected active account: @{self.username}")
        except Exception:
            pass

    def push_video_to_device(self, local_video_path):
        """Pushes a local video from PC to the device camera folder and updates media store."""
        self.check_cancelled()
        if not os.path.isfile(local_video_path):
            raise FileNotFoundError(f"Local video not found: {local_video_path}")

        filename = os.path.basename(local_video_path)
        safe_filename = f"upload_{int(time.time())}_{filename.replace(' ', '_')}"
        remote_path = f"/sdcard/DCIM/Camera/{safe_filename}"

        self.log("info", f"Pushing video to device: {remote_path}...")
        self.d.push(local_video_path, remote_path)
        self._staged_video_path = remote_path
        self.evidence["selected_video"] = safe_filename
        self.human.sleep(1.0)

        self.log("info", "Scanning media file into Android gallery...")
        self.d.shell(f'am broadcast -a android.intent.action.MEDIA_SCANNER_SCAN_FILE -d "file://{remote_path}"')
        self.human.sleep(1.5)
        self.log("success", f"Video successfully added to device gallery: {safe_filename}")
        return remote_path

    def _ensure_target_foreground(self, step=None, timeout=0.0, context="step execution", settle_from_launcher=False):
        """Verifies target clone package is currently in foreground; auto-handles permission dialogs and fails fast if bounced to launcher/wrong app."""
        deadline = time.time() + max(0.0, float(timeout))
        perm_deadline = time.time() + max(6.0, float(timeout))
        curr_pkg, curr_act = self._get_foreground_app()
        saw_target = (curr_pkg == self.package_name)
        bounced_streak = 0

        while True:
            if curr_pkg == self.package_name:
                saw_target = True
                bounced_streak = 0
                if not settle_from_launcher or "UrlHandlerLauncherActivity" not in (curr_act or ""):
                    return curr_pkg, curr_act
            elif curr_pkg and ("permissioncontroller" in curr_pkg.lower() or "packageinstaller" in curr_pkg.lower()):
                bounced_streak = 0
                if time.time() >= perm_deadline:
                    break
            else:
                if time.time() >= deadline:
                    break
                if saw_target:
                    # Target appeared briefly (e.g. UrlHandlerLauncherActivity) and then left foreground
                    bounced_streak += 1
                    if bounced_streak >= 2:
                        break
            self.check_cancelled()
            self.handle_unexpected_popups()
            time.sleep(0.4)
            curr_pkg, curr_act = self._get_foreground_app()

        if curr_pkg != self.package_name:
            err_msg = (
                f"Clone launch/routing failed (Wrong foreground app during {context}): expected '{self.package_name}', "
                f"found '{curr_pkg}/{curr_act}'.\n{self._format_evidence()}"
            )
            self.log("error", err_msg, step=step)
            raise RuntimeError(err_msg)
        return curr_pkg, curr_act

    def _is_gallery_grid_visible(self):
        """Returns True only when target package is foreground and real gallery grid/thumbnail UI is visible."""
        if not self._is_target_in_foreground():
            return False
        return (
            self._selector_exists(self.d(descriptionMatches="(?i).*thumbnail.*"))
            or self._any_id_exists("gallery_grid_item_thumbnail")
            or self._any_id_exists("gallery_recycler_view")
        )

    @staticmethod
    def _first_element(selector):
        """Returns the first indexed child element when supported, else the selector itself."""
        try:
            return selector[0]
        except Exception:
            return selector

    def _describe_ui_element(self, elem, fallback="matched_element"):
        """Extracts a concise readable identifier from a UI element's accessibility info."""
        try:
            info = getattr(elem, "info", {}) or {}
            if isinstance(info, dict):
                desc = (info.get("contentDescription") or info.get("text") or "").strip()
                res_id = (info.get("resourceId") or info.get("resourceName") or "").strip()
                bounds = info.get("bounds")
                parts = [p for p in (desc, res_id) if p]
                if bounds:
                    parts.append(f"bounds={bounds}")
                if parts:
                    return " | ".join(parts)
        except Exception:
            pass
        return fallback

    def open_audio_page(self, audio_url):
        """Step 1: Normalize Audio URL to HTTPS, route to target clone MainActivity/VIEW handler, and verify Audio screen."""
        self.check_cancelled()
        normalized_url = self.normalize_audio_url(audio_url)
        if normalized_url != str(audio_url or "").strip():
            self.log("info", f"Normalized audio URL to HTTPS: {normalized_url}", step=1)

        self.evidence["intent_result"] = None
        self.evidence["gallery_selector"] = None
        self.log("info", f"Opening Instagram audio link in {self.package_name}: {normalized_url}", step=1)

        # Anti-speedrun cadence: natural ambient settling pause before firing deep link
        self.human.sleep(1.2, jitter_ratio=0.25)

        # APKCloner clones drop VIEW intents routed via UrlHandlerLauncherActivity; targeting
        # {package}/com.instagram.mainactivity.InstagramMainActivity with -S clears any stale launcher-hidden task
        # and works cleanly on both official & cloned APKs.
        primary_cmd = (
            f'am start -S -n {self.package_name}/com.instagram.mainactivity.InstagramMainActivity '
            f'-a android.intent.action.VIEW -d "{normalized_url}"'
        )
        res = self.d.shell(primary_cmd)
        exit_code, intent_out = self._parse_shell_result(res)

        if (exit_code not in (0, None)) or re.search(
            r"(?im)(Activity class .* does not exist|Permission Denial|not exported)",
            intent_out or "",
        ):
            # Only fall back to package-scoped VIEW intent if InstagramMainActivity component is missing/unexported;
            # NEVER route unscoped or silently fall back to official com.instagram.android.
            if re.search(
                r"(?im)(Activity class .* does not exist|Permission Denial|not exported)",
                intent_out or "",
            ):
                self.log(
                    "warning",
                    f"Direct InstagramMainActivity route unsupported for {self.package_name}; retrying package-scoped VIEW intent...",
                    step=1,
                )
                fallback_cmd = f'am start -S -a android.intent.action.VIEW -d "{normalized_url}" {self.package_name}'
                res = self.d.shell(fallback_cmd)
                exit_code, intent_out = self._parse_shell_result(res)

        self.evidence["intent_result"] = f"exit={exit_code}; output={intent_out or '<empty>'}"
        self.log("info", f"Audio intent result ({self.package_name}): {self.evidence['intent_result']}", step=1)

        intent_failed = (exit_code is not None and exit_code != 0) or bool(
            re.search(
                r"(?im)(^\s*Error:|unable to resolve intent|SecurityException|Activity not started,\s*unable)",
                intent_out or "",
            )
        )
        if intent_failed:
            self._get_foreground_app()
            err_msg = (
                f"Clone launch/routing failed (Audio intent launch failed) for target package '{self.package_name}'.\n"
                f"{self._format_evidence()}"
            )
            self.log("error", err_msg, step=1)
            raise RuntimeError(err_msg)

        # Confirm the exact selected clone settled in foreground BEFORE waiting for 'Use audio'
        self._ensure_target_foreground(step=1, timeout=6.0, context="audio intent launch", settle_from_launcher=True)

        # State Verification 1: Verify audio screen loaded in target clone with 'Use audio' button (fail-fast if bounced)
        self.wait_and_verify_screen(
            "Audio Track Page",
            lambda: self._is_target_in_foreground() and (
                self._selector_exists(self.d(text="Use audio"))
                or self._selector_exists(self.d(textMatches=r"(?i)^\s*use audio\s*$"))
                or self._selector_exists(self.d(description="Use audio"))
                or self._selector_exists(self.d(descriptionMatches=r"(?i)^\s*use audio\s*$"))
                or self._any_id_exists("use_in_camera_header_button")
                or self._any_id_exists("use_in_camera_button_scene_root")
                or self._any_id_exists("clips_pivot_page_fragment")
            ),
            timeout=25,
            require_target_foreground=True,
        )
        self.log("success", f"Audio page verified and ready in {self.package_name}.", step=1)

    def tap_use_audio(self):
        """Step 2: Click 'Use audio' with human touch biometrics and VERIFY Reel camera screen."""
        self.check_cancelled()
        self._ensure_target_foreground(step=2, timeout=2.0, context="Use audio step")
        self.log("info", "Tapping 'Use audio' button (Human touch)...", step=2)

        btn_candidates = [
            ("text='Use audio'", self.d(text="Use audio")),
            ("textMatches='(?i)^\\s*use audio\\s*$'", self.d(textMatches=r"(?i)^\s*use audio\s*$")),
            ("description='Use audio'", self.d(description="Use audio")),
            ("descriptionMatches='(?i)^\\s*use audio\\s*$'", self.d(descriptionMatches=r"(?i)^\s*use audio\s*$")),
        ] + [
            (f"resourceId='{rid}'", self.d(resourceId=rid))
            for rid in self._id_candidates("use_in_camera_header_button") + self._id_candidates("use_in_camera_button_scene_root")
        ]
        btn_desc, use_audio_btn = self._find_first_selector(btn_candidates)
        if not use_audio_btn:
            self._get_foreground_app()
            err_msg = f"Could not find 'Use audio' button selector.\n{self._format_evidence()}"
            self.log("error", err_msg, step=2)
            raise RuntimeError(err_msg)

        self.log("info", f"Clicking 'Use audio' via selector [{btn_desc}]", step=2)
        self.human.click(use_audio_btn)

        # State Verification 2: Verify Reel Camera Screen is loaded in target clone
        self.wait_and_verify_screen(
            "Reel Camera Screen",
            lambda: self._is_target_in_foreground() and (
                self._any_id_exists("gallery_preview_button")
                or self._selector_exists(self.d(description="Gallery"))
                or self._any_id_exists("camera_shutter_button")
            ),
            timeout=12,
        )
        self.log("success", "Reel camera verified.", step=2)

    def _wait_and_select_gallery_video(self, staged_video_path=None, timeout=12.0):
        """Waits for the staged video/thumbnail in gallery UI and selects it without coordinate fallbacks."""
        expected_name = os.path.basename(staged_video_path) if staged_video_path else None
        expected_stem = os.path.splitext(expected_name)[0] if expected_name else None
        self.evidence["selected_video"] = expected_name or "latest_gallery_video"

        deadline = time.time() + max(1.0, float(timeout))
        rescan_done = False

        while time.time() < deadline:
            self.check_cancelled()
            self._ensure_target_foreground(step=4, timeout=0.0, context="gallery video selection")

            candidates = []
            if expected_stem:
                candidates.extend([
                    (f"descriptionContains='{expected_stem}'", self.d(descriptionContains=expected_stem)),
                    (f"textContains='{expected_stem}'", self.d(textContains=expected_stem)),
                ])
            candidates.append(("descriptionMatches='(?i).*video thumbnail.*'", self.d(descriptionMatches="(?i).*video thumbnail.*")))
            for rid in self._id_candidates("gallery_grid_item_thumbnail"):
                candidates.append((f"resourceId='{rid}'", self.d(resourceId=rid)))

            sel_desc, matched_sel = self._find_first_selector(candidates)
            if matched_sel is not None:
                item_obj = self._first_element(matched_sel)
                elem_desc = self._describe_ui_element(item_obj, fallback=sel_desc)
                self.evidence["gallery_selector"] = sel_desc
                self.evidence["selected_video"] = (
                    f"{expected_name} [{elem_desc}]" if expected_name else elem_desc
                )
                self.log(
                    "info",
                    f"Selecting gallery video via [{sel_desc}] -> {self.evidence['selected_video']}",
                    step=4,
                )
                self.human.click(item_obj)
                return sel_desc, self.evidence["selected_video"]

            if staged_video_path and not rescan_done and (deadline - time.time()) <= (float(timeout) * 0.6):
                rescan_done = True
                self.log("info", f"Waiting for staged video '{expected_name}' in MediaStore; re-triggering scan...", step=4)
                try:
                    self.d.shell(
                        f'am broadcast -a android.intent.action.MEDIA_SCANNER_SCAN_FILE -d "file://{staged_video_path}"'
                    )
                except Exception:
                    pass

            time.sleep(0.4)

        self.evidence["gallery_selector"] = "MISSING(staged/video thumbnail | gallery_grid_item_thumbnail)"
        self._get_foreground_app()
        err_msg = (
            "Could not locate staged video thumbnail selector in Gallery Grid (no coordinate fallback attempted).\n"
            f"{self._format_evidence()}"
        )
        self.log("error", err_msg, step=4)
        raise RuntimeError(err_msg)

    def open_gallery_and_select_video(self, staged_video_path=None):
        """Step 3 & 4: Open Gallery via verified selector, select staged video via selector, verify preview editor."""
        self.check_cancelled()
        target_video_path = staged_video_path or self._staged_video_path
        if target_video_path:
            self.evidence["selected_video"] = os.path.basename(target_video_path)
        self._ensure_target_foreground(step=3, timeout=2.0, context="Gallery open step")
        self.log("info", "Opening Gallery picker...", step=3)

        if self._selector_exists(self.d(text="REEL")):
            self.human.click(self.d(text="REEL"))
            self.human.sleep(0.6)

        gallery_btn_candidates = [
            (f"resourceId='{rid}'", self.d(resourceId=rid))
            for rid in self._id_candidates("gallery_preview_button")
        ] + [
            ("description='Gallery'", self.d(description="Gallery")),
            ("descriptionMatches='(?i)^gallery.*'", self.d(descriptionMatches="(?i)^gallery.*")),
        ]

        gallery_sel_desc, gallery_btn = self._find_first_selector(gallery_btn_candidates)
        if gallery_btn is None:
            wait_deadline = time.time() + 2.0
            while gallery_btn is None and time.time() < wait_deadline:
                self.check_cancelled()
                time.sleep(0.4)
                gallery_sel_desc, gallery_btn = self._find_first_selector(gallery_btn_candidates)

        if gallery_btn is None:
            self.evidence["gallery_selector"] = "MISSING(gallery_preview_button | description=Gallery)"
            self._get_foreground_app()
            err_msg = (
                "Gallery button selector not found on Reel camera screen (no coordinate fallback attempted).\n"
                f"{self._format_evidence()}"
            )
            self.log("error", err_msg, step=3)
            raise RuntimeError(err_msg)

        self.evidence["gallery_selector"] = gallery_sel_desc
        self.log("info", f"Clicking gallery button via selector [{gallery_sel_desc}]", step=3)
        self.human.click(gallery_btn)

        # State Verification 3: Verify genuine Gallery grid is open (NO 'New reel' false positive)
        self.wait_and_verify_screen(
            "Gallery Grid",
            self._is_gallery_grid_visible,
            timeout=12,
        )
        self.log("success", "Gallery grid verified.", step=3)

        # Step 4: Wait for staged video in gallery, select via selector & VERIFY preview editor screen
        self.log("info", f"Selecting staged video from gallery ({self.evidence.get('selected_video') or 'latest'})...", step=4)
        self._wait_and_select_gallery_video(staged_video_path=target_video_path, timeout=12.0)

        # State Verification 4: Verify Video Preview / Editor screen is loaded in target clone
        self.wait_and_verify_screen(
            "Video Preview Editor",
            lambda: self._is_target_in_foreground() and (
                self._any_id_exists("clips_right_action_button")
                or self._selector_exists(self.d(text="Next"))
                or self._selector_exists(self.d(description="Next"))
            ),
            timeout=15,
        )
        self.log("success", f"Video loaded in preview editor ({self.evidence.get('selected_video')}).", step=4)

    def _is_share_screen_visible(self):
        return (
            self._any_id_exists("share_button")
            or self._selector_exists(self.d(text="Share"))
            or self._any_id_exists("caption_input_text_view")
        )

    def proceed_to_share_screen(self):
        """Step 5: Tap 'Next ->' on preview editor and VERIFY 'New reel' share screen."""
        self.check_cancelled()
        self.log("info", "Clicking 'Next' on preview screen...", step=5)
        for attempt in range(2):
            self.handle_unexpected_popups()
            if self._is_share_screen_visible():
                break
            next_btn = self._id_selector("clips_right_action_button")
            if not self._selector_exists(next_btn):
                next_btn = self.d(text="Next")
            if not self._selector_exists(next_btn):
                next_btn = self.d(description="Next")

            self.human.click(next_btn)

            for _ in range(5):
                time.sleep(0.5)
                if self._is_share_screen_visible():
                    break
                curr_pkg, _ = self._get_foreground_app()
                if curr_pkg == "com.android.vending":
                    self.log("info", "Self-healing: Dismissed Play Store promo overlay via Back.", step=5)
                    self.d.press("back")
                    self.human.sleep(0.8)
                    break

            if self._is_share_screen_visible():
                break
            if attempt == 0 and self._any_id_exists("clips_right_action_button"):
                self.log("info", "Returned to Preview Editor after promo dismissal; tapping 'Next' again...", step=5)
                continue
            break

        # State Verification 5: Verify 'New reel' Share/Details screen is loaded
        self.wait_and_verify_screen(
            "New Reel Share Screen",
            self._is_share_screen_visible,
            timeout=14,
            require_target_foreground=True,
        )
        # Clear any bottom sheet covering Share Screen (e.g., "New ways to reuse" -> OK)
        self.handle_unexpected_popups()
        self.log("success", "Share screen verified.", step=5)

    def write_caption(self, caption_text):
        """Step 6: Enter caption, reliably force-hide soft keyboard, and verify screen state."""
        self.check_cancelled()
        self.handle_unexpected_popups()
        if not caption_text:
            self.log("info", "No caption provided, skipping.", step=6)
            return

        self.log("info", f"Entering caption ({len(caption_text)} chars)...", step=6)
        caption_box = self._id_selector("caption_input_text_view")
        if not caption_box.exists:
            caption_box = self.d(textMatches="(?i)Write a caption.*")

        if caption_box.wait(timeout=8):
            # Human typing simulation
            self.human.type_text(caption_text, element=caption_box)
            self.human.sleep(0.4)

            # CRITICAL: Dismiss on-screen soft keyboard using hardware keyevent 4
            self.log("info", "Dismissing on-screen keyboard...", step=6)
            self.d.shell("input keyevent 4")
            self.human.sleep(0.6)

            # Check if keyboard switcher icon is still on screen (meaning keyboard didn't close)
            if self.d(resourceId="com.android.systemui:id/ime_switcher").exists:
                self.log("info", "Keyboard still open, dispatching secondary dismiss...", step=6)
                self.d.shell("input keyevent 4")
                self.human.sleep(0.6)

            # State Verification 6: Verify Share screen active and caption filled
            self.wait_and_verify_screen(
                "Share Screen (Caption Entered)",
                lambda: self._any_id_exists("share_button") or
                        self.d(text="Share").exists or
                        self._any_id_exists("caption_input_text_view"),
                timeout=10,
                require_target_foreground=True,
            )
            self.log("success", "Caption entered and keyboard dismissed.", step=6)
        else:
            self.log("warning", "Caption input box not found, skipping caption.", step=6)

    def configure_more_options(self, hide_likes=True, hide_shares=True):
        """Step 7 & 8: Open 'More options' using Bézier scroll, toggle switches, and return."""
        self.check_cancelled()
        if not hide_likes and not hide_shares:
            self.log("info", "Toggle options disabled, skipping.", step=7)
            return

        self.log("info", "Navigating to 'More options' via human scroll...", step=7)
        found_more_options = False
        for _ in range(4):
            if self.d(text="More options").exists:
                found_more_options = True
                break
            self.human.scroll_down(stride_ratio=0.45)
            self.human.sleep(0.15)

        more_options_btn = self.d(text="More options")
        if not found_more_options or not more_options_btn.exists:
            self.log("warning", "'More options' button not found, skipping toggle settings.", step=7)
            return

        self.human.click(more_options_btn)

        # State Verification 7: Verify 'More options' screen open
        self.wait_and_verify_screen(
            "More Options Settings Screen",
            lambda: self.d(textMatches="(?i).*hide (like|share) count.*").exists or
                    self.d(text="Schedule this reel").exists or
                    self.d(text="Upload at highest quality").exists,
            timeout=10
        )
        self.log("success", "'More options' screen verified.", step=7)

        # Step 8: Smooth scroll to interaction toggles
        self.log("info", "Configuring privacy toggles...", step=8)
        for _ in range(4):
            if self.d(text="Hide share count on this reel").exists:
                break
            self.human.scroll_down(stride_ratio=0.45)
            self.human.sleep(0.15)

        def toggle_option_on(label_text):
            row = self.d(text=label_text)
            if not row.exists:
                self.human.scroll_down(stride_ratio=0.20)
                row = self.d(text=label_text)

            if row.exists:
                top = row.info["bounds"]["top"]
                bottom = row.info["bounds"]["bottom"]
                y_center = (top + bottom) // 2

                toggles = self._id_selector("toggle")
                matched_toggle = None
                for t in toggles:
                    tb = t.info["bounds"]
                    if tb["top"] <= y_center <= tb["bottom"] or abs((tb["top"] + tb["bottom"]) // 2 - y_center) < 50:
                        matched_toggle = t
                        break

                if matched_toggle:
                    if not matched_toggle.info.get("checked", False):
                        self.log("info", f"Enabling switch for '{label_text}'...")
                        self.human.click(matched_toggle)
                        self.human.sleep(0.5)
                else:
                    w = self.d.info.get("displayWidth", 720)
                    self.human.click((w - 80, y_center))
                    self.human.sleep(0.5)

        if hide_likes:
            toggle_option_on("Hide like count on this reel")
        if hide_shares:
            toggle_option_on("Hide share count on this reel")

        # Press back to return to Share screen and VERIFY
        self.log("info", "Returning to Share screen...")
        back_btn = self._id_selector("action_bar_button_back")
        if back_btn.exists:
            self.human.click(back_btn)
        else:
            self.d.press("back")

        # State Verification 8: Verify successfully returned to Share screen
        self.wait_and_verify_screen(
            "Returned to Share Screen",
            lambda: self._any_id_exists("share_button") or
                    self.d(text="Share").exists or
                    self._any_id_exists("caption_input_text_view"),
            timeout=10,
            require_target_foreground=True,
        )
        self.log("success", "Toggles configured and confirmed.", step=8)

    def share_reel(self):
        """Step 9: Click Share/Next and VERIFY share screen is dismissed (upload commenced)."""
        self.check_cancelled()
        self.handle_unexpected_popups()
        self.log("info", "Tapping 'Share' button (Human touch)...", step=9)

        share_btn = self._id_selector("share_button")
        if not self._selector_exists(share_btn):
            share_btn = self.d(textMatches="(?i)^(share|next)$")

        # If share button is scrolled off-view, scroll down to reveal it
        if not self._selector_exists(share_btn):
            self.human.scroll_down(stride_ratio=0.35)
            self.human.sleep(0.5)
            share_btn = self._id_selector("share_button")
            if not self._selector_exists(share_btn):
                share_btn = self.d(textMatches="(?i)^(share|next)$")

        if not self._selector_exists(share_btn) and not share_btn.wait(timeout=10):
            raise TimeoutError("Share button not found on Share screen.")

        btn_text = ""
        try:
            info = getattr(share_btn, "info", {}) or {}
            btn_text = str(info.get("text") or info.get("contentDescription") or "").lower()
        except Exception:
            pass

        self.human.click(share_btn)

        # If the button was 'Next', Instagram may display a secondary 'Share' button or sheet
        if "next" in btn_text:
            self.human.sleep(1.2)
            self.handle_unexpected_popups()
            sec_share = self.d(textMatches="(?i)^share$")
            if self._selector_exists(sec_share):
                self.log("info", "Tapping secondary 'Share' button...", step=9)
                self.human.click(sec_share)

        # State Verification 9: Verify Share screen is dismissed or feed active
        self.wait_and_verify_screen(
            "Share Screen Dismissed (Upload Started)",
            lambda: not self._any_id_exists("share_button")
                    or not self.d(textMatches="(?i)^(share|next)$").exists
                    or self._any_id_exists("feed_tab")
                    or self.d(description="Home").exists,
            timeout=16,
            require_target_foreground=True,
        )
        self.log("success", "Reel submitted for upload!", step=9)

    def exit_audio_and_go_to_home_feed(self):
        """Exits the underlying audio activity and VERIFIES we are on Instagram Home feed."""
        self.log("info", "Exiting audio screen and navigating to Instagram Home feed...", step=10)

        # 1. Close audio/modal activities until feed_tab is visible
        for attempt in range(6):
            if self._any_id_exists("feed_tab") or self.d(description="Home").exists:
                break
            
            back_btn = self._id_selector("action_bar_button_back")
            if not back_btn.exists:
                back_btn = self.d(description="Back")
            
            if back_btn.exists:
                self.human.click(back_btn)
            else:
                self.d.press("back")
            self.human.sleep(0.8)

        # 2. Tap Home tab icon (double-tap to scroll to top)
        home_tab = self._id_selector("feed_tab")
        if not home_tab.exists:
            home_tab = self.d(description="Home")

        if home_tab.wait(timeout=6):
            self.human.click(home_tab)
            self.human.sleep(0.5)
            self.human.click(home_tab)  # Double-click to scroll feed to absolute top

        # State Verification 10: Verify on Home feed
        self.wait_and_verify_screen(
            "Instagram Home Feed (Top)",
            lambda: self._any_id_exists("feed_tab") or
                    self.d(description="Home").exists or
                    self.d(descriptionMatches=r"(?i).*'s story.*").exists,
            timeout=12
        )
        self.log("success", "Verified at top of Instagram Home feed.", step=10)

    def wait_for_upload_completion(self, max_wait_sec=120):
        """Monitors upload progress bar and completion banners on the Home feed."""
        self.log("info", f"Watching reel upload progress bar on Home feed (up to {max_wait_sec}s)...", step=10)
        start_time = time.time()
        was_uploading = False

        while time.time() - start_time < max_wait_sec:
            self.check_cancelled()
            elapsed = int(time.time() - start_time)

            # Check 1: Completion banner ("Posted! All set. Want to send it to friends?")
            posted_banner = self.d(textMatches="(?i).*(posted! all set|your reel was shared|shared to feed).*")
            if posted_banner.exists:
                self.log("success", "Detected 'Posted! All set.' completion banner!", step=10)
                self.human.sleep(0.8)
                return True

            # Check 2: Upload progress indicators on Home feed
            progress_bar = self.d(resourceIdMatches="(?i).*(row_feed_share_progressbar|row_pending_media|progressbar).*")
            upload_text = self.d(textMatches="(?i).*(sharing as reel|sharing|posting|finishing up).*")

            if progress_bar.exists or upload_text.exists:
                was_uploading = True
                status_msg = upload_text.info.get("text", "Sharing as reel...") if upload_text.exists else "Sharing as reel..."
                self.log("info", f"{status_msg} ({elapsed}s elapsed)...", step=10)
                time.sleep(2)
                continue

            # If progress bar was seen and now disappeared, upload is finished
            if was_uploading:
                self.log("success", "Upload progress bar finished!", step=10)
                self.human.sleep(1.0)
                return True

            time.sleep(2)

        self.log("warning", "Upload monitoring timed out, continuing to link extraction...", step=10)
        return False

    def copy_reel_url_from_share_sheet(self, timeout=8):
        """Helper to tap 'Copy link' inside an open Share drawer and return clipboard URL."""
        self.log("info", "Looking for 'Copy link' in share sheet...", step=11)
        self.human.sleep(1.5)  # Wait for bottom sheet animation to fully settle

        copy_link_btn = self.d(description="Copy link")
        if not copy_link_btn.exists:
            copy_link_btn = self.d(text="Copy link")

        if copy_link_btn.wait(timeout=timeout):
            # Clear previous clipboard so we can detect new copied link
            try:
                self.d.clipboard = ""
            except Exception:
                pass

            self.human.click(copy_link_btn)
            self.log("info", "Tapped 'Copy link', polling clipboard for URL...", step=11)

            # Poll clipboard for up to 8 seconds
            start_poll = time.time()
            while time.time() - start_poll < 8.0:
                time.sleep(0.5)
                clip = self.d.clipboard
                if clip and ("/reel/" in clip or "/p/" in clip):
                    self.log("success", f"Reel link successfully copied: {clip}", step=11)
                    return clip

            clip = self.d.clipboard
            if clip and "instagram.com" in clip and "/audio/" not in clip:
                self.log("success", f"Reel link captured: {clip}", step=11)
                return clip

            self.log("warning", f"Clipboard received non-reel URL or timed out: {repr(clip)}", step=11)
            return None
        else:
            self.log("warning", "'Copy link' button not found in share sheet.", step=11)
            return None

    def identify_and_copy_reel_link(self):
        """Step 11: Identifies newly posted Reel on Home feed and copies its link with state verification."""
        self.check_cancelled()
        self.log("info", "Identifying newly published Reel on Home feed...", step=11)
        w = self.d.info.get("displayWidth", 720)
        h = self.d.info.get("displayHeight", 1600)

        # Pattern 1: 'Posted! All set.' banner with 'Send' button (fastest)
        posted_banner = self.d(textMatches="(?i).*posted! all set.*")
        if posted_banner.exists:
            self.log("info", "Found 'Posted! All set' banner. Tapping 'Send' button on banner...", step=11)
            send_btn = self.d(text="Send")
            if send_btn.exists:
                self.human.click(send_btn)
                self.human.sleep(1.5)
                url = self.copy_reel_url_from_share_sheet()
                if url:
                    return url
                self.d.press("back")
                self.human.sleep(0.8)

        # Pattern 2: Tap the 1st reel post on the Home feed to enter full-screen
        self.log("info", "Locating 1st Reel on Home feed...", step=11)

        # Tap on the video body in the feed (y ~ 900) to open the full-screen Reel viewer
        self.log("info", "Tapping video on feed to open full-screen Reel viewer...", step=11)
        self.human.click((w // 2, 900))
        self.human.sleep(2.0)

        # In full screen Reel viewer, tap Share icon (direct_share_button or button_share)
        fullscreen_share = self._id_selector("direct_share_button")
        if not fullscreen_share.exists:
            fullscreen_share = self._id_selector("button_share")
        if not fullscreen_share.exists:
            fullscreen_share = self.d(descriptionMatches="(?i).*(share|send).*")

        if fullscreen_share.wait(timeout=6):
            self.log("info", "Tapping Share icon on full-screen Reel...", step=11)
            self.human.click(fullscreen_share)
            self.human.sleep(1.5)
            url = self.copy_reel_url_from_share_sheet()
            if url:
                return url
            self.d.press("back")
            self.human.sleep(0.8)

        # Check if direct share button on feed card is visible
        share_post_btn = self._id_selector("row_feed_button_share")
        if not share_post_btn.exists:
            share_post_btn = self._id_selector("direct_share_button")

        if share_post_btn.exists:
            self.log("info", "Tapping Share button on Home feed reel card...", step=11)
            self.human.click(share_post_btn[0])
            self.human.sleep(1.5)
            url = self.copy_reel_url_from_share_sheet()
            if url:
                return url
            self.d.press("back")
            self.human.sleep(0.8)

        # Pattern 3: Fallback to Profile Tab -> First Video
        self.log("info", "Falling back to Profile tab to retrieve latest Reel...", step=11)
        self.d.press("back")
        self.human.sleep(0.8)

        profile_tab = self._id_selector("profile_tab")
        if not profile_tab.exists:
            profile_tab = self.d(description="Profile")

        if profile_tab.wait(timeout=8):
            self.human.click(profile_tab)
            self.human.sleep(1.5)

            # Switch to Reels tab
            reels_icon = self.d(description="Reels")
            if reels_icon.exists:
                self.human.click(reels_icon)
                self.human.sleep(1.2)

            # Find video thumbnails
            thumbs = self._id_selector("preview_clip_thumbnail")
            if thumbs.wait(timeout=5):
                has_drafts = self.d(text="Drafts").exists
                target_idx = 1 if (has_drafts and thumbs.count > 1) else 0
                self.log("info", f"Tapping profile Reel thumbnail at index {target_idx}...", step=11)
                self.human.click(thumbs[target_idx])
                self.human.sleep(2.0)

                reels_share = self.d(resourceIdMatches="(?i).*(direct_share_button|button_share|row_feed_button_share).*")
                if reels_share.wait(timeout=6):
                    self.human.click(reels_share)
                    self.human.sleep(1.5)
                    url = self.copy_reel_url_from_share_sheet()
                    if url:
                        return url

        self.log("warning", "Could not automatically copy Reel URL.", step=11)
        return None

    def execute_workflow(self, audio_url, video_path=None, caption=DEFAULT_CAPTION,
                         hide_likes=True, hide_shares=True):
        """Runs the entire Instagram Reel post automation workflow with full human simulation & state verification."""
        try:
            staged_video_path = None
            if video_path:
                staged_video_path = self.push_video_to_device(local_video_path=video_path)

            # Step 1: Open Audio page & verify
            self.open_audio_page(audio_url)

            # Step 2: Click 'Use audio' & verify Reel camera opened
            self.tap_use_audio()

            # Step 3 & 4: Open gallery, verify picker, select staged video & verify preview editor
            self.open_gallery_and_select_video(staged_video_path=staged_video_path)

            # Step 5: Click Next & verify Share details screen
            self.proceed_to_share_screen()

            # Step 6: Enter caption with human typing cadence & verify keyboard dismissed
            self.write_caption(caption)

            # Step 7 & 8: More options & verify toggles & return to share screen
            self.configure_more_options(hide_likes=hide_likes, hide_shares=hide_shares)

            # Step 9: Share reel & verify screen dismissed
            self.share_reel()

            # Step 10: Exit audio activity, verify Home feed & monitor upload progress bar
            self.exit_audio_and_go_to_home_feed()
            self.wait_for_upload_completion()

            # Step 11: Identify newly uploaded Reel on Home feed & copy link
            reel_url = self.identify_and_copy_reel_link()
            if reel_url:
                self.log("success", f"Workflow completed successfully! URL: {reel_url}")
            else:
                self.log("warning", "Workflow finished, but link copy timed out.")
            return reel_url

        except Exception as e:
            self.log("error", f"Workflow failed at state check: {e}")
            raise


def parse_args():
    parser = argparse.ArgumentParser(description="Automate Instagram Reel upload with audio, options, and copy link.")
    parser.add_argument("--audio", default=DEFAULT_AUDIO_URL, help="Instagram audio URL (e.g. https://www.instagram.com/reels/audio/<id>/)")
    parser.add_argument("--video", default=None, help="Local path to MP4 video file to upload")
    parser.add_argument("--caption", default=DEFAULT_CAPTION, help="Reel caption with hashtags")
    parser.add_argument("--device", default=None, help="Specific ADB device serial (optional)")
    parser.add_argument("--show-likes", action="store_true", help="Don't hide like count (defaults to hidden)")
    parser.add_argument("--show-shares", action="store_true", help="Don't hide share count (defaults to hidden)")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    uploader = InstagramReelUploader(device_serial=args.device)
    uploader.execute_workflow(
        audio_url=args.audio,
        video_path=args.video,
        caption=args.caption,
        hide_likes=not args.show_likes,
        hide_shares=not args.show_shares,
    )
