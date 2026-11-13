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

import json
import os
import shutil
import sys
import tempfile
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

    # -----------------------------------------------------------------------
    # 16. Module 1 Task 1: Legacy Taxonomy Migration Tests
    # -----------------------------------------------------------------------
    def test_taxonomy_migration_preserves_existing_accounts_and_tags(self):
        import sqlite3
        legacy_db = os.path.join(os.path.dirname(__file__), "test_legacy_migration.db")
        if os.path.exists(legacy_db):
            os.remove(legacy_db)

        try:
            conn = sqlite3.connect(legacy_db)
            conn.execute("""
                CREATE TABLE accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    email TEXT,
                    device_serial TEXT,
                    package_name TEXT,
                    proxy_endpoint TEXT,
                    proxy_label TEXT,
                    tag TEXT,
                    notes TEXT,
                    status TEXT DEFAULT 'active',
                    login_status TEXT DEFAULT 'idle',
                    last_login_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
            """)
            conn.execute("""
                INSERT INTO accounts (username, device_serial, package_name, tag, created_at, updated_at)
                VALUES ('legacy_creator', 'DEV_001', 'com.instagram.clone1', 'creator', '2026-01-01 00:00:00', '2026-01-01 00:00:00');
            """)
            conn.commit()
            conn.close()

            db.init_db(legacy_db)

            acc = db.get_account_by_username("legacy_creator", db_path=legacy_db)
            self.assertIsNotNone(acc)
            self.assertEqual(acc["username"], "legacy_creator")
            self.assertEqual(acc["device_serial"], "DEV_001")
            self.assertEqual(acc["package_name"], "com.instagram.clone1")
            self.assertEqual(acc["tag"], "creator")

            taxonomy = db.get_account_taxonomy(acc["id"], db_path=legacy_db)
            self.assertIsNone(taxonomy["category"])
            self.assertEqual([t["name"] for t in taxonomy["tags"]], ["creator"])
        finally:
            if os.path.exists(legacy_db):
                try:
                    os.remove(legacy_db)
                except Exception:
                    pass

    def test_taxonomy_migration_is_idempotent(self):
        import sqlite3
        legacy_db = os.path.join(os.path.dirname(__file__), "test_legacy_idempotent.db")
        if os.path.exists(legacy_db):
            os.remove(legacy_db)

        try:
            conn = sqlite3.connect(legacy_db)
            conn.execute("""
                CREATE TABLE accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    email TEXT,
                    device_serial TEXT,
                    package_name TEXT,
                    proxy_endpoint TEXT,
                    proxy_label TEXT,
                    tag TEXT,
                    notes TEXT,
                    status TEXT DEFAULT 'active',
                    login_status TEXT DEFAULT 'idle',
                    last_login_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
            """)
            conn.execute("""
                INSERT INTO accounts (username, device_serial, package_name, tag, created_at, updated_at)
                VALUES ('idempotent_user', 'DEV_002', 'com.instagram.clone2', 'creator', '2026-01-01 00:00:00', '2026-01-01 00:00:00');
            """)
            conn.commit()
            conn.close()

            db.init_db(legacy_db)
            db.init_db(legacy_db)

            tags = db.list_tags("accounts", db_path=legacy_db)
            self.assertEqual(len(tags), 1)
            self.assertEqual(tags[0]["name"], "creator")

            acc = db.get_account_by_username("idempotent_user", db_path=legacy_db)
            with db.get_connection(legacy_db) as conn:
                rows = conn.execute(
                    "SELECT * FROM account_tags WHERE account_id = ?;",
                    (acc["id"],),
                ).fetchall()
            self.assertEqual(len(rows), 1)
        finally:
            if os.path.exists(legacy_db):
                try:
                    os.remove(legacy_db)
                except Exception:
                    pass

    # -----------------------------------------------------------------------
    # 17. Module 1 Task 2: Scoped Taxonomy Storage & Assignment Safety
    # -----------------------------------------------------------------------
    def test_taxonomy_scopes_and_normalized_uniqueness(self):
        # Invalid scope rejected
        with self.assertRaises(ValueError):
            db.list_categories("invalid_scope", db_path=self.test_db)
        with self.assertRaises(ValueError):
            db.create_tag("reels", "viral", db_path=self.test_db)

        # Scope isolation: accounts vs content
        acc_cat_id = db.create_category("accounts", "Client Tier 1", db_path=self.test_db)
        cnt_cat_id = db.create_category("content", "Meme Clips", db_path=self.test_db)
        acc_tag_id = db.create_tag("accounts", "Warming", db_path=self.test_db)
        cnt_tag_id = db.create_tag("content", "Trending Audio", db_path=self.test_db)

        acc_cats = db.list_categories("accounts", db_path=self.test_db)
        cnt_cats = db.list_categories("content", db_path=self.test_db)
        self.assertEqual([c["id"] for c in acc_cats], [acc_cat_id])
        self.assertEqual([c["id"] for c in cnt_cats], [cnt_cat_id])

        acc_tags = db.list_tags("accounts", db_path=self.test_db)
        cnt_tags = db.list_tags("content", db_path=self.test_db)
        self.assertEqual([t["id"] for t in acc_tags], [acc_tag_id])
        self.assertEqual([t["id"] for t in cnt_tags], [cnt_tag_id])

        # Normalized uniqueness within scope (whitespace + casefold), preserving first spelling
        with self.assertRaises(ValueError):
            db.create_category("accounts", "  client tier 1  ", db_path=self.test_db)
        with self.assertRaises(ValueError):
            db.create_tag("accounts", " WARMING ", db_path=self.test_db)

        # Cross-scope duplicate display names remain valid
        same_name_cnt_cat = db.create_category("content", "Client Tier 1", db_path=self.test_db)
        same_name_cnt_tag = db.create_tag("content", "Warming", db_path=self.test_db)
        self.assertNotEqual(same_name_cnt_cat, acc_cat_id)
        self.assertNotEqual(same_name_cnt_tag, acc_tag_id)

        # Semicolon rejected in tag names; empty names rejected
        with self.assertRaises(ValueError):
            db.create_tag("accounts", "bad;tag", db_path=self.test_db)
        with self.assertRaises(ValueError):
            db.create_category("accounts", "   ", db_path=self.test_db)

        # Display spelling kept from first entry; rename works and checks duplicates
        acc_cat = [c for c in db.list_categories("accounts", db_path=self.test_db) if c["id"] == acc_cat_id][0]
        self.assertEqual(acc_cat["name"], "Client Tier 1")
        self.assertTrue(db.rename_category(acc_cat_id, "VIP Clients", db_path=self.test_db))
        acc_cat2 = [c for c in db.list_categories("accounts", db_path=self.test_db) if c["id"] == acc_cat_id][0]
        self.assertEqual(acc_cat2["name"], "VIP Clients")

    def test_taxonomy_assignment_safety_and_delete_protection(self):
        acc_id = db.create_account(username="tax_user_1", db_path=self.test_db)
        acc_cat = db.create_category("accounts", "Brand", db_path=self.test_db)
        acc_tag1 = db.create_tag("accounts", "US", db_path=self.test_db)
        acc_tag2 = db.create_tag("accounts", "Reels", db_path=self.test_db)

        cnt_cat = db.create_category("content", "Raw Video", db_path=self.test_db)
        cnt_tag = db.create_tag("content", "4K", db_path=self.test_db)

        # Valid assignment succeeds
        db.set_account_taxonomy(acc_id, acc_cat, [acc_tag1, acc_tag2], db_path=self.test_db)
        tax = db.get_account_taxonomy(acc_id, db_path=self.test_db)
        self.assertEqual(tax["category"]["id"], acc_cat)
        self.assertEqual({t["id"] for t in tax["tags"]}, {acc_tag1, acc_tag2})

        # Cross-scope or missing IDs rejected atomically without mutating existing assignments
        with self.assertRaises(ValueError):
            db.set_account_taxonomy(acc_id, cnt_cat, [acc_tag1], db_path=self.test_db)
        with self.assertRaises(ValueError):
            db.set_account_taxonomy(acc_id, None, [acc_tag1, cnt_tag], db_path=self.test_db)
        with self.assertRaises(ValueError):
            db.set_account_taxonomy(acc_id, 99999, [], db_path=self.test_db)
        with self.assertRaises(ValueError):
            db.set_account_taxonomy(acc_id, None, [99999], db_path=self.test_db)

        # Verify previous assignments stayed intact after failed attempts
        tax_after_fail = db.get_account_taxonomy(acc_id, db_path=self.test_db)
        self.assertEqual(tax_after_fail["category"]["id"], acc_cat)
        self.assertEqual({t["id"] for t in tax_after_fail["tags"]}, {acc_tag1, acc_tag2})

        # Deleting assigned category or tag fails without losing account assignment
        with self.assertRaises(ValueError):
            db.delete_category(acc_cat, db_path=self.test_db)
        with self.assertRaises(ValueError):
            db.delete_tag(acc_tag1, db_path=self.test_db)

        tax_after_del_fail = db.get_account_taxonomy(acc_id, db_path=self.test_db)
        self.assertEqual(tax_after_del_fail["category"]["id"], acc_cat)
        self.assertEqual({t["id"] for t in tax_after_del_fail["tags"]}, {acc_tag1, acc_tag2})

        # Unassigning then deleting succeeds
        db.set_account_taxonomy(acc_id, None, [acc_tag2], db_path=self.test_db)
        self.assertTrue(db.delete_category(acc_cat, db_path=self.test_db))
        self.assertTrue(db.delete_tag(acc_tag1, db_path=self.test_db))

    # -----------------------------------------------------------------------
    # 18. Module 1 Task 3: Taxonomy HTTP API & Account Serialization Tests
    # -----------------------------------------------------------------------
    def test_api_taxonomy_crud_and_scope_validation(self):
        from app import app
        client = app.test_client()

        with patch("db.DEFAULT_DB_PATH", self.test_db):
            # Missing or invalid scope -> 400
            self.assertEqual(client.get("/api/categories").status_code, 400)
            self.assertEqual(client.get("/api/categories?scope=invalid").status_code, 400)
            self.assertEqual(client.get("/api/tags").status_code, 400)
            self.assertEqual(client.get("/api/tags?scope=other").status_code, 400)

            # Create in accounts & content scopes
            r1 = client.post("/api/categories", json={"scope": "accounts", "name": "Agency"})
            self.assertEqual(r1.status_code, 201)
            cat_acc_id = r1.get_json()["category"]["id"]

            r2 = client.post("/api/categories", json={"scope": "content", "name": "B-Roll"})
            self.assertEqual(r2.status_code, 201)
            cat_cnt_id = r2.get_json()["category"]["id"]

            # Valid scopes return only their own records
            acc_cats = client.get("/api/categories?scope=accounts").get_json()["categories"]
            cnt_cats = client.get("/api/categories?scope=content").get_json()["categories"]
            self.assertEqual([c["id"] for c in acc_cats], [cat_acc_id])
            self.assertEqual([c["id"] for c in cnt_cats], [cat_cnt_id])

            # Empty name -> 400; normalized duplicate -> 409
            self.assertEqual(client.post("/api/categories", json={"scope": "accounts", "name": "  "}).status_code, 400)
            self.assertEqual(client.post("/api/categories", json={"scope": "accounts", "name": " agency "}).status_code, 409)

            # Tag create, duplicate (409), semicolon (400), rename, and delete
            t1 = client.post("/api/tags", json={"scope": "accounts", "name": "Warmup"})
            self.assertEqual(t1.status_code, 201)
            tag_acc_id = t1.get_json()["tag"]["id"]
            self.assertEqual(client.post("/api/tags", json={"scope": "accounts", "name": "WARMUP"}).status_code, 409)
            self.assertEqual(client.post("/api/tags", json={"scope": "accounts", "name": "a;b"}).status_code, 400)

            # Rename valid + duplicate rename -> 409
            t2 = client.post("/api/tags", json={"scope": "accounts", "name": "Priority"})
            self.assertEqual(t2.status_code, 201)
            tag2_id = t2.get_json()["tag"]["id"]
            self.assertEqual(client.put(f"/api/tags/{tag_acc_id}", json={"name": " warmup-v2 "}).status_code, 200)
            self.assertEqual(client.put(f"/api/tags/{tag_acc_id}", json={"name": "priority"}).status_code, 409)

            # Assign to account -> delete assigned must return 409
            acc_res = client.post(
                "/api/accounts",
                json={"username": "api_tax_user", "category_id": cat_acc_id, "tag_ids": [tag_acc_id]},
            )
            self.assertEqual(acc_res.status_code, 201)
            self.assertEqual(client.delete(f"/api/categories/{cat_acc_id}").status_code, 409)
            self.assertEqual(client.delete(f"/api/tags/{tag_acc_id}").status_code, 409)

            # Deleting unassigned tag works
            self.assertEqual(client.delete(f"/api/tags/{tag2_id}").status_code, 200)

    def test_api_accounts_taxonomy_serialization_and_no_secret_leak(self):
        from app import app
        client = app.test_client()

        with patch("db.DEFAULT_DB_PATH", self.test_db):
            cat_id = db.create_category("accounts", "Main Brand", db_path=self.test_db)
            tag1_id = db.create_tag("accounts", "Reels", db_path=self.test_db)
            tag2_id = db.create_tag("accounts", "Daily", db_path=self.test_db)
            cnt_cat_id = db.create_category("content", "Meme", db_path=self.test_db)
            cnt_tag_id = db.create_tag("content", "Viral", db_path=self.test_db)

            # Content-scoped IDs must be rejected on account create
            bad_create = client.post(
                "/api/accounts",
                json={"username": "wrong_scope_u", "category_id": cnt_cat_id, "tag_ids": [tag1_id]},
            )
            self.assertEqual(bad_create.status_code, 400)

            bad_tag_create = client.post(
                "/api/accounts",
                json={"username": "wrong_scope_u2", "tag_ids": [cnt_tag_id]},
            )
            self.assertEqual(bad_tag_create.status_code, 400)

            # Create account with valid category_id, tag_ids, and secrets
            create_res = client.post(
                "/api/accounts",
                json={
                    "username": "serial_user",
                    "password": "SuperSecretPassword!",
                    "totp_seed": "JBSWY3DPEHPK3PXP",
                    "proxy_endpoint": "http://puser:ppass@10.0.0.5:8080",
                    "category_id": cat_id,
                    "tag_ids": [tag1_id, tag2_id],
                },
            )
            self.assertEqual(create_res.status_code, 201)
            acc_id = create_res.get_json()["account_id"]

            # Get single account & list accounts
            get_json = client.get(f"/api/accounts/{acc_id}").get_json()
            acc = get_json["account"]
            self.assertEqual(acc["category"]["name"], "Main Brand")
            self.assertEqual([t["name"] for t in acc["tags"]], ["Reels", "Daily"])
            self.assertEqual(acc["tag"], "Reels; Daily")

            list_json = client.get("/api/accounts").get_json()
            listed = [a for a in list_json["accounts"] if a["id"] == acc_id][0]
            self.assertEqual(listed["category"]["id"], cat_id)
            self.assertEqual(len(listed["tags"]), 2)

            # Ensure no plaintext secrets leak anywhere in responses
            dump_all = str(get_json) + str(list_json)
            self.assertNotIn("SuperSecretPassword!", dump_all)
            self.assertNotIn("JBSWY3DPEHPK3PXP", dump_all)
            self.assertNotIn("puser", dump_all)
            self.assertNotIn("ppass", dump_all)

            # Update category & tags
            upd_res = client.put(
                f"/api/accounts/{acc_id}",
                json={"category_id": None, "tag_ids": [tag2_id]},
            )
            self.assertEqual(upd_res.status_code, 200)
            after_upd = client.get(f"/api/accounts/{acc_id}").get_json()["account"]
            self.assertIsNone(after_upd["category"])
            self.assertEqual([t["id"] for t in after_upd["tags"]], [tag2_id])
            self.assertEqual(after_upd["tag"], "Daily")

    # -----------------------------------------------------------------------
    # 19. Module 1 Task 4: Spreadsheet Taxonomy Round-Trip & Validation
    # -----------------------------------------------------------------------
    def test_excel_taxonomy_roundtrip_and_tag_parsing(self):
        cat_id = db.create_category("accounts", "Agency", db_path=self.test_db)
        t_alpha = db.create_tag("accounts", "Alpha", db_path=self.test_db)
        t_beta = db.create_tag("accounts", "Beta", db_path=self.test_db)
        t_legacy = db.create_tag("accounts", "LegacySingle", db_path=self.test_db)

        acc_id = db.create_account(
            username="roundtrip_user",
            password="VaultPassword99",
            totp_seed="JBSWY3DPEHPK3PXP",
            category_id=cat_id,
            tag_ids=[t_alpha, t_beta],
            db_path=self.test_db,
        )

        # Export workbook and inspect contents
        xlsx_bytes = excel_manager.export_accounts_to_excel(db_path=self.test_db)
        self.assertNotIn(b"VaultPassword99", xlsx_bytes)
        self.assertNotIn(b"JBSWY3DPEHPK3PXP", xlsx_bytes)

        wb = openpyxl.load_workbook(io.BytesIO(xlsx_bytes))
        ws = wb.active
        headers = [ws.cell(row=1, column=c).value for c in range(1, ws.max_column + 1)]
        self.assertIn("Category", headers)
        self.assertIn("Tags", headers)
        cat_col = headers.index("Category") + 1
        tags_col = headers.index("Tags") + 1
        self.assertEqual(ws.cell(row=2, column=cat_col).value, "Agency")
        self.assertEqual(ws.cell(row=2, column=tags_col).value, "Alpha; Beta")

        # Delete account and round-trip re-import via exported workbook
        db.delete_account(acc_id, db_path=self.test_db)
        parsed = excel_manager.parse_and_validate_excel(xlsx_bytes, db_path=self.test_db)
        self.assertTrue(parsed["success"])
        self.assertEqual(len(parsed["errors"]), 0)
        self.assertEqual(len(parsed["to_create"]), 1)
        res = excel_manager.execute_import(parsed["to_create"], parsed["to_update"], db_path=self.test_db)
        self.assertEqual(res["created"], 1)

        reimported = db.get_account_by_username("roundtrip_user", db_path=self.test_db)
        self.assertEqual(reimported["category"]["name"], "Agency")
        self.assertEqual([t["name"] for t in reimported["tags"]], ["Alpha", "Beta"])

        # Workbook edge cases: duplicates in one cell, empty values, legacy Tag fallback, semicolon in legacy Tag
        wb2 = openpyxl.Workbook()
        ws2 = wb2.active
        ws2.append(["Username", "Category", "Tags", "Tag"])
        ws2.append(["dedup_user", "Agency", " Alpha ; alpha ;  Beta  ; ", ""])
        ws2.append(["empty_tax_user", "", "", ""])
        ws2.append(["legacy_col_user", "", "", "LegacySingle"])
        ws2.append(["bad_semicolon_user", "", "", "bad;tag"])

        buf = io.BytesIO()
        wb2.save(buf)
        parsed2 = excel_manager.parse_and_validate_excel(buf.getvalue(), db_path=self.test_db)
        self.assertTrue(parsed2["success"])
        # bad_semicolon_user rejected with row-level error
        self.assertEqual(len(parsed2["errors"]), 1)
        self.assertIn("semicolon", parsed2["errors"][0].lower())
        self.assertEqual(len(parsed2["to_create"]), 3)

        excel_manager.execute_import(parsed2["to_create"], parsed2["to_update"], db_path=self.test_db)
        u_dedup = db.get_account_by_username("dedup_user", db_path=self.test_db)
        self.assertEqual([t["id"] for t in u_dedup["tags"]], [t_alpha, t_beta])
        u_empty = db.get_account_by_username("empty_tax_user", db_path=self.test_db)
        self.assertIsNone(u_empty["category"])
        self.assertEqual(u_empty["tags"], [])
        u_leg = db.get_account_by_username("legacy_col_user", db_path=self.test_db)
        self.assertEqual([t["id"] for t in u_leg["tags"]], [t_legacy])

    def test_excel_unknown_taxonomy_requires_explicit_create(self):
        # 1. Standard workbook preview & import rejects unknown category/tag by default
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Username", "Category", "Tags"])
        ws.append(["unknown_cat_user", "NonExistentCat", ""])
        ws.append(["unknown_tag_user", "", "BrandNewTag"])
        buf = io.BytesIO()
        wb.save(buf)
        xlsx_bytes = buf.getvalue()

        parsed_strict = excel_manager.parse_and_validate_excel(xlsx_bytes, db_path=self.test_db, create_missing_taxonomy=False)
        self.assertEqual(len(parsed_strict["to_create"]), 0)
        self.assertEqual(len(parsed_strict["errors"]), 2)
        self.assertEqual(db.list_categories("accounts", db_path=self.test_db), [])
        self.assertEqual(db.list_tags("accounts", db_path=self.test_db), [])
        self.assertIsNone(db.get_account_by_username("unknown_cat_user", db_path=self.test_db))

        # 2. Universal column matcher import_accounts_with_mapping rejects unknown taxonomy unless opted in
        csv_bytes = "User,Cat,TagList\ncsv_unk,NewCat,TagX; TagY\n".encode("utf-8")
        parse_res = excel_manager.parse_file_headers_and_rows(csv_bytes, "unk.csv")
        sess = excel_manager.pop_import_session(parse_res["import_token"])
        mapping = {"username": "User", "category": "Cat", "tags": "TagList"}

        strict_res = excel_manager.import_accounts_with_mapping(
            raw_rows=sess["raw_rows"],
            column_mapping=mapping,
            create_missing_taxonomy=False,
            db_path=self.test_db,
        )
        self.assertEqual(strict_res["created"], 0)
        self.assertEqual(len(strict_res["errors"]), 1)
        self.assertIsNone(db.get_account_by_username("csv_unk", db_path=self.test_db))
        self.assertEqual(db.list_categories("accounts", db_path=self.test_db), [])

        # 3. With explicit create_missing_taxonomy=True, creates scoped taxonomy and links account
        opt_in_res = excel_manager.import_accounts_with_mapping(
            raw_rows=sess["raw_rows"],
            column_mapping=mapping,
            create_missing_taxonomy=True,
            db_path=self.test_db,
        )
        self.assertEqual(opt_in_res["created"], 1)
        self.assertEqual(len(opt_in_res["errors"]), 0)
        acc = db.get_account_by_username("csv_unk", db_path=self.test_db)
        self.assertEqual(acc["category"]["name"], "NewCat")
        self.assertEqual([t["name"] for t in acc["tags"]], ["TagX", "TagY"])

    def test_web_shell_and_taxonomy_controls_present(self):
        import app as app_module

        with patch("db.DEFAULT_DB_PATH", self.test_db):
            client = app_module.app.test_client()
            resp = client.get("/")
            self.assertEqual(resp.status_code, 200)
            html = resp.get_data(as_text=True)

            # Desktop sidebar shell + separated views
            for marker in [
                "navTabOverview",
                "navTabReels",
                "navTabAccounts",
                "navTabConsole",
                "navTabContent",
                "navTabAutomations",
                "navTabTasks",
                "overviewView",
                "consoleView",
                "contentView",
                "automationsView",
                "tasksView",
                "tasksRunsList",
                # Preserved Campaigns / Mirror controls
                "audioUrl",
                "dropzone",
                "videoFileInput",
                "captionText",
                "startBtn",
                "mirrorImg",
                "logConsole",
                "com.instagram.android",
                # Taxonomy UI + Column Matcher alignment
                "taxonomyManagerModal",
                "filterCategorySelect",
                "filterTagSelect",
                "formCategoryId",
                "formTagsContainer",
                "quickCategoryInput",
                "quickTagInput",
                "createMissingTaxonomyCheck",
                "/api/accounts/import-mapped",
            ]:
                self.assertIn(marker, html)

            # End-to-end mapped CSV import HTTP contract matches template expectations
            csv_data = (
                "Handle,Cat,LabelList\n"
                "shell_import_user,ThemeA,Warm;Ready\n"
            ).encode("utf-8")
            parse_resp = client.post(
                "/api/accounts/parse-file",
                data={"file": (io.BytesIO(csv_data), "accounts.csv")},
                content_type="multipart/form-data",
            )
            self.assertEqual(parse_resp.status_code, 200)
            pdata = parse_resp.get_json()
            self.assertIn("auto_mapping", pdata)
            self.assertIn("sample_rows", pdata)
            self.assertNotIn("detected_mapping", pdata)

            commit_resp = client.post(
                "/api/accounts/import-mapped",
                json={
                    "import_token": pdata["import_token"],
                    "mapping": {
                        "username": "Handle",
                        "category": "Cat",
                        "tags": "LabelList",
                    },
                    "default_package": "com.instagram.android",
                    "create_missing_taxonomy": True,
                },
            )
            self.assertEqual(commit_resp.status_code, 200)
            cdata = commit_resp.get_json()
            self.assertTrue(cdata["success"])
            self.assertEqual(cdata["created"], 1)

            acc_list = client.get("/api/accounts").get_json()["accounts"]
            imported = next(a for a in acc_list if a["username"] == "shell_import_user")
            self.assertEqual(imported["category"]["name"], "ThemeA")
            self.assertEqual([t["name"] for t in imported["tags"]], ["Warm", "Ready"])

    def test_taxonomy_persistence_across_reinit_and_app_restart(self):
        import app as app_module

        with patch("db.DEFAULT_DB_PATH", self.test_db):
            client = app_module.app.test_client()
            c_acc = client.post("/api/categories", json={"scope": "accounts", "name": "Warming"})
            self.assertEqual(c_acc.status_code, 201)
            cat_id = c_acc.get_json()["category"]["id"]

            t1 = client.post("/api/tags", json={"scope": "accounts", "name": "ReelReady"}).get_json()["tag"]["id"]
            t2 = client.post("/api/tags", json={"scope": "accounts", "name": "TierOne"}).get_json()["tag"]["id"]
            client.post("/api/categories", json={"scope": "content", "name": "Hooks"})

            acc_resp = client.post(
                "/api/accounts",
                json={
                    "username": "persist_user",
                    "category_id": cat_id,
                    "tag_ids": [t1, t2],
                },
            )
            self.assertEqual(acc_resp.status_code, 201)

        # Simulate app/database restart via fresh init_db + fresh client request
        db.init_db(db_path=self.test_db)

        with patch("db.DEFAULT_DB_PATH", self.test_db):
            fresh_client = app_module.app.test_client()
            cats = fresh_client.get("/api/categories?scope=accounts").get_json()["categories"]
            self.assertEqual([c["name"] for c in cats], ["Warming"])
            content_cats = fresh_client.get("/api/categories?scope=content").get_json()["categories"]
            self.assertEqual([c["name"] for c in content_cats], ["Hooks"])
            acc = fresh_client.get("/api/accounts").get_json()["accounts"][0]
            self.assertEqual(acc["category"]["name"], "Warming")
            self.assertEqual([t["name"] for t in acc["tags"]], ["ReelReady", "TierOne"])

    # -----------------------------------------------------------------------
    # Module 2 Task 1: Explicit Connected-Device Inventory
    # -----------------------------------------------------------------------
    def test_list_devices_preserves_adb_status_and_serial(self):
        adb_out = (
            "* daemon started successfully\n"
            "List of devices attached\n"
            "0624531123033570       device product:TECNO_LD7j model:TECNO_POVA device:TECNO-LD7j\n"
            "UNAUTH_9911            unauthorized\n"
            "OFFLINE_2200           offline\n"
        )
        with patch("package_scanner.run_adb_command", return_value=adb_out) as mock_adb:
            devices = package_scanner.list_connected_devices()
            mock_adb.assert_called_once()
            self.assertEqual(len(devices), 3)

            self.assertEqual(devices[0]["serial"], "0624531123033570")
            self.assertEqual(devices[0]["status"], "device")
            self.assertEqual(devices[0]["product_name"], "TECNO POVA")
            self.assertTrue(devices[0]["selectable"])

            self.assertEqual(devices[1]["serial"], "UNAUTH_9911")
            self.assertEqual(devices[1]["status"], "unauthorized")
            self.assertIsNone(devices[1]["product_name"])
            self.assertFalse(devices[1]["selectable"])

            self.assertEqual(devices[2]["serial"], "OFFLINE_2200")
            self.assertEqual(devices[2]["status"], "offline")
            self.assertIsNone(devices[2]["product_name"])
            self.assertFalse(devices[2]["selectable"])

    def test_device_list_endpoint_marks_selectability(self):
        import app as app_module

        with patch("db.DEFAULT_DB_PATH", self.test_db):
            client = app_module.app.test_client()

            with patch("package_scanner.run_adb_command", return_value="List of devices attached\n"):
                empty_resp = client.get("/api/devices")
                self.assertEqual(empty_resp.status_code, 200)
                self.assertEqual(empty_resp.get_json()["devices"], [])

            mixed_out = (
                "List of devices attached\n"
                "DEV_ONLINE\tdevice model:Pixel_8\n"
                "DEV_UNAUTH\tunauthorized\n"
            )
            with patch("package_scanner.run_adb_command", return_value=mixed_out):
                resp = client.get("/api/devices")
                self.assertEqual(resp.status_code, 200)
                devs = resp.get_json()["devices"]
                self.assertEqual(len(devs), 2)
                self.assertTrue(devs[0]["selectable"])
                self.assertEqual(devs[0]["product_name"], "Pixel 8")
                self.assertFalse(devs[1]["selectable"])
                self.assertEqual(devs[1]["status"], "unauthorized")

    # -----------------------------------------------------------------------
    # Module 2 Task 2: Device-Scoped Package Checks
    # -----------------------------------------------------------------------
    def test_require_online_device_and_confirmed_package_validation(self):
        # 1. Missing serial rejected (never falls back to first device)
        with patch("package_scanner.run_adb_command", return_value="List of devices attached\nDEV1\tdevice\n"):
            with self.assertRaises(package_scanner.OnboardingValidationError) as ctx_empty:
                package_scanner.require_online_device("")
            self.assertEqual(ctx_empty.exception.code, "missing_device_serial")

        # 2. Offline / unauthorized / unknown device rejected
        adb_devs = (
            "List of devices attached\n"
            "DEV_OK\tdevice model:Pixel_7\n"
            "DEV_OFF\toffline\n"
            "DEV_UNAUTH\tunauthorized\n"
        )
        with patch("package_scanner.run_adb_command", return_value=adb_devs):
            with self.assertRaises(package_scanner.OnboardingValidationError) as ctx_off:
                package_scanner.require_online_device("DEV_OFF")
            self.assertEqual(ctx_off.exception.code, "device_not_online")

            with self.assertRaises(package_scanner.OnboardingValidationError) as ctx_unauth:
                package_scanner.require_online_device("DEV_UNAUTH")
            self.assertEqual(ctx_unauth.exception.code, "device_not_online")

            with self.assertRaises(package_scanner.OnboardingValidationError) as ctx_missing:
                package_scanner.require_online_device("DEV_UNKNOWN")
            self.assertEqual(ctx_missing.exception.code, "device_not_online")

            ok_dev = package_scanner.require_online_device("DEV_OK")
            self.assertEqual(ok_dev["serial"], "DEV_OK")
            self.assertEqual(ok_dev["status"], "device")

        # 3. Package absent on selected device -> rejected, and ADB checked with exact serial
        adb_calls = []

        def mock_adb(cmd, device_serial=None, timeout=8):
            adb_calls.append((cmd, device_serial))
            if cmd[:1] == ["devices"]:
                return "List of devices attached\nDEV_OK\tdevice model:Pixel_7\nDEV_OTHER\tdevice\n"
            if cmd == ["shell", "pm", "path", "com.clone.absent"]:
                return ""
            if cmd == ["shell", "pm", "path", "com.clone.unconfirmed"] and device_serial == "DEV_OK":
                return "package:/data/app/com.clone.unconfirmed/base.apk\n"
            if cmd == ["shell", "pm", "path", "com.clone.ready"] and device_serial == "DEV_OK":
                return "package:/data/app/com.clone.ready/base.apk\n"
            return ""

        db.upsert_clone("DEV_OK", "com.clone.unconfirmed", app_name="Clone Unconf", is_confirmed=False, db_path=self.test_db)
        db.upsert_clone("DEV_OTHER", "com.clone.ready", app_name="Other Device Clone", is_confirmed=True, db_path=self.test_db)
        db.upsert_clone("DEV_OK", "com.clone.ready", app_name="Clone Ready", is_confirmed=True, db_path=self.test_db)

        with patch("package_scanner.run_adb_command", side_effect=mock_adb):
            with self.assertRaises(package_scanner.OnboardingValidationError) as ctx_abs:
                package_scanner.validate_confirmed_package("DEV_OK", "com.clone.absent", db_path=self.test_db)
            self.assertEqual(ctx_abs.exception.code, "package_not_installed")

            with self.assertRaises(package_scanner.OnboardingValidationError) as ctx_unc:
                package_scanner.validate_confirmed_package("DEV_OK", "com.clone.unconfirmed", db_path=self.test_db)
            self.assertEqual(ctx_unc.exception.code, "package_not_confirmed")

            # Confirmed package succeeds and returns structured details without launching
            validated = package_scanner.validate_confirmed_package("DEV_OK", "com.clone.ready", db_path=self.test_db)
            self.assertTrue(validated["valid"])
            self.assertEqual(validated["device_serial"], "DEV_OK")
            self.assertEqual(validated["package_name"], "com.clone.ready")
            self.assertEqual(validated["app_name"], "Clone Ready")

        # Every package ADB check used DEV_OK explicitly
        pm_calls = [c for c in adb_calls if c[0][:2] == ["shell", "pm"]]
        self.assertTrue(len(pm_calls) >= 3)
        self.assertTrue(all(serial == "DEV_OK" for _, serial in pm_calls))

    # -----------------------------------------------------------------------
    # Module 2 Task 3: Profile Capture Parser & Service
    # -----------------------------------------------------------------------
    def test_profile_metric_count_parser_edge_cases(self):
        import profile_capture

        self.assertEqual(profile_capture.parse_metric_count("123"), 123)
        self.assertEqual(profile_capture.parse_metric_count("0"), 0)
        self.assertEqual(profile_capture.parse_metric_count("1,234"), 1234)
        self.assertEqual(profile_capture.parse_metric_count("1,234,567"), 1234567)
        self.assertEqual(profile_capture.parse_metric_count("1.2K"), 1200)
        self.assertEqual(profile_capture.parse_metric_count("1.2k"), 1200)
        self.assertEqual(profile_capture.parse_metric_count("2M"), 2000000)
        self.assertEqual(profile_capture.parse_metric_count("2.5M"), 2500000)
        self.assertEqual(profile_capture.parse_metric_count("123 posts"), 123)
        self.assertEqual(profile_capture.parse_metric_count("1.2K followers"), 1200)

        # Unparseable, empty, localized ambiguous, negative, or unrelated values -> None
        for bad in [
            None,
            "",
            "   ",
            "1,2 mil",
            "1,2 тыс.",
            "1.2 लाख",
            "1,2K",
            "1.234",
            "5m ago",
            "@user123",
            "2026-11-12",
            "v2.1.0",
            "999+",
            "-15",
            "12% off",
        ]:
            self.assertIsNone(profile_capture.parse_metric_count(bad), f"Expected None for {bad!r}")

    def test_profile_capture_service_navigation_states(self):
        import profile_capture

        serial = "CAP_DEV_1"
        pkg = "com.instagram.cloner1"
        db.upsert_clone(serial, pkg, app_name="Clone One", is_confirmed=True, db_path=self.test_db)

        feed_with_one_tab_xml = f"""<hierarchy>
            <node resource-id="{pkg}:id/feed_tab" content-desc="Home" clickable="true" selected="true" />
            <node resource-id="{pkg}:id/profile_tab" content-desc="Profile" clickable="true" selected="false" />
        </hierarchy>"""

        profile_xml = f"""<hierarchy>
            <node resource-id="{pkg}:id/action_bar_title" text="creator.alpha" />
            <node resource-id="{pkg}:id/row_profile_header_textview_post_count" text="42" />
            <node resource-id="{pkg}:id/row_profile_header_textview_followers_count" text="1.2K" />
            <node text="posts" />
            <node text="followers" />
            <node text="Edit profile" clickable="true" />
            <node resource-id="{pkg}:id/profile_tab" content-desc="Profile" clickable="true" selected="true" />
        </hierarchy>"""

        ambiguous_feed_xml = f"""<hierarchy>
            <node content-desc="Profile" clickable="true" />
            <node content-desc="Profile" clickable="true" />
        </hierarchy>"""

        signed_out_xml = f"""<hierarchy>
            <node resource-id="{pkg}:id/login_username" text="Phone number, username, or email" />
            <node resource-id="{pkg}:id/password" text="Password" />
            <node text="Log in" clickable="true" />
        </hierarchy>"""

        missing_counts_xml = f"""<hierarchy>
            <node resource-id="{pkg}:id/action_bar_title" text="partial.handle" />
            <node resource-id="{pkg}:id/row_profile_header_textview_post_count" text="1,2 тыс." />
            <node text="EST 2024 notifications 99+" />
            <node text="Edit profile" clickable="true" />
            <node resource-id="{pkg}:id/profile_tab" content-desc="Profile" clickable="true" selected="true" />
        </hierarchy>"""

        def mock_adb(cmd, device_serial=None, timeout=8):
            if cmd[:1] == ["devices"]:
                return f"List of devices attached\n{serial}\tdevice model:Pixel_8\n"
            if cmd == ["shell", "pm", "path", pkg] and device_serial == serial:
                return f"package:/data/app/{pkg}/base.apk\n"
            return ""

        with patch("package_scanner.run_adb_command", side_effect=mock_adb), \
             patch.object(profile_capture, "_NAV_WAIT_SEC", 0):

            # A. Profile tab available -> navigates and captures handle + normalized counts
            mock_d = MagicMock()
            mock_d.app_current.return_value = {"package": pkg}
            mock_d.dump_hierarchy.side_effect = [feed_with_one_tab_xml, profile_xml]
            with patch("profile_capture.u2.connect", return_value=mock_d) as mock_conn:
                open_res = profile_capture.open_profile_app(serial, pkg, db_path=self.test_db)
                self.assertEqual(open_res["status"], "launched")
                mock_conn.assert_called_with(serial)
                mock_d.app_start.assert_called_with(pkg, stop=False)

                cap_res = profile_capture.capture_profile(serial, pkg, db_path=self.test_db)
                self.assertEqual(cap_res["status"], "captured")
                self.assertEqual(cap_res["profile_handle"], "creator.alpha")
                self.assertEqual(cap_res["posts_count"], 42)
                self.assertEqual(cap_res["followers_count"], 1200)
                self.assertFalse(cap_res["needs_manual_profile_navigation"])
                self.assertIsInstance(cap_res["raw_labels"], list)
                self.assertNotIn("<hierarchy", " ".join(cap_res["raw_labels"]))

            # B. Ambiguous/unavailable Profile selector -> returns needs_manual_profile_navigation
            mock_d2 = MagicMock()
            mock_d2.app_current.return_value = {"package": pkg}
            mock_d2.dump_hierarchy.return_value = ambiguous_feed_xml
            with patch("profile_capture.u2.connect", return_value=mock_d2):
                nav_res = profile_capture.capture_profile(serial, pkg, db_path=self.test_db)
                self.assertEqual(nav_res["status"], "needs_manual_profile_navigation")
                self.assertTrue(nav_res["needs_manual_profile_navigation"])
                self.assertIsNone(nav_res["profile_handle"])

            # C. Signed-out screen -> pauses cleanly
            mock_d3 = MagicMock()
            mock_d3.app_current.return_value = {"package": pkg}
            mock_d3.dump_hierarchy.return_value = signed_out_xml
            with patch("profile_capture.u2.connect", return_value=mock_d3):
                so_res = profile_capture.capture_profile(serial, pkg, db_path=self.test_db)
                self.assertEqual(so_res["status"], "signed_out")
                self.assertFalse(so_res["needs_manual_profile_navigation"])

            # D. Wrong expected handle -> status handle_mismatch
            mock_d4 = MagicMock()
            mock_d4.app_current.return_value = {"package": pkg}
            mock_d4.dump_hierarchy.return_value = profile_xml
            with patch("profile_capture.u2.connect", return_value=mock_d4):
                mm_res = profile_capture.capture_profile(serial, pkg, expected_username="different_user", db_path=self.test_db)
                self.assertEqual(mm_res["status"], "handle_mismatch")
                self.assertEqual(mm_res["profile_handle"], "creator.alpha")

            # E. Missing / localized counts -> return a verified partial issue, never guess
            mock_d5 = MagicMock()
            mock_d5.app_current.return_value = {"package": pkg}
            mock_d5.dump_hierarchy.return_value = missing_counts_xml
            with patch("profile_capture.u2.connect", return_value=mock_d5):
                partial_res = profile_capture.capture_profile(serial, pkg, db_path=self.test_db)
                self.assertEqual(partial_res["status"], "profile_metrics_incomplete")
                self.assertEqual(partial_res["profile_handle"], "partial.handle")
                self.assertIsNone(partial_res["posts_count"])
                self.assertIsNone(partial_res["followers_count"])

            # F. Lock screen / systemui overlay does not leak OS notifications
            sysui_xml = '<hierarchy><node package="com.android.systemui" text="Private notification" /></hierarchy>'
            mock_d6 = MagicMock()
            mock_d6.app_current.return_value = {"package": pkg}
            mock_d6.dump_hierarchy.return_value = sysui_xml
            with patch("profile_capture.u2.connect", return_value=mock_d6):
                lock_res = profile_capture.capture_profile(serial, pkg, db_path=self.test_db)
                self.assertEqual(lock_res["status"], "needs_manual_profile_navigation")
                self.assertEqual(lock_res["raw_labels"], [])

    def test_profile_capture_waits_through_splash_and_stops_exact_app(self):
        import profile_capture

        serial = "CAP_WAIT_DEV"
        pkg = "com.instagram.clone_wait"
        db.upsert_clone(serial, pkg, app_name="Clone Wait", is_confirmed=True, db_path=self.test_db)

        splash_xml = f'<hierarchy><node package="{pkg}" text="Instagram" /></hierarchy>'
        profile_xml = f"""<hierarchy>
            <node package="{pkg}" resource-id="{pkg}:id/action_bar_title" text="ready.owner" />
            <node package="{pkg}" resource-id="{pkg}:id/row_profile_header_textview_post_count" text="12" />
            <node package="{pkg}" resource-id="{pkg}:id/row_profile_header_textview_followers_count" text="3.4K" />
            <node package="{pkg}" resource-id="{pkg}:id/profile_tab" content-desc="Profile" clickable="true" selected="true" />
        </hierarchy>"""
        def mock_adb(cmd, device_serial=None, timeout=8):
            if cmd[:1] == ["devices"]:
                return f"List of devices attached\n{serial}\tdevice model:Pixel_8\n"
            if cmd == ["shell", "pm", "path", pkg] and device_serial == serial:
                return f"package:/data/app/{pkg}/base.apk\n"
            return ""

        with patch("package_scanner.run_adb_command", side_effect=mock_adb), \
             patch.object(profile_capture, "_NAV_WAIT_SEC", 0.01), \
             patch.object(profile_capture, "_CAPTURE_READY_TIMEOUT_SEC", 2), \
             patch.object(profile_capture, "_PROFILE_SETTLE_TIMEOUT_SEC", 1), \
             patch.object(profile_capture, "_POLL_INTERVAL_SEC", 0), \
             patch.object(profile_capture.time, "sleep"):
            device = MagicMock()
            device.app_current.return_value = {"package": pkg}
            device.dump_hierarchy.side_effect = [splash_xml, profile_xml]
            with patch("profile_capture.u2.connect", return_value=device):
                result = profile_capture.capture_profile(serial, pkg, db_path=self.test_db)

        self.assertEqual(result["status"], "captured")
        self.assertEqual(result["profile_handle"], "ready.owner")
        self.assertEqual(result["posts_count"], 12)
        self.assertEqual(result["followers_count"], 3400)
        self.assertIn("screen:splash_or_loading", result["verification_steps"])
        self.assertIn("captured", result["verification_steps"])

        def mock_adb(cmd, device_serial=None, timeout=8):
            if cmd[:1] == ["devices"]:
                return f"List of devices attached\n{serial}\tdevice model:Pixel_8\n"
            if cmd == ["shell", "pm", "path", pkg] and device_serial == serial:
                return f"package:/data/app/{pkg}/base.apk\n"
            return ""

        with patch("package_scanner.run_adb_command", side_effect=mock_adb), \
             patch("profile_capture.u2.connect") as connect_mock, \
             patch("profile_capture.time.sleep"):
            device = connect_mock.return_value
            device.app_list_running.return_value = []
            close_result = profile_capture.close_profile_app(serial, pkg, db_path=self.test_db)

        self.assertEqual(close_result["status"], "closed")
        connect_mock.assert_called_once_with(serial)
        device.app_stop.assert_called_once_with(pkg)

    # -----------------------------------------------------------------------
    # Module 2 Task 4: Timestamped Profile Snapshots Persistence
    # -----------------------------------------------------------------------
    def test_profile_snapshots_persistence_ordering_nullables_and_cascade(self):
        acc_id = db.create_account(
            username="snap_owner",
            device_serial="DEV_SNAP",
            package_name="com.instagram.android",
            db_path=self.test_db,
        )

        # Negative / non-integer counts rejected
        with self.assertRaises(ValueError):
            db.add_profile_snapshot(
                account_id=acc_id,
                profile_handle="snap_owner",
                posts_count=-1,
                followers_count=100,
                source_device_serial="DEV_SNAP",
                source_package_name="com.instagram.android",
                db_path=self.test_db,
            )
        with self.assertRaises(ValueError):
            db.add_profile_snapshot(
                account_id=acc_id,
                profile_handle="snap_owner",
                posts_count=True,
                followers_count=100,
                source_device_serial="DEV_SNAP",
                source_package_name="com.instagram.android",
                db_path=self.test_db,
            )

        # Insert nullable snapshot first, then newer populated snapshot
        snap1_id = db.add_profile_snapshot(
            account_id=acc_id,
            profile_handle="snap_owner",
            posts_count=None,
            followers_count=None,
            source_device_serial="DEV_SNAP",
            source_package_name="com.instagram.android",
            capture_source="ui",
            db_path=self.test_db,
        )
        snap2_id = db.add_profile_snapshot(
            account_id=acc_id,
            profile_handle="snap_owner",
            posts_count=18,
            followers_count=2450,
            source_device_serial="DEV_SNAP",
            source_package_name="com.instagram.android",
            capture_source="manual",
            db_path=self.test_db,
        )
        self.assertGreater(snap2_id, snap1_id)

        # Simulate restart / re-init
        db.init_db(db_path=self.test_db)

        snaps = db.get_profile_snapshots(acc_id, limit=20, db_path=self.test_db)
        self.assertEqual(len(snaps), 2)
        self.assertEqual(snaps[0]["id"], snap2_id)
        self.assertEqual(snaps[0]["posts_count"], 18)
        self.assertEqual(snaps[0]["followers_count"], 2450)
        self.assertEqual(snaps[0]["capture_source"], "manual")
        self.assertEqual(snaps[1]["id"], snap1_id)
        self.assertIsNone(snaps[1]["posts_count"])
        self.assertIsNone(snaps[1]["followers_count"])

        # Account hydration includes latest_snapshot safely
        acc = db.get_account_by_id(acc_id, db_path=self.test_db)
        self.assertIsNotNone(acc["latest_snapshot"])
        self.assertEqual(acc["latest_snapshot"]["id"], snap2_id)

        # Cascade delete removes profile snapshots cleanly
        self.assertTrue(db.delete_account(acc_id, db_path=self.test_db))
        self.assertEqual(db.get_profile_snapshots(acc_id, db_path=self.test_db), [])

    # -----------------------------------------------------------------------
    # Module 2 Task 5: Onboarding Launch, Capture, Confirm & History APIs
    # -----------------------------------------------------------------------
    def test_onboarding_routes_validation_conflicts_and_confirm(self):
        import app as app_module
        import profile_capture

        serial = "ONB_DEV_1"
        pkg_ok = "com.instagram.clone_a"
        pkg_unconf = "com.instagram.clone_unconf"
        pkg_absent = "com.instagram.absent"

        with patch("db.DEFAULT_DB_PATH", self.test_db):
            client = app_module.app.test_client()
            db.upsert_clone(serial, pkg_ok, app_name="Clone A", is_confirmed=True, db_path=self.test_db)
            db.upsert_clone(serial, pkg_unconf, app_name="Clone Unconf", is_confirmed=False, db_path=self.test_db)

            def mock_adb(cmd, device_serial=None, timeout=8):
                if cmd[:1] == ["devices"]:
                    return f"List of devices attached\n{serial}\tdevice model:Pixel_8\nDEV_OFF\toffline\n"
                if cmd == ["shell", "pm", "path", pkg_ok] and device_serial == serial:
                    return f"package:/data/app/{pkg_ok}/base.apk\n"
                if cmd == ["shell", "pm", "path", pkg_unconf] and device_serial == serial:
                    return f"package:/data/app/{pkg_unconf}/base.apk\n"
                return ""

            profile_xml = f"""<hierarchy>
                <node resource-id="{pkg_ok}:id/action_bar_title" text="new_creator" />
                <node resource-id="{pkg_ok}:id/row_profile_header_textview_post_count" text="12" />
                <node resource-id="{pkg_ok}:id/row_profile_header_textview_followers_count" text="3.4K" />
                <node resource-id="{pkg_ok}:id/profile_tab" content-desc="Profile" clickable="true" selected="true" />
            </hierarchy>"""

            feed_no_tab_xml = "<hierarchy><node text='Feed without profile selector' /></hierarchy>"

            with patch("package_scanner.run_adb_command", side_effect=mock_adb), \
                 patch.object(profile_capture, "_NAV_WAIT_SEC", 0):

                # 1. Invalid / missing / offline device rejected on launch & capture
                r_miss = client.post("/api/accounts/onboarding/launch", json={"device_serial": "", "package_name": pkg_ok})
                self.assertEqual(r_miss.status_code, 400)

                r_off = client.post("/api/accounts/onboarding/capture", json={"device_serial": "DEV_OFF", "package_name": pkg_ok})
                self.assertEqual(r_off.status_code, 400)

                # 2. Unconfirmed / missing package rejected without creating any account
                r_unconf = client.post("/api/accounts/onboarding/launch", json={"device_serial": serial, "package_name": pkg_unconf})
                self.assertEqual(r_unconf.status_code, 400)
                r_abs = client.post("/api/accounts/onboarding/capture", json={"device_serial": serial, "package_name": pkg_absent})
                self.assertEqual(r_abs.status_code, 400)
                self.assertEqual(db.get_accounts(db_path=self.test_db), [])

                # 3. Launch succeeds without side-effect account creation
                mock_d = MagicMock()
                mock_d.app_current.return_value = {"package": pkg_ok}
                mock_d.dump_hierarchy.return_value = profile_xml
                with patch("profile_capture.u2.connect", return_value=mock_d):
                    r_launch = client.post("/api/accounts/onboarding/launch", json={"device_serial": serial, "package_name": pkg_ok})
                    self.assertEqual(r_launch.status_code, 200)
                    self.assertEqual(r_launch.get_json()["status"], "launched")
                    self.assertEqual(db.get_accounts(db_path=self.test_db), [])

                    # 4. Capture succeeds cleanly with zero account rows created and zero secrets
                    r_cap = client.post("/api/accounts/onboarding/capture", json={"device_serial": serial, "package_name": pkg_ok})
                    self.assertEqual(r_cap.status_code, 200)
                    cdata = r_cap.get_json()
                    self.assertEqual(cdata["status"], "captured")
                    self.assertEqual(cdata["profile_handle"], "new_creator")
                    self.assertEqual(cdata["posts_count"], 12)
                    self.assertEqual(cdata["followers_count"], 3400)
                    self.assertEqual(db.get_accounts(db_path=self.test_db), [])

                # 5. Manual profile navigation case returns structured guidance
                mock_d_manual = MagicMock()
                mock_d_manual.app_current.return_value = {"package": pkg_ok}
                mock_d_manual.dump_hierarchy.return_value = feed_no_tab_xml
                with patch("profile_capture.u2.connect", return_value=mock_d_manual):
                    r_nav = client.post("/api/accounts/onboarding/capture", json={"device_serial": serial, "package_name": pkg_ok})
                    self.assertEqual(r_nav.status_code, 200)
                    self.assertTrue(r_nav.get_json()["needs_manual_profile_navigation"])

                # 6. Confirm creates account + taxonomy + first snapshot in one transaction
                cat_id = db.create_category("accounts", "Lifestyle", db_path=self.test_db)
                tag_id = db.create_tag("accounts", "VerifiedOnboard", db_path=self.test_db)

                # Invalid negative count must reject atomically
                r_bad_cnt = client.post("/api/accounts/onboarding/confirm", json={
                    "device_serial": serial,
                    "package_name": pkg_ok,
                    "profile_handle": "new_creator",
                    "posts_count": -5,
                })
                self.assertEqual(r_bad_cnt.status_code, 400)
                self.assertEqual(db.get_accounts(db_path=self.test_db), [])

                r_conf = client.post("/api/accounts/onboarding/confirm", json={
                    "device_serial": serial,
                    "package_name": pkg_ok,
                    "profile_handle": "new_creator",
                    "posts_count": 12,
                    "followers_count": 3400,
                    "category_id": cat_id,
                    "tag_ids": [tag_id],
                    "capture_source": "ui",
                })
                self.assertEqual(r_conf.status_code, 201)
                conf_data = r_conf.get_json()
                acc_id = conf_data["account"]["id"]
                self.assertEqual(conf_data["account"]["category"]["id"], cat_id)
                self.assertEqual([t["id"] for t in conf_data["account"]["tags"]], [tag_id])
                self.assertEqual(conf_data["snapshot"]["followers_count"], 3400)
                dumped_conf = json.dumps(conf_data).lower()
                self.assertNotIn("totp_seed", dumped_conf)
                self.assertNotIn("<hierarchy", dumped_conf)

                # 7. Duplicate username conflict (new account mode) -> 409 with existing account
                pkg_ok_2 = "com.instagram.clone_b"
                db.upsert_clone(serial, pkg_ok_2, app_name="Clone B", is_confirmed=True, db_path=self.test_db)
                def mock_adb_2(cmd, device_serial=None, timeout=8):
                    if cmd == ["shell", "pm", "path", pkg_ok_2] and device_serial == serial:
                        return f"package:/data/app/{pkg_ok_2}/base.apk\n"
                    return mock_adb(cmd, device_serial=device_serial, timeout=timeout)

                with patch("package_scanner.run_adb_command", side_effect=mock_adb_2):
                    r_dup_user = client.post("/api/accounts/onboarding/confirm", json={
                        "device_serial": serial,
                        "package_name": pkg_ok_2,
                        "profile_handle": "new_creator",
                    })
                    self.assertEqual(r_dup_user.status_code, 409)
                    self.assertEqual(r_dup_user.get_json()["code"], "duplicate_username")

                # 8. Duplicate device/package binding conflict (different handle) -> 409
                r_dup_bind = client.post("/api/accounts/onboarding/confirm", json={
                    "device_serial": serial,
                    "package_name": pkg_ok,
                    "profile_handle": "another_creator",
                })
                self.assertEqual(r_dup_bind.status_code, 409)
                self.assertEqual(r_dup_bind.get_json()["code"], "duplicate_binding")

                # 9. Existing account confirm with handle mismatch -> 409
                r_mismatch = client.post("/api/accounts/onboarding/confirm", json={
                    "device_serial": serial,
                    "package_name": pkg_ok,
                    "account_id": acc_id,
                    "profile_handle": "wrong_handle",
                })
                self.assertEqual(r_mismatch.status_code, 409)
                self.assertEqual(r_mismatch.get_json()["code"], "handle_mismatch")

                # 10. Existing account confirm with matching handle appends 2nd snapshot
                r_refresh = client.post("/api/accounts/onboarding/confirm", json={
                    "device_serial": serial,
                    "package_name": pkg_ok,
                    "account_id": acc_id,
                    "profile_handle": "new_creator",
                    "posts_count": 15,
                    "followers_count": None,
                    "capture_source": "mixed",
                })
                self.assertEqual(r_refresh.status_code, 200)

                # 11. Profile history endpoint returns snapshots newest-first
                r_hist = client.get(f"/api/accounts/{acc_id}/profile-history?limit=5")
                self.assertEqual(r_hist.status_code, 200)
                snaps = r_hist.get_json()["snapshots"]
                self.assertEqual(len(snaps), 2)
                self.assertEqual(snaps[0]["posts_count"], 15)
                self.assertIsNone(snaps[0]["followers_count"])
                self.assertEqual(snaps[1]["posts_count"], 12)

                # Missing account history returns 404
                self.assertEqual(client.get("/api/accounts/999999/profile-history").status_code, 404)

    def test_onboarding_ui_controls_and_no_secret_leak(self):
        import app as app_module

        with patch("db.DEFAULT_DB_PATH", self.test_db):
            client = app_module.app.test_client()
            resp = client.get("/")
            self.assertEqual(resp.status_code, 200)
            html = resp.get_data(as_text=True)

            for marker in [
                "btnAddFromDevice",
                "deviceOnboardingModal",
                "onboardDeviceSelect",
                "onboardPackageSelect",
                "btnOnboardLaunch",
                "btnOnboardCapture",
                "onboardHandleInput",
                "onboardPostsInput",
                "onboardFollowersInput",
                "btnConfirmOnboarding",
                "onboardingConflictBox",
                "profileHistoryModal",
                "/api/devices",
                "/api/accounts/onboarding/launch",
                "/api/accounts/onboarding/capture",
                "/api/accounts/onboarding/confirm",
            ]:
                self.assertIn(marker, html)

            # Rebuilt Accounts workspace markers and profile-only absence of credential prompts
            for rebuild_marker in [
                "accSubTabList",
                "accSubTabCapture",
                "accSubTabClones",
                "accSubTabTransfer",
                "accountSearchInput",
                "btnManualAddAccount",
                "formPostsCount",
                "formFollowersCount",
                "accountManualReviewBox",
                "bulkDeviceSelect",
                "btnBulkScanDevice",
                "bulkPackageChecklist",
                "btnBulkCaptureStart",
                "btnBulkCapturePause",
                "btnBulkCaptureCancel",
                "bulkPauseOnIssue",
                "bulkReviewTbody",
                "btnSaveBulkSelected",
                "cloneDeviceFilter",
            ]:
                self.assertIn(rebuild_marker, html)

            for removed_cred_marker in [
                "formPassword",
                "formTotpSeed",
                "secretsModal",
                "mapCol_password",
                "mapCol_totp_seed",
            ]:
                self.assertNotIn(removed_cred_marker, html)

    def test_manual_profile_add_and_update_optional_snapshot_counts(self):
        import app as app_module

        cat_id = db.create_category("accounts", "Reels Creators", db_path=self.test_db)
        tag_id = db.create_tag("accounts", "ManualTag", db_path=self.test_db)

        with patch("db.DEFAULT_DB_PATH", self.test_db):
            client = app_module.app.test_client()

            # Reject negative manual count upfront
            r_bad = client.post("/api/accounts", json={
                "username": "bad_count_user",
                "posts_count": -1,
            })
            self.assertEqual(r_bad.status_code, 400)
            self.assertIsNone(db.get_account_by_username("bad_count_user", db_path=self.test_db))

            # Create profile-only account with optional counts, device/app, taxonomy, and zero credentials
            r_create = client.post("/api/accounts", json={
                "username": "manual_creator",
                "posts_count": 24,
                "followers_count": 1520,
                "device_serial": "SER_MAN_01",
                "package_name": "com.instagram.clone1",
                "category_id": cat_id,
                "tag_ids": [tag_id],
                "notes": "Profile-only entry",
            })
            self.assertEqual(r_create.status_code, 201)
            acc = r_create.get_json()["account"]
            self.assertEqual(acc["username"], "manual_creator")
            self.assertFalse(acc["has_password"])
            self.assertFalse(acc["has_totp"])
            self.assertEqual(acc["category"]["name"], "Reels Creators")
            self.assertEqual(acc["latest_snapshot"]["posts_count"], 24)
            self.assertEqual(acc["latest_snapshot"]["followers_count"], 1520)
            self.assertEqual(acc["latest_snapshot"]["capture_source"], "manual")

            # Update account followers count manually
            acc_id = acc["id"]
            r_upd = client.put(f"/api/accounts/{acc_id}", json={
                "posts_count": 25,
                "followers_count": 1600,
            })
            self.assertEqual(r_upd.status_code, 200)
            updated = r_upd.get_json()["account"]
            self.assertEqual(updated["latest_snapshot"]["posts_count"], 25)
            self.assertEqual(updated["latest_snapshot"]["followers_count"], 1600)

            hist = client.get(f"/api/accounts/{acc_id}/profile-history").get_json()["snapshots"]
            self.assertEqual(len(hist), 2)

    def test_screen_verification_splash_wait_and_startup_prompt_dismissal(self):
        import profile_capture

        serial = "CAP_WAIT_DEV"
        pkg = "com.instagram.android.clone10"
        db.upsert_clone(serial, pkg, app_name="Clone Ten", is_confirmed=True, db_path=self.test_db)

        def mock_adb(cmd, device_serial=None, timeout=8):
            if cmd[:1] == ["devices"]:
                return f"List of devices attached\n{serial}\tdevice model:TECNO_LD7j\n"
            if cmd == ["shell", "pm", "path", pkg] and device_serial == serial:
                return f"package:/data/app/{pkg}/base.apk\n"
            return ""

        splash_sysbar_xml = f"""<hierarchy>
            <node package="com.android.systemui" resource-id="com.android.systemui:id/status_bar_container" />
            <node package="com.android.systemui" resource-id="com.android.systemui:id/scrim_behind" />
        </hierarchy>"""

        save_login_modal_xml = f"""<hierarchy>
            <node package="com.android.systemui" resource-id="com.android.systemui:id/status_bar_container" />
            <node package="{pkg}" text="Save your login info to Instagram?" />
            <node package="{pkg}" text="verified.creator" />
            <node package="{pkg}" text="Save" clickable="true" />
            <node package="{pkg}" text="Not now" clickable="false" />
        </hierarchy>"""

        home_tabs_xml = f"""<hierarchy>
            <node package="{pkg}" resource-id="{pkg}:id/feed_tab" content-desc="Home" clickable="true" selected="true" />
            <node package="{pkg}" resource-id="{pkg}:id/profile_tab" content-desc="Profile" clickable="true" selected="false" />
        </hierarchy>"""

        profile_partial_xml = f"""<hierarchy>
            <node package="{pkg}" resource-id="{pkg}:id/profile_tab" content-desc="Profile" clickable="true" selected="true" />
            <node package="{pkg}" resource-id="{pkg}:id/action_bar_title" text="verified.creator" />
            <node package="{pkg}" text="Edit profile" clickable="true" />
        </hierarchy>"""

        profile_full_xml = f"""<hierarchy>
            <node package="{pkg}" resource-id="{pkg}:id/profile_tab" content-desc="Profile" clickable="true" selected="true" />
            <node package="{pkg}" resource-id="{pkg}:id/action_bar_title" text="verified.creator" />
            <node package="{pkg}" resource-id="{pkg}:id/row_profile_header_textview_post_count" text="154" />
            <node package="{pkg}" resource-id="{pkg}:id/row_profile_header_textview_followers_count" text="1,955" />
            <node package="{pkg}" text="Edit profile" clickable="true" />
        </hierarchy>"""

        keyguard_xml = """<hierarchy>
            <node package="com.android.systemui" resource-id="com.android.systemui:id/keyguard_message_area" text="Unlock screen" />
            <node package="com.android.systemui" text="Sensitive OTP 999888" />
        </hierarchy>"""

        with patch("package_scanner.run_adb_command", side_effect=mock_adb), \
             patch.object(profile_capture, "_NAV_WAIT_SEC", 0.01), \
             patch.object(profile_capture, "_LAUNCH_READY_TIMEOUT_SEC", 1.5), \
             patch.object(profile_capture, "_CAPTURE_READY_TIMEOUT_SEC", 1.5), \
             patch.object(profile_capture, "_PROFILE_SETTLE_TIMEOUT_SEC", 1.5), \
             patch.object(profile_capture, "_POLL_INTERVAL_SEC", 0.01), \
             patch("profile_capture.time.sleep", return_value=None):

            # 1. open_profile_app waits past statusbar splash until home tabs appear
            mock_launch_d = MagicMock()
            mock_launch_d.app_current.side_effect = [
                {"package": "com.transsion.hilauncher"},
                {"package": pkg},
                {"package": pkg},
            ]
            mock_launch_d.dump_hierarchy.side_effect = [
                splash_sysbar_xml,
                splash_sysbar_xml,
                home_tabs_xml,
            ]
            with patch("profile_capture.u2.connect", return_value=mock_launch_d):
                open_res = profile_capture.open_profile_app(serial, pkg, db_path=self.test_db)
                self.assertEqual(open_res["status"], "launched")
                self.assertEqual(open_res["screen_state"], "home_or_tab_screen")
                self.assertIn("screen:splash_or_loading", open_res["verification_steps"])
                self.assertIn("screen:home_or_tab_screen", open_res["verification_steps"])

            # 2. capture_profile waits past splash + dismisses benign 'Save your login info?' + waits for counts
            mock_cap_d = MagicMock()
            mock_cap_d.app_current.side_effect = [
                {"package": pkg},                        # splash
                {"package": pkg},                        # save-login modal
                {"package": "com.transsion.hilauncher"}, # launcher lag while nodes are already pkg!
                {"package": pkg},                        # settle partial
                {"package": pkg},                        # settle full
            ]
            mock_cap_d.dump_hierarchy.side_effect = [
                splash_sysbar_xml,
                save_login_modal_xml,
                home_tabs_xml,
                profile_partial_xml,
                profile_full_xml,
            ]
            with patch("profile_capture.u2.connect", return_value=mock_cap_d):
                cap_res = profile_capture.capture_profile(serial, pkg, db_path=self.test_db)
                self.assertEqual(cap_res["status"], "captured")
                self.assertEqual(cap_res["profile_handle"], "verified.creator")
                self.assertEqual(cap_res["posts_count"], 154)
                self.assertEqual(cap_res["followers_count"], 1955)
                self.assertIn("dismissed_startup_prompt", cap_res["verification_steps"])
                self.assertIn("clicked_profile_tab", cap_res["verification_steps"])
                self.assertIn("captured", cap_res["verification_steps"])

            # 3. Genuine keyguard lockscreen does NOT bypass or leak notifications
            mock_lock_d = MagicMock()
            mock_lock_d.app_current.return_value = {"package": "com.android.systemui"}
            mock_lock_d.dump_hierarchy.return_value = keyguard_xml
            with patch("profile_capture.u2.connect", return_value=mock_lock_d):
                lock_res = profile_capture.capture_profile(serial, pkg, db_path=self.test_db)
                self.assertEqual(lock_res["status"], "needs_manual_profile_navigation")
                self.assertEqual(lock_res["screen_state"], "systemui_locked")
                self.assertEqual(lock_res["raw_labels"], [])

    # -----------------------------------------------------------------------
    # Module 3 Task 1: Persistent Content Asset Records
    # -----------------------------------------------------------------------
    def test_content_library_repository_crud_taxonomy_and_pagination(self):
        acc_cat = db.create_category("accounts", "AcctOnlyCat", db_path=self.test_db)
        acc_tag = db.create_tag("accounts", "AcctOnlyTag", db_path=self.test_db)
        con_cat1 = db.create_category("content", "Reels", db_path=self.test_db)
        con_cat2 = db.create_category("content", "B-Roll", db_path=self.test_db)
        con_tag1 = db.create_tag("content", "Cars", db_path=self.test_db)
        con_tag2 = db.create_tag("content", "4K", db_path=self.test_db)

        # Idempotent repeated init_db preserves data & migrations
        cid1 = db.create_content_item(
            original_filename="porsche_launch.mp4",
            storage_key="aaaa1111_porsche_launch.mp4",
            media_type="video",
            mime_type="video/mp4",
            size_bytes=1234567,
            category_id=con_cat1,
            tag_ids=[con_tag1, con_tag2],
            db_path=self.test_db,
        )
        cid2 = db.create_content_item(
            original_filename="cover_shot.jpg",
            storage_key="bbbb2222_cover_shot.jpg",
            media_type="image",
            mime_type="image/jpeg",
            size_bytes=54321,
            category_id=None,
            tag_ids=[con_tag1],
            db_path=self.test_db,
        )
        cid3 = db.create_content_item(
            original_filename="track_day.mov",
            storage_key="cccc3333_track_day.mov",
            media_type="video",
            mime_type="video/quicktime",
            size_bytes=987654,
            category_id=con_cat2,
            tag_ids=[],
            db_path=self.test_db,
        )

        db.init_db(self.test_db)
        item1 = db.get_content_item(cid1, db_path=self.test_db)
        self.assertIsNotNone(item1)
        self.assertEqual(item1["original_filename"], "porsche_launch.mp4")
        self.assertEqual(item1["media_type"], "video")
        self.assertEqual(item1["category"]["name"], "Reels")
        self.assertEqual([t["name"] for t in item1["tags"]], ["Cars", "4K"])

        # Accounts-scoped taxonomy cannot attach to content
        with self.assertRaises(ValueError):
            db.create_content_item(
                original_filename="bad_scope.mp4",
                storage_key="dddd4444_bad_scope.mp4",
                media_type="video",
                mime_type="video/mp4",
                size_bytes=100,
                category_id=acc_cat,
                tag_ids=[],
                db_path=self.test_db,
            )
        with self.assertRaises(ValueError):
            db.update_content_item_taxonomy(cid1, con_cat2, [con_tag2, acc_tag], db_path=self.test_db)

        # Failed update must leave existing category and tags untouched
        item1_after_fail = db.get_content_item(cid1, db_path=self.test_db)
        self.assertEqual(item1_after_fail["category_id"], con_cat1)
        self.assertEqual(item1_after_fail["tag_ids"], [con_tag1, con_tag2])

        # In-use protection for delete_category and delete_tag in content scope
        with self.assertRaises(db.TaxonomyInUseError):
            db.delete_category(con_cat1, db_path=self.test_db)
        with self.assertRaises(db.TaxonomyInUseError):
            db.delete_tag(con_tag1, db_path=self.test_db)

        # Atomic update replaces category & tags
        db.update_content_item_taxonomy(cid1, con_cat2, [con_tag2], db_path=self.test_db)
        item1_updated = db.get_content_item(cid1, db_path=self.test_db)
        self.assertEqual(item1_updated["category_id"], con_cat2)
        self.assertEqual(item1_updated["tag_ids"], [con_tag2])
        self.assertTrue(db.delete_category(con_cat1, db_path=self.test_db))

        # Filtering + pagination bounds (default 50, max 100)
        page_res = db.list_content_items(
            search="porsche", media_type="video", category_id=con_cat2, tag_id=con_tag2,
            page=1, per_page=500, db_path=self.test_db
        )
        self.assertEqual(page_res["per_page"], 100)
        self.assertEqual(page_res["total"], 1)
        self.assertEqual(page_res["pages"], 1)
        self.assertEqual(page_res["items"][0]["id"], cid1)

        p1 = db.list_content_items(page=1, per_page=2, db_path=self.test_db)
        p2 = db.list_content_items(page=2, per_page=2, db_path=self.test_db)
        self.assertEqual(p1["total"], 3)
        self.assertEqual(p1["pages"], 2)
        self.assertEqual(len(p1["items"]), 2)
        self.assertEqual(len(p2["items"]), 1)

        # Delete removes item and cascades content_tags so orphaned tag becomes deletable
        deleted = db.delete_content_item(cid2, db_path=self.test_db)
        self.assertIsNotNone(deleted)
        self.assertEqual(deleted["storage_key"], "bbbb2222_cover_shot.jpg")
        self.assertIsNone(db.get_content_item(cid2, db_path=self.test_db))
        self.assertTrue(db.delete_tag(con_tag1, db_path=self.test_db))
        self.assertIsNone(db.delete_content_item(999999, db_path=self.test_db))

    # -----------------------------------------------------------------------
    # Module 3 Task 2: Content Library API + Secure File Serving + Reel Start
    # -----------------------------------------------------------------------
    def test_content_library_api_upload_serve_patch_delete_and_security(self):
        import tempfile
        import shutil
        import app as app_module

        tmp_uploads = tempfile.mkdtemp(prefix="instoper_content_test_")
        try:
            with patch("db.DEFAULT_DB_PATH", self.test_db), \
                 patch.dict(app_module.app.config, {"UPLOAD_FOLDER": tmp_uploads}):
                client = app_module.app.test_client()
                acc_cat = db.create_category("accounts", "WrongScopeCat", db_path=self.test_db)
                con_cat = db.create_category("content", "Supercars", db_path=self.test_db)
                con_tag = db.create_tag("content", "Vertical", db_path=self.test_db)

                # Multi-file upload: valid video + valid image + disallowed exe
                res_up = client.post(
                    "/api/content",
                    data={
                        "files": [
                            (io.BytesIO(b"\x00\x00\x00\x18ftypmp42"), "../evil/launch_clip.mp4"),
                            (io.BytesIO(b"\x89PNG\r\n\x1a\nPNGDATA"), "thumb_cover.png"),
                            (io.BytesIO(b"MZ_BAD"), "malware.exe"),
                        ]
                    },
                    content_type="multipart/form-data",
                )
                self.assertIn(res_up.status_code, (201, 207))
                body = res_up.get_json()
                self.assertEqual(len(body["items"]), 2)
                self.assertEqual(len(body["errors"]), 1)

                video_item = next(i for i in body["items"] if i["media_type"] == "video")
                image_item = next(i for i in body["items"] if i["media_type"] == "image")
                self.assertNotIn("..", video_item["storage_key"])
                self.assertNotIn("/", video_item["storage_key"])

                # All-invalid upload returns 400 and leaves zero files
                res_bad = client.post(
                    "/api/content",
                    data={"files": [(io.BytesIO(b"nope"), "script.sh")]},
                    content_type="multipart/form-data",
                )
                self.assertEqual(res_bad.status_code, 400)

                # DB failure during upload cleans up temp saved file (no orphan)
                content_dir = os.path.join(tmp_uploads, "content")
                before_files = set(os.listdir(content_dir))
                with patch("db.create_content_item", side_effect=RuntimeError("db boom")):
                    res_fail = client.post(
                        "/api/content",
                        data={"files": [(io.BytesIO(b"videobytes"), "orphan_check.mp4")]},
                        content_type="multipart/form-data",
                    )
                    self.assertIn(res_fail.status_code, (400, 500))
                self.assertEqual(set(os.listdir(content_dir)), before_files)

                # GET /api/content filtering & pagination
                r_vid = client.get("/api/content?media_type=video&search=launch_clip")
                self.assertEqual(r_vid.status_code, 200)
                self.assertEqual(r_vid.get_json()["total"], 1)
                self.assertEqual(r_vid.get_json()["items"][0]["id"], video_item["id"])

                # GET /api/content/<id>/file streams safely + range conditional
                r_file = client.get(f"/api/content/{video_item['id']}/file")
                self.assertEqual(r_file.status_code, 200)
                self.assertEqual(r_file.data, b"\x00\x00\x00\x18ftypmp42")
                r_file.close()

                # Path-traversal storage_key in DB cannot escape uploads/content
                evil_id = db.create_content_item(
                    original_filename="escape.mp4",
                    storage_key="../../secret.txt",
                    media_type="video",
                    mime_type="video/mp4",
                    size_bytes=10,
                    db_path=self.test_db,
                )
                self.assertEqual(client.get(f"/api/content/{evil_id}/file").status_code, 404)

                # PATCH taxonomy + scope mismatch rejection
                r_bad_scope = client.patch(
                    f"/api/content/{video_item['id']}",
                    json={"category_id": acc_cat, "tag_ids": [con_tag]},
                )
                self.assertEqual(r_bad_scope.status_code, 400)

                r_patch = client.patch(
                    f"/api/content/{video_item['id']}",
                    json={"category_id": con_cat, "tag_ids": [con_tag]},
                )
                self.assertEqual(r_patch.status_code, 200)
                patched = r_patch.get_json()
                self.assertEqual(patched["category"]["name"], "Supercars")
                self.assertEqual([t["name"] for t in patched["tags"]], ["Vertical"])

                # In-use tag/category returns 409 via taxonomy API
                self.assertEqual(client.delete(f"/api/categories/{con_cat}").status_code, 409)
                self.assertEqual(client.delete(f"/api/tags/{con_tag}").status_code, 409)

                # DELETE content removes both DB record and disk file
                vid_disk_path = os.path.join(content_dir, video_item["storage_key"])
                self.assertTrue(os.path.exists(vid_disk_path))
                r_del = client.delete(f"/api/content/{video_item['id']}")
                self.assertEqual(r_del.status_code, 200)
                self.assertFalse(os.path.exists(vid_disk_path))
                self.assertEqual(client.get(f"/api/content/{video_item['id']}/file").status_code, 404)
                self.assertEqual(client.delete("/api/content/999999").status_code, 404)

                # Request exceeding MAX_CONTENT_LENGTH returns JSON 413 on /api/*
                with patch.dict(app_module.app.config, {"MAX_CONTENT_LENGTH": 32}):
                    r_413 = client.post(
                        "/api/content",
                        data={"files": [(io.BytesIO(b"X" * 256), "too_big.mp4")]},
                        content_type="multipart/form-data",
                    )
                    self.assertEqual(r_413.status_code, 413)
                    self.assertIn("error", r_413.get_json())
        finally:
            shutil.rmtree(tmp_uploads, ignore_errors=True)

    def test_reel_workflow_start_from_library_video_and_reject_image(self):
        import tempfile
        import shutil
        import app as app_module

        tmp_uploads = tempfile.mkdtemp(prefix="instoper_reel_lib_test_")
        try:
            with patch("db.DEFAULT_DB_PATH", self.test_db), \
                 patch.dict(app_module.app.config, {"UPLOAD_FOLDER": tmp_uploads}), \
                 patch.dict(app_module.workflow_status, {"state": "idle"}):
                client = app_module.app.test_client()

                r_up = client.post(
                    "/api/content",
                    data={
                        "files": [
                            (io.BytesIO(b"MP4DATA"), "reel_clip.mp4"),
                            (io.BytesIO(b"JPGDATA"), "still_photo.jpg"),
                        ]
                    },
                    content_type="multipart/form-data",
                )
                items = r_up.get_json()["items"]
                vid = next(i for i in items if i["media_type"] == "video")
                img = next(i for i in items if i["media_type"] == "image")

                # Reject missing content_id, image content_id, and mutually exclusive payload before thread start
                with patch.object(app_module.threading, "Thread") as mock_thread:
                    r_missing = client.post("/api/start", json={"content_id": 999999})
                    self.assertEqual(r_missing.status_code, 404)

                    r_img = client.post("/api/start", json={"content_id": img["id"]})
                    self.assertEqual(r_img.status_code, 400)

                    r_both = client.post("/api/start", json={"content_id": vid["id"], "video_filename": "direct.mp4"})
                    self.assertEqual(r_both.status_code, 400)
                    mock_thread.assert_not_called()

                    # Valid library video resolves exact disk path & starts workflow thread
                    r_ok = client.post(
                        "/api/start",
                        json={"content_id": vid["id"], "caption": "Lib Caption", "package_name": PACKAGE_NAME},
                    )
                    self.assertEqual(r_ok.status_code, 200)
                    mock_thread.assert_called_once()
                    args = mock_thread.call_args.kwargs.get("args")
                    expected_path = os.path.abspath(os.path.join(tmp_uploads, "content", vid["storage_key"]))
                    self.assertEqual(os.path.abspath(args[1]), expected_path)
        finally:
            shutil.rmtree(tmp_uploads, ignore_errors=True)

    def test_content_library_ui_and_reel_picker_markers(self):
        html_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "templates", "index.html"))
        with open(html_path, "r", encoding="utf-8") as fh:
            html = fh.read()

        required_markers = [
            "id=\"contentView\"",
            "id=\"contentUploadInput\"",
            "id=\"contentSearchInput\"",
            "id=\"contentMediaTypeFilter\"",
            "id=\"contentCategoryFilter\"",
            "id=\"contentTagFilter\"",
            "id=\"contentLibraryGrid\"",
            "id=\"contentPaginationBar\"",
            "id=\"contentPreviewModal\"",
            "id=\"contentTaxonomyEditModal\"",
            "id=\"reelLibraryPickerModal\"",
            "id=\"btnChooseReelFromLibrary\"",
            "openTaxonomyManagerModal('content')",
        ]
        for marker in required_markers:
            self.assertIn(marker, html, f"Missing Content Library UI marker: {marker}")
        self.assertNotIn("Content Library Coming Soon", html)
        for legacy in ("bg-slate-", "text-slate-", "border-slate-", "bg-pink-", "text-pink-"):
            self.assertNotIn(legacy, html, f"Disallowed hardcoded palette class: {legacy}")

    # -----------------------------------------------------------------------
    # Module 4 Tasks 1 & 2: Automation Catalog, Target Validation & Serial Dispatch
    # -----------------------------------------------------------------------
    def test_automations_catalog_endpoint_and_no_script_runner(self):
        import app as app_module

        with patch("db.DEFAULT_DB_PATH", self.test_db):
            client = app_module.app.test_client()
            res = client.get("/api/automations")
            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            automations = data.get("automations", [])
            self.assertEqual(len(automations), 1)
            entry = automations[0]
            self.assertEqual(entry["id"], "reel_upload_with_music")
            self.assertEqual(entry["name"], "Reel Upload with Music")
            self.assertEqual(entry["status"], "ready")
            self.assertEqual(entry["steps"], 11)
            self.assertTrue(entry.get("description"))

            # Unknown automation ID is rejected cleanly before worker creation
            with patch.dict(app_module.workflow_status, {"state": "idle"}), \
                 patch.object(app_module.threading, "Thread") as mock_thread:
                r_unknown = client.post("/api/start", json={"automation_id": "custom_python_runner"})
                self.assertEqual(r_unknown.status_code, 400)
                mock_thread.assert_not_called()

    def test_automation_start_target_validation_audio_conflict_and_serial(self):
        import tempfile
        import shutil
        import app as app_module
        import adb

        tmp_uploads = tempfile.mkdtemp(prefix="instoper_m4_test_")
        try:
            with patch("db.DEFAULT_DB_PATH", self.test_db), \
                 patch.dict(app_module.app.config, {"UPLOAD_FOLDER": tmp_uploads}):
                client = app_module.app.test_client()

                dev_online = "DEV_AUTO_1"
                dev_offline = "DEV_OFFLINE_2"
                pkg_ok = "com.instagram.clone9"
                pkg_unconf = "com.instagram.clone_unconf"
                pkg_uninst = "com.instagram.clone_gone"

                db.upsert_clone(dev_online, pkg_ok, app_name="IG Clone 9", is_confirmed=True, db_path=self.test_db)
                db.upsert_clone(dev_online, pkg_unconf, app_name="IG Unconf", is_confirmed=False, db_path=self.test_db)
                db.upsert_clone(dev_online, pkg_uninst, app_name="IG Gone", is_confirmed=True, db_path=self.test_db)
                db.upsert_clone(dev_offline, pkg_ok, app_name="IG Offline", is_confirmed=True, db_path=self.test_db)

                acc_unbound = db.create_account(username="unbound_user", db_path=self.test_db)
                acc_offline = db.create_account(
                    username="offline_user", device_serial=dev_offline, package_name=pkg_ok, db_path=self.test_db
                )
                acc_unconf = db.create_account(
                    username="unconf_user", device_serial=dev_online, package_name=pkg_unconf, db_path=self.test_db
                )
                acc_uninst = db.create_account(
                    username="uninst_user", device_serial=dev_online, package_name=pkg_uninst, db_path=self.test_db
                )
                acc_ok = db.create_account(
                    username="ready_user", device_serial=dev_online, package_name=pkg_ok, db_path=self.test_db
                )

                up = client.post(
                    "/api/content",
                    data={"files": [(io.BytesIO(b"MP4DATA"), "auto_clip.mp4")]},
                    content_type="multipart/form-data",
                )
                vid_id = up.get_json()["items"][0]["id"]

                def mock_adb(cmd, device_serial=None, timeout=8):
                    if cmd[:1] == ["devices"]:
                        return f"List of devices attached\n{dev_online}\tdevice model:LD7j\n{dev_offline}\toffline\n"
                    if cmd == ["shell", "pm", "path", pkg_ok] and device_serial == dev_online:
                        return f"package:/data/app/{pkg_ok}/base.apk\n"
                    if cmd == ["shell", "pm", "path", pkg_unconf] and device_serial == dev_online:
                        return f"package:/data/app/{pkg_unconf}/base.apk\n"
                    return ""

                valid_audio = "https://www.instagram.com/reels/audio/1656648626250751/"

                with patch("package_scanner.run_adb_command", side_effect=mock_adb), \
                     patch.dict(app_module.workflow_status, {"state": "idle"}), \
                     patch.object(app_module.threading, "Thread") as mock_thread:

                    # Missing / invalid / non-existent account ID
                    self.assertEqual(client.post("/api/start", json={"automation_id": "reel_upload_with_music", "audio_url": valid_audio}).status_code, 400)
                    self.assertEqual(client.post("/api/start", json={"automation_id": "reel_upload_with_music", "account_id": "abc", "audio_url": valid_audio}).status_code, 400)
                    self.assertEqual(client.post("/api/start", json={"automation_id": "reel_upload_with_music", "account_id": 999999, "audio_url": valid_audio}).status_code, 404)

                    # Unbound, offline, unconfirmed, uninstalled accounts all rejected
                    for bad_acc in (acc_unbound, acc_offline, acc_unconf, acc_uninst):
                        r_bad = client.post(
                            "/api/start",
                            json={"automation_id": "reel_upload_with_music", "account_id": bad_acc, "audio_url": valid_audio},
                        )
                        self.assertEqual(r_bad.status_code, 400, f"Expected 400 for account {bad_acc}, got {r_bad.data}")

                    # Malformed / non-Instagram audio URLs rejected
                    for bad_audio in ("", "not-a-url", "https://youtube.com/watch?v=123", "https://www.instagram.com/"):
                        r_aud = client.post(
                            "/api/start",
                            json={
                                "automation_id": "reel_upload_with_music",
                                "account_id": acc_ok,
                                "content_id": vid_id,
                                "audio_url": bad_audio,
                            },
                        )
                        self.assertEqual(r_aud.status_code, 400, f"Expected 400 for audio_url={bad_audio!r}")

                    mock_thread.assert_not_called()

                    # Active run conflict returns 409 and spawns no thread
                    with patch.dict(app_module.workflow_status, {"state": "running"}):
                        r_conf = client.post(
                            "/api/start",
                            json={
                                "automation_id": "reel_upload_with_music",
                                "account_id": acc_ok,
                                "content_id": vid_id,
                                "audio_url": valid_audio,
                            },
                        )
                        self.assertEqual(r_conf.status_code, 409)
                        mock_thread.assert_not_called()

                    # Valid start dispatches exact device_serial & package_name
                    r_ok = client.post(
                        "/api/start",
                        json={
                            "automation_id": "reel_upload_with_music",
                            "account_id": acc_ok,
                            "content_id": vid_id,
                            "audio_url": valid_audio,
                            "caption": "Auto Center #test",
                            "hide_likes": False,
                            "hide_shares": True,
                        },
                    )
                    self.assertEqual(r_ok.status_code, 200)
                    mock_thread.assert_called_once()
                    t_args = mock_thread.call_args.kwargs.get("args")
                    self.assertEqual(t_args[0], valid_audio)
                    self.assertIn("auto_clip.mp4", t_args[1])
                    self.assertEqual(t_args[2], "Auto Center #test")
                    self.assertEqual(t_args[5], dev_online)
                    self.assertEqual(t_args[6], pkg_ok)

                    st = client.get("/api/status").get_json()
                    self.assertEqual(st["state"], "running")
                    self.assertEqual(st["automation_id"], "reel_upload_with_music")
                    self.assertEqual(st["device_serial"], dev_online)
                    self.assertEqual(st["package_name"], pkg_ok)

                # Verify run_workflow_async + InstagramReelUploader connect strictly to explicit serial
                mock_d = MagicMock()
                mock_d.serial = dev_online
                mock_d.info = {"productName": "LD7j"}
                with patch("adb.u2.connect", return_value=mock_d) as mock_u2_connect, \
                     patch.object(adb.InstagramReelUploader, "execute_workflow", return_value="https://www.instagram.com/reel/ABC/") as mock_exec:
                    app_module.run_workflow_async(
                        valid_audio, None, "Auto Caption", True, False, device_serial=dev_online, package_name=pkg_ok
                    )
                    mock_u2_connect.assert_called_once_with(dev_online)
                    mock_exec.assert_called_once()
                    self.assertEqual(app_module.workflow_status["state"], "success")
                    self.assertEqual(app_module.workflow_status["reel_url"], "https://www.instagram.com/reel/ABC/")
        finally:
            shutil.rmtree(tmp_uploads, ignore_errors=True)

    def test_automation_center_ui_and_shared_workflow_helpers(self):
        html_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "templates", "index.html"))
        with open(html_path, "r", encoding="utf-8") as fh:
            html = fh.read()

        required_markers = [
            "id=\"automationsView\"",
            "id=\"automationCatalog\"",
            "id=\"automationRunConfig\"",
            "id=\"automationReview\"",
            "id=\"automationActiveRun\"",
            "id=\"automationAccountSelect\"",
            "id=\"automationMediaModeSelect\"",
            "id=\"automationLibraryVideoSelect\"",
            "id=\"automationVideoFileInput\"",
            "id=\"automationAudioUrl\"",
            "id=\"automationCaption\"",
            "id=\"automationHideLikes\"",
            "id=\"automationHideShares\"",
            "id=\"automationStartBtn\"",
            "id=\"automationStopBtn\"",
            "id=\"automationRetryBtn\"",
            "id=\"automationStepsList\"",
            "id=\"automationResultCard\"",
            "id=\"automationResultUrlInput\"",
            "function buildWorkflowStartPayload(",
            "async function launchWorkflowRun(",
            "async function startSelectedAutomation(",
        ]
        for marker in required_markers:
            self.assertIn(marker, html, f"Missing Automation Center marker: {marker}")

        self.assertNotIn("Reusable multi-account workflow presets and schedules will arrive", html)
        for legacy in ("bg-slate-", "text-slate-", "border-slate-", "bg-pink-", "text-pink-"):
            self.assertNotIn(legacy, html, f"Disallowed hardcoded palette class: {legacy}")

    # -----------------------------------------------------------------------
    # Module 5: Campaigns & Persistent Tasks (Task 1 TDD)
    # -----------------------------------------------------------------------
    def test_campaign_crud_and_target_order_persist_after_init_db(self):
        acc1 = db.create_account(username="camp_user_1", device_serial="DEV1", package_name="com.ig.c1", db_path=self.test_db)
        acc2 = db.create_account(username="camp_user_2", device_serial="DEV1", package_name="com.ig.c2", db_path=self.test_db)
        vid1 = db.create_content_item(
            media_type="video",
            original_filename="clip1.mp4",
            mime_type="video/mp4",
            size_bytes=1024,
            storage_key="clip1_key.mp4",
            db_path=self.test_db,
        )
        vid2 = db.create_content_item(
            media_type="video",
            original_filename="clip2.mp4",
            mime_type="video/mp4",
            size_bytes=2048,
            storage_key="clip2_key.mp4",
            db_path=self.test_db,
        )

        created = db.create_campaign(
            name="Morning Launch",
            automation_id="reel_upload_with_music",
            audio_url="https://www.instagram.com/reels/audio/1656648626250751/",
            hide_likes=True,
            hide_shares=False,
            cooldown_seconds=5,
            failure_policy="continue",
            targets=[
                {"account_id": acc2, "content_id": vid2, "caption": "Second first"},
                {"account_id": acc1, "content_id": vid1, "caption": "First second"},
            ],
            db_path=self.test_db,
        )
        cid = created["id"]
        self.assertEqual(created["name"], "Morning Launch")
        self.assertEqual(created["automation_id"], "reel_upload_with_music")
        self.assertEqual(created["cooldown_seconds"], 5)
        self.assertEqual(created["failure_policy"], "continue")
        self.assertTrue(created["hide_likes"])
        self.assertFalse(created["hide_shares"])
        self.assertEqual([t["account_id"] for t in created["targets"]], [acc2, acc1])
        self.assertEqual([t["position"] for t in created["targets"]], [0, 1])
        self.assertEqual([t["content_id"] for t in created["targets"]], [vid2, vid1])
        self.assertEqual([t["caption"] for t in created["targets"]], ["Second first", "First second"])

        # Re-running init_db must preserve campaign & target ordering (idempotent migration)
        db.init_db(self.test_db)
        reloaded = db.get_campaign(cid, db_path=self.test_db)
        self.assertIsNotNone(reloaded)
        self.assertEqual([t["account_id"] for t in reloaded["targets"]], [acc2, acc1])
        self.assertEqual(reloaded["targets"][0]["account_username"], "camp_user_2")
        self.assertEqual(reloaded["targets"][0]["content_filename"], "clip2.mp4")

        # Update targets order, cooldown, failure policy
        updated = db.update_campaign(
            cid,
            name="Evening Launch",
            cooldown_seconds=12,
            failure_policy="pause",
            targets=[{"account_id": acc1, "content_id": vid1, "caption": "Only one"}],
            db_path=self.test_db,
        )
        self.assertEqual(updated["name"], "Evening Launch")
        self.assertEqual(updated["cooldown_seconds"], 12)
        self.assertEqual(updated["failure_policy"], "pause")
        self.assertEqual(len(updated["targets"]), 1)
        self.assertEqual(updated["targets"][0]["account_id"], acc1)
        self.assertEqual(updated["targets"][0]["position"], 0)

        listed = db.list_campaigns(db_path=self.test_db)
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["target_count"], 1)

        # Delete campaign succeeds when no runs reference it
        self.assertTrue(db.delete_campaign(cid, db_path=self.test_db))
        self.assertIsNone(db.get_campaign(cid, db_path=self.test_db))

    def test_campaign_target_video_only_and_validation(self):
        acc1 = db.create_account(username="val_user_1", db_path=self.test_db)
        acc2 = db.create_account(username="val_user_2", db_path=self.test_db)
        vid1 = db.create_content_item(
            media_type="video",
            original_filename="ok.mp4",
            mime_type="video/mp4",
            size_bytes=500,
            storage_key="ok_key.mp4",
            db_path=self.test_db,
        )
        img1 = db.create_content_item(
            media_type="image",
            original_filename="pic.png",
            mime_type="image/png",
            size_bytes=500,
            storage_key="pic_key.png",
            db_path=self.test_db,
        )

        # Reject image content item in reel campaign target
        with self.assertRaises(ValueError):
            db.create_campaign(
                name="Bad Media",
                audio_url="https://www.instagram.com/reels/audio/1656648626250751/",
                targets=[{"account_id": acc1, "content_id": img1, "caption": "nope"}],
                db_path=self.test_db,
            )

        # Reject duplicate account_id in same campaign, missing target -> no partial campaign saved
        with self.assertRaises(ValueError):
            db.create_campaign(
                name="Dup Accounts",
                audio_url="https://www.instagram.com/reels/audio/1656648626250751/",
                targets=[
                    {"account_id": acc1, "content_id": vid1, "caption": "a"},
                    {"account_id": acc1, "content_id": vid1, "caption": "b"},
                ],
                db_path=self.test_db,
            )
        with self.assertRaises(ValueError):
            db.create_campaign(
                name="Missing Second Account",
                audio_url="https://www.instagram.com/reels/audio/1656648626250751/",
                targets=[
                    {"account_id": acc1, "content_id": vid1, "caption": "a"},
                    {"account_id": 99999, "content_id": vid1, "caption": "b"},
                ],
                db_path=self.test_db,
            )
        # Reject out-of-range cooldown & unsupported automation_id & failure_policy
        with self.assertRaises(ValueError):
            db.create_campaign(
                name="Bad Cooldown",
                audio_url="https://www.instagram.com/reels/audio/1656648626250751/",
                cooldown_seconds=31,
                targets=[{"account_id": acc1, "content_id": vid1, "caption": "a"}],
                db_path=self.test_db,
            )
        with self.assertRaises(ValueError):
            db.create_campaign(
                name="Bad Policy",
                audio_url="https://www.instagram.com/reels/audio/1656648626250751/",
                failure_policy="ignore",
                targets=[{"account_id": acc2, "content_id": vid1, "caption": "a"}],
                db_path=self.test_db,
            )
        with self.assertRaises(ValueError):
            db.create_campaign(
                name="Bad Automation",
                automation_id="profile_visit",
                audio_url="https://www.instagram.com/reels/audio/1656648626250751/",
                targets=[{"account_id": acc2, "content_id": vid1, "caption": "a"}],
                db_path=self.test_db,
            )
        # Zero campaigns persisted after failed validations
        self.assertEqual(db.list_campaigns(db_path=self.test_db), [])

    def test_campaign_references_block_account_or_content_delete(self):
        acc1 = db.create_account(username="ref_user_1", db_path=self.test_db)
        vid1 = db.create_content_item(
            media_type="video",
            original_filename="ref_clip.mp4",
            mime_type="video/mp4",
            size_bytes=1200,
            storage_key="ref_clip_key.mp4",
            db_path=self.test_db,
        )
        camp = db.create_campaign(
            name="Protected Assets",
            audio_url="https://www.instagram.com/reels/audio/1656648626250751/",
            targets=[{"account_id": acc1, "content_id": vid1, "caption": "hold"}],
            db_path=self.test_db,
        )

        with self.assertRaises(db.ResourceInUseError):
            db.delete_account(acc1, db_path=self.test_db)
        self.assertIsNotNone(db.get_account_by_id(acc1, db_path=self.test_db))

        with self.assertRaises(db.ResourceInUseError):
            db.delete_content_item(vid1, db_path=self.test_db)
        self.assertIsNotNone(db.get_content_item(vid1, db_path=self.test_db))

        # Removing campaign frees account & content for deletion
        self.assertTrue(db.delete_campaign(camp["id"], db_path=self.test_db))
        self.assertTrue(db.delete_account(acc1, db_path=self.test_db))
        self.assertIsNotNone(db.delete_content_item(vid1, db_path=self.test_db))

    # -----------------------------------------------------------------------
    # Module 5: Persistent Runs, Tasks, Events & Crash Recovery (Task 2 TDD)
    # -----------------------------------------------------------------------
    def test_persistent_run_tasks_events_and_transitions(self):
        acc1 = db.create_account(username="run_u1", device_serial="SER_1", package_name="com.ig.r1", db_path=self.test_db)
        acc2 = db.create_account(username="run_u2", device_serial="SER_1", package_name="com.ig.r2", db_path=self.test_db)
        vid1 = db.create_content_item(
            media_type="video",
            original_filename="run_v1.mp4",
            mime_type="video/mp4",
            size_bytes=512,
            storage_key="run_v1_k.mp4",
            db_path=self.test_db,
        )
        vid2 = db.create_content_item(
            media_type="video",
            original_filename="run_v2.mp4",
            mime_type="video/mp4",
            size_bytes=512,
            storage_key="run_v2_k.mp4",
            db_path=self.test_db,
        )
        camp = db.create_campaign(
            name="Persisted Run Campaign",
            audio_url="https://www.instagram.com/reels/audio/1656648626250751/",
            cooldown_seconds=4,
            failure_policy="pause",
            targets=[
                {"account_id": acc1, "content_id": vid1, "caption": "Cap 1"},
                {"account_id": acc2, "content_id": vid2, "caption": "Cap 2"},
            ],
            db_path=self.test_db,
        )

        run = db.create_campaign_run(camp["id"], db_path=self.test_db)
        run_id = run["id"]
        self.assertEqual(run["status"], "queued")
        self.assertEqual(run["campaign_name"], "Persisted Run Campaign")
        self.assertEqual([t["sequence_number"] for t in run["tasks"]], [1, 2])
        self.assertEqual([t["account_username"] for t in run["tasks"]], ["run_u1", "run_u2"])
        self.assertEqual([t["status"] for t in run["tasks"]], ["pending", "pending"])

        t1_id = run["tasks"][0]["id"]
        t2_id = run["tasks"][1]["id"]

        # Legal run & task transitions + ordered task events
        db.transition_run_status(run_id, "running", current_task_id=t1_id, db_path=self.test_db)
        db.transition_task_state(t1_id, "running", current_step=1, current_step_label="Open Audio Link", db_path=self.test_db)
        e1 = db.append_task_event(t1_id, "info", "Step 1 started", step=1, db_path=self.test_db)
        e2 = db.append_task_event(t1_id, "success", "Step 9 shared", step=9, db_path=self.test_db)
        self.assertLess(e1["id"], e2["id"])

        db.transition_task_state(
            t1_id,
            "closing_app",
            current_step=11,
            reel_url="https://www.instagram.com/reel/XYZ/",
            db_path=self.test_db,
        )
        db.transition_task_state(t1_id, "cooldown", cleanup_status="closed", db_path=self.test_db)
        db.transition_task_state(t1_id, "completed", db_path=self.test_db)

        # Illegal transitions must raise ValueError
        with self.assertRaises(ValueError):
            db.transition_task_state(t1_id, "running", db_path=self.test_db)
        with self.assertRaises(ValueError):
            db.transition_task_state(t2_id, "completed", db_path=self.test_db)

        db.transition_run_status(run_id, "completed", current_task_id=None, db_path=self.test_db)
        with self.assertRaises(ValueError):
            db.transition_run_status(run_id, "running", db_path=self.test_db)

        # Filtering & pagination on list_tasks and get_task_detail
        filtered, total = db.list_tasks(run_id=run_id, status="completed", limit=10, offset=0, db_path=self.test_db)
        self.assertEqual(total, 1)
        self.assertEqual(filtered[0]["id"], t1_id)
        detail = db.get_task_detail(t1_id, db_path=self.test_db)
        self.assertEqual([ev["message"] for ev in detail["events"]], ["Step 1 started", "Step 9 shared"])

        # Campaign delete blocked once run history exists
        with self.assertRaises(db.ResourceInUseError):
            db.delete_campaign(camp["id"], db_path=self.test_db)

    def test_init_db_crash_recovery_marks_active_runs_and_tasks_interrupted(self):
        acc1 = db.create_account(username="crash_u1", device_serial="SER_C", package_name="com.ig.cr1", db_path=self.test_db)
        vid1 = db.create_content_item(
            media_type="video",
            original_filename="crash_v1.mp4",
            mime_type="video/mp4",
            size_bytes=512,
            storage_key="crash_v1_k.mp4",
            db_path=self.test_db,
        )
        camp = db.create_campaign(
            name="Crash Recovery Test",
            audio_url="https://www.instagram.com/reels/audio/1656648626250751/",
            targets=[{"account_id": acc1, "content_id": vid1, "caption": "crash"}],
            db_path=self.test_db,
        )
        run = db.create_campaign_run(camp["id"], db_path=self.test_db)
        t1_id = run["tasks"][0]["id"]
        db.transition_run_status(run["id"], "running", current_task_id=t1_id, db_path=self.test_db)
        db.transition_task_state(t1_id, "running", current_step=9, current_step_label="Click Share", db_path=self.test_db)

        # Simulate process restart calling init_db()
        db.init_db(self.test_db)

        recovered_run = db.get_campaign_run(run["id"], db_path=self.test_db)
        self.assertEqual(recovered_run["status"], "interrupted")
        self.assertFalse(db.has_active_campaign_run(db_path=self.test_db))

        recovered_task = db.get_task_detail(t1_id, db_path=self.test_db)
        self.assertEqual(recovered_task["status"], "interrupted")
        self.assertTrue(recovered_task["retry_requires_review"])
        self.assertIn("restarted", recovered_task["error_message"].lower())
        self.assertTrue(any("interrupted" in ev["message"].lower() for ev in recovered_task["events"]))

    # -----------------------------------------------------------------------
    # Module 5: Campaign APIs, Preflight & In-Use 409 (Task 3 TDD)
    # -----------------------------------------------------------------------
    def test_campaign_crud_preflight_and_in_use_http_409_apis(self):
        import shutil
        import tempfile
        import app as app_module

        tmp_content_dir = tempfile.mkdtemp(prefix="instoper_camp_api_")
        try:
            dev_online = "CAMP_DEV_ONLINE"
            pkg_ok = "com.instagram.clone_camp"
            acc_id = db.create_account(
                username="camp_api_user",
                device_serial=dev_online,
                package_name=pkg_ok,
                db_path=self.test_db,
            )
            acc_unbound = db.create_account(
                username="camp_api_unbound",
                db_path=self.test_db,
            )
            db.upsert_clone(dev_online, pkg_ok, app_name="Clone Camp", is_confirmed=True, db_path=self.test_db)

            vid_key = "camp_clip_test.mp4"
            with open(os.path.join(tmp_content_dir, vid_key), "wb") as fh:
                fh.write(b"FAKEVIDEO")
            vid_id = db.create_content_item(
                media_type="video",
                original_filename="camp_clip.mp4",
                mime_type="video/mp4",
                size_bytes=9,
                storage_key=vid_key,
                db_path=self.test_db,
            )
            img_key = "camp_img_test.png"
            with open(os.path.join(tmp_content_dir, img_key), "wb") as fh:
                fh.write(b"FAKEIMG")
            img_id = db.create_content_item(
                media_type="image",
                original_filename="camp_img.png",
                mime_type="image/png",
                size_bytes=7,
                storage_key=img_key,
                db_path=self.test_db,
            )

            with patch.object(db, "DEFAULT_DB_PATH", self.test_db), \
                 patch.object(app_module, "_get_content_upload_dir", return_value=tmp_content_dir), \
                 patch.object(package_scanner, "DEFAULT_DB_PATH", self.test_db, create=True):
                app_module.app.testing = True
                client = app_module.app.test_client()

                # Reject image target in POST /api/campaigns
                r_bad = client.post("/api/campaigns", json={
                    "name": "Bad Media Campaign",
                    "audio_url": "https://www.instagram.com/reels/audio/1656648626250751/",
                    "targets": [{"account_id": acc_id, "content_id": img_id, "caption": "bad"}],
                })
                self.assertEqual(r_bad.status_code, 400)

                # Create valid campaign
                r_create = client.post("/api/campaigns", json={
                    "name": "Q4 Brand Launch",
                    "audio_url": "https://www.instagram.com/reels/audio/1656648626250751/",
                    "hide_likes": True,
                    "hide_shares": False,
                    "cooldown_seconds": 6,
                    "failure_policy": "continue",
                    "targets": [{"account_id": acc_id, "content_id": vid_id, "caption": "Caption 1 #launch"}],
                })
                self.assertEqual(r_create.status_code, 201)
                c_data = r_create.get_json()["campaign"]
                cid = c_data["id"]
                dumped = json.dumps(c_data)
                self.assertNotIn("camp_clip_test.mp4", dumped)
                self.assertNotIn(tmp_content_dir, dumped)

                # Reload via fresh client persists saved campaign
                fresh_client = app_module.app.test_client()
                r_list = fresh_client.get("/api/campaigns")
                self.assertEqual(r_list.status_code, 200)
                self.assertEqual(len(r_list.get_json()["campaigns"]), 1)
                self.assertEqual(r_list.get_json()["campaigns"][0]["name"], "Q4 Brand Launch")

                # Deleting referenced account or content returns HTTP 409
                r_del_acc = client.delete(f"/api/accounts/{acc_id}")
                self.assertEqual(r_del_acc.status_code, 409)
                r_del_vid = client.delete(f"/api/content/{vid_id}")
                self.assertEqual(r_del_vid.status_code, 409)

                # Preflight succeeds when online & installed & file exists
                def mock_adb(cmd, device_serial=None, timeout=8):
                    if cmd == ["shell", "pm", "path", pkg_ok] and device_serial == dev_online:
                        return f"package:/data/app/{pkg_ok}/base.apk\n"
                    return ""

                with patch("package_scanner.list_connected_devices", return_value=[{"serial": dev_online, "status": "device"}]), \
                     patch("package_scanner.run_adb_command", side_effect=mock_adb):
                    r_pre_ok = client.post(f"/api/campaigns/{cid}/preflight")
                    self.assertEqual(r_pre_ok.status_code, 200)
                    pre_ok = r_pre_ok.get_json()
                    self.assertTrue(pre_ok["ready"])
                    self.assertEqual(pre_ok["blockers"], [])

                # Preflight reports blockers when device offline & missing file
                os.remove(os.path.join(tmp_content_dir, vid_key))
                with patch("package_scanner.list_connected_devices", return_value=[]):
                    r_pre_fail = client.post(f"/api/campaigns/{cid}/preflight")
                    self.assertEqual(r_pre_fail.status_code, 200)
                    pre_fail = r_pre_fail.get_json()
                    self.assertFalse(pre_fail["ready"])
                    msg_blob = " ".join(b["message"] for b in pre_fail["blockers"]).lower()
                    self.assertIn("offline", msg_blob)
                    self.assertIn("missing", msg_blob)
                    self.assertNotIn(tmp_content_dir.lower(), msg_blob)

                # Patch unbound target triggers blocker too
                r_patch = client.patch(f"/api/campaigns/{cid}", json={
                    "targets": [{"account_id": acc_unbound, "content_id": vid_id, "caption": "unbound"}],
                })
                self.assertEqual(r_patch.status_code, 200)
                with patch("package_scanner.list_connected_devices", return_value=[]):
                    r_pre_unbound = client.post(f"/api/campaigns/{cid}/preflight")
                    self.assertFalse(r_pre_unbound.get_json()["ready"])
        finally:
            shutil.rmtree(tmp_content_dir, ignore_errors=True)

    def test_campaign_sequential_worker_cleanup_policies_and_locking(self):
        import shutil
        import tempfile
        import app as app_module
        import adb as adb_module

        tmp_content_dir = tempfile.mkdtemp(prefix="instoper_camp_worker_")
        try:
            dev_serial = "SEQ_DEV_01"
            pkg_a = "com.instagram.seq_a"
            pkg_b = "com.instagram.seq_b"
            acc_a = db.create_account(username="seq_alpha", device_serial=dev_serial, package_name=pkg_a, db_path=self.test_db)
            acc_b = db.create_account(username="seq_beta", device_serial=dev_serial, package_name=pkg_b, db_path=self.test_db)
            db.upsert_clone(dev_serial, pkg_a, app_name="Seq A", is_confirmed=True, db_path=self.test_db)
            db.upsert_clone(dev_serial, pkg_b, app_name="Seq B", is_confirmed=True, db_path=self.test_db)

            for fname in ("seq_a.mp4", "seq_b.mp4"):
                with open(os.path.join(tmp_content_dir, fname), "wb") as fh:
                    fh.write(b"MP4DATA")
            vid_a = db.create_content_item(
                media_type="video", original_filename="seq_a.mp4", mime_type="video/mp4",
                size_bytes=7, storage_key="seq_a.mp4", db_path=self.test_db,
            )
            vid_b = db.create_content_item(
                media_type="video", original_filename="seq_b.mp4", mime_type="video/mp4",
                size_bytes=7, storage_key="seq_b.mp4", db_path=self.test_db,
            )

            def mock_adb(cmd, device_serial=None, timeout=8):
                if len(cmd) == 4 and cmd[:3] == ["shell", "pm", "path"] and device_serial == dev_serial:
                    return f"package:/data/app/{cmd[3]}/base.apk\n"
                return ""

            with patch.object(db, "DEFAULT_DB_PATH", self.test_db), \
                 patch.object(app_module, "_get_content_upload_dir", return_value=tmp_content_dir), \
                 patch("package_scanner.list_connected_devices", return_value=[{"serial": dev_serial, "status": "device"}]), \
                 patch("package_scanner.run_adb_command", side_effect=mock_adb):
                app_module.app.testing = True
                client = app_module.app.test_client()

                # 1. Mutual exclusion with /api/start when legacy workflow_status is running
                camp = client.post("/api/campaigns", json={
                    "name": "Sequential Test",
                    "audio_url": "https://www.instagram.com/reels/audio/1656648626250751/",
                    "cooldown_seconds": 1,
                    "failure_policy": "pause",
                    "targets": [
                        {"account_id": acc_a, "content_id": vid_a, "caption": "Alpha Cap"},
                        {"account_id": acc_b, "content_id": vid_b, "caption": "Beta Cap"},
                    ],
                }).get_json()["campaign"]
                cid = camp["id"]

                app_module.workflow_status["state"] = "running"
                try:
                    r_locked = client.post(f"/api/campaigns/{cid}/runs")
                    self.assertEqual(r_locked.status_code, 409)
                finally:
                    app_module.workflow_status["state"] = "idle"

                # 2. Verify strict order: A execute -> A close -> cooldown -> B execute -> B close
                events = []

                class FakeUploader:
                    def __init__(self, device_serial=None, package_name=None, log_callback=None):
                        self.device_serial = device_serial
                        self.package_name = package_name
                        self.log_callback = log_callback
                        self.is_cancelled = False

                    def execute_workflow(self, audio_url, video_path=None, caption="", hide_likes=True, hide_shares=True):
                        events.append(("exec_start", self.package_name, caption, os.path.basename(video_path)))
                        if self.log_callback:
                            self.log_callback("info", "Step 1", 1)
                            self.log_callback("info", "Step 9", 9)
                        events.append(("exec_done", self.package_name))
                        return f"https://www.instagram.com/reel/{self.package_name}/"

                def fake_close(device_serial=None, package_name=None, db_path=None):
                    events.append(("close", package_name))
                    return {"status": "closed", "device_serial": device_serial, "package_name": package_name}

                def fake_cooldown_wait(timeout=None):
                    events.append(("cooldown", timeout))
                    return False

                with patch.object(adb_module, "InstagramReelUploader", side_effect=FakeUploader), \
                     patch.object(app_module.profile_capture, "close_profile_app", side_effect=fake_close), \
                     patch.object(app_module.campaign_cancel_event, "wait", side_effect=fake_cooldown_wait):
                    r_start = client.post(f"/api/campaigns/{cid}/runs")
                    self.assertEqual(r_start.status_code, 202)
                    run_id = r_start.get_json()["run"]["id"]

                    # Wait briefly for worker completion
                    for _ in range(50):
                        cur = client.get(f"/api/campaign-runs/{run_id}").get_json()["run"]
                        if cur["status"] not in ("queued", "running"):
                            break
                        time.sleep(0.05)

                final_run = client.get(f"/api/campaign-runs/{run_id}").get_json()["run"]
                self.assertEqual(final_run["status"], "completed")
                self.assertEqual(events, [
                    ("exec_start", pkg_a, "Alpha Cap", "seq_a.mp4"),
                    ("exec_done", pkg_a),
                    ("close", pkg_a),
                    ("cooldown", 1),
                    ("exec_start", pkg_b, "Beta Cap", "seq_b.mp4"),
                    ("exec_done", pkg_b),
                    ("close", pkg_b),
                ])
                self.assertEqual(final_run["tasks"][0]["status"], "completed")
                self.assertEqual(final_run["tasks"][0]["cleanup_status"], "closed")
                self.assertEqual(final_run["tasks"][1]["reel_url"], f"https://www.instagram.com/reel/{pkg_b}/")

                # 3. Stop failure on A must pause run and NEVER start B
                events.clear()
                def fake_close_fail(device_serial=None, package_name=None, db_path=None):
                    events.append(("close_fail", package_name))
                    raise RuntimeError(f"'{package_name}' is still running")

                with patch.object(adb_module, "InstagramReelUploader", side_effect=FakeUploader), \
                     patch.object(app_module.profile_capture, "close_profile_app", side_effect=fake_close_fail):
                    r_stop_fail = client.post(f"/api/campaigns/{cid}/runs")
                    self.assertEqual(r_stop_fail.status_code, 202)
                    run_id_2 = r_stop_fail.get_json()["run"]["id"]
                    for _ in range(50):
                        cur = client.get(f"/api/campaign-runs/{run_id_2}").get_json()["run"]
                        if cur["status"] not in ("queued", "running"):
                            break
                        time.sleep(0.05)

                paused_run = client.get(f"/api/campaign-runs/{run_id_2}").get_json()["run"]
                self.assertEqual(paused_run["status"], "paused")
                self.assertEqual(paused_run["tasks"][0]["status"], "completed")
                self.assertIn("still running", paused_run["tasks"][0]["cleanup_status"])
                self.assertEqual(paused_run["tasks"][1]["status"], "pending")
                self.assertFalse(any(e[1] == pkg_b for e in events))

                # Continuing paused run runs remaining pending Task B only
                events.clear()
                with patch.object(adb_module, "InstagramReelUploader", side_effect=FakeUploader), \
                     patch.object(app_module.profile_capture, "close_profile_app", side_effect=fake_close), \
                     patch.object(app_module.campaign_cancel_event, "wait", side_effect=fake_cooldown_wait):
                    r_cont = client.post(f"/api/campaign-runs/{run_id_2}/continue")
                    self.assertEqual(r_cont.status_code, 202)
                    for _ in range(50):
                        cur = client.get(f"/api/campaign-runs/{run_id_2}").get_json()["run"]
                        if cur["status"] not in ("queued", "running"):
                            break
                        time.sleep(0.05)

                resumed_run = client.get(f"/api/campaign-runs/{run_id_2}").get_json()["run"]
                self.assertEqual(resumed_run["status"], "completed")
                self.assertEqual(events, [
                    ("exec_start", pkg_b, "Beta Cap", "seq_b.mp4"),
                    ("exec_done", pkg_b),
                    ("close", pkg_b),
                ])

                # 4. Failure after Share (step 9) marks retry_requires_review=True; Pause stops B; Continue runs B
                class FailAfterShareUploader:
                    def __init__(self, device_serial=None, package_name=None, log_callback=None):
                        self.package_name = package_name
                        self.log_callback = log_callback
                        self.is_cancelled = False

                    def execute_workflow(self, *args, **kwargs):
                        if self.package_name == pkg_a:
                            if self.log_callback:
                                self.log_callback("info", "Tapping Share button", 9)
                            raise RuntimeError("Link copy timed out after share")
                        return "https://www.instagram.com/reel/RECOVERED_B/"

                with patch.object(adb_module, "InstagramReelUploader", side_effect=FailAfterShareUploader), \
                     patch.object(app_module.profile_capture, "close_profile_app", side_effect=fake_close):
                    run_id_3 = client.post(f"/api/campaigns/{cid}/runs").get_json()["run"]["id"]
                    for _ in range(50):
                        cur = client.get(f"/api/campaign-runs/{run_id_3}").get_json()["run"]
                        if cur["status"] not in ("queued", "running"):
                            break
                        time.sleep(0.05)

                failed_paused = client.get(f"/api/campaign-runs/{run_id_3}").get_json()["run"]
                self.assertEqual(failed_paused["status"], "paused")
                t_a_id = failed_paused["tasks"][0]["id"]
                self.assertEqual(failed_paused["tasks"][0]["status"], "failed")
                self.assertTrue(failed_paused["tasks"][0]["retry_requires_review"])
                self.assertEqual(failed_paused["tasks"][1]["status"], "pending")

                # Cancelling a paused run cancels remaining pending task
                r_cancel_paused = client.post(f"/api/campaign-runs/{run_id_3}/cancel")
                self.assertEqual(r_cancel_paused.status_code, 200)
                cancelled_run = r_cancel_paused.get_json()["run"]
                self.assertEqual(cancelled_run["status"], "cancelled")
                self.assertEqual(cancelled_run["tasks"][1]["status"], "cancelled")

                # Explicit task Retry creates a NEW single-task run leaving history intact
                with patch.object(adb_module, "InstagramReelUploader", side_effect=FakeUploader), \
                     patch.object(app_module.profile_capture, "close_profile_app", side_effect=fake_close):
                    r_retry = client.post(f"/api/tasks/{t_a_id}/retry")
                    self.assertEqual(r_retry.status_code, 202)
                    retry_run_id = r_retry.get_json()["run"]["id"]
                    self.assertNotEqual(retry_run_id, run_id_3)
                    for _ in range(50):
                        cur = client.get(f"/api/campaign-runs/{retry_run_id}").get_json()["run"]
                        if cur["status"] not in ("queued", "running"):
                            break
                        time.sleep(0.05)
                retry_final = client.get(f"/api/campaign-runs/{retry_run_id}").get_json()["run"]
                self.assertEqual(retry_final["status"], "completed")
                self.assertEqual(len(retry_final["tasks"]), 1)
                self.assertEqual(retry_final["tasks"][0]["account_id"], acc_a)
                # Original failed task remains preserved in historical run
                self.assertEqual(client.get(f"/api/tasks/{t_a_id}").get_json()["task"]["status"], "failed")

                # 5. failure_policy == 'continue' runs Task B cleanly after Task A fails + closes
                camp_cont = client.post("/api/campaigns", json={
                    "name": "Continue On Fail Test",
                    "audio_url": "https://www.instagram.com/reels/audio/1656648626250751/",
                    "cooldown_seconds": 1,
                    "failure_policy": "continue",
                    "targets": [
                        {"account_id": acc_a, "content_id": vid_a, "caption": "Alpha fail"},
                        {"account_id": acc_b, "content_id": vid_b, "caption": "Beta run"},
                    ],
                }).get_json()["campaign"]
                cid_cont = camp_cont["id"]

                class FailFirstPassSecond:
                    def __init__(self, device_serial=None, package_name=None, log_callback=None):
                        self.package_name = package_name
                        self.log_callback = log_callback
                        self.is_cancelled = False

                    def execute_workflow(self, *args, **kwargs):
                        if self.package_name == pkg_a:
                            raise RuntimeError("Pre-share UI hiccup")
                        return "https://www.instagram.com/reel/BETA_CONT/"

                with patch.object(adb_module, "InstagramReelUploader", side_effect=FailFirstPassSecond), \
                     patch.object(app_module.profile_capture, "close_profile_app", side_effect=fake_close), \
                     patch.object(app_module.campaign_cancel_event, "wait", side_effect=fake_cooldown_wait):
                    run_cont_id = client.post(f"/api/campaigns/{cid_cont}/runs").get_json()["run"]["id"]
                    for _ in range(50):
                        cur = client.get(f"/api/campaign-runs/{run_cont_id}").get_json()["run"]
                        if cur["status"] not in ("queued", "running"):
                            break
                        time.sleep(0.05)

                cont_final = client.get(f"/api/campaign-runs/{run_cont_id}").get_json()["run"]
                self.assertEqual(cont_final["status"], "failed")
                self.assertEqual(cont_final["tasks"][0]["status"], "failed")
                self.assertFalse(cont_final["tasks"][0]["retry_requires_review"])
                self.assertEqual(cont_final["tasks"][1]["status"], "completed")
                self.assertEqual(cont_final["tasks"][1]["reel_url"], "https://www.instagram.com/reel/BETA_CONT/")

                # Deleting or editing while run history / active run exists returns 409; /api/start returns 409 while campaign active
                r_del_hist = client.delete(f"/api/campaigns/{cid_cont}")
                self.assertEqual(r_del_hist.status_code, 409)

                gate = threading.Event()
                class BlockingUploader:
                    def __init__(self, device_serial=None, package_name=None, log_callback=None):
                        self.is_cancelled = False

                    def execute_workflow(self, *args, **kwargs):
                        gate.wait(timeout=3.0)
                        if self.is_cancelled:
                            raise InterruptedError("Cancelled during block")
                        return "https://www.instagram.com/reel/BLOCKED/"

                with patch.object(adb_module, "InstagramReelUploader", side_effect=BlockingUploader), \
                     patch.object(app_module.profile_capture, "close_profile_app", side_effect=fake_close):
                    run_block_id = client.post(f"/api/campaigns/{cid_cont}/runs").get_json()["run"]["id"]
                    time.sleep(0.08)
                    # Editing active campaign returns 409
                    self.assertEqual(client.patch(f"/api/campaigns/{cid_cont}", json={"name": "Nope"}).status_code, 409)
                    # Starting legacy /api/start returns 409
                    self.assertEqual(client.post("/api/start", json={"audio_url": "https://www.instagram.com/reels/audio/123/"}).status_code, 409)
                    # Cancel mid-task
                    r_can = client.post(f"/api/campaign-runs/{run_block_id}/cancel")
                    self.assertEqual(r_can.status_code, 200)
                    gate.set()
                    for _ in range(50):
                        cur = client.get(f"/api/campaign-runs/{run_block_id}").get_json()["run"]
                        if cur["status"] not in ("queued", "running"):
                            break
                        time.sleep(0.05)
                    fin_can = client.get(f"/api/campaign-runs/{run_block_id}").get_json()["run"]
                    self.assertEqual(fin_can["status"], "cancelled")
                    self.assertEqual(fin_can["tasks"][1]["status"], "cancelled")
        finally:
            shutil.rmtree(tmp_content_dir, ignore_errors=True)

    def test_campaigns_and_persistent_tasks_ui_markers(self):
        html_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "templates", "index.html"))
        with open(html_path, "r", encoding="utf-8") as fh:
            html = fh.read()

        required_markers = [
            "id=\"campaignsViewHeading\"",
            "id=\"savedCampaignsList\"",
            "id=\"campaignNameInput\"",
            "id=\"campaignCooldownInput\"",
            "id=\"campaignFailurePolicySelect\"",
            "id=\"campaignAccountSearch\"",
            "id=\"campaignAccountCategoryFilter\"",
            "id=\"campaignAccountTagFilter\"",
            "id=\"campaignTargetRows\"",
            "id=\"campaignPreflightBox\"",
            "id=\"saveCampaignBtn\"",
            "id=\"preflightCampaignBtn\"",
            "id=\"startBtn\"",
            "id=\"reelPickerCategoryFilter\"",
            "id=\"reelPickerTagFilter\"",
            "id=\"tasksFilterCampaign\"",
            "id=\"tasksFilterAccount\"",
            "id=\"tasksFilterStatus\"",
            "id=\"tasksRunsList\"",
            "id=\"tasksHistoryTableBody\"",
            "id=\"taskDetailModal\"",
            "id=\"taskDetailEventsList\"",
            "function saveCampaignDraft(",
            "function launchCurrentCampaign(",
            "function openTaskDetailModal(",
        ]
        for marker in required_markers:
            self.assertIn(marker, html, f"Missing Campaign/Tasks UI marker: {marker}")

        self.assertNotIn("Coming later", html)
        self.assertNotIn("Reel Studio", html)
        self.assertNotIn("truncated for diff preview", html)
        self.assertIn("</script>", html[-100:])
        self.assertIn("</html>", html[-30:])
        for legacy in ("bg-slate-", "text-slate-", "border-slate-", "bg-pink-", "text-pink-"):
            self.assertNotIn(legacy, html, f"Disallowed hardcoded palette class: {legacy}")


class _MockUiElement:
    def __init__(self, key, matcher_fn, info_fn=None):
        self._key = key
        self._matcher_fn = matcher_fn
        self._info_fn = info_fn

    @property
    def exists(self):
        return bool(self._matcher_fn(self._key))

    @property
    def info(self):
        if self._info_fn:
            return self._info_fn(self._key)
        return {"contentDescription": f"mock:{self._key}", "text": "", "bounds": {"left": 10, "top": 20, "right": 100, "bottom": 120}}

    def __getitem__(self, idx):
        return self

    def center(self):
        return (55, 70)


class _MockU2Device:
    def __init__(self, fg_fn, shell_fn, matcher_fn, info_fn=None):
        self.serial = "MOCK_SERIAL_1"
        self.info = {"productName": "MockTECNO", "displayHeight": 1600, "displayWidth": 720}
        self._fg_fn = fg_fn
        self._shell_fn = shell_fn
        self._matcher_fn = matcher_fn
        self._info_fn = info_fn
        self.pushed_files = []
        self.shell_cmds = []
        self.pressed_keys = []

    def press(self, key):
        self.pressed_keys.append(key)

    def app_current(self):
        return self._fg_fn()

    def shell(self, cmd):
        self.shell_cmds.append(cmd)
        return self._shell_fn(cmd)

    def push(self, local_path, remote_path):
        self.pushed_files.append((local_path, remote_path))

    def dump_hierarchy(self):
        return "<hierarchy><node text='Camera' /></hierarchy>"

    def click(self, x, y):
        pass

    def swipe(self, *args, **kwargs):
        pass

    def __call__(self, **kwargs):
        key = tuple(sorted(kwargs.items()))
        return _MockUiElement(key, self._matcher_fn, self._info_fn)


class TestReelRunnerFixes(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="test_reel_fixes_")
        self.test_db = os.path.join(self.tmp_dir, "test.db")
        db.init_db(self.test_db)

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_audio_url_http_rejected_in_preflight_and_normalized_in_runner(self):
        import adb as adb_module
        import app as app_module

        # 1. Validation & preflight reject plain http:// with explicit HTTPS guidance
        with self.assertRaises(ValueError) as ctx:
            db.validate_instagram_audio_url("http://www.instagram.com/reels/audio/1656648626250751/", require_nonempty=True)
        self.assertIn("https://", str(ctx.exception))

        # Preflight surfaces clear invalid_audio_url blocker on any stored plain http:// campaign
        acc_id = db.create_account(username="http_test", device_serial="D1", package_name="com.ig.clone1", db_path=self.test_db)
        vid_id = db.create_content_item(
            media_type="video", original_filename="c.mp4", mime_type="video/mp4",
            size_bytes=10, storage_key="c.mp4", db_path=self.test_db,
        )
        with open(os.path.join(self.tmp_dir, "c.mp4"), "wb") as fh:
            fh.write(b"VIDEO")

        with patch.object(db, "DEFAULT_DB_PATH", self.test_db), \
             patch.object(app_module, "_get_content_upload_dir", return_value=self.tmp_dir), \
             patch("package_scanner.list_connected_devices", return_value=[{"serial": "D1", "status": "device"}]), \
             patch("package_scanner.run_adb_command", return_value="package:/data/app/base.apk\n"):
            app_module.app.testing = True
            client = app_module.app.test_client()
            resp = client.post("/api/campaigns", json={
                "name": "HTTP Reject",
                "audio_url": "http://www.instagram.com/reels/audio/1656648626250751/",
                "targets": [{"account_id": acc_id, "content_id": vid_id}],
            })
            self.assertEqual(resp.status_code, 400)
            self.assertIn("https://", resp.get_json()["error"])

            preflight = app_module._run_campaign_preflight({
                "automation_id": app_module.BUILTIN_AUTOMATION_ID,
                "audio_url": "http://www.instagram.com/reels/audio/1656648626250751/",
                "targets": [{"account_id": acc_id, "content_id": vid_id}],
            }, include_lock_check=False)
            self.assertFalse(preflight["ready"])
            codes = [b["code"] for b in preflight["blockers"]]
            self.assertIn("invalid_audio_url", codes)

        # 2. Direct runner normalizes http:// to https:// before sending intent
        self.assertEqual(
            adb_module.InstagramReelUploader.normalize_audio_url("http://www.instagram.com/reels/audio/1656648626250751/"),
            "https://www.instagram.com/reels/audio/1656648626250751/",
        )

    def test_camera_new_reel_not_mistaken_for_gallery_grid(self):
        import adb as adb_module

        # Only text='New reel' is visible (like on camera creation UI); NO thumbnail or recycler grid exists
        def matcher(key):
            return dict(key).get("text") == "New reel"

        mock_dev = _MockU2Device(
            fg_fn=lambda: {"package": "com.instagram.android.clone2", "activity": ".CameraActivity"},
            shell_fn=lambda cmd: ("", 0),
            matcher_fn=matcher,
        )
        logs = []
        with patch.object(adb_module.u2, "connect", return_value=mock_dev):
            uploader = adb_module.InstagramReelUploader(
                device_serial="MOCK_SERIAL_1",
                package_name="com.instagram.android.clone2",
                log_callback=lambda lvl, msg, step=None: logs.append((lvl, msg, step)),
            )

        self.assertFalse(uploader._is_gallery_grid_visible())
        with patch("time.sleep", return_value=None):
            with self.assertRaises(TimeoutError) as ctx:
                uploader.wait_and_verify_screen("Gallery Grid", uploader._is_gallery_grid_visible, timeout=0.15, auto_dismiss=False)
        self.assertIn("Target package: com.instagram.android.clone2", str(ctx.exception))
        self.assertIn("Foreground: com.instagram.android.clone2/.CameraActivity", str(ctx.exception))

    def test_missing_gallery_or_video_selector_never_taps_coordinates(self):
        import adb as adb_module

        # Case A: Gallery open button missing -> fail cleanly with NO (70, h - 80) coordinate tap
        click_calls_a = []
        mock_dev_a = _MockU2Device(
            fg_fn=lambda: {"package": "com.instagram.android.clone4", "activity": ".CameraActivity"},
            shell_fn=lambda cmd: ("", 0),
            matcher_fn=lambda key: False,
        )
        with patch.object(adb_module.u2, "connect", return_value=mock_dev_a):
            uploader_a = adb_module.InstagramReelUploader(
                device_serial="MOCK_SERIAL_1",
                package_name="com.instagram.android.clone4",
                log_callback=lambda *a, **kw: None,
            )
        uploader_a.human.click = lambda target: click_calls_a.append(target)

        with patch("time.sleep", return_value=None):
            with self.assertRaises(RuntimeError) as ctx_a:
                uploader_a.open_gallery_and_select_video(staged_video_path="/sdcard/DCIM/Camera/upload_100_clip.mp4")

        self.assertEqual(click_calls_a, [])
        self.assertIn("Target package: com.instagram.android.clone4", str(ctx_a.exception))
        self.assertIn("Gallery selector: MISSING", str(ctx_a.exception))
        self.assertIn("Selected video: upload_100_clip.mp4", str(ctx_a.exception))

        # Case B: Gallery button opens Gallery Grid, but thumbnail selector never appears -> NO (360, 500) tap
        click_calls_b = []
        screen_state = {"grid_open": False}

        def matcher_b(key):
            k = dict(key)
            if k.get("resourceId") == "com.instagram.android.clone5:id/gallery_preview_button":
                return not screen_state["grid_open"]
            if k.get("resourceId") == "com.instagram.android.clone5:id/gallery_recycler_view":
                return screen_state["grid_open"]
            return False

        mock_dev_b = _MockU2Device(
            fg_fn=lambda: {"package": "com.instagram.android.clone5", "activity": ".GalleryActivity"},
            shell_fn=lambda cmd: ("", 0),
            matcher_fn=matcher_b,
        )
        with patch.object(adb_module.u2, "connect", return_value=mock_dev_b):
            uploader_b = adb_module.InstagramReelUploader(
                device_serial="MOCK_SERIAL_1",
                package_name="com.instagram.android.clone5",
                log_callback=lambda *a, **kw: None,
            )

        def fake_click_b(target):
            click_calls_b.append(target)
            screen_state["grid_open"] = True

        uploader_b.human.click = fake_click_b
        uploader_b.human.sleep = lambda *a, **kw: None

        # Simulate elapsed poll ticks quickly without waiting 12 real seconds
        tick = {"t": 0.0}

        def fake_time():
            tick["t"] += 1.5
            return tick["t"]

        with patch("adb.time.time", side_effect=fake_time), patch("adb.time.sleep", return_value=None):
            with self.assertRaises(RuntimeError) as ctx_b:
                uploader_b.open_gallery_and_select_video(staged_video_path="/sdcard/DCIM/Camera/upload_200_clip.mp4")

        self.assertEqual(len(click_calls_b), 1)
        self.assertNotIsInstance(click_calls_b[0], tuple)
        self.assertIn("Target package: com.instagram.android.clone5", str(ctx_b.exception))
        self.assertIn("Gallery selector: MISSING", str(ctx_b.exception))
        self.assertIn("Selected video: upload_200_clip.mp4", str(ctx_b.exception))

    def test_wrong_foreground_after_audio_intent_fails_clearly(self):
        import adb as adb_module

        # Even if 'Use audio' text is matched somewhere in wrong app, foreground verification must fail before Step 1 verification passes
        mock_dev = _MockU2Device(
            fg_fn=lambda: {"package": "com.android.chrome", "activity": "com.google.android.apps.chrome.Main"},
            shell_fn=lambda cmd: ("Starting: Intent { act=android.intent.action.VIEW }", 0),
            matcher_fn=lambda key: dict(key).get("text") == "Use audio",
        )
        with patch.object(adb_module.u2, "connect", return_value=mock_dev):
            uploader = adb_module.InstagramReelUploader(
                device_serial="MOCK_SERIAL_1",
                package_name="com.instagram.android.clone3",
                log_callback=lambda *a, **kw: None,
            )

        tick = {"t": 0.0}

        def fake_time():
            tick["t"] += 2.5
            return tick["t"]

        with patch("adb.time.time", side_effect=fake_time), patch("adb.time.sleep", return_value=None):
            with self.assertRaises(RuntimeError) as ctx:
                uploader.open_audio_page("http://www.instagram.com/reels/audio/999888/")

        msg = str(ctx.exception)
        self.assertIn("Wrong foreground app", msg)
        self.assertIn("Target package: com.instagram.android.clone3", msg)
        self.assertIn("Foreground: com.android.chrome/com.google.android.apps.chrome.Main", msg)
        self.assertIn("Intent result: exit=0", msg)
        self.assertTrue(any("https://www.instagram.com/reels/audio/999888/" in c for c in mock_dev.shell_cmds))

    def test_launcher_bounce_fails_fast_instead_of_audio_page_timeout(self):
        import adb as adb_module

        # Simulate clone opening UrlHandlerLauncherActivity briefly then dropping back to QuickstepLauncher
        fg_calls = {"n": 0}

        def fg():
            fg_calls["n"] += 1
            if fg_calls["n"] == 1:
                return {"package": "com.instagram.android.clone3", "activity": "com.instagram.url.UrlHandlerLauncherActivity"}
            return {"package": "com.transsion.hilauncher", "activity": "QuickstepLauncher"}

        mock_dev = _MockU2Device(
            fg_fn=fg,
            shell_fn=lambda cmd: ("Starting: Intent { act=android.intent.action.VIEW }", 0),
            matcher_fn=lambda key: False,
        )
        with patch.object(adb_module.u2, "connect", return_value=mock_dev):
            uploader = adb_module.InstagramReelUploader(
                device_serial="MOCK_SERIAL_1",
                package_name="com.instagram.android.clone3",
                log_callback=lambda *a, **kw: None,
            )

        with patch("adb.time.sleep", return_value=None):
            with self.assertRaises(RuntimeError) as ctx:
                uploader.open_audio_page("https://www.instagram.com/reels/audio/1656648626250751/")

        msg = str(ctx.exception)
        self.assertIn("Clone launch/routing failed", msg)
        self.assertIn("com.transsion.hilauncher/QuickstepLauncher", msg)
        self.assertNotIn("Screen verification timed out", msg)

    def test_proceed_to_share_dismisses_play_store_and_ok_sheet(self):
        import adb as adb_module

        state = {"step": "preview", "ok_visible": False}

        def fg():
            if state["step"] == "vending":
                return {"package": "com.android.vending", "activity": "HsdpAlias"}
            return {"package": "com.instagram.android.clone11", "activity": "com.instagram.modal.ModalActivity"}

        def matcher(key):
            d = dict(key)
            if state["step"] == "preview":
                return d.get("resourceId") == "com.instagram.android.clone11:id/clips_right_action_button"
            if state["step"] == "share":
                if state["ok_visible"] and d.get("textMatches") == "(?i)^ok$":
                    return True
                return d.get("resourceId") in (
                    "com.instagram.android.clone11:id/share_button",
                    "com.instagram.android.clone11:id/caption_input_text_view",
                )
            return False

        mock_dev = _MockU2Device(fg_fn=fg, shell_fn=lambda cmd: ("", 0), matcher_fn=matcher)
        original_press = mock_dev.press
        def custom_press(key):
            original_press(key)
            if key == "back" and state["step"] == "vending":
                state["step"] = "share"
                state["ok_visible"] = True
        mock_dev.press = custom_press

        with patch.object(adb_module.u2, "connect", return_value=mock_dev):
            uploader = adb_module.InstagramReelUploader(
                device_serial="MOCK_SERIAL_1",
                package_name="com.instagram.android.clone11",
                log_callback=lambda *a, **kw: None,
            )

        clicked = []
        def fake_click(el):
            key = dict(getattr(el, "_key", ()))
            clicked.append(key)
            if key.get("resourceId") == "com.instagram.android.clone11:id/clips_right_action_button":
                state["step"] = "vending"
            elif key.get("textMatches") == "(?i)^ok$":
                state["ok_visible"] = False

        with patch.object(uploader.human, "click", side_effect=fake_click), \
             patch.object(uploader.human, "sleep", return_value=None), \
             patch("adb.time.sleep", return_value=None):
            uploader.proceed_to_share_screen()

        self.assertIn("back", mock_dev.pressed_keys)
        self.assertFalse(state["ok_visible"])
        self.assertTrue(any(k.get("textMatches") == "(?i)^ok$" for k in clicked))

    def test_audio_intent_failure_saved_and_staged_single_to_multi_e2e(self):
        import adb as adb_module
        import app as app_module

        dev_serial = "STAGED_DEV_01"
        pkg_1 = "com.instagram.android.clone10"
        pkg_2 = "com.instagram.android.clone11"
        acc_1 = db.create_account(username="test_acc_one", device_serial=dev_serial, package_name=pkg_1, db_path=self.test_db)
        acc_2 = db.create_account(username="test_acc_two", device_serial=dev_serial, package_name=pkg_2, db_path=self.test_db)
        db.upsert_clone(dev_serial, pkg_1, app_name="Clone 10", is_confirmed=True, db_path=self.test_db)
        db.upsert_clone(dev_serial, pkg_2, app_name="Clone 11", is_confirmed=True, db_path=self.test_db)

        vid_file = os.path.join(self.tmp_dir, "staged_demo.mp4")
        with open(vid_file, "wb") as fh:
            fh.write(b"FAKE_MP4_DATA")
        vid_id = db.create_content_item(
            media_type="video", original_filename="staged_demo.mp4", mime_type="video/mp4",
            size_bytes=13, storage_key="staged_demo.mp4", db_path=self.test_db,
        )

        def mock_adb_cmd(cmd, device_serial=None, timeout=8):
            if len(cmd) == 4 and cmd[:3] == ["shell", "pm", "path"] and device_serial == dev_serial:
                return f"package:/data/app/{cmd[3]}/base.apk\n"
            return ""

        with patch.object(db, "DEFAULT_DB_PATH", self.test_db), \
             patch.object(app_module, "_get_content_upload_dir", return_value=self.tmp_dir), \
             patch("package_scanner.list_connected_devices", return_value=[{"serial": dev_serial, "status": "device"}]), \
             patch("package_scanner.run_adb_command", side_effect=mock_adb_cmd):
            app_module.app.testing = True
            client = app_module.app.test_client()

            # Stage A: Audio intent failure persisted into tasks.error_message & task_events
            fail_camp = client.post("/api/campaigns", json={
                "name": "Intent Fail Persistence",
                "audio_url": "https://www.instagram.com/reels/audio/1656648626250751/",
                "targets": [{"account_id": acc_1, "content_id": vid_id, "caption": "Fail check"}],
            }).get_json()["campaign"]

            fail_dev = _MockU2Device(
                fg_fn=lambda: {"package": "com.android.launcher", "activity": ".Launcher"},
                shell_fn=lambda cmd: (
                    ("Error: Activity not started, unable to resolve Intent", 1) if cmd.startswith("am start") else ("", 0)
                ),
                matcher_fn=lambda key: False,
            )
            closed_pkgs = []

            def fake_close(device_serial=None, package_name=None, db_path=None):
                closed_pkgs.append(package_name)
                return {"status": "closed"}

            with patch.object(adb_module.u2, "connect", return_value=fail_dev), \
                 patch.object(app_module.profile_capture, "close_profile_app", side_effect=fake_close), \
                 patch("adb.time.sleep", return_value=None):
                fail_run_id = client.post(f"/api/campaigns/{fail_camp['id']}/runs").get_json()["run"]["id"]
                for _ in range(50):
                    cur = client.get(f"/api/campaign-runs/{fail_run_id}").get_json()["run"]
                    if cur["status"] not in ("queued", "running"):
                        break
                    time.sleep(0.05)

            fail_run = client.get(f"/api/campaign-runs/{fail_run_id}").get_json()["run"]
            self.assertEqual(fail_run["status"], "failed")
            fail_task = fail_run["tasks"][0]
            self.assertEqual(fail_task["status"], "failed")
            self.assertIn(pkg_1, fail_task["error_message"])
            self.assertIn("Intent result: exit=1; output=Error: Activity not started, unable to resolve Intent", fail_task["error_message"])
            self.assertIn(pkg_1, closed_pkgs)

            task_detail = client.get(f"/api/tasks/{fail_task['id']}").get_json()
            ev_msgs = "\n".join(e["message"] for e in task_detail["task"]["events"])
            self.assertIn("Audio intent launch failed", ev_msgs)
            self.assertIn("Intent result: exit=1", ev_msgs)

            # Build realistic state-machine mock U2 device for single-account & multi-account E2E
            stage = {"screen": "audio", "pkg": pkg_1, "clicks": []}

            def make_e2e_device(target_pkg):
                stage["pkg"] = target_pkg
                stage["screen"] = "audio"

                def fg():
                    return {"package": stage["pkg"], "activity": f".{stage['screen'].title()}Activity"}

                def sh(cmd):
                    if cmd.startswith("am start"):
                        for part in cmd.split():
                            if part.startswith("com.instagram."):
                                stage["pkg"] = part.split("/", 1)[0]
                                stage["screen"] = "audio"
                                break
                        return ("Starting: Intent { act=android.intent.action.VIEW }", 0)
                    return ("", 0)

                def match(key):
                    k = dict(key)
                    s = stage["screen"]
                    p = stage["pkg"]
                    if s == "audio" and k.get("text") == "Use audio":
                        return True
                    if s == "camera" and k.get("resourceId") == f"{p}:id/gallery_preview_button":
                        return True
                    if s == "gallery" and (
                        k.get("resourceId") == f"{p}:id/gallery_recycler_view"
                        or k.get("descriptionContains", "").startswith("upload_")
                    ):
                        return True
                    if s == "preview" and k.get("resourceId") == f"{p}:id/clips_right_action_button":
                        return True
                    return False

                def elem_info(key):
                    k = dict(key)
                    if "descriptionContains" in k:
                        return {
                            "contentDescription": f"{k['descriptionContains']}.mp4 video",
                            "text": "",
                            "resourceId": f"{stage['pkg']}:id/gallery_grid_item_thumbnail",
                            "bounds": {"left": 10, "top": 200, "right": 230, "bottom": 500},
                        }
                    return {"contentDescription": str(k), "text": "", "bounds": {"left": 1, "top": 2, "right": 3, "bottom": 4}}

                return _MockU2Device(fg_fn=fg, shell_fn=sh, matcher_fn=match, info_fn=elem_info)

            def run_mocked_step_sequence(uploader):
                # Hook human.click to advance screen cleanly WITHOUT ever accepting tuple coordinates
                def advance_click(target):
                    self.assertNotIsInstance(target, tuple)
                    stage["clicks"].append(target)
                    if stage["screen"] == "audio":
                        stage["screen"] = "camera"
                    elif stage["screen"] == "camera":
                        stage["screen"] = "gallery"
                    elif stage["screen"] == "gallery":
                        stage["screen"] = "preview"

                uploader.human.click = advance_click
                uploader.human.sleep = lambda *a, **kw: None
                staged = uploader.push_video_to_device(vid_file)
                uploader.open_audio_page("http://www.instagram.com/reels/audio/1656648626250751/")
                uploader.tap_use_audio()
                uploader.open_gallery_and_select_video(staged_video_path=staged)
                return f"https://www.instagram.com/reel/{uploader.package_name}/"

            # Stage B: Single selected test account E2E first
            single_camp = client.post("/api/campaigns", json={
                "name": "Single Account Test",
                "audio_url": "https://www.instagram.com/reels/audio/1656648626250751/",
                "targets": [{"account_id": acc_1, "content_id": vid_id, "caption": "Single E2E"}],
            }).get_json()["campaign"]

            closed_single = []
            with patch.object(adb_module.u2, "connect", side_effect=lambda *a, **kw: make_e2e_device(pkg_1)), \
                 patch.object(adb_module.InstagramReelUploader, "execute_workflow", new=lambda self, **kw: run_mocked_step_sequence(self)), \
                 patch.object(app_module.profile_capture, "close_profile_app", side_effect=lambda device_serial=None, package_name=None, db_path=None: closed_single.append(package_name) or {"status": "closed"}), \
                 patch("adb.time.sleep", return_value=None):
                s_run_id = client.post(f"/api/campaigns/{single_camp['id']}/runs").get_json()["run"]["id"]
                for _ in range(120):
                    cur = client.get(f"/api/campaign-runs/{s_run_id}").get_json()["run"]
                    if cur["status"] not in ("queued", "running"):
                        break
                    time.sleep(0.05)

            s_run = client.get(f"/api/campaign-runs/{s_run_id}").get_json()["run"]
            self.assertEqual(s_run["status"], "completed")
            self.assertEqual(s_run["tasks"][0]["reel_url"], f"https://www.instagram.com/reel/{pkg_1}/")
            self.assertEqual(closed_single, [pkg_1])

            # Stage C: Multi-account sequential E2E with app-close & cooldown verification
            multi_camp = client.post("/api/campaigns", json={
                "name": "Multi Account Test",
                "audio_url": "https://www.instagram.com/reels/audio/1656648626250751/",
                "cooldown_seconds": 3,
                "targets": [
                    {"account_id": acc_1, "content_id": vid_id, "caption": "Multi 1"},
                    {"account_id": acc_2, "content_id": vid_id, "caption": "Multi 2"},
                ],
            }).get_json()["campaign"]

            seq_events = []

            def multi_close(device_serial=None, package_name=None, db_path=None):
                seq_events.append(("close", package_name))
                return {"status": "closed"}

            def multi_wait(timeout=None):
                seq_events.append(("cooldown", timeout))
                return False

            def multi_exec(self_u, **kw):
                seq_events.append(("start", self_u.package_name))
                url = run_mocked_step_sequence(self_u)
                seq_events.append(("done", self_u.package_name))
                return url

            with patch.object(adb_module.u2, "connect", side_effect=lambda *a, **kw: make_e2e_device(pkg_1)), \
                 patch.object(adb_module.InstagramReelUploader, "execute_workflow", new=multi_exec), \
                 patch.object(app_module.profile_capture, "close_profile_app", side_effect=multi_close), \
                 patch.object(app_module.campaign_cancel_event, "wait", side_effect=multi_wait), \
                 patch("adb.time.sleep", return_value=None):
                m_run_id = client.post(f"/api/campaigns/{multi_camp['id']}/runs").get_json()["run"]["id"]
                for _ in range(120):
                    cur = client.get(f"/api/campaign-runs/{m_run_id}").get_json()["run"]
                    if cur["status"] not in ("queued", "running"):
                        break
                    time.sleep(0.05)

            m_run = client.get(f"/api/campaign-runs/{m_run_id}").get_json()["run"]
            self.assertEqual(m_run["status"], "completed")
            self.assertEqual(m_run["tasks"][0]["reel_url"], f"https://www.instagram.com/reel/{pkg_1}/")
            self.assertEqual(m_run["tasks"][1]["reel_url"], f"https://www.instagram.com/reel/{pkg_2}/")
            self.assertEqual(
                seq_events,
                [
                    ("start", pkg_1),
                    ("done", pkg_1),
                    ("close", pkg_1),
                    ("cooldown", 3.0),
                    ("start", pkg_2),
                    ("done", pkg_2),
                    ("close", pkg_2),
                ],
            )


class TestTasksTabRebuildAndArtifacts(unittest.TestCase):
    def setUp(self):
        import app as app_module
        self.app_module = app_module
        self.tmp_dir = tempfile.mkdtemp()
        self.test_db = os.path.join(self.tmp_dir, "test_instoper.db")
        db.init_db(self.test_db)
        self.app_module.app.config["TESTING"] = True
        self.client = self.app_module.app.test_client()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_schema_migration_idempotent_and_structured_events(self):
        acc = db.create_account(username="mig_user", device_serial="SER_MIG", package_name="com.mig.test", db_path=self.test_db)
        vid = db.create_content_item(
            media_type="video",
            original_filename="mig_vid.mp4",
            mime_type="video/mp4",
            size_bytes=1024,
            storage_key="mig_vid.mp4",
            db_path=self.test_db,
        )
        camp = db.create_campaign(
            name="Migration Diagnostic Campaign",
            audio_url="https://www.instagram.com/reels/audio/1656648626250751/",
            targets=[{"account_id": acc, "content_id": vid, "caption": "Diag test"}],
            db_path=self.test_db,
        )
        run = db.create_campaign_run(camp["id"], db_path=self.test_db)
        t_id = run["tasks"][0]["id"]

        # Append structured event with stage and data
        ev = db.append_task_event(
            t_id,
            "error",
            "Audio launch failed",
            step=1,
            stage="open_audio_page",
            data={"foreground": "QuickstepLauncher", "target": "com.mig.test"},
            db_path=self.test_db,
        )
        self.assertEqual(ev["stage"], "open_audio_page")
        self.assertEqual(ev["data"]["foreground"], "QuickstepLauncher")

        # Update failure screenshot artifact
        updated_t = db.update_task_failure_artifact(
            t_id,
            screenshot_path="failure_task_test.png",
            captured_at="2026-11-12T18:00:00Z",
            error=None,
            db_path=self.test_db,
        )
        self.assertTrue(updated_t["has_failure_screenshot"])
        self.assertEqual(updated_t["failure_screenshot_path"], "failure_task_test.png")
        self.assertEqual(updated_t["failure_screenshot_url"], f"/api/tasks/{t_id}/failure-screenshot")

        # Idempotent init_db call
        db.init_db(self.test_db)

        # Retrieve task detail and verify structured events parsed back
        detail = db.get_task_detail(t_id, db_path=self.test_db)
        self.assertEqual(len(detail["events"]), 1)
        self.assertEqual(detail["events"][0]["stage"], "open_audio_page")
        self.assertEqual(detail["events"][0]["data"], {"foreground": "QuickstepLauncher", "target": "com.mig.test"})

        # Search filter on tasks and campaign runs
        found_tasks, t_cnt = db.list_tasks(search="mig_user", db_path=self.test_db)
        self.assertEqual(t_cnt, 1)
        found_runs, r_cnt = db.list_campaign_runs(search="Diagnostic", db_path=self.test_db)
        self.assertEqual(r_cnt, 1)

    def test_failure_screenshot_and_export_endpoints(self):
        with patch.object(db, "DEFAULT_DB_PATH", self.test_db):
            acc = db.create_account(username="exp_user", device_serial="SER_EXP", package_name="com.exp.app", db_path=self.test_db)
            vid = db.create_content_item(
                media_type="video",
                original_filename="exp_vid.mp4",
                mime_type="video/mp4",
                size_bytes=2048,
                storage_key="exp_vid.mp4",
                db_path=self.test_db,
            )
            camp = db.create_campaign(
                name="Export Test Campaign",
                audio_url="https://www.instagram.com/reels/audio/1656648626250751/",
                targets=[{"account_id": acc, "content_id": vid, "caption": "Export cap"}],
                db_path=self.test_db,
            )
            run = db.create_campaign_run(camp["id"], db_path=self.test_db)
            t_id = run["tasks"][0]["id"]
            db.append_task_event(t_id, "info", "Export event message", step=2, stage="use_audio", db_path=self.test_db)

            # 1. Run Export JSON
            r_json = self.client.get(f"/api/campaign-runs/{run['id']}/export?format=json")
            self.assertEqual(r_json.status_code, 200)
            data_json = r_json.get_json()
            self.assertEqual(data_json["status"], "ok")
            self.assertEqual(data_json["run"]["id"], run["id"])

            # 2. Run Export CSV
            r_csv = self.client.get(f"/api/campaign-runs/{run['id']}/export?format=csv")
            self.assertEqual(r_csv.status_code, 200)
            self.assertIn("text/csv", r_csv.content_type)
            csv_text = r_csv.data.decode("utf-8")
            self.assertIn("Run ID,Campaign ID", csv_text)
            self.assertIn("exp_user", csv_text)

            # 3. Task Export JSON
            t_json = self.client.get(f"/api/tasks/{t_id}/export?format=json")
            self.assertEqual(t_json.status_code, 200)
            self.assertEqual(t_json.get_json()["task"]["id"], t_id)

            # 4. Task Export CSV
            t_csv = self.client.get(f"/api/tasks/{t_id}/export?format=csv")
            self.assertEqual(t_csv.status_code, 200)
            self.assertIn("text/csv", t_csv.content_type)
            t_csv_text = t_csv.data.decode("utf-8")
            self.assertIn("Task ID,Event ID,Timestamp,Level,Step,Stage,Message", t_csv_text)
            self.assertIn("Export event message", t_csv_text)

            # 5. Screenshot endpoint - 404 when no screenshot
            r_shot_404 = self.client.get(f"/api/tasks/{t_id}/failure-screenshot")
            self.assertEqual(r_shot_404.status_code, 404)

            # Create a mock screenshot file in artifacts directory
            artifacts_dir = self.app_module._get_task_artifacts_dir()
            shot_file = f"failure_task_{t_id}_mock.png"
            shot_full = os.path.join(artifacts_dir, shot_file)
            with open(shot_full, "wb") as fh:
                fh.write(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDRFAKEPNG")

            try:
                db.update_task_failure_artifact(t_id, screenshot_path=shot_file, db_path=self.test_db)
                r_shot_200 = self.client.get(f"/api/tasks/{t_id}/failure-screenshot")
                self.assertEqual(r_shot_200.status_code, 200)
                self.assertEqual(r_shot_200.content_type, "image/png")
                self.assertEqual(r_shot_200.data, b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDRFAKEPNG")
                r_shot_200.close()
            finally:
                if os.path.exists(shot_full):
                    try:
                        os.remove(shot_full)
                    except Exception:
                        pass


class TestPerTaskScreenRecording(unittest.TestCase):
    def setUp(self):
        from datetime import datetime, timezone, timedelta
        self.datetime = datetime
        self.timezone = timezone
        self.timedelta = timedelta
        import app as app_module
        import screen_recorder
        self.app_module = app_module
        self.screen_recorder = screen_recorder
        self.tmp_dir = tempfile.mkdtemp()
        self.test_db = os.path.join(self.tmp_dir, "test_recording.db")
        db.init_db(self.test_db)
        self.app_module.app.config["TESTING"] = True
        self.client = self.app_module.app.test_client()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _create_sample_task(self):
        acc = db.create_account(
            username="rec_user",
            device_serial="REC_SERIAL",
            package_name="com.rec.test",
            db_path=self.test_db,
        )
        vid = db.create_content_item(
            media_type="video",
            original_filename="rec_vid.mp4",
            mime_type="video/mp4",
            size_bytes=1024,
            storage_key="rec_vid.mp4",
            db_path=self.test_db,
        )
        camp = db.create_campaign(
            name="Recording Test Campaign",
            audio_url="https://www.instagram.com/reels/audio/1656648626250751/",
            targets=[{"account_id": acc, "content_id": vid, "caption": "Rec test"}],
            db_path=self.test_db,
        )
        run = db.create_campaign_run(camp["id"], db_path=self.test_db)
        return run["tasks"][0]["id"]

    def test_successful_task_deletes_recording_and_preserves_logs(self):
        t_id = self._create_sample_task()
        artifacts_base = os.path.join(self.tmp_dir, "recordings")

        recorder = self.screen_recorder.TaskScreenRecorder(
            task_id=t_id,
            device_serial="REC_SERIAL",
            storage_base_dir=artifacts_base,
            testing_mode=True,
        )
        self.assertTrue(recorder.start())

        # Log some structured events with elapsed_ms
        db.append_task_event(t_id, "info", "Task started", elapsed_ms=0, db_path=self.test_db)
        db.append_task_event(t_id, "success", "Step 11 finished", elapsed_ms=14500, db_path=self.test_db)

        # 100% Successful: completed workflow + verified app cleanup
        res = recorder.stop(terminal_task_state="completed", cleanup_ok=True)
        self.assertEqual(res["status"], "deleted_success")
        self.assertIsNone(res["recording_path"])

        # Update DB using helper
        db.update_task_recording(
            t_id,
            status=res["status"],
            recording_path=res["recording_path"],
            duration_seconds=res["duration_seconds"],
            segments=res["segments"],
            started_at=res["started_at"],
            completed_at=res["completed_at"],
            error=res["error"],
            db_path=self.test_db,
        )

        detail = db.get_task_detail(t_id, db_path=self.test_db)
        self.assertEqual(detail["recording_status"], "deleted_success")
        self.assertFalse(detail["has_recording"])
        self.assertIsNone(detail["recording_path"])
        # All logs permanently preserved
        self.assertEqual(len(detail["events"]), 2)
        self.assertEqual(detail["events"][0]["elapsed_ms"], 0)
        self.assertEqual(detail["events"][1]["elapsed_ms"], 14500)

        # Ensure directory on disk does not exist
        task_dir = os.path.join(artifacts_base, f"task_{t_id}")
        self.assertFalse(os.path.exists(task_dir))

        # Endpoint returns 404 for deleted recording
        with patch.object(db, "DEFAULT_DB_PATH", self.test_db):
            r_get = self.client.get(f"/api/tasks/{t_id}/recording")
            self.assertEqual(r_get.status_code, 404)
            self.assertIn("100% success", r_get.get_json()["error"])

    def test_failed_task_retains_recording_and_logs(self):
        t_id = self._create_sample_task()
        artifacts_base = os.path.join(self.tmp_dir, "recordings")

        recorder = self.screen_recorder.TaskScreenRecorder(
            task_id=t_id,
            device_serial="REC_SERIAL",
            storage_base_dir=artifacts_base,
            testing_mode=True,
        )
        self.assertTrue(recorder.start())

        db.append_task_event(t_id, "error", "Share screen timeout", elapsed_ms=28000, db_path=self.test_db)

        # Unsuccessful: failed workflow
        res = recorder.stop(terminal_task_state="failed", cleanup_ok=True)
        self.assertEqual(res["status"], "saved")
        self.assertIsNotNone(res["recording_path"])
        self.assertTrue(os.path.exists(res["recording_path"]))

        db.update_task_recording(
            t_id,
            status=res["status"],
            recording_path=res["recording_path"],
            duration_seconds=res["duration_seconds"],
            segments=res["segments"],
            started_at=res["started_at"],
            completed_at=res["completed_at"],
            error=res["error"],
            db_path=self.test_db,
        )

        detail = db.get_task_detail(t_id, db_path=self.test_db)
        self.assertEqual(detail["recording_status"], "saved")
        self.assertTrue(detail["has_recording"])
        self.assertEqual(detail["recording_url"], f"/api/tasks/{t_id}/recording")

        with patch.object(db, "DEFAULT_DB_PATH", self.test_db), \
             patch.object(self.app_module, "_get_recordings_artifacts_dir", lambda: artifacts_base):
            # Normal streaming GET
            r_get = self.client.get(f"/api/tasks/{t_id}/recording")
            self.assertEqual(r_get.status_code, 200)
            self.assertEqual(r_get.content_type, "video/mp4")
            self.assertEqual(r_get.headers.get("Accept-Ranges"), "bytes")
            r_get.close()

            # Download GET
            r_dl = self.client.get(f"/api/tasks/{t_id}/recording?download=true")
            self.assertEqual(r_dl.status_code, 200)
            self.assertIn("attachment", r_dl.headers.get("Content-Disposition", ""))
            r_dl.close()

    def test_cleanup_failure_retains_recording(self):
        t_id = self._create_sample_task()
        artifacts_base = os.path.join(self.tmp_dir, "recordings")

        recorder = self.screen_recorder.TaskScreenRecorder(
            task_id=t_id,
            device_serial="REC_SERIAL",
            storage_base_dir=artifacts_base,
            testing_mode=True,
        )
        recorder.start()

        # Workflow succeeded, but app stop failed!
        res = recorder.stop(terminal_task_state="completed", cleanup_ok=False)
        self.assertEqual(res["status"], "saved")
        self.assertIsNotNone(res["recording_path"])
        self.assertTrue(os.path.exists(res["recording_path"]))

    def test_interrupted_or_cancelled_task_retains_recording(self):
        t_id = self._create_sample_task()
        artifacts_base = os.path.join(self.tmp_dir, "recordings")

        recorder = self.screen_recorder.TaskScreenRecorder(
            task_id=t_id,
            device_serial="REC_SERIAL",
            storage_base_dir=artifacts_base,
            testing_mode=True,
        )
        recorder.start()

        # Task cancelled
        res = recorder.stop(terminal_task_state="cancelled", cleanup_ok=True)
        self.assertEqual(res["status"], "saved")
        self.assertTrue(os.path.exists(res["recording_path"]))

    def test_adb_start_failure_non_blocking_and_records_capture_failed(self):
        t_id = self._create_sample_task()
        artifacts_base = os.path.join(self.tmp_dir, "recordings")

        # Empty serial and non-testing mode -> fails gracefully
        recorder = self.screen_recorder.TaskScreenRecorder(
            task_id=t_id,
            device_serial="",
            storage_base_dir=artifacts_base,
            testing_mode=False,
        )
        start_ok = recorder.start()
        self.assertFalse(start_ok)
        self.assertIn("No device serial", recorder._error)

        res = recorder.stop(terminal_task_state="failed", cleanup_ok=True)
        self.assertEqual(res["status"], "capture_failed")
        self.assertIn("No device serial", res["error"])

        db.update_task_recording(
            t_id,
            status=res["status"],
            error=res["error"],
            db_path=self.test_db,
        )
        detail = db.get_task_detail(t_id, db_path=self.test_db)
        self.assertEqual(detail["recording_status"], "capture_failed")
        self.assertIn("No device serial", detail["recording_error"])

    def test_multi_segment_concatenation_and_segment_api(self):
        t_id = self._create_sample_task()
        artifacts_base = os.path.join(self.tmp_dir, "recordings")
        task_dir = os.path.join(artifacts_base, f"task_{t_id}")
        os.makedirs(task_dir, exist_ok=True)

        # Create two mock segment files
        seg0_path = os.path.join(task_dir, "seg0.mp4")
        seg1_path = os.path.join(task_dir, "seg1.mp4")
        with open(seg0_path, "wb") as f:
            f.write(b"MOCK_SEGMENT_0_VIDEO_DATA")
        with open(seg1_path, "wb") as f:
            f.write(b"MOCK_SEGMENT_1_VIDEO_DATA")

        joined_path = os.path.join(task_dir, "joined.mp4")
        # Test concatenate_video_segments helper
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            # Create the joined destination to simulate ffmpeg producing it
            with open(joined_path, "wb") as f:
                f.write(b"MOCK_JOINED_VIDEO_DATA")
            ok = self.screen_recorder.concatenate_video_segments([seg0_path, seg1_path], joined_path)
            self.assertTrue(ok)

        # Save segment metadata in DB
        segments = [
            {"index": 0, "filename": "seg0.mp4", "duration_seconds": 180.0, "size_bytes": 25},
            {"index": 1, "filename": "seg1.mp4", "duration_seconds": 45.0, "size_bytes": 25},
        ]
        db.update_task_recording(
            t_id,
            status="saved",
            recording_path=joined_path,
            duration_seconds=225.0,
            segments=segments,
            db_path=self.test_db,
        )

        with patch.object(db, "DEFAULT_DB_PATH", self.test_db), \
             patch.object(self.app_module, "_get_recordings_artifacts_dir", lambda: artifacts_base):
            # Test segment 0
            r_s0 = self.client.get(f"/api/tasks/{t_id}/recording/segments/0")
            self.assertEqual(r_s0.status_code, 200)
            self.assertEqual(r_s0.data, b"MOCK_SEGMENT_0_VIDEO_DATA")
            r_s0.close()

            # Test segment 1
            r_s1 = self.client.get(f"/api/tasks/{t_id}/recording/segments/1")
            self.assertEqual(r_s1.status_code, 200)
            self.assertEqual(r_s1.data, b"MOCK_SEGMENT_1_VIDEO_DATA")
            r_s1.close()

            # Test segment out of bounds
            r_s99 = self.client.get(f"/api/tasks/{t_id}/recording/segments/99")
            self.assertEqual(r_s99.status_code, 404)

    def test_30_day_retention_auto_cleanup_endpoint_and_db(self):
        t_id = self._create_sample_task()
        artifacts_base = os.path.join(self.tmp_dir, "recordings")
        task_dir = os.path.join(artifacts_base, f"task_{t_id}")
        os.makedirs(task_dir, exist_ok=True)
        rec_file = os.path.join(task_dir, "recording.mp4")
        with open(rec_file, "wb") as f:
            f.write(b"OLD_RECORDING_BYTES")

        db.update_task_recording(
            t_id,
            status="saved",
            recording_path=rec_file,
            duration_seconds=30.0,
            db_path=self.test_db,
        )

        # Backdate completed_at and created_at to 40 days ago
        old_completed = (self.datetime.now(self.timezone.utc) - self.timedelta(days=40)).isoformat()
        with db.get_connection(self.test_db) as conn:
            conn.execute(
                "UPDATE tasks SET completed_at = ?, created_at = ? WHERE id = ?",
                (old_completed, old_completed, t_id),
            )
            conn.commit()

        # Check list_expired_recordings finds it
        expired = db.list_expired_recordings(older_than_days=30, db_path=self.test_db)
        self.assertEqual(len(expired), 1)
        self.assertEqual(expired[0]["id"], t_id)

        # Run cleanup
        count = self.screen_recorder.cleanup_expired_recordings(older_than_days=30, db_path=self.test_db)
        self.assertEqual(count, 1)

        # Verify file deleted
        self.assertFalse(os.path.exists(rec_file))

        # Verify DB status updated to expired
        detail = db.get_task_detail(t_id, db_path=self.test_db)
        self.assertEqual(detail["recording_status"], "expired")
        self.assertIsNone(detail["recording_path"])

        # Test POST /api/recordings/cleanup-expired API endpoint
        with patch.object(db, "DEFAULT_DB_PATH", self.test_db):
            r_post = self.client.post("/api/recordings/cleanup-expired", json={"days": 30})
            self.assertEqual(r_post.status_code, 200)
            self.assertEqual(r_post.get_json()["cleaned_count"], 0)  # already cleaned

    def test_task_event_elapsed_ms_persistence_and_csv_export(self):
        t_id = self._create_sample_task()
        db.append_task_event(
            t_id,
            "info",
            "Synced event",
            step=1,
            stage="open_audio",
            elapsed_ms=7820,
            db_path=self.test_db,
        )

        detail = db.get_task_detail(t_id, db_path=self.test_db)
        self.assertEqual(detail["events"][0]["elapsed_ms"], 7820)

        with patch.object(db, "DEFAULT_DB_PATH", self.test_db):
            r_csv = self.client.get(f"/api/tasks/{t_id}/export?format=csv")
            self.assertEqual(r_csv.status_code, 200)
            csv_text = r_csv.data.decode("utf-8")
            self.assertIn("Elapsed (s)", csv_text)
            self.assertIn("7.82", csv_text)

    def test_screenrecord_process_graceful_stop_finalizes_valid_mp4(self):
        """Verifies SIGINT (kill -2) stops screenrecord gracefully and produces valid MP4 without CalledProcessError on pidof."""
        t_id = self._create_sample_task()
        artifacts_base = os.path.join(self.tmp_dir, "recordings")
        task_dir = os.path.join(artifacts_base, f"task_{t_id}")
        os.makedirs(task_dir, exist_ok=True)

        recorder = self.screen_recorder.TaskScreenRecorder(
            task_id=t_id,
            device_serial="MOCK_DEVICE",
            storage_base_dir=artifacts_base,
            testing_mode=False,
        )

        # Mock Popen for screenrecord process
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.returncode = 0
        mock_proc.wait.return_value = 0
        recorder._current_proc = mock_proc
        recorder._current_remote_path = f"/sdcard/instoper_task_{t_id}_seg0.mp4"
        recorder._current_seg_index = 0
        recorder._current_seg_start_time = time.time() - 5.0
        recorder._is_running = True

        # Track ADB calls
        calls = []

        def mock_subp_run(cmd, *args, **kwargs):
            calls.append(cmd)
            # 1. First pidof call returns PID 5678 (running)
            if "pidof" in cmd:
                return MagicMock(returncode=0, stdout="5678\n", stderr="")
            # 2. kill -2 call succeeds
            elif "kill" in cmd:
                return MagicMock(returncode=0, stdout="", stderr="")
            # 3. Pull command creates a valid mock MP4 on disk
            elif "pull" in cmd:
                dest = cmd[-1]
                with open(dest, "wb") as f:
                    # Valid MP4 header (ftyp box)
                    f.write(b"\x00\x00\x00\x20ftypmp42\x00\x00\x00\x00mp42isomavc1")
                return MagicMock(returncode=0, stdout="1 file pulled", stderr="")
            # 4. rm commands
            elif "rm" in cmd:
                return MagicMock(returncode=0, stdout="", stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("subprocess.run", side_effect=mock_subp_run), \
             patch("screen_recorder.probe_video_duration", return_value=5.0):
            # Stop the task with workflow failed to inspect the finalized MP4
            res = recorder.stop(terminal_task_state="failed", cleanup_ok=True)

        # Verify kill -2 was sent to device PID 5678
        kill_calls = [c for c in calls if "kill" in c and "-2" in c and "5678" in c]
        self.assertTrue(len(kill_calls) >= 1)

        # Verify proc.wait() was called
        mock_proc.wait.assert_called_once()

        # Verify resulting MP4 file exists and has valid MP4 header
        self.assertEqual(res["status"], "saved")
        self.assertIsNotNone(res["recording_path"])
        self.assertTrue(os.path.isfile(res["recording_path"]))
        with open(res["recording_path"], "rb") as f:
            header = f.read(16)
            self.assertIn(b"ftyp", header)

        # Also verify second pidof call when no process is running (exit code 1) does NOT raise
        with patch("subprocess.run", return_value=MagicMock(returncode=1, stdout="", stderr="")):
            # Should run silently without raising CalledProcessError
            recorder._stop_active_segment_on_device()

    def test_successful_task_recording_deletion_failure_preserves_task_result_and_logs_error(self):
        """Verifies that if deleting a recording on a successful task fails, status is 'delete_failed', error is logged, and task outcome is preserved."""
        t_id = self._create_sample_task()
        artifacts_base = os.path.join(self.tmp_dir, "recordings")

        recorder = self.screen_recorder.TaskScreenRecorder(
            task_id=t_id,
            device_serial="REC_SERIAL",
            storage_base_dir=artifacts_base,
            testing_mode=True,
        )
        recorder.start()

        # Set task in DB to completed state with reel URL
        with db.get_connection(self.test_db) as conn:
            conn.execute(
                "UPDATE tasks SET status = 'completed', reel_url = 'https://instagram.com/reel/XYZ123' WHERE id = ?",
                (t_id,),
            )
            conn.commit()

        # Simulate filesystem deletion failure (e.g. file lock or permission error)
        simulated_error = "PermissionError: [WinError 32] The process cannot access the file because it is being used by another process."
        recorder._simulate_delete_error = simulated_error

        # Workflow succeeded + app cleanup succeeded
        res = recorder.stop(terminal_task_state="completed", cleanup_ok=True)

        # 1. Recorder must NOT report deleted_success!
        self.assertNotEqual(res["status"], "deleted_success")
        self.assertEqual(res["status"], "delete_failed")
        self.assertIn("PermissionError", res["error"])

        # 2. Finalize recording in app
        with patch.object(db, "DEFAULT_DB_PATH", self.test_db):
            self.app_module._finalize_task_recording(
                recorder=recorder,
                task_id=t_id,
                terminal_state="completed",
                cleanup_ok=True,
                task_start_time=time.time() - 10.0,
            )

        # 3. Verify DB state:
        detail = db.get_task_detail(t_id, db_path=self.test_db)
        # Recording status and error accurately persisted:
        self.assertEqual(detail["recording_status"], "delete_failed")
        self.assertIn("PermissionError", detail["recording_error"])

        # CRITICAL: Original task result must NOT be overwritten!
        self.assertEqual(detail["status"], "completed")
        self.assertEqual(detail["reel_url"], "https://instagram.com/reel/XYZ123")
        self.assertIsNone(detail["error_message"])

        # 4. Verify task logs contain the deletion failure event
        events = detail["events"]
        err_events = [e for e in events if e["level"] == "error"]
        self.assertTrue(len(err_events) >= 1)
        self.assertIn("Failed to delete screen recording file", err_events[0]["message"])
        self.assertIn("PermissionError", err_events[0]["message"])


class TestHumanBehaviorEngineV2(unittest.TestCase):
    """
    Tests for HumanBehaviorEngine v2.0 (Phase 1 & Phase 2 Anti-Bot Upgrades):
    - Biomechanical asymmetric ballistic thumb jerk velocity fluctuation (> 0.20)
    - Path redundancy (~2.0% to 10.0% wander vs straight chord)
    - Contact dwell micro-wobble (tissue drift within safe bounds)
    - Heavy-tailed log-normal cognitive delay (positive skewness > 0, bounded)
    """

    def setUp(self):
        from unittest.mock import MagicMock
        from human_behavior import HumanBehaviorEngine
        self.mock_device = MagicMock()
        self.mock_device.info = {"displayWidth": 720, "displayHeight": 1600}
        self.engine = HumanBehaviorEngine(self.mock_device)

    def test_velocity_fluctuation_and_jerk(self):
        import math
        path = self.engine.generate_human_swipe_path((360, 1200), (360, 400), num_steps=14)
        self.assertEqual(len(path), 15)
        step_dists = [math.hypot(path[i][0] - path[i-1][0], path[i][1] - path[i-1][1]) for i in range(1, len(path))]
        mean_v = sum(step_dists) / len(step_dists)
        std_v = math.sqrt(sum((v - mean_v)**2 for v in step_dists) / len(step_dists))
        fluctuation = std_v / (mean_v + 1e-6)
        # Software injectors have fluctuation ~ 0, while human thumb exhibits > 0.20 (typically ~0.40 - 0.55)
        self.assertGreater(fluctuation, 0.20, f"Expected velocity fluctuation > 0.20, got {fluctuation:.3f}")

    def test_path_redundancy_and_wander(self):
        import math
        start = (360, 1300)
        end = (360, 300)
        chord_dist = math.hypot(end[0] - start[0], end[1] - start[1])
        redundancies = []
        for _ in range(5):
            path = self.engine.generate_human_swipe_path(start, end, num_steps=16)
            path_len = sum(math.hypot(path[i][0] - path[i-1][0], path[i][1] - path[i-1][1]) for i in range(1, len(path)))
            redundancy = (path_len / chord_dist - 1.0) * 100.0
            redundancies.append(redundancy)
        avg_redundancy = sum(redundancies) / len(redundancies)
        self.assertGreater(avg_redundancy, 1.5, f"Expected average path redundancy > 1.5%, got {avg_redundancy:.2f}%")
        self.assertLess(avg_redundancy, 12.0, f"Expected average path redundancy < 12.0%, got {avg_redundancy:.2f}%")

    def test_click_dwell_and_micro_wobble(self):
        touch_events = []
        self.mock_device.touch.down = lambda x, y: touch_events.append(("down", x, y))
        self.mock_device.touch.move = lambda x, y: touch_events.append(("move", x, y))
        self.mock_device.touch.up = lambda x, y: touch_events.append(("up", x, y))

        with patch("human_behavior.time.sleep", return_value=None):
            x, y = self.engine.click((300, 500))

        self.assertTrue(len(touch_events) >= 2)
        down_ev = [ev for ev in touch_events if ev[0] == "down"][0]
        up_ev = [ev for ev in touch_events if ev[0] == "up"][0]
        self.assertLessEqual(abs(down_ev[1] - x), 3)
        self.assertLessEqual(abs(down_ev[2] - y), 3)
        self.assertLessEqual(abs(up_ev[1] - x), 4)
        self.assertLessEqual(abs(up_ev[2] - y), 4)

    def test_heavy_tailed_sleep_distribution(self):
        with patch("human_behavior.time.sleep", return_value=None):
            samples = [self.engine.sleep(1.0, jitter_ratio=0.3) for _ in range(200)]
        mean = sum(samples) / len(samples)
        var = sum((s - mean)**2 for s in samples) / len(samples)
        skewness = sum((s - mean)**3 for s in samples) / (len(samples) * (var ** 1.5))
        self.assertGreater(skewness, 0.1, f"Expected positive skewness for cognitive pause, got {skewness:.3f}")
        self.assertTrue(all(0.12 <= s <= 2.2 for s in samples))


if __name__ == "__main__":
    unittest.main()
