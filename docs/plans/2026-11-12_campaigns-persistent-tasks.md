# Campaigns and Persistent Tasks Implementation Plan

> **For the implementation agent:** Follow the tasks in order. Treat each checkbox as a reviewable unit. Preserve other in-progress changes. Stop after verification and report results for user review; do not add AI custom-script generation or new automation types in this module.

**Goal:** Rebuild Reel Studio as Campaigns, allowing a named Reel Upload with Music campaign to save multiple selected account targets with individual captions/videos and run them sequentially while preserving campaign and task data.

**Architecture:** A Campaign is a saved configuration for one automation type. Its ordered account targets each hold a caption and Content Library video ID. Launching it creates a persistent Campaign Run and one persistent Task per target. A single worker processes tasks in order using the existing Reel uploader, then stops and verifies the exact clone package and waits before opening the next target.

**Tech stack:** Existing Flask/SQLite application, `db.py`, `app.py`, `adb.py` / `InstagramReelUploader`, existing `package_scanner.py` and `profile_capture.py` validation/stop services, Content Library APIs, Jinja/HTML and vanilla JavaScript in `templates/index.html`, and the existing unittest suite.

## Current project facts

- Automations currently includes the built-in `reel_upload_with_music` workflow.
- The current Reel Studio uses `POST /api/start` for one selected account per run. It has one process-global active workflow guard and in-memory active status.
- The Reel worker accepts explicit `device_serial` and `package_name`; account records have device/package bindings.
- `profile_capture.close_profile_app(device_serial, package_name)` validates the confirmed installed package, stops that exact app, then verifies it is no longer running.
- The Content Library stores video/image items in SQLite and supports video-only Reel selection plus Category/Tag filtering.
- There are no campaign, campaign target, campaign run, or persistent task/event tables yet. The current Tasks tab is a placeholder.

## Product behavior and scope

- Rename the existing **Reel Studio** navigation/view to **Campaigns**. Keep the **Automations** tab as the reusable automation catalog; keep the **Tasks** tab as saved run/task history.
- This release has one campaign form type: **Reel Upload with Music**. Future automation types can have their own campaign form later.
- A campaign has a name, Instagram music URL, global hide-like/share settings, a configurable app-close cooldown (default 3 seconds), and an ordered list of targets.
- Each target is one saved Instagram account with its bound device and clone package, its own caption, and its own video Content Library item.
- Campaign target selection supports account search and the existing account Category/Tag filters. The Reel picker supports Content Library search plus media type, Category, and Tag filters and returns videos only.
- Direct campaign upload creates a Content Library item and selects it for that target, so a saved campaign never depends on an unmanaged temporary file.
- Save Draft persists the campaign. Launch preflights all targets/settings and creates a run with one Task per selected account.
- Tasks show the campaign run and each account's status, progress, event log, Reel URL/error, app cleanup result, and sequence number. Both campaign definitions and run/task history must still appear after browser reload and app restart.
- Run all selected accounts strictly sequentially: launch/verify target package -> run Reel pipeline -> record success/failure -> stop exact package -> verify stopped -> wait configured cooldown -> launch next target.
- A package close failure pauses the run and blocks all later targets. Default policy is pause on task failure; user may explicitly choose continue after a failure only if cleanup succeeded.
- Cancellation stops the active task, cleans up the active package, and leaves later targets unstarted. No automatic retry after an ambiguous post/share result; retry requires a user action.
- Never run two campaign targets concurrently. The campaign worker must share an exclusive lock with existing `/api/start` so direct Reel Studio starts cannot overlap it.
- Out of scope: AI/custom script generation, Profile Visit automation, schedules, parallel device execution, broad generic queue features, image/carousel publishing, and arbitrary user code execution.

## Data model to add

1. **`campaigns`**: `id`, `name`, `automation_id`, `audio_url`, `hide_likes`, `hide_shares`, `cooldown_seconds`, `failure_policy`, `created_at`, `updated_at`.
2. **`campaign_targets`**: `id`, `campaign_id`, `account_id`, `position`, `content_id`, `caption`, `created_at`, `updated_at`; unique `(campaign_id, account_id)` and foreign keys to accounts/content items.
3. **`campaign_runs`**: `id`, `campaign_id`, `status`, `current_task_id`, `cancel_requested`, `started_at`, `completed_at`, `created_at`, `updated_at`, optional summary/error.
4. **`tasks`**: `id`, `run_id`, `campaign_target_id`, `sequence_number`, `account_id`, `device_serial`, `package_name`, `status`, `current_step`, `reel_url`, `error_message`, `cleanup_status`, `started_at`, `completed_at`, `created_at`, `updated_at`.
5. **`task_events`**: `id`, `task_id`, `timestamp`, `level`, `step`, `message`; index `(task_id, id)` for ordered task logs.

Use foreign keys and indexes for campaign target ordering, task status/run queries, and task event lookup. Restrict deletion of an account or content item referenced by a saved campaign and return a clear conflict response. Do not store file paths supplied by the browser; store Content Library IDs and resolve storage paths on the server.

## API contract

- `GET /api/campaigns`: paginated campaign list with target count and latest run summary.
- `POST /api/campaigns`: create campaign and ordered targets transactionally.
- `GET /api/campaigns/<campaign_id>`: campaign settings and ordered target rows.
- `PATCH /api/campaigns/<campaign_id>`: update settings/targets transactionally; reject edits while its run is active.
- `DELETE /api/campaigns/<campaign_id>`: delete only when no run is active; preserve prior run/task history or reject deletion while history exists (do not cascade away user-visible task history).
- `POST /api/campaigns/<campaign_id>/preflight`: validate campaign fields, each account binding, explicit device online state, confirmed installed clone package, and each Content Library video; return per-target blockers without launching an app.
- `POST /api/campaigns/<campaign_id>/runs`: repeat preflight, create run and ordered tasks in a transaction, acquire exclusive runner lock, then start one background worker; return `run_id`.
- `GET /api/campaign-runs`: paginated persistent run list, optionally filter by campaign/status.
- `GET /api/campaign-runs/<run_id>`: run summary plus ordered tasks.
- `POST /api/campaign-runs/<run_id>/cancel`: set persistent cancel request and signal the active worker.
- `POST /api/campaign-runs/<run_id>/continue`: resume pending tasks after a failed task only if cleanup succeeded and the user explicitly requests it.
- `GET /api/tasks`: paginated task list with campaign/account/status filters.
- `GET /api/tasks/<task_id>`: task details and ordered persisted events.
- `POST /api/tasks/<task_id>/retry`: create a new run/task for an explicitly selected failed target; never reset a completed/ambiguous task invisibly.
- Preserve existing single-target `/api/start`, `/api/status`, `/api/stop`, and `/api/automations` behavior. Enforce one global device-workflow execution at a time across campaign and legacy Reel Studio entry points.

## Files expected to change

- Modify: `db.py` — schema migrations, campaign/run/task/event repository functions and in-use deletion protections.
- Modify: `app.py` — campaign/task APIs, preflight, shared runner lock, persistent worker orchestration and task event persistence.
- Modify: `adb.py` — reuse the existing Reel workflow per target; no copied/forked 11-step pipeline.
- Modify: `templates/index.html` — Campaigns form/list/review; Tasks run history/detail; navigation rename; content/account filters.
- Test: `tests/test_full_suite.py` — repository/API/worker/UI regression coverage.
- Update after verified completion: `PROJECT_MEMORY.md`.

---

### Task 1: Persist Campaign definitions and ordered targets

**Files:** `db.py`, `tests/test_full_suite.py`

- [ ] Add idempotent schema migrations for `campaigns` and `campaign_targets`, foreign keys, uniqueness, and lookup indexes.
- [ ] Add repository functions for create/get/list/update/delete campaigns with ordered targets. Define exact return values and validate campaign name, supported automation ID (`reel_upload_with_music`), Instagram audio URL, cooldown bounds (1–30 seconds; default 3), failure policy (`pause` or `continue`), account IDs, and video-only Content IDs.
- [ ] Validate all targets before commit; invalid target N must not partially change campaign or reorder valid targets.
- [ ] Add `test_campaign_crud_and_target_order_persist_after_init_db`, `test_campaign_target_video_only_and_content_scope_validation`, and `test_campaign_references_block_account_or_content_delete`.
- [ ] Run focused DB tests and migration/restart tests before continuing.

### Task 2: Persist Campaign Runs, Tasks, and task events

**Files:** `db.py`, `tests/test_full_suite.py`

- [ ] Add idempotent schema migrations for `campaign_runs`, `tasks`, and `task_events` with indexes by run/status/sequence/task-event order.
- [ ] Add transactional helpers to create a run and all ordered tasks, transition run/task states, update current progress/result/cleanup state, append events, list paginated history, and fetch task details/events.
- [ ] Allowed task states: `pending`, `running`, `closing_app`, `cooldown`, `completed`, `failed`, `interrupted`, `cancelled`. Allowed run states: `queued`, `running`, `paused`, `completed`, `failed`, `cancelled`, `interrupted`.
- [ ] Ensure `init_db()` recovery marks a task that was `running`, `closing_app`, or `cooldown` when the process stopped as `interrupted`; never auto-run it again because the Reel may already have posted.
- [ ] Add tests for task/event persistence after DB reconnect, ordered events, legal state transitions, pagination, run summary hydration, and interrupted recovery.
- [ ] Run focused repository tests before continuing.

### Task 3: Add campaign CRUD, preflight, and history APIs

**Files:** `app.py`, `db.py`, `tests/test_full_suite.py`

- [ ] Implement the API contract above with validation and stable HTTP status codes (`400` invalid input, `404` missing IDs, `409` in-use/active conflicts).
- [ ] Preflight every selected target against explicit `device_serial`, bound `package_name`, online device status, confirmed clone inventory, live installed package, and existing video asset; include the exact blocker per row.
- [ ] Add tests for CRUD/list/detail, page reload persistence via a new app/test-client instance, invalid/offline/unconfirmed targets, wrong-device package, missing file, image media ID, campaign edits while running, and delete protections.
- [ ] Ensure API responses contain no passwords, TOTP seeds, provider secrets, or filesystem absolute paths.
- [ ] Run focused API tests and existing Automation `/api/start` tests.

### Task 4: Implement the sequential Campaign worker with verified cleanup

**Files:** `app.py`, `adb.py`, reuse `profile_capture.py` close service, `tests/test_full_suite.py`

- [ ] Add one shared exclusive execution lock for existing `/api/start` and Campaign Run workers; return `409` without starting a second worker when busy.
- [ ] Add a worker that loads the persisted run/tasks and processes exactly one task at a time using each account's serial, package, Content Library video, caption, campaign audio URL, and privacy settings.
- [ ] Persist `running` and per-step `task_events` as the existing uploader log callback fires; expose task current step in stored task detail.
- [ ] On success or failure, transition to `closing_app`, call the existing validated `profile_capture.close_profile_app(serial, package)`, persist close verification, wait `cooldown_seconds`, then start the next pending task only if cleanup succeeded and policy allows continuing.
- [ ] If cleanup fails, set run `paused`, record the package/device/error, and do not launch later tasks. Do not mark cleanup as successful from the stop request alone; require verified stopped state.
- [ ] On cancellation, signal the active uploader, perform exact-package cleanup, mark active/pending tasks accurately, and do not continue automatically.
- [ ] On failure after Share or other ambiguous completion, mark task `failed` or `interrupted` with `retry_requires_review=true`; do not auto-retry.
- [ ] Add mocked worker-order tests asserting `target A finish -> A app_stop -> A stop verified -> cooldown -> target B launch`; test stop failure blocks B, failure policy, cancel, and exclusion with direct Reel Studio starts.
- [ ] Verify the existing direct Reel Studio workflow still uses the same `InstagramReelUploader` and status compatibility.

### Task 5: Rebuild Reel Studio as Campaigns

**Files:** `templates/index.html`, `tests/test_full_suite.py`

- [ ] Rename the visible nav label to **Campaigns** and update visible headings/breadcrumbs; retain element IDs until code references are safely migrated.
- [ ] Build a saved campaign list with Create, Edit, Duplicate, Delete, and Launch actions; show campaign name, automation type, target count, and latest run summary.
- [ ] Build the Reel Upload with Music campaign form: name, audio URL, cooldown, failure policy, global privacy settings, searchable account targets, and an ordered target table.
- [ ] Each target row must have independent enabled state, caption, and selected video. Add account Category/Tag filters and a “copy caption/video to selected rows” action that requires explicit click.
- [ ] Reuse the Content Library picker with search, Category, Tag, and video-only filter. Campaign direct upload must use `/api/content` and auto-select the created video ID.
- [ ] Add preflight/review UI listing each target and blockers; disable Launch while required blockers remain.
- [ ] Persist drafts through campaign APIs; reopening the tab or reloading the page must restore campaign settings and ordered targets.
- [ ] Add UI/API tests for editing and reload persistence, multiple selected targets, independent captions/media, copy-to-selected, content filters, direct upload, preflight blocker display, and campaign delete conflicts.

### Task 6: Show persistent Campaign Run and Tasks data

**Files:** `templates/index.html`, `tests/test_full_suite.py`

- [ ] Replace the Tasks placeholder with paginated saved Campaign Runs and per-account tasks; add filters for campaign, account, and state.
- [ ] Show sequence number, username, device/package, task state, current Reel step, last update, cleanup result, Reel URL/error, and retry/continue eligibility.
- [ ] Add a run-detail view with persisted ordered task events/logs and links back to its Campaign.
- [ ] Poll or refresh task data from GET APIs; browser reload must reattach to existing run history and never create or restart a run.
- [ ] Add tests for task list/detail/history after page reload, log ordering, and retry action creating an explicit new run/task rather than altering old history.

### Task 7: End-to-end verification and stop for review

**Files:** `tests/test_full_suite.py`, `PROJECT_MEMORY.md` after verified completion

- [ ] Run `python -m unittest tests/test_full_suite.py`, `git diff --check`, and `node --check` on all extracted inline JavaScript blocks.
- [ ] Run browser E2E with at least three mocked onboarded accounts and distinct Content Library videos/captions. Verify campaign draft persists after reload and run/task history persists after another reload.
- [ ] Verify event ordering per target: launch -> 11-step Reel result -> exact package close -> close verified -> configured cooldown -> next launch. Confirm there is never overlap between target workers.
- [ ] Verify offline device, missing clone, invalid video, stop failure, pause/continue, cancellation, ambiguous post result, and process restart/interrupted states.
- [ ] Verify UI at desktop and mobile sizes in Light and Dark themes, including account filters and Content Category/Tag picker filters.
- [ ] If a physical phone and safe test accounts are available, run a small sequential campaign and verify every app is closed before the next starts. Otherwise report physical-device verification as not run.
- [ ] Record actual verification in `PROJECT_MEMORY.md`; stop and show the user the campaign and saved Tasks screens/results. Do not start another module.
