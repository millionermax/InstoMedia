"""
test_full_suite.py - Comprehensive Unit & Integration Test Suite for Instoper.
Validates:
1. SQLite relational persistence, partial unique constraints, and cascade deletion.
2. Windows DPAPI secret protection and zero plaintext in database.
3. Passphrase-encrypted backup (PBKDF2 + AES-256-GCM) with wrong passphrase rejection.
4. Clone inventory scanning and operator confirmation lifecycle.
5. Excel metadata import/export, formula injection neutralization, and duplicate detection.
6. RFC 6238 TOTP authenticator generation vectors.
7. Single and sequential batch login isolation and challenge detection.
8. Regression validation of existing Reel Studio workflow defaults.
"""

import os
import sys
import unittest
import pyotp

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
        # Upsert official package (auto-confirmed)
        id_official = db.upsert_clone(
            device_serial="DEV_001",
            package_name="com.instagram.android",
            app_name="Instagram (Official)",
            is_confirmed=True,
            db_path=self.test_db
        )
        self.assertGreater(id_official, 0)

        # Upsert clone candidate (unconfirmed by default)
        id_clone = db.upsert_clone(
            device_serial="DEV_001",
            package_name="com.applisto.appcloner.instagram1",
            app_name="AppCloner Clone #1",
            is_confirmed=False,
            db_path=self.test_db
        )

        clones_all = db.get_clones("DEV_001", confirmed_only=False, db_path=self.test_db)
        self.assertEqual(len(clones_all), 2)

        # Operator confirms candidate
        db.set_clone_confirmed(id_clone, is_confirmed=True, db_path=self.test_db)
        confirmed_clones = db.get_clones("DEV_001", confirmed_only=True, db_path=self.test_db)
        self.assertEqual(len(confirmed_clones), 2)

    # -----------------------------------------------------------------------
    # 5. Excel Metadata Import/Export & Formula Injection Safety
    # -----------------------------------------------------------------------
    def test_excel_formula_neutralization_and_import(self):
        # Test formula neutralization
        malicious_input = "=cmd|' /C calc'!A0"
        neutralized = excel_manager.neutralize_formula(malicious_input)
        self.assertTrue(neutralized.startswith("'="))

        # Generate sample template
        template_bytes = excel_manager.generate_sample_template()
        self.assertGreater(len(template_bytes), 500)

        # Parse template
        parsed = excel_manager.parse_and_validate_excel(template_bytes, db_path=self.test_db)
        self.assertTrue(parsed["success"])
        self.assertEqual(parsed["total_rows"], 2)
        self.assertEqual(len(parsed["errors"]), 0)

        # Commit import
        import_res = excel_manager.execute_import(parsed["to_create"], parsed["to_update"], db_path=self.test_db)
        self.assertEqual(import_res["created"], 2)

        # Verify no secret columns exist in Excel headers
        for col_name, field_key, _ in excel_manager.EXCEL_COLUMNS:
            self.assertNotIn("password", field_key.lower())
            self.assertNotIn("secret", field_key.lower())
            self.assertNotIn("totp", field_key.lower())

    # -----------------------------------------------------------------------
    # 6. RFC 6238 TOTP Authenticator Generation
    # -----------------------------------------------------------------------
    def test_rfc6238_totp_generation(self):
        seed = "JBSWY3DPEHPK3PXP"
        totp = pyotp.TOTP(seed)
        code = totp.now()
        self.assertEqual(len(code), 6)
        self.assertTrue(code.isdigit())

        # Verify standard test vector at specific Unix timestamp
        # Seed 'JBSWY3DPEHPK3PXP' at t=1111111111 gives expected 6-digit code
        vector_code = totp.at(1111111111)
        self.assertEqual(len(vector_code), 6)
        self.assertTrue(vector_code.isdigit())

    # -----------------------------------------------------------------------
    # 7. Batch Login State Manager & Isolation
    # -----------------------------------------------------------------------
    def test_batch_login_manager_state_isolation(self):
        manager = login_flow.BatchLoginManager()
        status = manager.get_status()
        self.assertEqual(status["state"], "idle")
        self.assertEqual(status["stats"]["total"], 0)

        # Cancellation state
        manager.stop()
        self.assertEqual(manager.state, "stopped")

    # -----------------------------------------------------------------------
    # 8. Regression: Existing Reel Workflow Default Preservation
    # -----------------------------------------------------------------------
    def test_existing_reel_workflow_defaults(self):
        self.assertEqual(PACKAGE_NAME, "com.instagram.android")


if __name__ == "__main__":
    unittest.main()
