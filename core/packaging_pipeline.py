"""
PackagingPipeline — автономное применение профиля (имя, аватар, bio, канал)
к Telegram-аккаунтам с человекоподобными задержками, распределёнными на один день.

Шаги применяются в разные часовые окна (по часовому поясу персоны),
чтобы не вызывать подозрений у антиспам-системы Telegram.

ПРАВИЛА БЕЗОПАСНОСТИ:
1. Никогда не менять профиль сразу после подключения — изменения разнесены на весь день.
2. Никогда не менять несколько атрибутов одновременно — каждый шаг отдельно.
3. Обрабатывать frozen-аккаунты без повторных попыток.
4. Каждый шаг идемпотентен — если done=true, шаг пропускается.
5. Всегда освобождать Telethon-клиент в блоке finally.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.ai_router import route_ai_task
from core.anti_detection import AntiDetection
from storage.models import AccountPackagingPreset, AccountPersona
from utils.helpers import utcnow

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Telethon импорты — опциональны (тесты могут работать без Telethon)
# ---------------------------------------------------------------------------

try:
    from telethon.tl.functions.account import UpdateProfileRequest  # type: ignore
    from telethon.tl.functions.photos import UploadProfilePhotoRequest  # type: ignore
    from telethon.tl.functions.channels import CreateChannelRequest  # type: ignore
    _TELETHON_AVAILABLE = True
except ImportError:
    _TELETHON_AVAILABLE = False
    UpdateProfileRequest = None        # type: ignore
    UploadProfilePhotoRequest = None   # type: ignore
    CreateChannelRequest = None        # type: ignore

# ---------------------------------------------------------------------------
# SessionPool импорт — опционален
# ---------------------------------------------------------------------------

try:
    from core.session_pool import SessionPool, SessionDeadError  # type: ignore
    _SESSION_POOL_AVAILABLE = True
except ImportError:
    SessionPool = None           # type: ignore
    SessionDeadError = Exception
    _SESSION_POOL_AVAILABLE = False


# ---------------------------------------------------------------------------
# Расписание шагов — распределение по часам дня
# ---------------------------------------------------------------------------

PACKAGING_STEPS: list[dict] = [
    {"step": "set_name",       "hour_range": (8, 10),  "description": "Сменить имя"},
    {"step": "set_avatar",     "hour_range": (12, 14), "description": "Поставить аватар"},
    {"step": "set_bio",        "hour_range": (16, 18), "description": "Заполнить bio"},
    {"step": "create_channel", "hour_range": (20, 22), "description": "Создать канал + pin"},
]

# Базовая директория хранилища аватаров
_STORAGE_ROOT = "storage/accounts"


def _avatar_storage_path(tenant_id: int, account_id: int) -> str:
    """Возвращает директорию хранения аватаров для аккаунта."""
    return os.path.join(_STORAGE_ROOT, str(tenant_id), str(account_id))


def _schedule_time_for_step(hour_range: tuple[int, int], timezone_offset: int) -> datetime:
    """
    Рассчитывает UTC-время применения шага.

    Берёт случайную минуту внутри часового окна (hour_range),
    корректируя по timezone_offset персоны.
    """
    import random

    now_utc = utcnow()
    # Начало окна в UTC (local_hour - offset = utc_hour)
    local_start_hour, local_end_hour = hour_range
    utc_start_hour = (local_start_hour - timezone_offset) % 24

    # Строим сегодняшнее UTC-время для начала окна
    candidate = now_utc.replace(
        hour=utc_start_hour,
        minute=random.randint(5, 55),
        second=random.randint(0, 59),
        microsecond=0,
    )

    # Если окно уже прошло — переносим на завтра
    window_duration_hours = local_end_hour - local_start_hour
    window_end = candidate + timedelta(hours=window_duration_hours)
    if window_end <= now_utc:
        candidate += timedelta(days=1)

    return candidate


class PackagingPipeline:
    """
    Автономный пайплайн упаковки Telegram-аккаунта.

    Применяет данные профиля (имя, аватар, bio, канал) с человекоподобными
    задержками, распределяя каждый шаг в разные часы в течение одного дня.
    """

    def __init__(
        self,
        session_pool: Optional[Any] = None,
        anti_detection_mode: str = "conservative",
    ) -> None:
        """
        Параметры
        ----------
        session_pool : SessionPool | None
            Пул Telethon-клиентов. Если None — создаётся на лету при необходимости.
        anti_detection_mode : str
            Режим AntiDetection: conservative / moderate / aggressive.
        """
        self._pool = session_pool
        self._anti_detection = AntiDetection(mode=anti_detection_mode)

    # ------------------------------------------------------------------
    # Публичный API
    # ------------------------------------------------------------------

    async def schedule_packaging(
        self,
        account_id: int,
        tenant_id: int,
        session: AsyncSession,
    ) -> Optional[AccountPackagingPreset]:
        """
        Загружает пресет (status='ready') и планирует временны́е слоты для каждого шага.

        Возвращает пресет с заполненным apply_log, либо None если пресет не найден.
        Вызывающая сторона должна отправить алерт при получении None.
        """
        stmt = (
            select(AccountPackagingPreset)
            .where(
                AccountPackagingPreset.account_id == account_id,
                AccountPackagingPreset.tenant_id == tenant_id,
                AccountPackagingPreset.status == "ready",
            )
            .limit(1)
        )
        result = await session.execute(stmt)
        preset = result.scalar_one_or_none()

        if preset is None:
            log.warning(
                "schedule_packaging: пресет не найден "
                "(account_id=%s, tenant_id=%s, status=ready)",
                account_id, tenant_id,
            )
            return None

        # Загружаем персону для определения часового пояса
        timezone_offset = 3  # UTC+3 по умолчанию (Москва)
        if preset.account_id:
            persona_stmt = (
                select(AccountPersona)
                .where(
                    AccountPersona.account_id == account_id,
                    AccountPersona.tenant_id == tenant_id,
                )
                .limit(1)
            )
            persona_res = await session.execute(persona_stmt)
            persona = persona_res.scalar_one_or_none()
            if persona and persona.timezone_offset is not None:
                timezone_offset = persona.timezone_offset

        # Строим apply_log
        apply_log: list[dict] = []
        for step_cfg in PACKAGING_STEPS:
            apply_at = _schedule_time_for_step(
                hour_range=step_cfg["hour_range"],
                timezone_offset=timezone_offset,
            )
            apply_log.append(
                {
                    "step": step_cfg["step"],
                    "description": step_cfg["description"],
                    "scheduled_at": apply_at.isoformat(),
                    "done": False,
                    "done_at": None,
                    "error": None,
                }
            )

        preset.status = "scheduled"
        preset.apply_log = apply_log
        preset.updated_at = utcnow()
        await session.commit()
        await session.refresh(preset)

        log.info(
            "schedule_packaging: запланировано %d шагов для account_id=%s",
            len(apply_log), account_id,
        )
        return preset

    async def execute_step(
        self,
        account_id: int,
        tenant_id: int,
        step_name: str,
        session: AsyncSession,
    ) -> dict:
        """
        Выполняет один шаг упаковки (set_name / set_avatar / set_bio / create_channel).

        Возвращает {"step": str, "status": "done"|"skipped"|"error", "error": str|None}.

        Каждый шаг идемпотентен: если он уже помечен done=true — возвращает {"status": "skipped"}.
        """
        # Загрузка пресета
        preset_stmt = (
            select(AccountPackagingPreset)
            .where(
                AccountPackagingPreset.account_id == account_id,
                AccountPackagingPreset.tenant_id == tenant_id,
                AccountPackagingPreset.status.in_(["scheduled", "applying"]),
            )
            .limit(1)
        )
        result = await session.execute(preset_stmt)
        preset = result.scalar_one_or_none()

        if preset is None:
            return {
                "step": step_name,
                "status": "error",
                "error": "Пресет не найден или не находится в статусе scheduled/applying",
            }

        # Проверка идемпотентности
        apply_log: list[dict] = list(preset.apply_log or [])
        step_entry = next((e for e in apply_log if e["step"] == step_name), None)

        if step_entry is None:
            return {
                "step": step_name,
                "status": "error",
                "error": f"Шаг '{step_name}' не найден в apply_log",
            }

        if step_entry.get("done"):
            log.info("execute_step: шаг '%s' уже выполнен (idempotent skip)", step_name)
            return {"step": step_name, "status": "skipped", "error": None}

        # Загрузка персоны (опционально)
        persona: Optional[AccountPersona] = None
        persona_stmt = (
            select(AccountPersona)
            .where(
                AccountPersona.account_id == account_id,
                AccountPersona.tenant_id == tenant_id,
            )
            .limit(1)
        )
        persona_res = await session.execute(persona_stmt)
        persona = persona_res.scalar_one_or_none()

        # Путь к аватару
        avatar_storage_dir = _avatar_storage_path(tenant_id, account_id)
        avatar_full_path: Optional[str] = None
        if preset.avatar_path:
            # avatar_path может быть абсолютным или относительным
            if os.path.isabs(preset.avatar_path):
                avatar_full_path = preset.avatar_path
            else:
                avatar_full_path = os.path.join(avatar_storage_dir, preset.avatar_path)

        # Обновляем статус пресета
        preset.status = "applying"
        preset.updated_at = utcnow()
        await session.commit()

        client: Any = None
        error_msg: Optional[str] = None

        try:
            client = await self._acquire_client(account_id, tenant_id, session)

            if step_name == "set_name":
                error_msg = await self._step_set_name(client, preset)
            elif step_name == "set_avatar":
                error_msg = await self._step_set_avatar(client, preset, avatar_full_path)
            elif step_name == "set_bio":
                error_msg = await self._step_set_bio(client, preset)
            elif step_name == "create_channel":
                error_msg = await self._step_create_channel(client, preset)
            else:
                error_msg = f"Неизвестный шаг: {step_name}"

        except Exception as exc:
            error_msg = str(exc)
            log.exception(
                "execute_step: ошибка при выполнении шага '%s' для account_id=%s",
                step_name, account_id,
            )
        finally:
            await self._release_client(account_id)

        # Обновляем apply_log
        now_iso = utcnow().isoformat()
        for entry in apply_log:
            if entry["step"] == step_name:
                if error_msg:
                    entry["error"] = error_msg
                else:
                    entry["done"] = True
                    entry["done_at"] = now_iso
                    entry["error"] = None
                break

        preset.apply_log = apply_log
        preset.updated_at = utcnow()

        # Если все шаги выполнены — переводим в applied
        if all(e.get("done") for e in apply_log):
            preset.status = "applied"
            preset.applied_at = utcnow()
        elif error_msg:
            preset.status = "error"
            preset.error_detail = error_msg
        else:
            preset.status = "scheduled"

        await session.commit()

        if error_msg:
            log.error(
                "execute_step: шаг '%s' завершился с ошибкой (account_id=%s): %s",
                step_name, account_id, error_msg,
            )
            return {"step": step_name, "status": "error", "error": error_msg}

        log.info(
            "execute_step: шаг '%s' выполнен успешно (account_id=%s)",
            step_name, account_id,
        )
        return {"step": step_name, "status": "done", "error": None}

    async def get_pending_steps(
        self,
        account_id: int,
        tenant_id: int,
        session: AsyncSession,
    ) -> list[dict]:
        """
        Возвращает шаги из apply_log, у которых done=False и scheduled_at <= now().

        Используется планировщиком для определения, что нужно запустить прямо сейчас.
        """
        stmt = (
            select(AccountPackagingPreset)
            .where(
                AccountPackagingPreset.account_id == account_id,
                AccountPackagingPreset.tenant_id == tenant_id,
                AccountPackagingPreset.status.in_(["scheduled", "applying"]),
            )
            .limit(1)
        )
        result = await session.execute(stmt)
        preset = result.scalar_one_or_none()

        if preset is None or not preset.apply_log:
            return []

        now = utcnow()
        pending: list[dict] = []
        for entry in preset.apply_log:
            if entry.get("done"):
                continue
            scheduled_at_str = entry.get("scheduled_at")
            if not scheduled_at_str:
                continue
            try:
                scheduled_at = datetime.fromisoformat(scheduled_at_str)
                # Нормализуем к naive UTC для сравнения
                if scheduled_at.tzinfo is not None:
                    scheduled_at = scheduled_at.replace(tzinfo=None)
            except ValueError:
                continue
            if scheduled_at <= now:
                pending.append(entry)

        return pending

    async def is_packaging_complete(
        self,
        account_id: int,
        tenant_id: int,
        session: AsyncSession,
    ) -> bool:
        """
        Проверяет, все ли шаги помечены done=True.

        Если да — переводит пресет в статус 'applied' и проставляет applied_at.
        """
        stmt = (
            select(AccountPackagingPreset)
            .where(
                AccountPackagingPreset.account_id == account_id,
                AccountPackagingPreset.tenant_id == tenant_id,
                AccountPackagingPreset.status.in_(["scheduled", "applying", "applied"]),
            )
            .limit(1)
        )
        result = await session.execute(stmt)
        preset = result.scalar_one_or_none()

        if preset is None or not preset.apply_log:
            return False

        all_done = all(entry.get("done") for entry in preset.apply_log)

        if all_done and preset.status != "applied":
            preset.status = "applied"
            preset.applied_at = utcnow()
            preset.updated_at = utcnow()
            await session.commit()
            log.info(
                "is_packaging_complete: упаковка завершена (account_id=%s)",
                account_id,
            )

        return all_done

    async def generate_preset_from_persona(
        self,
        account_id: int,
        tenant_id: int,
        session: AsyncSession,
    ) -> AccountPackagingPreset:
        """
        Генерирует пресет упаковки на основе персоны аккаунта через AI (worker-тир).

        Создаёт AccountPackagingPreset со статусом 'draft' и source='ai_generated'.
        Пресет требует ручного одобрения перед применением (смена статуса на 'ready').
        """
        # Загружаем персону
        persona_stmt = (
            select(AccountPersona)
            .where(
                AccountPersona.account_id == account_id,
                AccountPersona.tenant_id == tenant_id,
            )
            .limit(1)
        )
        persona_res = await session.execute(persona_stmt)
        persona: Optional[AccountPersona] = persona_res.scalar_one_or_none()

        # Строим описание персоны для промпта
        persona_desc = _build_persona_description(persona)

        prompt = f"""
Ты создаёшь реалистичный Telegram-профиль для аккаунта на основе персоны.

Персона:
{persona_desc}

Задача: сгенерируй JSON-объект со следующими полями:
{{
  "display_name": "Имя Фамилия (или только имя) — до 64 символов",
  "bio": "Краткое описание человека для Telegram bio — до 70 символов, без кавычек",
  "channel_name": "Название личного канала — до 100 символов, соответствует интересам персоны",
  "channel_description": "Описание канала — 1-2 предложения"
}}

Требования:
- Имя должно соответствовать стране и полу персоны
- Bio должен отражать занятость или интересы, звучать естественно
- Канал должен быть тематически связан с профессией или хобби персоны
- Всё на русском языке (если персона из RU/KZ/UA/BY)
- Без шаблонных фраз, без стикеров, без маркетингового языка
- Возвращай только JSON, без комментариев
"""

        ai_result = await route_ai_task(
            session,
            task_type="profile_generation",
            prompt=prompt,
            system_instruction=(
                "Ты генератор Telegram-профилей. "
                "Отвечай ТОЛЬКО валидным JSON-объектом, без пояснений."
            ),
            tenant_id=tenant_id,
        )

        # Парсим результат AI
        display_name: Optional[str] = None
        bio: Optional[str] = None
        channel_name: Optional[str] = None
        channel_description: Optional[str] = None

        try:
            # ai_result может быть строкой или dict
            if isinstance(ai_result, str):
                parsed = json.loads(ai_result)
            elif isinstance(ai_result, dict):
                parsed = ai_result
            else:
                parsed = {}

            display_name = parsed.get("display_name")
            bio = parsed.get("bio")
            channel_name = parsed.get("channel_name")
            channel_description = parsed.get("channel_description")
        except Exception as exc:
            log.warning(
                "generate_preset_from_persona: не удалось распарсить ответ AI: %s", exc
            )

        preset = AccountPackagingPreset(
            tenant_id=tenant_id,
            account_id=account_id,
            display_name=display_name,
            bio=bio,
            channel_name=channel_name,
            channel_description=channel_description,
            source="ai_generated",
            status="draft",
            persona_prompt=prompt,
        )
        session.add(preset)
        await session.commit()
        await session.refresh(preset)

        log.info(
            "generate_preset_from_persona: создан пресет id=%s для account_id=%s "
            "(требует ручного одобрения)",
            preset.id, account_id,
        )
        return preset

    # ------------------------------------------------------------------
    # Внутренние методы
    # ------------------------------------------------------------------

    async def _acquire_client(
        self,
        account_id: int,
        tenant_id: int,
        session: AsyncSession,
    ) -> Any:
        """
        Получает Telethon-клиент через SessionPool (если доступен).

        Raises RuntimeError если Telethon или SessionPool не доступны.
        """
        if not _TELETHON_AVAILABLE:
            raise RuntimeError(
                "Telethon не установлен — невозможно выполнить шаг упаковки"
            )
        if self._pool is not None:
            return await self._pool.get_client(account_id, db_session=session, tenant_id=tenant_id)

        # Попытка создать пул на лету через session_pool
        if _SESSION_POOL_AVAILABLE:
            from config import settings  # type: ignore
            from pathlib import Path
            pool = SessionPool(sessions_dir=Path(getattr(settings, "sessions_path", "data/sessions")))
            self._pool = pool
            return await self._pool.get_client(account_id, db_session=session, tenant_id=tenant_id)

        raise RuntimeError(
            "SessionPool недоступен — передайте session_pool в конструктор PackagingPipeline"
        )

    async def _release_client(self, account_id: int) -> None:
        """Освобождает клиент обратно в пул (no-op если пул не инициализирован)."""
        if self._pool is not None:
            try:
                await self._pool.release_client(account_id)
            except Exception as exc:
                log.warning("_release_client: ошибка при освобождении клиента: %s", exc)

    async def _step_set_name(
        self, client: Any, preset: AccountPackagingPreset
    ) -> Optional[str]:
        """
        Шаг 1: Смена имени аккаунта.

        Симулирует: пользователь открывает Настройки, думает о имени, набирает его.
        """
        if not preset.display_name:
            return "display_name не задан в пресете"
        try:
            # Имитируем: пользователь думает о имени
            await self._anti_detection.random_delay(10, 30)
            # Имитируем набор имени
            await self._anti_detection.simulate_typing(client, "me", (2, 5))
            await client(UpdateProfileRequest(
                first_name=preset.display_name,
                last_name="",
            ))
            log.info("_step_set_name: имя '%s' установлено", preset.display_name)
            return None
        except Exception as exc:
            return str(exc)

    async def _step_set_avatar(
        self, client: Any, preset: AccountPackagingPreset, avatar_full_path: Optional[str]
    ) -> Optional[str]:
        """
        Шаг 2: Загрузка аватара аккаунта.

        Симулирует: пользователь выбирает фото из галереи и загружает его.
        """
        try:
            # Имитируем: выбор фото
            await self._anti_detection.random_delay(15, 45)
            if not avatar_full_path or not os.path.exists(avatar_full_path):
                log.info(
                    "_step_set_avatar: файл аватара не найден (%s) — шаг пропущен",
                    avatar_full_path,
                )
                return None  # Считаем шаг выполненным (нет файла — нет ошибки)
            photo = await client.upload_file(avatar_full_path)
            await client(UploadProfilePhotoRequest(file=photo))
            log.info("_step_set_avatar: аватар загружен из '%s'", avatar_full_path)
            return None
        except Exception as exc:
            return str(exc)

    async def _step_set_bio(
        self, client: Any, preset: AccountPackagingPreset
    ) -> Optional[str]:
        """
        Шаг 3: Заполнение bio аккаунта.

        Симулирует: пользователь думает о тексте, пишет bio.
        """
        if not preset.bio:
            return "bio не задан в пресете"
        try:
            await self._anti_detection.random_delay(10, 25)
            await client(UpdateProfileRequest(about=preset.bio))
            log.info("_step_set_bio: bio установлен")
            return None
        except Exception as exc:
            return str(exc)

    async def _step_create_channel(
        self, client: Any, preset: AccountPackagingPreset
    ) -> Optional[str]:
        """
        Шаг 4: Создание личного канала и публикация закреплённого поста.

        Симулирует: пользователь создаёт канал, ждёт, публикует первый пост.
        """
        if not preset.channel_name:
            return None  # channel_name опционален — пропускаем без ошибки

        try:
            # Имитируем: пользователь вводит название и описание канала
            await self._anti_detection.random_delay(20, 60)

            result = await client(CreateChannelRequest(
                title=preset.channel_name,
                about=preset.channel_description or "",
                megagroup=False,  # broadcast-канал (не супергруппа)
            ))
            channel = result.chats[0]
            log.info(
                "_step_create_channel: канал '%s' создан (id=%s)",
                preset.channel_name, channel.id,
            )

            if preset.channel_pin_text:
                # Небольшая пауза перед первой публикацией
                await self._anti_detection.random_delay(5, 15)
                msg = await client.send_message(channel, preset.channel_pin_text)
                await client.pin_message(channel, msg)
                log.info("_step_create_channel: первый пост опубликован и закреплён")

            return None
        except Exception as exc:
            return str(exc)


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------


def _build_persona_description(persona: Optional[AccountPersona]) -> str:
    """Формирует текстовое описание персоны для AI-промпта."""
    if persona is None:
        return "Персона не задана. Создай нейтральный русскоязычный профиль."

    parts: list[str] = []

    if persona.country:
        parts.append(f"Страна: {persona.country}")
    if persona.gender:
        parts.append(f"Пол: {persona.gender}")
    if persona.age_range:
        parts.append(f"Возраст: {persona.age_range}")
    if persona.occupation:
        parts.append(f"Занятость: {persona.occupation}")
    if persona.interests:
        interests = persona.interests if isinstance(persona.interests, list) else [str(persona.interests)]
        parts.append(f"Интересы: {', '.join(interests)}")
    if persona.personality_traits:
        traits = persona.personality_traits if isinstance(persona.personality_traits, list) else [str(persona.personality_traits)]
        parts.append(f"Черты характера: {', '.join(traits)}")
    if persona.city:
        parts.append(f"Город: {persona.city}")
    if persona.language_primary:
        parts.append(f"Основной язык: {persona.language_primary}")

    return "\n".join(parts) if parts else "Персона не детализирована."
