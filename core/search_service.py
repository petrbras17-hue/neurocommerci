"""
Сервис полнотекстового поиска каналов через Meilisearch.

Предоставляет <5ms full-text поиск для Channel Map.
Использует Meilisearch REST API напрямую через aiohttp (без SDK).

Env vars:
    MEILI_URL         — адрес Meilisearch (default: http://localhost:7700)
    MEILI_MASTER_KEY  — мастер-ключ Meilisearch (опционально для dev)
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, List, Optional

import aiohttp

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Константы
# ---------------------------------------------------------------------------

CHANNELS_INDEX = "channels"

# Настройки индекса Meilisearch
INDEX_CONFIG: dict[str, Any] = {
    "searchableAttributes": [
        "title",
        "username",
        "description",
        "category",
        "language",
        "region",
    ],
    "filterableAttributes": [
        "category",
        "language",
        "region",
        "member_count",
        "spam_score",
        "has_comments",
        "_geo",
    ],
    "sortableAttributes": [
        "member_count",
        "engagement_rate",
        "created_at",
    ],
    "rankingRules": [
        "words",
        "typo",
        "proximity",
        "attribute",
        "sort",
        "exactness",
    ],
    "typoTolerance": {
        "enabled": True,
        "minWordSizeForTypos": {
            "oneTypo": 3,
            "twoTypos": 6,
        },
    },
    # Включаем geo-поиск — Meilisearch поддерживает _geo как зарезервированный атрибут
    "displayedAttributes": ["*"],
}

# Размер чанка при bulk-индексации
_BULK_CHUNK_SIZE = 1000

# Таймаут ожидания задачи Meilisearch (секунды)
_TASK_WAIT_TIMEOUT = 30


# ---------------------------------------------------------------------------
# Dataclass результата поиска
# ---------------------------------------------------------------------------

@dataclass
class SearchResult:
    """Результат поиска из Meilisearch."""
    hits: List[dict]
    total: int                          # estimatedTotalHits
    processing_time_ms: int
    facets: Optional[dict] = None       # facetDistribution, если запрошен


# ---------------------------------------------------------------------------
# Основной класс
# ---------------------------------------------------------------------------

class MeiliSearchService:
    """
    Сервис для работы с Meilisearch: индексация и поиск каналов.

    Жизненный цикл:
        svc = MeiliSearchService()
        await svc.setup_index()   # один раз при старте
        await svc.search("crypto", filters={"category": "crypto"})
    """

    def __init__(
        self,
        url: Optional[str] = None,
        master_key: Optional[str] = None,
    ) -> None:
        self._url = (url or os.getenv("MEILI_URL", "http://localhost:7700")).rstrip("/")
        self._master_key = master_key or os.getenv("MEILI_MASTER_KEY", "")
        # aiohttp session создаётся лениво — не все окружения asyncio-ready при import
        self._session: Optional[aiohttp.ClientSession] = None

    # ------------------------------------------------------------------
    # Управление HTTP-сессией
    # ------------------------------------------------------------------

    def _get_headers(self) -> dict[str, str]:
        """Формирует заголовки для запросов к Meilisearch."""
        headers = {"Content-Type": "application/json"}
        if self._master_key:
            headers["Authorization"] = f"Bearer {self._master_key}"
        return headers

    async def _client(self) -> aiohttp.ClientSession:
        """Возвращает (создаёт при необходимости) aiohttp ClientSession."""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=10)
            self._session = aiohttp.ClientSession(
                headers=self._get_headers(),
                timeout=timeout,
            )
        return self._session

    async def close(self) -> None:
        """Закрывает HTTP-сессию. Вызывать при shutdown приложения."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    # ------------------------------------------------------------------
    # Внутренние хелперы
    # ------------------------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        json: Optional[Any] = None,
        params: Optional[dict] = None,
    ) -> Any:
        """
        Выполняет HTTP-запрос к Meilisearch REST API.
        Бросает RuntimeError при HTTP 4xx/5xx.
        """
        client = await self._client()
        url = f"{self._url}{path}"
        async with client.request(method, url, json=json, params=params) as resp:
            body = await resp.json(content_type=None)
            if resp.status >= 400:
                raise RuntimeError(
                    f"Meilisearch {method} {path} -> {resp.status}: {body}"
                )
            return body

    async def _wait_task(self, task_uid: int) -> dict:
        """
        Ожидает завершения асинхронной задачи Meilisearch.
        Возвращает итоговый статус задачи.
        """
        import asyncio as _asyncio
        deadline = _asyncio.get_event_loop().time() + _TASK_WAIT_TIMEOUT
        while _asyncio.get_event_loop().time() < deadline:
            result = await self._request("GET", f"/tasks/{task_uid}")
            status = result.get("status")
            if status in ("succeeded", "failed", "canceled"):
                if status != "succeeded":
                    logger.warning(
                        "Meilisearch task %d завершилась со статусом '%s': %s",
                        task_uid,
                        status,
                        result.get("error"),
                    )
                return result
            await _asyncio.sleep(0.2)
        raise TimeoutError(f"Meilisearch task {task_uid} не завершилась за {_TASK_WAIT_TIMEOUT}s")

    # ------------------------------------------------------------------
    # Управление индексом
    # ------------------------------------------------------------------

    async def setup_index(self) -> None:
        """
        Создаёт индекс channels (если не существует) и применяет INDEX_CONFIG.
        Безопасно вызывать повторно — идемпотентно.
        """
        # Создаём индекс; игнорируем ошибку "already exists"
        try:
            task = await self._request(
                "POST",
                "/indexes",
                json={"uid": CHANNELS_INDEX, "primaryKey": "id"},
            )
            await self._wait_task(task["taskUid"])
            logger.info("Meilisearch: индекс '%s' создан", CHANNELS_INDEX)
        except RuntimeError as exc:
            if "already exists" in str(exc).lower() or "index_already_exists" in str(exc).lower():
                logger.debug("Meilisearch: индекс '%s' уже существует", CHANNELS_INDEX)
            else:
                raise

        # Применяем настройки
        task = await self._request(
            "PATCH",
            f"/indexes/{CHANNELS_INDEX}/settings",
            json=INDEX_CONFIG,
        )
        await self._wait_task(task["taskUid"])
        logger.info("Meilisearch: настройки индекса '%s' применены", CHANNELS_INDEX)

    # ------------------------------------------------------------------
    # Индексация
    # ------------------------------------------------------------------

    async def index_channel(self, channel: dict) -> None:
        """
        Добавляет или обновляет один канал в индексе.

        channel — словарь в формате документа Meilisearch (см. channel_to_doc).
        """
        task = await self._request(
            "POST",
            f"/indexes/{CHANNELS_INDEX}/documents",
            json=[channel],
        )
        await self._wait_task(task["taskUid"])

    async def bulk_index(self, channels: List[dict]) -> int:
        """
        Батч-индексация каналов. Разбивает на чанки по _BULK_CHUNK_SIZE.
        Возвращает общее количество успешно поставленных в очередь документов.
        """
        if not channels:
            return 0

        total_queued = 0
        for i in range(0, len(channels), _BULK_CHUNK_SIZE):
            chunk = channels[i : i + _BULK_CHUNK_SIZE]
            task = await self._request(
                "POST",
                f"/indexes/{CHANNELS_INDEX}/documents",
                json=chunk,
            )
            await self._wait_task(task["taskUid"])
            total_queued += len(chunk)
            logger.info(
                "Meilisearch bulk_index: отправлено %d/%d документов",
                total_queued,
                len(channels),
            )

        return total_queued

    async def delete_channel(self, channel_id: int) -> None:
        """Удаляет канал из поискового индекса по первичному ключу."""
        task = await self._request(
            "DELETE",
            f"/indexes/{CHANNELS_INDEX}/documents/{channel_id}",
        )
        await self._wait_task(task["taskUid"])

    # ------------------------------------------------------------------
    # Поиск
    # ------------------------------------------------------------------

    async def search(
        self,
        query: str = "",
        *,
        filters: Optional[str] = None,
        sort: Optional[List[str]] = None,
        limit: int = 20,
        offset: int = 0,
        facets: Optional[List[str]] = None,
    ) -> SearchResult:
        """
        Полнотекстовый поиск каналов.

        Параметры:
            query   — поисковый запрос (пустая строка = все документы)
            filters — строка фильтра Meilisearch, например:
                      "category = 'crypto' AND member_count >= 5000"
            sort    — список полей сортировки: ["member_count:desc"]
            limit   — лимит результатов (макс 1000)
            offset  — смещение для пагинации
            facets  — список атрибутов для фасетного подсчёта

        Возвращает SearchResult с hits, total, processing_time_ms, facets.
        """
        body: dict[str, Any] = {
            "q": query,
            "limit": min(limit, 1000),
            "offset": offset,
        }
        if filters:
            body["filter"] = filters
        if sort:
            body["sort"] = sort
        if facets:
            body["facets"] = facets

        result = await self._request(
            "POST",
            f"/indexes/{CHANNELS_INDEX}/search",
            json=body,
        )
        return SearchResult(
            hits=result.get("hits", []),
            total=result.get("estimatedTotalHits", len(result.get("hits", []))),
            processing_time_ms=result.get("processingTimeMs", 0),
            facets=result.get("facetDistribution"),
        )

    async def geo_search(
        self,
        query: str = "",
        *,
        lat: float,
        lng: float,
        radius_km: float = 500.0,
        filters: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> SearchResult:
        """
        Поиск каналов в радиусе от географической точки.

        Параметры:
            lat, lng    — координаты центра поиска
            radius_km   — радиус поиска в километрах
            filters     — дополнительный фильтр Meilisearch (AND-объединяется с geo)
            limit       — лимит результатов

        Geo-фильтр использует встроенный _geoRadius Meilisearch.
        """
        radius_m = int(radius_km * 1000)
        geo_filter = f"_geoRadius({lat}, {lng}, {radius_m})"
        combined_filter = (
            f"({filters}) AND {geo_filter}" if filters else geo_filter
        )

        body: dict[str, Any] = {
            "q": query,
            "filter": combined_filter,
            "limit": min(limit, 1000),
            "offset": offset,
            # Сортируем ближайшие каналы первыми
            "sort": [f"_geoPoint({lat}, {lng}):asc"],
        }

        result = await self._request(
            "POST",
            f"/indexes/{CHANNELS_INDEX}/search",
            json=body,
        )
        return SearchResult(
            hits=result.get("hits", []),
            total=result.get("estimatedTotalHits", len(result.get("hits", []))),
            processing_time_ms=result.get("processingTimeMs", 0),
        )

    # ------------------------------------------------------------------
    # Фасеты
    # ------------------------------------------------------------------

    async def get_facets(self, attribute: str) -> dict[str, int]:
        """
        Возвращает распределение значений по атрибуту-фасету.

        Например: get_facets("category") -> {"crypto": 120, "tech": 95, ...}
        """
        result = await self._request(
            "POST",
            f"/indexes/{CHANNELS_INDEX}/search",
            json={
                "q": "",
                "limit": 0,
                "facets": [attribute],
            },
        )
        distribution = result.get("facetDistribution", {})
        return distribution.get(attribute, {})

    # ------------------------------------------------------------------
    # Утилиты
    # ------------------------------------------------------------------

    async def get_index_stats(self) -> dict:
        """Возвращает статистику индекса: число документов, статус обновления."""
        return await self._request("GET", f"/indexes/{CHANNELS_INDEX}/stats")

    async def health(self) -> bool:
        """Проверяет доступность Meilisearch. Возвращает True если ОК."""
        try:
            result = await self._request("GET", "/health")
            return result.get("status") == "available"
        except Exception as exc:
            logger.warning("Meilisearch health check failed: %s", exc)
            return False


# ---------------------------------------------------------------------------
# Хелпер: конвертация ORM-объекта в документ Meilisearch
# ---------------------------------------------------------------------------

def channel_to_doc(channel: Any) -> dict:
    """
    Конвертирует ORM-объект ChannelMapEntry (или dict) в документ для Meilisearch.

    Поле _geo используется для geo-поиска — зарезервированное поле Meilisearch.
    """
    # Поддерживаем как ORM-объекты, так и plain-dict
    def _get(obj: Any, attr: str) -> Any:
        if isinstance(obj, dict):
            return obj.get(attr)
        return getattr(obj, attr, None)

    created_at = _get(channel, "created_at")
    created_at_iso = created_at.isoformat() if created_at else None

    doc: dict[str, Any] = {
        "id": _get(channel, "id"),
        "title": _get(channel, "title"),
        "username": _get(channel, "username"),
        "description": _get(channel, "description"),
        "category": _get(channel, "category"),
        "language": _get(channel, "language"),
        "region": _get(channel, "region"),
        "member_count": _get(channel, "member_count") or 0,
        "engagement_rate": _get(channel, "engagement_rate"),
        "topic_tags": _get(channel, "topic_tags") or [],
        "spam_score": _get(channel, "spam_score"),
        "has_comments": bool(_get(channel, "has_comments")),
        "created_at": created_at_iso,
    }

    # Geo-поле: только если обе координаты заданы
    lat = _get(channel, "lat")
    lng = _get(channel, "lng")
    if lat is not None and lng is not None:
        doc["_geo"] = {"lat": float(lat), "lng": float(lng)}

    return doc


# ---------------------------------------------------------------------------
# Синглтон для использования в FastAPI через Depends
# ---------------------------------------------------------------------------

_search_service: Optional[MeiliSearchService] = None


def get_search_service() -> MeiliSearchService:
    """
    FastAPI dependency / фабрика синглтона.

    Использование в эндпоинте:
        svc: MeiliSearchService = Depends(get_search_service)
    """
    global _search_service
    if _search_service is None:
        _search_service = MeiliSearchService()
    return _search_service
