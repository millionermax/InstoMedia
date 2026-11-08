# Project Living Memory & Audit Log
## NectarLink Reel Studio — Continuous Context Ledger

> **Mandatory Rule for All AI Agents**: Whenever any modification, bug fix, feature addition, or refactor is performed in this repository, the agent MUST immediately append an entry to the end of this file documenting the exact changes, files touched, root cause, and verification status.

---

## Standard Entry Format Template

```markdown
### [YYYY-MM-DD HH:MM:SS] <Headline / Feature / Fix Title>
- **User Request**: "<Exact or paraphrased user instruction>"
- **Context & Problem**: <What was the problem or requirement? Why did it happen?>
- **Action Taken & Changes**:
  - `<file_path>`: <Exact changes made in code>
- **Technical Mechanism**: <How the fix/feature works under the hood>
- **Verification & Outcome**: <How was it verified? What was the result?>
```

---

## Historical Audit Log

### [2026-11-08 07:15:00] Inception & Workflow Architecture Alignment
- **User Request**: Automate Instagram Reel uploading on Android device following the exact 11 screenshots workflow in `C:\Users\Lovekush Gola\Downloads\Nectarlink\workflow`.
- **Context & Problem**: Previously, basic ADB scripts lacked deep audio integration, were failing randomly due to blind `time.sleep`, and couldn't copy the uploaded Reel URL.
- **Action Taken & Changes**:
  - `adb.py`: Designed the 11-step structured pipeline matching the reference screenshots.
  - Added support for targeting connected device `LD7j-AS` (`0624531123033570`).
- **Technical Mechanism**: Mapped Instagram's internal resource IDs (`feed_tab`, `use_audio`, `gallery_picker`, `caption_input_text_view`, `share_button`, `row_feed_share_progressbar`).
- **Verification & Outcome**: Verified connection to device and pushed media successfully.

---

### [2026-11-08 07:45:00] Web Control Studio & Live Android Mirror Implementation
- **User Request**: Build a modern visual GUI (Dashboard) where the user can input audio URLs, video files, captions, view a real-time live mirror of the phone, see live color-coded logs, and track an 11-step visual status pipeline.
- **Action Taken & Changes**:
  - `app.py`: Created Flask server with `/api/stream` (MJPEG streaming via `d.screenshot()` at ~10–12 FPS), `/api/tap` (interactive touch forwarding), `/api/start` (async workflow execution), and `/api/logs` (SSE real-time log emitter).
  - `templates/index.html`: Crafted modern dark-mode Studio dashboard with glassmorphism, Instagram brand gradients, phone mirror frame, live terminal, and 11-step pipeline indicators.
  - `start_gui.bat`: Created 1-click Windows launcher.
- **Technical Mechanism**: Threaded background frame grabber converts Android screen to JPEG byte streams for low-latency web browser consumption.
- **Verification & Outcome**: Server booted at `http://127.0.0.1:5000` with functioning video drag-and-drop and mirror touch interactivity.

---

### [2026-11-08 08:30:00] Android Activity Resolution & U2 Compatibility Fix
- **User Request**: Fix automation crashing when launching Instagram or querying current app.
- **Context & Problem**: 
  1. `AttributeError: 'UIAutomatorServer' object has no attribute 'current_app'` occurred because modern `uiautomator2` uses `d.app_current()`.
  2. Launching `com.instagram.android/.activity.MainActivity` failed because Instagram's main activity was moved to `.activity.MainTabActivity` and `.com.instagram.mainactivity.InstagramMainActivity`.
- **Action Taken & Changes**:
  - `adb.py`: Replaced `d.current_app` with `d.app_current()`. Updated activity launchers to resolve correct active intent targets.
- **Verification & Outcome**: Clean intent dispatch without ActivityNotFound exceptions.

---

### [2026-11-08 09:10:00] Human Behavior Engine & Anti-Bot Touch Biometrics
- **User Request**: Research anti-bot software and human farm behavior. Prevent Instagram from detecting automated clicks and swipes.
- **Context & Problem**: Mechanical ADB clicks at exact button centers and linear `adb shell input swipe` gestures trigger Instagram's bot heuristics.
- **Action Taken & Changes**:
  - `human_behavior.py`: Built `HumanBehaviorEngine` featuring:
    1. **Cubic Bézier Swiping**: 2 randomized control points off-line with sinusoidal ease-in/ease-out acceleration and micro-tremor noise.
    2. **Gaussian Click Jitter**: Off-center click calculation with normal distribution ($\sigma = 2\text{px}-4\text{px}$).
    3. **Touch Dwell**: Emulated finger contact pressure with 65ms–125ms touch-down duration before release.
    4. **10x Human Cadence Typing**: Word-chunk streaming with 5ms–12ms micro-delays (<2s for full captions).
  - `adb.py`: Replaced all raw `d.click()` and `d.swipe()` calls with `self.human.click()` and `self.human.scroll_down()`.
- **Verification & Outcome**: Organic touch inputs with zero straight lines or robotic timing.

---

### [2026-11-08 12:56:00] Soft Keyboard Blockade & Hardware KeyEvent 4 Fix
- **User Request**: `Workflow failed at state check: Screen verification timed out for: [Caption Entered (Keyboard Dismissed)] after 6s.`
- **Context & Problem**: When caption is typed, the Android soft keyboard (Gboard/AOSP IME) remained open, covering the bottom 50% of the screen where the `Share` button and `More options` reside. `d.press("back")` was intercepted by the text input box instead of closing the IME.
- **Action Taken & Changes**:
  - `adb.py`: Switched to hardware Android event `d.shell("input keyevent 4")` (KEYCODE_BACK) which specifically dismisses the active input method without losing focus or deleting text.
  - Made verification check for either `share_button` OR `caption_input_text_view` so minor layout shifts don't cause false failures.
- **Verification & Outcome**: Keyboard closes cleanly; Step 6 completes in <2s without timing out.

---

### [2026-11-08 13:03:00] Audio Activity Freeze & Home Feed Navigation Fix
- **User Request**: "they still freze in same audio page after uplode" (Phone stayed on audio track page while automation waited on Home feed).
- **Context & Problem**: Opening an Instagram audio URL launches `ModalActivity` / `ClipsAudioUrlHandlerActivity`. When the Reel is shared, Instagram finishes the composer sub-activity and returns to the parent audio page. The automation was looking for the upload progress bar on the Home feed, which was not visible because the audio page was covering it.
- **Action Taken & Changes**:
  - `adb.py` (`exit_audio_and_go_to_home_feed`): Implemented active back navigation loop (up to 6 retries) until `feed_tab` is visible.
  - Once on Home feed, double-taps `feed_tab` to scroll feed to absolute top where the progress bar and newly posted Reel appear.
- **Technical Mechanism**: Evaluates `feed_tab.exists`; if false, taps `action_bar_button_back` or hardware back to pop the modal stack until reaching `InstagramMainActivity`.
- **Verification & Outcome**: Tested via ADB test harness; audio page pops immediately and lands on Home feed.

---

### [2026-11-08 13:18:00] Fast Snappy Scroll Optimization (Ponytail Philosophy)
- **User Request**: "Bro scroll time ko increase kar bahut jayda slow hai" (Make scroll fast and snappy).
- **Context & Problem**: Swiping down to find "More options" and privacy toggles was taking 4–6 seconds due to high swipe duration (0.35s–0.55s), 0.4s post-swipe pause, and low viewport coverage (30%).
- **Action Taken & Changes**:
  - `human_behavior.py`:
    - Swipe duration cut from `0.35s–0.55s` to `0.12s–0.20s`.
    - Post-swipe settling pause cut from `0.4s` to `0.12s`.
    - Viewport coverage increased from `30%` to `45%` per scroll (`stride_ratio=0.45`).
  - `adb.py`: Removed redundant `sleep(0.5)` calls after scroll commands.
- **Verification & Outcome**: Scrolling completes in <1s while preserving curved Bézier trajectories for anti-bot safety.

---

### [2026-11-08 13:25:00] Product Requirements Document (PRD.md) Generation
- **User Request**: Create a comprehensive PRD covering description, features, how it works, tech stack, architecture, file structure, and failure recovery.
- **Action Taken & Changes**:
  - `PRD.md`: Authored full enterprise-grade PRD with executive summary, Mermaid flowcharts, tech stack breakdown, stealth touch models, and failure recovery matrix.
- **Verification & Outcome**: File created cleanly at repository root.

---

### [2026-11-08 13:34:00] Project Memory Ledger & Agent Instruction Hook Setup
- **User Request**: "AB EK NEW fILE BANA Memory file... ab tk tune Step by step kya kiya problems aayi kaise fix ki and instruction add kar jisse agent her chat me changes report kare."
- **Context & Problem**: Need continuous context retention across chat resets and a standardized audit log so any future agent knows exactly what was built, why errors happened, and how to fix them without losing context.
- **Action Taken & Changes**:
  - `PROJECT_MEMORY.md`: Created continuous living ledger with chronological logs from project inception to date, including technical root-causes, code diffs, and verification steps.
  - `GEMINI.md`: Established workspace instruction rule binding all future AI agent sessions to auto-append updates to `PROJECT_MEMORY.md` after every modification.
  - `.agents/rules/project_memory_rule.md`: Added hierarchical discovery rule for agent runtime engine.
- **Verification & Outcome**: Rules verified and placed in workspace discovery paths. Every future change will be tracked automatically.

---

### [2026-11-08 19:25:00] Instoper Tool Branding & GitHub Repository Setup
- **User Request**: "Hey bro can You push the Code into my github Make sure call this tool Instoper"
- **Context & Problem**: User requested publishing the codebase to their GitHub account (`ilovekushgola`) under the tool name **Instoper**.
- **Action Taken & Changes**:
  - `templates/index.html`: Updated brand headers and footers to **Instoper**.
  - `README.md`: Polished with complete documentation, 11-step pipeline, quickstart, architecture, and MIT license under Instoper.
  - `PRD.md`: Aligned tool name and architecture diagrams to Instoper.
  - `requirements.txt`: Created dependency manifest (`uiautomator2`, `flask`, `werkzeug`, `pillow`).
  - `.gitignore`: Configured to exclude large uploaded test videos, `__pycache__`, and OS artifacts.
  - Git repository initialized, remote created on GitHub (`ilovekushgola/Instoper`), and pushed to `main` branch.
- **Verification & Outcome**: Code committed and pushed to `https://github.com/ilovekushgola/Instoper`.
