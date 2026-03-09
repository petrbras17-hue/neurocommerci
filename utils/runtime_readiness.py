"""Runtime readiness and blocker helpers for accounts/channels."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, TYPE_CHECKING, Any

from utils.account_uploads import metadata_has_required_api_credentials
from utils.session_topology import canonical_metadata_exists, canonical_session_exists

if TYPE_CHECKING:
    from storage.models import Account, Channel


@dataclass(frozen=True)
class ReadinessReport:
    blockers: list[str]

    @property
    def ready(self) -> bool:
        return not self.blockers

    @property
    def primary(self) -> str:
        return self.blockers[0] if self.blockers else "ready"


def account_blockers(account: Any, *, sessions_dir: Path, strict_proxy: bool) -> ReadinessReport:
    blockers: list[str] = []

    if account.user_id is None:
        blockers.append("no_owner")
    elif not canonical_session_exists(sessions_dir, account.user_id, account.phone):
        blockers.append("session_missing")
    elif not canonical_metadata_exists(sessions_dir, account.user_id, account.phone):
        blockers.append("metadata_missing")
    else:
        metadata_path = sessions_dir / str(int(account.user_id)) / f"{account.phone.lstrip('+')}.json"
        if not metadata_has_required_api_credentials(metadata_path):
            blockers.append("metadata_api_credentials_missing")

    if strict_proxy and account.proxy_id is None:
        blockers.append("no_proxy_binding")

    if account.status in {"banned", "error"}:
        blockers.append(f"status_{account.status}")
    elif account.status != "active":
        blockers.append(f"status_{account.status}")

    if account.health_status in {"dead", "restricted", "frozen", "expired"}:
        blockers.append(f"health_{account.health_status}")

    lifecycle = account.lifecycle_stage or "unknown"
    if lifecycle not in {"active_commenting", "execution_ready"}:
        blockers.append(f"stage_{lifecycle}")

    return ReadinessReport(blockers=blockers)


def summarize_account_blockers(
    accounts: Iterable[Any],
    *,
    sessions_dir: Path,
    strict_proxy: bool,
) -> dict[str, int]:
    summary: dict[str, int] = {}
    for account in accounts:
        report = account_blockers(account, sessions_dir=sessions_dir, strict_proxy=strict_proxy)
        for blocker in report.blockers:
            summary[blocker] = summary.get(blocker, 0) + 1
    return dict(sorted(summary.items(), key=lambda item: (-item[1], item[0])))


def channel_publish_ready(channel: Any) -> bool:
    if bool(channel.is_blacklisted):
        return False
    if (channel.review_state or "discovered") != "approved":
        return False
    return (channel.publish_mode or "research_only") == "auto_allowed"
