"""Helpers for safe account upload bundle handling."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from utils.session_topology import canonical_session_paths, normalize_phone


@dataclass(frozen=True)
class AccountUploadBundle:
    phone: str
    session_path: Path
    metadata_path: Path
    session_present: bool
    metadata_present: bool

    @property
    def ready(self) -> bool:
        return self.session_present and self.metadata_present


def get_account_upload_bundle(base_dir: Path, user_id: int, phone: str) -> AccountUploadBundle:
    session_path, metadata_path = canonical_session_paths(base_dir, user_id, phone)
    return AccountUploadBundle(
        phone=phone,
        session_path=session_path,
        metadata_path=metadata_path,
        session_present=session_path.exists(),
        metadata_present=metadata_path.exists(),
    )


def validate_and_normalize_account_metadata(
    payload: Any,
    *,
    expected_phone: str,
    expected_session_file: str,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("json root must be an object")

    normalized = dict(payload)
    embedded_phone = normalized.get("phone")
    if embedded_phone is not None:
        embedded_phone = normalize_phone(str(embedded_phone))
        if embedded_phone and embedded_phone != expected_phone:
            raise ValueError("phone inside json does not match file name")

    session_file = normalized.get("session_file")
    if session_file is not None:
        session_name = Path(str(session_file)).name
        if session_name and session_name != expected_session_file:
            session_digits = "".join(ch for ch in Path(session_name).stem if ch.isdigit())
            session_phone = normalize_phone(session_digits) if session_digits else ""
            if session_phone and session_phone != expected_phone and not embedded_phone:
                raise ValueError("session_file inside json points to another account")

    if normalized.get("app_id") not in (None, ""):
        try:
            normalized["app_id"] = int(normalized["app_id"])
        except Exception as exc:
            raise ValueError("app_id must be an integer") from exc
    else:
        raise ValueError("app_id is required inside json")

    app_hash = str(normalized.get("app_hash") or "").strip()
    if not app_hash:
        raise ValueError("app_hash is required inside json")
    normalized["app_hash"] = app_hash

    if normalized.get("first_name") is None:
        normalized["first_name"] = "User"

    for field in ("device", "sdk", "app_version"):
        value = str(normalized.get(field) or "").strip()
        if not value:
            raise ValueError(f"{field} is required inside json")
        normalized[field] = value

    normalized["phone"] = expected_phone
    normalized["session_file"] = expected_session_file
    return normalized


def read_account_metadata(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def metadata_api_credentials(path: Path) -> tuple[int | None, str]:
    payload = read_account_metadata(path) or {}
    app_id_raw = payload.get("app_id")
    app_hash = str(payload.get("app_hash") or "").strip()
    try:
        app_id = int(app_id_raw) if app_id_raw not in (None, "") else None
    except Exception:
        app_id = None
    return app_id, app_hash


def metadata_has_required_api_credentials(path: Path) -> bool:
    app_id, app_hash = metadata_api_credentials(path)
    return app_id is not None and bool(app_hash)


def find_api_credential_conflicts(
    base_dir: Path,
    *,
    user_id: int,
    expected_phone: str,
    app_id: int,
    app_hash: str,
) -> list[str]:
    tenant_dir = base_dir / str(int(user_id))
    if not tenant_dir.exists():
        return []
    conflicts: list[str] = []
    normalized_expected = normalize_phone(expected_phone)
    for json_path in tenant_dir.glob("*.json"):
        phone = normalize_phone(json_path.stem)
        if not phone or phone == normalized_expected:
            continue
        other_app_id, other_app_hash = metadata_api_credentials(json_path)
        if other_app_id == int(app_id) and str(other_app_hash or "").strip() == str(app_hash or "").strip():
            conflicts.append(phone)
    return sorted(set(conflicts))


def write_normalized_metadata(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
