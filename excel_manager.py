"""
excel_manager.py - Excel Metadata Import/Export Engine for Instoper.
Handles openpyxl workbook generation and parsing with formula-injection neutralization,
row-level validation, and duplicate conflict detection.
Strictly excludes passwords, TOTP seeds, and secret keys.
"""

import io
from typing import List, Dict, Any, Tuple, Optional
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

import db

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


def export_accounts_to_excel(accounts: Optional[List[Dict[str, Any]]] = None) -> bytes:
    """
    Exports a list of account metadata to an .xlsx binary stream.
    Strictly excludes secrets and neutralizes formula-like characters.
    """
    if accounts is None:
        accounts = db.get_accounts()

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
            raw_val = acc.get(field_key)
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
            "last_login_at": None
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
            "last_login_at": None
        }
    ]
    return export_accounts_to_excel(sample_data)


def parse_and_validate_excel(file_bytes: bytes, db_path: Optional[str] = None) -> Dict[str, Any]:
    """
    Parses an uploaded Excel workbook, performs row-level validation,
    checks for duplicates, and classifies rows into create/update operations.
    """
    try:
        wb = openpyxl.load_workbook(filename=io.BytesIO(file_bytes), data_only=True)
        ws = wb.active
    except Exception as e:
        return {"success": False, "error": f"Failed to open Excel workbook: {e}"}

    header_row = [cell.value for cell in ws[1]]
    if not header_row or not any(header_row):
        return {"success": False, "error": "Empty spreadsheet or missing header row."}

    # Map column headers to field keys
    header_map = {}
    for idx, cell_val in enumerate(header_row):
        if not cell_val:
            continue
        clean_name = str(cell_val).strip()
        for col_name, field_key, _ in EXCEL_COLUMNS:
            if clean_name.lower() == col_name.lower():
                header_map[field_key] = idx
                break

    if "username" not in header_map:
        return {"success": False, "error": "Required column 'Username' not found in spreadsheet."}

    existing_accounts = db.get_accounts(db_path=db_path)
    existing_by_id = {acc["id"]: acc for acc in existing_accounts}
    existing_by_user = {acc["username"].lower(): acc for acc in existing_accounts}

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


def execute_import(to_create: List[Dict[str, Any]], to_update: List[Dict[str, Any]], db_path: Optional[str] = None) -> Dict[str, int]:
    """Commits validated import rows to the database."""
    created_count = 0
    updated_count = 0

    for rec in to_create:
        try:
            db.create_account(
                username=rec["username"],
                email=rec["email"],
                device_serial=rec["device_serial"],
                package_name=rec["package_name"],
                proxy_endpoint=rec["proxy_endpoint"],
                proxy_label=rec["proxy_label"],
                notes=rec["notes"],
                status=rec["status"],
                db_path=db_path,
            )
            created_count += 1
        except Exception:
            pass

    for rec in to_update:
        try:
            db.update_account(
                account_id=rec["id"],
                username=rec["username"],
                email=rec["email"],
                device_serial=rec["device_serial"],
                package_name=rec["package_name"],
                proxy_endpoint=rec["proxy_endpoint"],
                proxy_label=rec["proxy_label"],
                notes=rec["notes"],
                status=rec["status"],
                db_path=db_path,
            )
            updated_count += 1
        except Exception:
            pass

    return {"created": created_count, "updated": updated_count}
