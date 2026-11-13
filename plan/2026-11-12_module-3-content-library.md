# Module 3: Content Library Implementation Plan

**Goal:** Build a persistent local library for video and image assets, organize them with Content-scoped categories and tags, and let Reel Studio reuse library videos.

**Architecture:** Keep media files on disk under `uploads/content/` and store their metadata and taxonomy links in SQLite. Reuse the existing scoped category/tag API and Flask/SQLite conventions; add a Content Library UI and connect only video selection to the existing Reel Studio publishing flow.

**Tech stack:** Flask, SQLite, existing `db.py` repository functions, Jinja/HTML and vanilla JavaScript in `templates/index.html`, local filesystem storage, existing `unittest` suite, and Node syntax checks.

**Scope:** Library upload, browse, search/filter, category/tag assignment, safe delete, and reusing a library video in the current Reel Studio flow. Photo/carousel publishing, scheduling, automations, AI features, cloud storage, and external services are out of scope. Stop after the verification task and wait for user review before adding follow-up features.

## Current project facts

- `#contentView` in `templates/index.html` is a Coming Later placeholder.
- `app.py` accepts one video at `POST /api/upload_video`, stores it in the top-level `uploads/` folder, and returns a filename and URL. It has no persistent library listing, metadata update, or delete API.
- Reel Studio starts work using `video_filename`, and the backend currently resolves that name under `UPLOAD_FOLDER`.
- `db.py` already supports `categories` and `tags` with `accounts` and `content` scopes. Generic `/api/categories` and `/api/tags` endpoints already accept a scope. There is no content item table or content-to-taxonomy assignment table.
- Existing Flask upload cap is 500 MB; existing video extension allowlist is `.mp4`, `.mov`, `.mkv`, `.avi`.

## Decisions for this module

- Store library files at `uploads/content/<random-id>_<safe-original-name>`; never use a user-supplied path as the storage path.
- Support video extensions `.mp4`, `.mov`, `.mkv`, `.avi` and image extensions `.jpg`, `.jpeg`, `.png`, `.webp`. Keep the existing 500 MB total request cap for this phase.
- Persist original filename, generated storage key, media type (`video` or `image`), MIME type, byte size, optional Content category, and created/updated timestamps. Do not store file bytes in SQLite.
- Use the existing `content` taxonomy scope. One category and multiple tags can be assigned to each asset. Unassigned assets are allowed.
- Deleting an asset removes its database record and its file. Taxonomy assigned to assets remains protected by the existing in-use delete behavior.
- The library can preview images and videos in the browser. Only video assets can be selected by Reel Studio in this module.
- Use page-based listing with a default of 50 items and a maximum of 100 per page so the browser does not load the entire library at once.

## Files expected to change

- `db.py`: `content_items` and `content_tags` schema/migration, scoped assignment validation, CRUD/list helpers.
- `app.py`: content upload/list/read/update/delete routes, safe file serving, and Reel Studio resolution of library asset IDs.
- `templates/index.html`: Content Library view, content taxonomy controls, and Reel Studio library picker.
- `tests/test_full_suite.py`: database/API/route regression tests and UI contract checks.
- `PROJECT_MEMORY.md`: record shipped behavior and verification after implementation.

## Review focus

- An unsupported extension, empty file, malformed taxonomy ID, or file exceeding the request cap is rejected without leaving an orphan record or file.
- A category or tag from `accounts` scope cannot be attached to content; failed updates leave old assignments intact.
- A stored filename or asset ID cannot be used to read or delete a file outside `uploads/content/`.
- Removing an asset that is selected or previewed in the browser leaves the UI in a clear state and does not break other assets.
- Page reload and app restart preserve asset records, category/tag links, and previews.
- Existing Reel Studio direct upload and publish behavior continues to work; a library image cannot be submitted as a Reel video.

---

### Task 1: Add persistent content asset records

**Files:**
- Modify: `db.py`
- Test: `tests/test_full_suite.py`

**Interfaces:**
- Produce `create_content_item(original_filename, storage_key, media_type, mime_type, size_bytes, category_id=None, tag_ids=None, db_path=None) -> int`.
- Produce `get_content_item(content_id, db_path=None) -> dict | None`.
- Produce `list_content_items(search=None, media_type=None, category_id=None, tag_id=None, page=1, per_page=50, db_path=None) -> dict` with `{items, total, page, per_page, pages}`.
- Produce `update_content_item_taxonomy(content_id, category_id, tag_ids, db_path=None) -> None`.
- Produce `delete_content_item(content_id, db_path=None) -> dict | None`; this removes the DB record and returns its storage key for filesystem cleanup by the API layer.

- [ ] Add a schema migration for `content_items` and `content_tags`; add indexes for created time, media type, category, and tag lookups. Foreign keys must cascade tag links when an item is removed and restrict deletion of an assigned Content category/tag.
- [ ] Extend `db.delete_category` and `db.delete_tag` to detect assignments from content items as well as accounts, and keep their existing `409 TaxonomyInUseError` API behavior for both scopes.
- [ ] Add failing database tests for create/get/list, category and multiple-tag assignment, `content` vs `accounts` scope isolation, pagination bounds, update atomicity, and deletion link cleanup.
- [ ] Implement the repository functions using the existing SQLite transaction and taxonomy normalization patterns.
- [ ] Verify the focused repository tests pass and that `init_db()` can run repeatedly without duplicating migrations or losing records.

### Task 2: Add safe content file and metadata APIs

**Files:**
- Modify: `app.py`
- Modify: `db.py` only if Task 1 interfaces need a reviewed adjustment
- Test: `tests/test_full_suite.py`

**Interfaces:**
- `POST /api/content`: accept multipart `files` (one or more files); return per-file success/error results and created asset metadata. A failed file must not prevent reporting other files in the same request.
- `GET /api/content`: accept `search`, `media_type`, `category_id`, `tag_id`, `page`, and `per_page`; return the repository page result.
- `GET /api/content/<int:content_id>/file`: serve only the file resolved from a database record whose storage key is contained under `uploads/content/`.
- `PATCH /api/content/<int:content_id>`: update `category_id` and `tag_ids` atomically.
- `DELETE /api/content/<int:content_id>`: remove the item record and its stored file; return 404 for an unknown asset.
- `POST /api/start` may accept `content_id` as an alternative to the existing `video_filename`; resolve it to a stored library video and reject image assets. Keep the current `video_filename` compatibility path.

- [ ] Add failing Flask client tests for multi-file upload, video/image allowlists, empty/unsupported files, listing filters/pagination, safe serving, taxonomy scope rejection, safe delete, and missing IDs.
- [ ] Add tests proving the library selector can pass a video `content_id` into the existing Reel workflow and that an image `content_id` is rejected before any device work starts.
- [ ] Implement safe generated storage keys and write each file under `uploads/content/`; create its DB row only after the file write succeeds, and remove the file if the DB transaction fails.
- [ ] Resolve all served/deleted files from DB records and verify the resolved path remains inside the content storage directory; never accept a raw storage path from the client.
- [ ] Preserve current `/api/upload_video`, `/uploads/<filename>`, and direct `video_filename` publishing behavior.
- [ ] Run focused API tests and existing upload/Reel regression tests.

### Task 3: Build the Content Library screen

**Files:**
- Modify: `templates/index.html`
- Test: `tests/test_full_suite.py`

**Interfaces:**
- Replace `#contentView` placeholder with a responsive Content Library view.
- Add selectors with stable IDs for multi-file upload, search, type/category/tag filters, grid/list results, pagination, and upload feedback.
- Use `GET /api/categories?scope=content` and `GET /api/tags?scope=content` for selectors; use the existing scoped CRUD endpoints for Content taxonomy management.

- [ ] Add UI contract tests for the upload input, filter controls, category/tag management, library results, pagination, and empty/error states.
- [ ] Implement image and video cards with filename, media type, size, created time, preview, category, and tags. Use text-safe DOM rendering for filenames and taxonomy names.
- [ ] Implement multi-file upload with visible per-file progress/result, then refresh the current page after successful uploads.
- [ ] Implement search, media type/category/tag filters, page navigation, category assignment, multi-tag assignment, and confirmed delete.
- [ ] Ensure the UI distinguishes an empty library from a filter with no matches, and preserves filter state while moving between pages.
- [ ] Run Node syntax checks on extracted inline scripts and the focused UI/API tests.

### Task 4: Add a Reel Studio library video picker

**Files:**
- Modify: `templates/index.html`
- Modify: `app.py` only for the `content_id` workflow path from Task 2
- Test: `tests/test_full_suite.py`

**Interfaces:**
- Reel Studio gets a “Choose from Content Library” action that lists only video assets and selects one `content_id`.
- Starting the Reel workflow sends `content_id` for a library asset or the existing `video_filename` for a direct upload; never send both.

- [ ] Add UI/backend regression tests that a chosen library video starts with its resolved file and that selecting an image is impossible in the picker and rejected by the API.
- [ ] Implement the picker and show the selected library filename in Reel Studio.
- [ ] Verify replacing a direct-upload video with a library video clears the old selection, and clearing a library selection restores the current direct-upload/gallery controls.
- [ ] Keep Instagram target package, caption/audio controls, and Reel posting pipeline unchanged.

### Task 5: End-to-end verification and stop for review

**Files:**
- Modify: `tests/test_full_suite.py` if verification exposes a gap
- Modify: `PROJECT_MEMORY.md` after implementation is verified

- [ ] Run the full test suite: `python -m unittest tests/test_full_suite.py`.
- [ ] Run `git diff --check` and `node --check` for every extracted inline script.
- [ ] Run browser E2E checks for empty state, multi-file upload, image/video preview, filters/search/pagination, category/tag assignment, delete, reload persistence, and Reel Studio library video selection.
- [ ] Verify upload/API rejection for unsupported and oversized media, taxonomy scope mismatch, missing/deleted assets, and attempted storage path traversal.
- [ ] Confirm existing direct video upload and a non-device Reel workflow test still pass.
- [ ] Record only the completed module and actual verification results in `PROJECT_MEMORY.md`; do not mark unverified physical Instagram publishing as tested.
- [ ] Stop and present the implementation plus verification report for user review. Do not start scheduling, automation, photo publishing, or other follow-up features.
