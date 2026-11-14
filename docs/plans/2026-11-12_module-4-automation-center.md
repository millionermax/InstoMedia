# Module 4: Automation Center Implementation Plan

**Goal:** Turn the Automations tab into a clear catalog and launch point, with the existing Reel Upload with Music flow as the first runnable automation.

**Architecture:** Keep the existing `InstagramReelUploader.execute_workflow()` as the Reel automation engine. Add a small automation catalog and a configuration/run experience in the Automations tab that calls the existing backend workflow API, with an explicit selected account/device/clone target. Do not create a second Reel pipeline or arbitrary script runner.

**Tech stack:** Flask, existing `adb.py`/`InstagramReelUploader`, current Accounts and Content APIs, Jinja/HTML and vanilla JavaScript in `templates/index.html`, existing `unittest` suite, and current SSE/status polling.

**Scope:** One built-in automation (`reel_upload_with_music`), one selected onboarded account/device/clone per run, video from Content Library or existing direct upload/gallery paths, music URL, caption, privacy toggles, live run progress, cancel, and final Reel URL. Keep the existing Reel Studio tab working. Do not add profile visit, scheduling, batch runs, parallel devices, editable custom scripts, arbitrary code execution, or persistent run history in this module. The existing Tasks tab remains the future home for persistent queue/history.

## Current project facts

- `#automationsView` in `templates/index.html` is still a Coming Later placeholder.
- Reel Studio already configures audio URL, direct video upload, Content Library video, phone gallery source, caption/hashtags, and hide-like/share options.
- `POST /api/start` starts `run_workflow_async()` in one daemon thread; `GET /api/status` returns a process-global in-memory state; `POST /api/stop` signals cancellation; `/api/logs` streams SSE messages.
- `adb.InstagramReelUploader.execute_workflow()` already implements the 11-step music/Reel publishing workflow.
- Content Library integration already accepts `content_id` and rejects image assets for Reel publishing.
- The workflow API currently has a single global active-run guard. It accepts `package_name` but does not pass an explicit selected `device_serial` into `InstagramReelUploader`; a missing target can connect to the default ADB device. Account records already contain the device serial and clone package association needed to target a selected onboarded account.
- The Tasks tab is reserved for persistent queue inspection/history; Overview and Console already expose current workflow state and live logs.

## Proposed Automations tab layout

1. **Header and status:** “Automation Center”, short explanation, and active/idle status.
2. **Available Automations:** one ready card, **Reel Upload with Music**, with a short description of its 11-step workflow and a **Configure & Run** action. Do not show fake or disabled profile-visit/custom-script cards in this release.
3. **Run configuration:** select one saved account (show its username, device, and clone app), then choose a Content Library video, direct upload, or existing phone-gallery source. Configure audio URL, caption/hashtags, and hide-like/share options. Show a review summary before start.
4. **Active run:** show the existing 11-step pipeline, current step, SSE logs, Cancel action, and the returned Reel URL when available. Keep the active status visible if the user changes tabs.
5. **Reel Studio compatibility:** keep Reel Studio available as its existing manual/diagnostic workspace. Both entry points must call the same backend workflow and use the same active-run state; do not maintain two copies of the publishing form logic or two upload engines.

## Files expected to change

- `app.py`: catalog data/endpoint if needed, selected-account validation, explicit device serial passed to the worker, consistent active-run status/reset behavior.
- `adb.py`: accept and use an explicit device serial for the selected account without changing the 11-step upload sequence.
- `templates/index.html`: replace the Automations placeholder with the catalog, configuration/review UI, and live run state; preserve the Reel Studio tab.
- `tests/test_full_suite.py`: target validation, API workflow compatibility, catalog and Automations UI behavior.
- `PROJECT_MEMORY.md`: record actual implementation and verification after completion.

## Review focus

- An account with no device/package binding, an offline device, an unconfirmed/uninstalled package, or a package not belonging to the selected device must be rejected before any device action.
- Starting a second run while one is active returns a clear conflict and must not start a second worker.
- Invalid/non-video content IDs, missing media, and malformed audio URLs fail clearly without marking a run successful.
- Cancel, success, and error states must update the same run UI and not leave the start button permanently disabled.
- The selected device serial must reach `uiautomator2.connect(device_serial)`; never fall back silently to the first/default device for a targeted automation.
- Starting the automation from the Automations tab and the existing Reel Studio must both call the same executor and must not race or duplicate runs.

---

### Task 1: Define the built-in automation and target contract

**Files:**
- Modify: `app.py`
- Modify: `adb.py`
- Test: `tests/test_full_suite.py`

**Interfaces:**
- Built-in ID: `reel_upload_with_music`.
- `InstagramReelUploader(device_serial=None, package_name=PACKAGE_NAME, log_callback=None)` remains backward compatible; when a serial is supplied, connect only to that serial.
- `run_workflow_async(audio_url, video_path, caption, hide_likes, hide_shares, device_serial, package_name)` passes the explicit target to the uploader.
- `POST /api/start` accepts the existing media/audio/caption/privacy fields plus `automation_id` and `account_id`. For `automation_id='reel_upload_with_music'`, require a valid `account_id`; reject an unknown automation ID. Preserve legacy direct Reel Studio requests without these two new fields during the transition.

- [ ] Add tests for account ID parsing, missing device/package binding, offline device, unconfirmed/uninstalled clone, and valid account-to-device/package resolution.
- [ ] Add tests for a malformed/non-Instagram audio URL on the built-in automation; reject it before creating the worker while preserving the existing UI default Instagram audio URL.
- [ ] Add a test proving the valid resolved `device_serial` and `package_name` reach the uploader constructor.
- [ ] Add a test proving a second start while `workflow_status.state == 'running'` returns `409` and does not create another thread.
- [ ] Implement request validation using existing account hydration, explicit device validation, and confirmed-package validation before creating the worker thread.
- [ ] Extend the uploader worker with explicit serial support while preserving its current default for CLI/backward-compatible callers.
- [ ] Keep the existing `content_id` vs `video_filename` mutual exclusion and video-only Content Library validation.
- [ ] Verify the focused backend tests and existing Reel workflow tests pass.

### Task 2: Expose a small built-in automation catalog

**Files:**
- Modify: `app.py`
- Test: `tests/test_full_suite.py`

**Interfaces:**
- `GET /api/automations` returns the built-in list, initially one entry:
  `{id: 'reel_upload_with_music', name: 'Reel Upload with Music', status: 'ready', description, steps: 11}`.
- Catalog definitions are static application metadata in this release; no custom automation persistence schema is introduced.

- [ ] Add tests for catalog response shape, stable automation ID, ready state, and 11-step count.
- [ ] Implement the read-only catalog endpoint.
- [ ] Confirm there are no endpoints that accept or execute user-provided Python/JavaScript/shell scripts.

### Task 3: Build Automation Center and configuration/review flow

**Files:**
- Modify: `templates/index.html`
- Test: `tests/test_full_suite.py`

**UI contract:**
- Replace `#automationsView` placeholder with `#automationCatalog`, `#automationRunConfig`, `#automationReview`, and `#automationActiveRun` sections.
- Add stable selectors for account, media source, audio URL, caption, privacy toggles, review, start, cancel, step list, and result URL.

- [ ] Add UI contract tests for the catalog card, configuration controls, account selection, review, run progress, cancel, and result URL.
- [ ] Load the catalog and eligible accounts from existing APIs; show account username plus its bound device and clone package in the option/summary.
- [ ] On Configure & Run, open a configuration panel using the existing source choices: Content Library video, direct upload, or phone gallery. Do not offer library images.
- [ ] Provide audio URL, caption/hashtags, hide-like/share controls, and a review summary before start.
- [ ] Render a clear empty state when no eligible account or no Content Library video exists, with links to Accounts or Content tabs.
- [ ] Use safe DOM APIs for account names, filenames, descriptions, and API error messages.

### Task 4: Connect the Automation Center to the shared Reel worker

**Files:**
- Modify: `templates/index.html`
- Modify: `app.py` only for required status/error consistency
- Test: `tests/test_full_suite.py`

- [ ] Add frontend tests proving the run sends `automation_id`, selected `account_id`, exactly one media source, audio URL, caption, and privacy values.
- [ ] Refactor payload creation into one shared helper and run submission into one shared launch/status path. Automation Center and Reel Studio must call the same helpers so payload rules and single-run exclusion cannot drift.
- [ ] Connect the run action to `POST /api/start`; display API conflicts/errors without leaving the configuration panel stuck.
- [ ] Reuse `/api/status`, `/api/stop`, SSE logs, and the existing 11-step renderer for active-run progress and cancellation.
- [ ] Keep run state reflected in Overview and Console; changing tabs must not cancel or duplicate the current worker.
- [ ] On success show the existing Reel URL and copy/open actions; on cancel/error show a clear retry path.
- [ ] Keep Reel Studio’s current controls and entry point functional by routing both views through the same start function/state.

### Task 5: End-to-end verification and stop for review

**Files:**
- Modify: `tests/test_full_suite.py` if verification reveals a gap
- Modify: `PROJECT_MEMORY.md` after implementation is verified

- [ ] Run `python -m unittest tests/test_full_suite.py`.
- [ ] Run `git diff --check` and `node --check` on every extracted inline JavaScript block.
- [ ] Run browser E2E at desktop and mobile sizes in Light and Dark themes: catalog -> configure -> review -> start (mocked worker) -> progress -> cancel/error/success -> result URL.
- [ ] Verify account/device/package validation with valid, offline, unconfirmed, missing-binding, and wrong-device cases using mocks or an isolated test device.
- [ ] Verify existing Reel Studio direct upload, Content Library video, and phone-gallery requests still start the same backend workflow.
- [ ] Confirm a real single-device upload only if the user/device is available and explicitly ready; otherwise report it as untested. Do not imply that a mocked browser run proves a Reel posted.
- [ ] Update `PROJECT_MEMORY.md` with actual test and E2E results; stop for user review before planning profile visit, custom automation, scheduling, batch runs, or persistent task history.
