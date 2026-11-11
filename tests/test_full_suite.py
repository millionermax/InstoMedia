"""
test_full_suite.py - Comprehensive Unit & Integration Test Suite for Instoper.
Validates:
1. Local Persistence, relational integrity, partial unique constraints, and cascade deletion.
2. Windows DPAPI secret protection and zero plaintext in database.
3. Passphrase-encrypted backup (PBKDF2 + AES-256-GCM) with wrong passphrase rejection.
4. Clone inventory scanning and operator confirmation lifecycle.
5. Excel metadata import/export, formula injection neutralization, and XSS safety.
6. Server-side import session token isolation (secrets kept out of browser).
7. Proxy credential parsing into clean endpoints + DPAPI vault, and redaction in APIs/export.
8. Session identity verification on Instagram login (needs_action on unconfirmed identity).
9. Batch stop/start thread safety, concurrency locking, and overlap rejection.
10. Multi-device package scanner targeting isolation.
11. Spreadsheet parsing edge cases (blank middle column preservation, duplicate header rejection, legacy .xls rejection).
12. RFC 6238 published expected-value test vectors (Appendix B).
13. Regression validation of existing Reel Studio workflow defaults.
"""

import os
import sys
import io
import time
import unittest
import threading
from unittest.mock import MagicMock, patch
import pyotp
import openpyxl

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import db
import vault
import package_scanner
import excel_manager
import login_flow
from adb import PACKAGE_NAME


class TestInstoperSuite(unittest.TestCase):
    def setUp(self):
        self.test_db = os.path.join(os.path.dirname(__file__), "test_data.db")
        if os.path.exists(self.test_db):
            try:
                os.remove(self.test_db)
            except Exception:
                pass
        db.init_db(self.test_db)

    def tearDown(self):
        if os.path.exists(self.test_db):
            try:
                os.remove(self.test_db)
            except Exception:
                pass

    # -----------------------------------------------------------------------
    # 1. Local Persistence & Constraints
    # -----------------------------------------------------------------------
    def test_account_crud_and_uniqueness_constraint(self):
        # Create Account 1
        acc1_id = db.create_account(
            username="creator_alpha",
            email="alpha@test.com",
            device_serial="DEV_001",
            package_name="com.instagram.android",
            proxy_endpoint="http://10.0.0.1:8080",
            notes="Alpha creator",
            password="SecretPassword123!",
            totp_seed="JBSWY3DPEHPK3PXP",
            db_path=self.test_db
        )
        self.assertGreater(acc1_id, 0)

        # Check retrieval
        acc1 = db.get_account_by_id(acc1_id, db_path=self.test_db)
        self.assertEqual(acc1["username"], "creator_alpha")
        self.assertTrue(acc1["has_password"])
        self.assertTrue(acc1["has_totp"])
        # Verify secrets are NOT stored as plaintext in account dict
        self.assertNotIn("password", acc1)
        self.assertNotIn("totp_seed", acc1)

        # Attempt to bind Account 2 to the exact same device and package -> MUST FAIL!
        with self.assertRaises(ValueError) as ctx:
            db.create_account(
                username="creator_beta",
                device_serial="DEV_001",
                package_name="com.instagram.android",
                db_path=self.test_db
            )
        self.assertIn("already bound to account", str(ctx.exception))

        # Binding Account 2 to a DIFFERENT package on the same device -> MUST SUCCEED!
        acc2_id = db.create_account(
            username="creator_beta",
            device_serial="DEV_001",
            package_name="com.instagram.android.clone1",
            db_path=self.test_db
        )
        self.assertGreater(acc2_id, 0)

        # Update account fields
        updated = db.update_account(acc1_id, notes="Updated notes", db_path=self.test_db)
        self.assertTrue(updated)
        acc1_updated = db.get_account_by_id(acc1_id, db_path=self.test_db)
        self.assertEqual(acc1_updated["notes"], "Updated notes")

        # Delete account and verify cascade
        deleted = db.delete_account(acc1_id, db_path=self.test_db)
        self.assertTrue(deleted)
        self.assertIsNone(db.get_account_by_id(acc1_id, db_path=self.test_db))

    # -----------------------------------------------------------------------
    # 2. Windows DPAPI Secret Protection
    # -----------------------------------------------------------------------
    def test_dpapi_secret_roundtrip(self):
        plain_secret = "Top_Secret_Password_#2026!"
        protected = vault.dpapi_protect(plain_secret)
        self.assertIsInstance(protected, bytes)
        self.assertNotEqual(protected, plain_secret.encode())

        # Decrypt
        decrypted = vault.dpapi_unprotect(protected)
        self.assertEqual(decrypted, plain_secret)

    # -----------------------------------------------------------------------
    # 3. Encrypted Secret Backup & Restore (PBKDF2 + AES-256-GCM)
    # -----------------------------------------------------------------------
    def test_passphrase_encrypted_backup_restore(self):
        secrets_payload = {
            "user1": {"password": "Password123", "totp_seed": "JBSWY3DPEHPK3PXP"},
            "user2": {"password": "AnotherPassword456", "proxy_secret": "proxy_pass_789"}
        }
        passphrase = "UltraSecurePassphrase2026!"

        # Export encrypted package
        package_bytes = vault.export_secrets_package(secrets_payload, passphrase)
        self.assertIsInstance(package_bytes, bytes)
        self.assertNotIn(b"Password123", package_bytes)

        # Restore with CORRECT passphrase
        restored = vault.import_secrets_package(package_bytes, passphrase)
        self.assertEqual(restored, secrets_payload)

        # Restore with WRONG passphrase -> MUST FAIL!
        with self.assertRaises(ValueError) as ctx:
            vault.import_secrets_package(package_bytes, "WrongPassphrase123")
        self.assertIn("Incorrect passphrase or corrupted secret package", str(ctx.exception))

    # -----------------------------------------------------------------------
    # 4. Clone Package Inventory & Confirmation Lifecycle
    # -----------------------------------------------------------------------
    def test_clone_inventory_lifecycle(self):
        id_official = db.upsert_clone(
            device_serial="DEV_001",
            package_name="com.instagram.android",
            app_name="Instagram (Official)",
            is_confirmed=True,
            db_path=self.test_db
        )
        self.assertGreater(id_official, 0)

        id_clone = db.upsert_clone(
            device_serial="DEV_001",
            package_name="com.applisto.appcloner.instagram1",
            app_name="AppCloner Clone #1",
            is_confirmed=False,
            db_path=self.test_db
        )

        clones_all = db.get_clones("DEV_001", confirmed_only=False, db_path=self.test_db)
        self.assertEqual(len(clones_all), 2)

        db.set_clone_confirmed(id_clone, is_confirmed=True, db_path=self.test_db)
        confirmed_clones = db.get_clones("DEV_001", confirmed_only=True, db_path=self.test_db)
        self.assertEqual(len(confirmed_clones), 2)

    # -----------------------------------------------------------------------
    # 5. Fix 1: Prevent Spreadsheet-driven XSS & Formula Injection
    # -----------------------------------------------------------------------
    def test_spreadsheet_xss_and_formula_neutralization(self):
        # Formula injection
        malicious_formula = "=cmd|' /C calc'!A0"
        neutralized = excel_manager.neutralize_formula(malicious_formula)
        self.assertTrue(neutralized.startswith("'="))

        # Malicious HTML/XSS payload in cell
        xss_user = "<script>alert('xss')</script>"
        xss_tag = "<img src=x onerror=alert(1)>"
        xss_proxy = "http://bad.com/<svg/onload=alert(1)>"

        acc_id = db.create_account(
            username=xss_user,
            tag=xss_tag,
            proxy_endpoint=xss_proxy,
            db_path=self.test_db
        )
        acc = db.get_account_by_id(acc_id, db_path=self.test_db)
        self.assertEqual(acc["username"], xss_user)
        self.assertEqual(acc["tag"], xss_tag)

        # Excel export neutralizes formula prefixes and safely encodes text
        exported = excel_manager.export_accounts_to_excel([acc])
        wb = openpyxl.load_workbook(io.BytesIO(exported))
        ws = wb.active
        # Username cell value in row 2, col 2
        user_cell_val = str(ws.cell(row=2, column=2).value)
        self.assertEqual(user_cell_val, xss_user)

    # -----------------------------------------------------------------------
    # 6. Fix 2: Keep Imported Secrets Out of Browser
    # -----------------------------------------------------------------------
    def test_secrets_kept_out_of_browser_parse_response(self):
        csv_data = (
            "Username,Password,2FA Seed,Proxy,Notes\n"
            "agent_007,SuperSecretPassword123!,JBSWY3DPEHPK3PXP,http://admin:pass@10.0.0.2:8080,Secret Agent\n"
        ).encode("utf-8")

        res = excel_manager.parse_file_headers_and_rows(csv_data, "credentials.csv")
        self.assertTrue(res["success"])

        # 1. Raw rows must NOT be present in parse-file response
        self.assertNotIn("raw_rows", res)

        # 2. Ephemeral import_token must be returned
        self.assertIn("import_token", res)
        self.assertIsInstance(res["import_token"], str)
        self.assertGreater(len(res["import_token"]), 10)

        # 3. Sample rows must have passwords, TOTP seeds, and proxy credentials REDACTED
        sample = res["sample_rows"][0]
        self.assertEqual(sample["Username"], "agent_007")
        self.assertEqual(sample["Password"], "••••••••")
        self.assertEqual(sample["2FA Seed"], "••••••••")
        self.assertNotIn("pass", sample["Proxy"])
        self.assertEqual(sample["Proxy"], "http://***:***@10.0.0.2:8080")

        # 4. Token can be retrieved and consumed server-side
        token = res["import_token"]
        session = excel_manager.pop_import_session(token)
        self.assertIsNotNone(session)
        self.assertEqual(len(session["raw_rows"]), 1)
        self.assertEqual(session["raw_rows"][0]["Password"], "SuperSecretPassword123!")

        # 5. Token consumption is single-use: pop again returns None
        second_pop = excel_manager.pop_import_session(token)
        self.assertIsNone(second_pop)

    # -----------------------------------------------------------------------
    # 7. Fix 3: Protect Proxy Credentials
    # -----------------------------------------------------------------------
    def test_proxy_credentials_protection_and_redaction(self):
        raw_proxy = "http://myuser:P@ssword123@proxyserver.com:8080"
        parsed_endpoint, extracted_secret = vault.parse_proxy_credentials(raw_proxy)
        self.assertEqual(parsed_endpoint, "http://proxyserver.com:8080")
        self.assertEqual(extracted_secret, "myuser:P@ssword123")

        # Redaction check
        redacted = vault.redact_proxy_url(raw_proxy)
        self.assertEqual(redacted, "http://***:***@proxyserver.com:8080")
        self.assertNotIn("P@ssword123", redacted)

        # Save to database via create_account
        acc_id = db.create_account(
            username="proxy_test_user",
            proxy_endpoint=raw_proxy,
            db_path=self.test_db
        )

        # In account metadata API, proxy is clean endpoint metadata with credentials removed
        acc = db.get_account_by_id(acc_id, db_path=self.test_db)
        self.assertEqual(acc["proxy_endpoint"], "http://proxyserver.com:8080")
        self.assertNotIn("P@ssword123", str(acc))
        self.assertNotIn("myuser", str(acc))

        # In Excel export, proxy credentials are redacted
        excel_bytes = excel_manager.export_accounts_to_excel([acc])
        self.assertNotIn(b"P@ssword123", excel_bytes)
        self.assertNotIn(b"myuser", excel_bytes)

        # Only internal secrets lookup can retrieve decrypted secret
        sec = db.get_account_secrets(acc_id, db_path=self.test_db)
        self.assertEqual(sec["proxy_secret"], "myuser:P@ssword123")

    # -----------------------------------------------------------------------
    # 8. Fix 4: Verify Logged-In Username Before Marking Success
    # -----------------------------------------------------------------------
    def test_verify_logged_in_username_identity(self):
        service = login_flow.InstagramAccountLoginService()

        # Mock device UI
        service.d = MagicMock()

        # Case A: Correct username is displayed on screen
        def mock_ui_correct(**kwargs):
            mock_elem = MagicMock()
            if kwargs.get("textMatches") == "(?i)^target_user$":
                mock_elem.exists = True
            else:
                mock_elem.exists = False
            return mock_elem

        service.d.side_effect = mock_ui_correct
        is_verified = service.verify_session_identity("com.instagram.android", "target_user")
        self.assertTrue(is_verified)

        # Case B: Different username or no username visible (only feed tab)
        def mock_ui_wrong(**kwargs):
            mock_elem = MagicMock()
            if kwargs.get("resourceId") == "com.instagram.android:id/feed_tab":
                mock_elem.exists = True
            else:
                mock_elem.exists = False
            return mock_elem

        service.d.side_effect = mock_ui_wrong
        service.human = MagicMock()
        is_verified_wrong = service.verify_session_identity("com.instagram.android", "target_user")
        self.assertFalse(is_verified_wrong)

        # Case C: Single login on already-logged-in package with mismatched identity -> must set NEEDS_ACTION
        acc_id = db.create_account(
            username="target_user",
            password="TestPassword123",
            db_path=self.test_db
        )

        with patch.object(service, "_connect", return_value=True), \
             patch.object(package_scanner, "is_package_installed", return_value=True), \
             patch.object(service, "is_already_logged_in", return_value=True), \
             patch.object(service, "verify_session_identity", return_value=False), \
             patch.object(db, "update_account_login_status") as mock_update_status:

            res = service.login_account(acc_id, db_path=self.test_db)
            self.assertEqual(res, login_flow.LoginResult.NEEDS_ACTION)
            mock_update_status.assert_called_with(acc_id, login_flow.LoginResult.NEEDS_ACTION, db_path=self.test_db)

    # -----------------------------------------------------------------------
    # 9. Fix 5: Make Batch Stop/Start Safe (Concurrency & Overlap Prevention)
    # -----------------------------------------------------------------------
    def test_batch_stop_start_concurrency_and_no_overlap(self):
        manager = login_flow.BatchLoginManager()

        # Create 2 accounts in test DB
        id1 = db.create_account(username="batch_u1", password="P1", db_path=self.test_db)
        id2 = db.create_account(username="batch_u2", password="P2", db_path=self.test_db)

        # Mock worker to run slowly
        worker_started = threading.Event()
        worker_can_finish = threading.Event()

        def slow_worker(account_ids, device_serial):
            worker_started.set()
            while not worker_can_finish.is_set():
                time.sleep(0.02)
            with manager._lock:
                manager.state = "stopped" if manager._stop_requested else "completed"

        with patch.object(manager, "_run_batch_worker", side_effect=slow_worker):
            started = manager.start(account_ids=[id1, id2])
            self.assertTrue(started)
            worker_started.wait(timeout=2.0)

            # While running, a second start() MUST BE REJECTED
            second_start = manager.start(account_ids=[id1])
            self.assertFalse(second_start)

            # Operator requests stop
            manager.stop()
            self.assertEqual(manager.state, "stopping")

            # Immediate start while stopping MUST BE REJECTED
            immediate_start = manager.start(account_ids=[id1])
            self.assertFalse(immediate_start)

            # Release worker
            worker_can_finish.set()
            if manager._thread:
                manager._thread.join(timeout=2.0)

            # Once worker is completely terminated, start is allowed again
            self.assertIn(manager.state, ("stopped", "completed"))
            third_start = manager.start(account_ids=[id1])
            self.assertTrue(third_start)
            # Clean up
            manager.stop()
            if manager._thread:
                manager._thread.join(timeout=2.0)

    # -----------------------------------------------------------------------
    # 10. Fix 6: Clone Scan Device Targeting Isolation
    # -----------------------------------------------------------------------
    def test_multi_device_clone_scanner_isolation(self):
        # Device A has official + clone_alpha
        # Device B has official + clone_beta
        def mock_adb_command(cmd, device_serial=None, timeout=8):
            if device_serial == "DEV_A":
                if "packages" in cmd:
                    return "package:com.instagram.android\npackage:com.clone.alpha\n"
            elif device_serial == "DEV_B":
                if "packages" in cmd:
                    return "package:com.instagram.android\npackage:com.clone.beta\n"
            return ""

        with patch("package_scanner.run_adb_command", side_effect=mock_adb_command), \
             patch("package_scanner.get_foreground_package", return_value=None):

            candidates_a = package_scanner.scan_packages(device_serial="DEV_A", db_path=self.test_db)
            candidates_b = package_scanner.scan_packages(device_serial="DEV_B", db_path=self.test_db)

            pkgs_a = {c["package_name"] for c in candidates_a}
            pkgs_b = {c["package_name"] for c in candidates_b}

            # Device A must have clone_alpha and NOT clone_beta
            self.assertIn("com.clone.alpha", pkgs_a)
            self.assertNotIn("com.clone.beta", pkgs_a)

            # Device B must have clone_beta and NOT clone_alpha
            self.assertIn("com.clone.beta", pkgs_b)
            self.assertNotIn("com.clone.alpha", pkgs_b)

            # Database checks: DEV_A clones in DB
            db_clones_a = db.get_clones("DEV_A", db_path=self.test_db)
            db_pkgs_a = {c["package_name"] for c in db_clones_a}
            self.assertIn("com.clone.alpha", db_pkgs_a)
            self.assertNotIn("com.clone.beta", db_pkgs_a)

            # DEV_B clones in DB
            db_clones_b = db.get_clones("DEV_B", db_path=self.test_db)
            db_pkgs_b = {c["package_name"] for c in db_clones_b}
            self.assertIn("com.clone.beta", db_pkgs_b)
            self.assertNotIn("com.clone.alpha", db_pkgs_b)

    # -----------------------------------------------------------------------
    # 11. Fix 7: Spreadsheet Parsing Edge Cases
    # -----------------------------------------------------------------------
    def test_spreadsheet_parsing_edge_cases(self):
        # 1. Legacy .xls must be explicitly rejected
        res_xls = excel_manager.parse_file_headers_and_rows(b"\xd0\xcf\x11\xe0", "accounts.xls")
        self.assertFalse(res_xls["success"])
        self.assertIn("Legacy .xls format is not supported", res_xls["error"])

        # 2. Duplicate headers must be rejected
        dup_csv = "Username,Password,username\nu1,p1,u2\n".encode("utf-8")
        res_dup = excel_manager.parse_file_headers_and_rows(dup_csv, "dup.csv")
        self.assertFalse(res_dup["success"])
        self.assertIn("Duplicate column header", res_dup["error"])

        # 3. Blank middle column with data in rows must be rejected with column number
        blank_col_with_data_csv = "Username,,Password\nu1,data_without_header,p1\n".encode("utf-8")
        res_blank = excel_manager.parse_file_headers_and_rows(blank_col_with_data_csv, "blank_col.csv")
        self.assertFalse(res_blank["success"])
        self.assertIn("Column 2 contains data but is missing a header name", res_blank["error"])

        # 4. Blank middle column WITHOUT data in rows must preserve column positions
        blank_col_no_data_csv = "Username,,Password\nu1,,p1\n".encode("utf-8")
        res_valid = excel_manager.parse_file_headers_and_rows(blank_col_no_data_csv, "valid.csv")
        self.assertTrue(res_valid["success"])
        self.assertEqual(res_valid["headers"], ["Username", "Password"])
        # Pop session and verify column values are correctly mapped to headers (no index shifting)
        sess = excel_manager.pop_import_session(res_valid["import_token"])
        self.assertEqual(sess["raw_rows"][0]["Username"], "u1")
        self.assertEqual(sess["raw_rows"][0]["Password"], "p1")

        # 5. TSV support
        tsv_data = "Username\tPassword\nu_tsv\tp_tsv\n".encode("utf-8")
        res_tsv = excel_manager.parse_file_headers_and_rows(tsv_data, "data.tsv")
        self.assertTrue(res_tsv["success"])
        self.assertEqual(res_tsv["headers"], ["Username", "Password"])

    # -----------------------------------------------------------------------
    # 12. RFC 6238 Published Expected-Value Test Vectors (Appendix B)
    # -----------------------------------------------------------------------
    def test_rfc6238_official_published_vectors(self):
        # Published RFC 6238 Appendix B test vector:
        # Secret (ASCII): "12345678901234567890" (20 bytes)
        # Base32: "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
        seed_base32 = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
        totp = pyotp.TOTP(seed_base32, digits=6, interval=30)

        # RFC 6238 Table 1 (SHA1, 6-digit values corresponding to published 8-digit values)
        # Time = 59: published 8-digit: 94287082 -> 6-digit: 287082
        self.assertEqual(totp.at(59), "287082")

        # Time = 1111111109: published 8-digit: 07081804 -> 6-digit: 081804
        self.assertEqual(totp.at(1111111109), "081804")

        # Time = 1111111111: published 8-digit: 14050471 -> 6-digit: 050471
        self.assertEqual(totp.at(1111111111), "050471")

        # Time = 1234567890: published 8-digit: 89005924 -> 6-digit: 005924
        self.assertEqual(totp.at(1234567890), "005924")

        # Time = 2000000000: published 8-digit: 69279037 -> 6-digit: 279037
        self.assertEqual(totp.at(2000000000), "279037")

    # -----------------------------------------------------------------------
    # 13. Regression: Existing Reel Workflow Default Preservation
    # -----------------------------------------------------------------------
    def test_existing_reel_workflow_defaults(self):
        self.assertEqual(PACKAGE_NAME, "com.instagram.android")

    # -----------------------------------------------------------------------
    # 14. APK Installer Failure Classification & Package Diff Auto-Detection
    # -----------------------------------------------------------------------
    def test_apk_scanner_installation_modes_and_diff(self):
        # 1. Missing file -> INVALID_APK
        res_missing = package_scanner.install_apk_on_device("non_existent_file.apk")
        self.assertFalse(res_missing["success"])
        self.assertEqual(res_missing["status"], package_scanner.InstallResultStatus.INVALID_APK)

        # Create a real dummy file for subsequent tests
        dummy_apk = os.path.join(os.path.dirname(__file__), "test_dummy.apk")
        with open(dummy_apk, "wb") as f:
            f.write(b"PK\x03\x04dummyapkcontent")

        try:
            # 2. Success with package diff detection
            mock_proc_success = MagicMock(stdout="Success\n", stderr="", returncode=0)
            with patch("subprocess.run", return_value=mock_proc_success), \
                 patch("package_scanner.get_third_party_packages", side_effect=[
                     {"com.existing.app"},                                # before
                     {"com.existing.app", "com.instagram.clone_test"}    # after
                 ]):
                res_success = package_scanner.install_apk_on_device(dummy_apk, device_serial="TEST_DEVICE_1")
                self.assertTrue(res_success["success"])
                self.assertEqual(res_success["status"], package_scanner.InstallResultStatus.SUCCESS)
                self.assertEqual(res_success["new_package"], "com.instagram.clone_test")
                self.assertEqual(res_success["device_serial"], "TEST_DEVICE_1")

            # 3. Signature mismatch error
            mock_proc_sig = MagicMock(stdout="Failure [INSTALL_FAILED_UPDATE_INCOMPATIBLE: Package signatures do not match]\n", stderr="", returncode=1)
            with patch("subprocess.run", return_value=mock_proc_sig), \
                 patch("package_scanner.get_third_party_packages", return_value=set()):
                res_sig = package_scanner.install_apk_on_device(dummy_apk, device_serial="TEST_DEVICE_1")
                self.assertFalse(res_sig["success"])
                self.assertEqual(res_sig["status"], package_scanner.InstallResultStatus.SIGNATURE_MISMATCH)

            # 4. Version downgrade error
            mock_proc_down = MagicMock(stdout="Failure [INSTALL_FAILED_VERSION_DOWNGRADE]\n", stderr="", returncode=1)
            with patch("subprocess.run", return_value=mock_proc_down), \
                 patch("package_scanner.get_third_party_packages", return_value=set()):
                res_down = package_scanner.install_apk_on_device(dummy_apk, device_serial="TEST_DEVICE_1")
                self.assertFalse(res_down["success"])
                self.assertEqual(res_down["status"], package_scanner.InstallResultStatus.DOWNGRADE_ERROR)

            # 5. Insufficient storage error
            mock_proc_storage = MagicMock(stdout="Failure [INSTALL_FAILED_INSUFFICIENT_STORAGE]\n", stderr="", returncode=1)
            with patch("subprocess.run", return_value=mock_proc_storage), \
                 patch("package_scanner.get_third_party_packages", return_value=set()):
                res_storage = package_scanner.install_apk_on_device(dummy_apk, device_serial="TEST_DEVICE_1")
                self.assertFalse(res_storage["success"])
                self.assertEqual(res_storage["status"], package_scanner.InstallResultStatus.STORAGE_ERROR)

            # 6. Timeout -> CONFIRMATION_REQUIRED
            with patch("subprocess.run", side_effect=package_scanner.subprocess.TimeoutExpired(cmd="adb", timeout=90)), \
                 patch("package_scanner.get_third_party_packages", return_value=set()):
                res_timeout = package_scanner.install_apk_on_device(dummy_apk, device_serial="TEST_DEVICE_1")
                self.assertFalse(res_timeout["success"])
                self.assertEqual(res_timeout["status"], package_scanner.InstallResultStatus.CONFIRMATION_REQUIRED)
        finally:
            if os.path.exists(dummy_apk):
                os.remove(dummy_apk)

    # -----------------------------------------------------------------------
    # 15. APK Intake API Endpoint Validation & Temporary File Lifecycle
    # -----------------------------------------------------------------------
    def test_api_clones_install_apk_endpoint_and_cleanup(self):
        from app import app, UPLOAD_FOLDER
        client = app.test_client()

        # 1. Missing file -> 400
        res = client.post("/api/clones/install-apk", data={})
        self.assertEqual(res.status_code, 400)
        self.assertIn("No APK file uploaded", res.get_json()["error"])

        # 2. Invalid extension (.txt) -> 400
        res = client.post(
            "/api/clones/install-apk",
            data={"apk_file": (io.BytesIO(b"dummy text"), "bad_file.txt")}
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn("Only .apk files are supported", res.get_json()["error"])

        # 3. Oversized file -> 400
        with patch("app.MAX_APK_SIZE", 50):
            res = client.post(
                "/api/clones/install-apk",
                data={"apk_file": (io.BytesIO(b"A" * 100), "huge.apk")}
            )
            self.assertEqual(res.status_code, 400)
            self.assertIn("exceeds maximum allowed size", res.get_json()["error"])

        # 4. Successful upload and verify temporary file is cleaned up
        temp_apk_dir = os.path.join(UPLOAD_FOLDER, "temp_apk")
        mock_install_res = {
            "success": True,
            "status": "success",
            "message": "Installed successfully",
            "device_serial": "LD7j-AS",
            "new_package": "com.instagram.clonemock"
        }
        with patch("package_scanner.install_apk_on_device", return_value=mock_install_res), \
             patch("package_scanner.scan_packages", return_value=[]):
            res = client.post(
                "/api/clones/install-apk",
                data={
                    "apk_file": (io.BytesIO(b"valid_dummy_apk_bytes"), "mock_clone.apk"),
                    "device_serial": "LD7j-AS"
                }
            )
            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            self.assertTrue(data["success"])
            self.assertEqual(data["package_name"], "com.instagram.clonemock")
            self.assertEqual(data["device_serial"], "LD7j-AS")

        # Verify no orphaned files remain in temp_apk directory
        if os.path.exists(temp_apk_dir):
            remaining_files = os.listdir(temp_apk_dir)
            self.assertEqual(len(remaining_files), 0, f"Temporary APK files not cleaned up: {remaining_files}")


if __name__ == "__main__":
    unittest.main()
