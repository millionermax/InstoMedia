"""
db.py - SQLite Database persistence and Account/Clone CRUD for Instoper.
Maintains relational tables for accounts, device/package clone bindings,
DPAPI-protected secrets, and audit logs.
"""

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Tuple

import vault

DEFAULT_DB_PATH = os.path.join(os.path.dirname(__file__), "data", "instoper.db")


@contextmanager
def get_connection(db_path: Optional[str] = None):
    path = db_path or DEFAULT_DB_PATH
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        yield conn
    finally:
        conn.close()


def init_db(db_path: Optional[str] = None):
    """Initializes tables and indexes if they do not exist."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()

        # 1. Accounts Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS accounts (
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

        # Migration: ensure 'tag' column exists if db was created earlier
        cursor.execute("PRAGMA table_info(accounts);")
        columns = [row["name"] for row in cursor.fetchall()]
        if "tag" not in columns:
            cursor.execute("ALTER TABLE accounts ADD COLUMN tag TEXT;")

        # Partial Unique Index: prevent two accounts mapping to same non-empty device/package pair
        cursor.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_account_device_package 
            ON accounts(device_serial, package_name) 
            WHERE device_serial IS NOT NULL AND device_serial != '' 
              AND package_name IS NOT NULL AND package_name != '';
        """)

        # 2. Clones / Discovered Packages Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS clones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_serial TEXT NOT NULL,
                package_name TEXT NOT NULL,
                app_name TEXT,
                is_confirmed INTEGER DEFAULT 0,
                last_seen_at TEXT NOT NULL,
                UNIQUE(device_serial, package_name)
            );
        """)

        # 3. Protected Secrets Table (DPAPI encrypted BLOBs)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS secrets (
                account_id INTEGER PRIMARY KEY,
                password_encrypted BLOB,
                totp_seed_encrypted BLOB,
                proxy_secret_encrypted BLOB,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE
            );
        """)

        # 4. Login and Activity Logs Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS login_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER,
                timestamp TEXT NOT NULL,
                status TEXT NOT NULL,
                message TEXT,
                FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE
            );
        """)
        conn.commit()


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# Account Management
# ---------------------------------------------------------------------------
def create_account(
    username: str,
    email: Optional[str] = None,
    device_serial: Optional[str] = None,
    package_name: Optional[str] = None,
    proxy_endpoint: Optional[str] = None,
    proxy_label: Optional[str] = None,
    tag: Optional[str] = None,
    notes: Optional[str] = None,
    status: str = "active",
    password: Optional[str] = None,
    totp_seed: Optional[str] = None,
    proxy_secret: Optional[str] = None,
    db_path: Optional[str] = None,
) -> int:
    """Creates a new account and securely stores optional credentials."""
    username = username.strip()
    if not username:
        raise ValueError("Username cannot be empty.")

    device_serial = device_serial.strip() if device_serial else None
    package_name = package_name.strip() if package_name else None
    tag = tag.strip() if tag else None

    # Sanitize proxy endpoint and extract authentication credentials into vault
    clean_proxy = proxy_endpoint
    if proxy_endpoint:
        parsed_endpoint, extracted_secret = vault.parse_proxy_credentials(proxy_endpoint)
        clean_proxy = parsed_endpoint
        if extracted_secret and not proxy_secret:
            proxy_secret = extracted_secret

    now = _utcnow()
    with get_connection(db_path) as conn:
        cursor = conn.cursor()

        # Check device/package collision before insert to provide clean error
        if device_serial and package_name:
            cursor.execute(
                "SELECT username FROM accounts WHERE device_serial = ? AND package_name = ?;",
                (device_serial, package_name),
            )
            existing = cursor.fetchone()
            if existing:
                raise ValueError(
                    f"Device '{device_serial}' and package '{package_name}' is already bound to account '{existing['username']}'."
                )

        cursor.execute(
            """
            INSERT INTO accounts (username, email, device_serial, package_name, 
                                proxy_endpoint, proxy_label, tag, notes, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (username, email, device_serial, package_name, clean_proxy, proxy_label, tag, notes, status, now, now),
        )
        account_id = cursor.lastrowid

        # Save secrets if provided
        if password or totp_seed or proxy_secret:
            enc_pwd = vault.dpapi_protect(password) if password else None
            enc_totp = vault.dpapi_protect(totp_seed) if totp_seed else None
            enc_proxy = vault.dpapi_protect(proxy_secret) if proxy_secret else None
            cursor.execute(
                """
                INSERT INTO secrets (account_id, password_encrypted, totp_seed_encrypted, proxy_secret_encrypted, updated_at)
                VALUES (?, ?, ?, ?, ?);
                """,
                (account_id, enc_pwd, enc_totp, enc_proxy, now),
            )
        conn.commit()
        return account_id


def update_account(
    account_id: int,
    username: Optional[str] = None,
    email: Optional[str] = None,
    device_serial: Optional[str] = None,
    package_name: Optional[str] = None,
    proxy_endpoint: Optional[str] = None,
    proxy_label: Optional[str] = None,
    tag: Optional[str] = None,
    notes: Optional[str] = None,
    status: Optional[str] = None,
    login_status: Optional[str] = None,
    db_path: Optional[str] = None,
) -> bool:
    """Updates account fields while enforcing constraints."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM accounts WHERE id = ?;", (account_id,))
        acc = cursor.fetchone()
        if not acc:
            return False

        new_user = username.strip() if username is not None else acc["username"]
        new_dev = device_serial.strip() if device_serial is not None else acc["device_serial"]
        new_pkg = package_name.strip() if package_name is not None else acc["package_name"]
        new_tag = tag.strip() if tag is not None else (acc["tag"] if "tag" in acc.keys() else None)

        clean_proxy = proxy_endpoint if proxy_endpoint is not None else acc["proxy_endpoint"]
        if proxy_endpoint is not None:
            parsed_endpoint, extracted_secret = vault.parse_proxy_credentials(proxy_endpoint)
            clean_proxy = parsed_endpoint
            if extracted_secret:
                save_account_secrets(account_id, proxy_secret=extracted_secret, db_path=db_path)

        # Check duplicate device/package constraint
        if new_dev and new_pkg:
            cursor.execute(
                "SELECT id, username FROM accounts WHERE device_serial = ? AND package_name = ? AND id != ?;",
                (new_dev, new_pkg, account_id),
            )
            conflict = cursor.fetchone()
            if conflict:
                raise ValueError(
                    f"Device '{new_dev}' and package '{new_pkg}' is already assigned to account '{conflict['username']}'."
                )

        now = _utcnow()
        cursor.execute(
            """
            UPDATE accounts SET
                username = ?,
                email = coalesce(?, email),
                device_serial = ?,
                package_name = ?,
                proxy_endpoint = ?,
                proxy_label = coalesce(?, proxy_label),
                tag = coalesce(?, tag),
                notes = coalesce(?, notes),
                status = coalesce(?, status),
                login_status = coalesce(?, login_status),
                updated_at = ?
            WHERE id = ?;
            """,
            (
                new_user,
                email,
                new_dev,
                new_pkg,
                clean_proxy,
                proxy_label,
                new_tag,
                notes,
                status,
                login_status,
                now,
                account_id,
            ),
        )
        conn.commit()
        return True


def delete_account(account_id: int, db_path: Optional[str] = None) -> bool:
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM accounts WHERE id = ?;", (account_id,))
        conn.commit()
        return cursor.rowcount > 0


def get_account_by_id(account_id: int, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM accounts WHERE id = ?;", (account_id,))
        row = cursor.fetchone()
        if not row:
            return None
        res = dict(row)
        res["proxy_endpoint"] = vault.redact_proxy_url(res.get("proxy_endpoint"))
        # Include secret flags (never the secrets themselves!)
        cursor.execute("SELECT password_encrypted, totp_seed_encrypted, proxy_secret_encrypted FROM secrets WHERE account_id = ?;", (account_id,))
        sec = cursor.fetchone()
        res["has_password"] = bool(sec and sec["password_encrypted"])
        res["has_totp"] = bool(sec and sec["totp_seed_encrypted"])
        res["has_proxy_secret"] = bool(sec and sec["proxy_secret_encrypted"])
        return res


def get_account_by_username(username: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM accounts WHERE username = ?;", (username.strip(),))
        row = cursor.fetchone()
        if not row:
            return None
        return get_account_by_id(row["id"], db_path=db_path)


def get_accounts(status: Optional[str] = None, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        query = "SELECT * FROM accounts"
        params = []
        if status:
            query += " WHERE status = ?"
            params.append(status)
        query += " ORDER BY id ASC;"
        cursor.execute(query, params)
        rows = cursor.fetchall()

        accounts = []
        for r in rows:
            acc = dict(r)
            acc["proxy_endpoint"] = vault.redact_proxy_url(acc.get("proxy_endpoint"))
            cursor.execute("SELECT password_encrypted, totp_seed_encrypted, proxy_secret_encrypted FROM secrets WHERE account_id = ?;", (acc["id"],))
            sec = cursor.fetchone()
            acc["has_password"] = bool(sec and sec["password_encrypted"])
            acc["has_totp"] = bool(sec and sec["totp_seed_encrypted"])
            acc["has_proxy_secret"] = bool(sec and sec["proxy_secret_encrypted"])
            accounts.append(acc)
        return accounts


def update_account_login_status(account_id: int, status: str, db_path: Optional[str] = None):
    now = _utcnow()
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE accounts SET login_status = ?, last_login_at = ?, updated_at = ? WHERE id = ?;",
            (status, now if status == "logged_in" else None, now, account_id),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Account Secrets (DPAPI-Protected)
# ---------------------------------------------------------------------------
def save_account_secrets(
    account_id: int,
    password: Optional[str] = None,
    totp_seed: Optional[str] = None,
    proxy_secret: Optional[str] = None,
    db_path: Optional[str] = None,
):
    """Saves or updates DPAPI-protected credentials for an account."""
    now = _utcnow()
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM secrets WHERE account_id = ?;", (account_id,))
        existing = cursor.fetchone()

        enc_pwd = vault.dpapi_protect(password) if password is not None else (existing["password_encrypted"] if existing else None)
        enc_totp = vault.dpapi_protect(totp_seed) if totp_seed is not None else (existing["totp_seed_encrypted"] if existing else None)
        enc_proxy = vault.dpapi_protect(proxy_secret) if proxy_secret is not None else (existing["proxy_secret_encrypted"] if existing else None)

        if existing:
            cursor.execute(
                """
                UPDATE secrets SET 
                    password_encrypted = ?,
                    totp_seed_encrypted = ?,
                    proxy_secret_encrypted = ?,
                    updated_at = ?
                WHERE account_id = ?;
                """,
                (enc_pwd, enc_totp, enc_proxy, now, account_id),
            )
        else:
            cursor.execute(
                """
                INSERT INTO secrets (account_id, password_encrypted, totp_seed_encrypted, proxy_secret_encrypted, updated_at)
                VALUES (?, ?, ?, ?, ?);
                """,
                (account_id, enc_pwd, enc_totp, enc_proxy, now),
            )
        conn.commit()


def get_account_secrets(account_id: int, db_path: Optional[str] = None) -> Dict[str, Optional[str]]:
    """Retrieves and decrypts secrets for a specific account. Used ONLY by login worker."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM secrets WHERE account_id = ?;", (account_id,))
        row = cursor.fetchone()
        if not row:
            return {"password": None, "totp_seed": None, "proxy_secret": None}

        pwd = vault.dpapi_unprotect(row["password_encrypted"])
        totp = vault.dpapi_unprotect(row["totp_seed_encrypted"])
        proxy = vault.dpapi_unprotect(row["proxy_secret_encrypted"])
        return {"password": pwd, "totp_seed": totp, "proxy_secret": proxy}


# ---------------------------------------------------------------------------
# Clone Package Management
# ---------------------------------------------------------------------------
def upsert_clone(
    device_serial: str,
    package_name: str,
    app_name: Optional[str] = None,
    is_confirmed: Optional[bool] = None,
    db_path: Optional[str] = None,
) -> int:
    now = _utcnow()
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, is_confirmed FROM clones WHERE device_serial = ? AND package_name = ?;",
            (device_serial, package_name),
        )
        existing = cursor.fetchone()
        if existing:
            confirmed_val = int(is_confirmed) if is_confirmed is not None else existing["is_confirmed"]
            cursor.execute(
                """
                UPDATE clones SET
                    app_name = coalesce(?, app_name),
                    is_confirmed = ?,
                    last_seen_at = ?
                WHERE id = ?;
                """,
                (app_name, confirmed_val, now, existing["id"]),
            )
            clone_id = existing["id"]
        else:
            confirmed_val = int(is_confirmed) if is_confirmed is not None else 0
            cursor.execute(
                """
                INSERT INTO clones (device_serial, package_name, app_name, is_confirmed, last_seen_at)
                VALUES (?, ?, ?, ?, ?);
                """,
                (device_serial, package_name, app_name, confirmed_val, now),
            )
            clone_id = cursor.lastrowid
        conn.commit()
        return clone_id


def set_clone_confirmed(clone_id: int, is_confirmed: bool = True, db_path: Optional[str] = None) -> bool:
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE clones SET is_confirmed = ? WHERE id = ?;", (int(is_confirmed), clone_id))
        conn.commit()
        return cursor.rowcount > 0


def delete_clone(clone_id: int, db_path: Optional[str] = None) -> bool:
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM clones WHERE id = ?;", (clone_id,))
        conn.commit()
        return cursor.rowcount > 0


def get_clones(
    device_serial: Optional[str] = None,
    confirmed_only: bool = False,
    db_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        query = "SELECT * FROM clones"
        clauses = []
        params = []
        if device_serial:
            clauses.append("device_serial = ?")
            params.append(device_serial)
        if confirmed_only:
            clauses.append("is_confirmed = 1")

        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY is_confirmed DESC, id ASC;"

        cursor.execute(query, params)
        return [dict(r) for r in cursor.fetchall()]


# ---------------------------------------------------------------------------
# Logging & Audit Trail
# ---------------------------------------------------------------------------
def add_login_log(account_id: Optional[int], status: str, message: str, db_path: Optional[str] = None):
    now = _utcnow()
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO login_logs (account_id, timestamp, status, message) VALUES (?, ?, ?, ?);",
            (account_id, now, status, message),
        )
        conn.commit()


def get_login_logs(account_id: Optional[int] = None, limit: int = 50, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        if account_id:
            cursor.execute(
                "SELECT * FROM login_logs WHERE account_id = ? ORDER BY id DESC LIMIT ?;",
                (account_id, limit),
            )
        else:
            cursor.execute("SELECT * FROM login_logs ORDER BY id DESC LIMIT ?;", (limit,))
        return [dict(r) for r in cursor.fetchall()]


# Auto-initialize database schema on module import
init_db()
