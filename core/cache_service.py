"""Сервис кэширования Redis для снижения нагрузки на PostgreSQL.

Предоставляет единый слой кэширования поверх существующего подключения
task_queue._redis (redis.asyncio). Не создаёт отдельного соединения —
использует то же соединение, что и TaskQueue.

Типичное использование в эндпоинте:
    cache = CacheService(task_queue)
    cached = await cache.get("clusters:5:tech")
    if cached:
        return cached
    result = compute_heavy_query()
    await cache.set("clusters:5:tech", result, ttl=60)
    return result
"""

from __future__ import annotations

import functools
import json
import logging
from typing import Any, Callable, Optional

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# TTL по умолчанию для каждого типа данных
# ---------------------------------------------------------------------------

TTL_BRIEF = 300          # BusinessBrief — 5 мин (меняется редко)
TTL_HEALTH = 30          # AccountHealthScore — 30 сек (часто обновляется)
TTL_CLUSTERS = 60        # Кластеры карты — 1 мин (тяжёлый SQL GROUP BY)
TTL_CHANNEL_GEO = 300    # Массив [lat, lng, id] для глобуса — 5 мин
TTL_FARM_STATS = 10      # Живые счётчики фермы — 10 сек (почти real-time)
TTL_CHANNEL_DETAIL = 120 # Детальная запись канала — 2 мин

# Префикс для всех ключей кэша (отделяет от очередей / состояний)
_CACHE_NS = "cache:"


class CacheService:
    """Высокоуровневый кэш поверх redis.asyncio.

    Принимает экземпляр TaskQueue вместо прямого Redis-клиента, чтобы
    переиспользовать уже установленное соединение и не плодить лишние
    коннекшны к Redis.

    Args:
        task_queue: экземпляр TaskQueue (должен быть подключён к Redis).
    """

    def __init__(self, task_queue: Any) -> None:
        # Принимаем TaskQueue — обращаемся через его вспомогательные методы
        self._tq = task_queue

    # ------------------------------------------------------------------
    # Базовые операции
    # ------------------------------------------------------------------

    async def get(self, key: str) -> Optional[dict]:
        """Получить значение из кэша. Возвращает dict или None при промахе.

        Args:
            key: ключ без namespace-префикса (добавляется автоматически).
        """
        raw: Optional[str] = await self._tq.cache_get(_CACHE_NS + key)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError, ValueError):
            log.warning("cache.get: не удалось десериализовать ключ %r — инвалидируем", key)
            # Сломанное значение удаляем, чтобы не отдавать мусор
            await self.delete(key)
            return None

    async def set(self, key: str, value: Any, ttl_seconds: int = 60) -> None:
        """Сохранить значение в кэш с TTL.

        Args:
            key: ключ без namespace-префикса.
            value: любой JSON-сериализуемый объект (dict, list и т.д.).
            ttl_seconds: время жизни в секундах.
        """
        try:
            raw = json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            log.warning("cache.set: не удалось сериализовать ключ %r: %s", key, exc)
            return
        await self._tq.cache_set(_CACHE_NS + key, raw, ex=ttl_seconds)

    async def delete(self, key: str) -> None:
        """Удалить один ключ из кэша.

        Args:
            key: ключ без namespace-префикса.
        """
        await self._tq.cache_delete(_CACHE_NS + key)

    async def delete_pattern(self, pattern: str) -> int:
        """SCAN + DEL для массовой инвалидации по паттерну.

        Внутренне добавляет namespace-префикс. Работает через SCAN ITER,
        поэтому безопасно для больших БД (не блокирует Redis).

        Args:
            pattern: glob-паттерн, например "clusters:*" или "health:42".

        Returns:
            Количество удалённых ключей.
        """
        # Убеждаемся, что соединение установлено
        await self._tq.connect()
        redis = self._tq._redis
        if redis is None:
            return 0

        full_pattern = _CACHE_NS + pattern
        keys: list[str] = []
        async for key in redis.scan_iter(match=full_pattern):
            keys.append(str(key))

        if not keys:
            return 0

        removed: int = await redis.delete(*keys)
        log.debug("cache.delete_pattern %r: удалено %d ключей", full_pattern, removed)
        return int(removed)

    # ------------------------------------------------------------------
    # Декоратор для кэширования результатов эндпоинтов
    # ------------------------------------------------------------------

    def cached(self, key_pattern: str, ttl: int = 60) -> Callable:
        """Декоратор для автоматического кэширования результата async-функции.

        Ключ кэша строится через key_pattern.format(**kwargs) — параметры
        декорируемой функции подставляются как именованные аргументы.

        Пример использования:
            @cache.cached("my_data:{workspace_id}", ttl=300)
            async def get_my_data(workspace_id: int):
                return await db.fetch(...)

        Args:
            key_pattern: шаблон ключа, например "clusters:{zoom}:{category}".
            ttl: время жизни кэша в секундах.
        """
        def decorator(func: Callable) -> Callable:
            @functools.wraps(func)
            async def wrapper(*args: Any, **kwargs: Any) -> Any:
                # Строим ключ из kwargs (позиционные аргументы не поддерживаем
                # в шаблоне — явно лучше, чем магия по индексу)
                try:
                    cache_key = key_pattern.format(**kwargs)
                except KeyError as exc:
                    log.warning(
                        "cache.cached: не удалось подставить %r в шаблон %r: %s",
                        kwargs, key_pattern, exc,
                    )
                    # При ошибке шаблона просто выполняем функцию без кэша
                    return await func(*args, **kwargs)

                hit = await self.get(cache_key)
                if hit is not None:
                    log.debug("cache HIT: %r", cache_key)
                    return hit

                log.debug("cache MISS: %r", cache_key)
                result = await func(*args, **kwargs)

                if result is not None:
                    await self.set(cache_key, result, ttl_seconds=ttl)

                return result

            return wrapper
        return decorator

    # ------------------------------------------------------------------
    # Хелперы инвалидации для конкретных сущностей
    # ------------------------------------------------------------------

    async def invalidate_brief(self, workspace_id: int) -> None:
        """Инвалидировать кэш BusinessBrief для воркспейса.

        Вызывать при POST /v1/context/confirm.

        Args:
            workspace_id: ID воркспейса.
        """
        await self.delete(f"brief:{workspace_id}")
        log.debug("cache: инвалидирован brief для workspace_id=%d", workspace_id)

    async def invalidate_health(self, account_id: int) -> None:
        """Инвалидировать кэш AccountHealthScore для аккаунта.

        Вызывать при обновлении health score.

        Args:
            account_id: ID аккаунта.
        """
        await self.delete(f"health:{account_id}")
        log.debug("cache: инвалидирован health для account_id=%d", account_id)

    async def invalidate_clusters(self) -> int:
        """Инвалидировать все кластеры карты каналов.

        Вызывать при импорте каналов (POST /v1/channel-db/{id}/import).

        Returns:
            Количество удалённых ключей кэша кластеров.
        """
        removed = await self.delete_pattern("clusters:*")
        # Также инвалидируем предвычисленный гео-массив
        await self.delete("channel_geo")
        log.debug("cache: инвалидированы clusters (удалено %d) + channel_geo", removed)
        return removed + 1

    async def invalidate_farm_stats(self, farm_id: int) -> None:
        """Инвалидировать кэш живых счётчиков фермы.

        Вызывать при событиях фермы (start/stop/thread event).

        Args:
            farm_id: ID фермы.
        """
        await self.delete(f"farm_stats:{farm_id}")
        log.debug("cache: инвалидирован farm_stats для farm_id=%d", farm_id)

    async def invalidate_channel_detail(self, channel_id: int) -> None:
        """Инвалидировать кэш детальной записи канала.

        Args:
            channel_id: ID записи channel_map_entries.
        """
        await self.delete(f"channel_detail:{channel_id}")
        log.debug("cache: инвалидирован channel_detail для channel_id=%d", channel_id)

    # ------------------------------------------------------------------
    # Удобные методы с фиксированными ключами и TTL
    # (соответствуют спецификации — см. docstring модуля)
    # ------------------------------------------------------------------

    async def get_brief(self, workspace_id: int) -> Optional[dict]:
        """Получить BusinessBrief из кэша."""
        return await self.get(f"brief:{workspace_id}")

    async def set_brief(self, workspace_id: int, value: dict) -> None:
        """Сохранить BusinessBrief в кэш на 5 минут."""
        await self.set(f"brief:{workspace_id}", value, ttl_seconds=TTL_BRIEF)

    async def get_health(self, account_id: int) -> Optional[dict]:
        """Получить AccountHealthScore из кэша."""
        return await self.get(f"health:{account_id}")

    async def set_health(self, account_id: int, value: dict) -> None:
        """Сохранить AccountHealthScore в кэш на 30 секунд."""
        await self.set(f"health:{account_id}", value, ttl_seconds=TTL_HEALTH)

    async def get_clusters(self, zoom: int, category: Optional[str]) -> Optional[dict]:
        """Получить предвычисленные кластеры карты из кэша."""
        return await self.get(f"clusters:{zoom}:{category or 'all'}")

    async def set_clusters(
        self, zoom: int, category: Optional[str], value: dict
    ) -> None:
        """Сохранить кластеры карты в кэш на 60 секунд."""
        await self.set(
            f"clusters:{zoom}:{category or 'all'}", value, ttl_seconds=TTL_CLUSTERS
        )

    async def get_channel_geo(self) -> Optional[list]:
        """Получить компактный гео-массив [lat, lng, id] для глобуса."""
        return await self.get("channel_geo")

    async def set_channel_geo(self, value: list) -> None:
        """Сохранить гео-массив в кэш на 5 минут."""
        await self.set("channel_geo", value, ttl_seconds=TTL_CHANNEL_GEO)

    async def get_farm_stats(self, farm_id: int) -> Optional[dict]:
        """Получить живые счётчики фермы из кэша."""
        return await self.get(f"farm_stats:{farm_id}")

    async def set_farm_stats(self, farm_id: int, value: dict) -> None:
        """Сохранить живые счётчики фермы в кэш на 10 секунд."""
        await self.set(f"farm_stats:{farm_id}", value, ttl_seconds=TTL_FARM_STATS)

    async def get_channel_detail(self, channel_id: int) -> Optional[dict]:
        """Получить детальную запись канала из кэша."""
        return await self.get(f"channel_detail:{channel_id}")

    async def set_channel_detail(self, channel_id: int, value: dict) -> None:
        """Сохранить детальную запись канала в кэш на 2 минуты."""
        await self.set(
            f"channel_detail:{channel_id}", value, ttl_seconds=TTL_CHANNEL_DETAIL
        )
