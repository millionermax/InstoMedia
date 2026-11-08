"""
login_flow.py - Account-Aware Login & Sequential Batch Automation for Instoper.
Supports single-account login with RFC 6238 TOTP authenticator generation,
challenge detection (pauses for manual action on CAPTCHA/SMS/Checkpoints),
and thread-safe sequential batch execution with isolated credential lifecycles.
"""

import time
import threading
from typing import Dict, Any, Optional, List, Callable
import pyotp
import uiautomator2 as u2

import db
import vault
import package_scanner
from human_behavior import HumanBehaviorEngine


class LoginResult:
    SUCCESS = "logged_in"
    ALREADY_LOGGED_IN = "already_logged_in"
    NEEDS_ACTION = "needs_action"
    FAILED_CREDENTIALS = "failed_credentials"
    PACKAGE_NOT_FOUND = "package_not_found"
    DEVICE_OFFLINE = "device_offline"
    ERROR = "error"
    CANCELLED = "cancelled"


class InstagramAccountLoginService:
    """Executes single-account authentication lifecycle on target device and package."""

    def __init__(self, device_serial: Optional[str] = None, log_callback: Optional[Callable[[str, str], None]] = None):
        self.device_serial = device_serial
        self.log_callback = log_callback
        self.d = None
        self.human = None

    def log(self, level: str, message: str, account_id: Optional[int] = None):
        if self.log_callback:
            self.log_callback(level, message)
        db.add_login_log(account_id=account_id, status=level, message=message)

    def _connect(self) -> bool:
        try:
            self.d = u2.connect(self.device_serial) if self.device_serial else u2.connect()
            self.human = HumanBehaviorEngine(self.d)
            return True
        except Exception as e:
            self.log("error", f"Could not connect to device '{self.device_serial or 'default'}': {e}")
            return False

    def is_already_logged_in(self, package_name: str) -> bool:
        """Inspects whether the active package is currently displaying an authenticated session."""
        if not self.d:
            return False

        # Common indicators of authenticated home / profile state in Instagram
        indicators = [
            self.d(resourceId=f"{package_name}:id/feed_tab"),
            self.d(resourceId=f"{package_name}:id/profile_tab"),
            self.d(resourceId=f"{package_name}:id/action_bar_container"),
            self.d(description="Home"),
            self.d(description="Profile"),
        ]
        return any(ind.exists for ind in indicators)

    def login_account(self, account_id: int, cancel_check: Optional[Callable[[], bool]] = None) -> str:
        """
        Executes single account login:
        1. Validates device connection & package availability.
        2. Retrieves credentials from protected vault.
        3. Launches package.
        4. Detects already-logged-in session.
        5. Fills username and password via HumanBehaviorEngine.
        6. Solves TOTP if authenticator challenge is presented.
        7. Pauses and sets NEEDS_ACTION if unsupported challenge is detected.
        """
        account = db.get_account_by_id(account_id)
        if not account:
            self.log("error", f"Account #{account_id} not found in database.")
            return LoginResult.ERROR

        username = account["username"]
        package_name = account["package_name"] or "com.instagram.android"
        serial = account["device_serial"] or self.device_serial

        self.log("info", f"Starting login process for account '{username}' on package '{package_name}'...", account_id)

        # Step 1: Connect to target device
        self.device_serial = serial
        if not self._connect():
            self.log("error", f"Device '{serial or 'default'}' is offline or unresponsive.", account_id)
            db.update_account_login_status(account_id, LoginResult.DEVICE_OFFLINE)
            return LoginResult.DEVICE_OFFLINE

        # Step 2: Verify package is installed
        if not package_scanner.is_package_installed(package_name, device_serial=self.device_serial):
            self.log("error", f"Package '{package_name}' is not installed on device '{self.device_serial}'.", account_id)
            db.update_account_login_status(account_id, LoginResult.PACKAGE_NOT_FOUND)
            return LoginResult.PACKAGE_NOT_FOUND

        if cancel_check and cancel_check():
            return LoginResult.CANCELLED

        # Step 3: Launch target package
        self.log("info", f"Launching package '{package_name}'...", account_id)
        try:
            self.d.app_start(package_name, stop=False)
            self.human.sleep(2.5, jitter_ratio=0.2)
        except Exception as e:
            self.log("error", f"Failed to launch package '{package_name}': {e}", account_id)
            return LoginResult.ERROR

        if cancel_check and cancel_check():
            return LoginResult.CANCELLED

        # Step 4: Check if already logged in
        if self.is_already_logged_in(package_name):
            self.log("success", f"Account '{username}' is already logged in!", account_id)
            db.update_account_login_status(account_id, LoginResult.SUCCESS)
            return LoginResult.ALREADY_LOGGED_IN

        # Step 5: Retrieve secrets from vault
        secrets = db.get_account_secrets(account_id)
        password = secrets.get("password")
        totp_seed = secrets.get("totp_seed")

        if not password:
            self.log("warning", f"No password found in protected vault for account '{username}'. Cannot proceed automatically.", account_id)
            db.update_account_login_status(account_id, LoginResult.NEEDS_ACTION)
            return LoginResult.NEEDS_ACTION

        # Step 6: Find login fields on screen
        # Try clicking "Log in" button if on landing splash screen
        splash_login = self.d(textMatches="(?i).*log in.*", clickable=True)
        if splash_login.exists and not self.d(resourceIdMatches="(?i).*password.*").exists:
            self.log("info", "Tapping 'Log in' on welcome splash screen...", account_id)
            self.human.click(splash_login)
            self.human.sleep(1.5)

        # Locate Username Input
        user_field = self.d(resourceId=f"{package_name}:id/login_username")
        if not user_field.exists:
            user_field = self.d(resourceIdMatches="(?i).*(login_username|username|identifier).*")
        if not user_field.exists:
            user_field = self.d(textMatches="(?i).*(phone number, email or username|username).*")

        # Locate Password Input
        pass_field = self.d(resourceId=f"{package_name}:id/password")
        if not pass_field.exists:
            pass_field = self.d(resourceIdMatches="(?i).*(password).*")
        if not pass_field.exists:
            pass_field = self.d(textMatches="(?i).*(password).*")

        if not user_field.exists or not pass_field.exists:
            # Check if an unsupported prompt or checkpoint is already present
            if self._check_unsupported_challenges(package_name, account_id):
                return LoginResult.NEEDS_ACTION

            self.log("warning", "Could not locate login credentials fields. Manual inspection required.", account_id)
            db.update_account_login_status(account_id, LoginResult.NEEDS_ACTION)
            return LoginResult.NEEDS_ACTION

        # Type username
        self.log("info", f"Entering username for '{username}'...", account_id)
        self.human.type_text(username, element=user_field)
        self.human.sleep(0.4)

        if cancel_check and cancel_check():
            return LoginResult.CANCELLED

        # Type password (without logging its value!)
        self.log("info", "Entering password from protected vault...", account_id)
        self.human.type_text(password, element=pass_field)
        self.human.sleep(0.5)

        # Dismiss keyboard so Log In button is visible
        self.d.shell("input keyevent 4")
        self.human.sleep(0.4)

        # Tap 'Log in' button
        login_btn = self.d(resourceId=f"{package_name}:id/next_button")
        if not login_btn.exists:
            login_btn = self.d(textMatches="(?i)^log in$")
        if not login_btn.exists:
            login_btn = self.d(resourceIdMatches="(?i).*(login_button|button_text|next_button).*")

        if login_btn.exists:
            self.log("info", "Tapping 'Log in' button...", account_id)
            self.human.click(login_btn)
        else:
            self.d.press("enter")

        # Step 7: Wait and evaluate post-login response screen
        self.log("info", "Awaiting login response from Instagram...", account_id)
        self.human.sleep(4.0)

        # Check loop for up to 20 seconds
        start_wait = time.time()
        while time.time() - start_wait < 20:
            if cancel_check and cancel_check():
                return LoginResult.CANCELLED

            # 1. Success check: is home feed visible?
            if self.is_already_logged_in(package_name):
                self.log("success", f"Successfully authenticated and logged in account '{username}'!", account_id)
                db.update_account_login_status(account_id, LoginResult.SUCCESS)
                return LoginResult.SUCCESS

            # 2. Check 2FA / TOTP Authenticator Challenge
            is_2fa = self.d(textMatches="(?i).*(security code|two-factor|authenticator app|enter code|6-digit).*").exists
            if is_2fa:
                self.log("info", "Two-Factor Authentication (2FA) challenge detected.", account_id)
                if not totp_seed:
                    self.log("warning", "No TOTP seed found in vault. Manual 2FA verification required.", account_id)
                    db.update_account_login_status(account_id, LoginResult.NEEDS_ACTION)
                    return LoginResult.NEEDS_ACTION

                return self._solve_totp_challenge(totp_seed, package_name, account_id)

            # 3. Check for invalid credentials
            invalid_banner = self.d(textMatches="(?i).*(incorrect password|wrong password|username not found|the password that you've entered is incorrect).*")
            if invalid_banner.exists:
                self.log("error", f"Login rejected: Invalid credentials for '{username}'.", account_id)
                db.update_account_login_status(account_id, LoginResult.FAILED_CREDENTIALS)
                return LoginResult.FAILED_CREDENTIALS

            # 4. Check for unsupported challenges (CAPTCHA, SMS, checkpoint, suspended)
            if self._check_unsupported_challenges(package_name, account_id):
                return LoginResult.NEEDS_ACTION

            # Handle "Save login info?" dialog if it appears
            save_info = self.d(textMatches="(?i).*(save your login info|save login info).*")
            if save_info.exists:
                save_btn = self.d(textMatches="(?i).*(save|save info|not now).*")
                if save_btn.exists:
                    self.human.click(save_btn)
                    self.human.sleep(1.5)
                    continue

            time.sleep(1.5)

        # If time expired without explicit resolution:
        if self.is_already_logged_in(package_name):
            db.update_account_login_status(account_id, LoginResult.SUCCESS)
            return LoginResult.SUCCESS

        self.log("warning", "Login response evaluation timed out. Check screen mirror.", account_id)
        db.update_account_login_status(account_id, LoginResult.NEEDS_ACTION)
        return LoginResult.NEEDS_ACTION

    def _solve_totp_challenge(self, totp_seed: str, package_name: str, account_id: int) -> str:
        """Generates RFC 6238 6-digit TOTP code and submits it."""
        try:
            clean_seed = totp_seed.replace(" ", "").replace("-", "").strip().upper()
            totp = pyotp.TOTP(clean_seed)
            code = totp.now()
            self.log("info", "Generated current 6-digit TOTP authenticator code.", account_id)

            # Locate 2FA code input
            code_input = self.d(focused=True)
            if not code_input.exists or not code_input.info.get("className", "").endswith("EditText"):
                code_input = self.d(resourceIdMatches="(?i).*(code|two_factor|security_code|verification).*")
            if not code_input.exists:
                code_input = self.d(className="android.widget.EditText")

            if code_input.exists:
                self.human.type_text(code, element=code_input)
                self.human.sleep(0.5)

                # Tap Confirm / Continue
                confirm_btn = self.d(textMatches="(?i).*(continue|confirm|next|submit).*")
                if confirm_btn.exists:
                    self.human.click(confirm_btn)
                else:
                    self.d.press("enter")

                self.human.sleep(3.5)

                if self.is_already_logged_in(package_name):
                    self.log("success", "TOTP verified! Account successfully authenticated.", account_id)
                    db.update_account_login_status(account_id, LoginResult.SUCCESS)
                    return LoginResult.SUCCESS
                else:
                    self.log("info", "Submitted TOTP code; verifying session state...", account_id)
                    self.human.sleep(2.0)
                    if self.is_already_logged_in(package_name):
                        db.update_account_login_status(account_id, LoginResult.SUCCESS)
                        return LoginResult.SUCCESS
            else:
                self.log("warning", "Could not locate 2FA code input field.", account_id)

        except Exception as e:
            self.log("error", f"TOTP code generation failed: {e}", account_id)

        db.update_account_login_status(account_id, LoginResult.NEEDS_ACTION)
        return LoginResult.NEEDS_ACTION

    def _check_unsupported_challenges(self, package_name: str, account_id: int) -> bool:
        """
        Detects security challenges that require human operator action:
        CAPTCHA, SMS/Phone, Email code, Checkpoint, Account Suspended.
        NEVER attempts bypass. Pauses and sets status to needs_action.
        """
        challenges = [
            ("Help us confirm that it's you", "Identity verification prompt"),
            ("Suspicious activity", "Suspicious activity checkpoint"),
            ("Verify your account", "Account verification challenge"),
            ("Enter the code we sent", "SMS or Email OTP challenge"),
            ("Security check", "Automated security challenge"),
            ("Login approval needed", "Two-device login approval"),
            ("Confirm you're a human", "Anti-bot CAPTCHA challenge"),
            ("We suspended your account", "Account suspension banner"),
        ]

        for trigger_text, challenge_name in challenges:
            elem = self.d(textMatches=f"(?i).*{trigger_text}.*")
            if elem.exists:
                self.log("warning", f"MANUAL ACTION REQUIRED: [{challenge_name}] detected. Workflow paused.", account_id)
                db.update_account_login_status(account_id, LoginResult.NEEDS_ACTION)
                return True

        return False


# ---------------------------------------------------------------------------
# Sequential Batch Login Manager
# ---------------------------------------------------------------------------
class BatchLoginManager:
    """Orchestrates sequential, isolated account logins on a connected device."""

    def __init__(self, log_callback: Optional[Callable[[str, str], None]] = None):
        self.log_callback = log_callback
        self.state = "idle"  # idle, running, stopped, completed
        self._stop_requested = False
        self._thread: Optional[threading.Thread] = None
        self.stats = {
            "total": 0,
            "current": 0,
            "success": 0,
            "needs_action": 0,
            "failed": 0,
            "current_account": None,
        }

    def get_status(self) -> Dict[str, Any]:
        return {
            "state": self.state,
            "stats": self.stats,
        }

    def stop(self):
        """Requests cancellation of ongoing batch operations."""
        self._stop_requested = True
        self.state = "stopped"
        if self.log_callback:
            self.log_callback("warning", "Batch login cancellation requested by operator.")

    def start(self, account_ids: Optional[List[int]] = None, device_serial: Optional[str] = None) -> bool:
        """Launches background sequential batch login thread."""
        if self.state == "running":
            return False

        if not account_ids:
            accounts = db.get_accounts(status="active")
            account_ids = [acc["id"] for acc in accounts]

        if not account_ids:
            if self.log_callback:
                self.log_callback("warning", "No active accounts selected for batch login.")
            return False

        self.state = "running"
        self._stop_requested = False
        self.stats = {
            "total": len(account_ids),
            "current": 0,
            "success": 0,
            "needs_action": 0,
            "failed": 0,
            "current_account": None,
        }

        self._thread = threading.Thread(
            target=self._run_batch_worker,
            args=(account_ids, device_serial),
            daemon=True
        )
        self._thread.start()
        return True

    def _run_batch_worker(self, account_ids: List[int], device_serial: Optional[str]):
        service = InstagramAccountLoginService(device_serial=device_serial, log_callback=self.log_callback)

        if self.log_callback:
            self.log_callback("info", f"Starting sequential batch login for {len(account_ids)} account(s)...")

        for idx, acc_id in enumerate(account_ids, start=1):
            if self._stop_requested:
                if self.log_callback:
                    self.log_callback("warning", f"Batch stopped. Remaining accounts ({len(account_ids) - idx + 1}) deferred.")
                break

            acc = db.get_account_by_id(acc_id)
            if not acc:
                continue

            self.stats["current"] = idx
            self.stats["current_account"] = acc["username"]

            if self.log_callback:
                self.log_callback("info", f"--- [{idx}/{len(account_ids)}] Processing account: {acc['username']} ---")

            # Execute single login with cancellation check
            result = service.login_account(acc_id, cancel_check=lambda: self._stop_requested)

            if result in (LoginResult.SUCCESS, LoginResult.ALREADY_LOGGED_IN):
                self.stats["success"] += 1
            elif result == LoginResult.NEEDS_ACTION:
                self.stats["needs_action"] += 1
            elif result == LoginResult.CANCELLED:
                break
            else:
                self.stats["failed"] += 1

            # Organic cool-down between accounts to prevent rapid-fire device throttling
            if not self._stop_requested and idx < len(account_ids):
                time.sleep(3.0)

        self.state = "completed" if not self._stop_requested else "stopped"
        self.stats["current_account"] = None

        if self.log_callback:
            self.log_callback(
                "success" if self.stats["failed"] == 0 else "info",
                f"Batch execution finished: {self.stats['success']} Succeeded, {self.stats['needs_action']} Need Action, {self.stats['failed']} Failed."
            )
