# Antigravity review notes

> [!NOTE]
> **Canonical Location:** These review notes have been organized into the central documentation directory at [`docs/ANTIGRAVITY_REVIEW_NOTES.md`](docs/ANTIGRAVITY_REVIEW_NOTES.md).
> This root copy is retained for backwards compatibility.

Please address these before marking the clone APK install work complete:

1. **Do not update an installed app implicitly.** `package_scanner.install_apk_on_device()` currently runs `adb install -r`. The `-r` option permits replacing/reinstalling an existing package. Use plain `adb install` by default, or add an explicit update choice with a clear confirmation. Do not uninstall existing app data automatically. Add a test for an APK whose package is already installed. This is still outstanding in the current code review.

2. **Avoid ambiguous target selection.** The first release targets one connected phone, so primary-device auto-detection is acceptable when exactly one device is online. If multiple devices are online, require an explicit serial instead of silently choosing the first. Test zero, one, and multiple connected devices.

3. **Verify package routing after install.** An ADB install success should trigger a rescan on that same device, but the operator must select/confirm the exact detected package before assigning it to an account. Do not treat an arbitrary uploaded APK as an Instagram clone based only on the filename.

4. **Add install-flow tests before calling the feature complete.** `tests/test_full_suite.py` currently has no `install_apk_on_device` or `/api/clones/install-apk` coverage. Mock ADB and cover APK validation/temporary-file cleanup, success and rescan on the same device, installation errors, and the zero/one/multiple-device cases above.

5. **Clean up the patch before handoff.** `git diff --check` reports trailing whitespace in `PROJECT_MEMORY.md` at the newly added `Context & Problem` line. Remove it so the final diff check is clean.

6. **Make the APK API response contract match its tests.** The route currently returns `{status, result, candidates}`, while the new endpoint test reads top-level `success`, `package_name`, and `device_serial`. Pick one response shape and update both route/UI/tests consistently; otherwise the test will fail with a missing key.

Relevant files: `package_scanner.py`, `app.py`, and `templates/index.html`.
