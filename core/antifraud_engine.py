"""
Anti-Fraud Engine — risk scoring and cross-account pattern detection.

Evaluates each Telegram action for fraud risk using multiple factors,
makes proceed/delay/skip/alert decisions, and detects cross-account
patterns that could trigger Telegram anti-spam systems.

Usage:
    from core.antifraud_engine import (
        score_action_risk, detect_cross_account_patterns,
        get_risk_summary, resolve_pattern, get_pattern_alerts,
    )
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select, update, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from storage.models import (
    Account,
    AccountHealthScore,
    AntifraudScore,
    FarmEvent,
    PatternDetection,
)
from storage.sqlite_db import async_session, apply_session_rls_context
from utils.helpers import utcnow

try:
    from core.operation_logger import log_operation
except ImportError:
    async def log_operation(*a: Any, **kw: Any) -> None:  # type: ignore[misc]
        pass

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Risk factor weights
# ---------------------------------------------------------------------------

_FACTOR_WEIGHTS: Dict[str, float] = {
    "timing_pattern": 0.25,
    "content_similarity": 0.20,
    "burst_activity": 0.20,
    "account_age": 0.10,
    "health_score": 0.15,
    "time_of_day": 0.10,
}

# Decision thresholds
_THRESHOLD_PROCEED = 0.3
_THRESHOLD_DELAY = 0.6
_THRESHOLD_SKIP = 0.8
# Above 0.8 = alert


def _decide(risk: float) -> str:
    """Map overall risk score to a decision."""
    if risk < _THRESHOLD_PROCEED:
        return "proceed"
    if risk < _THRESHOLD_DELAY:
        return "delay"
    if risk < _THRESHOLD_SKIP:
        return "skip"
    return "alert"


# ---------------------------------------------------------------------------
# Individual risk factor evaluators
# ---------------------------------------------------------------------------


async def _eval_timing_pattern(
    session: AsyncSession,
    workspace_id: int,
    account_id: int,
) -> float:
    """
    Check if this account's actions are too regular (low variance in intervals).

    Looks at the last 20 antifraud scores for the account and checks the
    standard deviation of time intervals between decisions.
    """
    result = await session.execute(
        select(AntifraudScore.decided_at)
        .where(
            and_(
                AntifraudScore.account_id == account_id,
            )
        )
        .order_by(AntifraudScore.id.desc())
        .limit(20)
    )
    timestamps = [row[0] for row in result.fetchall() if row[0] is not None]
    if len(timestamps) < 3:
        return 0.0

    intervals = []
    for i in range(len(timestamps) - 1):
        diff = abs((timestamps[i] - timestamps[i + 1]).total_seconds())
        intervals.append(diff)

    if not intervals:
        return 0.0

    mean = sum(intervals) / len(intervals)
    if mean == 0:
        return 0.8  # All at same time = suspicious

    variance = sum((x - mean) ** 2 for x in intervals) / len(intervals)
    std_dev = variance ** 0.5
    cv = std_dev / mean if mean > 0 else 0

    # Low coefficient of variation = too regular
    if cv < 0.1:
        return 0.9
    if cv < 0.2:
        return 0.6
    if cv < 0.3:
        return 0.3
    return 0.1


async def _eval_burst_activity(
    session: AsyncSession,
    workspace_id: int,
    account_id: int,
) -> float:
    """Check if too many actions in a short time window (last 5 minutes)."""
    cutoff = utcnow() - timedelta(minutes=5)
    result = await session.execute(
        select(func.count(AntifraudScore.id)).where(
            and_(
                AntifraudScore.account_id == account_id,
                AntifraudScore.decided_at >= cutoff,
            )
        )
    )
    count = result.scalar() or 0
    if count >= 10:
        return 0.95
    if count >= 5:
        return 0.7
    if count >= 3:
        return 0.4
    return 0.1


async def _eval_account_age(
    session: AsyncSession,
    workspace_id: int,
    account_id: int,
) -> float:
    """New accounts are riskier."""
    result = await session.execute(
        select(Account.created_at).where(Account.id == account_id)
    )
    row = result.first()
    if not row or not row[0]:
        return 0.5

    created = row[0]
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    age_days = (utcnow() - created).total_seconds() / 86400.0

    if age_days < 1:
        return 0.9
    if age_days < 3:
        return 0.6
    if age_days < 7:
        return 0.3
    if age_days < 30:
        return 0.15
    return 0.05


async def _eval_health_score(
    session: AsyncSession,
    workspace_id: int,
    account_id: int,
) -> float:
    """Low health = higher risk."""
    result = await session.execute(
        select(AccountHealthScore.health_score).where(
            AccountHealthScore.account_id == account_id,
        )
    )
    row = result.first()
    if not row:
        return 0.3  # unknown health = moderate risk

    health = row[0] or 50
    if health >= 80:
        return 0.05
    if health >= 60:
        return 0.2
    if health >= 40:
        return 0.4
    if health >= 20:
        return 0.7
    return 0.9


def _eval_time_of_day() -> float:
    """Actions outside normal hours (8-23 Moscow time = UTC+3) are slightly riskier."""
    now_utc = utcnow()
    moscow_hour = (now_utc.hour + 3) % 24
    if 8 <= moscow_hour <= 23:
        return 0.05
    return 0.4


def _eval_content_similarity(context: Dict[str, Any]) -> float:
    """
    Check if comment content is too similar to recent comments from same workspace.

    Expects context to contain 'recent_comments' (list of str) and 'current_comment' (str).
    """
    recent = context.get("recent_comments", [])
    current = context.get("current_comment", "")
    if not current or not recent:
        return 0.0

    current_lower = current.lower().strip()
    exact_matches = sum(1 for c in recent if c.lower().strip() == current_lower)
    if exact_matches > 0:
        return 0.95

    # Simple word overlap check
    current_words = set(current_lower.split())
    if not current_words:
        return 0.0

    max_overlap = 0.0
    for comment in recent:
        comment_words = set(comment.lower().strip().split())
        if not comment_words:
            continue
        intersection = current_words & comment_words
        overlap = len(intersection) / max(len(current_words), len(comment_words))
        max_overlap = max(max_overlap, overlap)

    if max_overlap > 0.8:
        return 0.8
    if max_overlap > 0.6:
        return 0.5
    if max_overlap > 0.4:
        return 0.2
    return 0.05


# ---------------------------------------------------------------------------
# Main scoring function
# ---------------------------------------------------------------------------


async def score_action_risk(
    workspace_id: int,
    account_id: int,
    action_type: str,
    context: Optional[Dict[str, Any]] = None,
) -> AntifraudScore:
    """
    Score the risk of an action and persist the result.

    Returns the saved AntifraudScore with risk_score, risk_factors, and decision.
    """
    context = context or {}
    factors: Dict[str, float] = {}

    async with async_session() as session:
        async with session.begin():
            await apply_session_rls_context(session, tenant_id=workspace_id)

            factors["timing_pattern"] = await _eval_timing_pattern(
                session, workspace_id, account_id
            )
            factors["burst_activity"] = await _eval_burst_activity(
                session, workspace_id, account_id
            )
            factors["account_age"] = await _eval_account_age(
                session, workspace_id, account_id
            )
            factors["health_score"] = await _eval_health_score(
                session, workspace_id, account_id
            )
            factors["time_of_day"] = _eval_time_of_day()
            factors["content_similarity"] = _eval_content_similarity(context)

            # Weighted average
            total_weight = sum(_FACTOR_WEIGHTS.values())
            risk = sum(
                factors[k] * _FACTOR_WEIGHTS[k]
                for k in _FACTOR_WEIGHTS
            ) / total_weight

            risk = max(0.0, min(1.0, risk))
            decision = _decide(risk)

            score = AntifraudScore(
                workspace_id=workspace_id,
                account_id=account_id,
                action_type=action_type,
                risk_score=round(risk, 4),
                risk_factors={k: round(v, 4) for k, v in factors.items()},
                decision=decision,
            )
            session.add(score)
            await session.flush()
            score_id = score.id

    # Log the decision
    try:
        await log_operation(
            workspace_id=workspace_id,
            account_id=account_id,
            module="antifraud",
            action=f"score_{action_type}",
            status=decision,
            detail=f"risk={risk:.4f} factors={factors}",
        )
    except Exception:
        pass

    # Re-fetch with server defaults
    async with async_session() as session:
        async with session.begin():
            await apply_session_rls_context(session, tenant_id=workspace_id)
            result = await session.execute(
                select(AntifraudScore).where(AntifraudScore.id == score_id)
            )
            return result.scalar_one()


# ---------------------------------------------------------------------------
# Cross-account pattern detection
# ---------------------------------------------------------------------------


async def detect_cross_account_patterns(
    workspace_id: int,
) -> List[PatternDetection]:
    """
    Detect cross-account patterns that suggest coordinated automation.

    Returns a list of newly created PatternDetection objects.
    """
    detections: List[PatternDetection] = []

    async with async_session() as session:
        async with session.begin():
            await apply_session_rls_context(session, tenant_id=workspace_id)

            # 1. Identical timing: 2+ accounts acting within same 5-second window repeatedly
            cutoff = utcnow() - timedelta(hours=1)
            result = await session.execute(
                select(AntifraudScore.account_id, AntifraudScore.decided_at)
                .where(AntifraudScore.decided_at >= cutoff)
                .order_by(AntifraudScore.decided_at)
            )
            rows = result.fetchall()
            if len(rows) >= 2:
                timing_groups: Dict[int, List[int]] = {}
                for i, (acct_id, ts) in enumerate(rows):
                    if ts is None:
                        continue
                    bucket = int(ts.timestamp()) // 5
                    timing_groups.setdefault(bucket, []).append(acct_id)

                for bucket, accts in timing_groups.items():
                    unique = list(set(accts))
                    if len(unique) >= 2:
                        det = PatternDetection(
                            workspace_id=workspace_id,
                            pattern_type="identical_timing",
                            accounts_involved=unique[:10],
                            severity="medium" if len(unique) < 4 else "high",
                            detail=f"{len(unique)} accounts acted within same 5-sec window",
                        )
                        session.add(det)
                        detections.append(det)

            # 2. Burst activity: sudden spike from multiple accounts
            burst_cutoff = utcnow() - timedelta(minutes=5)
            result = await session.execute(
                select(
                    AntifraudScore.account_id,
                    func.count(AntifraudScore.id).label("cnt"),
                )
                .where(AntifraudScore.decided_at >= burst_cutoff)
                .group_by(AntifraudScore.account_id)
            )
            burst_accounts = [
                row[0] for row in result.fetchall() if row[1] >= 5
            ]
            if len(burst_accounts) >= 2:
                det = PatternDetection(
                    workspace_id=workspace_id,
                    pattern_type="burst_activity",
                    accounts_involved=burst_accounts[:10],
                    severity="high",
                    detail=f"{len(burst_accounts)} accounts in burst mode simultaneously",
                )
                session.add(det)
                detections.append(det)

            await session.flush()

    # Log detections
    for det in detections:
        try:
            await log_operation(
                workspace_id=workspace_id,
                account_id=None,
                module="antifraud",
                action=f"pattern_{det.pattern_type}",
                status="detected",
                detail=det.detail,
            )
        except Exception:
            pass

    return detections


# ---------------------------------------------------------------------------
# Summary and management
# ---------------------------------------------------------------------------


async def get_risk_summary(workspace_id: int) -> Dict[str, Any]:
    """Aggregate risk scores, recent alerts, and pattern counts."""
    async with async_session() as session:
        async with session.begin():
            await apply_session_rls_context(session, tenant_id=workspace_id)

            # Total scores today
            today_start = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
            result = await session.execute(
                select(func.count(AntifraudScore.id)).where(
                    AntifraudScore.decided_at >= today_start,
                )
            )
            total_today = result.scalar() or 0

            # Average risk today
            result = await session.execute(
                select(func.avg(AntifraudScore.risk_score)).where(
                    AntifraudScore.decided_at >= today_start,
                )
            )
            avg_risk = result.scalar()
            avg_risk = round(float(avg_risk), 4) if avg_risk else 0.0

            # Decision distribution today
            result = await session.execute(
                select(
                    AntifraudScore.decision,
                    func.count(AntifraudScore.id),
                )
                .where(AntifraudScore.decided_at >= today_start)
                .group_by(AntifraudScore.decision)
            )
            decisions = {row[0]: row[1] for row in result.fetchall()}

            # Unresolved pattern count
            result = await session.execute(
                select(func.count(PatternDetection.id)).where(
                    PatternDetection.is_resolved == False,  # noqa: E712
                )
            )
            unresolved_alerts = result.scalar() or 0

            # Pattern type breakdown
            result = await session.execute(
                select(
                    PatternDetection.pattern_type,
                    func.count(PatternDetection.id),
                )
                .where(PatternDetection.is_resolved == False)  # noqa: E712
                .group_by(PatternDetection.pattern_type)
            )
            pattern_types = {row[0]: row[1] for row in result.fetchall()}

    return {
        "total_scores_today": total_today,
        "avg_risk_today": avg_risk,
        "decisions_today": decisions,
        "unresolved_alerts": unresolved_alerts,
        "pattern_types": pattern_types,
    }


async def resolve_pattern(workspace_id: int, pattern_id: int) -> bool:
    """Mark a pattern detection as resolved. Returns True if updated."""
    async with async_session() as session:
        async with session.begin():
            await apply_session_rls_context(session, tenant_id=workspace_id)
            result = await session.execute(
                update(PatternDetection)
                .where(PatternDetection.id == pattern_id)
                .values(is_resolved=True)
            )
            return result.rowcount > 0


async def get_pattern_alerts(
    workspace_id: int,
    unresolved_only: bool = True,
    limit: int = 50,
) -> List[PatternDetection]:
    """Get pattern alerts, optionally filtered to unresolved only."""
    async with async_session() as session:
        async with session.begin():
            await apply_session_rls_context(session, tenant_id=workspace_id)
            q = select(PatternDetection)
            if unresolved_only:
                q = q.where(PatternDetection.is_resolved == False)  # noqa: E712
            q = q.order_by(PatternDetection.id.desc()).limit(limit)
            result = await session.execute(q)
            return list(result.scalars().all())
