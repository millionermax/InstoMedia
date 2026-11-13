# Module 2: Device & Account Onboarding Implementation Plan

> **For agentic workers:** Implement this plan in order. Use focused tests for each task. Stop after Task 7 and wait for user review before starting Module 3.

**Goal:** Let an operator choose a connected Android device and confirmed Instagram package, open the already-signed-in app, capture the account owner's profile summary, review it, and persist the account and profile snapshot.

**Architecture:** Reuse Flask, SQLite, ADB, and uiautomator2. Add a small profile-capture service that connects to the explicitly selected device serial, launches user-selected confirmed packages sequentially (one foreground app at a time), opens each app's Profile tab when its accessibility element is available, and reads accessible profile labels. Show a preview and require operator confirmation before creating/updating account records; store profile metrics as timestamped snapshots.

**Tech Stack:** Python 3, Flask, SQLite, ADB, uiautomator2, existing Jinja/HTML/Tailwind browser UI, `unittest`.

**Spec:** `PRD.md`, completed `plan/2026-11-12_module-1-foundation.md`, and the agreed Devices & Accounts onboarding workflow in the current conversation.

## User-approved Accounts Rebuild Extension (2026-11-12)

The Accounts rebuild plan now extends the original single-package onboarding flow:

- The operator selects one online device, scans and maps installed Instagram/clone candidates, then may select multiple confirmed packages on that same device.
- Open and inspect selected apps **sequentially**, one foreground package at a time. Do not run concurrent device/app work or add a background task queue.
- Capture only the currently signed-in account's own Profile screen (or an account the operator is authorized to manage): username, follower count, and post count.
- Collect per-package results into a review list. The operator may edit, skip, or select rows and apply shared Category/Tag defaults with per-row overrides, then save selected accounts with one explicit confirmation.
- Manual and bulk profile-account creation do not require or prompt for passwords, 2FA seeds, or login credentials. If an app is signed out or challenged, mark it skipped/needs attention; do not ask for credentials or automate login.
- This extension supersedes the original one-package-per-session limitation below only for this sequential, user-selected multi-package capture flow. All other device binding, profile ownership, confirmation, privacy, and no-bypass constraints remain in force.

## Global Constraints

- This module is browser-based and uses the existing local Flask app and SQLite database.
- Select one device per run. The operator may select multiple confirmed clone packages on that device, but launch/capture them sequentially, one at a time; do not run parallel device tasks or add a background task queue.
- Only inspect the currently signed-in account's own profile, or an account the operator is authorized to manage. Do not crawl arbitrary profile URLs or include unrelated profiles in a selected package batch.
- Do not request or automate credential entry or 2FA in this onboarding flow. If a selected app is signed out or blocked by a challenge, mark that package skipped/needs attention and continue only when safe; the operator may separately resolve it on the phone.
- Do not add private Instagram APIs, anti-detection behavior, proxy rotation, or methods to bypass platform limits.
- ADB and uiautomator2 commands must always receive the explicitly selected device serial; never silently fall back to the first device for onboarding actions.
- Use the existing package confirmation workflow. Require the selected package to be installed on the selected device and confirmed before launch/capture.
- Profile metrics are a best-effort snapshot. If a value cannot be read or parsed reliably, store `NULL` and let the operator correct it; never guess.
- Do not store raw accessibility XML, screenshots, passwords, TOTP seeds, or proxy credentials in profile snapshot rows.
- Reuse Module 1 account category/tag support. Preserve all existing account, clone, Excel, vault, login, and Reel behavior.

## Review Focus

- ADB may return `device`, `unauthorized`, `offline`, or no device; only online/authorized devices can be selected for capture.
- Two connected phones must never be confused: scan, launch, capture, and persistence all use the same explicit serial.
- Package names can be misleading or omit Instagram-like keywords; detected candidates require operator confirmation, and a package not currently installed must not launch.
- The visible profile may belong to the wrong account; do not create/update an account until the captured handle is shown and explicitly confirmed.
- Counts can be missing, localized, abbreviated (for example `1.2K`), or stale; unknown values remain empty and preview values are editable before save.

---

## Scope and Acceptance Criteria

The Accounts page gains a guided **Add account from device** flow:

1. Select one online connected device.
2. Scan and map installed Instagram/clone candidates for that device. Let the operator select one or more confirmed packages.
3. Process selected packages sequentially. Launch one exact package at a time. If it is signed out or a security challenge appears, mark it skipped/needs attention and continue only when safe; do not request credentials.
4. Navigate to that app's own Profile tab when a supported accessibility selector is found; otherwise instruct the operator to open their own Profile tab manually, then retry or skip that package.
5. Capture username, post count, and follower count from each selected package's visible profile screen.
6. Show all results in an editable review list with source device/package, per-row status, duplicate warnings, Category/Tag defaults and per-row overrides. Let the operator skip rows or select which accounts to save.
7. Save selected rows after one explicit bulk confirmation. Preserve successful saves if another row fails; show failed rows for retry. Manual account entry uses the same profile-only fields and does not require credentials.

Acceptance criteria:

- Device and package choices remain correctly bound throughout scan, launch, capture, and save.
- No selected package is treated as captured until its username/profile screen is identified and shown in review; no account is saved until the operator confirms selected rows.
- A selected multi-package run opens no more than one app at a time and reports success/skip/error for every package.
- Missing or ambiguous counts do not prevent the operator from saving a manually reviewed profile, but remain `NULL` unless the operator enters a value.
- Profile snapshots persist with capture time and source package; prior snapshots remain available.
- Existing duplicate device/package and unique username rules remain enforced with clear UI errors.
- Unit/API tests pass and an authorized physical-device walkthrough of sequential multi-package capture is recorded when such a device is available; no credentials are requested or logged.

## Approaches Considered

| Approach | Benefits | Costs / risks | Decision |
|---|---|---|---|
| Operator enters all account details manually | Most reliable across app versions | Repeats data entry and does not satisfy clone-to-account onboarding | Keep as fallback |
| Read the visible profile screen through uiautomator2, then require review | Reuses existing device stack, limits capture to the selected app, and lets user correct uncertain data | Accessibility labels vary by Instagram/clone version; capture may be partial | **Selected** |
| Call private Instagram endpoints or scrape arbitrary profiles | Could return more fields automatically | Fragile, outside the native UI workflow, and risks collecting data beyond the account being onboarded | Excluded |

## File Map

- `package_scanner.py` â€” expose connected ADB device status and package availability for the selected serial; do not pick a device implicitly.
- `profile_capture.py` â€” new module for selected-package launch, Profile-tab navigation, UI hierarchy parsing, and count normalization.
- `db.py` â€” add profile snapshot persistence and account snapshot retrieval while reusing existing category/tag assignments.
- `app.py` â€” add device list, launch, capture, confirm, and profile-history endpoints.
- `templates/index.html` â€” add the guided onboarding UI in Accounts and surface the latest profile summary/history.
- `tests/test_full_suite.py` â€” add mocked device, package, parser, persistence, route, and regression tests.
- `PROJECT_MEMORY.md` â€” record actual implementation files and verifications after work is complete.

## Implementation Tasks

### Task 1: Return explicit connected-device inventory

**Files:**
- Modify: `package_scanner.py`
- Modify: `app.py`
- Test: `tests/test_full_suite.py`

**Interfaces:**
- Consumes: `package_scanner.list_connected_devices()` and current clone inventory.
- Produces: `GET /api/devices` returning `devices: [{serial, status, product_name}]`; only ADB `device` status is selectable, while unauthorized/offline entries are visible but disabled.

- [ ] **Step 1: Add `test_list_devices_preserves_adb_status_and_serial`**. Mock `adb devices` output with one device, one unauthorized device, and one offline device; assert all serial/status values are parsed without selecting an invalid device.
- [ ] **Step 2: Add `test_device_list_endpoint_marks_selectability`**. Assert only `status == 'device'` is selectable and no-device output returns an empty list.
- [ ] **Step 3: Implement `GET /api/devices`** using the existing ADB wrapper; include product name only when available, and do not persist transient connections in SQLite.
- [ ] **Step 4: Run the focused device tests and confirm they pass.**

### Task 2: Make package checks and launch explicitly device-scoped

**Files:**
- Modify: `package_scanner.py`
- Test: `tests/test_full_suite.py`

**Interfaces:**
- Consumes: Task 1 device response and existing clone scan/confirmation DB records.
- Produces:
  - `require_online_device(device_serial: str) -> Dict[str, str]`
  - `validate_confirmed_package(device_serial: str, package_name: str) -> Dict[str, Any]`

- [ ] **Step 1: Add tests for missing serial, offline/unauthorized device, package absent, unconfirmed package, and confirmed package.** Assert every ADB command includes the selected serial.
- [ ] **Step 2: Implement `require_online_device`** so unknown or non-`device` serials return a specific error; no first-device fallback is allowed in onboarding.
- [ ] **Step 3: Implement `validate_confirmed_package`** to verify the package is installed on the selected device and has a confirmed clone inventory row for that same serial. Return a structured result without launching the app.
- [ ] **Step 4: Run the focused package/launch tests and confirm they pass.**

### Task 3: Build the profile capture parser and service

**Files:**
- Create: `profile_capture.py`
- Test: `tests/test_full_suite.py`

**Interfaces:**
- Produces:
  - `open_profile_app(device_serial: str, package_name: str) -> Dict[str, Any]`
  - `capture_profile(device_serial: str, package_name: str, expected_username: Optional[str] = None) -> Dict[str, Any]`
  - Return keys: `status`, `device_serial`, `package_name`, `profile_handle`, `posts_count`, `followers_count`, `raw_labels`, `needs_manual_profile_navigation`, `message`.
- `raw_labels` is returned only as a bounded preview payload and is never persisted; do not return full UI XML.

- [ ] **Step 1: Add parser tests** for `123`, `1,234`, `1.2K`, `2M`, empty labels, ambiguous localized abbreviations, and unrelated numbers. Unparseable values must become `None`.
- [ ] **Step 2: Add service tests with a mocked uiautomator2 device** for Profile tab available, Profile tab unavailable, signed-out screen, wrong expected handle, missing counts, and successful capture.
- [ ] **Step 3: Implement `open_profile_app`** using `uiautomator2.connect(device_serial)` and `device.app_start(package_name, stop=False)` after Task 2 validation; serialize operations by device serial.
- [ ] **Step 4: Implement `capture_profile`** to verify the selected package is foregrounded, detect signed-out/challenge states, and open Profile only when one supported accessibility selector is unambiguous. Never launch an arbitrary profile URL.
- [ ] **Step 5: Verify the profile header before returning data.** For an existing account, require a handle match; for a new account, return the observed handle for operator confirmation.
- [ ] **Step 6: Return `needs_manual_profile_navigation` instead of guessing** when the app is signed in but the profile screen or handle cannot be reliably identified. Do not save until a subsequent capture succeeds or the user enters a handle manually.
- [ ] **Step 7: Run the focused parser/service tests and confirm they pass.**

### Task 4: Persist timestamped profile snapshots

**Files:**
- Modify: `db.py`
- Test: `tests/test_full_suite.py`

**Interfaces:**
- Produces:
  - `add_profile_snapshot(account_id: int, profile_handle: str, posts_count: Optional[int], followers_count: Optional[int], source_device_serial: str, source_package_name: str, capture_source: str = 'ui', db_path: Optional[str] = None) -> int`
  - `get_profile_snapshots(account_id: int, limit: int = 20, db_path: Optional[str] = None) -> List[Dict[str, Any]]`

- [ ] **Step 1: Add tests for snapshot insert, ordering by capture time, nullable metrics, account deletion cascade, and restart persistence.**
- [ ] **Step 2: Add an idempotent SQLite migration** for `profile_snapshots(id, account_id, profile_handle, posts_count, followers_count, source_device_serial, source_package_name, capture_source, captured_at)`; use `INTEGER NULL` for unparseable or unentered counts.
- [ ] **Step 3: Implement the two repository functions** with foreign-key validation, integer/non-negative metric validation, and UTC timestamps.
- [ ] **Step 4: Run the focused persistence tests and confirm they pass.**

### Task 5: Add preview, confirmation, and account onboarding APIs

**Files:**
- Modify: `app.py`
- Modify: `db.py`
- Test: `tests/test_full_suite.py`

**Interfaces:**
- Produces:
  - `POST /api/accounts/onboarding/launch` with `{device_serial, package_name}`.
  - `POST /api/accounts/onboarding/capture` with `{device_serial, package_name, account_id?}`.
  - `POST /api/accounts/onboarding/confirm` with `{device_serial, package_name, account_id?, profile_handle, posts_count?, followers_count?, category_id?, tag_ids?}`.
  - `GET /api/accounts/<int:acc_id>/profile-history`.

- [ ] **Step 1: Add route tests** for invalid/missing device, unconfirmed/missing package, capture failure, `needs_manual_profile_navigation`, duplicate username, duplicate device/package binding, existing-account handle mismatch, invalid counts, and successful confirmation.
- [ ] **Step 2: Implement launch/capture endpoints** using `open_profile_app` and `capture_profile`; never create an account as a side effect of launch or capture.
- [ ] **Step 3: Implement confirm endpoint**. Revalidate that the device is online and the package is installed/confirmed. For new accounts, create the account and first snapshot in one transaction. For existing accounts, require exact handle confirmation before adding a snapshot or changing its device/package binding.
- [ ] **Step 4: Return only safe status and profile fields**; do not include credentials, TOTP seeds, full UI hierarchy, or raw ADB output.
- [ ] **Step 5: Implement profile-history endpoint** with a bounded limit and account existence validation.
- [ ] **Step 6: Run focused API tests and confirm they pass.**

### Task 6: Add the guided onboarding UI to Accounts

**Files:**
- Modify: `templates/index.html`

**Interfaces:**
- Consumes: Task 1 device API, Task 2 launch API, Task 3 capture preview, Task 5 onboarding endpoints, and Module 1 category/tag controls.
- Produces: single or bulk onboarding flow: one device → scan/map clones → select one or more confirmed packages → sequential launch/capture per package → review/edit results → save selected rows in one explicit action.

- [ ] **Step 1: Add Manual add and Add from device actions** to Accounts. Manual add must not request credentials; include a single-package and multi-select bulk mode for device onboarding.
- [ ] **Step 2: Add an explicit device selector and clone scan/map action**. Allow selecting several confirmed, installed packages on the same online device; show the full serial and package IDs.
- [ ] **Step 3: Run selected packages sequentially** through launch and capture, with per-package progress, skip/retry/cancel controls, and clear instructions if manual profile navigation is needed. Never run two packages concurrently or request credentials.
- [ ] **Step 4: Add a bulk review table** showing profile handle, post/follower counts, device, package, capture status, category, and tags. Allow shared Category/Tag defaults, per-row overrides, edits, skips, and selection of rows to save.
- [ ] **Step 5: Require one explicit “Save selected accounts” action** after review. Detect duplicate handles/package bindings, explain conflicts, and never overwrite silently; preserve successful saves if a different row fails.
- [ ] **Step 6: Show latest snapshot and history** from the selected account; escape all device/package/profile text through the existing safe DOM patterns.
- [ ] **Step 7: Manually inspect** empty, offline, unauthorized, unconfirmed package, signed-out, sequential queue progress, capture-success, duplicate-account, and partial-save states in the browser.

### Task 7: Run end-to-end and regression verification

**Files:**
- Modify: `tests/test_full_suite.py` only if a missing regression test is identified.
- Modify: `PROJECT_MEMORY.md` after results are known.

- [ ] **Step 1: Run the full suite** with `python -m unittest tests/test_full_suite.py`; expected result: all tests pass.
- [ ] **Step 2: Run `git diff --check`**; expected result: no whitespace errors.
- [ ] **Step 3: Perform an authorized physical-device flow** on one device with multiple already-signed-in clones: scan/map packages → select several → verify apps open one by one → capture each own Profile → review/edit/category/tag assignment → save selected accounts → restart app → verify records and snapshot history persist.
- [ ] **Step 4: Verify wrong-account, unreadable-count, signed-out, and package-removed cases** stop safely and do not create incorrect account/profile rows.
- [ ] **Step 5: Regression-check Module 1 taxonomy, Excel import/export, protected secrets, clone inventory, login flow, and the existing Reel workflow/default package.**
- [ ] **Step 6: Record exact test output, device/Android/app version, limitations, and files changed in `PROJECT_MEMORY.md`; do not claim live device checks that were not run.**

## Non-Goals for Module 2

- Automatic login or credential/2FA entry; those remain in the existing separately controlled login flow.
- Scraping arbitrary public profiles or visiting profiles outside the user-selected, authorized clone apps.
- Repeated/scheduled profile refreshes or follower-growth analytics; snapshots are taken only during explicit onboarding or a user-requested refresh.
- Concurrent device scheduling, task queues, remote device farms, or unattended operation.
- Clone APK generation/repackaging or installing new apps; this module selects already-installed, operator-confirmed packages.
- Writing passwords, TOTP seeds, proxy credentials, raw UI hierarchy, or screenshots into profile history.

## Handoff

Implement only this module and stop after Task 7. Show the user the device selector, clone-to-account onboarding flow, captured profile preview, persistence/history, full test output, and the authorized-device verification result. Wait for review before Module 3: Content Library.



