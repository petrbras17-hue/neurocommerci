"""
Менеджер аккаунтов — пул, ротация, статусы.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Optional, Iterable

from sqlalchemy import select, update, or_
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from core.proxy_manager import ProxyManager
from core.session_manager import SessionManager
from core.rate_limiter import RateLimiter
from storage.models import Account, AccountStageEvent
from storage.sqlite_db import async_session
from utils.helpers import utcnow
from utils.logger import log
from utils.proxy_bindings import get_bound_proxy_config


class AccountManager:
    """Управление пулом Telegram аккаунтов."""

    BLOCKED_HEALTH_STATUSES = {"dead", "restricted", "frozen", "expired"}

    def __init__(
        self,
        session_manager: SessionManager,
        proxy_manager: ProxyManager,
        rate_limiter: RateLimiter,
    ):
        self.session_mgr = session_manager
        self.proxy_mgr = proxy_manager
        self.rate_limiter = rate_limiter
        self._rotation_index = 0
        self._lock = asyncio.Lock()  # Защита от race condition при выборе аккаунта

    @staticmethod
    def resolve_post_warmup_stage(days_active: int) -> str:
        """Lifecycle policy after connection/warmup."""
        if int(days_active or 0) < 15:
            return "warming_up"
        if settings.MANUAL_GATE_REQUIRED:
            return "gate_review"
        return "execution_ready" if settings.HUMAN_GATED_PACKAGING else "active_commenting"

    @staticmethod
    def _normalize_phone(raw_phone: str | None) -> str:
        digits = "".join(ch for ch in str(raw_phone or "") if ch.isdigit())
        return f"+{digits}" if digits else ""

    async def load_accounts(self, include_banned: bool = False, user_id: int = None) -> list[Account]:
        """Загрузить аккаунты из БД. user_id фильтрует по пользователю."""
        async with async_session() as session:
            query = select(Account)
            if user_id is not None:
                query = query.where(Account.user_id == user_id)
            if not include_banned:
                query = query.where(Account.status != "banned")
            result = await session.execute(query.limit(10000))
            accounts = result.scalars().all()
            log.info(f"Загружено аккаунтов из БД: {len(accounts)}")
            return list(accounts)

    async def add_account(self, phone: str, session_file: str, user_id: int = None) -> Account:
        """Добавить новый аккаунт в БД."""
        async with async_session() as session:
            account = Account(
                phone=phone,
                session_file=session_file,
                user_id=user_id,
                status="active",
                created_at=utcnow(),
            )
            session.add(account)
            await session.commit()
            await session.refresh(account)
            log.info(f"Добавлен аккаунт: {phone}")
            return account

    async def connect_all(self, user_id: int = None) -> dict[str, str]:
        """Подключить все активные аккаунты. Возвращает {phone: status}."""
        accounts = await self.load_accounts(user_id=user_id)
        results = {}

        for i, account in enumerate(accounts):
            if account.status == "banned":
                results[account.phone] = "banned"
                continue

            # Антибан задержка 5с между подключениями аккаунтов
            if i > 0:
                log.info("Антибан задержка 5с...")
                await asyncio.sleep(5)

            proxy = await get_bound_proxy_config(account.phone)
            if proxy is None:
                proxy = self.proxy_mgr.assign_to_account(account.phone)
            results[account.phone] = await self._connect_one(account.phone, proxy, user_id=account.user_id)

        return results

    async def _connect_one(self, phone: str, proxy, user_id: int | None = None) -> str:
        """Подключить один аккаунт. Возвращает статус строкой."""
        from telethon.errors import FloodWaitError

        try:
            client = await self.session_mgr.connect_client(phone, proxy, user_id=user_id)
            if client:
                return "connected"
            await self._update_status(phone, "error")
            return "failed"
        except FloodWaitError as e:
            await self.handle_error(phone, "flood_wait", str(e.seconds))
            return f"flood_wait_{e.seconds}s"
        except Exception as e:
            await self._update_status(phone, "error")
            log.error(f"Ошибка подключения {phone}: {e}")
            return "failed"

    async def _connect_one_with_proxy(self, acc: Account) -> tuple[str, str]:
        """Assign proxy and connect one account. Returns (phone, status)."""
        proxy = await get_bound_proxy_config(acc.phone)
        if proxy is None:
            proxy = self.proxy_mgr.assign_to_account(acc.phone)
        status = await self._connect_one(acc.phone, proxy, user_id=acc.user_id)
        return acc.phone, status

    async def connect_batch(
        self,
        report_every: int = 15,
        progress_callback=None,
        user_id: int = None,
        batch_size: int = 5,
    ) -> dict[str, str]:
        """
        Подключить аккаунты батчами по batch_size с 5с задержкой между батчами (антибан).
        batch_size — сколько аккаунтов подключать параллельно в одном батче.
        report_every — как часто вызывать progress_callback (каждые N аккаунтов).
        progress_callback(done, total, results) — для отчётности в боте.
        """
        accounts = await self.load_accounts(user_id=user_id)
        active = [a for a in accounts if a.status != "banned"]
        results: dict[str, str] = {}

        # Разбить на батчи по batch_size
        batches = [active[i:i + batch_size] for i in range(0, len(active), batch_size)]

        done = 0
        for batch_idx, batch in enumerate(batches):
            # Антибан: 5с задержка между батчами
            if batch_idx > 0:
                log.info("Антибан задержка 5с между батчами...")
                await asyncio.sleep(5)

            gather_results = await asyncio.gather(
                *[self._connect_one_with_proxy(acc) for acc in batch],
                return_exceptions=True,
            )

            for acc, item in zip(batch, gather_results):
                if isinstance(item, Exception):
                    log.error(f"Ошибка подключения {acc.phone}: {item}")
                    results[acc.phone] = "error"
                else:
                    phone, status = item
                    results[phone] = status

            done += len(batch)
            log.info(f"Batch connect: {done}/{len(active)} аккаунтов обработано")

            # Отчёт каждые report_every аккаунтов или в конце
            if progress_callback and (done % report_every == 0 or done == len(active)):
                try:
                    await progress_callback(done, len(active), results)
                except Exception:
                    pass

        return results

    async def get_next_available(
        self,
        user_id: int = None,
        phones_subset: Optional[Iterable[str]] = None,
    ) -> Optional[Account]:
        """Получить следующий доступный аккаунт для комментирования (round-robin).

        Используем asyncio.Lock для защиты от race condition:
        без него два concurrent вызова могут выбрать один и тот же аккаунт.
        """
        async with self._lock:
            subset: Optional[set[str]] = None
            if phones_subset is not None:
                subset = set(phones_subset)
                if not subset:
                    log.warning("Передан пустой subset аккаунтов")
                    return None

            async with async_session() as session:
                query = select(Account).where(
                    Account.status == "active",
                    Account.lifecycle_stage.in_(("active_commenting", "execution_ready")),
                    or_(
                        Account.health_status.is_(None),
                        Account.health_status.notin_(list(self.BLOCKED_HEALTH_STATUSES)),
                    ),
                    or_(Account.quarantined_until.is_(None), Account.quarantined_until <= utcnow()),
                )
                if settings.STRICT_PROXY_PER_ACCOUNT:
                    query = query.where(Account.proxy_id.is_not(None))
                parser_phone = self._normalize_phone(settings.PARSER_ONLY_PHONE)
                if settings.STRICT_PARSER_ONLY and parser_phone:
                    query = query.where(Account.phone != parser_phone)
                if user_id is not None:
                    query = query.where(Account.user_id == user_id)
                if subset is not None:
                    query = query.where(Account.phone.in_(subset))
                result = await session.execute(query)
                active = list(result.scalars().all())

            if not active:
                log.warning("Нет активных аккаунтов")
                return None

            # Round-robin с проверкой лимитов
            for _ in range(len(active)):
                idx = self._rotation_index % len(active)
                self._rotation_index += 1
                account = active[idx]

                if self.rate_limiter.can_comment(account.phone, account.days_active or 0):
                    # Проверить что клиент подключён
                    client = self.session_mgr.get_client(account.phone)
                    if client and client.is_connected():
                        return account
                    else:
                        log.debug(f"{account.phone}: клиент не подключён")

            log.warning("Все аккаунты на cooldown или отключены")
            return None

    async def record_comment(self, phone: str):
        """Зафиксировать комментарий: rate limiter + БД."""
        self.rate_limiter.record_comment(phone)
        async with async_session() as session:
            await session.execute(
                update(Account)
                .where(Account.phone == phone)
                .values(
                    comments_today=Account.comments_today + 1,
                    total_comments=Account.total_comments + 1,
                    last_active_at=utcnow(),
                )
            )
            await session.commit()

    async def handle_error(self, phone: str, error_type: str, details: str = ""):
        """Обработать ошибку аккаунта."""
        if error_type == "flood_wait":
            seconds = int(details) if details.isdigit() else 300
            self.rate_limiter.set_flood_wait(phone, seconds)
            await self._update_status(
                phone,
                "flood_wait",
                quarantine_sec=max(300, seconds),
                restriction_reason="flood_wait",
            )

        elif error_type == "banned":
            await self._update_status(
                phone,
                "banned",
                health_status="dead",
                lifecycle_stage="restricted",
                restriction_reason="banned",
            )
            await self.session_mgr.disconnect_client(phone)
            log.error(f"Аккаунт ЗАБАНЕН: {phone}")

        elif error_type == "forbidden":
            self.rate_limiter.set_cooldown(phone, settings.COMMENT_COOLDOWN_AFTER_ERROR_SEC)
            await self._update_status(
                phone,
                "cooldown",
                quarantine_sec=settings.COMMENT_COOLDOWN_AFTER_ERROR_SEC,
                restriction_reason="forbidden",
            )

        else:
            self.rate_limiter.set_cooldown(phone, settings.COMMENT_COOLDOWN_AFTER_ERROR_SEC)
            await self._update_status(
                phone,
                "cooldown",
                quarantine_sec=settings.COMMENT_COOLDOWN_AFTER_ERROR_SEC,
                restriction_reason="unknown_error",
            )

    async def _update_status(
        self,
        phone: str,
        status: str,
        *,
        quarantine_sec: int | None = None,
        health_status: str | None = None,
        lifecycle_stage: str | None = None,
        restriction_reason: str | None = None,
    ):
        """Обновить статус аккаунта в БД."""
        async with async_session() as session:
            result = await session.execute(select(Account).where(Account.phone == phone))
            account = result.scalar_one_or_none()
            if account is None:
                return

            values: dict = {"status": status}
            if quarantine_sec is not None:
                values["quarantined_until"] = utcnow() + timedelta(seconds=max(1, int(quarantine_sec)))
            if health_status is not None:
                values["health_status"] = health_status
            if lifecycle_stage is not None:
                values["lifecycle_stage"] = lifecycle_stage
            if restriction_reason is not None:
                values["restriction_reason"] = restriction_reason

            await session.execute(
                update(Account)
                .where(Account.phone == phone)
                .values(**values)
            )
            if lifecycle_stage and account.lifecycle_stage != lifecycle_stage:
                session.add(
                    AccountStageEvent(
                        account_id=account.id,
                        phone=phone,
                        from_stage=account.lifecycle_stage,
                        to_stage=lifecycle_stage,
                        actor="account_manager",
                        reason=f"status={status}",
                    )
                )
            await session.commit()

    async def reset_daily_counters(self):
        """Сбросить дневные счётчики (вызывается в полночь)."""
        async with async_session() as session:
            # Сбросить comments_today для всех, но days_active только для НЕ banned
            await session.execute(
                update(Account).values(comments_today=0)
            )
            await session.execute(
                update(Account)
                .where(Account.status != "banned")
                .values(days_active=Account.days_active + 1)
            )
            # Lifecycle transition after warmup period.
            await session.execute(
                update(Account)
                .where(Account.lifecycle_stage == "warming_up", Account.days_active >= 15)
                .values(
                    lifecycle_stage=(
                        "gate_review"
                        if settings.MANUAL_GATE_REQUIRED
                        else ("execution_ready" if settings.HUMAN_GATED_PACKAGING else "active_commenting")
                    )
                )
            )
            # Восстановить cooldown и flood_wait аккаунты
            await session.execute(
                update(Account)
                .where(Account.status.in_(["cooldown", "flood_wait"]))
                .values(status="active")
            )
            await session.commit()
        log.info("Дневные счётчики сброшены, days_active увеличен")

    async def get_status_summary(self, user_id: int = None) -> dict:
        """Сводка по всем аккаунтам (включая banned)."""
        accounts = await self.load_accounts(include_banned=True, user_id=user_id)
        summary = {
            "total": len(accounts),
            "active": 0,
            "cooldown": 0,
            "banned": 0,
            "flood_wait": 0,
            "error": 0,
            "connected": 0,
            "total_comments_today": 0,
        }

        for acc in accounts:
            summary[acc.status] = summary.get(acc.status, 0) + 1
            summary["total_comments_today"] += acc.comments_today or 0
            if self.session_mgr.get_client(acc.phone):
                summary["connected"] += 1

        return summary

    async def auto_recover(self, user_id: int = None) -> dict:
        """
        Авто-восстановление аккаунтов из flood_wait/cooldown/error.
        Проверяет прошло ли достаточно времени и пытается переподключить.

        Возвращает {"recovered": int, "still_blocked": int, "reconnected": int}.
        """
        recovered = 0
        still_blocked = 0
        reconnected = 0

        async with async_session() as session:
            query = select(Account).where(
                Account.status.in_(["flood_wait", "cooldown", "error"])
            )
            if user_id is not None:
                query = query.where(Account.user_id == user_id)
            result = await session.execute(query)
            blocked = list(result.scalars().all())

        for account in blocked:
            phone = account.phone

            # Проверить через rate_limiter не истёк ли cooldown
            if account.quarantined_until and account.quarantined_until > utcnow():
                still_blocked += 1
                continue
            if self.rate_limiter.can_comment(phone, account.days_active or 0):
                client = self.session_mgr.get_client(phone)

                if client and client.is_connected():
                    await self._update_status(phone, "active")
                    recovered += 1
                    log.info(f"{phone}: авто-восстановлен из {account.status}")
                else:
                    proxy = self.proxy_mgr.get_for_account(phone)
                    try:
                        client = await self.session_mgr.connect_client(phone, proxy, user_id=account.user_id)
                    except Exception as exc:
                        log.warning(f"{phone}: ошибка реконнекта в auto_recover: {exc}")
                        still_blocked += 1
                        continue
                    if client:
                        await self._update_status(phone, "active")
                        recovered += 1
                        reconnected += 1
                        log.info(f"{phone}: переподключён и восстановлен")
                    else:
                        still_blocked += 1
            else:
                still_blocked += 1

        if recovered > 0:
            log.info(
                f"Авто-восстановление: {recovered} восстановлено, "
                f"{reconnected} переподключено, {still_blocked} ещё заблокировано"
            )

        return {
            "recovered": recovered,
            "still_blocked": still_blocked,
            "reconnected": reconnected,
        }

    async def disconnect_all(self):
        """Отключить все аккаунты."""
        await self.session_mgr.disconnect_all()
