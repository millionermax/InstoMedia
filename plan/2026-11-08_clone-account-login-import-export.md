---
mode: plan
task: Clone-aware account management, login, and spreadsheet support
complexity: complex
created_at: 2026-11-08T00:00:00+05:30
validated: true
---

# Clone-aware accounts, login, and spreadsheet support

## Approval

Approved by the user on 2026-11-08. This document is the build handoff plan for Antigravity. No implementation changes are included here.

## Problem Definition

- **Core problem:** The current Reel Studio automates one fixed Instagram package and cannot inventory multiple cloned packages, bind accounts to clones, manage login credentials, or import/export account metadata.
- **Constraints:** Keep the Python/Flask/uiautomator2 stack; first release is local, single-user, Windows, and one connected Android phone. Do not log secrets or put secrets in ordinary Excel. Run one login at a time per phone. Do not bypass CAPTCHA, checkpoint, SMS/email verification, or login approval challenges. Existing Reel workflow must continue to use its current default package unless explicitly configured otherwise.
- **Success criteria:**
  1. Discover installed package candidates and let the operator confirm them.
  2. Prevent two accounts from mapping to the same device/package pair.
  3. Create, edit, and disable accounts; safely bind an account to a device and exact package.
  4. Import/export account metadata, proxy endpoint/reference, and notes through Excel.
  5. Store passwords, TOTP seeds, and proxy authentication secrets in a protected local vault; provide a separate encrypted backup/restore path.
  6. Support single-account and sequential batch login with authenticator-app TOTP. Pause for human action on unsupported challenges.
  7. Pass mocked integration checks and an end-to-end run on a user-authorized test account, while preserving the current Reel workflow.

## Analysis Summary

The project is currently a small Flask monolith with one global connected device and in-memory workflow state. It has no account persistence, clone inventory, login flow, Excel import/export, or test suite. IgMatrix documents the useful core pattern: discover a package, bind an exact package name to an account, and execute accounts sequentially. It also describes a `password:2fa_secret` spreadsheet format, but that places secrets in ordinary Excel; this plan instead keeps secrets in a protected vault and treats spreadsheets as metadata files.

Package scanning can identify candidate apps, but a package name alone cannot prove that an app is an authentic or safe Instagram build. Require operator confirmation before using a candidate. The login workflow supports authenticator-app TOTP based on RFC 6238; other challenges are handed back to the user.

## Execution Plan

1. **Add local persistence and protected secret storage.** Create SQLite tables for accounts, device/package bindings, proxy metadata, notes, and login state. Keep credentials in a Windows-protected store, with no secret values in logs or normal database fields.  
   **Verification:** CRUD checks, uniqueness constraints, restart persistence, and encrypted secret round trip.

2. **Add ADB package discovery.** Read installed package IDs and the current foreground package for a selected device. Present the official Instagram package and possible clone packages as candidates.  
   **Verification:** Parse captured ADB outputs and confirm the inventory against a connected device, including package removal/refresh.

3. **Add clone inventory and account mapping UI/API.** Require operator confirmation of a candidate, show device/package identity, and bind an account to one exact package. Check that the package is still installed before launching.  
   **Verification:** Refresh candidate list, reject duplicate device/package assignments, and handle an uninstalled mapped package.

4. **Add account CRUD.** Manage username, optional email, device, exact package, notes, proxy details/reference, enabled state, and login state. Preserve the current Reel workflow’s default package behavior.  
   **Verification:** Field validation, account/package/device mapping, and regression check of existing Reel workflow behavior.

5. **Add Excel metadata import/export.** Use `openpyxl` for a documented template, import preview, row-level errors, and stable account ID based create/update. Include proxy endpoint/label and notes, but not passwords, TOTP seeds, or proxy-auth passwords. Make formula-like spreadsheet values safe.  
   **Verification:** Round trip, duplicate rows, malformed rows, update/create behavior, and formula-like cell values.

6. **Add encrypted secret backup and restore.** Keep passwords, TOTP seeds, and proxy-auth secrets in the Windows-protected local store. Offer a separate passphrase-encrypted backup/import format; never place secrets in standard `.xlsx`. Explain that Windows-bound secrets may not move automatically to another PC.  
   **Verification:** Successful export/import restore, wrong-passphrase rejection, and confirmation that ordinary Excel export has no secret columns or values.

7. **Implement single-account login with TOTP.** Launch the selected installed package, detect an already-authenticated session, and generate an authenticator code from the protected TOTP seed using RFC 6238. Pause and request manual action for CAPTCHA, SMS/email code, login approval, checkpoint, or other unsupported challenge.  
   **Verification:** Mock screen-state and TOTP test vectors, then one manual device run with a user-authorized test account.

8. **Add sequential batch login.** Run one account at a time on a phone. Track per-account status, allow stop/resume, and use bounded retries only for transient device errors. A failure must not mix account state or credentials with the next account.  
   **Verification:** Ordering, cancellation, resume, failure isolation, transient-error handling, and log inspection for secret leakage.

9. **Integrate the UI and complete end-to-end verification.** Add account management, clone scan/confirmation, import/export, and login batch controls to the existing dashboard. First run mocked integration checks, then the complete flow on a test account.  
   **Verification:** `Excel import → clone detect/confirm → account bind → login → TOTP → status update → export`; also verify the existing Reel workflow still works.

## Alternatives Considered

| Approach | Simplicity | Credential safety | Clone/account accuracy | Decision |
|---|---|---|---|---|
| Account/package metadata in SQLite, secrets in Windows-protected storage, Excel metadata-only | Fits local Flask app; modest dependency additions | Stronger local protection; encrypted backup required for migration | Exact package binding; operator confirms candidates | **Selected** |
| Put password and TOTP seed directly in ordinary Excel, matching IgMatrix’s documented format | Easiest bulk editing | Plaintext file can be copied, synced, or emailed | Same package mapping capability | Rejected as default because of secret exposure |
| Discover clones only by package prefix at run time | Quick for similarly named clones | Does not address credential safety | Ambiguous mapping; harder to guarantee one account per clone | Rejected for account-bound login |
| Build a remote multi-user backend and concurrent device scheduler now | Larger, more infrastructure | Requires a broader server-side secrets design | Supports scale beyond current app | Deferred; outside the first-release assumptions |

## Risks and Mitigations

- **Candidate package may be unrelated or modified:** require operator confirmation and persist exact package/device mapping; show that package names do not verify authenticity.
- **Clone apps may trigger service/account restrictions:** disclose the product risk; do not promise account safety.
- **Login UI changes or security challenges can break automation:** use explicit state checks and pause for a human instead of attempting bypasses.
- **Excel can expose secrets or interpret values as formulas:** omit secrets from ordinary workbooks and neutralize formula-like cells.
- **Windows-protected secrets may not migrate to another PC:** provide a separate passphrase-encrypted backup and explain that local DPAPI protection is user/device bound.
- **Existing global device/workflow state can conflict with a login worker:** serialize device operations and keep first release to one phone.
- **Proxy metadata may be mistaken for proxy automation:** only store/import/export proxy association and credentials; proxy rotation is outside this feature.
- **Existing project contains anti-detection logic:** do not extend it as part of clone/login support; limit this feature to account administration and user-authorized login, with manual handoff for challenges.

## Open Questions and Assumptions

- [x] First release targets Windows local single-user install and one connected Android phone; multi-device concurrency is deferred.
- [x] Standard Excel contains metadata, proxy endpoint/reference, and notes. Passwords, 2FA seeds, and proxy-auth secrets use protected storage and separate encrypted backup.
- [x] Batch login is for accounts the operator owns or is authorized to manage. Non-TOTP challenges require manual handling.
- [ ] Before implementation, Antigravity should inspect the current UI and preserve its existing visual conventions; only add the minimum controls needed for these workflows.

## Research Sources

- [IgMatrix: Start here](https://igmatrix.com/docs/intro)
- [IgMatrix: Account Management](https://igmatrix.com/docs/tutorial-basics/manage-accounts)
- [IgMatrix: Clone App Support](https://www.igmatrix.com/docs/tutorial-basics/clone-apps-support)
- [IgMatrix: Multi-Account Switching](https://www.igmatrix.com/docs/tutorial-basics/multi-account-switching)
- [Microsoft: Handling Passwords](https://learn.microsoft.com/en-us/windows/win32/secbp/handling-passwords)
- [Microsoft: DPAPI ProtectedData](https://learn.microsoft.com/en-us/dotnet/api/system.security.cryptography.protecteddata)
- [RFC 6238: TOTP](https://datatracker.ietf.org/doc/html/rfc6238)
