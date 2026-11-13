"""
excel_manager.py - Excel Metadata Import/Export Engine for Instoper.
Handles openpyxl workbook generation and parsing with formula-injection neutralization,
row-level validation, and duplicate conflict detection.
Strictly excludes passwords, TOTP seeds, and secret keys.
"""

import io
import csv
import secrets
import time
import threading
from typing import List, Dict, Any, Tuple, Optional
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

import db
import vault

# ---------------------------------------------------------------------------
# Server-Side Ephemeral Import Sessions (Prevents Secrets from Entering Browser)
# ---------------------------------------------------------------------------
_import_sessions: Dict[str, Dict[str, Any]] = {}
_import_sessions_lock = threading.Lock()
IMPORT_SESSION_TTL_SECONDS = 900  # 15 minutes


def cleanup_expired_import_sessions():
    """Removes sessions older than IMPORT_SESSION_TTL_SECONDS."""
    now = time.time()
    with _import_sessions_lock:
        expired = [
            t for t, d in _import_sessions.items()
            if now - d.get("created_at", 0) > IMPORT_SESSION_TTL_SECONDS
        ]
        for t in expired:
            _import_sessions.pop(t, None)


def store_import_session(raw_rows: List[Dict[str, Any]], headers: List[str]) -> str:
    """Stores parsed rows securely in memory and returns a short-lived random token."""
    cleanup_expired_import_sessions()
    token = secrets.token_hex(16)
    with _import_sessions_lock:
        _import_sessions[token] = {
            "created_at": time.time(),
            "headers": headers,
            "raw_rows": raw_rows,
        }
    return token


def get_import_session(token: str) -> Optional[Dict[str, Any]]:
    """Retrieves session without deleting it."""
    cleanup_expired_import_sessions()
    with _import_sessions_lock:
        sess = _import_sessions.get(token)
        if sess and (time.time() - sess.get("created_at", 0) <= IMPORT_SESSION_TTL_SECONDS):
            return sess
    return None


def pop_import_session(token: str) -> Optional[Dict[str, Any]]:
    """Retrieves session and deletes it immediately (one-time token consumption)."""
    cleanup_expired_import_sessions()
    with _import_sessions_lock:
        sess = _import_sessions.pop(token, None)
        if sess and (time.time() - sess.get("created_at", 0) <= IMPORT_SESSION_TTL_SECONDS):
            return sess
    return None


EXCEL_COLUMNS = [
    ("Account ID", "id", 12),
    ("Username", "username", 22),
    ("Email", "email", 26),
    ("Device Serial", "device_serial", 22),
    ("Package Name", "package_name", 28),
    ("Proxy Endpoint", "proxy_endpoint", 30),
    ("Proxy Label", "proxy_label", 20),
    ("Notes", "notes", 30),
    ("Status", "status", 14),
    ("Login Status", "login_status", 16),
    ("Last Login", "last_login_at", 20),
    ("Category", "category_name", 20),
    ("Tags", "tags_serialized", 28),
]

FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def neutralize_formula(value: Any) -> Any:
    """Prepend single quote to prevent formula injection in spreadsheet software."""
    if isinstance(value, str):
        if value.startswith(FORMULA_PREFIXES):
            return "'" + value
    return value


def sanitize_input(value: Any) -> Optional[str]:
    """Cleans imported cell values, stripping prepended escape quotes and whitespace."""
    if value is None:
        return None
    val_str = str(value).strip()
    if not val_str:
        return None
    if val_str.startswith("'"):
        val_str = val_str[1:]
    return val_str.strip()


def parse_tags_cell(
    raw_tags: Optional[str],
    raw_legacy_tag: Optional[str] = None,
) -> Tuple[List[str], Optional[str]]:
    """
    Parses a semicolon-separated `tags` cell (or single-value legacy `tag` cell fallback).
    Trims and deduplicates case-insensitively while keeping first spelling.
    """
    if raw_tags is not None and str(raw_tags).strip():
        seen = set()
        result = []
        for piece in str(raw_tags).split(";"):
            cleaned = piece.strip()
            if not cleaned:
                continue
            norm = cleaned.casefold()
            if norm not in seen:
                seen.add(norm)
                result.append(cleaned)
        return result, None

    if raw_legacy_tag is not None and str(raw_legacy_tag).strip():
        single = str(raw_legacy_tag).strip()
        if ";" in single:
            return [], f"Tag name '{single}' cannot contain semicolon delimiter ';'."
        return [single], None

    return [], None


def _build_taxonomy_maps(db_path: Optional[str] = None) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    cats = db.list_categories("accounts", db_path=db_path)
    tags = db.list_tags("accounts", db_path=db_path)
    return (
        {c["normalized_name"]: c for c in cats},
        {t["normalized_name"]: t for t in tags},
    )


def _resolve_row_taxonomy(
    category_name: Optional[str],
    raw_tags: Optional[str],
    raw_legacy_tag: Optional[str],
    cat_map: Dict[str, Dict[str, Any]],
    tag_map: Dict[str, Dict[str, Any]],
    create_missing: bool = False,
    dry_run: bool = False,
    db_path: Optional[str] = None,
) -> Tuple[Optional[int], List[int], Optional[str]]:
    """
    Resolves category name and tags cell against 'accounts'-scoped taxonomy.
    Never silently creates or discards unknown values when create_missing is False.
    """
    tag_names, parse_err = parse_tags_cell(raw_tags, raw_legacy_tag)
    if parse_err:
        return None, [], parse_err

    category_id: Optional[int] = None
    if category_name is not None and str(category_name).strip():
        cat_display = str(category_name).strip()
        cat_norm = cat_display.casefold()
        cat_entry = cat_map.get(cat_norm)
        if cat_entry:
            category_id = cat_entry["id"]
        elif create_missing:
            if not dry_run:
                new_id = db.create_category("accounts", cat_display, db_path=db_path)
                cat_map[cat_norm] = {"id": new_id, "name": cat_display, "normalized_name": cat_norm, "scope": "accounts"}
                category_id = new_id
        else:
            return None, [], f"Unknown category '{cat_display}' (create it first or enable create-missing)."

    tag_ids: List[int] = []
    for tname in tag_names:
        if ";" in tname:
            return None, [], f"Tag name '{tname}' cannot contain semicolon delimiter ';'."
        t_norm = tname.casefold()
        tag_entry = tag_map.get(t_norm)
        if tag_entry:
            tag_ids.append(tag_entry["id"])
        elif create_missing:
            if not dry_run:
                new_tid = db.create_tag("accounts", tname, db_path=db_path)
                tag_map[t_norm] = {"id": new_tid, "name": tname, "normalized_name": t_norm, "scope": "accounts"}
                tag_ids.append(new_tid)
        else:
            return None, [], f"Unknown tag '{tname}' (create it first or enable create-missing)."

    return category_id, tag_ids, None


def export_accounts_to_excel(accounts: Optional[List[Dict[str, Any]]] = None, db_path: Optional[str] = None) -> bytes:
    """
    Exports a list of account metadata to an .xlsx binary stream.
    Strictly excludes secrets and neutralizes formula-like characters.
    """
    if accounts is None:
        accounts = db.get_accounts(db_path=db_path)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Instoper Accounts"

    # Header styling
    header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="1F2937", end_color="1F2937", fill_type="solid")
    center_align = Alignment(horizontal="center", vertical="center")
    left_align = Alignment(horizontal="left", vertical="center")
    thin_border = Border(
        left=Side(style="thin", color="E5E7EB"),
        right=Side(style="thin", color="E5E7EB"),
        top=Side(style="thin", color="E5E7EB"),
        bottom=Side(style="thin", color="E5E7EB")
    )

    # Write Header
    for col_idx, (col_name, _, width) in enumerate(EXCEL_COLUMNS, start=1):
        cell = ws.cell(row=1, column=col_idx, value=col_name)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = center_align
        ws.column_dimensions[get_column_letter(col_idx)].width = width

    # Write Data Rows
    for row_idx, acc in enumerate(accounts, start=2):
        for col_idx, (_, field_key, _) in enumerate(EXCEL_COLUMNS, start=1):
            if field_key == "category_name":
                cat = acc.get("category")
                raw_val = cat.get("name") if isinstance(cat, dict) else acc.get("category_name")
            elif field_key == "tags_serialized":
                tags_list = acc.get("tags")
                if isinstance(tags_list, list) and tags_list:
                    raw_val = "; ".join(t["name"] for t in tags_list if t.get("name"))
                else:
                    raw_val = acc.get("tags_serialized") or acc.get("tag")
            else:
                raw_val = acc.get(field_key)

            if field_key == "proxy_endpoint" and raw_val:
                raw_val = vault.redact_proxy_url(raw_val)
            safe_val = neutralize_formula(raw_val)
            cell = ws.cell(row=row_idx, column=col_idx, value=safe_val)
            cell.font = Font(name="Calibri", size=10)
            cell.border = thin_border
            cell.alignment = left_align

    # Save to memory stream
    output = io.BytesIO()
    wb.save(output)
    return output.getvalue()


def generate_sample_template() -> bytes:
    """Generates an empty sample Excel workbook template with column guide."""
    sample_data = [
        {
            "id": None,
            "username": "brand_marketing_01",
            "email": "brand01@example.com",
            "device_serial": "0624531123033570",
            "package_name": "com.instagram.android",
            "proxy_endpoint": "http://192.168.1.100:8080",
            "proxy_label": "Office Proxy #1",
            "notes": "Primary business reel publisher",
            "status": "active",
            "login_status": "idle",
            "last_login_at": None,
            "category_name": "Main Brand",
            "tags_serialized": "Reels; Daily",
        },
        {
            "id": None,
            "username": "lifestyle_reel_creator",
            "email": "lifestyle@example.com",
            "device_serial": "0624531123033570",
            "package_name": "com.instagram.android.clone1",
            "proxy_endpoint": None,
            "proxy_label": None,
            "notes": "Secondary clone test account",
            "status": "active",
            "login_status": "idle",
            "last_login_at": None,
            "category_name": None,
            "tags_serialized": "Creator",
        }
    ]
    return export_accounts_to_excel(sample_data)


def parse_and_validate_excel(
    file_bytes: bytes,
    db_path: Optional[str] = None,
    create_missing_taxonomy: bool = False,
) -> Dict[str, Any]:
    """
    Parses an uploaded Excel workbook, performs row-level validation,
    checks for duplicates and scoped taxonomy resolution, and classifies rows.
    """
    try:
        wb = openpyxl.load_workbook(filename=io.BytesIO(file_bytes), data_only=True)
        ws = wb.active
    except Exception as e:
        return {"success": False, "error": f"Failed to open Excel workbook: {e}"}

    header_row = [cell.value for cell in ws[1]]
    if not header_row or not any(header_row):
        return {"success": False, "error": "Empty spreadsheet or missing header row."}

    # Map column headers to field keys (plus legacy singular 'tag')
    header_map = {}
    for idx, cell_val in enumerate(header_row):
        if not cell_val:
            continue
        clean_name = str(cell_val).strip().lower()
        if clean_name == "tag":
            header_map["legacy_tag"] = idx
            continue
        for col_name, field_key, _ in EXCEL_COLUMNS:
            if clean_name == col_name.lower() or clean_name == field_key.lower():
                header_map[field_key] = idx
                break

    if "username" not in header_map:
        return {"success": False, "error": "Required column 'Username' not found in spreadsheet."}

    existing_accounts = db.get_accounts(db_path=db_path)
    existing_by_id = {acc["id"]: acc for acc in existing_accounts}
    existing_by_user = {acc["username"].lower(): acc for acc in existing_accounts}
    cat_map, tag_map = _build_taxonomy_maps(db_path=db_path)

    # Track seen in current sheet to prevent in-sheet duplicate mapping
    seen_users = set()
    seen_device_packages = set()

    for acc in existing_accounts:
        dev = acc["device_serial"]
        pkg = acc["package_name"]
        if dev and pkg:
            seen_device_packages.add((dev.lower(), pkg.lower(), acc["id"]))

    rows_to_create = []
    rows_to_update = []
    errors = []

    has_cat_col = "category_name" in header_map
    has_tags_col = ("tags_serialized" in header_map) or ("legacy_tag" in header_map)

    for row_idx in range(2, ws.max_row + 1):
        row_cells = ws[row_idx]
        # Skip completely empty rows
        if all(cell.value is None or str(cell.value).strip() == "" for cell in row_cells):
            continue

        def get_val(key):
            if key in header_map and header_map[key] < len(row_cells):
                return sanitize_input(row_cells[header_map[key]].value)
            return None

        raw_id = get_val("id")
        username = get_val("username")
        email = get_val("email")
        device_serial = get_val("device_serial")
        package_name = get_val("package_name") or "com.instagram.android"
        proxy_endpoint = get_val("proxy_endpoint")
        proxy_label = get_val("proxy_label")
        notes = get_val("notes")
        status = (get_val("status") or "active").lower()
        raw_category = get_val("category_name") if has_cat_col else None
        raw_tags = get_val("tags_serialized")
        raw_legacy_tag = get_val("legacy_tag")

        if status not in ("active", "disabled"):
            status = "active"

        # Validation Rule 1: Username required
        if not username:
            errors.append(f"Row {row_idx}: 'Username' cannot be blank.")
            continue

        user_lower = username.lower()
        # Validation Rule 2: In-sheet duplicate username
        if user_lower in seen_users:
            errors.append(f"Row {row_idx}: Duplicate username '{username}' found in spreadsheet.")
            continue
        seen_users.add(user_lower)

        # Validation Rule 3: Scoped taxonomy resolution (dry run; no DB writes on preview)
        cat_id, tag_ids, tax_err = _resolve_row_taxonomy(
            raw_category,
            raw_tags,
            raw_legacy_tag,
            cat_map,
            tag_map,
            create_missing=create_missing_taxonomy,
            dry_run=True,
            db_path=db_path,
        )
        if tax_err:
            errors.append(f"Row {row_idx}: {tax_err}")
            continue

        parsed_tag_names, _ = parse_tags_cell(raw_tags, raw_legacy_tag)

        # Parse Account ID if provided or resolve by existing username
        account_id = None
        if raw_id:
            try:
                account_id = int(raw_id)
            except ValueError:
                errors.append(f"Row {row_idx}: Invalid Account ID '{raw_id}', must be an integer.")
                continue
        elif user_lower in existing_by_user:
            account_id = existing_by_user[user_lower]["id"]

        # Check device/package collision
        has_collision = False
        if device_serial and package_name:
            dev_lower = device_serial.lower()
            pkg_lower = package_name.lower()
            for s_dev, s_pkg, s_id in seen_device_packages:
                if s_dev == dev_lower and s_pkg == pkg_lower and s_id != account_id:
                    errors.append(f"Row {row_idx}: Device '{device_serial}' & package '{package_name}' already mapped to another account.")
                    has_collision = True
                    break
            if not has_collision:
                seen_device_packages.add((dev_lower, pkg_lower, account_id))

        if has_collision:
            continue

        record = {
            "row_index": row_idx,
            "id": account_id,
            "username": username,
            "email": email,
            "device_serial": device_serial,
            "package_name": package_name,
            "proxy_endpoint": proxy_endpoint,
            "proxy_label": proxy_label,
            "notes": notes,
            "status": status,
            "has_category_col": has_cat_col,
            "has_tags_col": has_tags_col,
            "category_name": raw_category,
            "tag_names": parsed_tag_names,
            "category_id": cat_id,
            "tag_ids": tag_ids,
        }

        # Classify as Update or Create
        if account_id and account_id in existing_by_id:
            rows_to_update.append(record)
        elif user_lower in existing_by_user:
            record["id"] = existing_by_user[user_lower]["id"]
            rows_to_update.append(record)
        else:
            rows_to_create.append(record)

    return {
        "success": True,
        "total_rows": len(rows_to_create) + len(rows_to_update),
        "to_create": rows_to_create,
        "to_update": rows_to_update,
        "errors": errors,
    }


def execute_import(
    to_create: List[Dict[str, Any]],
    to_update: List[Dict[str, Any]],
    db_path: Optional[str] = None,
    create_missing_taxonomy: bool = False,
) -> Dict[str, Any]:
    """Commits validated import rows and taxonomy assignments safely."""
    created_count = 0
    updated_count = 0
    errors: List[str] = []
    cat_map, tag_map = _build_taxonomy_maps(db_path=db_path)

    def resolve_rec_taxonomy(rec: Dict[str, Any]):
        has_cat = rec.get("has_category_col", "category_name" in rec or "category_id" in rec)
        has_tags = rec.get("has_tags_col", "tag_names" in rec or "tag_ids" in rec)
        cat_arg = db._UNSET
        tags_arg = db._UNSET
        if has_cat or has_tags:
            raw_cat = rec.get("category_name")
            tag_names = rec.get("tag_names") or []
            raw_tags = "; ".join(tag_names) if tag_names else None
            c_id, t_ids, err = _resolve_row_taxonomy(
                raw_cat if has_cat else None,
                raw_tags if has_tags else None,
                None,
                cat_map,
                tag_map,
                create_missing=create_missing_taxonomy,
                dry_run=False,
                db_path=db_path,
            )
            if err:
                return None, None, err
            if has_cat:
                cat_arg = c_id
            if has_tags:
                tags_arg = t_ids
        return cat_arg, tags_arg, None

    for rec in to_create:
        try:
            cat_arg, tags_arg, tax_err = resolve_rec_taxonomy(rec)
            if tax_err:
                errors.append(f"Row {rec.get('row_index', '?')} ({rec['username']}): {tax_err}")
                continue
            db.create_account(
                username=rec["username"],
                email=rec.get("email"),
                device_serial=rec.get("device_serial"),
                package_name=rec.get("package_name"),
                proxy_endpoint=rec.get("proxy_endpoint"),
                proxy_label=rec.get("proxy_label"),
                notes=rec.get("notes"),
                status=rec.get("status", "active"),
                category_id=cat_arg,
                tag_ids=tags_arg,
                db_path=db_path,
            )
            created_count += 1
        except Exception as e:
            errors.append(f"Row {rec.get('row_index', '?')} ({rec['username']}): {e}")

    for rec in to_update:
        try:
            cat_arg, tags_arg, tax_err = resolve_rec_taxonomy(rec)
            if tax_err:
                errors.append(f"Row {rec.get('row_index', '?')} ({rec['username']}): {tax_err}")
                continue
            db.update_account(
                account_id=rec["id"],
                username=rec["username"],
                email=rec.get("email"),
                device_serial=rec.get("device_serial"),
                package_name=rec.get("package_name"),
                proxy_endpoint=rec.get("proxy_endpoint"),
                proxy_label=rec.get("proxy_label"),
                notes=rec.get("notes"),
                status=rec.get("status"),
                category_id=cat_arg,
                tag_ids=tags_arg,
                db_path=db_path,
            )
            updated_count += 1
        except Exception as e:
            errors.append(f"Row {rec.get('row_index', '?')} ({rec['username']}): {e}")

    return {"created": created_count, "updated": updated_count, "errors": errors}


# ---------------------------------------------------------------------------
# Dynamic Column Mapping & Universal File Parsing (XLSX, CSV, TSV)
# ---------------------------------------------------------------------------
TOOL_DATA_POINTS = [
    {
        "key": "username",
        "label": "Username",
        "required": True,
        "storage": "Local Database (Metadata)",
        "description": "Instagram username / handle",
        "synonyms": ["user", "username", "login", "handle", "account", "ig_user", "profile"]
    },
    {
        "key": "password",
        "label": "Password",
        "required": False,
        "storage": "Protected Vault (Windows DPAPI Encrypted)",
        "description": "Account password (Stored securely, never plaintext)",
        "synonyms": ["password", "pass", "pwd", "credential", "auth_pass"]
    },
    {
        "key": "totp_seed",
        "label": "2FA Key / TOTP Seed",
        "required": False,
        "storage": "Protected Vault (Windows DPAPI Encrypted)",
        "description": "Authenticator secret key for 6-digit TOTP codes",
        "synonyms": ["2fa", "2fa_key", "totp", "secret", "2fa_secret", "authenticator", "seed", "otp_key"]
    },
    {
        "key": "category",
        "label": "Category",
        "required": False,
        "storage": "Local Database (Scoped Taxonomy)",
        "description": "Primary account category (one per account)",
        "synonyms": ["category", "group", "folder", "account_category"]
    },
    {
        "key": "tags",
        "label": "Tags (Semicolon-separated)",
        "required": False,
        "storage": "Local Database (Scoped Taxonomy)",
        "description": "Multiple account tags separated by semicolons (e.g. Reels; Daily)",
        "synonyms": ["tags", "labels", "tag_list"]
    },
    {
        "key": "tag",
        "label": "Legacy Single Tag",
        "required": False,
        "storage": "Local Database (Scoped Taxonomy)",
        "description": "Single tag fallback when 'Tags' is unmapped",
        "synonyms": ["tag", "label", "niche", "campaign"]
    },
    {
        "key": "notes",
        "label": "Notes",
        "required": False,
        "storage": "Local Database (Metadata)",
        "description": "Custom comments or warm-up notes",
        "synonyms": ["note", "notes", "comment", "comments", "description", "info", "memo"]
    },
    {
        "key": "proxy_endpoint",
        "label": "Proxy Endpoint",
        "required": False,
        "storage": "Local Database (Metadata)",
        "description": "Proxy IP:Port or http://user:pass@host:port",
        "synonyms": ["proxy", "proxy_endpoint", "proxy_ip", "ip", "endpoint", "server"]
    },
    {
        "key": "device_serial",
        "label": "Device Serial",
        "required": False,
        "storage": "Local Database (Metadata)",
        "description": "Bound Android phone serial (or use current device)",
        "synonyms": ["device", "device_serial", "serial", "phone", "udid"]
    },
    {
        "key": "package_name",
        "label": "Package Name",
        "required": False,
        "storage": "Local Database (Metadata)",
        "description": "Instagram or Clone package name (default com.instagram.android)",
        "synonyms": ["package", "package_name", "pkg", "clone", "app", "target_package"]
    },
]


def suggest_auto_mapping(headers: List[str]) -> Dict[str, Optional[str]]:
    """Suggests best column matching for each tool data point based on header synonyms."""
    mapping = {}
    normalized_headers = [(h, h.strip().lower().replace("_", "").replace(" ", "").replace("-", "")) for h in headers]

    for dp in TOOL_DATA_POINTS:
        key = dp["key"]
        matched_header = None
        # Pass 1: exact normalized match
        for syn in dp["synonyms"]:
            norm_syn = syn.replace("_", "").replace(" ", "").replace("-", "")
            for orig_h, norm_h in normalized_headers:
                if norm_syn == norm_h:
                    matched_header = orig_h
                    break
            if matched_header:
                break
        # Pass 2: substring match if no exact match
        if not matched_header:
            for syn in dp["synonyms"]:
                norm_syn = syn.replace("_", "").replace(" ", "").replace("-", "")
                for orig_h, norm_h in normalized_headers:
                    if norm_syn in norm_h:
                        matched_header = orig_h
                        break
                if matched_header:
                    break
        mapping[key] = matched_header

    return mapping


def parse_file_headers_and_rows(file_bytes: bytes, filename: str) -> Dict[str, Any]:
    """
    Parses an uploaded .xlsx or .csv/.tsv file and returns:
    - Detected column headers
    - First 5 sample rows for preview with secrets redacted
    - Ephemeral import_token referencing server-side cached rows (no secrets returned)
    - Suggested auto-mapping based on column names
    """
    fn_lower = filename.lower()
    headers = []
    raw_rows = []

    if fn_lower.endswith(".xls"):
        return {
            "success": False,
            "error": "Legacy .xls format is not supported. Please save your spreadsheet as modern Excel (.xlsx) or CSV (.csv)."
        }

    if fn_lower.endswith(".xlsx"):
        try:
            wb = openpyxl.load_workbook(filename=io.BytesIO(file_bytes), data_only=True)
            ws = wb.active
            if ws.max_column == 0 or ws.max_row == 0:
                return {"success": False, "error": "Empty spreadsheet or missing header row."}

            max_col = ws.max_column
            max_row = ws.max_row

            # Determine which columns have actual non-empty data in rows 2..max_row
            cols_with_data = set()
            for r in range(2, max_row + 1):
                for c in range(1, max_col + 1):
                    val = ws.cell(row=r, column=c).value
                    if val is not None and str(val).strip() != "":
                        cols_with_data.add(c)

            # Read headers while preserving column positions and rejecting invalid headers
            headers = []
            col_indices = []
            seen_headers = {}

            for c in range(1, max_col + 1):
                h_val = ws.cell(row=1, column=c).value
                h_str = str(h_val).strip() if h_val is not None else ""

                if not h_str:
                    if c in cols_with_data:
                        return {
                            "success": False,
                            "error": f"Column {c} contains data but is missing a header name. All data columns must have valid headers."
                        }
                    # Empty column with no header and no data -> omit without shifting column positions
                    continue

                h_lower = h_str.lower()
                if h_lower in seen_headers:
                    return {
                        "success": False,
                        "error": f"Duplicate column header '{h_str}' found at column {c} (previously seen at column {seen_headers[h_lower]})."
                    }
                seen_headers[h_lower] = c
                headers.append(h_str)
                col_indices.append(c)

            if not headers:
                return {"success": False, "error": "No header columns found in file."}

            for row_idx in range(2, max_row + 1):
                row_vals = [ws.cell(row=row_idx, column=c).value for c in col_indices]
                if all(v is None or str(v).strip() == "" for v in row_vals):
                    continue
                row_dict = {}
                for col_name, val in zip(headers, row_vals):
                    row_dict[col_name] = sanitize_input(val)
                raw_rows.append(row_dict)
        except Exception as e:
            return {"success": False, "error": f"Failed to parse Excel file: {e}"}

    elif fn_lower.endswith((".csv", ".tsv", ".txt")):
        try:
            content_str = None
            for enc in ("utf-8", "utf-8-sig", "latin-1", "cp1252"):
                try:
                    content_str = file_bytes.decode(enc)
                    break
                except UnicodeDecodeError:
                    continue

            if not content_str:
                return {"success": False, "error": "Unable to decode text file. Ensure UTF-8 encoding."}

            first_line = content_str.splitlines()[0] if content_str.splitlines() else ""
            delim = ","
            if "\t" in first_line:
                delim = "\t"
            elif ";" in first_line and first_line.count(";") > first_line.count(","):
                delim = ";"
            elif "|" in first_line and first_line.count("|") > first_line.count(","):
                delim = "|"

            csv_reader = csv.reader(io.StringIO(content_str), delimiter=delim)
            all_lines = list(csv_reader)
            if not all_lines:
                return {"success": False, "error": "Empty spreadsheet or missing header row."}

            raw_header_cells = all_lines[0]
            # Identify columns with data in rows 1..N
            cols_with_data = set()
            for row in all_lines[1:]:
                for c_idx, cell_val in enumerate(row):
                    if cell_val is not None and str(cell_val).strip() != "":
                        cols_with_data.add(c_idx)

            headers = []
            col_indices = []
            seen_headers = {}

            for c_idx, h_val in enumerate(raw_header_cells):
                h_str = str(h_val).strip() if h_val is not None else ""
                if not h_str:
                    if c_idx in cols_with_data:
                        return {
                            "success": False,
                            "error": f"Column {c_idx + 1} contains data but is missing a header name. All data columns must have valid headers."
                        }
                    continue

                h_lower = h_str.lower()
                if h_lower in seen_headers:
                    return {
                        "success": False,
                        "error": f"Duplicate column header '{h_str}' found at column {c_idx + 1} (previously seen at column {seen_headers[h_lower]})."
                    }
                seen_headers[h_lower] = c_idx + 1
                headers.append(h_str)
                col_indices.append(c_idx)

            if not headers:
                return {"success": False, "error": "No header columns found in file."}

            for row in all_lines[1:]:
                row_vals = [row[c_idx] if c_idx < len(row) else None for c_idx in col_indices]
                if all(v is None or str(v).strip() == "" for v in row_vals):
                    continue
                clean_row = {}
                for col_name, val in zip(headers, row_vals):
                    clean_row[col_name] = sanitize_input(val)
                raw_rows.append(clean_row)
        except Exception as e:
            return {"success": False, "error": f"Failed to parse CSV file: {e}"}
    else:
        return {"success": False, "error": "Unsupported file format. Please upload .xlsx, .csv, or .tsv"}

    if not headers:
        return {"success": False, "error": "No header columns found in file."}

    auto_mapping = suggest_auto_mapping(headers)

    # Cache parsed rows in server-side ephemeral session and generate secure token
    import_token = store_import_session(raw_rows, headers)

    # Redact sensitive preview data (never send raw passwords, TOTP seeds, or proxy credentials to browser)
    sensitive_cols = set()
    for k in ("password", "totp_seed"):
        if auto_mapping.get(k):
            sensitive_cols.add(auto_mapping[k])
    for h in headers:
        h_low = h.lower()
        if any(s in h_low for s in ("password", "pass", "pwd", "totp", "secret", "2fa", "seed")):
            sensitive_cols.add(h)

    proxy_cols = set()
    if auto_mapping.get("proxy_endpoint"):
        proxy_cols.add(auto_mapping["proxy_endpoint"])
    for h in headers:
        if "proxy" in h.lower():
            proxy_cols.add(h)

    redacted_samples = []
    for r in raw_rows[:5]:
        red_r = {}
        for k, v in r.items():
            if k in sensitive_cols and v:
                red_r[k] = "••••••••"
            elif k in proxy_cols and v:
                red_r[k] = vault.redact_proxy_url(v)
            else:
                red_r[k] = v
        redacted_samples.append(red_r)

    return {
        "success": True,
        "import_token": import_token,
        "filename": filename,
        "headers": headers,
        "total_rows": len(raw_rows),
        "sample_rows": redacted_samples,
        "auto_mapping": auto_mapping,
        "tool_data_points": TOOL_DATA_POINTS,
    }


def import_accounts_with_mapping(
    raw_rows: List[Dict[str, Any]],
    column_mapping: Dict[str, Optional[str]],
    default_device: Optional[str] = None,
    default_package: Optional[str] = None,
    create_missing_taxonomy: bool = False,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Imports accounts according to the user's custom column mapping.
    Stores metadata & scoped taxonomy in database and credentials in DPAPI vault.
    """
    user_col = column_mapping.get("username")
    if not user_col:
        return {"success": False, "error": "Username column must be mapped."}

    pwd_col = column_mapping.get("password")
    totp_col = column_mapping.get("totp_seed")
    cat_col = column_mapping.get("category")
    tags_col = column_mapping.get("tags")
    tag_col = column_mapping.get("tag")
    notes_col = column_mapping.get("notes")
    proxy_col = column_mapping.get("proxy_endpoint")
    dev_col = column_mapping.get("device_serial")
    pkg_col = column_mapping.get("package_name")

    cat_map, tag_map = _build_taxonomy_maps(db_path=db_path)

    created_count = 0
    updated_count = 0
    errors = []

    for idx, row in enumerate(raw_rows, start=1):
        username = row.get(user_col)
        if not username:
            errors.append(f"Row {idx}: Username is empty, skipped.")
            continue

        username = str(username).strip()
        password = str(row.get(pwd_col)).strip() if pwd_col and row.get(pwd_col) else None
        totp_seed = str(row.get(totp_col)).strip() if totp_col and row.get(totp_col) else None
        raw_cat = str(row.get(cat_col)).strip() if cat_col and row.get(cat_col) else None
        raw_tags = str(row.get(tags_col)).strip() if tags_col and row.get(tags_col) else None
        raw_legacy_tag = str(row.get(tag_col)).strip() if tag_col and row.get(tag_col) else None
        notes = str(row.get(notes_col)).strip() if notes_col and row.get(notes_col) else None
        proxy = str(row.get(proxy_col)).strip() if proxy_col and row.get(proxy_col) else None

        device_serial = (str(row.get(dev_col)).strip() if dev_col and row.get(dev_col) else None) or default_device
        package_name = (str(row.get(pkg_col)).strip() if pkg_col and row.get(pkg_col) else None) or default_package or "com.instagram.android"

        cat_arg: Any = db._UNSET
        tags_arg: Any = db._UNSET
        if cat_col or tags_col or tag_col:
            c_id, t_ids, tax_err = _resolve_row_taxonomy(
                raw_cat,
                raw_tags,
                raw_legacy_tag,
                cat_map,
                tag_map,
                create_missing=create_missing_taxonomy,
                dry_run=False,
                db_path=db_path,
            )
            if tax_err:
                errors.append(f"Row {idx} ({username}): {tax_err}")
                continue
            if cat_col:
                cat_arg = c_id
            if tags_col or tag_col:
                tags_arg = t_ids

        # Check if account already exists
        existing = db.get_account_by_username(username, db_path=db_path)
        if existing:
            try:
                db.update_account(
                    account_id=existing["id"],
                    username=username,
                    device_serial=device_serial,
                    package_name=package_name,
                    proxy_endpoint=proxy,
                    notes=notes,
                    category_id=cat_arg,
                    tag_ids=tags_arg,
                    db_path=db_path,
                )
                if password or totp_seed:
                    db.save_account_secrets(
                        account_id=existing["id"],
                        password=password,
                        totp_seed=totp_seed,
                        db_path=db_path,
                    )
                updated_count += 1
            except Exception as e:
                errors.append(f"Row {idx} ({username}): Update failed - {e}")
        else:
            try:
                db.create_account(
                    username=username,
                    device_serial=device_serial,
                    package_name=package_name,
                    proxy_endpoint=proxy,
                    notes=notes,
                    password=password,
                    totp_seed=totp_seed,
                    category_id=cat_arg,
                    tag_ids=tags_arg,
                    db_path=db_path,
                )
                created_count += 1
            except Exception as e:
                errors.append(f"Row {idx} ({username}): Create failed - {e}")

    return {
        "success": True,
        "created": created_count,
        "updated": updated_count,
        "total_processed": len(raw_rows),
        "errors": errors,
    }
