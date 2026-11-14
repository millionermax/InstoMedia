---
mode: plan
task: Install and manage Instagram clone apps from the PC
complexity: complex
created_at: 2026-11-08T00:00:00+05:30
validated: false
---

# Install and manage Instagram clone apps from the PC

## Analysis

### Step 1/9 [analysis]
- **Focus**: Define what clone support means for this release
- **Findings**: The request is to manage existing Instagram-compatible app instances on a connected Android phone, install an operator-provided APK from the PC, discover installed packages, and bind each managed account to an exact device/package pair. Creating or repackaging APK clones is not established as a requirement and is excluded.
- **Confidence**: high
- **Next**: Break the user workflow into install, discover, map, and operate responsibilities.

### Step 2/9 [decomposition]
- **Focus**: Separate feature responsibilities
- **Findings**: The workflow divides into four parts: APK selection and install; package inventory and operator confirmation; persistent account-to-device/package assignment; and launch/login use of that assignment. Existing account credentials, 2FA, Excel, and Reel features are adjacent but should remain separate integrations.
- **Confidence**: high
- **Next**: Check what the current backend already provides.

### Step 3/9 [analysis]
- **Focus**: Assess current project readiness
- **Findings**: The Flask app already scans Android package names, stores per-device clone candidates and confirmation state, exposes clone scan/confirm routes, and stores `device_serial` and `package_name` on accounts. The account/package uniqueness constraint is already present. There is no APK upload/install workflow in the current backend, so this is the main missing capability.
- **Confidence**: high
- **Next**: Extract competitor product patterns from public documentation.

### Step 4/9 [analysis]
- **Focus**: Learn the documented IgMatrix pattern
- **Findings**: IgMatrix has users install clone apps separately, then uses package IDs to detect, select, and target them. It recommends exact account-level package assignment where fixed account routing matters; prefix discovery is a bulk alternative with a multi-account switching limitation. Public docs describe user-facing behavior, not internal source code.
- **Confidence**: high
- **Next**: Extract the documented GenFarmer pattern and its limits.

### Step 5/9 [analysis]
- **Focus**: Learn the documented GenFarmer pattern
- **Findings**: GenFarmer documents APK upload and verified-store installation to connected devices, plus centralized account/device operations. Public materials do not establish that it creates or repackages Instagram clones, nor do they substantiate a 25+ clone limit per phone. The useful pattern for this project is PC-to-device install and post-install management.
- **Confidence**: high
- **Next**: Compare viable implementation approaches against the project's constraints.

### Step 6/9 [comparison]
- **Focus**: Compare installation and assignment strategies
- **Findings**: User-installed APKs plus manual package mapping is the smallest change but makes installation cumbersome. A local APK install flow plus package rescan and exact account mapping matches the documented competitor experience and reuses the existing backend. Building an APK clone/repackaging pipeline adds signing, compatibility, integrity, and maintenance concerns without evidence it is needed. The local install-and-map flow is selected for the first release.
- **Confidence**: high
- **Next**: Set verifiable success conditions and safety boundaries.

### Step 7/9 [evaluation]
- **Focus**: Define boundaries and acceptance criteria
- **Findings**: Success means an operator can choose a local APK, select a connected device, review the target and install action, install successfully, rescan packages, confirm the detected package, and assign an account to that exact package. Device/install errors must be clear; failed installs must not create account bindings. Do not download arbitrary APKs, re-sign/repackage them, bypass Android or Instagram security prompts, or promise a safe clone count.
- **Confidence**: high
- **Next**: Order the implementation into independently verifiable slices.

### Step 8/9 [decomposition]
- **Focus**: Sequence implementation and verification
- **Findings**: Start with existing database and route conventions, then add safe file handling and ADB install, then refresh package inventory, integrate exact mapping in the UI, and verify login/automation launch uses the saved package. Finish with mocked ADB checks and one authorized test device/account flow. Keep existing login, spreadsheet, and Reel behavior covered as regression checks.
- **Confidence**: high
- **Next**: Identify operational risks and mitigations.

### Step 9/9 [synthesis]
- **Focus**: Resolve risks and finalize the build handoff
- **Findings**: Main risks are invalid or incompatible APKs, installation requiring device-side confirmation, package detection mistakes, accidental APK retention, and regression from changing package resolution. Mitigate with validation, explicit device/action confirmation, post-install rescan plus operator confirmation, temporary-file cleanup, and single-account end-to-end verification before batch operation. The implementation plan is ready for validation.
- **Confidence**: high
- **Next**: → Gate

## Problem Definition

- **Core problem**: The project can discover and confirm clone candidates but cannot install an operator-provided APK from the PC or provide a complete install → detect → confirm → account-bind workflow.
- **Constraints**: Keep the local Python/Flask/uiautomator2/ADB architecture; first release is a local Windows single-user app with one connected Android phone; use exact package mapping per account; do not build an APK repackaging/cloning/signing pipeline; do not download APKs from untrusted sources; preserve existing account, Excel, login, and Reel workflows.
- **Success criteria**: An operator selects a local APK and connected device, confirms the install, receives a clear result, rescans installed packages, confirms the package, maps an account to that exact package, and can verify the selected package is the one launched. Errors do not create partial bindings, and temporary APK copies are cleaned up.

## Analysis Summary

IgMatrix's published flow centers on package discovery and exact account-to-package mapping after the user installs a clone app. GenFarmer's public app manager documents PC-side APK upload and installation to a device. This plan combines those documented product patterns without claiming access to either vendor's internal code. The project already has per-device package inventory, candidate confirmation, account package fields, and a uniqueness constraint; APK installation and end-to-end wiring remain to be implemented.

## Execution Plan

1. **Map current flows and preserve existing behavior.** Trace clone scan/confirm, account CRUD, account launch/login, and ADB command conventions. Identify the package-resolution path used by login and Reel execution. → **Verification:** Record current defaults and add/identify regression checks showing an unconfigured account still targets the current official package.
2. **Add guarded local APK intake.** Accept a user-selected APK through the local UI, enforce `.apk` extension and a bounded file size, use a generated temporary filename, and remove temporary copies after install or failure. Do not add an APK download service. → **Verification:** Test allowed/rejected extensions, size limits, safe filenames, and cleanup on success, error, and cancellation.
3. **Implement device-targeted installation.** Reuse the connected-device ADB layer to run a single explicit install operation against the selected serial. Return structured outcomes for success, no device, offline device, parse/install error, downgrade or signature conflict, and user/device confirmation required. → **Verification:** Mock ADB outputs and confirm the selected serial is passed to every command; test each result classification.
4. **Rescan and verify package inventory after install.** Run package discovery for that same device only after installation reports success. Show package ID and app label; require operator confirmation before treating a new candidate as an Instagram clone. → **Verification:** Mock package lists for new package, pre-existing package, unrelated app, failed install, and no device; ensure no false clone confirmation.
5. **Bind an account to an exact device/package.** Reuse account `device_serial` and `package_name`; validate the package is installed and confirmed, enforce the existing unique device/package mapping, and show the mapping in the account list/editor. → **Verification:** Cover valid mapping, duplicate mapping rejection, package removed after scan, device changed, and account reassignment.
6. **Route account operations through the saved mapping.** Ensure login and any account-scoped action resolve the selected account's exact package. Keep the official package fallback only for accounts without an explicit mapping; do not use prefix matching to silently select an account's app. → **Verification:** Mock two packages on one device and assert each account launches only its saved package; verify fallback behavior for legacy accounts.
7. **Add minimal install and mapping UI.** Add device selection, APK chooser, install progress/result, refresh packages, confirm candidate, and account assignment using existing dashboard conventions. Keep per-device counts informational; do not promise 25+ clones or infer capacity from package count. → **Verification:** Manual UI flow on a connected test phone and checks for empty, loading, success, failure, and disconnected states.
8. **Run staged end-to-end and regression verification.** Use mocked ADB checks first, then one operator-authorized test APK and test device/account. Verify install → rescan → confirm → account bind → launch selected package. Check account import/export, 2FA login flow, and existing Reel workflow remain functional. → **Verification:** Save test output, confirm no secret/APK path leakage in logs, and record device/Android/app version used for the manual run.

## Alternatives Considered

| Approach | Implementation effort | Mapping reliability | Operational risk | Decision |
|---|---:|---:|---:|---|
| User installs APK manually; app only scans and maps packages | Low | High after confirmation | Repeated manual device work | Keep as fallback, not primary UX |
| PC-side local APK install, rescan, operator confirm, exact account mapping | Medium | High; package tied to device and account | Install may fail for compatibility/signature/device reasons | **Selected** |
| Build APK clone/repackage/signing system | High | Variable across Instagram releases | Signing, compatibility, integrity, distribution, and maintenance burden | Excluded from first release |
| Prefix-based runtime targeting for account operations | Low initially | Low for one-account-per-clone mapping | Ambiguous routing and difficult verification | Not selected for account-bound flows |

## Risks and Mitigations

- **APK is incompatible, malformed, or signed differently from an existing install** → show ADB install result plainly; do not auto-uninstall or overwrite; let operator resolve it on-device.
- **Phone requires an install confirmation or special USB permission** → show a clear waiting/permission state and let the user approve on the device; do not bypass Android prompts.
- **Wrong app is treated as an Instagram clone** → display full package ID and app label, require explicit operator confirmation, and avoid claiming package identity proves authenticity.
- **Install succeeds but package scan is stale or inconclusive** → rescan the same device, show scan time, and do not create a mapping until the package is present and confirmed.
- **APK temporary file is left on disk** → use a task-specific temporary directory and cleanup in `finally`; test interrupted and error paths.
- **Existing account execution breaks when routing changes** → preserve official-app fallback and verify all account-scoped launch paths plus the Reel regression flow.
- **User expects 25+ apps to work on every phone** → report installed count only; device capacity depends on model, Android version, storage, RAM, and clone implementation; do not advertise a fixed limit.
- **Clone apps may violate Instagram terms or trigger account restrictions** → make supported-app status and account risk clear; limit automated operations to accounts the operator is authorized to manage.

## Open Questions and Assumptions

- [x] First release is local Windows + one connected phone; no remote multi-user/device farm.
- [x] APK source is user-provided local file; no automatic download catalogue or clone generation.
- [x] Exact account-to-package mapping is authoritative; runtime package-prefix matching is not used for account-bound login/actions.
- [ ] Before implementation, inspect existing UI conventions and device-specific ADB handling; retain the current dashboard design.

## Research Sources

- [IgMatrix: Clone App Support](https://www.igmatrix.com/docs/tutorial-basics/clone-apps-support)
- [IgMatrix: App Package Selection](https://www.igmatrix.com/docs/tutorial-scripts/package-selection)
- [GenFarmer: App Manager](https://genfarmer-support.gitbook.io/genfarmer-eng/main-menu-bar/app-manager)
- [GenFarmer: Instagram Trust](https://genfarmer.com/package/genfarmer-instagram-trust/)
