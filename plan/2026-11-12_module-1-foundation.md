# Module 1: Instoper Web Foundation Implementation Plan

> **For agentic workers:** Implement this plan in order. Treat every checkbox as a separate reviewable task. Do not begin Module 2 until this module's verification passes and the user reviews the result.

**Goal:** Establish the browser app shell and durable, scoped category/tag foundation needed by Accounts and Content while preserving current Reel and account workflows.

**Architecture:** Keep the existing local Flask + SQLite + Jinja/HTML/Tailwind architecture. Add a lightweight navigation shell in the current frontend, use SQLite migrations for normalized categories/tags, and keep current feature behavior in place. New sections without a module implementation should show a clear “Coming later” state and must not invent metrics or actions.

**Tech Stack:** Python 3, Flask, SQLite, Jinja/HTML, Tailwind CSS CDN, existing inline JavaScript, `unittest`.

**Spec:** `PRD.md` plus the agreed product module sequence in the current conversation.

## Global Constraints

- Keep this release browser-based and local; do not package a Windows desktop executable.
- Preserve the existing Reel Studio behavior and official Instagram package fallback.
- Keep existing account, clone scan, login, Excel import/export, and secret-vault behavior working.
- Account passwords, TOTP seeds, and proxy credentials stay in the protected vault; never add them to category/tag records or ordinary API responses.
- A category is one primary grouping per item; tags are zero or more labels. Category/tag lists are scoped separately for Accounts and Content.
- Normalize category/tag uniqueness using `name.strip().casefold()`; keep the first entered spelling for display.
- In Excel, serialize multiple tags in a `tags` column separated by semicolons; reject semicolons in individual tag names so import/export stays unambiguous.
- Keep categories/tags and all user-entered values persistent in the existing SQLite database at `data/instoper.db`.
- Do not add a JavaScript framework, database server, task scheduler, multi-device orchestration, or custom automation engine in this module.
- Do not run profile visits or profile scraping as part of the foundation module.

## Review Focus

- Existing account rows with a legacy single `tag` value must survive the schema migration and appear as one account tag.
- Category/tag names that differ only by whitespace or letter case must not create duplicate values within the same scope.
- Account and Content taxonomies must not leak into each other's pickers or filters.
- Deleting a category or tag already assigned to an item must fail clearly without silently losing assignments.
- Spreadsheet and UI values containing HTML characters, commas, leading/trailing spaces, or formula-like text must remain safe and preserve current import/export behavior.

---

## Scope and Acceptance Criteria

This module creates the navigation/data foundation only. The Accounts section remains functional and gains account category/tag assignment. Content, Automations, and Tasks receive navigation destinations with honest “Coming later” states; their full workflows are later modules. Overview and Console may use only data and device controls that already exist.

Acceptance criteria:

1. Existing account and clone data open after migration without loss.
2. Legacy `accounts.tag` values migrate to normalized account-tag associations, preserving each original value as one tag.
3. An account can have zero or one category and multiple tags. Content uses an independently scoped category/tag catalogue for the later Content module.
4. Category/tag CRUD and account assignments persist after server restart.
5. The Accounts table can display and filter by category/tags without exposing credentials.
6. Browser navigation clearly separates Overview, Console, Content, Automations, Tasks, and Accounts; only implemented sections appear actionable.
7. Existing Reel, clone scan, account CRUD, Excel, login and vault workflows pass regression checks.

## Approaches Considered

| Approach | Benefits | Costs / risks | Decision |
|---|---|---|---|
| Keep the current two top tabs and add taxonomy fields in-place | Smallest patch | Navigation will become crowded as the planned modules arrive | Not selected |
| Replace the existing app with a new frontend framework and route system | Clear separation for a large product | Adds a build toolchain and rewrites a currently working frontend before module needs are proven | Deferred |
| Add a light sidebar/view shell to the current Flask/Jinja app and normalized SQLite taxonomies | Reuses current stack, supports the requested modules, and limits migration risk | The main template remains large until later module work justifies splitting it | **Selected for Module 1** |

## File Map

- `db.py` — SQLite schema migration, scoped category/tag persistence, and account taxonomy assignments.
- `app.py` — validated category/tag endpoints and compatible account API serialization.
- `excel_manager.py` — account import/export compatibility for one category and multiple tags.
- `templates/index.html` — browser navigation shell, category/tag controls, account table display/filtering, and honest future-module states.
- `tests/test_full_suite.py` — migration, persistence, API, spreadsheet compatibility, and regression coverage.
- `PROJECT_MEMORY.md` — concise record of delivered scope and verification results after implementation.

## Implementation Tasks

### Task 1: Pin the legacy data migration behavior

**Files:**
- Modify: `tests/test_full_suite.py`
- Test: `tests/test_full_suite.py`

**Interfaces:**
- Consumes: current `db.init_db(db_path)`, current `accounts.tag` field, and existing temporary-database test setup.
- Produces: regression tests defining the migration from one legacy account tag string to a one-element tag association.

- [ ] **Step 1: Add `test_taxonomy_migration_preserves_existing_accounts_and_tags`**. Create a temporary legacy SQLite database with the current accounts table and one account with `tag='creator'`; call `db.init_db(db_path)` and assert the account, username, device/package assignment, and tag value remain represented.
- [ ] **Step 2: Run the focused test and confirm it fails** because normalized taxonomy tables and migration logic do not exist yet.
- [ ] **Step 3: Add `test_taxonomy_migration_is_idempotent`**. Call `db.init_db(db_path)` twice and assert there is one migrated tag and one account-tag association.
- [ ] **Step 4: Run both focused tests and confirm the expected failure is limited to missing taxonomy behavior.**

### Task 2: Add scoped taxonomy storage and repository functions

**Files:**
- Modify: `db.py`
- Test: `tests/test_full_suite.py`

**Interfaces:**
- Consumes: existing `get_connection(db_path)` and `init_db(db_path)` patterns.
- Produces:
  - `list_categories(scope: str, db_path: Optional[str] = None) -> List[Dict[str, Any]]`
  - `create_category(scope: str, name: str, db_path: Optional[str] = None) -> int`
  - `rename_category(category_id: int, name: str, db_path: Optional[str] = None) -> bool`
  - `delete_category(category_id: int, db_path: Optional[str] = None) -> bool`
  - `list_tags(scope: str, db_path: Optional[str] = None) -> List[Dict[str, Any]]`
  - `create_tag(scope: str, name: str, db_path: Optional[str] = None) -> int`
  - `rename_tag(tag_id: int, name: str, db_path: Optional[str] = None) -> bool`
  - `delete_tag(tag_id: int, db_path: Optional[str] = None) -> bool`
  - `set_account_taxonomy(account_id: int, category_id: Optional[int], tag_ids: List[int], db_path: Optional[str] = None) -> None`
  - `get_account_taxonomy(account_id: int, db_path: Optional[str] = None) -> Dict[str, Any]`

- [ ] **Step 1: Add tests for `accounts` and `content` scopes**. Assert a category/tag created in one scope is absent from the other scope's list.
- [ ] **Step 2: Add tests for normalized uniqueness**. Normalize with `strip().casefold()`; uniqueness is case-insensitive within a scope; cross-scope duplicate display names remain valid.
- [ ] **Step 3: Add tests for assignment safety**. An account accepts one account-scoped category and multiple account-scoped tags; wrong-scope or missing IDs are rejected without partially changing assignments.
- [ ] **Step 4: Add tests that deleting an assigned category/tag fails without losing the account assignment.**
- [ ] **Step 5: Implement tables and indexes**: `categories(scope, name, normalized_name, created_at, updated_at)`, `tags(scope, name, normalized_name, created_at, updated_at)`, nullable `accounts.category_id`, and `account_tags(account_id, tag_id)` with foreign keys and unique constraints. Restrict `scope` to `accounts` or `content`.
- [ ] **Step 6: Migrate each non-empty legacy `accounts.tag` value** into one account-scoped tag and one `account_tags` row. Keep the legacy column during this release for backwards compatibility; do not split legacy values on commas.
- [ ] **Step 7: Implement the listed repository functions**. Each assignment update must validate all taxonomy IDs and commit category/tag associations in one SQLite transaction.
- [ ] **Step 8: Run the focused taxonomy tests and confirm they pass.**

### Task 3: Expose taxonomy APIs and extend account responses

**Files:**
- Modify: `app.py`
- Modify: `db.py`
- Test: `tests/test_full_suite.py`

**Interfaces:**
- Consumes: Task 2 repository functions.
- Produces: scoped JSON endpoints and account JSON containing `category` and `tags` while retaining the legacy `tag` field during transition.

- [ ] **Step 1: Add tests for `GET /api/categories?scope=accounts` and `GET /api/tags?scope=accounts`.** Assert valid scopes return only their own records and invalid/missing scopes return a 400 response.
- [ ] **Step 2: Add tests for create, rename, and delete API behavior**: empty names return 400; normalized duplicates return 409; deleting assigned taxonomy returns 409; valid operations persist.
- [ ] **Step 3: Add account API tests** proving category and tags survive create, update, get, and list operations; the API does not return password/TOTP/proxy-auth values.
- [ ] **Step 4: Implement CRUD routes** using the existing `/api/...` conventions. Accept only `scope=accounts|content`; return stable IDs, display names, and scope.
- [ ] **Step 5: Update account create/update/get/list serialization** to accept `category_id` and `tag_ids`, return `category`, `tags`, and a backwards-compatible joined `tag` value, and reject IDs from the Content scope.
- [ ] **Step 6: Run the focused route/account tests and confirm they pass.**

### Task 4: Preserve taxonomy through account spreadsheet import/export

**Files:**
- Modify: `excel_manager.py`
- Modify: `app.py`
- Test: `tests/test_full_suite.py`

**Interfaces:**
- Consumes: Task 2 account taxonomy repository functions and Task 3 account API contract.
- Produces: account workbook columns `category` and `tags`; legacy `tag` input remains accepted as one tag when `tags` is absent.

- [ ] **Step 1: Add round-trip tests** for one category, several tags, a legacy `tag` column, empty values, duplicate tag names in one cell, and tag names containing the semicolon delimiter (rejected with a row-level error).
- [ ] **Step 2: Add import tests for unknown categories/tags**. Import preview must show row errors or an explicit create choice; it must not silently create taxonomy values or silently discard assignments.
- [ ] **Step 3: Implement category/tag workbook export** without adding any secret fields. Keep formula-like value neutralization in place.
- [ ] **Step 4: Implement import mapping**. Parse a documented `tags` cell format (semicolon-separated after CSV/XLSX cell parsing); trim and deduplicate tag names case-insensitively; preserve `tag` as a legacy one-value fallback.
- [ ] **Step 5: Ensure the server-side import applies account fields and taxonomy associations safely** and reports row-level errors for unresolved category/tag names.
- [ ] **Step 6: Run the focused spreadsheet tests and confirm they pass.**

### Task 5: Add the browser shell and account taxonomy controls

**Files:**
- Modify: `templates/index.html`
- Modify: `app.py` only if the current page needs a small route/view-model addition.

**Interfaces:**
- Consumes: existing Reel Studio, Accounts & Clones UI, Task 3 APIs, and existing `escapeHtml` helper.
- Produces: persistent browser navigation for Overview, Console, Content, Automations, Tasks, Accounts; functional account category/tag assignment and filters.

- [ ] **Step 1: Keep the current Reel screen and Accounts & Clones screen functional** while moving navigation to a left-side desktop shell; preserve existing element IDs or update their callers in the same change.
- [ ] **Step 2: Add page states for planned sections.** Overview and Console may show only existing account/clone/device and live log/screen data. Content, Automations, and Tasks show clearly labeled “Coming later” states until their modules exist; do not show fake charts or fake task controls.
- [ ] **Step 3: Add category and multi-tag controls** to account create/edit. Load only account-scoped taxonomy, show “Uncategorized” when none is assigned, and allow creating a category/tag from the management control without leaving the form.
- [ ] **Step 4: Add category/tag display and filters** to the accounts table. Continue escaping all user-controlled values through `escapeHtml`; do not interpolate raw values into event-handler JavaScript.
- [ ] **Step 5: Add loading, empty, error, and save-success states** for taxonomy lists and account assignment.
- [ ] **Step 6: Manually inspect the browser at the project's current desktop viewport**. Verify navigation, modal usability, long names, empty lists, and the existing Reel workflow without changing its steps.

### Task 6: Run migration, persistence, and whole-module regression checks

**Files:**
- Modify: `tests/test_full_suite.py` if a regression case is missing.
- Modify: `PROJECT_MEMORY.md` after verification results are known.

- [ ] **Step 1: Add/confirm a restart-persistence test**: create category/tag/account assignments, reinitialize the database connection, and verify all associations remain.
- [ ] **Step 2: Run the full existing suite** with `python -m unittest tests/test_full_suite.py`; expected result is all tests pass.
- [ ] **Step 3: Run `git diff --check`**; expected result is no whitespace errors.
- [ ] **Step 4: Manually verify the flow**: create account category/tags → save → reload page → filter account → export/import workbook → verify assignment persists.
- [ ] **Step 5: Regression-check Reel upload, clone scan, account login, and protected secrets**. Confirm the existing Reel workflow still defaults to `com.instagram.android` and no secrets appear in API responses or standard workbooks.
- [ ] **Step 6: Record actual changed files and observed test results in `PROJECT_MEMORY.md`**. Do not claim a test or manual device check that was not run.

## Non-Goals for Module 1

- Full Content Library upload, media processing, and retention controls.
- Profile visiting or profile scraping.
- Multi-device task scheduling, background task queue, or fleet concurrency.
- Custom automation editor/script marketplace.
- Fake analytics or charts without persistent task history.
- Rewriting the backend in a new framework or replacing SQLite.

## Handoff

Implement only this module. Stop after Task 6 and show the user the updated browser shell, taxonomy behavior, migration results, exact test output, and any remaining limitations. Wait for review before beginning Module 2: Devices & Accounts onboarding/profile capture.
