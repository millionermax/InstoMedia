complexity: complex

### Step 1/8 [analysis]
- **Focus**: Define the Tasks tab problem and measurable outcomes.
- **Findings**: Current page shows run cards and a dense persistent task table. Long error messages dominate rows and make run status, account, and result hard to scan. User wants an Apify-inspired run workflow, task-level result/log inspection, export, and failure screenshots.
- **Confidence**: high
- **Next**: Map the existing data and API boundaries.

### Step 2/8 [analysis]
- **Focus**: Identify existing persistence and endpoint contracts.
- **Findings**: `db.py` already persists `campaign_runs`, ordered `tasks`, and timestamped `task_events`. `app.py` exposes campaign run listing/detail and task history APIs. The frontend in `templates/index.html` already renders run cards and task history, so the rebuild can evolve those contracts instead of introducing a second task system.
- **Confidence**: high
- **Next**: Identify missing diagnostics and screenshot lifecycle requirements.

### Step 3/8 [decomposition]
- **Focus**: Break the work into independent product areas.
- **Findings**: Work divides into (1) run list and filters, (2) run detail and account task rows, (3) structured task result/logs, (4) failure screenshot capture/storage/serving, (5) run/task exports, and (6) compatibility, accessibility, and verification. Screenshot capture crosses the Android worker, filesystem/storage, database, API, and UI.
- **Confidence**: high
- **Next**: Compare UI and persistence approaches.

### Step 4/8 [comparison]
- **Focus**: Compare implementation approaches and tradeoffs.
- **Findings**: A single-page expandable run/task view is the smallest change and keeps navigation simple. A separate run-detail route gives more room for logs and screenshots but adds navigation/state work. Replacing all task persistence is unnecessary because the current model already records runs, tasks, and events.
- **Confidence**: high
- **Next**: Choose the least disruptive architecture and define data changes.

### Step 5/8 [evaluation]
- **Focus**: Select a UI and data evolution strategy.
- **Findings**: Extend the existing Tasks workspace with a run-first list and a dedicated detail panel or view; keep existing run/task IDs and history APIs compatible. Add only the minimum schema/API fields needed for structured event context and failure screenshot metadata. Preserve old events and display them as legacy messages when structured fields are absent.
- **Confidence**: high
- **Next**: Define screenshot and export behavior precisely.

### Step 6/8 [analysis]
- **Focus**: Define failure screenshot capture and export semantics.
- **Findings**: Capture one screenshot when a task transitions to failed, associate it with that task and run, record capture timestamp and capture error if any, and serve it through an authorized local API path. Avoid storing screenshots in task log text or embedding image blobs in normal run responses. Export CSV for tabular summaries and JSON for full structured events; screenshots should be downloadable separately and included in an export manifest by filename/reference.
- **Confidence**: medium
- **Next**: Resolve uncertainty with implementation-time storage audit and set verification gates.

### Step 7/8 [revision]
- **Focus**: Resolve screenshot storage uncertainty and bound scope.
- **Findings**: The repository has local application storage conventions, but the correct retention path and cleanup policy need confirmation during implementation by inspecting current storage helpers. Agent should reuse established storage roots; if no suitable policy exists, keep screenshots under a dedicated task-artifact directory with task-ID filenames and document a conservative retention rule before coding. Do not add cloud storage or a general observability platform.
- **Confidence**: medium
- **Next**: Synthesize implementation gates and risks.

### Step 8/8 [synthesis]
- **Focus**: Synthesize a reviewable, incremental agent plan.
- **Findings**: Implement in layers: data/storage/API compatibility, run-first UI, task details/logs, failure screenshots, export, then verification. Keep each layer independently reviewable and avoid unrelated campaign workflow changes.
- **Confidence**: high
- **Next**: → Gate

## Plan: Rebuild Tasks Tab as a Run-Centered Workspace

### Problem Definition
- **Core problem**: The current Tasks tab is visually dense and difficult to debug. Run summaries, per-account tasks, long errors, and actions compete in one page; failure clues are not easy to inspect or export.
- **Constraints**: Preserve existing campaign/run/task/event history and APIs where possible. Follow the current black/white light/dark design system and existing components. Treat Apify screenshots as interaction and information-hierarchy references, not a pixel-copy requirement. Do not change campaign execution behavior except where needed to capture failure artifacts and structured diagnostics.
- **Success criteria**:
  - Runs are quickly scannable and filterable by campaign, status, account, and time range.
  - Opening a run shows ordered account-task rows with concise status and result summaries.
  - Opening a task shows ordered timestamped logs, stage context, error detail, cleanup result, Reel URL, and any failure screenshot.
  - Failed tasks attempt a screenshot capture and expose either the artifact or an explicit capture failure.
  - Run and task data can be exported as CSV/JSON without losing event order or context.
  - Existing history remains visible after schema changes; loading, empty, and error states are clear.

### Analysis Summary
- Steps 1–2: Current UI has scanability problems, while persistent runs, tasks, ordered events, and API routes already exist.
- Steps 3–5: Extend existing data/API and build a run-first UI; avoid replacing the task subsystem.
- Steps 6–7: Failure screenshots need a dedicated artifact lifecycle and conservative local retention; inspect storage conventions before choosing the path.

### Execution Plan
1. **Audit current UI, API, storage, and schema contracts.** Trace `campaign_runs`, `tasks`, `task_events`, current run/task endpoints, pagination/filter semantics, theme tokens, and file storage helpers. Record existing response shapes and legacy data requirements. → Verify: a short implementation map lists exact files, API contracts, and any schema migration needed before edits.
2. **Define the run-first information architecture and states.** Design a compact run list with status, campaign, started time/duration, completed/failed/queued counts, and clear actions. Add run search/filters and a selected-run detail view containing ordered account task rows. Keep errors collapsed to a one-line summary. → Verify: a UI state matrix covers queued/running/paused/completed/failed/cancelled plus empty/loading/API-error states, in light and dark themes.
3. **Add minimal structured event context and compatibility.** Extend event persistence only for fields needed to debug stage failures (for example event type/stage, device serial, package, and structured diagnostic JSON); keep human-readable message and timestamp. Add an idempotent migration and fallback handling for existing rows. → Verify: old event rows load unchanged, new events preserve ordering/context, and migration can run repeatedly without data loss.
4. **Add failure screenshot capture and task artifact lifecycle.** At the task failure boundary, capture the connected device screen before app cleanup when possible; persist artifact path, timestamp, and capture outcome against the task. Use the existing local storage convention, prevent path traversal, expose a task-scoped API, and define retention/cleanup behavior. → Verify: a simulated failure stores and serves the correct image for only that task; unavailable device/storage records a capture error without masking the original task failure.
5. **Build task detail with logs and diagnostics.** Add a task result panel/drawer that loads full events on demand, groups or labels them by workflow step, highlights errors, shows target/device/package, cleanup status, output URL, and screenshot. Long logs should wrap and support copy/download without expanding the table row. → Verify: a known failed task shows the exact failure stage and relevant diagnostics; successful tasks show output URL and event timeline; legacy unstructured messages remain readable.
6. **Implement run and task export.** Provide run-level CSV summary and JSON full-detail exports, plus task-level event export. Preserve timestamps, task order, stage, severity, account/package/device, error context, cleanup state, and artifact references. Do not embed screenshot bytes in CSV/JSON. → Verify: exported files parse correctly and contain all selected run/task records in deterministic order, including failed tasks and legacy event rows.
7. **Rebuild the Tasks tab UI and wire it to existing APIs.** Implement run list, filters, pagination, selected-run task rows, detail panel, export actions, and responsive layouts using current design tokens. Keep state updates/polling for active runs and do not route a run title back to the campaign editor unexpectedly. → Verify: user can move from run list to a task’s logs/screenshot/export and back without losing current filters or run selection.
8. **Run focused regression and end-to-end verification.** Cover migration/data compatibility, API pagination/filtering, screenshot success/failure, export contents, run/task state rendering, and responsive/light-dark UI. Use existing test conventions; then manually inspect one historical failed run and one successful run. → Verify: all relevant checks pass; screenshots/logs from the two records match stored run/task states; no existing campaign history is lost.

### Risks
- **Screenshot capture can fail or slow cleanup** → Capture once at the failure boundary with a bounded timeout; preserve original exception and record capture failure separately.
- **Screenshot artifacts can consume disk** → Use a dedicated task artifact directory, document retention, and provide safe cleanup that never deletes active-run artifacts.
- **Schema evolution can break old history** → Add an idempotent migration, nullable/defaulted fields, and legacy rendering tests.
- **Large log payloads can slow the run list** → Load detailed events and screenshots only when a task is opened; paginate or cap event responses if needed.
- **Exports can expose too much or lose context** → Scope exports to selected run/task and include only operational metadata; exclude credentials and raw UI hierarchy.
- **UI refactor can disrupt existing controls** → Preserve relevant continue/cancel/retry actions and verify state-specific visibility.

### Open Questions
- [ ] Confirm existing storage root and preferred screenshot retention duration during implementation; reuse current convention if one is already defined.
- [ ] Confirm whether CSV and JSON are sufficient, or whether Excel export is also required. Default plan is CSV + JSON.
- [ ] Confirm whether screenshots should be captured for every failure or only failures with a connected device. Default is attempt when a device is available.
