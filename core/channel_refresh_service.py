"""ChannelRefreshService — фоновый сервис ежедневного обновления channel_map_entries.

Запускается как asyncio background task при старте FastAPI (аналогично WarmupScheduler).
Обновляет каналы батчами, уважая rate limits.

Источники данных (в порядке приоритета):
  1. TGStat API (если задан TGSTAT_API_TOKEN)
  2. Telethon (если есть доступная сессия в session_manager)

Важно:
  - НЕ использует KZ warmup-аккаунт (и вообще warmup-аккаунты)
  - Максимум 50 каналов за цикл обновления
  - Graceful degrade: если колонки из migration 20260316_45 не существуют,
    продолжает работу без сбоя
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

import aiohttp
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from storage.models import ChannelMapEntry
from utils.helpers import utcnow

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Конфигурация из переменных окружения
# ---------------------------------------------------------------------------

def _env_bool(key: str, default: bool) -> bool:
    val = os.environ.get(key, "").strip().lower()
    if val in ("1", "true", "yes", "on"):
        return True
    if val in ("0", "false", "no", "off"):
        return False
    return default


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, default))
    except (ValueError, TypeError):
        return default


CHANNEL_REFRESH_ENABLED: bool = _env_bool("CHANNEL_REFRESH_ENABLED", False)
CHANNEL_REFRESH_INTERVAL_HOURS: int = _env_int("CHANNEL_REFRESH_INTERVAL_HOURS", 24)
CHANNEL_REFRESH_BATCH_SIZE: int = min(_env_int("CHANNEL_REFRESH_BATCH_SIZE", 50), 50)
CHANNEL_REFRESH_DELAY_BETWEEN_SEC: int = _env_int("CHANNEL_REFRESH_DELAY_BETWEEN_SEC", 5)
TGSTAT_API_TOKEN: str = os.environ.get("TGSTAT_API_TOKEN", "").strip()
TGSTAT_BASE_URL: str = "https://api.tgstat.ru/channels/stat"

# Защита: никогда не обновлять более 50 каналов за цикл
_MAX_BATCH = 50

# ---------------------------------------------------------------------------
# Вспомогательные классы данных
# ---------------------------------------------------------------------------


class ChannelLiveData:
    """Живые данные канала, полученные из внешнего источника."""

    __slots__ = (
        "member_count",
        "engagement_rate",
        "avg_post_reach",
        "post_frequency_daily",
        "source",
    )

    def __init__(
        self,
        member_count: Optional[int] = None,
        engagement_rate: Optional[float] = None,
        avg_post_reach: Optional[int] = None,
        post_frequency_daily: Optional[float] = None,
        source: str = "unknown",
    ) -> None:
        self.member_count = member_count
        self.engagement_rate = engagement_rate
        self.avg_post_reach = avg_post_reach
        self.post_frequency_daily = post_frequency_daily
        self.source = source


# ---------------------------------------------------------------------------
# ChannelRefreshService
# ---------------------------------------------------------------------------


class ChannelRefreshService:
    """Background service that refreshes channel_map_entries with live Telegram data.

    Runs as a background asyncio task, similar to WarmupScheduler.
    Refreshes channels in batches, respecting rate limits.
    """

    def __init__(
        self,
        db_session_factory: Any = None,
        session_manager: Any = None,
    ) -> None:
        self._db_session_factory = db_session_factory
        # session_manager — опциональный, используется для Telethon fallback.
        # Warmup-аккаунты игнорируются (контролируется в _get_non_warmup_client).
        self._session_manager = session_manager
        self._running = False
        self._task: Optional[asyncio.Task] = None
        # HTTP-сессия для TGStat API (создаётся лениво)
        self._http_session: Optional[aiohttp.ClientSession] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Запустить фоновый asyncio task."""
        if not CHANNEL_REFRESH_ENABLED:
            log.info(
                "channel_refresh_service: CHANNEL_REFRESH_ENABLED=false, "
                "service not started"
            )
            return
        if self._running:
            log.warning(
                "channel_refresh_service: already running, skipping duplicate start"
            )
            return
        self._running = True
        self._task = asyncio.create_task(self._run_forever())
        self._task.add_done_callback(self._on_task_done)
        log.info(
            "channel_refresh_service: started "
            "(interval=%dh, batch=%d, delay_between=%ds, tgstat=%s)",
            CHANNEL_REFRESH_INTERVAL_HOURS,
            CHANNEL_REFRESH_BATCH_SIZE,
            CHANNEL_REFRESH_DELAY_BETWEEN_SEC,
            "enabled" if TGSTAT_API_TOKEN else "disabled",
        )

    async def stop(self) -> None:
        """Остановить фоновый task gracefully."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._http_session and not self._http_session.closed:
            await self._http_session.close()
            self._http_session = None
        log.info("channel_refresh_service: stopped")

    def _on_task_done(self, task: asyncio.Task) -> None:
        """Callback при завершении background task."""
        self._running = False
        if task.cancelled():
            log.info("channel_refresh_service: background task cancelled (normal shutdown)")
        elif task.exception():
            exc = task.exception()
            log.error(
                "channel_refresh_service: background task crashed: %s",
                exc,
                exc_info=exc,
            )
        else:
            log.info("channel_refresh_service: background task finished normally")

    @property
    def is_running(self) -> bool:
        return self._running

    # ------------------------------------------------------------------
    # Главный цикл
    # ------------------------------------------------------------------

    async def _run_forever(self) -> None:
        """Основной loop: спит до следующего окна обновления, затем запускает батч."""
        log.info(
            "channel_refresh_service: poll loop started, next refresh in %dh",
            CHANNEL_REFRESH_INTERVAL_HOURS,
        )
        # Первый запуск — через 5 минут после старта приложения,
        # чтобы не нагружать старт.
        await asyncio.sleep(300)

        while self._running:
            try:
                refreshed = await self.refresh_batch(CHANNEL_REFRESH_BATCH_SIZE)
                log.info(
                    "channel_refresh_service: batch complete, refreshed=%d",
                    refreshed,
                )
            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.error(
                    "channel_refresh_service: refresh_batch failed: %s",
                    exc,
                    exc_info=True,
                )

            # Спим до следующего окна
            sleep_sec = CHANNEL_REFRESH_INTERVAL_HOURS * 3600
            log.info(
                "channel_refresh_service: sleeping %dh until next refresh cycle",
                CHANNEL_REFRESH_INTERVAL_HOURS,
            )
            try:
                await asyncio.sleep(sleep_sec)
            except asyncio.CancelledError:
                break

    # ------------------------------------------------------------------
    # Публичные методы
    # ------------------------------------------------------------------

    async def refresh_batch(self, batch_size: int = 50) -> int:
        """Выбрать и обновить батч самых устаревших каналов.

        Критерий выборки: last_refreshed_at < now() - 24h, NULL в начале.
        Возвращает количество успешно обновлённых каналов.
        """
        batch_size = min(batch_size, _MAX_BATCH)

        if self._db_session_factory is None:
            log.warning("channel_refresh_service: no db_session_factory, skipping")
            return 0

        cutoff = utcnow() - timedelta(hours=CHANNEL_REFRESH_INTERVAL_HOURS)

        async with self._db_session_factory() as session:
            stmt = (
                select(ChannelMapEntry)
                .where(
                    ChannelMapEntry.username.isnot(None),
                    ChannelMapEntry.username != "",
                    (
                        ChannelMapEntry.last_refreshed_at.is_(None)
                        | (ChannelMapEntry.last_refreshed_at < cutoff)
                    ),
                )
                .order_by(ChannelMapEntry.last_refreshed_at.asc().nullsfirst())
                .limit(batch_size)
            )
            result = await session.execute(stmt)
            channels = list(result.scalars().all())

        if not channels:
            log.info("channel_refresh_service: no stale channels found in this batch")
            return 0

        log.info(
            "channel_refresh_service: refreshing %d channels (cutoff=%s)",
            len(channels),
            cutoff.strftime("%Y-%m-%d %H:%M"),
        )

        refreshed_count = 0
        for i, channel in enumerate(channels):
            if not self._running:
                log.info("channel_refresh_service: stopped mid-batch at %d/%d", i, len(channels))
                break

            try:
                success = await self.refresh_channel(channel.id)
                if success:
                    refreshed_count += 1
                    log.info(
                        "channel_refresh_service: refreshed @%s (id=%d) [%d/%d]",
                        channel.username or "?",
                        channel.id,
                        i + 1,
                        len(channels),
                    )
                else:
                    log.info(
                        "channel_refresh_service: skipped @%s (id=%d) — no data source",
                        channel.username or "?",
                        channel.id,
                    )
            except Exception as exc:
                log.warning(
                    "channel_refresh_service: failed @%s (id=%d): %s",
                    channel.username or "?",
                    channel.id,
                    exc,
                )

            # Задержка между обращениями к Telegram API для избежания rate limit
            if i < len(channels) - 1 and CHANNEL_REFRESH_DELAY_BETWEEN_SEC > 0:
                await asyncio.sleep(CHANNEL_REFRESH_DELAY_BETWEEN_SEC)

        return refreshed_count

    async def refresh_channel(self, channel_id: int) -> bool:
        """Обновить один канал из живого источника.

        Порядок приоритетов:
          1. TGStat API (если TGSTAT_API_TOKEN задан)
          2. Telethon (если есть non-warmup сессия)

        Обновляет: member_count, engagement_rate, avg_post_reach,
                   post_frequency_daily, last_refreshed_at.

        Дополнительно: вставляет subscriber_snapshot за сегодня (если таблица существует),
                       пересчитывает growth_rate_7d / growth_rate_30d.

        Возвращает True при успешном получении живых данных, False — если источник
        недоступен (что не является ошибкой — просто нет данных).
        """
        if self._db_session_factory is None:
            return False

        # Загружаем запись канала
        async with self._db_session_factory() as session:
            channel = await session.get(ChannelMapEntry, channel_id)
            if channel is None:
                log.warning(
                    "channel_refresh_service: channel id=%d not found", channel_id
                )
                return False
            username = channel.username

        if not username:
            log.info("channel_refresh_service: channel id=%d has no username, skipping", channel_id)
            return False

        # --- Попытка получить живые данные ---
        live_data: Optional[ChannelLiveData] = None

        if TGSTAT_API_TOKEN:
            try:
                live_data = await self._fetch_via_tgstat(username)
            except Exception as exc:
                log.warning(
                    "channel_refresh_service: TGStat failed for @%s: %s", username, exc
                )

        if live_data is None:
            # Fallback на Telethon
            client = await self._get_non_warmup_client()
            if client is not None:
                try:
                    live_data = await self._fetch_via_telethon(client, username)
                except Exception as exc:
                    log.warning(
                        "channel_refresh_service: Telethon failed for @%s: %s", username, exc
                    )

        if live_data is None:
            # Нет источников данных — обновляем только timestamp,
            # чтобы не застревать в очереди
            async with self._db_session_factory() as session:
                ch = await session.get(ChannelMapEntry, channel_id)
                if ch is not None:
                    ch.last_refreshed_at = utcnow()
                    await session.commit()
            return False

        # --- Сохраняем живые данные ---
        async with self._db_session_factory() as session:
            ch = await session.get(ChannelMapEntry, channel_id)
            if ch is None:
                return False

            now = utcnow()

            # Безопасное обновление полей (graceful — если колонка отсутствует, пропускаем)
            _safe_set(ch, "member_count", live_data.member_count)
            _safe_set(ch, "engagement_rate", live_data.engagement_rate)
            _safe_set(ch, "avg_post_reach", live_data.avg_post_reach)
            _safe_set(ch, "post_frequency_daily", live_data.post_frequency_daily)
            _safe_set(ch, "last_refreshed_at", now)

            await session.commit()
            log.debug(
                "channel_refresh_service: saved live data for @%s (source=%s, members=%s)",
                username,
                live_data.source,
                live_data.member_count,
            )

        # --- Снапшот подписчиков (если таблица существует) ---
        if live_data.member_count is not None:
            await self._upsert_subscriber_snapshot(
                channel_id=channel_id,
                member_count=live_data.member_count,
            )
            await self.calculate_growth_rates(channel_id)

        return True

    # ------------------------------------------------------------------
    # TGStat API
    # ------------------------------------------------------------------

    async def _fetch_via_tgstat(self, username: str) -> Optional[ChannelLiveData]:
        """Получить данные канала через TGStat API.

        Endpoint: GET /channels/stat?token=...&peer=@username
        Документация: https://api.tgstat.ru/docs/ru/channels/stat.html
        """
        if not TGSTAT_API_TOKEN:
            return None

        http = await self._get_http_session()
        peer = f"@{username.lstrip('@')}"
        params = {"token": TGSTAT_API_TOKEN, "peer": peer}

        async with http.get(
            TGSTAT_BASE_URL, params=params, timeout=aiohttp.ClientTimeout(total=15)
        ) as resp:
            if resp.status == 404:
                log.debug("channel_refresh_service: TGStat 404 for %s", peer)
                return None
            if resp.status == 429:
                log.warning("channel_refresh_service: TGStat rate limit hit for %s", peer)
                return None
            if resp.status != 200:
                log.warning(
                    "channel_refresh_service: TGStat returned %d for %s",
                    resp.status,
                    peer,
                )
                return None

            payload = await resp.json(content_type=None)

        # TGStat response shape: {"status": "ok", "response": {...}}
        if payload.get("status") != "ok":
            log.warning(
                "channel_refresh_service: TGStat non-ok status for %s: %s",
                peer,
                payload.get("error", "unknown"),
            )
            return None

        data = payload.get("response", {}) or {}
        participants_count: Optional[int] = _safe_int(data.get("participantsCount"))
        avg_reach: Optional[int] = _safe_int(data.get("avgPostReach"))
        er: Optional[float] = _safe_float(data.get("er"))
        # TGStat не даёт post_frequency напрямую — оставляем None
        post_freq: Optional[float] = None
        posts_per_week = _safe_float(data.get("postsPerWeek"))
        if posts_per_week is not None:
            post_freq = round(posts_per_week / 7, 3)

        log.debug(
            "channel_refresh_service: TGStat @%s members=%s er=%s reach=%s freq=%.3f/day",
            username,
            participants_count,
            er,
            avg_reach,
            post_freq or 0.0,
        )

        return ChannelLiveData(
            member_count=participants_count,
            engagement_rate=er,
            avg_post_reach=avg_reach,
            post_frequency_daily=post_freq,
            source="tgstat",
        )

    # ------------------------------------------------------------------
    # Telethon fallback
    # ------------------------------------------------------------------

    async def _fetch_via_telethon(
        self, client: Any, username: str
    ) -> Optional[ChannelLiveData]:
        """Получить данные канала через Telethon GetFullChannelRequest + GetHistory."""
        from telethon import functions as tl_functions
        from telethon.tl.types import Channel as TLChannel

        peer = username.lstrip("@")

        try:
            entity = await client.get_entity(peer)
        except Exception as exc:
            log.debug(
                "channel_refresh_service: Telethon get_entity @%s failed: %s", peer, exc
            )
            return None

        if not isinstance(entity, TLChannel) or not getattr(entity, "broadcast", False):
            log.debug(
                "channel_refresh_service: @%s is not a broadcast channel", peer
            )
            return None

        participants_count: Optional[int] = None
        avg_post_reach: Optional[int] = None

        try:
            full = await client(tl_functions.channels.GetFullChannelRequest(channel=entity))
            full_chat = full.full_chat
            participants_count = getattr(full_chat, "participants_count", None)
            if participants_count is None:
                participants_count = getattr(entity, "participants_count", None)
        except Exception as exc:
            log.debug(
                "channel_refresh_service: GetFullChannelRequest @%s failed: %s", peer, exc
            )
            # Не прерываем — попробуем взять хотя бы basic entity данные
            participants_count = getattr(entity, "participants_count", None)

        # Оцениваем частоту постов по последним 5 сообщениям
        post_freq: Optional[float] = None
        try:
            messages = await client.get_messages(entity, limit=5)
            if messages and len(messages) >= 2:
                dates = sorted(
                    [getattr(m, "date", None) for m in messages if getattr(m, "date", None)],
                    reverse=True,
                )
                if len(dates) >= 2:
                    newest, oldest = dates[0], dates[-1]
                    if newest.tzinfo is None:
                        newest = newest.replace(tzinfo=timezone.utc)
                    if oldest.tzinfo is None:
                        oldest = oldest.replace(tzinfo=timezone.utc)
                    days = max((newest - oldest).total_seconds() / 86400, 1)
                    post_freq = round(len(dates) / days, 3)
            # Reach из views первого сообщения (приблизительно)
            if messages:
                first_msg = messages[0]
                views = getattr(first_msg, "views", None)
                if views:
                    avg_post_reach = int(views)
        except Exception as exc:
            log.debug(
                "channel_refresh_service: get_messages @%s failed: %s", peer, exc
            )

        # Engagement rate не вычисляем через Telethon — нет реакций в basic API
        log.debug(
            "channel_refresh_service: Telethon @%s members=%s reach=%s freq=%s",
            peer,
            participants_count,
            avg_post_reach,
            post_freq,
        )

        return ChannelLiveData(
            member_count=_safe_int(participants_count),
            engagement_rate=None,
            avg_post_reach=avg_post_reach,
            post_frequency_daily=post_freq,
            source="telethon",
        )

    # ------------------------------------------------------------------
    # Снапшоты подписчиков и расчёт growth rate
    # ------------------------------------------------------------------

    async def _upsert_subscriber_snapshot(
        self, channel_id: int, member_count: int
    ) -> None:
        """UPSERT строки subscriber_snapshots за сегодня.

        Graceful: если таблица не существует (migration 20260316_45 не применена),
        логирует предупреждение и продолжает работу без сбоя.
        """
        if self._db_session_factory is None:
            return

        today = date.today().isoformat()
        try:
            async with self._db_session_factory() as session:
                await session.execute(
                    text(
                        """
                        INSERT INTO channel_subscriber_snapshots
                            (channel_id, snapshot_date, member_count, created_at)
                        VALUES
                            (:channel_id, :snapshot_date, :member_count, NOW())
                        ON CONFLICT (channel_id, snapshot_date)
                        DO UPDATE SET
                            member_count = EXCLUDED.member_count
                        """
                    ),
                    {
                        "channel_id": channel_id,
                        "snapshot_date": today,
                        "member_count": member_count,
                    },
                )
                await session.commit()
        except Exception as exc:
            # Таблица может не существовать — это не критичная ошибка
            err_str = str(exc).lower()
            if "does not exist" in err_str or "no such table" in err_str:
                log.debug(
                    "channel_refresh_service: subscriber_snapshots table not found "
                    "(migration 20260316_45 pending), skipping snapshot"
                )
            else:
                log.warning(
                    "channel_refresh_service: failed to upsert subscriber_snapshot "
                    "for channel_id=%d: %s",
                    channel_id,
                    exc,
                )

    async def calculate_growth_rates(self, channel_id: int) -> None:
        """Пересчитать growth_rate_7d и growth_rate_30d из таблицы снапшотов.

        Формула: ((current - past) / past) * 100, округлено до 2 знаков.

        Graceful: если таблица или колонки не существуют, пропускает расчёт.
        """
        if self._db_session_factory is None:
            return

        try:
            async with self._db_session_factory() as session:
                result = await session.execute(
                    text(
                        """
                        SELECT snapshot_date, member_count
                        FROM channel_subscriber_snapshots
                        WHERE channel_id = :channel_id
                        ORDER BY snapshot_date DESC
                        LIMIT 31
                        """
                    ),
                    {"channel_id": channel_id},
                )
                rows = result.fetchall()
        except Exception as exc:
            err_str = str(exc).lower()
            if "does not exist" in err_str or "no such table" in err_str:
                log.debug(
                    "channel_refresh_service: subscriber_snapshots table not found, "
                    "skipping growth rate calculation"
                )
            else:
                log.warning(
                    "channel_refresh_service: failed to fetch snapshots for "
                    "channel_id=%d: %s",
                    channel_id,
                    exc,
                )
            return

        if not rows:
            return

        # rows отсортированы DESC (newest first)
        current_count = rows[0][1]  # member_count сегодня

        growth_7d: Optional[float] = None
        growth_30d: Optional[float] = None

        # Ищем снапшоты ближайшие к 7 и 30 дням назад
        today = date.today()
        target_7 = today - timedelta(days=7)
        target_30 = today - timedelta(days=30)

        for row in rows:
            snap_date_val = row[0]
            snap_count = row[1]
            # snap_date_val может быть str или date
            if isinstance(snap_date_val, str):
                snap_date = date.fromisoformat(snap_date_val)
            elif isinstance(snap_date_val, datetime):
                snap_date = snap_date_val.date()
            else:
                snap_date = snap_date_val  # уже date

            if growth_7d is None and snap_date <= target_7:
                if snap_count and snap_count > 0:
                    growth_7d = round((current_count - snap_count) / snap_count * 100, 2)

            if growth_30d is None and snap_date <= target_30:
                if snap_count and snap_count > 0:
                    growth_30d = round((current_count - snap_count) / snap_count * 100, 2)

            if growth_7d is not None and growth_30d is not None:
                break

        # Сохраняем в channel_map_entries (graceful если колонки нет)
        try:
            async with self._db_session_factory() as session:
                ch = await session.get(ChannelMapEntry, channel_id)
                if ch is not None:
                    _safe_set(ch, "growth_rate_7d", growth_7d)
                    _safe_set(ch, "growth_rate_30d", growth_30d)
                    await session.commit()
                    log.debug(
                        "channel_refresh_service: growth rates for channel_id=%d "
                        "7d=%s%% 30d=%s%%",
                        channel_id,
                        growth_7d,
                        growth_30d,
                    )
        except Exception as exc:
            log.warning(
                "channel_refresh_service: failed to save growth rates for "
                "channel_id=%d: %s",
                channel_id,
                exc,
            )

    # ------------------------------------------------------------------
    # Вспомогательные приватные методы
    # ------------------------------------------------------------------

    async def _get_non_warmup_client(self) -> Optional[Any]:
        """Вернуть первый доступный Telethon-клиент, который НЕ является warmup-аккаунтом.

        Warmup-аккаунты определяются по наличию активной WarmupConfig в БД.
        Если session_manager недоступен или нет подходящих клиентов — возвращает None.
        """
        if self._session_manager is None:
            return None

        # Получаем список warmup-телефонов из БД (не нагружаем warmup-аккаунты)
        warmup_phones: set[str] = set()
        if self._db_session_factory is not None:
            try:
                warmup_phones = await self._load_warmup_phones()
            except Exception as exc:
                log.debug(
                    "channel_refresh_service: failed to load warmup phones: %s", exc
                )

        try:
            phones = self._session_manager.get_connected_phones()
            for phone in phones:
                # Пропускаем warmup-аккаунты
                normalized = str(phone).lstrip("+")
                if normalized in warmup_phones:
                    continue
                client = self._session_manager.get_client(phone)
                if client and client.is_connected():
                    return client
        except Exception as exc:
            log.warning(
                "channel_refresh_service: _get_non_warmup_client error: %s", exc
            )
        return None

    async def _load_warmup_phones(self) -> set[str]:
        """Загрузить номера телефонов аккаунтов, у которых есть активный WarmupConfig.

        Graceful: если таблица WarmupConfig не существует, возвращает пустое множество.
        """
        from storage.models import Account
        try:
            # Ленивый импорт, чтобы не тянуть WarmupConfig до инициализации модели
            from storage.models import WarmupConfig
        except ImportError:
            return set()

        try:
            async with self._db_session_factory() as session:
                stmt = (
                    select(Account.phone)
                    .join(WarmupConfig, WarmupConfig.account_id == Account.id)
                    .where(WarmupConfig.is_active.is_(True))
                )
                result = await session.execute(stmt)
                return {str(row[0]).lstrip("+") for row in result.fetchall()}
        except Exception as exc:
            err_str = str(exc).lower()
            if "does not exist" in err_str or "no such table" in err_str:
                log.debug(
                    "channel_refresh_service: warmup_configs table not found, "
                    "treating all phones as non-warmup"
                )
            else:
                log.warning("channel_refresh_service: _load_warmup_phones: %s", exc)
            return set()

    async def _get_http_session(self) -> aiohttp.ClientSession:
        """Лениво создать или вернуть HTTP-сессию для TGStat API."""
        if self._http_session is None or self._http_session.closed:
            self._http_session = aiohttp.ClientSession(
                headers={
                    "User-Agent": "NEURO-COMMENTING/1.0 ChannelRefreshService",
                    "Accept": "application/json",
                }
            )
        return self._http_session


# ---------------------------------------------------------------------------
# Приватные вспомогательные функции модуля
# ---------------------------------------------------------------------------


def _safe_set(obj: Any, attr: str, value: Any) -> None:
    """Установить атрибут объекта, если он существует, иначе — тихо пропустить.

    Защищает от ситуации, когда колонка ещё не добавлена миграцией.
    """
    if value is None:
        return  # не перезаписываем None поверх существующих данных
    try:
        if hasattr(obj, attr):
            setattr(obj, attr, value)
        else:
            log.debug(
                "channel_refresh_service: attribute '%s' not found on %s, skipping",
                attr,
                type(obj).__name__,
            )
    except Exception as exc:
        log.debug(
            "channel_refresh_service: _safe_set('%s', %r) failed: %s", attr, value, exc
        )


def _safe_int(value: Any) -> Optional[int]:
    """Конвертировать в int, вернуть None при ошибке."""
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _safe_float(value: Any) -> Optional[float]:
    """Конвертировать в float, вернуть None при ошибке."""
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None
