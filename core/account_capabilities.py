"""Account capability probes and persistence helpers.

Compliance-first checks should run before risky operations (packaging/parser/commenting).
"""

from __future__ import annotations

import json
from typing import Any

from telethon import functions

from sqlalchemy import select, update

from storage.models import Account
from storage.sqlite_db import async_session
from utils.helpers import utcnow


_FROZEN_MARKERS = (
    "not available for frozen accounts",
    "frozen account",
    "frozen",
    "you are limited",
)


_RESTRICTED_MARKERS = (
    "user_deactivated",
    "auth key unregistered",
    "account was deleted",
    "restricted",
)


def is_frozen_error(exc: Exception | str) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in _FROZEN_MARKERS)



def is_restricted_error(exc: Exception | str) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in _RESTRICTED_MARKERS)



def _classify_error(exc: Exception | str) -> str:
    if is_frozen_error(exc):
        return "frozen"
    if is_restricted_error(exc):
        return "restricted"
    return "probe_error"


async def probe_account_capabilities(client: Any, *, run_search_probe: bool = True) -> dict[str, Any]:
    """Probe account runtime capabilities with low-impact checks.

    Returned keys:
      can_search, can_profile_edit, can_create_channel, reason, raw_error, checked_at
    """
    result: dict[str, Any] = {
        "can_search": True,
        "can_profile_edit": True,
        "can_create_channel": True,
        "reason": "ok",
        "raw_error": "",
        "checked_at": utcnow().isoformat(),
    }

    try:
        me = await client.get_me()
        if me is None:
            result.update(
                {
                    "can_search": False,
                    "can_profile_edit": False,
                    "can_create_channel": False,
                    "reason": "unauthorized",
                    "raw_error": "get_me returned None",
                }
            )
            return result
    except Exception as exc:  # pragma: no cover - network/runtime error path
        result.update(
            {
                "can_search": False,
                "can_profile_edit": False,
                "can_create_channel": False,
                "reason": _classify_error(exc),
                "raw_error": str(exc)[:500],
            }
        )
        return result

    if not run_search_probe:
        return result

    try:
        # Read-only probe that reliably fails on frozen accounts.
        await client(functions.contacts.SearchRequest(q="news", limit=1))
    except Exception as exc:
        reason = _classify_error(exc)
        restricted = reason in {"frozen", "restricted"}
        result.update(
            {
                "can_search": False,
                "can_profile_edit": not restricted,
                "can_create_channel": not restricted,
                "reason": reason,
                "raw_error": str(exc)[:500],
            }
        )

    return result


async def persist_probe_result(
    phone: str,
    probe: dict[str, Any],
    *,
    mark_restricted_on_failure: bool = False,
    restriction_reason: str | None = None,
) -> None:
    """Persist last probe snapshot and optionally restrict account."""
    checked_at = utcnow()

    payload = {
        "can_search": bool(probe.get("can_search", False)),
        "can_profile_edit": bool(probe.get("can_profile_edit", False)),
        "can_create_channel": bool(probe.get("can_create_channel", False)),
        "reason": str(probe.get("reason", "unknown")),
        "raw_error": str(probe.get("raw_error", ""))[:500],
        "checked_at": str(probe.get("checked_at") or checked_at.isoformat()),
    }

    reason = restriction_reason or payload["reason"]
    values: dict[str, Any] = {
        "last_probe_at": checked_at,
        "capabilities_json": json.dumps(payload, ensure_ascii=False),
    }

    if reason and reason != "ok":
        values["restriction_reason"] = reason

    if mark_restricted_on_failure and reason in {"frozen", "restricted"}:
        values.update(
            {
                "status": "error",
                "health_status": reason,
                "lifecycle_stage": "restricted",
                "quarantined_until": None,
                "restriction_reason": reason,
            }
        )

    async with async_session() as session:
        await session.execute(
            update(Account)
            .where(Account.phone == phone)
            .values(**values)
        )
        await session.commit()


async def load_capabilities(phone: str) -> dict[str, Any]:
    """Load last persisted capability snapshot for account."""
    async with async_session() as session:
        result = await session.execute(
            select(Account.capabilities_json).where(Account.phone == phone)
        )
        raw = result.scalar_one_or_none()

    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}
