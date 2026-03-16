"""
NEURO COMMENTING — Embedding Service (Pinecone + multilingual-e5-large).

Назначение: хранить AI-контекст в Pinecone для anti-hallucination retrieval.
Вместо того чтобы LLM «вспоминал» факты из training data, мы подаём ему
реальные данные из векторной БД через get_enriched_ai_context().

Индексы:
  nc-business-context  — brief + assets per workspace (1024 dims, cosine)
  nc-channel-knowledge — channel title + description + topics (1024 dims, cosine)
  nc-comment-patterns  — успешные комментарии (score > 0.8)  (1024 dims, cosine)
  nc-competitor-intel  — research transcripts / конкурентная разведка

ENV:
  PINECONE_API_KEY          — обязательный
  PINECONE_ENVIRONMENT      — опциональный (для serverless: gcp-starter и т.п.)
  PINECONE_EMBED_MODEL      — модель для Inference API (default: multilingual-e5-large)

Зависимости (уже должны быть в venv):
  pinecone-client >= 3.0
  aiohttp
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

import aiohttp

from config import settings

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Константы
# ---------------------------------------------------------------------------

# Размерность эмбеддингов multilingual-e5-large
EMBED_DIM = 1024

# Метрика сходства
EMBED_METRIC = "cosine"

# Имена индексов
INDEX_BUSINESS = "nc-business-context"
INDEX_CHANNELS = "nc-channel-knowledge"
INDEX_COMMENTS = "nc-comment-patterns"
INDEX_COMPETITOR = "nc-competitor-intel"

ALL_INDEXES = [INDEX_BUSINESS, INDEX_CHANNELS, INDEX_COMMENTS, INDEX_COMPETITOR]

# Порог score для сохранения комментария
COMMENT_MIN_SCORE = 0.8

# Параметры retry / rate-limit backoff
_RETRY_MAX = 5
_RETRY_BASE_DELAY = 1.0   # секунд
_RETRY_MAX_DELAY = 32.0   # секунд

# Максимум векторов в одном upsert-батче (лимит Pinecone — 100)
UPSERT_BATCH_SIZE = 100


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------

def _stable_id(*parts: Any) -> str:
    """Детерминированный ID для вектора на основе входных частей."""
    raw = "|".join(str(p) for p in parts)
    return hashlib.sha256(raw.encode()).hexdigest()[:40]


def _truncate(text: str, max_chars: int = 4000) -> str:
    """Обрезать текст до max_chars, чтобы не превысить лимит токенов."""
    if not text:
        return ""
    return text[:max_chars]


async def _retry_request(
    coro_factory,
    *,
    operation: str = "Pinecone request",
) -> Any:
    """
    Выполнить корутину с exponential backoff при ошибках 429/5xx.

    coro_factory — callable без аргументов, возвращающий корутину.
    Повторяет до _RETRY_MAX раз.
    """
    delay = _RETRY_BASE_DELAY
    for attempt in range(1, _RETRY_MAX + 1):
        try:
            return await coro_factory()
        except aiohttp.ClientResponseError as exc:
            if exc.status == 429 or exc.status >= 500:
                if attempt == _RETRY_MAX:
                    log.error("[embedding] %s: исчерпаны попытки (%d). Ошибка: %s",
                              operation, _RETRY_MAX, exc)
                    raise
                wait = min(delay, _RETRY_MAX_DELAY)
                log.warning("[embedding] %s: статус %d, retry %d/%d через %.1fs",
                            operation, exc.status, attempt, _RETRY_MAX, wait)
                await asyncio.sleep(wait)
                delay = min(delay * 2.0, _RETRY_MAX_DELAY)
            else:
                # Не 429/5xx — не ретраить
                raise
        except (aiohttp.ClientConnectionError, asyncio.TimeoutError) as exc:
            if attempt == _RETRY_MAX:
                log.error("[embedding] %s: исчерпаны попытки. Ошибка: %s", operation, exc)
                raise
            wait = min(delay, _RETRY_MAX_DELAY)
            log.warning("[embedding] %s: connection error, retry %d/%d через %.1fs: %s",
                        operation, attempt, _RETRY_MAX, wait, exc)
            await asyncio.sleep(wait)
            delay = min(delay * 2.0, _RETRY_MAX_DELAY)
    # Никогда не достигается, но для mypy
    raise RuntimeError(f"[embedding] {operation}: не удалось выполнить после {_RETRY_MAX} попыток")


# ---------------------------------------------------------------------------
# Основной класс
# ---------------------------------------------------------------------------

class EmbeddingService:
    """
    Сервис векторных эмбеддингов для anti-hallucination pipeline.

    Использует Pinecone Inference API (multilingual-e5-large) для создания
    эмбеддингов и Pinecone Data Plane API для upsert/query.

    Инициализация ленивая — соединения создаются при первом вызове.
    """

    def __init__(self) -> None:
        # Ключ и окружение берутся из settings / env
        self._api_key: str = getattr(settings, "PINECONE_API_KEY", "") or ""
        self._environment: str = getattr(settings, "PINECONE_ENVIRONMENT", "") or ""
        self._embed_model: str = (
            getattr(settings, "PINECONE_EMBED_MODEL", "") or "multilingual-e5-large"
        )

        # Базовые URL Pinecone REST API
        self._controller_url = "https://api.pinecone.io"

        # Кэш хостов индексов { index_name: host_url }
        self._index_hosts: Dict[str, str] = {}

        # Общий aiohttp.ClientSession (lazy)
        self._session: Optional[aiohttp.ClientSession] = None

    # ------------------------------------------------------------------
    # Управление сессией
    # ------------------------------------------------------------------

    def _get_session(self) -> aiohttp.ClientSession:
        """Получить или создать aiohttp.ClientSession."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={
                    "Api-Key": self._api_key,
                    "Content-Type": "application/json",
                    "X-Pinecone-API-Version": "2024-07",
                },
                timeout=aiohttp.ClientTimeout(total=30),
            )
        return self._session

    async def close(self) -> None:
        """Закрыть aiohttp-сессию при завершении работы."""
        if self._session and not self._session.closed:
            await self._session.close()

    # ------------------------------------------------------------------
    # Управление индексами
    # ------------------------------------------------------------------

    async def _get_or_create_index(self, index_name: str) -> str:
        """
        Вернуть host URL для индекса.
        Если индекс не существует — создать serverless (gcp-starter / aws us-east-1).
        Результат кэшируется в self._index_hosts.
        """
        if index_name in self._index_hosts:
            return self._index_hosts[index_name]

        session = self._get_session()

        # Попробуем получить описание существующего индекса
        async def _describe() -> Optional[str]:
            async with session.get(
                f"{self._controller_url}/indexes/{index_name}"
            ) as resp:
                if resp.status == 404:
                    return None
                resp.raise_for_status()
                data = await resp.json()
                return data.get("host") or ""

        try:
            host = await _retry_request(_describe, operation=f"describe_index({index_name})")
        except aiohttp.ClientResponseError as exc:
            if exc.status == 404:
                host = None
            else:
                raise

        if host is None:
            # Индекс не существует — создаём
            log.info("[embedding] Создаём индекс %s (dim=%d, metric=%s)",
                     index_name, EMBED_DIM, EMBED_METRIC)

            # Определяем spec: serverless (предпочтительно) или pod
            spec: Dict[str, Any]
            if self._environment:
                # Serverless: environment задаёт cloud+region через env-строку вида "aws-us-east-1"
                parts = self._environment.split("-", 1)
                cloud = parts[0] if len(parts) >= 1 else "aws"
                region = parts[1] if len(parts) == 2 else "us-east-1"
                spec = {
                    "serverless": {
                        "cloud": cloud,
                        "region": region,
                    }
                }
            else:
                # Дефолт: serverless на GCP starter
                spec = {
                    "serverless": {
                        "cloud": "gcp",
                        "region": "us-central1",
                    }
                }

            payload = {
                "name": index_name,
                "dimension": EMBED_DIM,
                "metric": EMBED_METRIC,
                "spec": spec,
            }

            async def _create() -> str:
                async with session.post(
                    f"{self._controller_url}/indexes",
                    data=json.dumps(payload),
                ) as resp:
                    resp.raise_for_status()
                    data = await resp.json()
                    return data.get("host") or ""

            host = await _retry_request(_create, operation=f"create_index({index_name})")
            log.info("[embedding] Индекс %s создан: %s", index_name, host)

            # Ждём готовности индекса (до 60 секунд)
            await self._wait_index_ready(index_name, timeout_sec=60)

            # Перечитываем host после ready
            host = await _retry_request(
                lambda: _describe(),  # type: ignore[return-value]
                operation=f"describe_index_after_create({index_name})",
            ) or host

        self._index_hosts[index_name] = f"https://{host}" if not host.startswith("http") else host
        return self._index_hosts[index_name]

    async def _wait_index_ready(self, index_name: str, timeout_sec: int = 60) -> None:
        """Ждём пока индекс перейдёт в статус Ready."""
        session = self._get_session()
        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            async with session.get(
                f"{self._controller_url}/indexes/{index_name}"
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    status = (data.get("status") or {}).get("state", "")
                    if status == "Ready":
                        return
            await asyncio.sleep(2)
        log.warning("[embedding] Индекс %s не стал Ready за %ds", index_name, timeout_sec)

    async def ensure_indexes(self) -> None:
        """
        Убедиться что все 4 индекса существуют.
        Вызывать при старте приложения (опционально).
        """
        if not self._api_key:
            log.warning("[embedding] PINECONE_API_KEY не задан — пропускаем ensure_indexes")
            return
        for idx in ALL_INDEXES:
            try:
                await self._get_or_create_index(idx)
            except Exception as exc:
                log.error("[embedding] ensure_indexes: ошибка для %s: %s", idx, exc)

    # ------------------------------------------------------------------
    # Embed (Pinecone Inference API)
    # ------------------------------------------------------------------

    async def embed_text(self, text: str) -> List[float]:
        """
        Создать эмбеддинг для одного текста через Pinecone Inference API.

        Возвращает список из EMBED_DIM=1024 float.
        При ошибке возвращает пустой список (fallback — не уронить pipeline).
        """
        if not self._api_key:
            log.debug("[embedding] PINECONE_API_KEY не задан, возвращаем нулевой вектор")
            return [0.0] * EMBED_DIM

        if not text or not text.strip():
            return [0.0] * EMBED_DIM

        session = self._get_session()
        payload = {
            "model": self._embed_model,
            "inputs": [{"text": _truncate(text)}],
            "parameters": {
                "input_type": "passage",
                "truncate": "END",
            },
        }

        async def _call() -> List[float]:
            async with session.post(
                f"{self._controller_url}/embed",
                data=json.dumps(payload),
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
                # Ответ: {"model": ..., "data": [{"values": [...]}], ...}
                items = data.get("data") or []
                if items:
                    return items[0].get("values") or [0.0] * EMBED_DIM
                return [0.0] * EMBED_DIM

        try:
            return await _retry_request(_call, operation="embed_text")
        except Exception as exc:
            log.error("[embedding] embed_text: ошибка: %s", exc)
            return [0.0] * EMBED_DIM

    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """
        Эмбеддинг батча текстов одним запросом к Pinecone Inference API.

        Pinecone принимает до 100 inputs в одном запросе.
        Если тексты пустые — возвращаем нулевые векторы.
        """
        if not self._api_key or not texts:
            return [[0.0] * EMBED_DIM for _ in texts]

        session = self._get_session()
        inputs = [{"text": _truncate(t or "")} for t in texts]
        payload = {
            "model": self._embed_model,
            "inputs": inputs,
            "parameters": {
                "input_type": "passage",
                "truncate": "END",
            },
        }

        async def _call() -> List[List[float]]:
            async with session.post(
                f"{self._controller_url}/embed",
                data=json.dumps(payload),
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
                items = data.get("data") or []
                result = [item.get("values") or [0.0] * EMBED_DIM for item in items]
                # Дополняем нулями если ответ короче
                while len(result) < len(texts):
                    result.append([0.0] * EMBED_DIM)
                return result

        try:
            return await _retry_request(_call, operation="embed_batch")
        except Exception as exc:
            log.error("[embedding] embed_batch: ошибка: %s", exc)
            return [[0.0] * EMBED_DIM for _ in texts]

    # ------------------------------------------------------------------
    # Upsert helpers
    # ------------------------------------------------------------------

    async def _upsert_vectors(
        self,
        index_name: str,
        vectors: List[Dict[str, Any]],
        namespace: str = "",
    ) -> None:
        """
        Сохранить векторы в Pinecone Data Plane.

        vectors — список dict { id, values, metadata }.
        Автоматически разбивает на батчи по UPSERT_BATCH_SIZE.
        """
        if not vectors:
            return
        if not self._api_key:
            log.debug("[embedding] PINECONE_API_KEY не задан — пропускаем upsert в %s", index_name)
            return

        host = await self._get_or_create_index(index_name)
        session = self._get_session()

        # Разбиваем на батчи
        batches = [vectors[i:i + UPSERT_BATCH_SIZE]
                   for i in range(0, len(vectors), UPSERT_BATCH_SIZE)]

        for batch in batches:
            payload: Dict[str, Any] = {"vectors": batch}
            if namespace:
                payload["namespace"] = namespace

            async def _call(b=batch, p=payload) -> None:
                async with session.post(
                    f"{host}/vectors/upsert",
                    data=json.dumps(p),
                ) as resp:
                    resp.raise_for_status()

            try:
                await _retry_request(_call, operation=f"upsert({index_name})")
            except Exception as exc:
                log.error("[embedding] _upsert_vectors(%s): ошибка батча: %s", index_name, exc)

    # ------------------------------------------------------------------
    # Публичные upsert-методы
    # ------------------------------------------------------------------

    async def upsert_brief(self, workspace_id: int, brief: Dict[str, Any]) -> None:
        """
        Разбить brief на смысловые чанки, создать эмбеддинги и записать
        в индекс nc-business-context с namespace = workspace_{workspace_id}.

        brief — dict с полями BusinessBrief:
          product_name, offer_summary, target_audience, competitors,
          tone_of_voice, pain_points, telegram_goals, website_url, ...
        """
        # Формируем чанки: каждое смысловое поле — отдельный вектор
        chunks: List[Tuple[str, str, Dict[str, Any]]] = []  # (chunk_id, text, metadata)

        tenant_id = brief.get("tenant_id", 0)
        brief_id = brief.get("id", 0)
        product_name = brief.get("product_name") or ""

        # Поля для индексации
        field_map = {
            "product_offer": (
                f"Продукт: {product_name}. "
                f"Оффер: {brief.get('offer_summary') or ''}"
            ),
            "target_audience": (
                f"Целевая аудитория: {brief.get('target_audience') or ''}"
            ),
            "tone_pain": (
                f"Тон: {brief.get('tone_of_voice') or ''}. "
                f"Боли клиентов: {_json_to_text(brief.get('pain_points'))}"
            ),
            "goals_competitors": (
                f"Цели в Telegram: {_json_to_text(brief.get('telegram_goals'))}. "
                f"Конкуренты: {_json_to_text(brief.get('competitors'))}"
            ),
            "urls": (
                f"Сайт: {brief.get('website_url') or ''}. "
                f"Канал: {brief.get('channel_url') or ''}. "
                f"Бот: {brief.get('bot_url') or ''}"
            ),
        }

        for field_key, text in field_map.items():
            if not text.strip().replace(":", "").replace(".", "").strip():
                continue
            chunk_id = _stable_id("brief", brief_id, workspace_id, field_key)
            meta = {
                "workspace_id": workspace_id,
                "tenant_id": tenant_id,
                "brief_id": brief_id,
                "field": field_key,
                "product_name": product_name,
                "text": _truncate(text, 1000),
            }
            chunks.append((chunk_id, text, meta))

        if not chunks:
            log.debug("[embedding] upsert_brief: нет данных для workspace=%d", workspace_id)
            return

        # Получаем эмбеддинги батчем
        texts = [c[1] for c in chunks]
        embeddings = await self.embed_batch(texts)

        vectors = [
            {"id": cid, "values": emb, "metadata": meta}
            for (cid, _text, meta), emb in zip(chunks, embeddings)
        ]

        namespace = f"workspace_{workspace_id}"
        await self._upsert_vectors(INDEX_BUSINESS, vectors, namespace=namespace)
        log.info("[embedding] upsert_brief: workspace=%d, %d чанков сохранено",
                 workspace_id, len(vectors))

    async def upsert_channel(self, channel: Dict[str, Any]) -> None:
        """
        Создать эмбеддинг для канала и записать в nc-channel-knowledge.

        channel — dict с полями ChannelMapEntry:
          id, title, description, category, subcategory, language,
          region, topic_tags, member_count, spam_score, username, ...
        """
        channel_id = channel.get("id", 0)
        title = channel.get("title") or ""
        description = channel.get("description") or ""
        category = channel.get("category") or ""
        subcategory = channel.get("subcategory") or ""
        topic_tags = _json_to_text(channel.get("topic_tags"))
        language = channel.get("language") or ""
        region = channel.get("region") or ""

        # Единый текст для индексации
        text = " | ".join(filter(None, [
            title,
            description[:500] if description else "",
            f"Категория: {category}",
            f"Подкатегория: {subcategory}" if subcategory else "",
            f"Теги: {topic_tags}" if topic_tags else "",
            f"Язык: {language}",
            f"Регион: {region}",
        ]))

        if not text.strip():
            return

        vector_id = _stable_id("channel", channel_id)
        embedding = await self.embed_text(text)

        meta = {
            "channel_id": channel_id,
            "username": channel.get("username") or "",
            "title": title[:200],
            "category": category,
            "subcategory": subcategory,
            "language": language,
            "region": region,
            "member_count": channel.get("member_count") or 0,
            "spam_score": channel.get("spam_score") or 0.0,
            "topic_tags": topic_tags[:500] if topic_tags else "",
            "text": _truncate(text, 1000),
        }

        await self._upsert_vectors(INDEX_CHANNELS, [{"id": vector_id, "values": embedding, "metadata": meta}])
        log.debug("[embedding] upsert_channel: id=%d title=%r", channel_id, title[:60])

    async def upsert_comment(
        self,
        comment_text: str,
        score: float,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Сохранить успешный комментарий в nc-comment-patterns.

        Комментарий сохраняется только если score >= COMMENT_MIN_SCORE (0.8).
        metadata — любые поля для фильтрации: workspace_id, tenant_id, style, channel_id.
        """
        if score < COMMENT_MIN_SCORE:
            log.debug("[embedding] upsert_comment: score=%.2f < %.2f, пропуск", score, COMMENT_MIN_SCORE)
            return

        if not comment_text or not comment_text.strip():
            return

        meta = dict(metadata or {})
        meta["score"] = score
        meta["text"] = _truncate(comment_text, 500)

        vector_id = _stable_id("comment", comment_text[:100], score, meta.get("channel_id", ""))
        embedding = await self.embed_text(comment_text)

        await self._upsert_vectors(
            INDEX_COMMENTS,
            [{"id": vector_id, "values": embedding, "metadata": meta}],
        )
        log.debug("[embedding] upsert_comment: score=%.2f, text=%r", score, comment_text[:60])

    async def upsert_competitor_intel(
        self,
        doc_id: str,
        text: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Сохранить фрагмент конкурентной разведки в nc-competitor-intel.

        doc_id — уникальный идентификатор документа (например, имя файла + chunk_index).
        text   — текстовый фрагмент (транскрипт, заметка, анализ).
        """
        if not text or not text.strip():
            return

        meta = dict(metadata or {})
        meta["doc_id"] = doc_id
        meta["text"] = _truncate(text, 1000)

        vector_id = _stable_id("competitor", doc_id)
        embedding = await self.embed_text(text)

        await self._upsert_vectors(
            INDEX_COMPETITOR,
            [{"id": vector_id, "values": embedding, "metadata": meta}],
        )
        log.debug("[embedding] upsert_competitor_intel: doc_id=%r", doc_id)

    # ------------------------------------------------------------------
    # Semantic search
    # ------------------------------------------------------------------

    async def search_context(
        self,
        query: str,
        workspace_id: int,
        *,
        index: str = INDEX_BUSINESS,
        top_k: int = 5,
        namespace: Optional[str] = None,
        filter_meta: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Семантический поиск в заданном индексе.

        Возвращает список dict:
          { id, score, text, metadata }

        workspace_id используется для namespace-изоляции бизнес-контекста.
        """
        if not self._api_key:
            return []

        if not query or not query.strip():
            return []

        # Для бизнес-индекса автоматически ставим namespace по workspace
        if namespace is None and index == INDEX_BUSINESS:
            namespace = f"workspace_{workspace_id}"

        # Эмбеддинг запроса (input_type=query для E5)
        session = self._get_session()
        query_payload = {
            "model": self._embed_model,
            "inputs": [{"text": _truncate(query, 500)}],
            "parameters": {
                "input_type": "query",
                "truncate": "END",
            },
        }

        async def _embed_query() -> List[float]:
            async with session.post(
                f"{self._controller_url}/embed",
                data=json.dumps(query_payload),
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
                items = data.get("data") or []
                if items:
                    return items[0].get("values") or [0.0] * EMBED_DIM
                return [0.0] * EMBED_DIM

        try:
            query_vector = await _retry_request(_embed_query, operation="embed_query")
        except Exception as exc:
            log.error("[embedding] search_context: ошибка эмбеддинга запроса: %s", exc)
            return []

        host = await self._get_or_create_index(index)

        search_payload: Dict[str, Any] = {
            "vector": query_vector,
            "topK": top_k,
            "includeMetadata": True,
            "includeValues": False,
        }
        if namespace:
            search_payload["namespace"] = namespace
        if filter_meta:
            search_payload["filter"] = filter_meta

        async def _query() -> List[Dict[str, Any]]:
            async with session.post(
                f"{host}/query",
                data=json.dumps(search_payload),
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
                matches = data.get("matches") or []
                results = []
                for m in matches:
                    meta = m.get("metadata") or {}
                    results.append({
                        "id": m.get("id", ""),
                        "score": m.get("score", 0.0),
                        "text": meta.get("text", ""),
                        "metadata": meta,
                    })
                return results

        try:
            return await _retry_request(_query, operation=f"query({index})")
        except Exception as exc:
            log.error("[embedding] search_context(%s): ошибка: %s", index, exc)
            return []

    # ------------------------------------------------------------------
    # Anti-hallucination pipeline
    # ------------------------------------------------------------------

    async def get_enriched_ai_context(
        self,
        query: str,
        workspace_id: int,
    ) -> Dict[str, Any]:
        """
        Извлечь фактический контекст из Pinecone для anti-hallucination retrieval.

        Вместо того чтобы LLM «вспоминал» данные из training data,
        мы подаём ему реальные факты из векторной БД.

        Возвращает структурированный контекст:
        {
            "business_facts": ["..."],       # из nc-business-context
            "relevant_channels": ["..."],    # из nc-channel-knowledge
            "comment_patterns": ["..."],     # из nc-comment-patterns
            "sources": [...]                 # все raw matches для аудита
        }
        """
        # Параллельно запрашиваем все 3 индекса
        business_task = asyncio.create_task(
            self.search_context(query, workspace_id, index=INDEX_BUSINESS, top_k=3)
        )
        channel_task = asyncio.create_task(
            self.search_context(query, workspace_id, index=INDEX_CHANNELS, top_k=3)
        )
        pattern_task = asyncio.create_task(
            self.search_context(query, workspace_id, index=INDEX_COMMENTS, top_k=2)
        )

        business_ctx, channel_ctx, pattern_ctx = await asyncio.gather(
            business_task, channel_task, pattern_task, return_exceptions=True
        )

        # Безопасная нормализация — если задача вернула исключение
        def _safe(result: Any) -> List[Dict[str, Any]]:
            if isinstance(result, Exception):
                log.error("[embedding] get_enriched_ai_context: ошибка задачи: %s", result)
                return []
            return result or []

        business_ctx = _safe(business_ctx)
        channel_ctx = _safe(channel_ctx)
        pattern_ctx = _safe(pattern_ctx)

        return {
            "business_facts": [r["text"] for r in business_ctx if r.get("text")],
            "relevant_channels": [r["text"] for r in channel_ctx if r.get("text")],
            "comment_patterns": [r["text"] for r in pattern_ctx if r.get("text")],
            "sources": business_ctx + channel_ctx + pattern_ctx,
        }

    # ------------------------------------------------------------------
    # Синхронизация существующих данных (batch sync)
    # ------------------------------------------------------------------

    async def sync_briefs_to_pinecone(
        self,
        db_session: Any,
        *,
        batch_size: int = 50,
    ) -> int:
        """
        Батчевая синхронизация всех существующих BusinessBrief из PostgreSQL в Pinecone.

        db_session — AsyncSession SQLAlchemy.
        Возвращает количество обработанных бриефов.
        """
        from sqlalchemy import select as sa_select
        from storage.models import BusinessBrief

        log.info("[embedding] sync_briefs_to_pinecone: начало")
        offset = 0
        total = 0

        while True:
            # Загружаем батч бриефов
            stmt = (
                sa_select(BusinessBrief)
                .where(BusinessBrief.status.in_(["confirmed", "draft"]))
                .order_by(BusinessBrief.id)
                .limit(batch_size)
                .offset(offset)
            )
            result = await db_session.execute(stmt)
            briefs = result.scalars().all()

            if not briefs:
                break

            for brief in briefs:
                brief_dict = {
                    "id": brief.id,
                    "tenant_id": brief.tenant_id,
                    "workspace_id": brief.workspace_id,
                    "product_name": brief.product_name,
                    "offer_summary": brief.offer_summary,
                    "target_audience": brief.target_audience,
                    "competitors": brief.competitors,
                    "tone_of_voice": brief.tone_of_voice,
                    "pain_points": brief.pain_points,
                    "telegram_goals": brief.telegram_goals,
                    "website_url": brief.website_url,
                    "channel_url": brief.channel_url,
                    "bot_url": brief.bot_url,
                }
                try:
                    await self.upsert_brief(brief.workspace_id, brief_dict)
                    total += 1
                except Exception as exc:
                    log.error("[embedding] sync_briefs: ошибка brief.id=%d: %s", brief.id, exc)

            offset += batch_size
            # Небольшая пауза чтобы не перегрузить Pinecone rate limits
            await asyncio.sleep(0.1)

        log.info("[embedding] sync_briefs_to_pinecone: завершено, %d бриефов обработано", total)
        return total

    async def sync_channels_to_pinecone(
        self,
        db_session: Any,
        *,
        batch_size: int = 100,
        only_with_description: bool = True,
    ) -> int:
        """
        Батчевая синхронизация всех ChannelMapEntry из PostgreSQL в Pinecone.

        db_session — AsyncSession SQLAlchemy.
        only_with_description — пропускать каналы без описания (рекомендуется).
        Возвращает количество обработанных каналов.
        """
        from sqlalchemy import select as sa_select
        from storage.models import ChannelMapEntry

        log.info("[embedding] sync_channels_to_pinecone: начало")
        offset = 0
        total = 0

        while True:
            stmt = (
                sa_select(ChannelMapEntry)
                .order_by(ChannelMapEntry.id)
                .limit(batch_size)
                .offset(offset)
            )
            if only_with_description:
                stmt = stmt.where(ChannelMapEntry.description.isnot(None))

            result = await db_session.execute(stmt)
            channels = result.scalars().all()

            if not channels:
                break

            # Батчевая обработка для скорости
            channel_dicts = [
                {
                    "id": ch.id,
                    "username": ch.username,
                    "title": ch.title,
                    "description": ch.description,
                    "category": ch.category,
                    "subcategory": ch.subcategory,
                    "language": ch.language,
                    "region": ch.region,
                    "member_count": ch.member_count,
                    "spam_score": ch.spam_score,
                    "topic_tags": ch.topic_tags,
                }
                for ch in channels
            ]

            # Собираем тексты для батчевого embed
            texts = [
                " | ".join(filter(None, [
                    ch.get("title") or "",
                    (ch.get("description") or "")[:500],
                    f"Категория: {ch.get('category') or ''}",
                    f"Теги: {_json_to_text(ch.get('topic_tags'))}" if ch.get("topic_tags") else "",
                ]))
                for ch in channel_dicts
            ]

            embeddings = await self.embed_batch(texts)

            vectors = []
            for ch, text, emb in zip(channel_dicts, texts, embeddings):
                if not text.strip():
                    continue
                vector_id = _stable_id("channel", ch["id"])
                meta = {
                    "channel_id": ch["id"],
                    "username": ch.get("username") or "",
                    "title": (ch.get("title") or "")[:200],
                    "category": ch.get("category") or "",
                    "language": ch.get("language") or "",
                    "region": ch.get("region") or "",
                    "member_count": ch.get("member_count") or 0,
                    "spam_score": ch.get("spam_score") or 0.0,
                    "text": _truncate(text, 1000),
                }
                vectors.append({"id": vector_id, "values": emb, "metadata": meta})

            if vectors:
                await self._upsert_vectors(INDEX_CHANNELS, vectors)
                total += len(vectors)

            offset += batch_size
            await asyncio.sleep(0.05)  # мягкий rate-limit

        log.info("[embedding] sync_channels_to_pinecone: завершено, %d каналов обработано", total)
        return total


# ---------------------------------------------------------------------------
# Вспомогательные функции модуля
# ---------------------------------------------------------------------------

def _json_to_text(value: Any) -> str:
    """
    Конвертировать JSON-поле модели (list или dict) в читаемый текст.

    Используется при формировании текста для эмбеддинга.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return ", ".join(str(v) for v in value if v)
    if isinstance(value, dict):
        return "; ".join(f"{k}: {v}" for k, v in value.items() if v)
    return str(value)


# ---------------------------------------------------------------------------
# Singleton (lazy init)
# ---------------------------------------------------------------------------

_service_instance: Optional[EmbeddingService] = None


def get_embedding_service() -> EmbeddingService:
    """
    Получить singleton экземпляр EmbeddingService.

    Используется через dependency injection в FastAPI или напрямую в core-модулях.
    """
    global _service_instance
    if _service_instance is None:
        _service_instance = EmbeddingService()
    return _service_instance
