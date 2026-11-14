"""
db.py - SQLite Database persistence and Account/Clone CRUD for Instoper.
Maintains relational tables for accounts, device/package clone bindings,
DPAPI-protected secrets, and audit logs.
"""

import json
import math
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Tuple
from urllib.parse import urlparse

try:
    from backend.storage import vault
except ImportError:
    try:
        from . import vault
    except ImportError:
        import vault

DEFAULT_DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data", "instoper.db"))
DEFAULT_AUDIO_URL = "https://www.instagram.com/reels/audio/1656648626250751/"
VALID_TAXONOMY_SCOPES = ("accounts", "content")
VALID_CAMPAIGN_AUTOMATION_IDS = ("reel_upload_with_music",)
VALID_CAMPAIGN_FAILURE_POLICIES = ("pause", "continue")
CAMPAIGN_COOLDOWN_MIN = 1
CAMPAIGN_COOLDOWN_MAX = 30
CAMPAIGN_COOLDOWN_DEFAULT = 3
_UNSET = object()


class ResourceInUseError(ValueError):
    """Raised when deleting or modifying a record referenced by campaigns or task history."""


class TaxonomyError(ValueError):
    """Base error for taxonomy validation and integrity rules."""


class TaxonomyDuplicateError(TaxonomyError):
    """Raised when a normalized category/tag already exists in the same scope."""


class TaxonomyInUseError(TaxonomyError):
    """Raised when deleting a category/tag that is still assigned."""


class OnboardingConflictError(ValueError):
    """Raised when onboarding hits duplicate username, duplicate binding, or handle mismatch."""

    def __init__(self, message: str, existing_account: Optional[Dict[str, Any]] = None, code: str = "conflict"):
        super().__init__(message)
        self.message = message
        self.existing_account = existing_account
        self.code = code


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


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def init_db(db_path: Optional[str] = None, recover_in_flight: bool = True):
    """Initializes tables, indexes, and idempotent schema migrations."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()

        # 0. Schema migrations log
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                name TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL
            );
        """)

        # 1. Scoped Categories & Tags Tables
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scope TEXT NOT NULL CHECK(scope IN ('accounts', 'content')),
                name TEXT NOT NULL,
                normalized_name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(scope, normalized_name)
            );
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scope TEXT NOT NULL CHECK(scope IN ('accounts', 'content')),
                name TEXT NOT NULL,
                normalized_name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(scope, normalized_name)
            );
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_categories_scope_norm
            ON categories(scope, normalized_name);
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_tags_scope_norm
            ON tags(scope, normalized_name);
        """)

        # 2. Accounts Table
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
                category_id INTEGER REFERENCES categories(id) ON DELETE RESTRICT,
                notes TEXT,
                status TEXT DEFAULT 'active',
                login_status TEXT DEFAULT 'idle',
                last_login_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
        """)

        # Migrations for legacy accounts table
        cursor.execute("PRAGMA table_info(accounts);")
        columns = [row["name"] for row in cursor.fetchall()]
        if "tag" not in columns:
            cursor.execute("ALTER TABLE accounts ADD COLUMN tag TEXT;")
        if "category_id" not in columns:
            cursor.execute("ALTER TABLE accounts ADD COLUMN category_id INTEGER REFERENCES categories(id) ON DELETE RESTRICT;")

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_accounts_category_id
            ON accounts(category_id);
        """)

        # Partial Unique Index: prevent two accounts mapping to same non-empty device/package pair
        cursor.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_account_device_package 
            ON accounts(device_serial, package_name) 
            WHERE device_serial IS NOT NULL AND device_serial != '' 
              AND package_name IS NOT NULL AND package_name != '';
        """)

        # 3. Account Tags Join Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS account_tags (
                account_id INTEGER NOT NULL,
                tag_id INTEGER NOT NULL,
                PRIMARY KEY (account_id, tag_id),
                FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE,
                FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE RESTRICT
            );
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_account_tags_tag_id
            ON account_tags(tag_id);
        """)

        # 4. Clones / Discovered Packages Table
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

        # 5. Protected Secrets Table (DPAPI encrypted BLOBs)
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

        # 6. Login and Activity Logs Table
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

        # 6b. Timestamped Profile Snapshots Table (Module 2)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS profile_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL,
                profile_handle TEXT NOT NULL,
                posts_count INTEGER,
                followers_count INTEGER,
                source_device_serial TEXT NOT NULL,
                source_package_name TEXT NOT NULL,
                capture_source TEXT NOT NULL DEFAULT 'ui',
                captured_at TEXT NOT NULL,
                FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE
            );
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_profile_snapshots_account_captured
            ON profile_snapshots(account_id, captured_at DESC, id DESC);
        """)

        # 7. Idempotent migration of legacy accounts.tag -> tags + account_tags
        cursor.execute(
            "SELECT 1 FROM schema_migrations WHERE name = 'migrate_legacy_account_tags';"
        )
        if not cursor.fetchone():
            now = _utcnow()
            cursor.execute(
                "SELECT id, tag FROM accounts WHERE tag IS NOT NULL AND TRIM(tag) != '';"
            )
            legacy_rows = cursor.fetchall()
            for r in legacy_rows:
                display_name = str(r["tag"]).strip()
                if not display_name:
                    continue
                norm = display_name.casefold()
                cursor.execute(
                    "SELECT id FROM tags WHERE scope = 'accounts' AND normalized_name = ?;",
                    (norm,),
                )
                tag_row = cursor.fetchone()
                if tag_row:
                    tag_id = tag_row["id"]
                else:
                    cursor.execute(
                        """
                        INSERT INTO tags (scope, name, normalized_name, created_at, updated_at)
                        VALUES ('accounts', ?, ?, ?, ?);
                        """,
                        (display_name, norm, now, now),
                    )
                    tag_id = cursor.lastrowid
                cursor.execute(
                    "INSERT OR IGNORE INTO account_tags (account_id, tag_id) VALUES (?, ?);",
                    (r["id"], tag_id),
                )

            cursor.execute(
                "INSERT INTO schema_migrations (name, applied_at) VALUES ('migrate_legacy_account_tags', ?);",
                (now,),
            )

        # Persistent Content Library metadata & content tags
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS content_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                original_filename TEXT NOT NULL,
                storage_key TEXT NOT NULL UNIQUE,
                media_type TEXT NOT NULL CHECK(media_type IN ('video', 'image')),
                mime_type TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                category_id INTEGER REFERENCES categories(id) ON DELETE RESTRICT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS content_tags (
                content_id INTEGER NOT NULL REFERENCES content_items(id) ON DELETE CASCADE,
                tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE RESTRICT,
                PRIMARY KEY (content_id, tag_id)
            );
            """
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_content_items_created ON content_items(created_at DESC, id DESC);"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_content_items_media_type ON content_items(media_type);"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_content_items_category_id ON content_items(category_id);"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_content_tags_tag_id ON content_tags(tag_id);"
        )
        cursor.execute("SELECT 1 FROM schema_migrations WHERE name = 'create_content_library_tables';")
        if not cursor.fetchone():
            cursor.execute(
                "INSERT INTO schema_migrations (name, applied_at) VALUES ('create_content_library_tables', ?);",
                (_utcnow(),),
            )

        # Campaigns and ordered per-account targets
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS campaigns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                automation_id TEXT NOT NULL DEFAULT 'reel_upload_with_music',
                audio_url TEXT NOT NULL DEFAULT '',
                hide_likes INTEGER NOT NULL DEFAULT 1,
                hide_shares INTEGER NOT NULL DEFAULT 1,
                cooldown_seconds INTEGER NOT NULL DEFAULT 3,
                failure_policy TEXT NOT NULL DEFAULT 'pause',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS campaign_targets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
                account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE RESTRICT,
                position INTEGER NOT NULL,
                content_id INTEGER NOT NULL REFERENCES content_items(id) ON DELETE RESTRICT,
                caption TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(campaign_id, account_id)
            );
            """
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_campaign_targets_campaign_pos ON campaign_targets(campaign_id, position);"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_campaign_targets_account ON campaign_targets(account_id);"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_campaign_targets_content ON campaign_targets(content_id);"
        )
        cursor.execute("SELECT 1 FROM schema_migrations WHERE name = 'create_campaign_tables';")
        if not cursor.fetchone():
            cursor.execute(
                "INSERT INTO schema_migrations (name, applied_at) VALUES ('create_campaign_tables', ?);",
                (_utcnow(),),
            )

        # Persistent campaign runs, tasks, and ordered task events
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS campaign_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE RESTRICT,
                campaign_name TEXT NOT NULL DEFAULT '',
                automation_id TEXT NOT NULL DEFAULT 'reel_upload_with_music',
                audio_url TEXT NOT NULL DEFAULT '',
                hide_likes INTEGER NOT NULL DEFAULT 1,
                hide_shares INTEGER NOT NULL DEFAULT 1,
                cooldown_seconds INTEGER NOT NULL DEFAULT 3,
                failure_policy TEXT NOT NULL DEFAULT 'pause',
                status TEXT NOT NULL DEFAULT 'queued',
                current_task_id INTEGER,
                cancel_requested INTEGER NOT NULL DEFAULT 0,
                error_message TEXT,
                started_at TEXT,
                completed_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL REFERENCES campaign_runs(id) ON DELETE CASCADE,
                campaign_id INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE RESTRICT,
                campaign_target_id INTEGER,
                sequence_number INTEGER NOT NULL,
                account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE RESTRICT,
                account_username TEXT NOT NULL DEFAULT '',
                device_serial TEXT,
                package_name TEXT,
                content_id INTEGER NOT NULL REFERENCES content_items(id) ON DELETE RESTRICT,
                content_filename TEXT NOT NULL DEFAULT '',
                caption TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                current_step INTEGER NOT NULL DEFAULT 0,
                current_step_label TEXT NOT NULL DEFAULT '',
                reel_url TEXT,
                error_message TEXT,
                cleanup_status TEXT,
                retry_requires_review INTEGER NOT NULL DEFAULT 0,
                failure_screenshot_path TEXT,
                failure_screenshot_captured_at TEXT,
                failure_screenshot_error TEXT,
                recording_status TEXT NOT NULL DEFAULT 'unavailable',
                recording_path TEXT,
                recording_duration_seconds REAL,
                recording_segments_json TEXT,
                recording_started_at TEXT,
                recording_completed_at TEXT,
                recording_error TEXT,
                started_at TEXT,
                completed_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(run_id, sequence_number)
            );
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS task_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                timestamp TEXT NOT NULL,
                level TEXT NOT NULL DEFAULT 'info',
                step INTEGER,
                stage TEXT,
                data_json TEXT,
                elapsed_ms INTEGER,
                message TEXT NOT NULL
            );
            """
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_campaign_runs_campaign_status ON campaign_runs(campaign_id, status);"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_tasks_run_seq ON tasks(run_id, sequence_number);"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_tasks_account ON tasks(account_id);"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_task_events_task_id ON task_events(task_id, id);"
        )
        cursor.execute("SELECT 1 FROM schema_migrations WHERE name = 'create_campaign_runs_and_tasks_tables';")
        if not cursor.fetchone():
            cursor.execute(
                "INSERT INTO schema_migrations (name, applied_at) VALUES ('create_campaign_runs_and_tasks_tables', ?);",
                (_utcnow(),),
            )

        # Idempotent migration: Add failure screenshot artifacts to tasks and structured context to task_events
        if _table_exists(cursor, "tasks"):
            task_cols = {col["name"] for col in cursor.execute("PRAGMA table_info(tasks);").fetchall()}
            if "failure_screenshot_path" not in task_cols:
                cursor.execute("ALTER TABLE tasks ADD COLUMN failure_screenshot_path TEXT;")
            if "failure_screenshot_captured_at" not in task_cols:
                cursor.execute("ALTER TABLE tasks ADD COLUMN failure_screenshot_captured_at TEXT;")
            if "failure_screenshot_error" not in task_cols:
                cursor.execute("ALTER TABLE tasks ADD COLUMN failure_screenshot_error TEXT;")

        if _table_exists(cursor, "task_events"):
            event_cols = {col["name"] for col in cursor.execute("PRAGMA table_info(task_events);").fetchall()}
            if "stage" not in event_cols:
                cursor.execute("ALTER TABLE task_events ADD COLUMN stage TEXT;")
            if "data_json" not in event_cols:
                cursor.execute("ALTER TABLE task_events ADD COLUMN data_json TEXT;")

        cursor.execute("SELECT 1 FROM schema_migrations WHERE name = 'rebuild_tasks_tab_artifacts_and_diagnostics';")
        if not cursor.fetchone():
            cursor.execute(
                "INSERT INTO schema_migrations (name, applied_at) VALUES ('rebuild_tasks_tab_artifacts_and_diagnostics', ?);",
                (_utcnow(),),
            )

        # Idempotent migration: Add per-task screen recording columns and task event elapsed_ms
        if _table_exists(cursor, "tasks"):
            task_cols = {col["name"] for col in cursor.execute("PRAGMA table_info(tasks);").fetchall()}
            if "recording_status" not in task_cols:
                cursor.execute("ALTER TABLE tasks ADD COLUMN recording_status TEXT NOT NULL DEFAULT 'unavailable';")
            if "recording_path" not in task_cols:
                cursor.execute("ALTER TABLE tasks ADD COLUMN recording_path TEXT;")
            if "recording_duration_seconds" not in task_cols:
                cursor.execute("ALTER TABLE tasks ADD COLUMN recording_duration_seconds REAL;")
            if "recording_segments_json" not in task_cols:
                cursor.execute("ALTER TABLE tasks ADD COLUMN recording_segments_json TEXT;")
            if "recording_started_at" not in task_cols:
                cursor.execute("ALTER TABLE tasks ADD COLUMN recording_started_at TEXT;")
            if "recording_completed_at" not in task_cols:
                cursor.execute("ALTER TABLE tasks ADD COLUMN recording_completed_at TEXT;")
            if "recording_error" not in task_cols:
                cursor.execute("ALTER TABLE tasks ADD COLUMN recording_error TEXT;")

        if _table_exists(cursor, "task_events"):
            event_cols = {col["name"] for col in cursor.execute("PRAGMA table_info(task_events);").fetchall()}
            if "elapsed_ms" not in event_cols:
                cursor.execute("ALTER TABLE task_events ADD COLUMN elapsed_ms INTEGER;")

        cursor.execute("SELECT 1 FROM schema_migrations WHERE name = 'add_task_recording_and_event_elapsed_columns';")
        if not cursor.fetchone():
            cursor.execute(
                "INSERT INTO schema_migrations (name, applied_at) VALUES ('add_task_recording_and_event_elapsed_columns', ?);",
                (_utcnow(),),
            )

        conn.commit()

    if recover_in_flight:
        recover_interrupted_in_flight_tasks(db_path=db_path)


def recover_interrupted_in_flight_tasks(db_path: Optional[str] = None) -> int:
    """Invoked explicitly on server boot to safely mark in-flight work interrupted after a hard crash."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        recovery_now = _utcnow()
        recovery_task_msg = (
            "Process restarted while task was in progress; marked interrupted and requires review before retrying."
        )
        cursor.execute(
            "SELECT id, error_message FROM tasks WHERE status IN ('running', 'closing_app', 'cooldown');"
        )
        interrupted_tasks = cursor.fetchall()
        for trow in interrupted_tasks:
            tid = trow["id"]
            msg = trow["error_message"] or recovery_task_msg
            cursor.execute(
                """
                UPDATE tasks
                SET status = 'interrupted',
                    retry_requires_review = 1,
                    error_message = ?,
                    completed_at = COALESCE(completed_at, ?),
                    updated_at = ?
                WHERE id = ?;
                """,
                (msg, recovery_now, recovery_now, tid),
            )
            cursor.execute(
                """
                INSERT INTO task_events (task_id, timestamp, level, step, message)
                VALUES (?, ?, 'warning', NULL, ?);
                """,
                (tid, recovery_now, recovery_task_msg),
            )

        cursor.execute(
            """
            UPDATE campaign_runs
            SET status = 'interrupted',
                current_task_id = NULL,
                error_message = COALESCE(
                    NULLIF(error_message, ''),
                    'Process restarted while campaign run was active; review interrupted tasks before continuing or retrying.'
                ),
                completed_at = COALESCE(completed_at, ?),
                updated_at = ?
            WHERE status IN ('queued', 'running');
            """,
            (recovery_now, recovery_now),
        )
        conn.commit()
        return len(interrupted_tasks)


# ---------------------------------------------------------------------------
# Scoped Categories & Tags (Accounts / Content)
# ---------------------------------------------------------------------------
def _validate_scope(scope: str) -> str:
    if scope not in VALID_TAXONOMY_SCOPES:
        raise ValueError(f"Invalid scope '{scope}'. Must be one of: {', '.join(VALID_TAXONOMY_SCOPES)}.")
    return scope


def _normalize_taxonomy_name(name: Any, forbid_semicolon: bool = False) -> Tuple[str, str]:
    if name is None:
        raise ValueError("Name cannot be empty.")
    display = str(name).strip()
    if not display:
        raise ValueError("Name cannot be empty.")
    if forbid_semicolon and ";" in display:
        raise ValueError("Tag name cannot contain ';'.")
    return display, display.casefold()


def _list_taxonomy(table: str, scope: str, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    _validate_scope(scope)
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT id, scope, name, normalized_name, created_at, updated_at FROM {table} WHERE scope = ? ORDER BY LOWER(name) ASC, id ASC;",
            (scope,),
        )
        return [dict(r) for r in cursor.fetchall()]


def _create_taxonomy(table: str, scope: str, name: str, forbid_semicolon: bool = False, db_path: Optional[str] = None) -> int:
    _validate_scope(scope)
    display, norm = _normalize_taxonomy_name(name, forbid_semicolon=forbid_semicolon)
    now = _utcnow()
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT id, name FROM {table} WHERE scope = ? AND normalized_name = ?;",
            (scope, norm),
        )
        if cursor.fetchone():
            label = "Category" if table == "categories" else "Tag"
            raise TaxonomyDuplicateError(f"{label} '{display}' already exists in '{scope}' scope.")
        cursor.execute(
            f"""
            INSERT INTO {table} (scope, name, normalized_name, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?);
            """,
            (scope, display, norm, now, now),
        )
        conn.commit()
        return cursor.lastrowid


def _rename_taxonomy(table: str, item_id: int, name: str, forbid_semicolon: bool = False, db_path: Optional[str] = None) -> bool:
    display, norm = _normalize_taxonomy_name(name, forbid_semicolon=forbid_semicolon)
    now = _utcnow()
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(f"SELECT id, scope FROM {table} WHERE id = ?;", (item_id,))
        row = cursor.fetchone()
        if not row:
            return False

        cursor.execute(
            f"SELECT id FROM {table} WHERE scope = ? AND normalized_name = ? AND id != ?;",
            (row["scope"], norm, item_id),
        )
        if cursor.fetchone():
            label = "Category" if table == "categories" else "Tag"
            raise TaxonomyDuplicateError(f"{label} '{display}' already exists in '{row['scope']}' scope.")

        cursor.execute(
            f"UPDATE {table} SET name = ?, normalized_name = ?, updated_at = ? WHERE id = ?;",
            (display, norm, now, item_id),
        )
        conn.commit()
        return cursor.rowcount > 0


def list_categories(scope: str, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    return _list_taxonomy("categories", scope, db_path=db_path)


def create_category(scope: str, name: str, db_path: Optional[str] = None) -> int:
    return _create_taxonomy("categories", scope, name, forbid_semicolon=False, db_path=db_path)


def rename_category(category_id: int, name: str, db_path: Optional[str] = None) -> bool:
    return _rename_taxonomy("categories", category_id, name, forbid_semicolon=False, db_path=db_path)


def delete_category(category_id: int, db_path: Optional[str] = None) -> bool:
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM categories WHERE id = ?;", (category_id,))
        if not cursor.fetchone():
            return False
        cursor.execute("SELECT COUNT(*) AS cnt FROM accounts WHERE category_id = ?;", (category_id,))
        if cursor.fetchone()["cnt"] > 0:
            raise TaxonomyInUseError("Category is assigned to one or more accounts and cannot be deleted.")
        cursor.execute("SELECT COUNT(*) AS cnt FROM content_items WHERE category_id = ?;", (category_id,))
        if cursor.fetchone()["cnt"] > 0:
            raise TaxonomyInUseError("Category is assigned to one or more content items and cannot be deleted.")
        cursor.execute("DELETE FROM categories WHERE id = ?;", (category_id,))
        conn.commit()
        return cursor.rowcount > 0


def list_tags(scope: str, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    return _list_taxonomy("tags", scope, db_path=db_path)


def create_tag(scope: str, name: str, db_path: Optional[str] = None) -> int:
    return _create_taxonomy("tags", scope, name, forbid_semicolon=True, db_path=db_path)


def rename_tag(tag_id: int, name: str, db_path: Optional[str] = None) -> bool:
    return _rename_taxonomy("tags", tag_id, name, forbid_semicolon=True, db_path=db_path)


def delete_tag(tag_id: int, db_path: Optional[str] = None) -> bool:
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM tags WHERE id = ?;", (tag_id,))
        if not cursor.fetchone():
            return False
        cursor.execute("SELECT COUNT(*) AS cnt FROM account_tags WHERE tag_id = ?;", (tag_id,))
        if cursor.fetchone()["cnt"] > 0:
            raise TaxonomyInUseError("Tag is assigned to one or more accounts and cannot be deleted.")
        cursor.execute("SELECT COUNT(*) AS cnt FROM content_tags WHERE tag_id = ?;", (tag_id,))
        if cursor.fetchone()["cnt"] > 0:
            raise TaxonomyInUseError("Tag is assigned to one or more content items and cannot be deleted.")
        cursor.execute("DELETE FROM tags WHERE id = ?;", (tag_id,))
        conn.commit()
        return cursor.rowcount > 0


def _validate_and_resolve_taxonomy_tx(
    cursor: sqlite3.Cursor,
    scope: str,
    category_id: Any,
    tag_ids: Any,
) -> Tuple[Any, Optional[List[Tuple[int, str]]]]:
    """Validates category_id and tag_ids belong to the requested scope before mutating DB."""
    if scope not in VALID_TAXONOMY_SCOPES:
        raise ValueError(f"Invalid taxonomy scope '{scope}'.")

    resolved_cat_id = category_id
    if category_id is not _UNSET and category_id is not None:
        resolved_cat_id = int(category_id)
        cursor.execute("SELECT id, scope FROM categories WHERE id = ?;", (resolved_cat_id,))
        cat = cursor.fetchone()
        if not cat:
            raise ValueError(f"Category #{resolved_cat_id} does not exist.")
        if cat["scope"] != scope:
            raise ValueError(f"Category #{resolved_cat_id} belongs to '{cat['scope']}' scope, not '{scope}'.")

    resolved_tags: Optional[List[Tuple[int, str]]] = None
    if tag_ids is not _UNSET:
        unique_ids = list(dict.fromkeys(int(t) for t in (tag_ids or [])))
        if unique_ids:
            placeholders = ",".join("?" for _ in unique_ids)
            cursor.execute(
                f"SELECT id, scope, name FROM tags WHERE id IN ({placeholders});",
                unique_ids,
            )
            found = {row["id"]: row for row in cursor.fetchall()}
            resolved_tags = []
            for tid in unique_ids:
                row = found.get(tid)
                if not row:
                    raise ValueError(f"Tag #{tid} does not exist.")
                if row["scope"] != scope:
                    raise ValueError(f"Tag #{tid} belongs to '{row['scope']}' scope, not '{scope}'.")
                resolved_tags.append((tid, row["name"]))
        else:
            resolved_tags = []

    return resolved_cat_id, resolved_tags


def _validate_and_resolve_account_taxonomy_tx(
    cursor: sqlite3.Cursor,
    category_id: Any,
    tag_ids: Any,
) -> Tuple[Any, Optional[List[Tuple[int, str]]]]:
    """Validates category_id and tag_ids belong to 'accounts' scope before mutating DB."""
    return _validate_and_resolve_taxonomy_tx(cursor, "accounts", category_id, tag_ids)


def _apply_account_taxonomy_tx(
    cursor: sqlite3.Cursor,
    account_id: int,
    resolved_cat_id: Any,
    resolved_tags: Optional[List[Tuple[int, str]]],
    now: str,
) -> None:
    if resolved_cat_id is not _UNSET:
        cursor.execute(
            "UPDATE accounts SET category_id = ?, updated_at = ? WHERE id = ?;",
            (resolved_cat_id, now, account_id),
        )

    if resolved_tags is not None:
        cursor.execute("DELETE FROM account_tags WHERE account_id = ?;", (account_id,))
        for tid, _ in resolved_tags:
            cursor.execute(
                "INSERT INTO account_tags (account_id, tag_id) VALUES (?, ?);",
                (account_id, tid),
            )
        joined_tag = "; ".join(name for _, name in resolved_tags) if resolved_tags else None
        cursor.execute(
            "UPDATE accounts SET tag = ?, updated_at = ? WHERE id = ?;",
            (joined_tag, now, account_id),
        )


def set_account_taxonomy(
    account_id: int,
    category_id: Optional[int],
    tag_ids: List[int],
    db_path: Optional[str] = None,
) -> None:
    """Atomically sets an account's category and tags within a single transaction."""
    now = _utcnow()
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM accounts WHERE id = ?;", (account_id,))
        if not cursor.fetchone():
            raise ValueError(f"Account #{account_id} does not exist.")

        resolved_cat_id, resolved_tags = _validate_and_resolve_account_taxonomy_tx(
            cursor, category_id, tag_ids
        )
        _apply_account_taxonomy_tx(cursor, account_id, resolved_cat_id, resolved_tags, now)
        conn.commit()


def _get_account_taxonomy_tx(cursor: sqlite3.Cursor, account_id: int, category_id: Optional[int]) -> Dict[str, Any]:
    category = None
    if category_id is not None:
        cursor.execute("SELECT id, scope, name FROM categories WHERE id = ?;", (category_id,))
        cat_row = cursor.fetchone()
        if cat_row:
            category = dict(cat_row)

    cursor.execute(
        """
        SELECT t.id, t.scope, t.name
        FROM tags t
        INNER JOIN account_tags at ON at.tag_id = t.id
        WHERE at.account_id = ?
        ORDER BY t.id ASC;
        """,
        (account_id,),
    )
    tags = [dict(r) for r in cursor.fetchall()]
    return {"category": category, "tags": tags}


def get_account_taxonomy(account_id: int, db_path: Optional[str] = None) -> Dict[str, Any]:
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, category_id FROM accounts WHERE id = ?;", (account_id,))
        acc = cursor.fetchone()
        if not acc:
            raise ValueError(f"Account #{account_id} does not exist.")
        return _get_account_taxonomy_tx(cursor, account_id, acc["category_id"])


def _get_or_create_account_tag_id_tx(cursor: sqlite3.Cursor, name: str, now: str) -> int:
    display, norm = _normalize_taxonomy_name(name, forbid_semicolon=True)
    cursor.execute(
        "SELECT id FROM tags WHERE scope = 'accounts' AND normalized_name = ?;",
        (norm,),
    )
    row = cursor.fetchone()
    if row:
        return row["id"]
    cursor.execute(
        """
        INSERT INTO tags (scope, name, normalized_name, created_at, updated_at)
        VALUES ('accounts', ?, ?, ?, ?);
        """,
        (display, norm, now, now),
    )
    return cursor.lastrowid


def _hydrate_account_row(cursor: sqlite3.Cursor, acc: Dict[str, Any]) -> Dict[str, Any]:
    acc["proxy_endpoint"] = vault.redact_proxy_url(acc.get("proxy_endpoint"))
    cursor.execute(
        "SELECT password_encrypted, totp_seed_encrypted, proxy_secret_encrypted FROM secrets WHERE account_id = ?;",
        (acc["id"],),
    )
    sec = cursor.fetchone()
    acc["has_password"] = bool(sec and sec["password_encrypted"])
    acc["has_totp"] = bool(sec and sec["totp_seed_encrypted"])
    acc["has_proxy_secret"] = bool(sec and sec["proxy_secret_encrypted"])

    tax = _get_account_taxonomy_tx(cursor, acc["id"], acc.get("category_id"))
    acc["category"] = tax["category"]
    acc["category_id"] = tax["category"]["id"] if tax["category"] else None
    acc["tags"] = tax["tags"]
    acc["tag_ids"] = [t["id"] for t in tax["tags"]]
    if tax["tags"]:
        acc["tag"] = "; ".join(t["name"] for t in tax["tags"])

    cursor.execute(
        """
        SELECT id, account_id, profile_handle, posts_count, followers_count,
               source_device_serial, source_package_name, capture_source, captured_at
        FROM profile_snapshots
        WHERE account_id = ?
        ORDER BY captured_at DESC, id DESC
        LIMIT 1;
        """,
        (acc["id"],),
    )
    snap_row = cursor.fetchone()
    acc["latest_snapshot"] = dict(snap_row) if snap_row else None
    return acc


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
    category_id: Any = _UNSET,
    tag_ids: Any = _UNSET,
    db_path: Optional[str] = None,
) -> int:
    """Creates a new account and securely stores optional credentials and taxonomy."""
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

        resolved_cat_id, resolved_tags = _validate_and_resolve_account_taxonomy_tx(
            cursor, category_id, tag_ids
        )

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

        cat_val = None if resolved_cat_id is _UNSET else resolved_cat_id
        cursor.execute(
            """
            INSERT INTO accounts (username, email, device_serial, package_name, 
                                proxy_endpoint, proxy_label, tag, category_id, notes, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (username, email, device_serial, package_name, clean_proxy, proxy_label, tag, cat_val, notes, status, now, now),
        )
        account_id = cursor.lastrowid

        if resolved_tags is not None:
            _apply_account_taxonomy_tx(cursor, account_id, _UNSET, resolved_tags, now)
        elif tag:
            tid = _get_or_create_account_tag_id_tx(cursor, tag, now)
            cursor.execute(
                "INSERT OR IGNORE INTO account_tags (account_id, tag_id) VALUES (?, ?);",
                (account_id, tid),
            )

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
    category_id: Any = _UNSET,
    tag_ids: Any = _UNSET,
    db_path: Optional[str] = None,
) -> bool:
    """Updates account fields and optional taxonomy while enforcing constraints."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM accounts WHERE id = ?;", (account_id,))
        acc = cursor.fetchone()
        if not acc:
            return False

        resolved_cat_id, resolved_tags = _validate_and_resolve_account_taxonomy_tx(
            cursor, category_id, tag_ids
        )

        new_user = username.strip() if username is not None else acc["username"]
        new_dev = device_serial.strip() if device_serial is not None else acc["device_serial"]
        new_pkg = package_name.strip() if package_name is not None else acc["package_name"]
        new_tag = tag.strip() if tag is not None else (acc["tag"] if "tag" in acc.keys() else None)

        clean_proxy = proxy_endpoint if proxy_endpoint is not None else acc["proxy_endpoint"]
        extracted_secret = None
        if proxy_endpoint is not None:
            parsed_endpoint, extracted_secret = vault.parse_proxy_credentials(proxy_endpoint)
            clean_proxy = parsed_endpoint

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

        _apply_account_taxonomy_tx(cursor, account_id, resolved_cat_id, resolved_tags, now)
        if resolved_tags is None and tag is not None and tag.strip():
            tid = _get_or_create_account_tag_id_tx(cursor, tag.strip(), now)
            cursor.execute("DELETE FROM account_tags WHERE account_id = ?;", (account_id,))
            cursor.execute(
                "INSERT INTO account_tags (account_id, tag_id) VALUES (?, ?);",
                (account_id, tid),
            )

        if extracted_secret:
            enc_proxy = vault.dpapi_protect(extracted_secret)
            cursor.execute("SELECT account_id FROM secrets WHERE account_id = ?;", (account_id,))
            if cursor.fetchone():
                cursor.execute(
                    "UPDATE secrets SET proxy_secret_encrypted = ?, updated_at = ? WHERE account_id = ?;",
                    (enc_proxy, now, account_id),
                )
            else:
                cursor.execute(
                    "INSERT INTO secrets (account_id, proxy_secret_encrypted, updated_at) VALUES (?, ?, ?);",
                    (account_id, enc_proxy, now),
                )

        conn.commit()
        return True


def _table_exists(cursor, table_name: str) -> bool:
    cursor.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?;",
        (table_name,),
    )
    return cursor.fetchone() is not None


def _ensure_account_deletable_tx(cursor, account_id: int) -> None:
    cursor.execute(
        """
        SELECT c.id, c.name
        FROM campaign_targets ct
        JOIN campaigns c ON c.id = ct.campaign_id
        WHERE ct.account_id = ?
        LIMIT 1;
        """,
        (account_id,),
    )
    row = cursor.fetchone()
    if row:
        raise ResourceInUseError(
            f"Cannot delete account #{account_id} because it is referenced by saved campaign '{row['name']}' (#{row['id']})."
        )
    if _table_exists(cursor, "tasks"):
        cursor.execute("SELECT id, run_id FROM tasks WHERE account_id = ? LIMIT 1;", (account_id,))
        t_row = cursor.fetchone()
        if t_row:
            raise ResourceInUseError(
                f"Cannot delete account #{account_id} because it is referenced by campaign run history (Run #{t_row['run_id']}, Task #{t_row['id']})."
            )


def _ensure_content_deletable_tx(cursor, content_id: int) -> None:
    cursor.execute(
        """
        SELECT c.id, c.name
        FROM campaign_targets ct
        JOIN campaigns c ON c.id = ct.campaign_id
        WHERE ct.content_id = ?
        LIMIT 1;
        """,
        (content_id,),
    )
    row = cursor.fetchone()
    if row:
        raise ResourceInUseError(
            f"Cannot delete content #{content_id} because it is referenced by saved campaign '{row['name']}' (#{row['id']})."
        )
    if _table_exists(cursor, "tasks"):
        cursor.execute("SELECT id, run_id FROM tasks WHERE content_id = ? LIMIT 1;", (content_id,))
        t_row = cursor.fetchone()
        if t_row:
            raise ResourceInUseError(
                f"Cannot delete content #{content_id} because it is referenced by campaign run history (Run #{t_row['run_id']}, Task #{t_row['id']})."
            )


def delete_account(account_id: int, db_path: Optional[str] = None) -> bool:
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM accounts WHERE id = ?;", (account_id,))
        if not cursor.fetchone():
            return False
        _ensure_account_deletable_tx(cursor, account_id)
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
        return _hydrate_account_row(cursor, dict(row))


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
        return [_hydrate_account_row(cursor, dict(r)) for r in rows]


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
# Profile Snapshots & Onboarding Confirmation
# ---------------------------------------------------------------------------
def _validate_metric_int(value: Any, field_name: str) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be a non-negative integer or null.")
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        if not stripped.isdigit():
            raise ValueError(f"{field_name} must be a non-negative integer or null.")
        parsed = int(stripped)
    elif isinstance(value, int):
        parsed = value
    else:
        raise ValueError(f"{field_name} must be a non-negative integer or null.")

    if parsed < 0:
        raise ValueError(f"{field_name} cannot be negative.")
    return parsed


def _insert_profile_snapshot_tx(
    cursor: sqlite3.Cursor,
    account_id: int,
    profile_handle: str,
    posts_count: Optional[int],
    followers_count: Optional[int],
    source_device_serial: str,
    source_package_name: str,
    capture_source: str,
    now: str,
) -> int:
    clean_handle = str(profile_handle or "").strip().lstrip("@")
    if not clean_handle:
        raise ValueError("Profile handle cannot be empty.")

    serial = str(source_device_serial or "").strip()
    if not serial:
        raise ValueError("Source device serial cannot be empty.")

    pkg = str(source_package_name or "").strip()
    if not pkg:
        raise ValueError("Source package name cannot be empty.")

    src = str(capture_source or "ui").strip().lower()
    if src not in ("ui", "manual", "mixed"):
        raise ValueError("capture_source must be 'ui', 'manual', or 'mixed'.")

    posts_val = _validate_metric_int(posts_count, "posts_count")
    followers_val = _validate_metric_int(followers_count, "followers_count")

    cursor.execute(
        """
        INSERT INTO profile_snapshots (
            account_id, profile_handle, posts_count, followers_count,
            source_device_serial, source_package_name, capture_source, captured_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?);
        """,
        (account_id, clean_handle, posts_val, followers_val, serial, pkg, src, now),
    )
    return cursor.lastrowid


def add_profile_snapshot(
    account_id: int,
    profile_handle: str,
    posts_count: Optional[int],
    followers_count: Optional[int],
    source_device_serial: str,
    source_package_name: str,
    capture_source: str = "ui",
    db_path: Optional[str] = None,
) -> int:
    """Persists a timestamped profile snapshot linked to an existing account."""
    now = _utcnow()
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM accounts WHERE id = ?;", (account_id,))
        if not cursor.fetchone():
            raise ValueError(f"Account #{account_id} does not exist.")
        snap_id = _insert_profile_snapshot_tx(
            cursor=cursor,
            account_id=account_id,
            profile_handle=profile_handle,
            posts_count=posts_count,
            followers_count=followers_count,
            source_device_serial=source_device_serial,
            source_package_name=source_package_name,
            capture_source=capture_source,
            now=now,
        )
        conn.commit()
        return snap_id


def get_profile_snapshots(
    account_id: int,
    limit: int = 20,
    db_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Returns newest-first profile snapshots for the given account."""
    bounded_limit = max(1, min(int(limit or 20), 100))
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, account_id, profile_handle, posts_count, followers_count,
                   source_device_serial, source_package_name, capture_source, captured_at
            FROM profile_snapshots
            WHERE account_id = ?
            ORDER BY captured_at DESC, id DESC
            LIMIT ?;
            """,
            (account_id, bounded_limit),
        )
        return [dict(r) for r in cursor.fetchall()]


def confirm_onboarding_account(
    device_serial: str,
    package_name: str,
    profile_handle: str,
    account_id: Optional[int] = None,
    posts_count: Optional[int] = None,
    followers_count: Optional[int] = None,
    capture_source: str = "ui",
    category_id: Any = _UNSET,
    tag_ids: Any = _UNSET,
    notes: Optional[str] = None,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Atomically persists an operator-confirmed onboarding result in one transaction:
    - If account_id is None: creates the new account + taxonomy + first snapshot.
      Rejects duplicate username or duplicate (device_serial, package_name) binding.
    - If account_id is set: requires exact handle match against account username,
      updates device/package binding (checking for conflicts with other accounts),
      and appends a new timestamped snapshot.
    """
    serial = str(device_serial or "").strip()
    pkg = str(package_name or "").strip()
    clean_handle = str(profile_handle or "").strip().lstrip("@")
    if not clean_handle:
        raise ValueError("Profile handle cannot be empty.")
    if not serial or not pkg:
        raise ValueError("Device serial and package name are required.")

    now = _utcnow()
    with get_connection(db_path) as conn:
        cursor = conn.cursor()

        resolved_cat_id, resolved_tags = _validate_and_resolve_account_taxonomy_tx(
            cursor, category_id, tag_ids
        )

        if account_id is None:
            cursor.execute(
                "SELECT * FROM accounts WHERE LOWER(username) = LOWER(?);",
                (clean_handle,),
            )
            dup_user = cursor.fetchone()
            if dup_user:
                existing_hydrated = _hydrate_account_row(cursor, dict(dup_user))
                raise OnboardingConflictError(
                    f"Account '@{dup_user['username']}' already exists. Select that existing account to append a snapshot instead of creating a duplicate.",
                    existing_account=existing_hydrated,
                    code="duplicate_username",
                )

            cursor.execute(
                "SELECT * FROM accounts WHERE device_serial = ? AND package_name = ?;",
                (serial, pkg),
            )
            dup_bind = cursor.fetchone()
            if dup_bind:
                existing_hydrated = _hydrate_account_row(cursor, dict(dup_bind))
                raise OnboardingConflictError(
                    f"Device '{serial}' and package '{pkg}' is already bound to account '@{dup_bind['username']}'.",
                    existing_account=existing_hydrated,
                    code="duplicate_binding",
                )

            cat_val = None if resolved_cat_id is _UNSET else resolved_cat_id
            cursor.execute(
                """
                INSERT INTO accounts (
                    username, device_serial, package_name, category_id,
                    notes, status, login_status, last_login_at, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, 'active', 'logged_in', ?, ?, ?);
                """,
                (clean_handle, serial, pkg, cat_val, notes, now, now, now),
            )
            target_account_id = cursor.lastrowid
            if resolved_tags is not None:
                _apply_account_taxonomy_tx(cursor, target_account_id, _UNSET, resolved_tags, now)
            created = True
        else:
            cursor.execute("SELECT * FROM accounts WHERE id = ?;", (account_id,))
            existing_row = cursor.fetchone()
            if not existing_row:
                raise LookupError(f"Account #{account_id} does not exist.")

            existing_hydrated = _hydrate_account_row(cursor, dict(existing_row))
            if clean_handle.casefold() != existing_row["username"].strip().lstrip("@").casefold():
                raise OnboardingConflictError(
                    f"Profile handle mismatch: captured '@{clean_handle}' does not match existing account '@{existing_row['username']}'.",
                    existing_account=existing_hydrated,
                    code="handle_mismatch",
                )

            cursor.execute(
                "SELECT * FROM accounts WHERE device_serial = ? AND package_name = ? AND id != ?;",
                (serial, pkg, account_id),
            )
            dup_bind = cursor.fetchone()
            if dup_bind:
                conflict_hydrated = _hydrate_account_row(cursor, dict(dup_bind))
                raise OnboardingConflictError(
                    f"Device '{serial}' and package '{pkg}' is already bound to account '@{dup_bind['username']}'.",
                    existing_account=conflict_hydrated,
                    code="duplicate_binding",
                )

            cursor.execute(
                """
                UPDATE accounts SET
                    device_serial = ?,
                    package_name = ?,
                    notes = coalesce(?, notes),
                    login_status = 'logged_in',
                    last_login_at = ?,
                    updated_at = ?
                WHERE id = ?;
                """,
                (serial, pkg, notes, now, now, account_id),
            )
            _apply_account_taxonomy_tx(cursor, account_id, resolved_cat_id, resolved_tags, now)
            target_account_id = account_id
            created = False

        snap_id = _insert_profile_snapshot_tx(
            cursor=cursor,
            account_id=target_account_id,
            profile_handle=clean_handle,
            posts_count=posts_count,
            followers_count=followers_count,
            source_device_serial=serial,
            source_package_name=pkg,
            capture_source=capture_source,
            now=now,
        )
        conn.commit()

        cursor.execute("SELECT * FROM accounts WHERE id = ?;", (target_account_id,))
        final_acc = _hydrate_account_row(cursor, dict(cursor.fetchone()))
        cursor.execute(
            """
            SELECT id, account_id, profile_handle, posts_count, followers_count,
                   source_device_serial, source_package_name, capture_source, captured_at
            FROM profile_snapshots
            WHERE id = ?;
            """,
            (snap_id,),
        )
        final_snap = dict(cursor.fetchone())
        return {"account": final_acc, "snapshot": final_snap, "created": created}


# ---------------------------------------------------------------------------
# Logging & Audit Trail
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Content Library CRUD & Filtering
# ---------------------------------------------------------------------------

def _hydrate_content_rows(cursor: sqlite3.Cursor, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Batch-loads content-scoped categories and tags onto content item rows."""
    if not rows:
        return []

    cat_ids = sorted({r["category_id"] for r in rows if r.get("category_id") is not None})
    cat_map: Dict[int, Dict[str, Any]] = {}
    if cat_ids:
        placeholders = ",".join("?" for _ in cat_ids)
        cursor.execute(
            f"SELECT id, scope, name FROM categories WHERE id IN ({placeholders});",
            cat_ids,
        )
        cat_map = {c["id"]: dict(c) for c in cursor.fetchall()}

    item_ids = [r["id"] for r in rows]
    tag_map: Dict[int, List[Dict[str, Any]]] = {cid: [] for cid in item_ids}
    placeholders = ",".join("?" for _ in item_ids)
    cursor.execute(
        f"""
        SELECT ct.content_id, t.id, t.scope, t.name
        FROM content_tags ct
        INNER JOIN tags t ON t.id = ct.tag_id
        WHERE ct.content_id IN ({placeholders})
        ORDER BY t.id ASC;
        """,
        item_ids,
    )
    for trow in cursor.fetchall():
        tag_map[trow["content_id"]].append(
            {"id": trow["id"], "scope": trow["scope"], "name": trow["name"]}
        )

    for item in rows:
        cat = cat_map.get(item.get("category_id"))
        tags = tag_map.get(item["id"], [])
        item["category"] = cat
        item["tags"] = tags
        item["tag_ids"] = [t["id"] for t in tags]
    return rows


def create_content_item(
    original_filename: str,
    storage_key: str,
    media_type: str,
    mime_type: str,
    size_bytes: int,
    category_id: Optional[int] = None,
    tag_ids: Optional[List[int]] = None,
    db_path: Optional[str] = None,
) -> int:
    """Creates a persistent Content Library record with validated content-scoped taxonomy."""
    if media_type not in ("video", "image"):
        raise ValueError(f"Unsupported media_type '{media_type}'.")
    clean_original = (original_filename or "").strip()
    clean_key = (storage_key or "").strip()
    clean_mime = (mime_type or "").strip() or "application/octet-stream"
    if not clean_original or not clean_key:
        raise ValueError("original_filename and storage_key are required.")
    if int(size_bytes) < 0:
        raise ValueError("size_bytes must be non-negative.")

    now = _utcnow()
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        resolved_cat_id, resolved_tags = _validate_and_resolve_taxonomy_tx(
            cursor, "content", category_id, [] if tag_ids is None else tag_ids
        )
        cursor.execute(
            """
            INSERT INTO content_items (
                original_filename, storage_key, media_type, mime_type,
                size_bytes, category_id, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (clean_original, clean_key, media_type, clean_mime, int(size_bytes), resolved_cat_id, now, now),
        )
        content_id = cursor.lastrowid
        for tid, _ in (resolved_tags or []):
            cursor.execute(
                "INSERT INTO content_tags (content_id, tag_id) VALUES (?, ?);",
                (content_id, tid),
            )
        conn.commit()
        return content_id


def get_content_item(content_id: int, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM content_items WHERE id = ?;", (content_id,))
        row = cursor.fetchone()
        if not row:
            return None
        return _hydrate_content_rows(cursor, [dict(row)])[0]


def list_content_items(
    search: Optional[str] = None,
    media_type: Optional[str] = None,
    category_id: Optional[int] = None,
    tag_id: Optional[int] = None,
    page: int = 1,
    per_page: int = 50,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Lists Content Library items with SQL filtering and bounded pagination (max 100)."""
    safe_page = max(1, int(page or 1))
    safe_per_page = min(100, max(1, int(per_page or 50)))

    clauses: List[str] = []
    params: List[Any] = []

    if search and str(search).strip():
        clauses.append("LOWER(ci.original_filename) LIKE ?")
        params.append(f"%{str(search).strip().lower()}%")
    if media_type in ("video", "image"):
        clauses.append("ci.media_type = ?")
        params.append(media_type)
    if category_id is not None and str(category_id).strip() != "":
        clauses.append("ci.category_id = ?")
        params.append(int(category_id))
    if tag_id is not None and str(tag_id).strip() != "":
        clauses.append("EXISTS (SELECT 1 FROM content_tags ct WHERE ct.content_id = ci.id AND ct.tag_id = ?)")
        params.append(int(tag_id))

    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    offset = (safe_page - 1) * safe_per_page

    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(f"SELECT COUNT(*) AS total FROM content_items ci {where_sql};", params)
        total = int(cursor.fetchone()["total"])
        pages = math.ceil(total / safe_per_page) if total > 0 else 0

        cursor.execute(
            f"""
            SELECT ci.*
            FROM content_items ci
            {where_sql}
            ORDER BY ci.created_at DESC, ci.id DESC
            LIMIT ? OFFSET ?;
            """,
            [*params, safe_per_page, offset],
        )
        rows = [dict(r) for r in cursor.fetchall()]
        items = _hydrate_content_rows(cursor, rows)
        return {
            "items": items,
            "total": total,
            "page": safe_page,
            "per_page": safe_per_page,
            "pages": pages,
        }


def update_content_item_taxonomy(
    content_id: int,
    category_id: Any = _UNSET,
    tag_ids: Any = _UNSET,
    db_path: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Atomically validates and updates a content item's content-scoped category and tags."""
    now = _utcnow()
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM content_items WHERE id = ?;", (content_id,))
        if not cursor.fetchone():
            return None

        resolved_cat_id, resolved_tags = _validate_and_resolve_taxonomy_tx(
            cursor, "content", category_id, tag_ids
        )
        if resolved_cat_id is not _UNSET:
            cursor.execute(
                "UPDATE content_items SET category_id = ?, updated_at = ? WHERE id = ?;",
                (resolved_cat_id, now, content_id),
            )
        if resolved_tags is not None:
            cursor.execute("DELETE FROM content_tags WHERE content_id = ?;", (content_id,))
            for tid, _ in resolved_tags:
                cursor.execute(
                    "INSERT INTO content_tags (content_id, tag_id) VALUES (?, ?);",
                    (content_id, tid),
                )
            cursor.execute(
                "UPDATE content_items SET updated_at = ? WHERE id = ?;",
                (now, content_id),
            )
        conn.commit()
        cursor.execute("SELECT * FROM content_items WHERE id = ?;", (content_id,))
        row = cursor.fetchone()
        return _hydrate_content_rows(cursor, [dict(row)])[0]


def delete_content_item(content_id: int, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Deletes a content item record (cascading content_tags) and returns the removed row."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM content_items WHERE id = ?;", (content_id,))
        row = cursor.fetchone()
        if not row:
            return None
        _ensure_content_deletable_tx(cursor, content_id)
        deleted = _hydrate_content_rows(cursor, [dict(row)])[0]
        cursor.execute("DELETE FROM content_items WHERE id = ?;", (content_id,))
        conn.commit()
        return deleted


# ---------------------------------------------------------------------------
# Campaigns & Ordered Targets
# ---------------------------------------------------------------------------
def validate_instagram_audio_url(raw_url: Optional[str], require_nonempty: bool = False) -> str:
    cleaned = str(raw_url or "").strip()
    if not cleaned:
        if require_nonempty:
            raise ValueError("Instagram audio URL is required.")
        return DEFAULT_AUDIO_URL

    parsed = urlparse(cleaned)
    host = (parsed.hostname or "").lower()
    path = parsed.path or ""
    scheme = (parsed.scheme or "").lower()
    if scheme == "http" and (host == "instagram.com" or host.endswith(".instagram.com")):
        raise ValueError("Audio URL must use https:// (Android App Links reject plain http://). Use https://www.instagram.com/reels/audio/... .")
    if scheme != "https" or not (host == "instagram.com" or host.endswith(".instagram.com")):
        raise ValueError("Audio URL must be a valid https://www.instagram.com/reels/audio/... link.")
    if not any(
        path.startswith(pfx) and len(path.rstrip("/")) >= len(pfx.rstrip("/")) + 1 and len(path) > len(pfx)
        for pfx in ("/reels/audio/", "/audio/")
    ):
        raise ValueError("Audio URL must point to an Instagram audio track path (/reels/audio/<id>/).")
    return cleaned


def _validate_campaign_scalar_fields(
    name: str,
    automation_id: str,
    audio_url: Optional[str],
    hide_likes: Any,
    hide_shares: Any,
    cooldown_seconds: Any,
    failure_policy: str,
) -> Dict[str, Any]:
    clean_name = str(name or "").strip()
    if not clean_name:
        raise ValueError("Campaign name is required.")
    if len(clean_name) > 120:
        raise ValueError("Campaign name must be 120 characters or fewer.")

    clean_auto = str(automation_id or "reel_upload_with_music").strip()
    if clean_auto not in VALID_CAMPAIGN_AUTOMATION_IDS:
        raise ValueError(
            f"Unsupported automation_id '{clean_auto}'. Only '{VALID_CAMPAIGN_AUTOMATION_IDS[0]}' is supported in this release."
        )

    clean_audio = validate_instagram_audio_url(audio_url, require_nonempty=True)

    if isinstance(cooldown_seconds, bool):
        raise ValueError("cooldown_seconds must be an integer between 1 and 30.")
    try:
        cooldown_val = int(cooldown_seconds)
    except (TypeError, ValueError):
        raise ValueError("cooldown_seconds must be an integer between 1 and 30.")
    if cooldown_val < CAMPAIGN_COOLDOWN_MIN or cooldown_val > CAMPAIGN_COOLDOWN_MAX:
        raise ValueError(
            f"cooldown_seconds must be between {CAMPAIGN_COOLDOWN_MIN} and {CAMPAIGN_COOLDOWN_MAX}."
        )

    clean_policy = str(failure_policy or "pause").strip().lower()
    if clean_policy not in VALID_CAMPAIGN_FAILURE_POLICIES:
        raise ValueError("failure_policy must be 'pause' or 'continue'.")

    return {
        "name": clean_name,
        "automation_id": clean_auto,
        "audio_url": clean_audio,
        "hide_likes": 1 if bool(hide_likes) else 0,
        "hide_shares": 1 if bool(hide_shares) else 0,
        "cooldown_seconds": cooldown_val,
        "failure_policy": clean_policy,
    }


def _validate_campaign_targets_tx(cursor, targets: Any) -> List[Dict[str, Any]]:
    if not isinstance(targets, list) or not targets:
        raise ValueError("Campaign must include at least one ordered account target.")

    seen_accounts = set()
    validated: List[Dict[str, Any]] = []
    for idx, entry in enumerate(targets):
        if not isinstance(entry, dict):
            raise ValueError(f"Target at position {idx + 1} must be an object.")
        raw_acc = entry.get("account_id")
        raw_content = entry.get("content_id")
        if raw_acc is None or isinstance(raw_acc, bool):
            raise ValueError(f"Target #{idx + 1}: account_id is required.")
        if raw_content is None or isinstance(raw_content, bool):
            raise ValueError(f"Target #{idx + 1}: video content_id is required.")
        try:
            acc_id = int(raw_acc)
            content_id = int(raw_content)
        except (TypeError, ValueError):
            raise ValueError(f"Target #{idx + 1}: account_id and content_id must be integers.")

        if acc_id in seen_accounts:
            raise ValueError(f"Account #{acc_id} cannot appear more than once in the same campaign.")
        seen_accounts.add(acc_id)

        cursor.execute("SELECT id, username FROM accounts WHERE id = ?;", (acc_id,))
        acc_row = cursor.fetchone()
        if not acc_row:
            raise ValueError(f"Target #{idx + 1}: account #{acc_id} does not exist.")

        cursor.execute(
            "SELECT id, original_filename, media_type FROM content_items WHERE id = ?;",
            (content_id,),
        )
        content_row = cursor.fetchone()
        if not content_row:
            raise ValueError(f"Target #{idx + 1}: content item #{content_id} does not exist.")
        if content_row["media_type"] != "video":
            raise ValueError(
                f"Target #{idx + 1} (@{acc_row['username']}): '{content_row['original_filename']}' is not a video. Only video items can be used in Reel campaigns."
            )

        caption = str(entry.get("caption") if entry.get("caption") is not None else "")
        validated.append(
            {
                "account_id": acc_id,
                "position": idx,
                "content_id": content_id,
                "caption": caption,
            }
        )
    return validated


def _hydrate_campaigns_tx(cursor, campaign_rows: List[sqlite3.Row]) -> List[Dict[str, Any]]:
    if not campaign_rows:
        return []
    campaign_ids = [int(r["id"]) for r in campaign_rows]
    placeholders = ",".join("?" for _ in campaign_ids)
    cursor.execute(
        f"""
        SELECT
            ct.id,
            ct.campaign_id,
            ct.account_id,
            ct.position,
            ct.content_id,
            ct.caption,
            ct.created_at,
            ct.updated_at,
            a.username AS account_username,
            a.device_serial AS account_device_serial,
            a.package_name AS account_package_name,
            a.category_id AS account_category_id,
            ci.original_filename AS content_filename,
            ci.media_type AS content_media_type,
            ci.mime_type AS content_mime_type,
            ci.storage_key AS content_storage_key
        FROM campaign_targets ct
        JOIN accounts a ON a.id = ct.account_id
        JOIN content_items ci ON ci.id = ct.content_id
        WHERE ct.campaign_id IN ({placeholders})
        ORDER BY ct.campaign_id ASC, ct.position ASC, ct.id ASC;
        """,
        campaign_ids,
    )
    targets_by_campaign: Dict[int, List[Dict[str, Any]]] = {cid: [] for cid in campaign_ids}
    for row in cursor.fetchall():
        d = dict(row)
        d["content_file_url"] = f"/api/content/{d['content_id']}/file"
        d.pop("content_storage_key", None)
        targets_by_campaign[int(d["campaign_id"])].append(d)

    run_counts: Dict[int, Dict[str, Any]] = {}
    if _table_exists(cursor, "campaign_runs"):
        cursor.execute(
            f"""
            SELECT campaign_id,
                   COUNT(*) AS run_count,
                   MAX(id) AS latest_run_id
            FROM campaign_runs
            WHERE campaign_id IN ({placeholders})
            GROUP BY campaign_id;
            """,
            campaign_ids,
        )
        for rr in cursor.fetchall():
            run_counts[int(rr["campaign_id"])] = {
                "run_count": int(rr["run_count"]),
                "latest_run_id": int(rr["latest_run_id"]) if rr["latest_run_id"] is not None else None,
            }

    campaigns: List[Dict[str, Any]] = []
    for row in campaign_rows:
        c = dict(row)
        cid = int(c["id"])
        c["hide_likes"] = bool(c["hide_likes"])
        c["hide_shares"] = bool(c["hide_shares"])
        c["cooldown_seconds"] = int(c["cooldown_seconds"])
        targets = targets_by_campaign.get(cid, [])
        c["targets"] = targets
        c["target_count"] = len(targets)
        meta = run_counts.get(cid, {})
        c["run_count"] = meta.get("run_count", 0)
        c["latest_run_id"] = meta.get("latest_run_id")
        campaigns.append(c)
    return campaigns


def create_campaign(
    name: str,
    targets: List[Dict[str, Any]],
    automation_id: str = "reel_upload_with_music",
    audio_url: Optional[str] = None,
    hide_likes: bool = True,
    hide_shares: bool = True,
    cooldown_seconds: int = CAMPAIGN_COOLDOWN_DEFAULT,
    failure_policy: str = "pause",
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    fields = _validate_campaign_scalar_fields(
        name=name,
        automation_id=automation_id,
        audio_url=audio_url,
        hide_likes=hide_likes,
        hide_shares=hide_shares,
        cooldown_seconds=cooldown_seconds,
        failure_policy=failure_policy,
    )
    now = _utcnow()
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        validated_targets = _validate_campaign_targets_tx(cursor, targets)
        cursor.execute(
            """
            INSERT INTO campaigns (
                name, automation_id, audio_url, hide_likes, hide_shares,
                cooldown_seconds, failure_policy, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                fields["name"],
                fields["automation_id"],
                fields["audio_url"],
                fields["hide_likes"],
                fields["hide_shares"],
                fields["cooldown_seconds"],
                fields["failure_policy"],
                now,
                now,
            ),
        )
        campaign_id = cursor.lastrowid
        for t in validated_targets:
            cursor.execute(
                """
                INSERT INTO campaign_targets (
                    campaign_id, account_id, position, content_id, caption, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?);
                """,
                (campaign_id, t["account_id"], t["position"], t["content_id"], t["caption"], now, now),
            )
        conn.commit()
        cursor.execute("SELECT * FROM campaigns WHERE id = ?;", (campaign_id,))
        return _hydrate_campaigns_tx(cursor, [cursor.fetchone()])[0]


def get_campaign(campaign_id: int, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM campaigns WHERE id = ?;", (campaign_id,))
        row = cursor.fetchone()
        if not row:
            return None
        return _hydrate_campaigns_tx(cursor, [row])[0]


def list_campaigns(db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM campaigns ORDER BY updated_at DESC, id DESC;")
        return _hydrate_campaigns_tx(cursor, cursor.fetchall())


def update_campaign(
    campaign_id: int,
    name: Any = _UNSET,
    automation_id: Any = _UNSET,
    audio_url: Any = _UNSET,
    hide_likes: Any = _UNSET,
    hide_shares: Any = _UNSET,
    cooldown_seconds: Any = _UNSET,
    failure_policy: Any = _UNSET,
    targets: Any = _UNSET,
    db_path: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    now = _utcnow()
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM campaigns WHERE id = ?;", (campaign_id,))
        existing = cursor.fetchone()
        if not existing:
            return None

        if _table_exists(cursor, "campaign_runs"):
            cursor.execute(
                "SELECT id, status FROM campaign_runs WHERE campaign_id = ? AND status IN ('queued', 'running') LIMIT 1;",
                (campaign_id,),
            )
            active_run = cursor.fetchone()
            if active_run:
                raise ResourceInUseError(
                    f"Cannot edit campaign #{campaign_id} while Run #{active_run['id']} is {active_run['status']}."
                )

        fields = _validate_campaign_scalar_fields(
            name=existing["name"] if name is _UNSET else name,
            automation_id=existing["automation_id"] if automation_id is _UNSET else automation_id,
            audio_url=existing["audio_url"] if audio_url is _UNSET else audio_url,
            hide_likes=bool(existing["hide_likes"]) if hide_likes is _UNSET else hide_likes,
            hide_shares=bool(existing["hide_shares"]) if hide_shares is _UNSET else hide_shares,
            cooldown_seconds=existing["cooldown_seconds"] if cooldown_seconds is _UNSET else cooldown_seconds,
            failure_policy=existing["failure_policy"] if failure_policy is _UNSET else failure_policy,
        )

        validated_targets = None
        if targets is not _UNSET:
            validated_targets = _validate_campaign_targets_tx(cursor, targets)

        cursor.execute(
            """
            UPDATE campaigns
            SET name = ?, automation_id = ?, audio_url = ?, hide_likes = ?,
                hide_shares = ?, cooldown_seconds = ?, failure_policy = ?, updated_at = ?
            WHERE id = ?;
            """,
            (
                fields["name"],
                fields["automation_id"],
                fields["audio_url"],
                fields["hide_likes"],
                fields["hide_shares"],
                fields["cooldown_seconds"],
                fields["failure_policy"],
                now,
                campaign_id,
            ),
        )

        if validated_targets is not None:
            cursor.execute("DELETE FROM campaign_targets WHERE campaign_id = ?;", (campaign_id,))
            for t in validated_targets:
                cursor.execute(
                    """
                    INSERT INTO campaign_targets (
                        campaign_id, account_id, position, content_id, caption, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?);
                    """,
                    (campaign_id, t["account_id"], t["position"], t["content_id"], t["caption"], now, now),
                )

        conn.commit()
        cursor.execute("SELECT * FROM campaigns WHERE id = ?;", (campaign_id,))
        return _hydrate_campaigns_tx(cursor, [cursor.fetchone()])[0]


def delete_campaign(campaign_id: int, db_path: Optional[str] = None) -> bool:
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM campaigns WHERE id = ?;", (campaign_id,))
        if not cursor.fetchone():
            return False
        if _table_exists(cursor, "campaign_runs"):
            cursor.execute(
                "SELECT id FROM campaign_runs WHERE campaign_id = ? LIMIT 1;",
                (campaign_id,),
            )
            run_row = cursor.fetchone()
            if run_row:
                raise ResourceInUseError(
                    f"Cannot delete campaign #{campaign_id} because persistent run history (Run #{run_row['id']}) exists."
                )
        cursor.execute("DELETE FROM campaigns WHERE id = ?;", (campaign_id,))
        conn.commit()
        return cursor.rowcount > 0


# ---------------------------------------------------------------------------
# Persistent Campaign Runs, Tasks & Task Events
# ---------------------------------------------------------------------------
ACTIVE_RUN_STATES = ("queued", "running")
ACTIVE_TASK_STATES = ("running", "closing_app", "cooldown")

RUN_TRANSITIONS = {
    "queued": {"running", "cancelled", "failed", "interrupted"},
    "running": {"paused", "completed", "failed", "cancelled", "interrupted"},
    "paused": {"running", "cancelled", "completed", "failed"},
    "completed": set(),
    "failed": set(),
    "cancelled": set(),
    "interrupted": set(),
}

TASK_TRANSITIONS = {
    "pending": {"running", "cancelled", "failed"},
    "running": {"closing_app", "completed", "failed", "interrupted", "cancelled"},
    "closing_app": {"cooldown", "completed", "failed", "interrupted", "cancelled"},
    "cooldown": {"completed", "failed", "interrupted", "cancelled"},
    "completed": set(),
    "failed": set(),
    "interrupted": set(),
    "cancelled": set(),
}


def has_active_campaign_run(db_path: Optional[str] = None) -> bool:
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM campaign_runs WHERE status IN ('queued', 'running') LIMIT 1;")
        return cursor.fetchone() is not None


def get_active_campaign_run(db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM campaign_runs WHERE status IN ('queued', 'running') ORDER BY id DESC LIMIT 1;"
        )
        row = cursor.fetchone()
        if not row:
            return None
        return _hydrate_campaign_runs_tx(cursor, [row], include_tasks=True)[0]


def _hydrate_tasks_tx(cursor, task_rows: List[sqlite3.Row]) -> List[Dict[str, Any]]:
    if not task_rows:
        return []
    task_ids = [int(r["id"]) for r in task_rows]
    placeholders = ",".join("?" for _ in task_ids)
    cursor.execute(
        f"""
        SELECT task_id, COUNT(*) AS event_count
        FROM task_events
        WHERE task_id IN ({placeholders})
        GROUP BY task_id;
        """,
        task_ids,
    )
    event_counts = {int(r["task_id"]): int(r["event_count"]) for r in cursor.fetchall()}

    results: List[Dict[str, Any]] = []
    for row in task_rows:
        t = dict(row)
        tid = int(t["id"])
        t["retry_requires_review"] = bool(t["retry_requires_review"])
        t["current_step"] = int(t.get("current_step") or 0)
        t["sequence_number"] = int(t.get("sequence_number") or 0)
        t["event_count"] = event_counts.get(tid, 0)
        t["content_file_url"] = f"/api/content/{t['content_id']}/file"
        t["failure_screenshot_path"] = t.get("failure_screenshot_path")
        t["failure_screenshot_captured_at"] = t.get("failure_screenshot_captured_at")
        t["failure_screenshot_error"] = t.get("failure_screenshot_error")
        t["has_failure_screenshot"] = bool(t.get("failure_screenshot_path"))
        t["failure_screenshot_url"] = f"/api/tasks/{tid}/failure-screenshot" if t["has_failure_screenshot"] else None
        t["recording_status"] = t.get("recording_status") or "unavailable"
        t["recording_path"] = t.get("recording_path")
        t["recording_duration_seconds"] = (
            float(t["recording_duration_seconds"]) if t.get("recording_duration_seconds") is not None else None
        )
        t["recording_started_at"] = t.get("recording_started_at")
        t["recording_completed_at"] = t.get("recording_completed_at")
        t["recording_error"] = t.get("recording_error")
        raw_seg = t.get("recording_segments_json")
        if raw_seg:
            try:
                t["recording_segments"] = json.loads(raw_seg)
            except Exception:
                t["recording_segments"] = []
        else:
            t["recording_segments"] = []
        t["has_recording"] = bool(t.get("recording_status") == "saved" and t.get("recording_path"))
        t["recording_url"] = f"/api/tasks/{tid}/recording" if t["has_recording"] else None
        results.append(t)
    return results


def _hydrate_campaign_runs_tx(
    cursor,
    run_rows: List[sqlite3.Row],
    include_tasks: bool = True,
) -> List[Dict[str, Any]]:
    if not run_rows:
        return []
    run_ids = [int(r["id"]) for r in run_rows]
    placeholders = ",".join("?" for _ in run_ids)

    cursor.execute(
        f"""
        SELECT t.*, c.name AS live_campaign_name
        FROM tasks t
        LEFT JOIN campaigns c ON c.id = t.campaign_id
        WHERE t.run_id IN ({placeholders})
        ORDER BY t.run_id ASC, t.sequence_number ASC, t.id ASC;
        """,
        run_ids,
    )
    raw_tasks = cursor.fetchall()
    hydrated_tasks = _hydrate_tasks_tx(cursor, raw_tasks)

    tasks_by_run: Dict[int, List[Dict[str, Any]]] = {rid: [] for rid in run_ids}
    counts_by_run: Dict[int, Dict[str, int]] = {
        rid: {
            "total": 0,
            "pending": 0,
            "running": 0,
            "closing_app": 0,
            "cooldown": 0,
            "completed": 0,
            "failed": 0,
            "interrupted": 0,
            "cancelled": 0,
        }
        for rid in run_ids
    }
    for task in hydrated_tasks:
        rid = int(task["run_id"])
        tasks_by_run[rid].append(task)
        st = task["status"]
        counts_by_run[rid]["total"] += 1
        if st in counts_by_run[rid]:
            counts_by_run[rid][st] += 1

    runs: List[Dict[str, Any]] = []
    for row in run_rows:
        r = dict(row)
        rid = int(r["id"])
        r["hide_likes"] = bool(r["hide_likes"])
        r["hide_shares"] = bool(r["hide_shares"])
        r["cancel_requested"] = bool(r["cancel_requested"])
        r["cooldown_seconds"] = int(r["cooldown_seconds"])
        r["task_counts"] = counts_by_run[rid]
        if include_tasks:
            r["tasks"] = tasks_by_run[rid]
        runs.append(r)
    return runs


def create_campaign_run(
    campaign_id: int,
    target_rows: Optional[List[Dict[str, Any]]] = None,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Creates a persisted Campaign Run + ordered Pending Tasks atomically.
    Raises ValueError if campaign missing/empty targets; ResourceInUseError if another run is active.
    """
    now = _utcnow()
    with get_connection(db_path) as conn:
        conn.isolation_level = None
        cursor = conn.cursor()
        cursor.execute("BEGIN IMMEDIATE;")
        try:
            cursor.execute("SELECT id FROM campaign_runs WHERE status IN ('queued', 'running') LIMIT 1;")
            active = cursor.fetchone()
            if active:
                raise ResourceInUseError(
                    f"Another campaign run (#{active['id']}) is already active. Wait for it to finish or cancel it first."
                )

            cursor.execute("SELECT * FROM campaigns WHERE id = ?;", (campaign_id,))
            camp = cursor.fetchone()
            if not camp:
                raise ValueError(f"Campaign #{campaign_id} not found.")

            if target_rows is None:
                cursor.execute(
                    """
                    SELECT
                        ct.id AS campaign_target_id,
                        ct.account_id,
                        ct.position,
                        ct.content_id,
                        ct.caption,
                        a.username AS account_username,
                        a.device_serial,
                        a.package_name,
                        ci.original_filename AS content_filename,
                        ci.media_type AS content_media_type
                    FROM campaign_targets ct
                    JOIN accounts a ON a.id = ct.account_id
                    JOIN content_items ci ON ci.id = ct.content_id
                    WHERE ct.campaign_id = ?
                    ORDER BY ct.position ASC, ct.id ASC;
                    """,
                    (campaign_id,),
                )
                resolved_targets = [dict(r) for r in cursor.fetchall()]
            else:
                resolved_targets = target_rows

            if not resolved_targets:
                raise ValueError(f"Campaign #{campaign_id} has no targets to run.")

            cursor.execute(
                """
                INSERT INTO campaign_runs (
                    campaign_id, campaign_name, automation_id, audio_url,
                    hide_likes, hide_shares, cooldown_seconds, failure_policy,
                    status, current_task_id, cancel_requested, error_message,
                    started_at, completed_at, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'queued', NULL, 0, NULL, NULL, NULL, ?, ?);
                """,
                (
                    camp["id"],
                    camp["name"],
                    camp["automation_id"],
                    camp["audio_url"],
                    camp["hide_likes"],
                    camp["hide_shares"],
                    camp["cooldown_seconds"],
                    camp["failure_policy"],
                    now,
                    now,
                ),
            )
            run_id = cursor.lastrowid

            for idx, tgt in enumerate(resolved_targets, start=1):
                cursor.execute(
                    """
                    INSERT INTO tasks (
                        run_id, campaign_id, campaign_target_id, sequence_number,
                        account_id, account_username, device_serial, package_name,
                        content_id, content_filename, caption, status,
                        current_step, current_step_label, retry_requires_review,
                        created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 0, '', 0, ?, ?);
                    """,
                    (
                        run_id,
                        camp["id"],
                        tgt.get("campaign_target_id"),
                        idx,
                        tgt["account_id"],
                        tgt.get("account_username", ""),
                        tgt.get("device_serial"),
                        tgt.get("package_name"),
                        tgt["content_id"],
                        tgt.get("content_filename", ""),
                        tgt.get("caption", ""),
                        now,
                        now,
                    ),
                )

            cursor.execute("COMMIT;")
            cursor.execute("SELECT * FROM campaign_runs WHERE id = ?;", (run_id,))
            return _hydrate_campaign_runs_tx(cursor, [cursor.fetchone()], include_tasks=True)[0]
        except Exception:
            cursor.execute("ROLLBACK;")
            raise


def get_campaign_run(run_id: int, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM campaign_runs WHERE id = ?;", (run_id,))
        row = cursor.fetchone()
        if not row:
            return None
        return _hydrate_campaign_runs_tx(cursor, [row], include_tasks=True)[0]


def list_campaign_runs(
    campaign_id: Optional[int] = None,
    status: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 25,
    offset: int = 0,
    db_path: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], int]:
    clauses: List[str] = []
    params: List[Any] = []
    if campaign_id is not None:
        clauses.append("campaign_id = ?")
        params.append(int(campaign_id))
    if status:
        clauses.append("status = ?")
        params.append(str(status).strip().lower())
    if search:
        s_clean = f"%{str(search).strip()}%"
        clauses.append("(campaign_name LIKE ? OR error_message LIKE ? OR CAST(id AS TEXT) LIKE ?)")
        params.extend([s_clean, s_clean, s_clean])
    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    safe_limit = max(1, min(int(limit), 200))
    safe_offset = max(0, int(offset))

    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(f"SELECT COUNT(*) AS cnt FROM campaign_runs {where_sql};", params)
        total = int(cursor.fetchone()["cnt"])
        cursor.execute(
            f"""
            SELECT * FROM campaign_runs
            {where_sql}
            ORDER BY id DESC
            LIMIT ? OFFSET ?;
            """,
            params + [safe_limit, safe_offset],
        )
        rows = cursor.fetchall()
        return _hydrate_campaign_runs_tx(cursor, rows, include_tasks=True), total


def transition_run_status(
    run_id: int,
    new_status: str,
    current_task_id: Any = _UNSET,
    error_message: Any = _UNSET,
    cancel_requested: Any = _UNSET,
    db_path: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    target_status = str(new_status or "").strip().lower()
    if target_status not in RUN_TRANSITIONS:
        raise ValueError(f"Unknown run status '{target_status}'.")
    now = _utcnow()
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM campaign_runs WHERE id = ?;", (run_id,))
        row = cursor.fetchone()
        if not row:
            return None
        prev_status = row["status"]
        if prev_status != target_status and target_status not in RUN_TRANSITIONS.get(prev_status, set()):
            raise ValueError(f"Illegal campaign run transition: '{prev_status}' -> '{target_status}'.")

        started_at = row["started_at"]
        completed_at = row["completed_at"]
        if target_status == "running":
            if not started_at:
                started_at = now
            completed_at = None
        elif target_status in ("completed", "failed", "cancelled", "interrupted"):
            if not completed_at:
                completed_at = now

        next_task_id = row["current_task_id"] if current_task_id is _UNSET else current_task_id
        next_err = row["error_message"] if error_message is _UNSET else error_message
        next_cancel = row["cancel_requested"] if cancel_requested is _UNSET else (1 if cancel_requested else 0)

        cursor.execute(
            """
            UPDATE campaign_runs
            SET status = ?, current_task_id = ?, error_message = ?, cancel_requested = ?,
                started_at = ?, completed_at = ?, updated_at = ?
            WHERE id = ?;
            """,
            (target_status, next_task_id, next_err, next_cancel, started_at, completed_at, now, run_id),
        )
        conn.commit()
        cursor.execute("SELECT * FROM campaign_runs WHERE id = ?;", (run_id,))
        return _hydrate_campaign_runs_tx(cursor, [cursor.fetchone()], include_tasks=True)[0]


def set_run_cancel_requested(run_id: int, requested: bool = True, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    now = _utcnow()
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM campaign_runs WHERE id = ?;", (run_id,))
        if not cursor.fetchone():
            return None
        cursor.execute(
            "UPDATE campaign_runs SET cancel_requested = ?, updated_at = ? WHERE id = ?;",
            (1 if requested else 0, now, run_id),
        )
        conn.commit()
        cursor.execute("SELECT * FROM campaign_runs WHERE id = ?;", (run_id,))
        return _hydrate_campaign_runs_tx(cursor, [cursor.fetchone()], include_tasks=True)[0]


def is_run_cancel_requested(run_id: int, db_path: Optional[str] = None) -> bool:
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT cancel_requested FROM campaign_runs WHERE id = ?;", (run_id,))
        row = cursor.fetchone()
        return bool(row and row["cancel_requested"])


def cancel_pending_tasks_for_run(
    run_id: int,
    reason: str = "Cancelled before task started.",
    db_path: Optional[str] = None,
) -> int:
    now = _utcnow()
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM tasks WHERE run_id = ? AND status = 'pending';", (run_id,))
        pending_ids = [int(r["id"]) for r in cursor.fetchall()]
        if not pending_ids:
            return 0
        for tid in pending_ids:
            cursor.execute(
                """
                UPDATE tasks
                SET status = 'cancelled',
                    error_message = ?,
                    completed_at = COALESCE(completed_at, ?),
                    updated_at = ?
                WHERE id = ? AND status = 'pending';
                """,
                (reason, now, now, tid),
            )
            cursor.execute(
                """
                INSERT INTO task_events (task_id, timestamp, level, step, message)
                VALUES (?, ?, 'warning', NULL, ?);
                """,
                (tid, now, reason),
            )
        conn.commit()
        return len(pending_ids)


def get_next_pending_task(run_id: int, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT * FROM tasks
            WHERE run_id = ? AND status = 'pending'
            ORDER BY sequence_number ASC, id ASC
            LIMIT 1;
            """,
            (run_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return _hydrate_tasks_tx(cursor, [row])[0]


def transition_task_state(
    task_id: int,
    new_status: str,
    current_step: Any = _UNSET,
    current_step_label: Any = _UNSET,
    reel_url: Any = _UNSET,
    error_message: Any = _UNSET,
    cleanup_status: Any = _UNSET,
    retry_requires_review: Any = _UNSET,
    failure_screenshot_path: Any = _UNSET,
    failure_screenshot_captured_at: Any = _UNSET,
    failure_screenshot_error: Any = _UNSET,
    db_path: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    target_status = str(new_status or "").strip().lower()
    if target_status not in TASK_TRANSITIONS:
        raise ValueError(f"Unknown task status '{target_status}'.")
    now = _utcnow()
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM tasks WHERE id = ?;", (task_id,))
        row = cursor.fetchone()
        if not row:
            return None
        prev_status = row["status"]
        if prev_status != target_status and target_status not in TASK_TRANSITIONS.get(prev_status, set()):
            raise ValueError(f"Illegal task state transition: '{prev_status}' -> '{target_status}'.")

        started_at = row["started_at"]
        completed_at = row["completed_at"]
        if target_status == "running" and not started_at:
            started_at = now
        elif target_status in ("completed", "failed", "interrupted", "cancelled") and not completed_at:
            completed_at = now

        next_step = row["current_step"] if current_step is _UNSET else int(current_step)
        next_label = row["current_step_label"] if current_step_label is _UNSET else str(current_step_label or "")
        next_url = row["reel_url"] if reel_url is _UNSET else reel_url
        next_err = row["error_message"] if error_message is _UNSET else error_message
        next_cleanup = row["cleanup_status"] if cleanup_status is _UNSET else cleanup_status
        next_review = (
            row["retry_requires_review"]
            if retry_requires_review is _UNSET
            else (1 if retry_requires_review else 0)
        )
        next_shot = row["failure_screenshot_path"] if failure_screenshot_path is _UNSET else failure_screenshot_path
        next_shot_at = row["failure_screenshot_captured_at"] if failure_screenshot_captured_at is _UNSET else failure_screenshot_captured_at
        next_shot_err = row["failure_screenshot_error"] if failure_screenshot_error is _UNSET else failure_screenshot_error

        cursor.execute(
            """
            UPDATE tasks
            SET status = ?, current_step = ?, current_step_label = ?, reel_url = ?,
                error_message = ?, cleanup_status = ?, retry_requires_review = ?,
                failure_screenshot_path = ?, failure_screenshot_captured_at = ?, failure_screenshot_error = ?,
                started_at = ?, completed_at = ?, updated_at = ?
            WHERE id = ?;
            """,
            (
                target_status,
                next_step,
                next_label,
                next_url,
                next_err,
                next_cleanup,
                next_review,
                next_shot,
                next_shot_at,
                next_shot_err,
                started_at,
                completed_at,
                now,
                task_id,
            ),
        )
        conn.commit()
        cursor.execute("SELECT * FROM tasks WHERE id = ?;", (task_id,))
        return _hydrate_tasks_tx(cursor, [cursor.fetchone()])[0]


def update_task_recording(
    task_id: int,
    status: str,
    recording_path: Optional[str] = None,
    duration_seconds: Optional[float] = None,
    segments: Optional[List[Dict[str, Any]]] = None,
    started_at: Optional[str] = None,
    completed_at: Optional[str] = None,
    error: Optional[str] = None,
    db_path: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    now = _utcnow()
    clean_status = str(status or "unavailable").strip().lower()
    clean_duration = float(duration_seconds) if duration_seconds is not None else None
    segments_json = json.dumps(segments) if segments is not None else None
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM tasks WHERE id = ?;", (task_id,))
        row = cursor.fetchone()
        if not row:
            return None
        cursor.execute(
            """
            UPDATE tasks
            SET recording_status = ?,
                recording_path = ?,
                recording_duration_seconds = ?,
                recording_segments_json = ?,
                recording_started_at = COALESCE(?, recording_started_at),
                recording_completed_at = COALESCE(?, recording_completed_at),
                recording_error = ?,
                updated_at = ?
            WHERE id = ?;
            """,
            (
                clean_status,
                recording_path,
                clean_duration,
                segments_json,
                started_at,
                completed_at or (now if clean_status in ("saved", "deleted_success", "capture_failed") else None),
                error,
                now,
                task_id,
            ),
        )
        conn.commit()
        cursor.execute("SELECT * FROM tasks WHERE id = ?;", (task_id,))
        return _hydrate_tasks_tx(cursor, [cursor.fetchone()])[0]


def list_expired_recordings(older_than_days: int = 30, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Returns tasks whose recording is 'saved' but completed/created more than older_than_days ago."""
    from datetime import datetime, timezone, timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max(1, int(older_than_days)))).isoformat()
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT * FROM tasks
            WHERE recording_status = 'saved'
              AND (
                  (completed_at IS NOT NULL AND completed_at < ?)
                  OR (completed_at IS NULL AND created_at < ?)
              );
            """,
            (cutoff, cutoff),
        )
        rows = cursor.fetchall()
        return _hydrate_tasks_tx(cursor, rows)


def mark_recording_expired(task_id: int, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    now = _utcnow()
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM tasks WHERE id = ?;", (task_id,))
        if not cursor.fetchone():
            return None
        cursor.execute(
            """
            UPDATE tasks
            SET recording_status = 'expired',
                recording_path = NULL,
                recording_segments_json = NULL,
                updated_at = ?
            WHERE id = ?;
            """,
            (now, task_id),
        )
        conn.commit()
        cursor.execute("SELECT * FROM tasks WHERE id = ?;", (task_id,))
        return _hydrate_tasks_tx(cursor, [cursor.fetchone()])[0]


def update_task_failure_artifact(
    task_id: int,
    screenshot_path: Optional[str] = None,
    captured_at: Optional[str] = None,
    error: Optional[str] = None,
    db_path: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    now = _utcnow()
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM tasks WHERE id = ?;", (task_id,))
        row = cursor.fetchone()
        if not row:
            return None
        cursor.execute(
            """
            UPDATE tasks
            SET failure_screenshot_path = ?,
                failure_screenshot_captured_at = ?,
                failure_screenshot_error = ?,
                updated_at = ?
            WHERE id = ?;
            """,
            (
                screenshot_path,
                captured_at or (now if screenshot_path else None),
                error,
                now,
                task_id,
            ),
        )
        conn.commit()
        cursor.execute("SELECT * FROM tasks WHERE id = ?;", (task_id,))
        return _hydrate_tasks_tx(cursor, [cursor.fetchone()])[0]


def update_task_progress(
    task_id: int,
    current_step: int,
    current_step_label: str = "",
    db_path: Optional[str] = None,
) -> None:
    now = _utcnow()
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE tasks
            SET current_step = ?, current_step_label = ?, updated_at = ?
            WHERE id = ?;
            """,
            (int(current_step), str(current_step_label or ""), now, task_id),
        )
        conn.commit()


def append_task_event(
    task_id: int,
    level: str,
    message: str,
    step: Optional[int] = None,
    stage: Optional[str] = None,
    data: Optional[Dict[str, Any]] = None,
    elapsed_ms: Optional[int] = None,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    now = _utcnow()
    clean_level = str(level or "info").strip().lower()[:32] or "info"
    clean_msg = str(message or "").strip() or "(empty log event)"
    clean_step = int(step) if step is not None else None
    clean_stage = str(stage).strip() if stage else None
    clean_elapsed = int(elapsed_ms) if elapsed_ms is not None else None
    data_dict = dict(data) if data is not None else {}
    if clean_elapsed is not None and "elapsed_ms" not in data_dict:
        data_dict["elapsed_ms"] = clean_elapsed
    data_json = json.dumps(data_dict) if data_dict else None
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO task_events (task_id, timestamp, level, step, stage, data_json, elapsed_ms, message)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (task_id, now, clean_level, clean_step, clean_stage, data_json, clean_elapsed, clean_msg),
        )
        ev_id = cursor.lastrowid
        conn.commit()
        return {
            "id": ev_id,
            "task_id": task_id,
            "timestamp": now,
            "level": clean_level,
            "step": clean_step,
            "stage": clean_stage,
            "elapsed_ms": clean_elapsed,
            "data": data_dict or None,
            "message": clean_msg,
        }


def list_tasks(
    run_id: Optional[int] = None,
    campaign_id: Optional[int] = None,
    account_id: Optional[int] = None,
    status: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    db_path: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], int]:
    clauses: List[str] = []
    params: List[Any] = []
    if run_id is not None:
        clauses.append("t.run_id = ?")
        params.append(int(run_id))
    if campaign_id is not None:
        clauses.append("t.campaign_id = ?")
        params.append(int(campaign_id))
    if account_id is not None:
        clauses.append("t.account_id = ?")
        params.append(int(account_id))
    if status:
        clauses.append("t.status = ?")
        params.append(str(status).strip().lower())
    if search:
        s_clean = f"%{str(search).strip()}%"
        clauses.append("(t.account_username LIKE ? OR t.error_message LIKE ? OR cr.campaign_name LIKE ? OR t.reel_url LIKE ?)")
        params.extend([s_clean, s_clean, s_clean, s_clean])
    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    safe_limit = max(1, min(int(limit), 200))
    safe_offset = max(0, int(offset))

    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"""
            SELECT COUNT(*) AS cnt
            FROM tasks t
            JOIN campaign_runs cr ON cr.id = t.run_id
            {where_sql};
            """,
            params,
        )
        total = int(cursor.fetchone()["cnt"])
        cursor.execute(
            f"""
            SELECT t.*, cr.campaign_name, cr.status AS run_status
            FROM tasks t
            JOIN campaign_runs cr ON cr.id = t.run_id
            {where_sql}
            ORDER BY t.run_id DESC, t.sequence_number ASC, t.id DESC
            LIMIT ? OFFSET ?;
            """,
            params + [safe_limit, safe_offset],
        )
        rows = cursor.fetchall()
        return _hydrate_tasks_tx(cursor, rows), total


def get_task_detail(task_id: int, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT t.*, cr.campaign_name, cr.status AS run_status, cr.failure_policy, cr.cooldown_seconds
            FROM tasks t
            JOIN campaign_runs cr ON cr.id = t.run_id
            WHERE t.id = ?;
            """,
            (task_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        task = _hydrate_tasks_tx(cursor, [row])[0]
        cursor.execute(
            "SELECT id, task_id, timestamp, level, step, stage, data_json, elapsed_ms, message FROM task_events WHERE task_id = ? ORDER BY id ASC;",
            (task_id,),
        )
        events = []
        for r in cursor.fetchall():
            ev = dict(r)
            dj = ev.pop("data_json", None)
            if dj:
                try:
                    ev["data"] = json.loads(dj)
                except Exception:
                    ev["data"] = None
            else:
                ev["data"] = None
            if ev.get("elapsed_ms") is not None:
                ev["elapsed_ms"] = int(ev["elapsed_ms"])
            elif ev.get("data") and ev["data"].get("elapsed_ms") is not None:
                ev["elapsed_ms"] = int(ev["data"]["elapsed_ms"])
            else:
                ev["elapsed_ms"] = None
            events.append(ev)
        task["events"] = events
        return task


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
