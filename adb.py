import argparse
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
import uiautomator2 as u2

from human_behavior import HumanBehaviorEngine

# Default Configuration
DEFAULT_AUDIO_URL = "https://www.instagram.com/reels/audio/1656648626250751/"
DEFAULT_CAPTION = "Automated Reel upload! #automation #tech"
PACKAGE_NAME = "com.instagram.android"


class InstagramReelUploader:
    def __init__(self, device_serial=None, package_name=PACKAGE_NAME, log_callback=None):
        self.package_name = package_name
        self.log_callback = log_callback or self._default_log
        self.is_cancelled = False
        self.device_serial = device_serial
        self.username = None

        self.log("info", f"Connecting to device{' ' + device_serial if device_serial else ''} via ADB...")
        try:
            self.d = u2.connect(device_serial) if device_serial else u2.connect()
            self.device_name = self.d.info.get("productName", "Unknown")
            # Initialize Human Biometrics & Anti-Bot Engine
            self.human = HumanBehaviorEngine(self.d)
            self.log("success", f"Connected: {self.device_name} (Serial: {self.d.serial}) | Anti-Bot Human Engine: ACTIVE")
            self._detect_username()
        except Exception as e:
            self.log("error", f"Failed to connect to ADB device: {e}")
            raise

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
        popups = [
            ("Not now", self.d(textMatches="(?i)^not now$")),
            ("Dismiss", self.d(textMatches="(?i)^dismiss$")),
            ("Got it", self.d(textMatches="(?i)^got it$")),
            ("Cancel", self.d(textMatches="(?i)^cancel$")),
            ("Allow", self.d(textMatches="(?i)^(allow|while using the app)$")),
            ("Don't allow", self.d(textMatches="(?i)^don't allow$")),
            ("Close button", self.d(descriptionMatches="(?i)^(close|dismiss)$")),
        ]
        for name, selector in popups:
            try:
                if selector.exists(timeout=0.1):
                    # Don't click Cancel if it's on the main video editor preview
                    if name == "Cancel" and self.d(resourceId="com.instagram.android:id/clips_right_action_button").exists:
                        continue
                    self.log("info", f"Self-healing: Auto-dismissed popup '{name}'.")
                    self.human.click(selector)
                    self.human.sleep(0.6)
                    return True
            except Exception:
                pass
        return False

    def wait_and_verify_screen(self, state_name, verify_fn, timeout=15, auto_dismiss=True):
        """
        Polls until verify_fn() returns True.
        Automatically dismisses popups, handles delays, and provides detailed diagnostics on failure.
        """
        self.check_cancelled()
        self.log("info", f"Verifying screen state: [{state_name}]...")
        start_time = time.time()

        while time.time() - start_time < timeout:
            self.check_cancelled()
            try:
                if verify_fn():
                    self.log("success", f"Verified state: [{state_name}]")
                    self.human.sleep(0.3, jitter_ratio=0.2)
                    return True
            except Exception:
                pass

            if auto_dismiss:
                self.handle_unexpected_popups()

            time.sleep(0.4)

        # DIAGNOSTIC REPORT ON FAILURE
        app_info = self.d.app_current()
        curr_activity = app_info.get("activity", "Unknown")
        curr_pkg = app_info.get("package", "Unknown")

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

        is_keyboard_open = self.d(resourceId="com.android.systemui:id/ime_switcher").exists

        diag_msg = (
            f"Screen verification timed out for [{state_name}]!\n"
            f"  Activity: {curr_pkg}/{curr_activity}\n"
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
        self.human.sleep(1.0)

        self.log("info", "Scanning media file into Android gallery...")
        self.d.shell(f'am broadcast -a android.intent.action.MEDIA_SCANNER_SCAN_FILE -d "file://{remote_path}"')
        self.human.sleep(1.5)
        self.log("success", "Video successfully added to device gallery.")
        return remote_path

    def open_audio_page(self, audio_url):
        """Step 1: Open Instagram Audio URL via intent and VERIFY audio screen loaded."""
        self.check_cancelled()
        self.log("info", f"Opening Instagram audio link: {audio_url}", step=1)
        self.d.shell(f'am start -a android.intent.action.VIEW -d "{audio_url}" {self.package_name}')

        # State Verification 1: Verify audio screen loaded with 'Use audio' button
        self.wait_and_verify_screen(
            "Audio Track Page",
            lambda: self.d(text="Use audio").exists or self.d(resourceId="com.instagram.android:id/use_in_camera_header_button").exists,
            timeout=16
        )
        self.log("success", "Audio page verified and ready.", step=1)

    def tap_use_audio(self):
        """Step 2: Click 'Use audio' with human touch biometrics and VERIFY Reel camera screen."""
        self.check_cancelled()
        self.log("info", "Tapping 'Use audio' button (Human touch)...", step=2)
        use_audio_btn = self.d(text="Use audio")
        if not use_audio_btn.exists:
            use_audio_btn = self.d(resourceId="com.instagram.android:id/use_in_camera_header_button")

        self.human.click(use_audio_btn)

        # State Verification 2: Verify Reel Camera Screen is loaded
        self.wait_and_verify_screen(
            "Reel Camera Screen",
            lambda: self.d(resourceId="com.instagram.android:id/gallery_preview_button").exists or
                    self.d(description="Gallery").exists or
                    self.d(resourceId="com.instagram.android:id/camera_shutter_button").exists,
            timeout=12
        )
        self.log("success", "Reel camera verified.", step=2)

    def open_gallery_and_select_video(self):
        """Step 3 & 4: Open Gallery, select video with human touch, and VERIFY preview editor."""
        self.check_cancelled()
        self.log("info", "Opening Gallery picker...", step=3)

        if self.d(text="REEL").exists:
            self.human.click(self.d(text="REEL"))
            self.human.sleep(0.6)

        gallery_btn = self.d(resourceId="com.instagram.android:id/gallery_preview_button")
        if not gallery_btn.exists:
            gallery_btn = self.d(description="Gallery")

        if gallery_btn.exists:
            self.human.click(gallery_btn)
        else:
            h = self.d.info.get("displayHeight", 1600)
            self.human.click((70, h - 80))

        # State Verification 3: Verify Gallery grid is open
        self.wait_and_verify_screen(
            "Gallery Grid",
            lambda: self.d(descriptionMatches="(?i).*thumbnail.*").exists or
                    self.d(resourceId="com.instagram.android:id/gallery_grid_item_thumbnail").exists or
                    self.d(resourceId="com.instagram.android:id/gallery_recycler_view").exists or
                    self.d(text="New reel").exists,
            timeout=12
        )
        self.log("success", "Gallery grid verified.", step=3)

        # Step 4: Select Video & VERIFY preview editor screen
        self.log("info", "Selecting latest video from gallery...", step=4)
        video_thumb = self.d(descriptionMatches="(?i).*video thumbnail.*")
        if video_thumb.wait(timeout=5):
            self.human.click(video_thumb[0])
        else:
            grid_items = self.d(resourceId="com.instagram.android:id/gallery_grid_item_thumbnail")
            if grid_items.wait(timeout=3):
                self.human.click(grid_items[0])
            else:
                self.human.click((360, 500))

        # State Verification 4: Verify Video Preview / Editor screen is loaded
        self.wait_and_verify_screen(
            "Video Preview Editor",
            lambda: self.d(resourceId="com.instagram.android:id/clips_right_action_button").exists or
                    self.d(text="Next").exists or
                    self.d(description="Next").exists,
            timeout=15
        )
        self.log("success", "Video loaded in preview editor.", step=4)

    def proceed_to_share_screen(self):
        """Step 5: Tap 'Next ->' on preview editor and VERIFY 'New reel' share screen."""
        self.check_cancelled()
        self.log("info", "Clicking 'Next' on preview screen...", step=5)
        next_btn = self.d(resourceId="com.instagram.android:id/clips_right_action_button")
        if not next_btn.exists:
            next_btn = self.d(text="Next")
        if not next_btn.exists:
            next_btn = self.d(description="Next")

        self.human.click(next_btn)

        # State Verification 5: Verify 'New reel' Share/Details screen is loaded
        self.wait_and_verify_screen(
            "New Reel Share Screen",
            lambda: self.d(resourceId="com.instagram.android:id/share_button").exists or
                    self.d(text="Share").exists or
                    self.d(resourceId="com.instagram.android:id/caption_input_text_view").exists,
            timeout=14
        )
        self.log("success", "Share screen verified.", step=5)

    def write_caption(self, caption_text):
        """Step 6: Enter caption, reliably force-hide soft keyboard, and verify screen state."""
        self.check_cancelled()
        if not caption_text:
            self.log("info", "No caption provided, skipping.", step=6)
            return

        self.log("info", f"Entering caption ({len(caption_text)} chars)...", step=6)
        caption_box = self.d(resourceId="com.instagram.android:id/caption_input_text_view")
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
                lambda: self.d(resourceId="com.instagram.android:id/share_button").exists or
                        self.d(text="Share").exists or
                        self.d(resourceId="com.instagram.android:id/caption_input_text_view").exists,
                timeout=10
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

                toggles = self.d(resourceId="com.instagram.android:id/toggle")
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
        back_btn = self.d(resourceId="com.instagram.android:id/action_bar_button_back")
        if back_btn.exists:
            self.human.click(back_btn)
        else:
            self.d.press("back")

        # State Verification 8: Verify successfully returned to Share screen
        self.wait_and_verify_screen(
            "Returned to Share Screen",
            lambda: self.d(resourceId="com.instagram.android:id/share_button").exists or
                    self.d(text="Share").exists or
                    self.d(resourceId="com.instagram.android:id/caption_input_text_view").exists,
            timeout=10
        )
        self.log("success", "Toggles configured and confirmed.", step=8)

    def share_reel(self):
        """Step 9: Click Share and VERIFY share screen is dismissed (upload commenced)."""
        self.check_cancelled()
        self.log("info", "Tapping 'Share' button (Human touch)...", step=9)
        share_btn = self.d(resourceId="com.instagram.android:id/share_button")
        if not share_btn.exists:
            share_btn = self.d(text="Share")

        # If share button is scrolled off-view, scroll down to reveal it
        if not share_btn.exists:
            self.human.scroll_down(stride_ratio=0.35)
            self.human.sleep(0.5)
            share_btn = self.d(resourceId="com.instagram.android:id/share_button")
            if not share_btn.exists:
                share_btn = self.d(text="Share")

        if not share_btn.wait(timeout=10):
            raise TimeoutError("Share button not found on Share screen.")

        self.human.click(share_btn)

        # State Verification 9: Verify Share screen is dismissed
        self.wait_and_verify_screen(
            "Share Screen Dismissed (Upload Started)",
            lambda: not self.d(resourceId="com.instagram.android:id/share_button").exists,
            timeout=12
        )
        self.log("success", "Reel submitted for upload!", step=9)

    def exit_audio_and_go_to_home_feed(self):
        """Exits the underlying audio activity and VERIFIES we are on Instagram Home feed."""
        self.log("info", "Exiting audio screen and navigating to Instagram Home feed...", step=10)

        # 1. Close audio/modal activities until feed_tab is visible
        for attempt in range(6):
            if self.d(resourceId="com.instagram.android:id/feed_tab").exists or self.d(description="Home").exists:
                break
            
            back_btn = self.d(resourceId="com.instagram.android:id/action_bar_button_back")
            if not back_btn.exists:
                back_btn = self.d(description="Back")
            
            if back_btn.exists:
                self.human.click(back_btn)
            else:
                self.d.press("back")
            self.human.sleep(0.8)

        # 2. Tap Home tab icon (double-tap to scroll to top)
        home_tab = self.d(resourceId="com.instagram.android:id/feed_tab")
        if not home_tab.exists:
            home_tab = self.d(description="Home")

        if home_tab.wait(timeout=6):
            self.human.click(home_tab)
            self.human.sleep(0.5)
            self.human.click(home_tab)  # Double-click to scroll feed to absolute top

        # State Verification 10: Verify on Home feed
        self.wait_and_verify_screen(
            "Instagram Home Feed (Top)",
            lambda: self.d(resourceId="com.instagram.android:id/feed_tab").exists or
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
        fullscreen_share = self.d(resourceId="com.instagram.android:id/direct_share_button")
        if not fullscreen_share.exists:
            fullscreen_share = self.d(resourceId="com.instagram.android:id/button_share")
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
        share_post_btn = self.d(resourceId="com.instagram.android:id/row_feed_button_share")
        if not share_post_btn.exists:
            share_post_btn = self.d(resourceId="com.instagram.android:id/direct_share_button")

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

        profile_tab = self.d(resourceId="com.instagram.android:id/profile_tab")
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
            thumbs = self.d(resourceId="com.instagram.android:id/preview_clip_thumbnail")
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
            if video_path:
                self.push_video_to_device(local_video_path=video_path)

            # Step 1: Open Audio page & verify
            self.open_audio_page(audio_url)

            # Step 2: Click 'Use audio' & verify Reel camera opened
            self.tap_use_audio()

            # Step 3 & 4: Open gallery, verify picker, select video & verify preview editor
            self.open_gallery_and_select_video()

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
