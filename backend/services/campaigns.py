"""
backend/services/campaigns.py - Campaign business logic and preflight checks.
"""
from typing import Optional, Dict, Any, List
import os
from backend.storage import db
from backend.config import BUILTIN_AUTOMATION_ID
from automation.android import package_scanner


def run_campaign_preflight(
    campaign: Dict[str, Any],
    target_subset: Optional[List[Dict[str, Any]]] = None,
    include_lock_check: bool = True,
    is_locked_fn=None,
    resolve_filepath_fn=None,
    db_module=None,
    scanner_module=None,
) -> Dict[str, Any]:
    """Validates audio URL, workflow lock, and every target's device/package/video readiness."""
    _db = db_module or db
    _scanner = scanner_module or package_scanner

    blockers = []
    target_checks = []
    targets = target_subset if target_subset is not None else (campaign.get("targets") or [])

    if campaign.get("automation_id") != BUILTIN_AUTOMATION_ID:
        blockers.append({
            "scope": "campaign",
            "code": "unsupported_automation",
            "message": f"Unsupported automation '{campaign.get('automation_id')}'.",
        })

    try:
        _db.validate_instagram_audio_url(campaign.get("audio_url", ""), require_nonempty=True)
    except ValueError as e:
        blockers.append({"scope": "campaign", "code": "invalid_audio_url", "message": str(e)})

    lock_msg = is_locked_fn() if (include_lock_check and is_locked_fn) else None
    if lock_msg:
        blockers.append({"scope": "campaign", "code": "workflow_locked", "message": lock_msg})

    if not targets:
        blockers.append({"scope": "campaign", "code": "no_targets", "message": "Campaign has no targets to execute."})

    try:
        connected_devices = _scanner.list_connected_devices()
    except Exception:
        connected_devices = []
    online_serials = {d.get("serial") for d in connected_devices if d.get("status") == "device"}

    for idx, tgt in enumerate(targets, start=1):
        acc_id = tgt.get("account_id")
        content_id = tgt.get("content_id")
        acc = _db.get_account_by_id(acc_id) if acc_id else None
        username = (acc or {}).get("username") or tgt.get("account_username") or f"account#{acc_id}"
        tgt_blockers = []

        serial = ((acc or {}).get("device_serial") or "").strip()
        pkg = ((acc or {}).get("package_name") or "").strip()

        if not acc:
            tgt_blockers.append({
                "scope": "target",
                "position": idx,
                "account_id": acc_id,
                "account_username": username,
                "code": "account_not_found",
                "message": f"Target #{idx}: account #{acc_id} no longer exists.",
            })
        elif not serial or not pkg:
            tgt_blockers.append({
                "scope": "target",
                "position": idx,
                "account_id": acc_id,
                "account_username": username,
                "code": "missing_device_binding",
                "message": f"@{username}: missing bound device serial or confirmed clone package.",
            })
        elif serial not in online_serials:
            tgt_blockers.append({
                "scope": "target",
                "position": idx,
                "account_id": acc_id,
                "account_username": username,
                "code": "device_offline",
                "message": f"@{username}: device '{serial}' is offline or not connected.",
            })
        else:
            try:
                _scanner.validate_confirmed_package(device_serial=serial, package_name=pkg)
            except _scanner.OnboardingValidationError as e:
                tgt_blockers.append({
                    "scope": "target",
                    "position": idx,
                    "account_id": acc_id,
                    "account_username": username,
                    "code": e.code,
                    "message": f"@{username}: {e.message}",
                })

        citem = _db.get_content_item(content_id) if content_id else None
        if not citem:
            tgt_blockers.append({
                "scope": "target",
                "position": idx,
                "account_id": acc_id,
                "account_username": username,
                "code": "content_not_found",
                "message": f"@{username}: assigned video content #{content_id} was not found.",
            })
        elif citem.get("media_type") != "video":
            tgt_blockers.append({
                "scope": "target",
                "position": idx,
                "account_id": acc_id,
                "account_username": username,
                "code": "invalid_media_type",
                "message": f"@{username}: content item #{content_id} is not a video.",
            })
        else:
            fpath = resolve_filepath_fn(citem.get("storage_key", "")) if resolve_filepath_fn else None
            if not fpath or not os.path.isfile(fpath):
                tgt_blockers.append({
                    "scope": "target",
                    "position": idx,
                    "account_id": acc_id,
                    "account_username": username,
                    "code": "content_file_missing",
                    "message": f"@{username}: video file '{citem.get('original_filename')}' (#{content_id}) is missing on server.",
                })

        blockers.extend(tgt_blockers)
        target_checks.append({
            "position": idx,
            "account_id": acc_id,
            "account_username": username,
            "device_serial": serial or None,
            "package_name": pkg or None,
            "content_id": content_id,
            "ready": len(tgt_blockers) == 0,
            "blockers": tgt_blockers,
        })

    return {
        "campaign_id": campaign.get("id"),
        "ready": len(blockers) == 0,
        "has_active_lock": bool(lock_msg),
        "blockers": blockers,
        "target_checks": target_checks,
    }
