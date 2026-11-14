"""
backend/services/accounts.py - Account domain logic and validation.
"""
from typing import Optional, Dict, Any, Tuple
from backend.storage import db
from automation.android import package_scanner


def parse_account_taxonomy_payload(data: Dict[str, Any]) -> Tuple[Any, Any]:
    """Extracts and validates optional category_id and tag_ids from account API JSON."""
    category_id = db._UNSET
    if "category_id" in data:
        raw_cat = data.get("category_id")
        if raw_cat in (None, ""):
            category_id = None
        elif isinstance(raw_cat, bool):
            raise ValueError("Invalid category_id.")
        else:
            try:
                category_id = int(raw_cat)
            except (TypeError, ValueError):
                raise ValueError("Invalid category_id.")

    tag_ids = db._UNSET
    if "tag_ids" in data:
        raw_tags = data.get("tag_ids")
        if raw_tags is None:
            tag_ids = []
        elif not isinstance(raw_tags, list):
            raise ValueError("tag_ids must be a list.")
        else:
            parsed = []
            for item in raw_tags:
                if isinstance(item, bool):
                    raise ValueError("Invalid tag ID in tag_ids.")
                try:
                    parsed.append(int(item))
                except (TypeError, ValueError):
                    raise ValueError("Invalid tag ID in tag_ids.")
            tag_ids = parsed

    return category_id, tag_ids


def serialize_account(acc: Dict[str, Any]) -> Dict[str, Any]:
    """Serializes account dictionary for public API responses."""
    if not acc:
        return {}
    clean = dict(acc)
    # Ensure sensitive credentials are not exposed
    clean.pop("password", None)
    clean.pop("totp_seed", None)
    clean.pop("proxy_secret", None)
    return clean


def resolve_automation_account_target(
    account_id_raw: Any,
    explicit_device_serial: Optional[str] = None,
    explicit_package_name: Optional[str] = None,
    db_module=None,
    scanner_module=None,
) -> Dict[str, Any]:
    """Validates and resolves device and clone package bindings for an automation account."""
    _db = db_module or db
    _scanner = scanner_module or package_scanner

    if account_id_raw is None or isinstance(account_id_raw, bool) or str(account_id_raw).strip() == "":
        raise ValueError("An onboarded account must be selected for this automation.")
    try:
        account_id = int(account_id_raw)
    except (TypeError, ValueError):
        raise ValueError("Invalid account_id.")

    acc = _db.get_account_by_id(account_id)
    if not acc:
        raise LookupError(f"Account #{account_id} not found.")

    bound_serial = (acc.get("device_serial") or "").strip()
    bound_pkg = (acc.get("package_name") or "").strip()
    if not bound_serial or not bound_pkg:
        raise ValueError(
            f"Account @{acc['username']} is missing a device or confirmed clone package binding. Update it in Accounts first."
        )

    if explicit_device_serial and str(explicit_device_serial).strip() != bound_serial:
        raise ValueError("Supplied device_serial does not match the selected account's bound device.")
    if explicit_package_name and str(explicit_package_name).strip() != bound_pkg:
        raise ValueError("Supplied package_name does not match the selected account's bound clone package.")

    _scanner.validate_confirmed_package(device_serial=bound_serial, package_name=bound_pkg)
    return {
        "account_id": acc["id"],
        "account_username": acc["username"],
        "device_serial": bound_serial,
        "package_name": bound_pkg,
    }
