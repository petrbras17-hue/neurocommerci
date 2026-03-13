"""Compliance policy engine.

Evaluates runtime events against machine-readable rules and enforces
risk containment (warn/block/quarantine) before risky actions are executed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

from utils.helpers import utcnow
from utils.logger import log
from config import settings
from storage.sqlite_db import async_session
from storage.models import (
    Account,
    PolicyEvent,
    AccountRiskState,
    AccountStageEvent,
)
from sqlalchemy import select


@dataclass
class PolicyDecision:
    rule_id: str
    event: str
    action: str  # allow|warn|block|quarantine
    severity: str  # low|medium|high|critical
    message: str = ""
    cooldown_sec: int = 0


_ACTION_PRIORITY = {
    "allow": 0,
    "warn": 1,
    "block": 2,
    "quarantine": 3,
}

_SEVERITY_WEIGHTS = {
    "low": 1.0,
    "medium": 3.0,
    "high": 6.0,
    "critical": 10.0,
}


_DEFAULT_RULES: list[dict[str, Any]] = [
    {
        "id": "TG-R001",
        "event": "comment_send_attempt",
        "condition": {
            "comparisons": [
                {"key": "account.lifecycle_stage", "op": "ne", "value": "active_commenting"},
            ]
        },
        "action": "block",
        "severity": "high",
        "cooldown_policy": 900,
        "message": "Commenting allowed only for active_commenting lifecycle.",
    },
    {
        "id": "TG-R002",
        "event": "parser_client_candidate",
        "condition": {
            "comparisons": [
                {"key": "account.health_status", "op": "in", "value": ["dead", "restricted", "frozen"]},
            ]
        },
        "action": "block",
        "severity": "high",
        "cooldown_policy": 1800,
        "message": "Restricted/dead account cannot be used for parser.",
    },
    {
        "id": "TG-R003",
        "event": "proxy_assignment",
        "condition": {
            "comparisons": [
                {"key": "strict_proxy", "op": "eq", "value": True},
                {"key": "proxy_assigned", "op": "eq", "value": False},
            ]
        },
        "action": "block",
        "severity": "high",
        "cooldown_policy": 600,
        "message": "Strict proxy mode blocks account without unique proxy.",
    },
    {
        "id": "TG-R004",
        "event": "session_duplicate_detected",
        "condition": {"comparisons": [{"key": "duplicate", "op": "eq", "value": True}]},
        "action": "quarantine",
        "severity": "critical",
        "cooldown_policy": 7200,
        "message": "Duplicate session usage detected.",
    },
    {
        "id": "TG-R005",
        "event": "action_rate_burst",
        "condition": {"comparisons": [{"key": "burst", "op": "eq", "value": True}]},
        "action": "quarantine",
        "severity": "critical",
        "cooldown_policy": 7200,
        "message": "Burst action rate detected.",
    },
    {
        "id": "TG-R006",
        "event": "floodwait_detected",
        "condition": {"comparisons": [{"key": "seconds", "op": "gte", "value": 1}]},
        "action": "warn",
        "severity": "medium",
        "cooldown_policy": 1800,
        "message": "FloodWait must be respected with cooldown.",
    },
    {
        "id": "TG-R007",
        "event": "packaging_account_candidate",
        "condition": {
            "any_comparisons": [
                {"key": "account.health_status", "op": "in", "value": ["dead", "restricted", "frozen"]},
                {"key": "account.lifecycle_stage", "op": "eq", "value": "restricted"},
            ]
        },
        "action": "block",
        "severity": "high",
        "cooldown_policy": 1800,
        "message": "Restricted/dead account cannot be packaged.",
    },
    {
        "id": "TG-R008",
        "event": "frozen_probe_failed",
        "condition": {
            "any_comparisons": [
                {"key": "reason", "op": "in", "value": ["frozen", "restricted"]},
                {"key": "capabilities.reason", "op": "in", "value": ["frozen", "restricted"]},
            ]
        },
        "action": "quarantine",
        "severity": "critical",
        "cooldown_policy": 7200,
        "message": "Capability probe detected frozen/restricted account.",
    },
    {
        "id": "TG-R009",
        "event": "parser_without_parser_phone",
        "condition": {
            "comparisons": [
                {"key": "strict_parser_only", "op": "eq", "value": True},
                {"key": "parser_phone_configured", "op": "eq", "value": False},
            ]
        },
        "action": "block",
        "severity": "high",
        "cooldown_policy": 900,
        "message": "Strict parser mode requires parser-only phone.",
    },
    {
        "id": "TG-R010",
        "event": "missing_pinned_phone",
        "condition": {
            "comparisons": [
                {"key": "required", "op": "eq", "value": True},
                {"key": "pinned_phone", "op": "eq", "value": ""},
            ]
        },
        "action": "block",
        "severity": "high",
        "cooldown_policy": 900,
        "message": "Pinned worker must have PINNED_PHONE configured.",
    },
    {
        "id": "TG-R011",
        "event": "risky_feature_enabled_in_strict",
        "condition": {
            "comparisons": [
                {"key": "strict_mode", "op": "eq", "value": True},
                {"key": "requested_enable", "op": "eq", "value": True},
                {"key": "emergency_flag", "op": "eq", "value": False},
            ]
        },
        "action": "block",
        "severity": "high",
        "cooldown_policy": 900,
        "message": "Risky feature blocked in strict mode without emergency flag.",
    },
    {
        "id": "TG-R012",
        "event": "parser_search_blocked",
        "condition": {
            "comparisons": [
                {"key": "blocked", "op": "eq", "value": True},
            ]
        },
        "action": "warn",
        "severity": "medium",
        "cooldown_policy": 900,
        "message": "Parser search blocked by Telegram.",
    },
]


class PolicyEngine:
    """Rule-based compliance policy evaluation and persistence."""

    def __init__(self):
        self._rules: list[dict[str, Any]] = []
        self._rules_mtime: float = 0.0

    @property
    def mode(self) -> str:
        return (settings.COMPLIANCE_MODE or "off").strip().lower()

    def _load_rules(self) -> list[dict[str, Any]]:
        path: Path = settings.policy_rules_path
        if not path.exists():
            return _DEFAULT_RULES

        try:
            mtime = path.stat().st_mtime
        except Exception:
            return _DEFAULT_RULES

        if self._rules and self._rules_mtime == mtime:
            return self._rules

        try:
            import yaml  # type: ignore

            loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
            rules = loaded.get("rules", loaded) if isinstance(loaded, dict) else loaded
            if not isinstance(rules, list):
                raise ValueError("rules must be a list")
            self._rules = [r for r in rules if isinstance(r, dict)]
            self._rules_mtime = mtime
            log.info(f"PolicyEngine: loaded {len(self._rules)} rules from {path}")
            return self._rules
        except Exception as exc:
            log.warning(f"PolicyEngine: failed to load {path}, fallback rules used: {exc}")
            self._rules = _DEFAULT_RULES
            self._rules_mtime = mtime
            return self._rules

    @staticmethod
    def _ctx_value(context: dict[str, Any], key: str) -> Any:
        cur: Any = context
        for part in key.split("."):
            if isinstance(cur, dict):
                cur = cur.get(part)
            else:
                cur = getattr(cur, part, None)
            if cur is None:
                return None
        return cur

    @staticmethod
    def _compare(actual: Any, op: str, expected: Any) -> bool:
        if op == "eq":
            return actual == expected
        if op == "ne":
            return actual != expected
        if op == "gt":
            return actual is not None and actual > expected
        if op == "gte":
            return actual is not None and actual >= expected
        if op == "lt":
            return actual is not None and actual < expected
        if op == "lte":
            return actual is not None and actual <= expected
        if op == "in":
            try:
                return actual in expected
            except Exception:
                return False
        if op == "not_in":
            try:
                return actual not in expected
            except Exception:
                return False
        if op == "exists":
            return actual is not None
        if op == "truthy":
            return bool(actual)
        return False

    def _condition_match(self, condition: dict[str, Any], context: dict[str, Any]) -> bool:
        required = condition.get("required_context", [])
        for key in required:
            if self._ctx_value(context, str(key)) is None:
                return False

        for cmp_item in condition.get("comparisons", []):
            key = str(cmp_item.get("key", "")).strip()
            op = str(cmp_item.get("op", "eq")).strip()
            expected = cmp_item.get("value")
            actual = self._ctx_value(context, key)
            if not self._compare(actual, op, expected):
                return False

        any_comparisons = condition.get("any_comparisons", [])
        if any_comparisons:
            any_ok = False
            for cmp_item in any_comparisons:
                key = str(cmp_item.get("key", "")).strip()
                op = str(cmp_item.get("op", "eq")).strip()
                expected = cmp_item.get("value")
                actual = self._ctx_value(context, key)
                if self._compare(actual, op, expected):
                    any_ok = True
                    break
            if not any_ok:
                return False

        return True

    def evaluate(self, event: str, context: dict[str, Any]) -> PolicyDecision:
        mode = self.mode
        if mode == "off":
            return PolicyDecision(rule_id="SYS-OFF", event=event, action="allow", severity="low")

        event_name = str(event).strip()
        matched: list[PolicyDecision] = []

        for rule in self._load_rules():
            if str(rule.get("event", "")).strip() != event_name:
                continue
            condition = rule.get("condition", {}) or {}
            if not isinstance(condition, dict):
                continue
            if not self._condition_match(condition, context):
                continue

            decision = str(rule.get("action", "allow")).strip().lower()
            severity = str(rule.get("severity", "low")).strip().lower()
            cooldown = int(rule.get("cooldown_policy", 0) or 0)
            matched.append(
                PolicyDecision(
                    rule_id=str(rule.get("id", "RULE-UNKNOWN")),
                    event=event_name,
                    action=decision,
                    severity=severity,
                    message=str(rule.get("message", "")).strip(),
                    cooldown_sec=max(0, cooldown),
                )
            )

        if not matched:
            return PolicyDecision(rule_id="SYS-DEFAULT", event=event_name, action="allow", severity="low")

        top = max(matched, key=lambda d: _ACTION_PRIORITY.get(d.action, 0))
        if mode == "warn" and top.action in {"block", "quarantine"}:
            return PolicyDecision(
                rule_id=top.rule_id,
                event=top.event,
                action="warn",
                severity=top.severity,
                message=f"warn-only mode: {top.message}",
                cooldown_sec=top.cooldown_sec,
            )
        return top

    async def check(
        self,
        event: str,
        context: dict[str, Any] | None = None,
        *,
        phone: str | None = None,
        worker_id: str | None = None,
    ) -> PolicyDecision:
        ctx = context or {}
        decision = self.evaluate(event, ctx)

        should_persist = decision.action != "allow" or bool(ctx.get("_policy_trace"))
        if should_persist:
            await self._persist_event(decision, ctx, phone=phone, worker_id=worker_id)

        if phone and decision.action in {"warn", "block", "quarantine"}:
            await self._apply_risk(phone, decision)

        return decision

    async def _persist_event(
        self,
        decision: PolicyDecision,
        context: dict[str, Any],
        *,
        phone: str | None,
        worker_id: str | None,
    ):
        payload = json.dumps(context, ensure_ascii=False)[:6000]
        async with async_session() as session:
            account_id = None
            if phone:
                result = await session.execute(select(Account.id).where(Account.phone == phone))
                account_id = result.scalar_one_or_none()

            session.add(
                PolicyEvent(
                    rule_id=decision.rule_id,
                    event_name=decision.event,
                    decision=decision.action,
                    severity=decision.severity,
                    account_id=account_id,
                    phone=phone,
                    worker_id=worker_id,
                    details_json=payload,
                )
            )
            await session.commit()

    @staticmethod
    def _risk_level(score: float) -> str:
        if score >= 20:
            return "critical"
        if score >= 12:
            return "high"
        if score >= 5:
            return "medium"
        return "low"

    async def _apply_risk(self, phone: str, decision: PolicyDecision):
        now = utcnow()
        delta = _SEVERITY_WEIGHTS.get(decision.severity, 1.0)
        if decision.action == "block":
            delta += 1.0
        if decision.action == "quarantine":
            delta += 4.0

        async with async_session() as session:
            result = await session.execute(select(Account).where(Account.phone == phone))
            account = result.scalar_one_or_none()
            if account is None:
                return

            if account.last_violation_at and (now - account.last_violation_at).total_seconds() > 24 * 3600:
                violations_24h = 1
            else:
                violations_24h = (int(account.violation_count_24h) if account.violation_count_24h is not None else 0) + 1

            old_stage = account.lifecycle_stage
            score = (float(account.risk_score) if account.risk_score is not None else 0.0) + float(delta)
            level = self._risk_level(score)

            account.risk_score = score
            account.risk_level = level
            account.violation_count_24h = violations_24h
            account.last_violation_at = now

            if decision.action == "quarantine" or level == "critical":
                cooldown = decision.cooldown_sec or 7200
                account.quarantined_until = now + timedelta(seconds=cooldown)
                account.status = "error"
                account.health_status = "restricted"
                account.lifecycle_stage = "restricted"
                account.restriction_reason = f"{decision.rule_id}:{decision.action}"
            elif decision.action == "block" or level in {"high"}:
                cooldown = decision.cooldown_sec or 900
                account.quarantined_until = now + timedelta(seconds=cooldown)
                if account.status == "active":
                    account.status = "cooldown"
                account.restriction_reason = f"{decision.rule_id}:{decision.action}"

            # Upsert account_risk_state
            rs_result = await session.execute(
                select(AccountRiskState).where(AccountRiskState.account_id == account.id)
            )
            risk_state = rs_result.scalar_one_or_none()
            if risk_state is None:
                risk_state = AccountRiskState(
                    account_id=account.id,
                    phone=account.phone,
                )
                session.add(risk_state)

            risk_state.phone = account.phone
            risk_state.risk_score = account.risk_score
            risk_state.risk_level = account.risk_level
            risk_state.violation_count_24h = account.violation_count_24h
            risk_state.last_violation_at = account.last_violation_at
            risk_state.quarantined_until = account.quarantined_until
            risk_state.updated_at = now

            if old_stage != account.lifecycle_stage:
                session.add(
                    AccountStageEvent(
                        account_id=account.id,
                        phone=account.phone,
                        from_stage=old_stage,
                        to_stage=account.lifecycle_stage,
                        actor="policy_engine",
                        reason=f"{decision.rule_id}:{decision.action}",
                    )
                )

            await session.commit()


policy_engine = PolicyEngine()
