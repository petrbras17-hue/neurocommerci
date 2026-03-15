"""
PersonaEngine — генерация и управление AI-персонами Telegram-аккаунтов
для автономного прогрева.

Персона определяет демографику, интересы, расписание активности и список
предпочтительных каналов, которые аккаунт будет читать во время прогрева.
"""
from __future__ import annotations

import json
import logging
import random
from datetime import datetime, timezone, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from storage.models import AccountPersona, ChannelMapEntry
from utils.helpers import utcnow

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Распределение типов сессий
# ---------------------------------------------------------------------------

SESSION_TYPES: dict[str, float] = {
    "quick_glance": 0.20,  # 1-3 мин, открыть и закрыть
    "normal": 0.50,        # 15-40 мин, чтение + реакции
    "deep_dive": 0.20,     # 30-60 мин, комментарии + подписки
    "skip": 0.10,          # аккаунт не открывает Telegram
}

# Персона по умолчанию, если AI недоступен
_DEFAULT_PERSONA_DATA: dict[str, Any] = {
    "city": "Москва",
    "language_primary": "ru",
    "language_secondary": None,
    "age_range": "25-34",
    "gender": "neutral",
    "occupation": "менеджер",
    "interests": ["технологии", "бизнес", "новости"],
    "personality_traits": ["вдумчивый", "любознательный", "умеренный"],
    "emoji_set": ["👍", "💡", "🔥", "📌", "✅"],
    "comment_style": "краткий и по делу, иногда с вопросом",
    "wake_hour": 8,
    "sleep_hour": 23,
    "peak_hours": [9, 13, 18, 21],
}

# Каналы-заглушки, если список предпочтительных каналов пуст
_FALLBACK_CHANNELS: list[str] = ["@durov", "@telegram"]

# Системный промпт для генерации персоны
_SYSTEM_PROMPT_TEMPLATE = (
    "Ты — генератор реалистичных цифровых персон для Telegram-аккаунтов. "
    "Верни строго валидный JSON-объект (без комментариев, без markdown) "
    "со следующими полями:\n"
    "  city (string),\n"
    "  language_primary (string, ISO 639-1),\n"
    "  language_secondary (string или null),\n"
    "  age_range (string, например \"25-34\"),\n"
    "  gender (string: male/female/neutral),\n"
    "  occupation (string),\n"
    "  interests (array of strings, 4-8 элементов),\n"
    "  personality_traits (array of strings, 3-5 элементов),\n"
    "  emoji_set (array of strings, ровно 5-7 эмодзи),\n"
    "  comment_style (string, короткое описание стиля),\n"
    "  wake_hour (integer 5-10),\n"
    "  sleep_hour (integer 21-1),\n"
    "  peak_hours (array of 4 integers, часы пиковой активности).\n"
    "Персона должна быть правдоподобной для указанной страны."
)


class PersonaEngine:
    """
    Движок генерации и управления AI-персонами Telegram-аккаунтов.

    Отвечает за:
    - генерацию персон через AI (route_ai_task)
    - подбор подходящих каналов по интересам
    - управление расписанием активности
    - выбор типа следующей сессии
    """

    # ------------------------------------------------------------------
    # Генерация персоны
    # ------------------------------------------------------------------

    async def generate_persona(
        self,
        account_id: int,
        tenant_id: int,
        session: AsyncSession,
        *,
        country: str,
        prompt: str | None = None,
        auto_channels: bool = True,
    ) -> AccountPersona:
        """
        Генерирует новую AI-персону для аккаунта.

        Если AI недоступен — возвращает персону по умолчанию.
        Если auto_channels=True — подбирает каналы по интересам из channel_map_entries.

        Args:
            account_id: ID аккаунта.
            tenant_id: ID тенанта (для RLS).
            session: Async DB-сессия.
            country: Страна персоны (например, "Россия", "Казахстан").
            prompt: Дополнительное описание персоны от оператора.
            auto_channels: Автоматически подобрать каналы по интересам.

        Returns:
            Созданная строка AccountPersona (approved=False).
        """
        persona_data = await self._call_ai(country=country, extra_prompt=prompt, tenant_id=tenant_id, session=session)

        preferred_channels: list[str] = []
        if auto_channels and persona_data.get("interests"):
            preferred_channels = await self._match_channels(
                interests=persona_data["interests"],
                session=session,
                count=random.randint(5, 10),
            )

        row = AccountPersona(
            tenant_id=tenant_id,
            account_id=account_id,
            city=persona_data.get("city"),
            language_primary=persona_data.get("language_primary", "ru"),
            language_secondary=persona_data.get("language_secondary"),
            age_range=persona_data.get("age_range"),
            gender=persona_data.get("gender"),
            occupation=persona_data.get("occupation"),
            interests=persona_data.get("interests"),
            personality_traits=persona_data.get("personality_traits"),
            emoji_set=persona_data.get("emoji_set"),
            comment_style=persona_data.get("comment_style"),
            wake_hour=int(persona_data.get("wake_hour", 8)),
            sleep_hour=int(persona_data.get("sleep_hour", 23)),
            peak_hours=persona_data.get("peak_hours"),
            timezone_offset=self._country_to_tz_offset(country),
            preferred_channels=preferred_channels or None,
            source="ai_generated",
            approved=False,
        )
        session.add(row)
        await session.flush()
        log.info(
            "Персона создана: account_id=%s tenant_id=%s city=%s",
            account_id,
            tenant_id,
            row.city,
        )
        return row

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    async def get_persona(
        self,
        account_id: int,
        tenant_id: int,
        session: AsyncSession,
    ) -> AccountPersona | None:
        """
        Возвращает персону аккаунта или None, если не найдена.

        Тенантная изоляция обеспечивается RLS плюс явным фильтром.
        """
        stmt = select(AccountPersona).where(
            AccountPersona.account_id == account_id,
            AccountPersona.tenant_id == tenant_id,
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    async def update_persona(
        self,
        account_id: int,
        tenant_id: int,
        session: AsyncSession,
        **fields: Any,
    ) -> AccountPersona:
        """
        Обновляет указанные поля персоны.

        Если источник был ai_generated — меняет его на hybrid.
        Всегда обновляет updated_at.

        Raises:
            ValueError: если персона не найдена.
        """
        persona = await self.get_persona(account_id, tenant_id, session)
        if persona is None:
            raise ValueError(f"Персона не найдена: account_id={account_id} tenant_id={tenant_id}")

        for key, value in fields.items():
            if hasattr(persona, key):
                setattr(persona, key, value)
            else:
                log.warning("Неизвестное поле персоны: %s", key)

        if persona.source == "ai_generated":
            persona.source = "hybrid"

        persona.updated_at = utcnow()
        await session.flush()
        log.info("Персона обновлена: account_id=%s tenant_id=%s fields=%s", account_id, tenant_id, list(fields.keys()))
        return persona

    async def approve_persona(
        self,
        account_id: int,
        tenant_id: int,
        session: AsyncSession,
    ) -> AccountPersona:
        """
        Подтверждает персону оператором.

        Устанавливает approved=True и approved_at=utcnow().

        Raises:
            ValueError: если персона не найдена.
        """
        persona = await self.get_persona(account_id, tenant_id, session)
        if persona is None:
            raise ValueError(f"Персона не найдена: account_id={account_id} tenant_id={tenant_id}")

        persona.approved = True
        persona.approved_at = utcnow()
        persona.updated_at = utcnow()
        await session.flush()
        log.info("Персона подтверждена: account_id=%s tenant_id=%s", account_id, tenant_id)
        return persona

    # ------------------------------------------------------------------
    # Логика сессий и расписания
    # ------------------------------------------------------------------

    def roll_session_type(self) -> str:
        """
        Случайный выбор типа сессии на основе весов SESSION_TYPES.

        Returns:
            Одно из: "quick_glance", "normal", "deep_dive", "skip".
        """
        choices = list(SESSION_TYPES.keys())
        weights = list(SESSION_TYPES.values())
        return random.choices(choices, weights=weights, k=1)[0]

    def is_active_time(self, persona: AccountPersona) -> bool:
        """
        Проверяет, находится ли текущий момент в активном окне персоны.

        Активное окно — между wake_hour и sleep_hour в часовом поясе персоны.

        Args:
            persona: Строка AccountPersona с параметрами расписания.

        Returns:
            True если сейчас рабочее время, False если ночь.
        """
        tz_offset = persona.timezone_offset if persona.timezone_offset is not None else 3
        now_local = datetime.now(timezone.utc) + timedelta(hours=tz_offset)
        current_hour = now_local.hour

        wake = persona.wake_hour if persona.wake_hour is not None else 8
        sleep = persona.sleep_hour if persona.sleep_hour is not None else 23

        if wake <= sleep:
            return wake <= current_hour < sleep
        # Ночная смена: wake=22, sleep=6
        return current_hour >= wake or current_hour < sleep

    def get_next_session_delay(
        self,
        persona: AccountPersona,
        phase_config: dict[str, Any],
    ) -> int:
        """
        Вычисляет задержку в секундах до следующей сессии.

        Алгоритм:
        1. Base = 24 * 3600 / sessions_per_day
        2. Применяет ±30% jitter
        3. С вероятностью 70% выравнивается по ближайшему peak_hour (± 30 мин)
        4. Если следующая сессия попадает в сон — переносит на wake_hour + 0-60 мин
        5. Если выходной и random() > weekend_activity — умножает задержку на 1.5

        Args:
            persona: Строка AccountPersona.
            phase_config: Словарь с ключами sessions_per_day (int)
                          и опционально weekend_activity (float 0-1).

        Returns:
            Задержка в секундах (минимум 60).
        """
        sessions_per_day: int = int(phase_config.get("sessions_per_day", 3))
        weekend_activity: float = float(phase_config.get("weekend_activity", 0.7))

        base_delay = (24 * 3600) // max(sessions_per_day, 1)

        # ±30% jitter
        jitter_factor = 1.0 + random.uniform(-0.30, 0.30)
        delay = int(base_delay * jitter_factor)

        # Выравнивание по peak_hour (70%)
        peak_hours: list[int] = persona.peak_hours or [9, 13, 18, 21]
        if peak_hours and random.random() < 0.70:
            tz_offset = persona.timezone_offset if persona.timezone_offset is not None else 3
            now_local = datetime.now(timezone.utc) + timedelta(hours=tz_offset)
            current_minutes_of_day = now_local.hour * 60 + now_local.minute

            target_hour = self._nearest_peak_hour(current_minutes_of_day, peak_hours)
            target_minutes = target_hour * 60 + random.randint(-30, 30)
            target_minutes = max(0, min(target_minutes, 24 * 60 - 1))

            diff = target_minutes - current_minutes_of_day
            if diff < 0:
                diff += 24 * 60  # следующие сутки
            delay = diff * 60

        # Проверка: не попадает ли следующая сессия в сон
        tz_offset = persona.timezone_offset if persona.timezone_offset is not None else 3
        future_local = datetime.now(timezone.utc) + timedelta(hours=tz_offset, seconds=delay)
        future_hour = future_local.hour

        wake = persona.wake_hour if persona.wake_hour is not None else 8
        sleep = persona.sleep_hour if persona.sleep_hour is not None else 23

        if not self._hour_in_active_window(future_hour, wake, sleep):
            # Перенести на wake_hour + 0..60 мин
            now_local = datetime.now(timezone.utc) + timedelta(hours=tz_offset)
            tomorrow_wake = now_local.replace(hour=wake, minute=0, second=0, microsecond=0)
            if tomorrow_wake <= now_local:
                tomorrow_wake += timedelta(days=1)
            extra_minutes = random.randint(0, 60)
            delay = int((tomorrow_wake - now_local).total_seconds()) + extra_minutes * 60

        # Выходной коэффициент
        now_utc = datetime.now(timezone.utc)
        if now_utc.weekday() in (5, 6) and random.random() > weekend_activity:
            delay = int(delay * 1.5)

        return max(delay, 60)

    def select_channels(self, persona: AccountPersona, count: int = 3) -> list[str]:
        """
        Выбирает `count` каналов из списка предпочтительных каналов персоны.

        Если каналов меньше, чем нужно — дополняет каналами по умолчанию.

        Args:
            persona: Строка AccountPersona.
            count: Сколько каналов нужно вернуть.

        Returns:
            Список username каналов (без @).
        """
        preferred: list[str] = list(persona.preferred_channels or [])
        random.shuffle(preferred)

        selected = preferred[:count]

        if len(selected) < count:
            padding = [
                ch for ch in _FALLBACK_CHANNELS
                if ch not in selected
            ]
            selected.extend(padding[: count - len(selected)])

        return selected

    # ------------------------------------------------------------------
    # Внутренние методы
    # ------------------------------------------------------------------

    async def _call_ai(
        self,
        *,
        country: str,
        extra_prompt: str | None,
        tenant_id: int,
        session: AsyncSession,
    ) -> dict[str, Any]:
        """
        Вызывает route_ai_task для генерации персоны.

        При ошибке возвращает _DEFAULT_PERSONA_DATA.
        """
        try:
            from core.ai_router import route_ai_task  # type: ignore

            user_prompt = f"Страна: {country}."
            if extra_prompt:
                user_prompt += f" Дополнительные требования: {extra_prompt}"

            result = await route_ai_task(
                session,
                task_type="persona_generation",
                prompt=user_prompt,
                system_instruction=_SYSTEM_PROMPT_TEMPLATE,
                tenant_id=tenant_id,
                max_output_tokens=600,
                temperature=0.7,
                surface="warmup",
            )

            raw_text: str = ""
            if hasattr(result, "text"):
                raw_text = result.text or ""
            elif isinstance(result, dict):
                raw_text = result.get("text", "")

            if raw_text:
                data = self._parse_json(raw_text)
                if data:
                    log.debug("AI-персона успешно получена для tenant_id=%s country=%s", tenant_id, country)
                    return data

        except Exception as exc:
            log.warning("AI-генерация персоны недоступна: %s — используем дефолт", exc)

        return dict(_DEFAULT_PERSONA_DATA)

    async def _match_channels(
        self,
        *,
        interests: list[str],
        session: AsyncSession,
        count: int,
    ) -> list[str]:
        """
        Подбирает каналы из channel_map_entries по пересечению topic_tags с interests.

        Возвращает до `count` username-ов (без ведущего @).
        """
        try:
            stmt = (
                select(ChannelMapEntry.username)
                .where(
                    ChannelMapEntry.username.isnot(None),
                    ChannelMapEntry.spam_score < 5,  # type: ignore[operator]
                )
                .limit(count * 5)
            )
            rows = (await session.execute(stmt)).scalars().all()

            # Локальная фильтрация по интересам через topic_tags
            candidates: list[str] = []
            interest_lower = {i.lower() for i in interests}

            # Получаем полные объекты для фильтрации
            stmt_full = (
                select(ChannelMapEntry)
                .where(
                    ChannelMapEntry.username.isnot(None),
                    ChannelMapEntry.spam_score < 5,  # type: ignore[operator]
                )
                .limit(200)
            )
            entries = (await session.execute(stmt_full)).scalars().all()

            for entry in entries:
                tags: list[str] = entry.topic_tags or []
                tags_lower = {t.lower() for t in tags}
                if interest_lower & tags_lower:
                    username = entry.username.lstrip("@")
                    candidates.append(username)

            if not candidates:
                # Fallback: берём любые каналы из индекса
                candidates = [r.lstrip("@") for r in rows if r]

            random.shuffle(candidates)
            return candidates[:count]

        except Exception as exc:
            log.warning("Не удалось подобрать каналы по интересам: %s", exc)
            return []

    @staticmethod
    def _parse_json(text: str) -> dict[str, Any] | None:
        """
        Извлекает JSON из ответа AI.

        Пытается найти первый блок {...} и распарсить его.
        """
        text = text.strip()
        # Убираем markdown-блоки ```json ... ```
        if "```" in text:
            start = text.find("{")
            end = text.rfind("}") + 1
            if start != -1 and end > start:
                text = text[start:end]

        try:
            data = json.loads(text)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass

        # Второй шанс: найти {...}
        start = text.find("{")
        end = text.rfind("}") + 1
        if start != -1 and end > start:
            try:
                data = json.loads(text[start:end])
                if isinstance(data, dict):
                    return data
            except json.JSONDecodeError:
                pass

        return None

    @staticmethod
    def _country_to_tz_offset(country: str) -> int:
        """
        Возвращает типичное смещение UTC в часах для страны (упрощённо).
        """
        country_lower = country.lower()
        tz_map: dict[str, int] = {
            "россия": 3,
            "russia": 3,
            "ru": 3,
            "казахстан": 5,
            "kazakhstan": 5,
            "kz": 5,
            "украина": 2,
            "ukraine": 2,
            "ua": 2,
            "беларусь": 3,
            "belarus": 3,
            "by": 3,
            "узбекистан": 5,
            "uzbekistan": 5,
            "uz": 5,
            "германия": 1,
            "germany": 1,
            "de": 1,
        }
        for key, offset in tz_map.items():
            if key in country_lower:
                return offset
        return 3  # UTC+3 по умолчанию

    @staticmethod
    def _nearest_peak_hour(current_minutes: int, peak_hours: list[int]) -> int:
        """
        Возвращает peak_hour, ближайший к текущему времени (в будущем).
        """
        future_peaks = []
        for h in peak_hours:
            peak_min = h * 60
            if peak_min > current_minutes:
                future_peaks.append((peak_min - current_minutes, h))
        if not future_peaks:
            # Все в прошлом — берём первый завтра
            h = min(peak_hours)
            future_peaks.append((h * 60 + 24 * 60 - current_minutes, h))
        future_peaks.sort()
        return future_peaks[0][1]

    @staticmethod
    def _hour_in_active_window(hour: int, wake: int, sleep: int) -> bool:
        """
        Проверяет, попадает ли hour в активное окно [wake, sleep).
        """
        if wake <= sleep:
            return wake <= hour < sleep
        return hour >= wake or hour < sleep
