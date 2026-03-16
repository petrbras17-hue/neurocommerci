"""
YouTube Competitor Intelligence Service — Supadata API + AI Analysis.

Workflow:
  YouTube URL -> Supadata transcript -> AI structured analysis -> CompetitorInsight

Primary target: GramGPT.io YouTube channel and similar Telegram SaaS competitors.

Env vars required:
  SUPADATA_API_KEY — Supadata API key (https://api.supadata.ai)
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, List, Optional
from urllib.parse import urlparse, parse_qs

import aiohttp

from config import settings
from utils.logger import log


# ---------------------------------------------------------------------------
# Supadata API constants
# ---------------------------------------------------------------------------

_SUPADATA_BASE = "https://api.supadata.ai/v1"
_SUPADATA_TRANSCRIPT_ENDPOINT = f"{_SUPADATA_BASE}/youtube/transcript"

# Задержка между запросами при пакетной обработке (секунды)
_BATCH_REQUEST_DELAY_SEC = 3.0

# Максимальный размер транскрипта для передачи в AI (символы)
_MAX_TRANSCRIPT_CHARS = 12_000

# Таймаут для HTTP-запросов к Supadata (секунды)
_HTTP_TIMEOUT_SEC = 60


# ---------------------------------------------------------------------------
# CompetitorInsight — структурированный результат анализа видео
# ---------------------------------------------------------------------------

@dataclass
class CompetitorInsight:
    """Структурированный результат анализа одного видео конкурента."""

    video_url: str
    video_title: str
    competitor_name: str
    key_points: List[str] = field(default_factory=list)
    features_mentioned: List[str] = field(default_factory=list)
    pricing_info: Optional[str] = None
    weaknesses: List[str] = field(default_factory=list)
    marketing_tactics: List[str] = field(default_factory=list)
    seo_keywords: List[str] = field(default_factory=list)
    threat_level: str = "medium"          # low | medium | high
    transcript_length: int = 0
    analyzed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    raw_ai_response: Optional[str] = None  # для отладки и хранения

    def to_dict(self) -> dict[str, Any]:
        return {
            "video_url": self.video_url,
            "video_title": self.video_title,
            "competitor_name": self.competitor_name,
            "key_points": self.key_points,
            "features_mentioned": self.features_mentioned,
            "pricing_info": self.pricing_info,
            "weaknesses": self.weaknesses,
            "marketing_tactics": self.marketing_tactics,
            "seo_keywords": self.seo_keywords,
            "threat_level": self.threat_level,
            "transcript_length": self.transcript_length,
            "analyzed_at": self.analyzed_at.isoformat(),
        }


# ---------------------------------------------------------------------------
# Ошибки сервиса
# ---------------------------------------------------------------------------

class YouTubeIntelError(Exception):
    """Базовый класс ошибок YouTubeIntelService."""


class VideoNotFoundError(YouTubeIntelError):
    """Видео не найдено или недоступно."""


class NoCaptionsError(YouTubeIntelError):
    """У видео нет субтитров / транскрипта."""


class SupadataRateLimitError(YouTubeIntelError):
    """Превышен лимит запросов Supadata API."""


class SupadataAuthError(YouTubeIntelError):
    """Неверный или отсутствующий SUPADATA_API_KEY."""


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------

def _extract_video_id(url: str) -> Optional[str]:
    """Извлечь video_id из различных форматов YouTube URL.

    Поддерживаемые форматы:
      - https://www.youtube.com/watch?v=VIDEO_ID
      - https://youtu.be/VIDEO_ID
      - https://www.youtube.com/shorts/VIDEO_ID
      - https://m.youtube.com/watch?v=VIDEO_ID
    """
    url = url.strip()
    parsed = urlparse(url)

    # youtu.be/VIDEO_ID
    if parsed.netloc in ("youtu.be", "www.youtu.be"):
        path = parsed.path.lstrip("/")
        if path:
            return path.split("/")[0]

    # youtube.com/watch?v=...
    if "youtube.com" in parsed.netloc:
        qs = parse_qs(parsed.query)
        if "v" in qs and qs["v"]:
            return qs["v"][0]
        # /shorts/VIDEO_ID
        path_parts = [p for p in parsed.path.split("/") if p]
        if len(path_parts) >= 2 and path_parts[0] == "shorts":
            return path_parts[1]

    return None


def _truncate_transcript(text: str, max_chars: int = _MAX_TRANSCRIPT_CHARS) -> str:
    """Обрезать транскрипт до max_chars символов, сохраняя целостность абзацев."""
    if len(text) <= max_chars:
        return text
    truncated = text[:max_chars]
    last_newline = truncated.rfind("\n")
    if last_newline > max_chars // 2:
        truncated = truncated[:last_newline]
    return truncated + "\n\n[... транскрипт обрезан для экономии токенов ...]"


def _build_analysis_prompt(transcript: str, competitor_name: str) -> str:
    return (
        f'Проанализируй транскрипт видео конкурента "{competitor_name}".\n\n'
        f"Транскрипт:\n{transcript}\n\n"
        "Извлеки следующую информацию и верни ТОЛЬКО валидный JSON без markdown-блоков:\n\n"
        "{\n"
        '  "video_title": "название видео из транскрипта или \'Неизвестно\'",\n'
        '  "key_points": ["тезис 1", "тезис 2"],\n'
        '  "features_mentioned": ["функция/фича 1", "функция/фича 2"],\n'
        '  "pricing_info": "описание цен или null если не упоминается",\n'
        '  "weaknesses": ["слабость/жалоба 1", "жалоба 2"],\n'
        '  "marketing_tactics": ["приём 1", "приём 2"],\n'
        '  "seo_keywords": ["ключевое слово 1", "ключевое слово 2"],\n'
        '  "threat_level": "low | medium | high"\n'
        "}\n\n"
        "Правила:\n"
        "- Все тексты на русском языке\n"
        "- key_points — 3-7 главных тезиса видео\n"
        "- features_mentioned — конкретные функции продукта конкурента\n"
        "- weaknesses — что клиенты/автор критикует или что выглядит слабым местом\n"
        "- marketing_tactics — как конкурент продвигает продукт (FOMO, демо, скидки и т.д.)\n"
        "- seo_keywords — 5-10 ключевых слов для SEO-обхода конкурента\n"
        "- threat_level: high если продукт сопоставим с нашим, medium частично, low слабый"
    )


def _parse_ai_json_response(raw: str) -> dict[str, Any]:
    """Разобрать JSON из ответа AI, убрав возможные markdown-обёртки."""
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s*```$", "", text, flags=re.MULTILINE)
    text = text.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start : end + 1]
    return json.loads(text)


# ---------------------------------------------------------------------------
# Основной сервис
# ---------------------------------------------------------------------------

class YouTubeIntelService:
    """YouTube competitor intelligence via Supadata API + AI analysis.

    Workflow: YouTube URL -> Supadata transcript -> AI structured analysis -> CompetitorInsight.

    Primary target: GramGPT.io YouTube channel and similar competitors.

    Usage (standalone, без SQLAlchemy session):
        service = YouTubeIntelService(api_key="sk-...")
        insight = await service.process_video("https://youtu.be/VIDEO_ID", "GramGPT")

    Usage (с AI router через DB session):
        service = YouTubeIntelService(api_key="sk-...", db_session=session, tenant_id=1)
        insight = await service.process_video("https://youtu.be/VIDEO_ID", "GramGPT")
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        db_session: Any = None,
        tenant_id: Optional[int] = None,
        workspace_id: Optional[int] = None,
    ) -> None:
        self._api_key = api_key or getattr(settings, "SUPADATA_API_KEY", "")
        self._db_session = db_session
        self._tenant_id = tenant_id
        self._workspace_id = workspace_id

    # ------------------------------------------------------------------
    # Публичный API
    # ------------------------------------------------------------------

    async def get_transcript(self, youtube_url: str) -> str:
        """Получить транскрипт видео через Supadata API.

        Args:
            youtube_url: полный URL видео YouTube (любой поддерживаемый формат)

        Returns:
            Сконкатенированный текст транскрипта.

        Raises:
            SupadataAuthError: неверный API ключ
            VideoNotFoundError: видео не найдено
            NoCaptionsError: субтитры недоступны
            SupadataRateLimitError: превышен rate limit
            YouTubeIntelError: другая ошибка Supadata
        """
        if not self._api_key:
            raise SupadataAuthError(
                "SUPADATA_API_KEY не задан. "
                "Добавьте переменную окружения или передайте api_key= в конструктор."
            )

        video_id = _extract_video_id(youtube_url)
        if video_id:
            params: dict[str, str] = {"videoId": video_id}
            log.debug(f"[youtube_intel] videoId={video_id} extracted from URL")
        else:
            params = {"url": youtube_url}
            log.debug(f"[youtube_intel] using raw url={youtube_url}")

        headers = {
            "x-api-key": self._api_key,
            "Accept": "application/json",
        }

        timeout = aiohttp.ClientTimeout(total=_HTTP_TIMEOUT_SEC)
        async with aiohttp.ClientSession(timeout=timeout) as http:
            async with http.get(
                _SUPADATA_TRANSCRIPT_ENDPOINT,
                params=params,
                headers=headers,
            ) as resp:
                body = await resp.text()
                log.debug(
                    f"[youtube_intel] Supadata response status={resp.status} "
                    f"body_len={len(body)}"
                )

                if resp.status in (401, 403):
                    raise SupadataAuthError(
                        f"Supadata вернул {resp.status}: неверный API ключ. "
                        "Проверьте SUPADATA_API_KEY."
                    )

                if resp.status == 429:
                    raise SupadataRateLimitError(
                        "Supadata API: превышен лимит запросов (429). "
                        "Подождите перед следующим запросом."
                    )

                if resp.status == 404:
                    raise VideoNotFoundError(
                        f"Видео не найдено: {youtube_url} (Supadata 404)"
                    )

                if resp.status != 200:
                    raise YouTubeIntelError(
                        f"Supadata вернул неожиданный статус {resp.status}: {body[:300]}"
                    )

                try:
                    data = json.loads(body)
                except json.JSONDecodeError as exc:
                    raise YouTubeIntelError(
                        f"Supadata вернул невалидный JSON: {body[:200]}"
                    ) from exc

                return self._parse_transcript_response(data, youtube_url)

    async def analyze_transcript(
        self,
        transcript: str,
        competitor_name: str,
    ) -> CompetitorInsight:
        """Проанализировать транскрипт с помощью AI и вернуть CompetitorInsight.

        Если доступна db_session + tenant_id — использует route_ai_task.
        Иначе — прямой вызов Gemini через settings.GEMINI_API_KEY.

        Args:
            transcript: текст транскрипта
            competitor_name: название конкурента (для промпта)

        Returns:
            CompetitorInsight с заполненными полями
        """
        truncated = _truncate_transcript(transcript)
        prompt = _build_analysis_prompt(truncated, competitor_name)
        system = (
            "Ты — аналитик конкурентной разведки для SaaS-продукта. "
            "Анализируешь видео конкурентов и извлекаешь структурированные инсайты. "
            "Всегда отвечаешь строго валидным JSON без markdown-блоков."
        )

        if self._db_session is not None and self._tenant_id is not None:
            raw_ai = await self._analyze_via_router(prompt, system)
        else:
            raw_ai = await self._analyze_via_gemini_direct(prompt, system)

        return self._build_insight_from_ai(
            raw_ai=raw_ai,
            video_url="",
            transcript_length=len(transcript),
            competitor_name=competitor_name,
        )

    async def process_video(
        self,
        youtube_url: str,
        competitor_name: str = "GramGPT",
    ) -> CompetitorInsight:
        """Полный пайплайн: получить транскрипт -> проанализировать -> вернуть инсайт.

        Args:
            youtube_url: URL видео YouTube
            competitor_name: название конкурента (по умолчанию GramGPT)

        Returns:
            CompetitorInsight
        """
        log.info(
            f"[youtube_intel] process_video start url={youtube_url} "
            f"competitor={competitor_name}"
        )

        transcript = await self.get_transcript(youtube_url)
        log.info(f"[youtube_intel] transcript fetched len={len(transcript)} chars")

        insight = await self.analyze_transcript(transcript, competitor_name)
        insight.video_url = youtube_url
        insight.transcript_length = len(transcript)

        log.info(
            f"[youtube_intel] analysis done threat={insight.threat_level} "
            f"features={len(insight.features_mentioned)} "
            f"keywords={len(insight.seo_keywords)}"
        )
        return insight

    async def batch_process(
        self,
        urls: List[str],
        competitor_name: str = "GramGPT",
    ) -> List[CompetitorInsight]:
        """Обработать список видео последовательно с задержкой между запросами.

        Args:
            urls: список URL видео YouTube
            competitor_name: название конкурента

        Returns:
            Список CompetitorInsight (успешные). Видео с ошибками пропускаются с логом.
        """
        results: List[CompetitorInsight] = []
        total = len(urls)

        for idx, url in enumerate(urls, start=1):
            log.info(f"[youtube_intel] batch_process [{idx}/{total}] url={url}")
            try:
                insight = await self.process_video(url, competitor_name)
                results.append(insight)
            except NoCaptionsError:
                log.warning(f"[youtube_intel] пропущено (нет субтитров): {url}")
            except VideoNotFoundError:
                log.warning(f"[youtube_intel] пропущено (видео не найдено): {url}")
            except SupadataRateLimitError:
                wait_sec = _BATCH_REQUEST_DELAY_SEC * 5
                log.warning(
                    f"[youtube_intel] rate limit от Supadata на [{idx}], "
                    f"ждём {wait_sec:.0f}s"
                )
                await asyncio.sleep(wait_sec)
                try:
                    insight = await self.process_video(url, competitor_name)
                    results.append(insight)
                except Exception as retry_exc:
                    log.error(
                        f"[youtube_intel] повтор не помог [{idx}]: {retry_exc}"
                    )
            except Exception as exc:
                log.error(
                    f"[youtube_intel] ошибка при обработке [{idx}] {url}: {exc}"
                )

            if idx < total:
                await asyncio.sleep(_BATCH_REQUEST_DELAY_SEC)

        log.info(
            f"[youtube_intel] batch_process завершён: {len(results)}/{total} успешно"
        )
        return results

    async def get_channel_videos(self, channel_url: str) -> List[str]:
        """Получить список URL видео из YouTube-канала.

        Использует Supadata channel endpoint или yt-dlp CLI как fallback.

        Args:
            channel_url: URL канала YouTube

        Returns:
            Список URL видео
        """
        try:
            urls = await self._get_channel_videos_supadata(channel_url)
            if urls:
                log.info(
                    f"[youtube_intel] Supadata вернул {len(urls)} видео для {channel_url}"
                )
                return urls
        except Exception as exc:
            log.warning(
                f"[youtube_intel] Supadata channel endpoint недоступен: {exc}, "
                "fallback на yt-dlp"
            )

        return await self._get_channel_videos_ytdlp(channel_url)

    # ------------------------------------------------------------------
    # Внутренние методы
    # ------------------------------------------------------------------

    def _parse_transcript_response(self, data: Any, source_url: str) -> str:
        """Извлечь текст транскрипта из ответа Supadata API.

        Supadata возвращает один из форматов:
          - {"content": [{"text": "...", "offset": 0, "duration": 3}, ...]}
          - {"transcript": "plain text"}
          - {"text": "plain text"}
          - строка напрямую
        """
        if isinstance(data, str):
            return data.strip()

        if not isinstance(data, dict):
            raise YouTubeIntelError(
                f"Неожиданный формат ответа Supadata: {type(data).__name__}"
            )

        # Формат content (массив сегментов)
        if "content" in data and isinstance(data["content"], list):
            segments = data["content"]
            if not segments:
                raise NoCaptionsError(
                    f"Supadata вернул пустой список сегментов для: {source_url}"
                )
            parts = []
            for seg in segments:
                if isinstance(seg, dict):
                    text = seg.get("text", "")
                elif isinstance(seg, str):
                    text = seg
                else:
                    continue
                if text:
                    parts.append(text.strip())
            result = " ".join(parts)
            if not result.strip():
                raise NoCaptionsError(
                    f"Транскрипт пустой после парсинга сегментов: {source_url}"
                )
            return result

        # Формат plain text
        for key in ("transcript", "text", "captions"):
            if key in data and isinstance(data[key], str) and data[key].strip():
                return data[key].strip()

        # Ошибки от Supadata
        error_msg = data.get("message") or data.get("error") or ""
        if error_msg:
            lower = error_msg.lower()
            if "not found" in lower or "does not exist" in lower:
                raise VideoNotFoundError(f"Supadata: {error_msg} ({source_url})")
            if "no caption" in lower or "transcript" in lower or "subtitle" in lower:
                raise NoCaptionsError(f"Supadata: {error_msg} ({source_url})")
            raise YouTubeIntelError(f"Supadata API error: {error_msg}")

        raise YouTubeIntelError(
            f"Не удалось распознать формат ответа Supadata: {list(data.keys())}"
        )

    async def _analyze_via_router(self, prompt: str, system: str) -> str:
        """Вызов AI через route_ai_task (когда есть db_session + tenant_id)."""
        from core.ai_router import route_ai_task  # type: ignore

        result = await route_ai_task(
            self._db_session,
            task_type="competitor_analysis",
            prompt=prompt,
            system_instruction=system,
            tenant_id=self._tenant_id,
            workspace_id=self._workspace_id,
            max_output_tokens=1500,
            temperature=0.2,
            surface="competitor_intel",
        )
        if not result.ok or not result.parsed:
            if result.response_meta and result.response_meta.get("raw_text"):
                return result.response_meta["raw_text"]
            raise YouTubeIntelError(
                f"route_ai_task вернул ошибку: outcome={result.outcome} "
                f"reason={result.reason_code}"
            )
        return json.dumps(result.parsed, ensure_ascii=False)

    async def _analyze_via_gemini_direct(self, prompt: str, system: str) -> str:
        """Прямой вызов Gemini API без route_ai_task (режим standalone CLI)."""
        api_key = settings.GEMINI_API_KEY
        if not api_key:
            raise YouTubeIntelError(
                "GEMINI_API_KEY не задан. Невозможно выполнить AI анализ без DB session."
            )

        from google import genai  # type: ignore
        from google.genai import types  # type: ignore

        client = genai.Client(api_key=api_key)
        model = settings.GEMINI_FLASH_MODEL or settings.GEMINI_MODEL or "gemini-2.5-flash"
        full_prompt = f"{system}\n\n{prompt}"

        response = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: client.models.generate_content(
                model=model,
                contents=full_prompt,
                config=types.GenerateContentConfig(
                    max_output_tokens=1500,
                    temperature=0.2,
                ),
            ),
        )
        return response.text or ""

    def _build_insight_from_ai(
        self,
        raw_ai: str,
        video_url: str,
        transcript_length: int,
        competitor_name: str,
    ) -> CompetitorInsight:
        """Разобрать ответ AI и собрать CompetitorInsight."""
        try:
            data = _parse_ai_json_response(raw_ai)
        except (json.JSONDecodeError, ValueError) as exc:
            log.warning(
                f"[youtube_intel] не удалось разобрать JSON от AI: {exc}. "
                "Создаём пустой инсайт."
            )
            return CompetitorInsight(
                video_url=video_url,
                video_title="Ошибка парсинга AI ответа",
                competitor_name=competitor_name,
                transcript_length=transcript_length,
                raw_ai_response=raw_ai[:2000] if raw_ai else None,
            )

        def _str_list(val: Any) -> List[str]:
            if isinstance(val, list):
                return [str(v) for v in val if v]
            return []

        threat = str(data.get("threat_level", "medium")).lower().strip()
        if threat not in {"low", "medium", "high"}:
            threat = "medium"

        return CompetitorInsight(
            video_url=video_url,
            video_title=str(data.get("video_title", "Неизвестно")),
            competitor_name=competitor_name,
            key_points=_str_list(data.get("key_points")),
            features_mentioned=_str_list(data.get("features_mentioned")),
            pricing_info=data.get("pricing_info") or None,
            weaknesses=_str_list(data.get("weaknesses")),
            marketing_tactics=_str_list(data.get("marketing_tactics")),
            seo_keywords=_str_list(data.get("seo_keywords")),
            threat_level=threat,
            transcript_length=transcript_length,
            raw_ai_response=raw_ai[:2000] if raw_ai else None,
        )

    async def _get_channel_videos_supadata(self, channel_url: str) -> List[str]:
        """Получить видео канала через Supadata channel endpoint (если доступен)."""
        headers = {
            "x-api-key": self._api_key,
            "Accept": "application/json",
        }
        endpoint = f"{_SUPADATA_BASE}/youtube/channel/videos"
        params = {"url": channel_url}

        timeout = aiohttp.ClientTimeout(total=_HTTP_TIMEOUT_SEC)
        async with aiohttp.ClientSession(timeout=timeout) as http:
            async with http.get(endpoint, params=params, headers=headers) as resp:
                if resp.status == 404:
                    return []
                if resp.status != 200:
                    raise YouTubeIntelError(
                        f"Supadata channel endpoint вернул {resp.status}"
                    )
                data = await resp.json()

        urls: List[str] = []
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    vid_id = item.get("videoId") or item.get("id")
                    vid_url = item.get("url")
                    if vid_url:
                        urls.append(vid_url)
                    elif vid_id:
                        urls.append(f"https://www.youtube.com/watch?v={vid_id}")
        elif isinstance(data, dict):
            items = data.get("videos") or data.get("items") or []
            for item in items:
                if isinstance(item, dict):
                    vid_id = item.get("videoId") or item.get("id")
                    vid_url = item.get("url")
                    if vid_url:
                        urls.append(vid_url)
                    elif vid_id:
                        urls.append(f"https://www.youtube.com/watch?v={vid_id}")

        return urls

    async def _get_channel_videos_ytdlp(self, channel_url: str) -> List[str]:
        """Получить список видео канала через yt-dlp CLI (fallback).

        Требует установленного yt-dlp в PATH (pip install yt-dlp).
        Использует asyncio.create_subprocess_exec — защита от shell injection.
        """
        # Аргументы передаются как отдельные токены (не через shell=True)
        # что исключает shell injection
        args = [
            "yt-dlp",
            "--flat-playlist",
            "--print", "webpage_url",
            "--no-warnings",
            channel_url,
        ]
        log.info(f"[youtube_intel] yt-dlp fallback для канала")
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=120.0
            )
        except FileNotFoundError:
            raise YouTubeIntelError(
                "yt-dlp не найден. Установите: pip install yt-dlp"
            )
        except asyncio.TimeoutError:
            raise YouTubeIntelError(
                "yt-dlp timeout при получении видео канала"
            )

        if proc.returncode != 0:
            err = stderr.decode("utf-8", errors="replace")[:500]
            raise YouTubeIntelError(
                f"yt-dlp вернул код {proc.returncode}: {err}"
            )

        raw_output = stdout.decode("utf-8", errors="replace")
        urls = [
            line.strip()
            for line in raw_output.splitlines()
            if line.strip().startswith("http")
        ]
        log.info(f"[youtube_intel] yt-dlp нашёл {len(urls)} видео")
        return urls
