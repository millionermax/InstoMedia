---
mode: plan
task: Frontend Light/Dark Design System and Shell Cleanup
complexity: medium
created_at: 2026-11-12
validated: false
---
complexity: medium

### Step 1/6 [analysis]
- **Focus**: Define the requested outcome and constraints.
- **Findings**: User wants a polished frontend with both Light and Dark themes selectable by the user, a black-and-white neutral foundation, blue primary actions, green success states, orange warnings, and a cleaner header/sidebar. Current scope is presentation only; existing account, device, automation and API behavior must stay intact.
- **Confidence**: high
- **Next**: Map the affected UI surfaces.

### Step 2/6 [decomposition]
- **Focus**: Identify where theme work belongs.
- **Findings**: `templates/index.html` is a single 1,951-line HTML template with inline CSS and JavaScript. Theme-sensitive surfaces include document/body, cards, borders, controls, tables, dialogs, device status, console, phone preview frame, header, sidebar, badges, and dynamically rendered content. `switchMainTab()` assigns pink classes at runtime, so static CSS replacements alone will not complete the change. Git status also shows unrelated in-progress product changes; the implementation agent must preserve them.
- **Confidence**: high
- **Next**: Compare implementation approaches.

### Step 3/6 [comparison]
- **Focus**: Choose between semantic theme tokens and broad utility-class rewriting.
- **Findings**: Semantic CSS variables with small reusable surface/action classes offer a centralized Light/Dark mapping and minimize repeated edits. Rewriting every Tailwind utility throughout the template could be more visually precise per element, but is more invasive and can easily miss dynamic or conditional classes. A third option, adding only a body-level light-mode override, is fastest but leaves pink/indigo accents, modals, and JavaScript-generated navigation inconsistent.
- **Confidence**: high
- **Next**: Weigh tradeoffs and define a chosen path.

### Step 4/6 [evaluation]
- **Focus**: Compare approaches against maintainability, consistency, and implementation risk.
- **Findings**: Semantic tokens are the best fit for this large single-file template: they keep existing HTML structure and behavior while letting the agent migrate shared surfaces and controls in bounded groups. Keep Tailwind for layout and spacing; use semantic classes or CSS variables for theme-sensitive colors. A full markup/class rewrite is not justified for this visual-system task.
- **Confidence**: high
- **Next**: Specify acceptance criteria and execution order.

### Step 5/6 [analysis]
- **Focus**: Set theme behavior and accessible color roles.
- **Findings**: Store explicit user choice in `localStorage`; on first visit use OS preference, then apply the selected theme before first paint to avoid a flash. Use neutral surface/text/border tokens for both modes; blue for primary/selected navigation, green for success/connected, orange for warning/attention, and red for destructive/error states. Include keyboard-operable toggle with an accessible name and visible focus. Check normal text at 4.5:1, large text at 3:1, and control/state boundaries at 3:1.
- **Confidence**: high
- **Next**: Convert the findings into reviewable agent tasks.

### Step 6/6 [synthesis]
- **Focus**: Produce ordered, independently verifiable implementation work and risk controls.
- **Findings**: Start with theme tokens and initialization, then shared shell, then recurring controls/cards, then remaining routes and dynamic states, then manual regression review. Keep scope limited to frontend presentation and avoid altering backend behavior or unrelated files.
- **Confidence**: high
- **Next**: → Gate

## Plan: Frontend Light/Dark Design System and Shell Cleanup

### Problem Definition
- **Core problem**: The existing interface is dark-only and mixes slate surfaces with pink/purple gradients and several unrelated accent colors. It needs a consistent user-selectable Light/Dark system and a cleaner header/sidebar.
- **Constraints**: Main implementation target is `templates/index.html`. Preserve current routes, tab navigation, forms, API calls, device state updates, modals, account operations, and unsaved work elsewhere in the repository. Do not introduce a new UI framework or build pipeline.
- **Success criteria**: (1) User can switch Light/Dark from the shell and the choice persists across reloads. (2) First-load theme is stable and has no visible flash. (3) Header, sidebar, pages, forms, dialogs, tables, badges, console and dynamic states are legible in both themes. (4) Blue/green/orange colors follow semantic roles, with red reserved for errors/destructive actions. (5) Navigation, device refresh, account and other existing controls continue to work.

### Analysis Summary
The app is a single, large HTML template with inline styles and JavaScript. A broad palette replacement risks missing conditional classes, while a body-only override would leave runtime states inconsistent. Semantic color tokens with staged migration provide the best balance of consistency and bounded change (Steps 2–4). Theme should be user-selectable and persistent, with OS preference as the first-visit default. Maintain readable contrast and keyboard accessibility (Step 5).

### Tradeoff Comparison

| Approach | Consistency | Change risk | Fit for this template |
|---|---|---|---|
| Semantic CSS tokens + shared component classes | High | Low to medium | Recommended; centralizes theme mapping and handles dynamic states |
| Replace color utilities across all markup | Medium to high | High | Too invasive; easy to miss conditionally generated classes |
| Add a Light-mode body override only | Low | Low initially, high follow-up cost | Incomplete; dialogs, controls, and pink accents remain inconsistent |

### Execution Plan
1. **Add semantic theme tokens and early theme initialization** in `templates/index.html`: define Light/Dark values for app background, raised surface, card, text levels, border, hover, primary action, success, warning, error, and focus ring. Persist explicit choice under a namespaced `localStorage` key; on first visit initialize from `prefers-color-scheme`. Apply theme before rendering to prevent a flash. → Verify saved preference survives reload and first visit follows OS theme when no saved preference exists.
2. **Add an accessible header theme control and simplify the shared header**: use a compact Light/Dark toggle with state conveyed in its accessible name/pressed state; remove the Instagram gradient treatment from product chrome; align device status and refresh control to semantic tokens. Keep the existing title and device-refresh function. → Verify toggle works via mouse and keyboard, exposes its current state, and does not break `refreshDevice()`.
3. **Clean the sidebar and make navigation state theme-aware**: group existing primary workspace links consistently, reduce clutter from the “Roadmap Modules” heading and Later badges without deleting existing tabs, and replace pink runtime active classes in `switchMainTab()` with semantic active/idle states. → Verify all seven shell tabs still navigate and selected state is clear in both themes.
4. **Migrate shared surfaces and controls**: make cards, form fields, buttons, tables, dialogs, badges, separators, focus states, and log panels consume semantic tokens. Retain blue for primary actions, green for success/connected, orange for warnings, and red only for errors/destructive actions. Keep any embedded Instagram/device screen content visually untouched where it represents external app pixels. → Verify representative controls and status states are legible in both themes.
5. **Apply theme tokens across all existing views and dynamic render paths**: cover Overview, Reel Studio, Accounts, Console, Content, Automations, Tasks, modals, device/clone status, and DOM produced by JavaScript. Keep layout, copy, and API behavior unchanged unless a visual cleanup requires a class-only adjustment. → Verify both themes on every view and check for leftover hard-coded brand gradients or unreadable legacy slate/pink classes.
6. **Perform a manual regression and accessibility pass** at desktop and narrow viewport sizes: check theme persistence, no flash, contrast, visible keyboard focus, responsive shell, scroll areas, modal content, and all existing navigation/form/device controls. → Record any known limits in the handoff; do not claim automated or E2E coverage unless actually run.

### Design References
- [Material Design 3 color roles](https://m3.material.io/styles/color/roles) — semantic roles for surfaces, foregrounds, containers, and accents.
- [IBM Carbon color](https://www.carbondesignsystem.com/building-blocks/foundations/color) — neutral-first palettes and role-based tokens across light and dark themes.
- [WCAG 2.2 contrast minimum](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html) — text contrast targets.
- [WCAG non-text contrast](https://www.w3.org/WAI/WCAG21/Understanding/non-text-contrast.html) — contrast for controls and state indicators.

### Risks
- **Missed hard-coded colors or inline dynamic styles** → Search the full template for legacy pink/indigo/gradient and dark-only color utilities; inspect JavaScript-generated `className`/`innerHTML` paths.
- **Theme flash before JavaScript runs** → Put a small theme bootstrap in `<head>` before content styles and use a fallback if storage is unavailable.
- **Tailwind CDN class scanning misses dynamic class names** → Keep layout utilities as-is, but implement semantic active/surface classes in authored CSS rather than assembling new theme colors from dynamic Tailwind fragments.
- **Unreadable status colors in one theme** → Pair semantic foreground/background tokens and verify contrast for text and non-text indicators.
- **Overlapping changes in shared working tree** → Restrict edits to the intended frontend file; inspect current diff before editing and preserve all existing work.

### Open Questions
- [x] Theme preference: both Light and Dark modes; user can choose.
- [x] Initial theme assumption: follow OS preference only until the user saves a selection.
- [ ] Product naming/title cleanup is not part of this plan unless the frontend owner explicitly adds it.

