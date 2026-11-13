# Accounts Module Rebuild Plan

> **For Antigravity:** Implement this plan in order. First inspect the current worktree and preserve all unrelated in-progress changes. Keep existing account records and compatible API contracts. Stop after final verification and report actual results.

## Goal

Rebuild the browser-based Accounts experience around profile-only account records. Scan and map installed Instagram/clone apps on the selected phone, let the user choose one or more apps, then open each selected app sequentially to capture the signed-in profile's username, follower count, and post count. Support single and bulk add, with category/tag assignment. Do not require or prompt for passwords or other login credentials. Apply the agreed monochrome visual foundation with user-selectable Light and Dark themes, blue primary actions, green success states, and orange warnings.

## Current State

- The Accounts view lives in `templates/index.html` and currently combines a six-column account table, category/tag filters, clone inventory, batch-login monitor, onboarding flow, manual account editing, Excel import/export, taxonomy management, secret-vault actions, and APK installation/clone scanning.
- The top toolbar exposes many equally weighted actions. Account and clone management compete for space in the same 8/4-column layout.
- Existing APIs and persistence cover accounts, taxonomy, encrypted secrets, clone inventory, profile snapshots, imports/exports, and login status. Current onboarding APIs launch, capture, and confirm one device/package at a time. Reuse them where possible and add only the smallest bulk orchestration needed.
- `plan/2026-11-12_module-2-devices-account-onboarding.md` defines device/profile safety rules. This plan extends it to a user-selected, sequential multi-package workflow; it does not permit parallel device activity or arbitrary-profile scraping.

## Scope and Constraints

- Rework the Accounts frontend information architecture and interaction flow in `templates/index.html`.
- Implement the Light/Dark preference and semantic tokens needed by the app shell and Accounts view if they are not already present. Persist the user selection; use OS preference only before the user chooses a theme.
- Reuse the existing backend/data model where possible. A minimal batch-capture/save endpoint or sequential client orchestration is allowed only if necessary to make bulk add reliable; document the choice and keep writes duplicate-safe.
- The new manual and device-based add flows are metadata/profile-only: no password, 2FA, or login credential fields, prompts, or credential entry. Do not delete legacy vault data or rewrite unrelated login code. Keep legacy login/vault controls out of this profile-onboarding flow.
- Bind every scan/launch/capture to one explicitly selected online device and confirmed installed package. The user may select multiple packages on that device, but process one package at a time. Require a final review and explicit save of selected captured profiles. Do not add unattended login or challenge bypass.
- Only capture a currently signed-in profile belonging to the user or an account they are authorized to manage. If signed out, blocked, ambiguous, or on the wrong profile, mark that package as skipped/needs attention; never enter credentials or save guessed data.
- Do not add arbitrary-profile scraping, proxy rotation, new database fields without justification, or a new frontend framework.

## Target Experience

1. **Accounts workspace:** Make the profile-account list the default focus. Put search, category/tag filters, account count, and a clear **Add account** action in a compact toolbar. Keep metadata import/export and taxonomy management discoverable as secondary actions.
2. **Account list:** Emphasize username, latest followers/posts snapshot, assigned clone app/device, category and tags. Keep any legacy records with extra fields readable, but do not require or expose credentials in the new profile-account workflow.
3. **Single add:** Offer a manual metadata form (username, optional profile counts, selected device/app, category/tags) or add-from-device capture. Save only after the user reviews the row.
4. **Bulk add:** Let the user select several confirmed clone apps from one selected phone. Process the queue sequentially: launch app → confirm it is foregrounded → inspect its own Profile screen → capture username/followers/posts → record result and continue. Show progress and allow pause/cancel. A signed-out, challenged, ambiguous, or mismatched app is skipped and clearly reported; no credentials are requested.
5. **Review and save:** Show a review table of captured rows with editable counts and username, common category/tag defaults plus per-row overrides, duplicate detection, and selectable save checkboxes. One explicit **Save selected accounts** action persists reviewed rows. Preserve successes if some rows fail and present failed rows for retry; never overwrite an existing account silently.
6. **Clone inventory:** Keep the full discovered package map visible by device, package ID, app label, confirmation state, and last-seen status. Do not assume every Instagram-like package is a valid clone; require user confirmation before it can be launched.
7. **Theme and responsive layout:** Apply both Light and Dark themes to shell, account list, clone inventory, wizard, review table, progress/errors, and dialogs. Keep scanning/capture state readable on desktop and narrow screens.
## Implementation Plan

### Task 1: Establish theme foundation for shell and Accounts

**Files:** `templates/index.html`

- Add semantic tokens for app background, raised surface, card, primary/secondary text, border, hover, focus ring, primary action, success, warning, and error in Light and Dark modes.
- Add an accessible Light/Dark control in the header and persist the user's explicit selection. On a first visit with no saved choice, follow `prefers-color-scheme`; initialize before the page paints.
- Replace the shell's active pink gradient with a blue semantic selected state. Keep green for success/connected, orange for warning/attention, and red for destructive/error states.
- Keep embedded Android/Instagram screen pixels untouched.

**Verify:** Toggle with keyboard and mouse; reload in both modes and confirm preference persists, first-load default follows OS setting when storage is empty, and device status refresh still works.

### Task 2: Restructure Accounts workspace hierarchy

**Files:** `templates/index.html`

- Refactor the Accounts view into clear account-list, clone-inventory, single-add, bulk-add, and review/save sections or lightweight in-module tabs/panels. Keep shared app tab routing.
- Make **Add account** the primary action. Group clone scan, import/export, and taxonomy actions as secondary utilities. Remove password/2FA fields and credential prompts from the new add/edit flows; keep legacy data untouched.
- Ensure loading, empty, filtered-empty, error, and success feedback have consistent layouts and theme-aware colors.

**Verify:** Navigate between Accounts subviews; confirm list is the default, clone scan and taxonomy/import tools remain reachable, and credential prompts are absent from profile onboarding.
### Task 3: Improve account list and filters

**Files:** `templates/index.html`

- Add client-side search across username, package, device serial, category, and tags using the already loaded `allAccounts` data; do not add an API request or pagination without need.
- Keep category and tag filters working together and make reset behavior clear. Retain any useful legacy status filter without making login status central to new profile-only records.
- Improve row hierarchy and responsive layout. Keep action buttons individually labeled for assistive technology and retain a confirmation step for delete.
- Do not show password/2FA controls or vault presence in the rebuilt profile-account list. Preserve existing stored data and backend records without displaying secrets.

**Verify:** Search/filter combinations return correct rows, reset clears all filters, empty states distinguish no accounts from no matches, and account row actions still target the correct ID.
### Task 4: Build manual single-add and device-based bulk capture

**Files:** `templates/index.html`; add a minimal route/service change only if the existing single-package endpoints cannot support sequential orchestration.

- Present Manual add and Add from device as distinct entry paths. Manual add captures profile metadata only and does not ask for passwords or credentials.
- For device mode, select one online device, scan installed clone candidates on that device, show the mapped inventory, and let the user select one or more confirmed packages.
- Orchestrate selected packages sequentially using the existing single-package launch/capture flow: never launch two apps concurrently. Verify foreground package before parsing its own profile screen.
- Capture username, post count, follower count, source device/package, and timestamp. Do not capture arbitrary profile URLs. If a package is signed out or blocked, skip it and let the user handle the app manually; never request or enter credentials.
- After queue completion, show captured/skipped/error/duplicate rows for review. Allow edit of uncertain metrics, shared default category/tags, per-row overrides, and save selection. Persist only selected/reviewed rows after one explicit bulk confirmation.
- Handle partial save errors without losing successful rows; retain failed rows and report the account/package-specific error for retry.
- Preserve Module 2 device binding, package confirmation, identity review, duplicate checks, and safe stop conditions.

**Verify:** Manually add a profile-only record with category/tags and no credentials; run a multi-package queue on an authorized device, confirm it opens one app at a time, inspect review/edit/skip states, save selected rows, and verify duplicate/partial-failure handling.
### Task 5: Separate clone inventory and metadata transfer

**Files:** `templates/index.html`

- Make clone inventory easier to scan by selected device, package, and confirmation state. Permit multi-select only among confirmed, installed packages on that device.
- Group spreadsheet template/download/import actions together and explain metadata export boundaries.
- Keep legacy encrypted vault functions out of the new profile add flow. Do not include credentials in profile-account creation or ordinary export.
- Preserve APK installation target selection and error handling.

**Verify:** Scan and confirm several clone candidates on one explicit device, import/export profile metadata with category/tags, and confirm no password/credential fields appear in the new flow.
### Task 6: Regression and accessibility review

**Files:** `templates/index.html`; tests only if a frontend behavior regression is identified.

- Review Light and Dark modes at desktop and narrow viewport widths.
- Check keyboard navigation, visible focus, accessible labels, semantic status text, text/control contrast, modal focus and dismissal, long usernames/package IDs, and scrolling.
- Run the existing relevant test suite and `git diff --check`; perform browser checks for the account flows. Perform physical-device onboarding checks only when an authorized device is available; record if unavailable.
- Confirm no backend/schema changes were introduced unless justified and listed in the handoff.

**Verify:** Report exact commands/results and manual flows completed. Do not claim device/E2E coverage that was not performed.

## Acceptance Criteria

- Accounts page is easier to scan and the account list is the clear default workspace.
- Single and bulk profile-account add both work; selected clone apps are opened strictly sequentially and progress/results are visible per app.
- Bulk review allows category/tag assignment, per-row edit/skip, duplicate detection, and one explicit save action; partial failures do not erase successful saves.
- Manual and bulk profile-account creation do not ask for or require a password or login credential.
- Account, taxonomy, clone inventory, metadata import/export, and profile-history data remain readable and persistent; legacy credential data is not deleted by this UI rebuild.
- Light/Dark themes both cover the shell and Accounts view; the selection persists and can be changed by the user.
- Account search and combined filters work without changing backend data behavior.
- No ordinary account list/export leaks password, 2FA, or proxy-secret values.
- Onboarding retains explicit device/package binding, review, confirmation, and safe stop behavior.
- Agent handoff documents files changed, test results, manual checks, and known limitations.

## Risks and Mitigations

- **Hidden actions disappear during decluttering** â†’ Map each existing button/function before restructuring and include a reachability checklist.
- **Large inline template increases accidental regressions** â†’ Make focused edits to Accounts markup and its handlers; preserve unrelated frontend and backend edits in the shared worktree.
- **New search conflicts with existing filters** â†’ Keep one derived filtered array and test all filter combinations against existing `allAccounts` rows.
- **Theme work leaves legacy colors in dynamic content** â†’ Search JavaScript `className` and `innerHTML` renderers, not just static markup.
- **Sensitive values leak during redesign** → Do not request or render credentials in new add flows; keep legacy vault values out of the DOM and logs.
- **Clone/account assignment crosses devices** â†’ Display full device serial and package identifier and preserve explicit target selection and confirmation.

## Out of Scope

- Cloud sync, remote device fleet support, arbitrary profile scraping, bulk profile visits beyond user-selected clone apps, proxy rotation, credential/2FA challenge bypass, concurrent app launches, or unattended automation.
- Rebuilding Dashboard, Reel Studio, Console, Content, Automations, or Tasks beyond shared theme compatibility.


