# Safety-First Project Structure Migration Plan

**Status:** Plan only; implementation has not started.  
**Goal:** Organize the existing Flask application into clear frontend, backend, and automation areas without losing user data or changing product behavior.

## Guardrails

- Treat the current application behavior as the compatibility baseline. This is a structure migration, not a feature rewrite.
- Do not delete, regenerate, replace, or relocate `data/instoper.db`, uploaded media, recordings, or other user-generated files in the first migration.
- Do not run `git reset`, `git clean`, checkout-overwrite commands, or automatic stash/pop operations.
- Preserve existing uncommitted edits. The current workspace has modifications in `adb.py`, `human_behavior.py`, `tests/test_full_suite.py`, and `PROJECT_MEMORY.md`; record their diffs before work and do not overwrite them.
- Make one bounded migration at a time. After each migration, verify imports, startup, relevant behavior, and paths before proceeding.
- Keep `app.py` as a backwards-compatible launcher during the migration.
- Do not create placeholder modules for speculative future features. Extract a module only when its responsibility and callers are confirmed in source.
- Do not change automation behavior, timing, UI selectors, database schema, or account data as part of file moves.

## Proposed Target Structure

```text
Platfrom-tool/
├── app.py
├── requirements.txt
├── start_gui.bat
├── .gitignore
├── backend/
│   ├── __init__.py
│   ├── config.py
│   ├── routes/
│   │   ├── __init__.py
│   │   ├── dashboard.py
│   │   ├── devices.py
│   │   ├── accounts.py
│   │   ├── content.py
│   │   ├── automations.py
│   │   ├── campaigns.py
│   │   ├── tasks.py
│   │   └── imports_exports.py
│   ├── services/
│   │   ├── accounts.py
│   │   ├── campaigns.py
│   │   ├── tasks.py
│   │   └── excel_manager.py
│   └── storage/
│       ├── __init__.py
│       ├── db.py
│       └── vault.py
├── automation/
│   ├── __init__.py
│   ├── instagram/
│   │   ├── __init__.py
│   │   ├── reel_workflow.py
│   │   └── login_flow.py
│   ├── android/
│   │   ├── __init__.py
│   │   ├── package_scanner.py
│   │   └── profile_capture.py
│   ├── interaction/
│   │   └── human_behavior.py
│   └── observability/
│       └── screen_recorder.py
├── frontend/
│   ├── templates/
│   │   └── index.html
│   └── static/
│       ├── css/
│       ├── js/
│       └── assets/
├── tests/
│   └── test_full_suite.py
├── data/                         # Runtime data; preserve current paths initially
├── uploads/                      # Runtime uploads; preserve current path initially
└── docs/
    ├── README.md
    ├── PRD.md
    ├── PROJECT_MEMORY.md
    ├── GEMINI.md
    ├── ANTIGRAVITY_REVIEW_NOTES.md
    └── plans/
```

### Existing File Destination Map

| Current file | Proposed destination | Notes |
|---|---|---|
| `app.py` | `app.py` | Keep as a thin launcher and compatibility entry point. |
| `adb.py` | `automation/instagram/reel_workflow.py` | Move the current Reel workflow without changing its behavior. Update imports and CLI entry handling carefully. |
| `human_behavior.py` | `automation/interaction/human_behavior.py` | Keep as reusable device interaction helpers. |
| `login_flow.py` | `automation/instagram/login_flow.py` | Login workflow module. |
| `package_scanner.py` | `automation/android/package_scanner.py` | Package discovery and clone inventory. |
| `profile_capture.py` | `automation/android/profile_capture.py` | Profile capture and app cleanup. |
| `screen_recorder.py` | `automation/observability/screen_recorder.py` | Task recording lifecycle. |
| `db.py` | `backend/storage/db.py` | SQLite access and schema functions. |
| `vault.py` | `backend/storage/vault.py` | Secret protection and encrypted backup helpers. |
| `excel_manager.py` | `backend/services/excel_manager.py` | Excel import/export logic. |
| Inline Flask routes in `app.py` | `backend/routes/*.py` | Split only after route inventory; preserve URLs, methods, payloads, and response shapes. |
| Inline business logic in `app.py` | `backend/services/*.py` | Extract only cohesive logic that currently exists; do not duplicate it. |
| `templates/index.html` | `frontend/templates/index.html` | Configure Flask with the new template directory. |
| `static/**` | `frontend/static/**` | Move existing assets only after inventory; preserve public asset URLs. |
| `tests/test_full_suite.py` | `tests/test_full_suite.py` | Keep first; splitting tests is out of scope for the structure migration. |
| `requirements.txt`, `start_gui.bat`, `.gitignore` | Repository root | Update startup/import paths only if needed. |
| `README.md`, `PRD.md`, `PROJECT_MEMORY.md`, `GEMINI.md`, `ANTIGRAVITY_REVIEW_NOTES.md` | `docs/` | Documentation move is a separate, low-priority phase; update all references before moving. |
| `plan/*.md` | `docs/plans/*.md` | Optional final documentation cleanup, not required for app architecture. |
| `data/**`, `uploads/**` | Stay at current paths | Do not move in this plan; database and media paths are runtime compatibility boundaries. |

## Execution Phases

### Phase 0 — Baseline and Safety Inventory

1. Record the current branch, `git status`, and diffs for all already-modified files.
2. Inventory source files, routes, route methods/paths, imports, app startup command, Flask template/static configuration, and every runtime path for DB/media/recordings.
3. Map each current route and helper to its proposed destination. Confirm which service functions actually exist in `app.py` before creating service modules.
4. Record baseline verification commands and expected results. Do not change files in this phase.

**Gate:** Stop if the dirty changes cannot be identified or preserved, or if runtime paths are unclear. Resolve the workspace ownership/baseline before moving files.

### Phase 1 — Establish Package Boundaries

1. Add only required package initializers/configuration and the Flask app factory scaffold.
2. Keep the current `app.py` invocation working as a launcher.
3. Verify app import/startup and existing routes before moving domain modules.

**Gate:** Existing startup and URL behavior must remain available before continuing.

### Phase 2 — Move Persistence and Security Modules

1. Move `db.py` to `backend/storage/db.py` and `vault.py` to `backend/storage/vault.py` in one small change.
2. Update imports in the app and dependent modules. Do not change schema, DB filename, encryption behavior, or stored records.
3. Verify database initialization, existing DB access, and vault-dependent code.

**Gate:** The existing `data/instoper.db` must still be the database opened by the application.

### Phase 3 — Move Automation Modules

Move one module group at a time, updating imports after each group:

1. `human_behavior.py` → `automation/interaction/`.
2. `adb.py` → `automation/instagram/reel_workflow.py`.
3. `login_flow.py` → `automation/instagram/`.
4. `package_scanner.py` and `profile_capture.py` → `automation/android/`.
5. `screen_recorder.py` → `automation/observability/`.

Preserve CLI behavior for `adb.py`, device selection, cancellation, callbacks, and recording artifact paths.

**Gate:** Each moved module imports successfully and its existing callers resolve before the next move.

### Phase 4 — Split the Flask Route Layer

1. Use the Phase 0 route inventory to move route handlers by domain into Flask Blueprints.
2. Preserve every existing HTTP path, method, request field, response field, and status code.
3. Keep `app.py` limited to launching the configured Flask app.
4. Extract a service module only when a cohesive block of business logic has been identified; avoid changing behavior while extracting.

**Gate:** Compare the before/after route inventory and verify all existing UI/API calls still resolve.

### Phase 5 — Move Frontend Assets

1. Move `templates/index.html` to `frontend/templates/index.html` and configure Flask's `template_folder`.
2. Inventory `static/` first; move only existing assets to `frontend/static/` and configure `static_folder`/URL paths.
3. Check browser-loaded CSS, JS, icons, uploads, and streaming endpoints.

**Gate:** The home page and its static assets load using the same local app URL as before.

### Phase 6 — Documentation Cleanup (Optional)

1. Move project docs to `docs/` only after searching all references in scripts, agent instructions, and links.
2. Keep `requirements.txt`, `.gitignore`, and `start_gui.bat` in the root.
3. Do not move product data or media as part of documentation cleanup.

**Gate:** All documented setup commands and references point to real files.

## Verification Plan

After each phase, run only the checks relevant to that phase and record the command/output:

- Python import/compile check for moved modules.
- Existing test suite, with the baseline comparison recorded before migration.
- Flask startup and route inventory comparison.
- Database path check confirming existing records remain readable.
- Frontend smoke check for page, assets, API calls, and live stream endpoints.
- `git diff --check`, changed-file review, and a final scan for stale old import paths.

No code is considered migrated merely because files exist at the new paths. Every phase must pass its gate before the next phase starts.

## Rollback Plan

- Keep migrations grouped so each phase can be reverted independently.
- Record every moved-file mapping and import change.
- If verification fails, revert only the current phase's code changes; do not delete or overwrite runtime data.
- Preserve any user edits that existed before the migration. Never restore a file from the migration baseline over later user changes without explicit coordination.
- Do not commit or publish automatically; leave the changes reviewable for the project owner.

## Completion Criteria

- The project has clear `frontend/`, `backend/`, and `automation/` responsibilities.
- `app.py` remains the supported startup entry point.
- Existing routes and user-facing behavior are unchanged.
- Existing database, uploads, and recording files remain accessible at their original paths.
- Imports resolve, the current verification suite passes, and no stale paths/imports remain.
- A concise migration report lists moved files, verification evidence, and any deferred items.

## Explicitly Out of Scope

- New automation, self-healing, OCR, or retry behavior.
- Database schema changes or data migration.
- Frontend redesign or JavaScript rewrite.
- New task concurrency behavior.
- Splitting tests solely to make the tree look cleaner.
- Deleting old runtime files or resetting the working tree.
