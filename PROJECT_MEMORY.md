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

---

### [2026-11-08 19:42:00] Clone-Aware Accounts, Login Automation, & Spreadsheet Engine
- **User Request**: "/goal E:/Download/Code/Platfrom-tool/plan/2026-11-08_clone-account-login-import-export.md Follow these instrution"
- **Context & Problem**: Instoper previously only automated a single hardcoded package (`com.instagram.android`) and lacked account persistence, clone package inventory, credential vaulting, spreadsheet import/export, and single/batch login with TOTP 2FA.
- **Action Taken & Changes**:
  - `vault.py`: Built Windows DPAPI protection (`CryptProtectData` / `CryptUnprotectData`) for local secrets, plus PBKDF2 HMAC-SHA256 (120k rounds) + AES-256-GCM passphrase-encrypted backup export/import with wrong-passphrase rejection.
  - `db.py`: Created SQLite relational database with partial unique index `idx_account_device_package` preventing two accounts from sharing the same device/package pair, cascade deletion, clone tracking, and audit logs.
  - `package_scanner.py`: Built ADB package discovery scanning third-party and clone-candidate apps, resolving foreground packages, and tracking operator confirmation status.
  - `excel_manager.py`: Implemented openpyxl Excel metadata import/export with formula injection neutralization (`=`, `+`, `-`, `@`), row-level error validation, and create/update classification.
  - `login_flow.py`: Built `InstagramAccountLoginService` with human cadence typing, RFC 6238 TOTP authenticator generation via `pyotp`, and automated pause on unsupported challenges (CAPTCHA, SMS, checkpoints). Created `BatchLoginManager` for isolated sequential multi-account execution.
  - `app.py`: Registered 15+ REST endpoints (`/api/accounts`, `/api/clones`, `/api/excel`, `/api/secrets`, `/api/batch-login`). Preserved existing Reel Studio workflow with optional package override.
  - `templates/index.html`: Added Tab Switcher (Reel Studio vs Accounts & Clones) and created a comprehensive dashboard with accounts table, clone inventory, batch login monitor, and modals for account editing, Excel preview, and secret vaulting.
  - `tests/test_full_suite.py`: Created 8 comprehensive automated unit/integration tests validating all success criteria.
- **Verification & Outcome**: Ran test suite (`python -m unittest tests/test_full_suite.py`) with 8 tests passing in 0.286s with 100% success. Verified server routes and UI integrity.

---

### [2026-11-08 20:31:00] Data Point Definition, Universal File Importer, & Interactive Column Matcher
- **User Request**: "bro make sure when user add account credension so defilne which data point you need and when user uplode that file where it save after uplode you make give user a screen where thye can Lable data point according our tools need... Username , Password , 2fa key our tool tag , note..."
- **Context & Problem**: Operators upload spreadsheets from different sources/vendors with non-standard column headers (e.g. `Account`, `Pass`, `2FA Secret`, `Niche Tag`). They need clear visibility into which data points are required, where they are saved, and an interactive UI to label/match columns with a live preview before importing.
- **Action Taken & Changes**:
  - `db.py`: Added `tag` column to `accounts` schema and CRUD methods with auto-migration (`PRAGMA table_info`).
  - `excel_manager.py`:
    - Defined `TOOL_DATA_POINTS` (Username, Password, 2FA Key, Tool Tag/Niche, Notes, Proxy Endpoint, Device Serial, Package Name) with explicit storage classification (Database vs DPAPI Vault).
    - Built `parse_file_headers_and_rows()` supporting XLSX, CSV, TSV with automated delimiter and encoding detection.
    - Added `suggest_auto_mapping()` with synonym heuristics matching vendor variations.
    - Built `import_accounts_with_mapping()` supporting custom column bindings, metadata persistence, and DPAPI vault credential encryption.
  - `app.py`: Added `/api/accounts/parse-file` and `/api/accounts/import-mapped` endpoints. Updated single account CRUD for tags.
  - `templates/index.html`:
    - Transformed import modal into a 2-step **Universal File Import & Column Matcher** with clear data storage badges (Database vs DPAPI Vault), dynamic dropdowns, and a live 3-row preview table.
    - Updated Single Account Modal with Tool Tag/Niche field and storage labels.
    - Updated Accounts Table with tag badges.
  - `tests/test_full_suite.py`: Added `test_universal_file_parsing_and_column_matcher` test case (9/9 passed).
- **Verification & Outcome**: Ran test suite (`9 passed in 0.343s`). Tested CSV import with custom columns (`Account`, `Pass`, `2FA Secret`, `Niche Tag`). DPAPI encryption and database metadata verified.


### [2026-11-08 21:05:00] Seven Surgical Priority Fixes: Security, Session Verification & Concurrency
- **User Request**: "Review the current implementation against the findings below. Fix these issues surgically; preserve the existing Reel workflow and UI conventions. Do not refactor unrelated code or add speculative features. Priority fixes: 1. Prevent spreadsheet-driven XSS; 2. Keep imported secrets out of the browser; 3. Protect proxy credentials; 4. Verify the logged-in username before marking an account successful; 5. Make batch stop/start safe; 6. Fix clone scan device targeting; 7. Fix spreadsheet parsing edge cases."
- **Context & Problem**: 
  - Spreadsheet cells and database values were previously interpolated directly into HTML strings (`innerHTML`), posing XSS risks.
  - The parse-file API returned raw parsed rows to the client browser, exposing passwords and TOTP seeds in client memory/network traffic.
  - Proxy URLs with embedded credentials (`user:pass@host:port`) were not separating credentials into DPAPI storage, risking exposure in APIs and standard Excel exports.
  - An Instagram account was previously marked successful based solely on feed/profile visibility without verifying that the displayed username matched the requested account.
  - Batch stop/start lacked thread transition locking and did not prevent worker overlap when stop was followed immediately by start.
  - Package scanner resolved device serial but passed `device_serial=None` to internal ADB commands, causing command drift in multi-device environments.
  - Spreadsheet parsing dropped empty header columns, corrupting subsequent column alignment; duplicate headers were not rejected; and `.xls` files caused openpyxl runtime errors.
- **Action Taken & Changes**:
  - `excel_manager.py`:
    - Added in-memory ephemeral import session store (`_import_sessions`, `_import_sessions_lock`, 15-minute TTL) generating single-use `import_token`.
    - Removed `raw_rows` from `parse_file_headers_and_rows` return payload. Masked sensitive preview data (`••••••••` for passwords/seeds, redacted proxy URLs).
    - Explicitly rejected legacy `.xls` format with a clear error prompt.
    - Preserved 1-based column indices when headers are blank; rejected blank headers that contain data in subsequent rows; rejected duplicate headers.
  - `app.py`:
    - Updated `/api/accounts/import-mapped` to accept `import_token` and pop the session from `excel_manager`.
  - `vault.py`:
    - Updated `redact_proxy_url` to completely redact embedded credentials (`username` and `password`) as `http://***:***@host:port`.
  - `package_scanner.py`:
    - Resolved `serial` upfront once and routed that exact serial to every ADB call (`pm list packages -3`, `pm list packages instagram`, `dumpsys window`) and database write (`db.upsert_clone`). Added `db_path` parameter support.
  - `login_flow.py`:
    - Implemented `verify_session_identity(package_name, expected_username) -> bool` to inspect action bar titles and profile handle before granting login success.
    - Updated `login_account` and `_solve_totp_challenge` to require identity confirmation; marks account as `needs_action` if session identity is unconfirmed.
    - Upgraded `BatchLoginManager` with `threading.Lock()` across all state transitions. Implemented `"stopping"` state, guarded against worker thread overlap, and cleanly finalized state in `try...finally`. Added `db_path` support.
  - `templates/index.html`:
    - Upgraded `escapeHtml` to safely handle null, undefined, and special characters (`&`, `<`, `>`, `"`, `'`).
    - Escaped all user and database values in accounts table, clone list, modal titles, live preview, and error banners.
    - Updated upload and commit workflow to pass `import_token`.
  - `tests/test_full_suite.py`:
    - Added comprehensive regression tests for all 7 fixes: XSS/formula neutralization, server token isolation, proxy credential protection, session identity verification, batch stop/start overlap prevention, multi-device clone targeting isolation, spreadsheet edge cases, and official RFC 6238 published test vectors.
- **Technical Mechanism**: Ephemeral backend sessions prevent secret exposure to browser DOM/memory; DPAPI encrypts proxy secrets while storing clean endpoint metadata in relational tables; UI Automator hierarchy analysis checks screen text against target account handles; thread locks enforce mutual exclusion on worker lifecycle transitions.
- **Verification & Outcome**: Ran full test suite (`python -m unittest tests/test_full_suite.py`): all 13 tests passed in 0.536s. Verified clone package scanner on physical device `0624531123033570`. Re-launched Flask app on port 5000. Existing Reel workflow package default (`com.instagram.android`) preserved.


### [2026-11-08 21:50:00] Clone APK Installation Pipeline, Auto-Detection & Account Binding
- **User Request**: "follow the instruction C:\Users\Lovekush Gola\.agents\skills\deep-plan\scripts\validate_plan.py' 'plan/2026-11-08_install-and-manage-instagram-clone-apps.md"
- **Context & Problem**: Operators needed the ability to install custom Instagram clone APKs (e.g. from App Cloner, Dual Space, or custom packages) directly from the PC to connected Android devices via ADB, auto-detect the newly installed package ID, display the clones in the inventory, and bind specific Instagram accounts to target devices and package pairs without disrupting the default Reel workflow (`com.instagram.android`).
- **Action Taken & Changes**:
  - `package_scanner.py`:
    - Implemented `InstallResultStatus` enum defining granular outcomes: `SUCCESS`, `ALREADY_EXISTS`, `SIGNATURE_MISMATCH`, `DOWNGRADE_ERROR`, `STORAGE_ERROR`, `CONFIRMATION_REQUIRED`, `DEVICE_OFFLINE`, `INVALID_APK`, `ERROR`.
    - Added `get_third_party_packages(device_serial)` to take pre- and post-installation package snapshots.
    - Implemented `install_apk_on_device(apk_path, device_serial, timeout=90)` executing `adb -s <serial> install -r <apk_path>`, parsing stdout/stderr, classifying error modes, and auto-detecting the new package ID from the package set diff.
  - `app.py`:
    - Added `MAX_APK_SIZE = 250 * 1024 * 1024` (250MB limit) and `tempfile.mkstemp` intake under `uploads/temp_apk`.
    - Implemented `/api/clones/install-apk` route supporting streaming upload, file validation, execution of `install_apk_on_device`, automatic package re-scanning upon success, and guaranteed cleanup of temporary `.apk` files in a `finally` block.
  - `templates/index.html`:
    - Added "Install APK" button and modal (`apkInstallModal`) with dropzone, target device selector (`adb -s <serial>`), USB permission notice, and real-time status output.
    - Added "Assign / Bind" button on clone inventory cards (`openAddAccountWithClone`), opening the account form pre-populated with target package name and device serial.
    - Populated clone package datalist (`#clonePackagesDatalist`) and devices datalist (`#devicesDatalist`) to provide auto-completion for account bindings.
    - Integrated the Universal Column Matcher with tokenized session flow and live redacted preview.
  - `tests/test_full_suite.py`:
    - Added `test_apk_scanner_installation_modes_and_diff` validating missing file rejection, package diff auto-detection on success, signature mismatch, version downgrade, insufficient storage, and USB confirmation timeout.
    - Added `test_api_clones_install_apk_endpoint_and_cleanup` validating missing file 400, non-.apk rejection 400, oversized file rejection 400, successful APK installation via test client, and guaranteed temporary file deletion in `uploads/temp_apk`.
- **Technical Mechanism**: Pre- and post-install set diffing of `pm list packages -3` reliably extracts package identifiers without requiring external binary parsing utilities (e.g. aapt/apktool); temporary files are strictly cleaned up via Python file descriptor management and context teardowns; ADB commands explicitly target resolved device serials to maintain multi-device isolation.
- **Verification & Outcome**: Ran full test suite (`python -m unittest tests/test_full_suite.py`): all 15 tests passed in 0.966s. Restarted Instoper web server as a background daemon on port 5000. Verified existing Reel workflow defaults remain preserved.
