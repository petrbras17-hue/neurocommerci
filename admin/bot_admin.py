"""
NEURO COMMENTING — Telegram Bot Admin Panel.
Основной интерфейс управления системой через кнопки в Telegram.
По образцу NeuroCom: всё управление через бота.
"""

import asyncio
import contextlib
import json
import os
from datetime import datetime, timedelta
from html import escape
from typing import Optional
from urllib.parse import quote

from utils.helpers import utcnow

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiogram import Bot, Dispatcher, Router, F, BaseMiddleware
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, CallbackQuery, TelegramObject,
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton,
)
from aiogram.filters import Command
from aiogram.enums import ParseMode
from sqlalchemy import select, func, update

from channels.channel_db import ChannelDB
from channels.discovery import ChannelDiscovery
from channels.monitor import ChannelMonitor
from channels.analyzer import PostAnalyzer
from comments.generator import CommentGenerator
from comments.poster import CommentPoster
from config import settings, Settings, BASE_DIR
from PIL import Image as PILImage
from core.account_manager import AccountManager
from core.account_audit import (
    collect_account_audit,
    collect_json_credentials_audit,
    collect_proxy_observability,
    collect_session_topology_audit,
)
from core.web_accounts import upsert_account_from_session_upload as shared_upsert_account_from_session_upload
from core.proxy_manager import ProxyManager
from core.rate_limiter import RateLimiter
from core.scheduler import TaskScheduler
from core.session_manager import SessionManager
from core.task_queue import task_queue
from storage.google_sheets import GoogleSheetsStorage
from storage.models import (
    Account,
    Comment,
    Post,
    Proxy,
    Channel as DbChannel,
    User,
    PolicyEvent,
    AccountRiskState,
    AccountStageEvent,
)
from storage.sqlite_db import async_session
from utils.logger import log
from utils.channel_subscriber import ChannelSubscriber
from utils.channel_setup import ChannelSetup, prepare_square_avatar
from utils.auto_responder import AutoResponder
from utils.account_uploads import (
    find_api_credential_conflicts,
    get_account_upload_bundle,
    metadata_api_credentials,
    metadata_has_required_api_credentials,
    validate_and_normalize_account_metadata,
    write_normalized_metadata,
)
from core.engine import CommentingEngine
from core.spambot_auto_appeal import SpamBotAutoAppeal
from core.policy_engine import policy_engine
from core.redis_state import redis_state
from utils.notifier import notifier
from utils.ops_api_client import OpsApiError, ops_api_get, ops_api_post
from utils.runtime_readiness import account_blockers
from utils.runtime_snapshot import collect_runtime_snapshot
from utils.proxy_bindings import (
    bind_accounts_to_proxies,
    get_bound_proxy_config,
    get_proxy_pool_summary,
    sync_proxies_from_file,
    validate_proxy_pool,
)
from utils.session_topology import (
    audit_session_topology,
    canonical_metadata_exists,
    canonical_session_dir,
    canonical_session_exists,
    canonical_session_paths,
    discover_session_assets,
)

router = Router()


_ADMIN_POLLER_LOCK_KEY = "admin_bot_poller"
_ADMIN_POLLER_LOCK_TTL_SEC = 60
_ADMIN_POLLER_LOCK_RENEW_SEC = 20


_CLIENT_CALLBACK_EXACT = {
    "onboard_product",
    "onboard_skip",
    "wizard_menu",
    "policy_status",
    "back_main",
    "back_accounts",
}
_CLIENT_CALLBACK_PREFIXES = (
    "wizard_",
)


def _is_client_allowed_callback(data: str | None) -> bool:
    if not data:
        return False
    if data in _CLIENT_CALLBACK_EXACT:
        return True
    return any(data.startswith(prefix) for prefix in _CLIENT_CALLBACK_PREFIXES)


def _tenant_read_scope_user_id(db_user: User | None) -> int | None:
    if db_user and db_user.is_admin:
        return None
    return db_user.id if db_user else None


def _tenant_write_scope_user_id(db_user: User | None) -> int | None:
    return db_user.id if db_user else None


def _is_distributed_production() -> bool:
    return bool(settings.DISTRIBUTED_QUEUE_MODE)


class UserContextMiddleware(BaseMiddleware):
    """Middleware: загружает/создаёт User и передаёт в data['db_user'].

    Блокирует неактивных и не-админов для CallbackQuery.
    """

    async def __call__(self, handler, event: TelegramObject, data: dict):
        from_user = None
        if isinstance(event, (Message, CallbackQuery)):
            from_user = event.from_user

        if from_user:
            db_user = await get_or_create_user(
                telegram_id=from_user.id,
                username=from_user.username,
                first_name=from_user.first_name,
            )
            if not db_user.is_active:
                if isinstance(event, CallbackQuery):
                    await event.answer("⛔ Ваш аккаунт деактивирован.", show_alert=True)
                elif isinstance(event, Message):
                    await event.answer("⛔ Ваш аккаунт деактивирован.")
                return
            # Блокировать CallbackQuery для не-админов (безопасность)
            if isinstance(event, CallbackQuery) and not db_user.is_admin:
                if _is_client_allowed_callback(event.data):
                    if (event.data or "").startswith("wizard_") and not settings.ENABLE_CLIENT_WIZARD:
                        await event.answer("Этот раздел сейчас недоступен", show_alert=True)
                        return
                    data["db_user"] = db_user
                    return await handler(event, data)
                await event.answer("⛔ Действие недоступно.", show_alert=True)
                return
            data["db_user"] = db_user

        return await handler(event, data)


router.message.middleware(UserContextMiddleware())
router.callback_query.middleware(UserContextMiddleware())

# Lock для защиты от race condition при первой регистрации admin
_admin_registration_lock = asyncio.Lock()

proxy_mgr = ProxyManager()
session_mgr = SessionManager()
rate_limiter = RateLimiter()
account_mgr = AccountManager(session_mgr, proxy_mgr, rate_limiter)
channel_db = ChannelDB()
channel_discovery = ChannelDiscovery(session_mgr, account_mgr, proxy_mgr)
sheets_storage = GoogleSheetsStorage(
    credentials_file=settings.GOOGLE_SHEETS_CREDENTIALS_FILE,
    spreadsheet_id=settings.CHANNELS_SPREADSHEET_ID,
)

# --- Мониторинг + Генерация + Отправка ---
channel_monitor = ChannelMonitor(session_mgr, account_mgr, proxy_mgr)
comment_generator = CommentGenerator()
comment_poster = CommentPoster(account_mgr, session_mgr, rate_limiter, comment_generator, channel_monitor)
task_scheduler = TaskScheduler()

# --- Новые модули: подписка, упаковка, автоответчик ---
channel_subscriber = ChannelSubscriber(session_mgr, account_mgr)
channel_setup = ChannelSetup(session_mgr, account_mgr)
auto_responder = AutoResponder(session_mgr)

# --- Движок нейрокомментирования ---
commenting_engine = CommentingEngine(
    account_manager=account_mgr,
    session_manager=session_mgr,
    proxy_manager=proxy_mgr,
    rate_limiter=rate_limiter,
    poster=comment_poster,
    monitor=channel_monitor,
    subscriber=channel_subscriber,
)
spambot_auto_appeal = SpamBotAutoAppeal()

TOPIC_TITLES = {
    "vpn": "VPN и обход блокировок",
    "ai": "Нейросети и AI",
    "social": "Instagram и соцсети",
    "it": "IT и технологии",
    "streaming": "Стриминг-сервисы",
}


class ParserStates(StatesGroup):
    waiting_keywords = State()
    waiting_channel = State()
    waiting_filter_min_subscribers = State()
    waiting_filter_stage1_limit = State()


class SettingsStates(StatesGroup):
    waiting_daily_limit = State()
    waiting_min_delay = State()
    waiting_max_delay = State()
    waiting_scenario_ratio = State()
    waiting_product_link = State()
    waiting_delayed_minutes = State()


class AvatarStates(StatesGroup):
    waiting_photo = State()


class OnboardingStates(StatesGroup):
    waiting_product_name = State()
    waiting_product_link = State()
    waiting_proxy_file = State()
    waiting_session_files = State()


class ComplianceStates(StatesGroup):
    waiting_parser_phone = State()
    waiting_packaging_phone = State()


class AccountFlowStates(StatesGroup):
    waiting_onboarding_phone = State()
    waiting_health_phone = State()


# ============================================================
# Клавиатуры
# ============================================================

def main_menu_kb() -> ReplyKeyboardMarkup:
    """Главное меню — reply-кнопки внизу экрана."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Обзор"), KeyboardButton(text="👤 Аккаунты")],
            [KeyboardButton(text="🌐 Прокси"), KeyboardButton(text="📢 Каналы")],
            [KeyboardButton(text="💬 Автоматический режим"), KeyboardButton(text="🔍 Поиск каналов")],
            [KeyboardButton(text="🎯 Продукт"), KeyboardButton(text="📖 Как пользоваться")],
        ],
        resize_keyboard=True,
    )


def accounts_kb() -> InlineKeyboardMarkup:
    """Меню аккаунтов."""
    rows = [
        [InlineKeyboardButton(text="📋 Мои аккаунты", callback_data="acc_list")],
        [InlineKeyboardButton(text="➕ Загрузить аккаунт", callback_data="acc_add")],
        [InlineKeyboardButton(text="🚀 Начать настройку аккаунта", callback_data="acc_onboarding_start")],
        [InlineKeyboardButton(text="📘 Продолжить настройку", callback_data="acc_onboarding_continue")],
        [InlineKeyboardButton(text="🔐 Проверить доступ", callback_data="acc_health")],
        [InlineKeyboardButton(text="🎨 Подготовить профиль", callback_data="acc_package")],
        [InlineKeyboardButton(text="📝 История шагов", callback_data="acc_onboarding_history")],
        [InlineKeyboardButton(text="🧾 Аудит аккаунтов", callback_data="acc_audit")],
        [InlineKeyboardButton(text="🧬 API JSON", callback_data="acc_api_audit")],
        [InlineKeyboardButton(text="🗂 Файлы сессий", callback_data="acc_session_audit")],
        [InlineKeyboardButton(text="🧹 Начать с нуля", callback_data="acc_reset_prompt")],
        [InlineKeyboardButton(text="🔧 Восстановить аккаунты", callback_data="acc_recovery_queue")],
        [InlineKeyboardButton(text="🧩 Аккаунт для поиска", callback_data="parser_set_account")],
        [InlineKeyboardButton(text="🔄 Обновить список", callback_data="acc_sync_sessions")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_main")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def proxy_kb() -> InlineKeyboardMarkup:
    """Меню прокси."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Список прокси", callback_data="proxy_list")],
        [InlineKeyboardButton(text="🩺 Аудит пула", callback_data="proxy_audit")],
        [InlineKeyboardButton(text="📂 Загрузить из файла", callback_data="proxy_load")],
        [InlineKeyboardButton(text="✅ Проверить все", callback_data="proxy_validate")],
        [InlineKeyboardButton(text="➕ Добавить прокси", callback_data="proxy_add")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_main")],
    ])


def channels_kb() -> InlineKeyboardMarkup:
    """Меню каналов."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 База каналов", callback_data="ch_list")],
        [InlineKeyboardButton(text="📝 Проверить найденные каналы", callback_data="ch_review")],
        [InlineKeyboardButton(text="➕ Добавить канал", callback_data="ch_add")],
        [InlineKeyboardButton(text="🔍 Найти каналы", callback_data="ch_search")],
        [InlineKeyboardButton(text="📊 Статистика каналов", callback_data="ch_stats")],
        [InlineKeyboardButton(text="🗑 Чёрный список", callback_data="ch_blacklist")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_main")],
    ])


def commenting_kb() -> InlineKeyboardMarkup:
    """Меню комментинга."""
    rows = [
        [InlineKeyboardButton(text="🚀 Управление автопилотом", callback_data="engine_menu")],
    ]
    if _is_distributed_production():
        rows.extend([
            [InlineKeyboardButton(text="📊 Что происходит сейчас", callback_data="com_stats")],
            [InlineKeyboardButton(text="📝 История комментариев", callback_data="com_history")],
            [InlineKeyboardButton(text="🎯 Баланс сообщений", callback_data="com_scenarios")],
            [InlineKeyboardButton(text="🧪 Проверить текст", callback_data="com_test")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back_main")],
        ])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    if (
        settings.ENABLE_LEGACY_COMMENTING
        and settings.ENABLE_ADMIN_LEGACY_TOOLS
        and not _is_distributed_production()
    ):
        rows.extend([
            [InlineKeyboardButton(text="▶️ Запустить комментинг", callback_data="com_start")],
            [InlineKeyboardButton(text="⏸ Остановить", callback_data="com_stop")],
            [InlineKeyboardButton(text="⏰ Отложенный запуск", callback_data="com_delayed")],
        ])
    else:
        rows.append([InlineKeyboardButton(text="ℹ️ Legacy режим отключён", callback_data="com_legacy_info")])

    rows.extend([
        [InlineKeyboardButton(text="📊 Статистика", callback_data="com_stats")],
        [InlineKeyboardButton(text="📝 История комментариев", callback_data="com_history")],
        [InlineKeyboardButton(text="🎯 Настроить сценарии A/B", callback_data="com_scenarios")],
        [InlineKeyboardButton(text="🧪 Тестовый комментарий", callback_data="com_test")],
        [InlineKeyboardButton(text="📜 Старые посты", callback_data="com_old_posts")],
        [InlineKeyboardButton(text="💬 Автоответчик ЛС", callback_data="com_autoresponder")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_main")],
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def engine_kb() -> InlineKeyboardMarkup:
    """Меню движка нейрокомментирования."""
    rows = [
        [InlineKeyboardButton(text="▶️ Включить автоматический режим", callback_data="engine_start")],
        [InlineKeyboardButton(text="⏹ Выключить автоматический режим", callback_data="engine_stop")],
        [InlineKeyboardButton(text="📊 Состояние автоматического режима", callback_data="engine_status")],
    ]
    if not _is_distributed_production():
        rows.extend([
            [InlineKeyboardButton(text="🔌 Batch-подключение", callback_data="engine_batch_connect")],
            [InlineKeyboardButton(text="📢 Подписка + капча", callback_data="engine_subscribe")],
        ])
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="back_commenting")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def parser_kb() -> InlineKeyboardMarkup:
    """Меню парсера каналов."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Поиск по ключевым словам", callback_data="parse_keywords")],
        [InlineKeyboardButton(text="📂 Поиск по тематике", callback_data="parse_topic")],
        [InlineKeyboardButton(text="🔗 Найти похожие", callback_data="parse_similar")],
        [InlineKeyboardButton(text="📊 Фильтры (подписчики, активность)", callback_data="parse_filters")],
        [InlineKeyboardButton(text="🗞 Отправить сводку", callback_data="parse_digest_send")],
        [InlineKeyboardButton(text="💾 Экспорт в TXT", callback_data="parse_export")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_main")],
    ])


def settings_kb() -> InlineKeyboardMarkup:
    """Меню настроек."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔗 Ссылка на продукт", callback_data="set_product")],
        [InlineKeyboardButton(text="⏱ Темп работы", callback_data="set_limits")],
        [InlineKeyboardButton(text="🤖 Тексты и стиль", callback_data="set_ai")],
        [InlineKeyboardButton(text="🔄 Баланс сообщений", callback_data="set_scenarios")],
        [InlineKeyboardButton(text="📊 Таблица", callback_data="set_sheets")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_main")],
    ])


# ============================================================
# Управление пользователями
# ============================================================

async def get_or_create_user(telegram_id: int, username: str = None, first_name: str = None) -> User:
    """Получить или создать пользователя. Первый пользователь становится admin."""
    async with async_session() as session:
        result = await session.execute(
            select(User).where(User.telegram_id == telegram_id)
        )
        user = result.scalar_one_or_none()

        if user:
            # Обновить username/first_name если изменились
            changed = False
            if username and user.username != username:
                user.username = username
                changed = True
            if first_name and user.first_name != first_name:
                user.first_name = first_name
                changed = True
            # Throttle last_active_at: update only if > 5 min since last write
            now = utcnow()
            if not user.last_active_at or (now - user.last_active_at).total_seconds() > 300:
                user.last_active_at = now
                changed = True
            if changed:
                await session.commit()
            return user

        # Новый пользователь
        # Первый пользователь или ADMIN_TELEGRAM_ID → admin
        # Lock для защиты от race condition при одновременной регистрации
        async with _admin_registration_lock:
            user_is_admin = (telegram_id == settings.ADMIN_TELEGRAM_ID) or (settings.ADMIN_TELEGRAM_ID == 0)

            if user_is_admin and settings.ADMIN_TELEGRAM_ID == 0:
                settings.ADMIN_TELEGRAM_ID = telegram_id
                _update_env("ADMIN_TELEGRAM_ID", str(telegram_id))

        user = User(
            telegram_id=telegram_id,
            username=username,
            first_name=first_name,
            is_active=True,
            is_admin=user_is_admin,
            product_name=settings.PRODUCT_NAME if user_is_admin else "",
            product_bot_link=settings.PRODUCT_BOT_LINK if user_is_admin else "",
            product_bot_username=settings.PRODUCT_BOT_USERNAME if user_is_admin else "",
            product_avatar_path=settings.PRODUCT_AVATAR_PATH if user_is_admin else "",
            product_short_desc=settings.PRODUCT_SHORT_DESC if user_is_admin else "",
            product_features=settings.PRODUCT_FEATURES if user_is_admin else "",
            product_category=settings.PRODUCT_CATEGORY if user_is_admin else "VPN",
            product_channel_prefix=settings.PRODUCT_CHANNEL_PREFIX if user_is_admin else "",
            scenario_b_ratio=settings.SCENARIO_B_RATIO,
            max_daily_comments=settings.MAX_COMMENTS_PER_ACCOUNT_PER_DAY,
            min_delay=settings.MIN_DELAY_BETWEEN_COMMENTS_SEC,
            max_delay=settings.MAX_DELAY_BETWEEN_COMMENTS_SEC,
            max_accounts=50 if user_is_admin else 3,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        log.info(f"Создан пользователь: {telegram_id} (admin={user_is_admin})")
        return user


def is_admin(user_id: int) -> bool:
    """Legacy: проверить что пользователь — администратор (deprecated, use db_user)."""
    if settings.ADMIN_TELEGRAM_ID == 0:
        return False  # Если ID не задан — запретить доступ (безопасность)
    return user_id == settings.ADMIN_TELEGRAM_ID


def parse_keywords(text: str) -> list[str]:
    raw = [item.strip() for item in text.split(",")]
    return [item for item in raw if item]


def normalize_channel_ref(text: str) -> str:
    parts = text.strip().split()
    if not parts:
        return ""
    cleaned = parts[0]
    if cleaned.startswith("https://t.me/"):
        cleaned = cleaned.replace("https://t.me/", "", 1).strip("/")
    if cleaned.startswith("http://t.me/"):
        cleaned = cleaned.replace("http://t.me/", "", 1).strip("/")
    if cleaned.startswith("t.me/"):
        cleaned = cleaned.replace("t.me/", "", 1).strip("/")
    return cleaned


_ACCOUNT_LIFECYCLE_LABELS = {
    "uploaded": "Новый аккаунт",
    "auth_verified": "Доступ подтверждён",
    "profile_draft": "Черновик профиля готов",
    "profile_applied": "Профиль подтверждён",
    "channel_draft": "Черновик канала готов",
    "channel_applied": "Канал подтверждён",
    "content_draft": "Черновик поста готов",
    "content_applied": "Пост подтверждён",
    "execution_ready": "Готов к работе",
    "packaging": "Идёт подготовка",
    "warming_up": "Прогрев",
    "gate_review": "Ждёт подтверждения",
    "active_commenting": "Готов к работе",
    "packaging_error": "Нужна перепроверка",
    "restricted": "Ограничен",
    "orphaned": "Нужно обновить список",
}

_ACCOUNT_HEALTH_LABELS = {
    "alive": "Доступ есть",
    "unknown": "Пока не проверен",
    "expired": "Нужен повторный вход",
    "restricted": "Ограничен",
    "dead": "Недоступен",
    "frozen": "Временно недоступен",
}

_RECOVERY_FILTER_LABELS = {
    "priority": "Приоритет",
    "parser": "Поиск",
    "uploaded": "Новые",
    "expired": "Повторный вход",
    "restricted": "Ограничены",
    "ready_for_packaging": "Готовы",
}


def _friendly_lifecycle_label(stage: str | None) -> str:
    if not stage:
        return "Статус уточняется"
    return _ACCOUNT_LIFECYCLE_LABELS.get(stage, stage.replace("_", " "))


def _friendly_health_label(health_status: str | None) -> str:
    if not health_status:
        return "Пока не проверен"
    return _ACCOUNT_HEALTH_LABELS.get(health_status, health_status.replace("_", " "))


def _friendly_channel_state(review_state: str | None, publish_mode: str | None) -> str:
    review = (review_state or "discovered").strip().lower()
    publish = (publish_mode or "research_only").strip().lower()
    mapping = {
        ("discovered", "research_only"): "Найден",
        ("candidate", "research_only"): "Сохранён для проверки",
        ("approved", "draft_only"): "Проверять вручную",
        ("approved", "auto_allowed"): "Готов для работы",
        ("blocked", "research_only"): "Не использовать",
    }
    return mapping.get((review, publish), "На проверке")


def _is_parser_account(account: Account) -> bool:
    parser_phone = _normalize_phone(settings.PARSER_ONLY_PHONE) if settings.PARSER_ONLY_PHONE else ""
    return bool(parser_phone) and _normalize_phone(account.phone) == parser_phone


def _friendly_probe_status(authorized: bool, probe_status: str | None) -> str:
    if authorized:
        return "Доступ есть"
    if probe_status in {"unauthorized", "auth_key_unregistered"}:
        return "Нужен повторный вход"
    if probe_status == "metadata_api_credentials_missing":
        return "В .json не хватает ключей входа"
    if probe_status == "proxy_unavailable":
        return "Не удалось подобрать живой прокси"
    if probe_status in {"restricted", "frozen"}:
        return "Нужно проверить вручную"
    return "Проверка требует внимания"


def _friendly_account_status(report: dict) -> str:
    account = report["account"]
    blockers = set(report["readiness"].blockers)

    if _is_parser_account(account) and not report["readiness"].ready:
        return "Нужен для поиска каналов"
    if not report["session_present"]:
        return "Нет файлов входа"
    if "metadata_api_credentials_missing" in blockers:
        return "Нужны ключи входа из .json"
    if "health_expired" in blockers or account.health_status == "expired":
        return "Нужен повторный вход"
    if "health_restricted" in blockers or account.health_status == "restricted":
        return "Ограничен"
    if account.lifecycle_stage == "packaging_error":
        return "Нужна перепроверка"
    if report["package_ready"]:
        return "Готов к первому черновику"
    if account.lifecycle_stage == "auth_verified":
        return "Можно начать черновики"
    if account.lifecycle_stage in {"profile_draft", "channel_draft", "content_draft"}:
        return "Ждёт подтверждения шага"
    if account.lifecycle_stage in {"profile_applied", "channel_applied", "content_applied"}:
        return "Готов к следующему шагу"
    if account.lifecycle_stage in {"active_commenting", "execution_ready"}:
        return "Готов к работе"
    return _friendly_health_label(account.health_status)


def _account_recovery_filter_matches(report: dict, filter_name: str) -> bool:
    account = report["account"]
    blockers = set(report["readiness"].blockers)

    if filter_name == "priority":
        return True
    if filter_name == "parser":
        return _is_parser_account(account)
    if filter_name == "uploaded":
        return account.lifecycle_stage == "uploaded"
    if filter_name == "expired":
        return "health_expired" in blockers or account.health_status == "expired"
    if filter_name == "restricted":
        return "health_restricted" in blockers or account.health_status == "restricted"
    if filter_name == "ready_for_packaging":
        return bool(report["package_ready"])
    return True


def render_channels(channels: list, title: str = "📋 <b>База каналов</b>") -> str:
    if not channels:
        return (
            f"{title}\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "<i>Каналы не найдены.</i>"
        )

    lines = [
        f"{title}",
        "━━━━━━━━━━━━━━━━━━━━",
        f"",
        f"Найдено каналов: <b>{len(channels)}</b>",
        "",
    ]
    for idx, channel in enumerate(channels[:20], start=1):
        username = f"@{escape(channel.username)}" if getattr(channel, "username", None) else "без username"
        title_text = escape((channel.title or "")[:60])
        user_state = escape(
            _friendly_channel_state(
                getattr(channel, "review_state", "discovered"),
                getattr(channel, "publish_mode", "research_only"),
            )
        )
        lines.append(
            f"{idx}. <b>{title_text}</b>\n"
            f"   {username} | 👥 {channel.subscribers} | 🧩 {channel.topic or '—'} | "
            f"статус: <b>{user_state}</b>"
        )
    if len(channels) > 20:
        lines.append("")
        lines.append(f"<i>Показаны первые 20 из {len(channels)} каналов.</i>")
    return "\n".join(lines)


def _render_channel_review_queue(channels: list[DbChannel]) -> str:
    if not channels:
        return (
            "📝 <b>Проверить найденные каналы</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "<i>Пока нет каналов для проверки.</i>"
        )

    lines = [
        "📝 <b>Проверить найденные каналы</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        f"Ждут решения: <b>{len(channels)}</b>",
        "",
    ]
    for channel in channels[:10]:
        username = f"@{escape(channel.username)}" if channel.username else f"id:{channel.telegram_id}"
        lines.append(
            f"#{channel.id} <b>{escape(channel.title)}</b>\n"
            f"   {username} | 👥 {channel.subscribers} | "
            f"статус: <b>{escape(_friendly_channel_state(channel.review_state, channel.publish_mode))}</b>"
        )
    if len(channels) > 10:
        lines.append("")
        lines.append(f"<i>Показаны первые 10 из {len(channels)}.</i>")
    return "\n".join(lines)


def _channel_review_queue_kb(channels: list[DbChannel]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for channel in channels[:10]:
        rows.append([
            InlineKeyboardButton(
                text=f"📝 #{channel.id} {channel.title[:20]}",
                callback_data=f"ch_review_pick:{channel.id}",
            )
        ])
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="back_channels")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _channel_review_action_kb(channel_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🟡 Оставить для проверки", callback_data=f"ch_review_candidate:{channel_id}")],
            [InlineKeyboardButton(text="✅ Разрешить автоматически", callback_data=f"ch_review_auto:{channel_id}")],
            [InlineKeyboardButton(text="📝 Только вручную", callback_data=f"ch_review_draft:{channel_id}")],
            [InlineKeyboardButton(text="⛔ Не использовать", callback_data=f"ch_review_block:{channel_id}")],
            [InlineKeyboardButton(text="◀️ К очереди", callback_data="ch_review")],
        ]
    )


def _build_account_recovery_report(account: Account) -> dict:
    readiness = account_blockers(
        account,
        sessions_dir=settings.sessions_path,
        strict_proxy=bool(settings.STRICT_PROXY_PER_ACCOUNT),
    )
    session_present = canonical_session_exists(
        settings.sessions_path,
        account.user_id,
        account.phone,
    )
    metadata_present = canonical_metadata_exists(
        settings.sessions_path,
        account.user_id,
        account.phone,
    )
    metadata_path = settings.sessions_path / str(int(account.user_id or 0)) / f"{account.phone.lstrip('+')}.json"
    metadata_creds_ok = metadata_has_required_api_credentials(metadata_path) if account.user_id is not None else False
    api_conflicts: list[str] = []
    if metadata_creds_ok and account.user_id is not None:
        app_id, app_hash = metadata_api_credentials(metadata_path)
        if app_id is not None and app_hash:
            api_conflicts = find_api_credential_conflicts(
                settings.sessions_path,
                user_id=int(account.user_id),
                expected_phone=account.phone,
                app_id=int(app_id),
                app_hash=str(app_hash),
            )
    package_ready = (
        session_present
        and metadata_present
        and metadata_creds_ok
        and account.status in {"active", "cooldown", "flood_wait"}
        and account.health_status == "alive"
        and account.lifecycle_stage in {"auth_verified", "packaging_error"}
        and (account.proxy_id is not None or not settings.STRICT_PROXY_PER_ACCOUNT)
    )
    return {
        "account": account,
        "readiness": readiness,
        "session_present": session_present,
        "metadata_present": metadata_present,
        "metadata_creds_ok": metadata_creds_ok,
        "api_conflicts": api_conflicts,
        "package_ready": package_ready,
        "next_step": _account_recovery_next_step(account, readiness.blockers, session_present),
    }


def _account_recovery_priority(report: dict) -> tuple[int, str]:
    account = report["account"]
    blockers = set(report["readiness"].blockers)
    priority = 90
    if _is_parser_account(account):
        priority = 0
    elif account.lifecycle_stage == "uploaded":
        priority = 1
    elif "health_expired" in blockers or account.health_status == "expired":
        priority = 2
    elif "health_restricted" in blockers or account.health_status == "restricted":
        priority = 3
    elif account.lifecycle_stage == "packaging_error":
        priority = 4
    elif "no_proxy_binding" in blockers:
        priority = 5
    elif account.lifecycle_stage == "gate_review":
        priority = 6
    elif account.lifecycle_stage == "warming_up":
        priority = 7
    return priority, str(account.phone)


def _account_recovery_next_step(account: Account, blockers: list[str], session_present: bool) -> str:
    blocker_set = set(blockers)
    if _is_parser_account(account) and ("status_error" in blocker_set or not session_present):
        return "Сначала восстановите этот аккаунт для поиска каналов."
    if not session_present:
        return "Загрузите файлы аккаунта заново."
    if "metadata_missing" in blocker_set:
        return "Добавьте файл .json с тем же номером телефона."
    if "metadata_api_credentials_missing" in blocker_set:
        return "Загрузите .json, в котором есть свой app_id и app_hash."
    if account.health_status in {"unknown", ""} and account.lifecycle_stage == "uploaded":
        return "Сначала проверьте доступ аккаунта."
    if "status_error" in blocker_set and "health_expired" in blocker_set:
        return "Обновите файлы входа и снова проверьте доступ."
    if "status_error" in blocker_set and "health_restricted" in blocker_set:
        return "Проверьте аккаунт вручную и затем повторите проверку."
    if "status_error" in blocker_set:
        return "Проверьте доступ ещё раз после ручной проверки."
    if "no_proxy_binding" in blocker_set:
        return "Откройте «Проверить доступ». Бот сам попробует подобрать живой прокси."
    if account.lifecycle_stage in {"uploaded", "auth_verified", "packaging_error"}:
        return "Откройте «Подготовить профиль»."
    if account.lifecycle_stage == "profile_draft":
        return "Проверьте варианты и подтвердите профиль."
    if account.lifecycle_stage == "profile_applied":
        return "Откройте «Подготовить канал»."
    if account.lifecycle_stage == "channel_draft":
        return "Проверьте варианты и подтвердите канал."
    if account.lifecycle_stage == "channel_applied":
        return "Откройте «Подготовить пост»."
    if account.lifecycle_stage == "content_draft":
        return "Проверьте варианты и подтвердите пост."
    if account.lifecycle_stage == "content_applied":
        return "Назначьте роль аккаунта и переведите его в рабочий пул."
    if account.lifecycle_stage in {"execution_ready", "active_commenting"}:
        return "Аккаунт уже в рабочем пуле."
    return "Проверьте аккаунт ещё раз."


async def _load_account_recovery_reports(
    db_user: User | None,
    *,
    filter_name: str = "priority",
) -> list[dict]:
    accounts = await account_mgr.load_accounts(user_id=_tenant_read_scope_user_id(db_user))
    reports = [
        _build_account_recovery_report(account)
        for account in accounts
    ]
    reports = [report for report in reports if not report["readiness"].ready]
    reports = [report for report in reports if _account_recovery_filter_matches(report, filter_name)]
    reports.sort(key=_account_recovery_priority)
    return reports


def _render_manual_recovery_queue(reports: list[dict], *, filter_name: str) -> str:
    filter_label = _RECOVERY_FILTER_LABELS.get(filter_name, "Приоритет")
    if not reports:
        return (
            "🔧 <b>Восстановить аккаунты</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"<b>Фильтр:</b> {escape(filter_label)}\n\n"
            "<i>Проблемных аккаунтов сейчас нет.</i>"
        )

    package_ready = sum(1 for report in reports if report["package_ready"])
    relogin_needed = sum(
        1
        for report in reports
        if report["account"].health_status == "expired" or "health_expired" in report["readiness"].blockers
    )
    lines = [
        "🔧 <b>Восстановить аккаунты</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        f"Фильтр: <b>{escape(filter_label)}</b>",
        f"Нужно внимания: <b>{len(reports)}</b>",
        f"Нужен повторный вход: <b>{relogin_needed}</b>",
        f"Готовы к подготовке: <b>{package_ready}</b>",
        "",
    ]
    for report in reports[:10]:
        account = report["account"]
        lines.append(
            f"<code>{escape(account.phone)}</code>\n"
            f"   статус: <b>{escape(_friendly_account_status(report))}</b>\n"
            f"   дальше: <b>{escape(report['next_step'])}</b>"
        )
    if len(reports) > 10:
        lines.append("")
        lines.append(f"<i>Показаны первые 10 из {len(reports)}.</i>")
    return "\n".join(lines)


def _manual_recovery_queue_kb(reports: list[dict], *, filter_name: str) -> InlineKeyboardMarkup:
    def _filter_title(name: str) -> str:
        title = _RECOVERY_FILTER_LABELS.get(name, name)
        return f"• {title}" if name == filter_name else title

    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(text=_filter_title("priority"), callback_data="acc_recovery_filter:priority"),
            InlineKeyboardButton(text=_filter_title("parser"), callback_data="acc_recovery_filter:parser"),
        ],
        [
            InlineKeyboardButton(text=_filter_title("uploaded"), callback_data="acc_recovery_filter:uploaded"),
            InlineKeyboardButton(text=_filter_title("expired"), callback_data="acc_recovery_filter:expired"),
        ],
        [
            InlineKeyboardButton(text=_filter_title("restricted"), callback_data="acc_recovery_filter:restricted"),
            InlineKeyboardButton(text=_filter_title("ready_for_packaging"), callback_data="acc_recovery_filter:ready_for_packaging"),
        ],
    ]
    for report in reports[:10]:
        account = report["account"]
        label = f"{account.phone[-4:]} • {_friendly_account_status(report)}"
        rows.append([
            InlineKeyboardButton(
                text=label,
                callback_data=f"acc_recovery_pick:{filter_name}:{account.id}",
            )
        ])
    rows.extend([
        [InlineKeyboardButton(text="🔐 Проверить доступ", callback_data="acc_health")],
        [InlineKeyboardButton(text="🔄 Обновить экран", callback_data=f"acc_recovery_filter:{filter_name}")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_accounts")],
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _render_account_recovery_detail(report: dict) -> str:
    account = report["account"]
    proxy_label = "подключён" if account.proxy_id is not None else "нужно добавить"
    session_label = "загружены" if report["session_present"] else "не найдены"
    metadata_label = "загружен" if report["metadata_present"] else "не найден"
    parser_note = "\nЭтот аккаунт используется для поиска каналов.\n" if _is_parser_account(account) else "\n"
    return (
        "🔧 <b>Аккаунт требует внимания</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Телефон: <code>{escape(account.phone)}</code>\n"
        f"Состояние: <b>{escape(_friendly_account_status(report))}</b>\n"
        f"Этап: <b>{escape(_friendly_lifecycle_label(account.lifecycle_stage))}</b>\n"
        f"Проверка доступа: <b>{escape(_friendly_health_label(account.health_status))}</b>\n"
        f"Прокси: <b>{proxy_label}</b>\n"
        f"Файл .session: <b>{session_label}</b>\n"
        f"Файл .json: <b>{metadata_label}</b>\n"
        f"{parser_note}\n"
        f"Что делать дальше:\n<b>{escape(report['next_step'])}</b>"
    )


def _account_recovery_detail_kb(report: dict, *, filter_name: str) -> InlineKeyboardMarkup:
    account = report["account"]
    rows: list[list[InlineKeyboardButton]] = []
    if not report["session_present"]:
        rows.append([
            InlineKeyboardButton(text="➕ Загрузить заново", callback_data="acc_add")
        ])
    elif report["package_ready"]:
        rows.append([
            InlineKeyboardButton(
                text="✨ Сгенерировать черновик профиля",
                callback_data=f"acc_recovery_package:{filter_name}:{account.id}",
            )
        ])
    elif account.lifecycle_stage == "gate_review":
        rows.append([
            InlineKeyboardButton(text="✅ Открыть подтверждение", callback_data="gate_review")
        ])
    else:
        rows.append([
            InlineKeyboardButton(text="🔐 Проверить доступ ещё раз", callback_data=f"acc_health_pick:{account.id}")
        ])
    rows.extend([
        [InlineKeyboardButton(text="🎨 Открыть подготовку", callback_data="acc_package")],
        [InlineKeyboardButton(text="🔄 К списку восстановления", callback_data=f"acc_recovery_filter:{filter_name}")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_accounts")],
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _collect_runtime_snapshot() -> dict:
    if settings.OPS_API_URL:
        try:
            return await ops_api_get("/v1/runtime/summary")
        except Exception as exc:
            log.warning(f"Не удалось получить runtime snapshot из ops-api: {exc}")
    return await collect_runtime_snapshot(
        initialize_db=False,
        close_backends=False,
        dispose_db=False,
    )


def _format_runtime_snapshot(report: dict, *, title: str) -> str:
    accounts = report["accounts"]
    channels = report["channels"]
    ready_accounts = int(accounts["lifecycle"].get("active_commenting", 0)) + int(accounts["lifecycle"].get("execution_ready", 0))
    waiting_review = int(accounts["lifecycle"].get("gate_review", 0))
    in_progress = (
        int(accounts["lifecycle"].get("packaging", 0))
        + int(accounts["lifecycle"].get("warming_up", 0))
        + int(accounts["lifecycle"].get("profile_draft", 0))
        + int(accounts["lifecycle"].get("profile_applied", 0))
        + int(accounts["lifecycle"].get("channel_draft", 0))
        + int(accounts["lifecycle"].get("channel_applied", 0))
        + int(accounts["lifecycle"].get("content_draft", 0))
        + int(accounts["lifecycle"].get("content_applied", 0))
    )
    needs_attention = max(0, int(accounts["total"]) - ready_accounts)
    return (
        f"{title}\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"✅ Готово аккаунтов: <b>{ready_accounts}</b>\n"
        f"🎨 В подготовке: <b>{in_progress}</b>\n"
        f"🕓 Ждут подтверждения: <b>{waiting_review}</b>\n"
        f"⚠️ Требуют внимания: <b>{needs_attention}</b>\n\n"
        f"📢 Готово каналов: <b>{channels.get('publishable', 0)}</b>\n"
        f"📝 На проверке: <b>{int(channels.get('review_state', {}).get('discovered', 0)) + int(channels.get('review_state', {}).get('candidate', 0))}</b>"
    )


def _friendly_audit_status(status: str | None) -> str:
    mapping = {
        "ready": "Готов",
        "auth-valid restricted": "Доступ есть, но есть ограничения",
        "unauthorized": "Нужен повторный вход",
        "uploaded": "Новый аккаунт",
        "stale-active": "Этап устарел",
    }
    value = str(status or "").strip()
    return mapping.get(value, value or "Неизвестно")


def _friendly_recovery_category(category: str | None) -> str:
    mapping = {
        "ready": "Готов",
        "auth-valid but restricted": "Ограничен после входа",
        "unauthorized but previously connected": "Раньше работал, сейчас нужен новый вход",
        "uploaded unauthorized": "Новая загрузка, вход не подтверждён",
        "stale active unauthorized": "Устаревший рабочий этап",
        "manual review needed": "Нужна ручная проверка",
    }
    value = str(category or "").strip()
    return mapping.get(value, value or "Неизвестно")


async def _fetch_account_audit_payload(*, db_user: User | None) -> dict:
    user_id = _tenant_read_scope_user_id(db_user)
    if settings.OPS_API_URL:
        try:
            suffix = f"?user_id={int(user_id)}" if user_id is not None else ""
            return await ops_api_get(f"/v1/audit/accounts{suffix}", timeout=20)
        except Exception as exc:
            log.warning(f"Не удалось получить account audit из ops-api: {exc}")
    return await collect_account_audit(user_id=user_id)


async def _fetch_json_credentials_audit_payload(*, db_user: User | None) -> dict:
    user_id = _tenant_read_scope_user_id(db_user)
    if settings.OPS_API_URL:
        try:
            suffix = f"?user_id={int(user_id)}" if user_id is not None else ""
            return await ops_api_get(f"/v1/audit/json-credentials{suffix}", timeout=20)
        except Exception as exc:
            log.warning(f"Не удалось получить json credentials audit из ops-api: {exc}")
    return await collect_json_credentials_audit(user_id=user_id)


async def _fetch_session_topology_audit_payload(*, db_user: User | None) -> dict:
    user_id = _tenant_read_scope_user_id(db_user)
    if settings.OPS_API_URL:
        try:
            suffix = f"?user_id={int(user_id)}" if user_id is not None else ""
            return await ops_api_get(f"/v1/audit/sessions{suffix}", timeout=20)
        except Exception as exc:
            log.warning(f"Не удалось получить session topology audit из ops-api: {exc}")
    return await collect_session_topology_audit(user_id=user_id)


async def _fetch_proxy_audit_payload(*, db_user: User | None) -> dict:
    user_id = _tenant_read_scope_user_id(db_user)
    if settings.OPS_API_URL:
        try:
            suffix = f"?user_id={int(user_id)}" if user_id is not None else ""
            return await ops_api_get(f"/v1/audit/proxies{suffix}", timeout=20)
        except Exception as exc:
            log.warning(f"Не удалось получить proxy audit из ops-api: {exc}")
    return await collect_proxy_observability(user_id=user_id)


def _account_audit_kb(items: list[dict]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for item in items[:10]:
        rows.append([
            InlineKeyboardButton(
                text=f"{item['phone'][-4:]} • {_friendly_audit_status(item.get('audit_status'))}",
                callback_data=f"acc_audit_pick:{item['id']}",
            )
        ])
    rows.extend([
        [InlineKeyboardButton(text="🧹 Исправить устаревшие этапы", callback_data="acc_audit_reconcile")],
        [InlineKeyboardButton(text="🧬 API JSON", callback_data="acc_api_audit")],
        [InlineKeyboardButton(text="🗂 Файлы сессий", callback_data="acc_session_audit")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_accounts")],
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _render_account_audit(payload: dict) -> str:
    summary = dict(payload.get("summary") or {})
    items = list(payload.get("items") or [])
    lines = [
        "🧾 <b>Аудит аккаунтов</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        f"Всего: <b>{int(summary.get('total', 0))}</b>",
        f"Готовы: <b>{int(summary.get('ready', 0))}</b>",
        f"Требуют внимания: <b>{int(summary.get('needs_attention', 0))}</b>",
        "",
    ]
    for status, count in (summary.get("status_counts") or {}).items():
        lines.append(f"• {_friendly_audit_status(status)}: <b>{int(count)}</b>")
    if summary.get("shared_api_pair_warnings"):
        lines.extend([
            "",
            f"⚠️ Общая пара app_id/app_hash у: <b>{int(summary.get('shared_api_pair_warnings', 0))}</b>",
        ])
    if items:
        lines.extend(["", "<b>Первые аккаунты:</b>"])
        for item in items[:10]:
            lines.append(
                f"<code>{escape(str(item.get('phone') or ''))}</code> — "
                f"<b>{escape(_friendly_audit_status(item.get('audit_status')))}</b>"
            )
    return "\n".join(lines)


def _render_account_audit_detail(item: dict) -> str:
    readiness = dict(item.get("readiness") or {})
    api = dict(item.get("api_credentials") or {})
    session = dict(item.get("session") or {})
    proxy = dict(item.get("proxy_binding") or {})
    shared_usage = int(api.get("shared_usage_count", 0) or 0)
    lines = [
        "🧾 <b>Карточка аккаунта</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        f"Телефон: <code>{escape(str(item.get('phone') or ''))}</code>",
        f"Статус аудита: <b>{escape(_friendly_audit_status(item.get('audit_status')))}</b>",
        f"План восстановления: <b>{escape(_friendly_recovery_category(item.get('recovery_category')))}</b>",
        f"Этап: <b>{escape(_friendly_lifecycle_label(item.get('lifecycle_stage')))}</b>",
        f"Служебный статус: <b>{escape(_friendly_health_label(item.get('health_status')))}</b>",
        f"Ограничение: <b>{escape(str(item.get('restriction_reason') or 'нет'))}</b>",
        f"Прокси: <b>{'привязан' if proxy.get('bound') else 'не привязан'}</b>",
        f"Файл .session: <b>{'есть' if session.get('present') else 'нет'}</b>",
        f"Файл .json: <b>{'есть' if session.get('metadata_present') else 'нет'}</b>",
        f"app_id: <b>{escape(str(api.get('app_id') or 'missing'))}</b>",
        f"app_hash: <b>{escape(str(api.get('app_hash_fingerprint') or 'missing'))}</b>",
        f"Использований этой пары: <b>{shared_usage}</b>",
        f"Главный blocker: <b>{escape(str(readiness.get('primary_blocker') or 'ready'))}</b>",
        "",
        "Что делать дальше:",
        f"<b>{escape(str(item.get('recommended_next_action') or ''))}</b>",
    ]
    if api.get("shared_warning"):
        lines.extend([
            "",
            "⚠️ Эта пара app_id/app_hash используется и в других JSON.",
        ])
    return "\n".join(lines)


def _account_audit_detail_kb(item: dict) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text="🔐 Проверить доступ", callback_data=f"acc_health_pick:{item['id']}")],
    ]
    if str(item.get("audit_status") or "") == "stale-active":
        rows.append([InlineKeyboardButton(text="🧹 Исправить этапы", callback_data="acc_audit_reconcile")])
    rows.extend([
        [InlineKeyboardButton(text="◀️ К аудиту", callback_data="acc_audit")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_accounts")],
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _render_json_credentials_audit(payload: dict) -> str:
    summary = dict(payload.get("summary") or {})
    items = list(payload.get("items") or [])
    lines = [
        "🧬 <b>Аудит API JSON</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        f"Всего JSON: <b>{int(summary.get('total', 0))}</b>",
        f"Без app_id/app_hash: <b>{int(summary.get('missing_required_credentials', 0))}</b>",
        f"Уникальных пар: <b>{int(summary.get('unique_pairs', 0))}</b>",
        f"Аккаунтов с общей парой: <b>{int(summary.get('shared_pair_accounts', 0))}</b>",
        "",
    ]
    for item in items[:15]:
        warning = " ⚠️" if item.get("shared_warning") else ""
        lines.append(
            f"<code>{escape(str(item.get('phone') or ''))}</code> — "
            f"app_id <b>{escape(str(item.get('app_id') or 'missing'))}</b>, "
            f"hash <b>{escape(str(item.get('app_hash_fingerprint') or 'missing'))}</b>, "
            f"использований <b>{int(item.get('shared_usage_count', 0))}</b>{warning}"
        )
    return "\n".join(lines)


def _render_session_topology_audit(payload: dict) -> str:
    summary = dict(payload.get("summary") or {})
    items = list(payload.get("items") or [])
    lines = [
        "🗂 <b>Аудит файлов сессий</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        f"Телефонов в audit: <b>{int(summary.get('phones_total', 0))}</b>",
        f"Канонический комплект: <b>{int(summary.get('canonical_complete', 0))}</b>",
        f"Root-копии: <b>{int(summary.get('with_root_copies', 0))}</b>",
        f"Legacy-копии: <b>{int(summary.get('with_legacy_copies', 0))}</b>",
        f"Дубликаты вне canonical: <b>{int(summary.get('duplicate_copy_phones', 0))}</b>",
        f"Безопасно убрать старые копии: <b>{int(summary.get('safe_to_quarantine', 0))}</b>",
        "",
    ]
    for item in items[:10]:
        if not item.get("flat_session") and not item.get("flat_metadata") and not item.get("legacy_sessions") and not item.get("legacy_metadata"):
            continue
        lines.append(
            f"<code>{escape(str(item.get('phone') or ''))}</code> — "
            f"<b>{escape(str(item.get('status_kind') or 'unknown'))}</b>"
        )
    return "\n".join(lines)


def _render_proxy_audit(payload: dict) -> str:
    summary = dict(payload.get("summary") or {})
    cleanup = dict(payload.get("cleanup") or {})
    lines = [
        "🩺 <b>Аудит прокси</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        f"Всего: <b>{int(summary.get('total', 0))}</b>",
        f"Активные: <b>{int(summary.get('active', 0))}</b>",
        f"Живые: <b>{int(summary.get('healthy', 0))}</b>",
        f"Не проверены: <b>{int(summary.get('unknown', 0))}</b>",
        f"Проблемные: <b>{int(summary.get('failing', 0))}</b>",
        f"Отключены/мёртвые: <b>{int(summary.get('dead_or_disabled', 0))}</b>",
        f"Свободны для привязки: <b>{int(summary.get('usable_for_binding', 0))}</b>",
        f"Дубликатов привязки: <b>{int(summary.get('duplicate_bound', 0))}</b>",
        f"Можно удалить старых невалидных: <b>{int(cleanup.get('deleted', 0))}</b>",
    ]
    if summary.get("low_stock"):
        lines.append(
            f"⚠️ Свободных прокси мало. Загрузите ещё <b>{int(summary.get('recommended_topup', 0))}</b>."
        )
    return "\n".join(lines)


async def sync_to_sheets_snapshot():
    if not sheets_storage.is_enabled:
        return

    channels = await channel_db.get_all()

    async with async_session() as session:
        accounts = list((await session.execute(select(Account))).scalars().all())
        comment_rows = list(
            (
                await session.execute(
                    select(Comment, Account.phone, DbChannel.title)
                    .join(Account, Account.id == Comment.account_id)
                    .outerjoin(Post, Post.id == Comment.post_id)
                    .outerjoin(DbChannel, DbChannel.id == Post.channel_id)
                    .order_by(Comment.created_at.desc())
                    .limit(200)
                )
            ).all()
        )

    comments_payload = [
        {
            "created_at": comment.created_at,
            "text": comment.text,
            "scenario": comment.scenario,
            "status": comment.status,
            "account_phone": phone or "—",
            "channel_name": channel_title or "—",
        }
        for comment, phone, channel_title in comment_rows
    ]
    accounts_payload = [
        {
            "phone": account.phone,
            "status": account.status,
            "comments_today": account.comments_today,
            "total_comments": account.total_comments,
            "proxy_url": str(account.proxy_id or ""),
        }
        for account in accounts
    ]

    await sheets_storage.sync_channels(channels)
    await sheets_storage.sync_accounts(accounts_payload)
    await sheets_storage.sync_comments_log(comments_payload)


# ============================================================
# Хендлеры команд
# ============================================================

@router.message(Command("start"))
async def cmd_start(message: Message, db_user: User = None):
    if not db_user:
        # Fallback: middleware should have set this
        db_user = await get_or_create_user(
            message.from_user.id,
            message.from_user.username,
            message.from_user.first_name,
        )

    # Check if new user needs onboarding (no product configured)
    if not db_user.product_name and not db_user.is_admin:
        await message.answer(
            "🚀 <b>NEURO COMMENTING</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Добро пожаловать! Здесь вы подготовите аккаунты,\n"
            "подберёте каналы и включите автоматический режим.\n\n"
            "Быстрый путь:\n\n"
            "1. Настройте продукт\n"
            "2. Загрузите прокси\n"
            "3. Добавьте аккаунты\n"
            "4. Подготовьте аккаунты к работе\n"
            "5. Добавьте каналы и включите автопилот\n\n"
            "Начнём с карточки продукта 👇",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🎯 Настроить продукт", callback_data="onboard_product")],
                [InlineKeyboardButton(text="⏭ Пропустить (настрою позже)", callback_data="onboard_skip")],
            ]),
        )
        return

    product_name = db_user.product_name or settings.PRODUCT_NAME
    product_link = db_user.product_bot_link or settings.PRODUCT_BOT_LINK

    await message.answer(
        "🚀 <b>NEURO COMMENTING</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"Помогает вести продвижение в Telegram\n"
        f"для <b>{escape(product_name)}</b>\n\n"
        f"🔗 Продукт: {escape(product_link)}\n\n"
        "<b>С чего начать:</b>\n"
        "1. 🎯 Продукт → проверьте название и ссылку\n"
        "2. 🌐 Прокси → Загрузить\n"
        "3. 👤 Аккаунты → Загрузить аккаунт\n"
        "4. 👤 Аккаунты → Проверить доступ\n"
        "5. 👤 Аккаунты → Черновики и применение\n"
        "6. 📢 Каналы и 🔍 Поиск каналов → выберите, где работать\n"
        "7. 💬 Автоматический режим → включите автопилот\n\n"
        "Выберите раздел в меню ниже 👇",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_kb(),
    )


# ============================================================
# Онбординг новых пользователей
# ============================================================

@router.callback_query(F.data == "onboard_product")
async def cb_onboard_product(callback: CallbackQuery, state: FSMContext, db_user: User = None):
    await callback.answer()
    await state.set_state(OnboardingStates.waiting_product_name)
    await callback.message.answer(
        "🎯 <b>Шаг 1: Название продукта</b>\n\n"
        "Введите название вашего продукта/сервиса/бота.\n"
        "Например: <code>DartVPN</code>, <code>AIBot</code>, <code>MyCourse</code>",
        parse_mode=ParseMode.HTML,
    )


@router.message(OnboardingStates.waiting_product_name, F.text)
async def process_onboard_product_name(message: Message, state: FSMContext, db_user: User = None):
    if not db_user:
        return
    product_name = message.text.strip()[:100]
    await state.update_data(product_name=product_name)
    await state.set_state(OnboardingStates.waiting_product_link)
    await message.answer(
        f"Продукт: <b>{escape(product_name)}</b>\n\n"
        "🔗 <b>Шаг 2: Ссылка на бот/сервис</b>\n\n"
        "Введите ссылку на ваш Telegram-бот или сервис.\n"
        "Например: <code>https://t.me/DartVPNBot?start=fly</code>",
        parse_mode=ParseMode.HTML,
    )


@router.message(OnboardingStates.waiting_product_link, F.text)
async def process_onboard_product_link(message: Message, state: FSMContext, db_user: User = None):
    if not db_user:
        return
    product_link = message.text.strip()[:300]
    data = await state.get_data()
    product_name = data.get("product_name", "")

    # Extract bot username from link
    bot_username = Settings._parse_bot_username_from_link(product_link) or ""

    # Save to user record
    async with async_session() as session:
        result = await session.execute(
            select(User).where(User.id == db_user.id)
        )
        user = result.scalar_one()
        user.product_name = product_name
        user.product_bot_link = product_link
        user.product_bot_username = bot_username
        await session.commit()

    await state.clear()
    await message.answer(
        "Продукт настроен!\n\n"
        f"Название: <b>{escape(product_name)}</b>\n"
        f"Ссылка: {escape(product_link)}\n"
        f"Username: @{escape(bot_username)}\n\n"
        "Теперь можете:\n"
        "• 🌐 Загрузить прокси\n"
        "• 👤 Добавить аккаунты (.session)\n"
        "• 📢 Найти каналы\n\n"
        "Используйте меню ниже 👇",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_kb(),
    )


@router.callback_query(F.data == "onboard_skip")
async def cb_onboard_skip(callback: CallbackQuery, db_user: User = None):
    await callback.answer("Настроите позже в разделе 🎯 Продукт")
    await callback.message.answer(
        "🚀 <b>NEURO COMMENTING</b>\n\n"
        "Добро пожаловать! Вы можете настроить всё позже.\n"
        "Используйте меню ниже 👇",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_kb(),
    )


# ============================================================
# Хендлеры reply-кнопок (главное меню)
# ============================================================

@router.message(F.text.in_(("📊 Дашборд", "📊 Обзор")))
async def menu_dashboard(message: Message, db_user: User = None):
    if not db_user:
        return

    now = datetime.now().strftime("%d.%m.%Y %H:%M")
    async with async_session() as session:
        comments_total = await session.scalar(select(func.count(Comment.id)))
        comments_today = await session.scalar(
            select(func.count(Comment.id)).where(
                func.date(Comment.created_at) == utcnow().date()
            )
        )
        proxy_total = await session.scalar(select(func.count(Proxy.id)))

    if _is_distributed_production():
        report = await _collect_runtime_snapshot()
        accounts = report["accounts"]
        channels = report["channels"]
        proxies = report.get("proxies") or {}
        status = "Работает" if commenting_engine.is_running else (
            "Готов к запуску" if accounts["total"] else "Ожидание настройки"
        )
        proxy_line = f"🌐 <b>Прокси:</b> {int(proxy_total or 0)}"
        if proxies:
            proxy_line = (
                f"🌐 <b>Прокси:</b> {int(proxies.get('active', 0))} активных, "
                f"{int(proxies.get('usable_for_binding', 0))} свободных"
            )
            if proxies.get("low_stock"):
                proxy_line += (
                    f"\n⚠️ <b>Пора загрузить новые:</b> запас почти закончился "
                    f"(рекомендуется ещё {int(proxies.get('recommended_topup', 0))})"
                )
        await message.answer(
            f"📊 <b>Обзор</b> | {now}\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"⚡ <b>Статус:</b> {status}\n\n"
            f"✅ <b>Готово аккаунтов:</b> {int(accounts['lifecycle'].get('active_commenting', 0)) + int(accounts['lifecycle'].get('execution_ready', 0))}\n"
            f"⚠️ <b>Требуют внимания:</b> {max(0, accounts['total'] - (int(accounts['lifecycle'].get('active_commenting', 0)) + int(accounts['lifecycle'].get('execution_ready', 0))))}\n"
            f"📢 <b>Готово каналов:</b> {channels.get('publishable', 0)}\n"
            f"{proxy_line}\n\n"
            f"💬 <b>Сегодня:</b> {comments_today or 0} комментариев\n"
            f"📈 <b>Всего:</b> {comments_total or 0} комментариев",
            parse_mode=ParseMode.HTML,
        )
        return

    accounts = await account_mgr.load_accounts()
    channel_stats = await channel_db.get_stats()

    monitor_stats = channel_monitor.get_stats()
    poster_stats = comment_poster.get_stats()

    if channel_monitor.is_running:
        status = "Работает"
    elif accounts:
        status = "Готов к запуску"
    else:
        status = "Ожидание настройки"
    proxy_summary = await get_proxy_pool_summary(user_id=_tenant_read_scope_user_id(db_user))
    proxy_line = (
        f"🌐 <b>Прокси:</b> {int(proxy_summary.get('active', 0))} активных, "
        f"{int(proxy_summary.get('usable_for_binding', 0))} свободных"
    )
    if proxy_summary.get("low_stock"):
        proxy_line += (
            f"\n⚠️ <b>Пора загрузить новые:</b> рекомендуемый запас "
            f"{int(proxy_summary.get('recommended_topup', 0))}"
        )

    await message.answer(
        f"📊 <b>Обзор</b> | {now}\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"⚡ <b>Статус:</b> {status}\n\n"
        f"✅ <b>Готово аккаунтов:</b> {len(accounts)}\n"
        f"{proxy_line}\n"
        f"📢 <b>Готово каналов:</b> {channel_stats['active']}\n\n"
        f"💬 <b>Сегодня:</b> {comments_today or 0} комментариев\n"
        f"📈 <b>Всего:</b> {comments_total or 0} комментариев\n"
        f"📝 <b>Отправлено:</b> {poster_stats['sent']}, ошибок {poster_stats['failed']}",
        parse_mode=ParseMode.HTML,
    )


@router.message(F.text == "👤 Аккаунты")
async def menu_accounts(message: Message, db_user: User = None):
    if not db_user:
        return
    await message.answer(
        "👤 <b>Аккаунты</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "Здесь вы загружаете аккаунты, проверяете доступ,\n"
        "ведёте пошаговую настройку и готовите аккаунты к работе.\n\n"
        "Если настраиваете новый аккаунт, начните с:\n"
        "1. Загрузить аккаунт\n"
        "2. Начать настройку аккаунта\n"
        "3. Проверить доступ",
        parse_mode=ParseMode.HTML,
        reply_markup=accounts_kb(),
    )


@router.message(F.text == "🌐 Прокси")
async def menu_proxy(message: Message, db_user: User = None):
    if not db_user:
        return
    summary = await get_proxy_pool_summary(user_id=_tenant_read_scope_user_id(db_user))
    warning = ""
    if summary.get("low_stock"):
        warning = (
            "\n⚠️ Свободных живых прокси мало. "
            f"Заранее подготовьте ещё {int(summary.get('recommended_topup', 0))}."
        )
    await message.answer(
        "🌐 <b>Прокси</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "Для каждого аккаунта нужен свой живой прокси.\n"
        "Перед проверкой доступа бот подберёт и перепроверит отдельный прокси.\n"
        "Нерабочие прокси отключаются автоматически.\n\n"
        "Поддерживаются форматы:\n"
        "<code>type://user:pass@host:port</code>\n"
        "<code>host:port:user:pass</code>"
        f"{warning}",
        parse_mode=ParseMode.HTML,
        reply_markup=proxy_kb(),
    )


@router.message(F.text == "📢 Каналы")
async def menu_channels(message: Message, db_user: User = None):
    if not db_user:
        return
    await message.answer(
        "📢 <b>Каналы</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "Здесь собраны ваши каналы и найденные варианты.\n\n"
        "Вы можете добавить свой канал, подобрать новые\n"
        "и отметить, какие каналы готовы к работе.",
        parse_mode=ParseMode.HTML,
        reply_markup=channels_kb(),
    )


@router.message(F.text.in_(("💬 Комментинг", "💬 Автоматический режим")))
async def menu_commenting(message: Message, db_user: User = None):
    if not db_user:
        return
    await message.answer(
        "💬 <b>Автоматический режим</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "Здесь вы включаете автопилот, смотрите историю,\n"
        "проверяете тексты и следите за общим состоянием.\n\n"
        "Перед запуском убедитесь, что аккаунты готовы,\n"
        "а каналы отмечены для работы.",
        parse_mode=ParseMode.HTML,
        reply_markup=commenting_kb(),
    )


@router.message(F.text.in_(("🔍 Парсер каналов", "🔍 Поиск каналов")))
async def menu_parser(message: Message, db_user: User = None):
    if not db_user:
        return
    await message.answer(
        "🔍 <b>Поиск каналов</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "Подберите новые каналы по ключевым словам,\n"
        "тематикам и похожим каналам.\n\n"
        "Можно настроить фильтры:\n"
        "• Ключевые слова\n"
        "• Мин. подписчиков\n"
        "• Наличие комментариев\n"
        "• Тематика\n"
        "• Язык",
        parse_mode=ParseMode.HTML,
        reply_markup=parser_kb(),
    )


@router.message(F.text.in_(("⚙️ Настройки", "🎯 Продукт")))
async def menu_settings(message: Message, db_user: User = None):
    if not db_user:
        return
    await message.answer(
        "🎯 <b>Продукт</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Название: <b>{escape(settings.PRODUCT_NAME)}</b>\n"
        f"Ссылка: <code>{escape(settings.PRODUCT_BOT_LINK)}</code>\n"
        f"Темп работы: <b>{settings.MAX_COMMENTS_PER_ACCOUNT_PER_DAY}</b> действий в день\n"
        f"Баланс рекомендаций: <b>{int(settings.SCENARIO_B_RATIO * 100)}%</b>\n\n"
        "Здесь можно обновить ссылку на продукт,\n"
        "темп работы и параметры текста.",
        parse_mode=ParseMode.HTML,
        reply_markup=settings_kb(),
    )


def help_kb() -> InlineKeyboardMarkup:
    """Меню инструкции."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 С чего начать", callback_data="help_quickstart")],
        [InlineKeyboardButton(text="🌐 Как загрузить прокси", callback_data="help_ai")],
        [InlineKeyboardButton(text="👤 Как добавить аккаунты", callback_data="help_accounts")],
        [InlineKeyboardButton(text="🔐 Как восстановить доступ", callback_data="help_faq")],
        [InlineKeyboardButton(text="🎨 Как подготовить профиль", callback_data="help_redirect")],
        [InlineKeyboardButton(text="📢 Как добавить и подобрать каналы", callback_data="help_stats")],
        [InlineKeyboardButton(text="💬 Как включить автоматический режим", callback_data="help_scenarios")],
        [InlineKeyboardButton(text="❓ Что значат статусы", callback_data="help_antiban")],
    ])


@router.message(F.text.in_(("📖 Помощь", "📖 Как пользоваться")))
async def menu_help(message: Message, db_user: User = None):
    if not db_user:
        return
    await message.answer(
        "📖 <b>Как пользоваться</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Здесь собраны все шаги: как загрузить аккаунты,\n"
        "подготовить их к работе, выбрать каналы и включить автопилот.\n\n"
        "<b>Выберите раздел:</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=help_kb(),
    )


@router.callback_query(F.data == "help_quickstart")
async def cb_help_quickstart(callback: CallbackQuery):
    await callback.answer()
    await callback.message.edit_text(
        "🚀 <b>С чего начать</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Пройдите путь по порядку:\n\n"
        "<b>1. Загрузите прокси</b>\n"
        "🌐 Прокси → Загрузить из файла\n\n"
        "<b>2. Добавьте аккаунты</b>\n"
        "👤 Аккаунты → Загрузить аккаунт\n\n"
        "<b>3. Запустите пошаговую настройку</b>\n"
        "👤 Аккаунты → Начать настройку аккаунта\n\n"
        "<b>4. Проверьте доступ</b>\n"
        "👤 Аккаунты → Проверить доступ\n\n"
        "<b>5. Если что-то не так — восстановите</b>\n"
        "👤 Аккаунты → Восстановить аккаунты\n\n"
        "<b>6. Подтверждайте шаги с Gemini</b>\n"
        "👤 Аккаунты → Черновики и применение\n"
        "и затем 👤 Аккаунты → Продолжить настройку\n\n"
        "<b>7. Добавьте свои каналы и найдите новые</b>\n"
        "📢 Каналы и 🔍 Поиск каналов\n\n"
        "<b>8. Назначьте роль аккаунта</b>\n"
        "внутри экрана пошаговой настройки\n\n"
        "<b>9. Включите автоматический режим</b>\n"
        "💬 Автоматический режим → Управление автопилотом",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад к инструкции", callback_data="help_back")],
        ]),
    )


@router.callback_query(F.data == "help_accounts")
async def cb_help_accounts(callback: CallbackQuery):
    await callback.answer()
    await callback.message.edit_text(
        "👤 <b>Как добавить аккаунты</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "1. Откройте:\n"
        "👤 Аккаунты → Загрузить аккаунт\n\n"
        "2. Отправьте в чат оба файла аккаунта:\n"
        "• <code>phone_number.session</code>\n"
        "• <code>phone_number.json</code>\n\n"
        "Порядок загрузки не важен.\n"
        "Бот сам покажет, готов ли комплект.\n\n"
        "3. После загрузки откройте:\n"
        "👤 Аккаунты → Начать настройку аккаунта\n\n"
        "4. Затем откройте:\n"
        "👤 Аккаунты → Проверить доступ\n\n"
        "5. Если доступ подтверждён — переходите к подготовке профиля.\n\n"
        "<b>Что должно получиться:</b>\n"
        "• аккаунт виден в разделе «Мои аккаунты»\n"
        "• комплект файлов готов\n"
        "• доступ подтверждён\n"
        "• аккаунт можно отправить на подготовку",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад к инструкции", callback_data="help_back")],
        ]),
    )


@router.callback_query(F.data == "help_redirect")
async def cb_help_redirect(callback: CallbackQuery):
    await callback.answer()
    await callback.message.edit_text(
        "✨ <b>Как работают черновики и применение</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "После проверки доступа настройка идёт по шагам:\n\n"
        "1. 👤 Аккаунты → Черновики и применение\n"
        "2. Сгенерируйте черновик профиля и подтвердите его\n"
        "3. Сгенерируйте черновик канала и подтвердите его\n"
        "4. Сгенерируйте черновик поста и подтвердите его\n"
        "5. Назначьте роль аккаунта\n\n"
        "<b>Во время подготовки:</b>\n"
        "• Gemini сначала предлагает вариант\n"
        "• Telegram-действие выполняется только после вашего подтверждения\n"
        "• каждый шаг сохраняется в историю\n\n"
        "Если аккаунт уже готов, он появится как «Готов к работе».",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад к инструкции", callback_data="help_back")],
        ]),
    )


@router.callback_query(F.data == "help_scenarios")
async def cb_help_scenarios(callback: CallbackQuery):
    await callback.answer()
    await callback.message.edit_text(
        "💬 <b>Как включить автоматический режим</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Когда аккаунты готовы, а каналы отмечены для работы:\n\n"
        "1. Откройте:\n"
        "💬 Автоматический режим → Управление автопилотом\n\n"
        "2. Нажмите:\n"
        "▶️ Включить автоматический режим\n\n"
        "3. Следите за разделами:\n"
        "• Что происходит сейчас\n"
        "• История комментариев\n"
        "• Проверить текст\n\n"
        "Если сначала хотите убедиться в тоне сообщений,\n"
        "используйте «Проверить текст» до запуска.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад к инструкции", callback_data="help_back")],
        ]),
    )


@router.callback_query(F.data == "help_antiban")
async def cb_help_antiban(callback: CallbackQuery):
    await callback.answer()
    await callback.message.edit_text(
        "❓ <b>Что значат статусы</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "<b>Новый аккаунт</b> — аккаунт загружен, но ещё не подготовлен.\n\n"
        "<b>Идёт подготовка</b> — сервис оформляет профиль и готовит его к работе.\n\n"
        "<b>Прогрев</b> — аккаунт проходит последние шаги перед запуском.\n\n"
        "<b>Ждёт подтверждения</b> — подготовка завершена, осталось финально подтвердить.\n\n"
        "<b>Готов к работе</b> — аккаунт можно использовать в автоматическом режиме.\n\n"
        "<b>Нужен повторный вход</b> — файлы аккаунта нужно обновить и снова проверить доступ.\n\n"
        "<b>Ограничен</b> — аккаунт требует ручной проверки перед продолжением.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад к инструкции", callback_data="help_back")],
        ]),
    )


@router.callback_query(F.data == "help_ai")
async def cb_help_ai(callback: CallbackQuery):
    await callback.answer()
    await callback.message.edit_text(
        "🌐 <b>Как загрузить прокси</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "1. Откройте:\n"
        "🌐 Прокси → Загрузить из файла\n\n"
        "2. Отправьте файл со списком прокси.\n\n"
        "3. После загрузки проверьте, что прокси появились в списке.\n\n"
        "<b>Важно:</b>\n"
        "• для каждого аккаунта нужен свой прокси\n"
        "• сначала загрузите прокси, потом аккаунты\n"
        "• если прокси обновились, вернитесь в «Аккаунты» и проверьте доступ",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад к инструкции", callback_data="help_back")],
        ]),
    )


@router.callback_query(F.data == "help_stats")
async def cb_help_stats(callback: CallbackQuery):
    await callback.answer()
    await callback.message.edit_text(
        "📢 <b>Как добавить и подобрать каналы</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "<b>Свои каналы</b>\n"
        "📢 Каналы → Добавить канал\n\n"
        "<b>Подбор новых каналов</b>\n"
        "🔍 Поиск каналов → Поиск по ключевым словам\n"
        "или Поиск по тематике\n\n"
        "<b>Проверка найденных каналов</b>\n"
        "📢 Каналы → Проверить найденные каналы\n\n"
        "<b>Что дальше</b>\n"
        "• «Готов для работы» — канал можно использовать\n"
        "• «Проверять вручную» — канал остаётся под контролем\n"
        "• «Не использовать» — канал исключается",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад к инструкции", callback_data="help_back")],
        ]),
    )


@router.callback_query(F.data == "help_faq")
async def cb_help_faq(callback: CallbackQuery):
    await callback.answer()
    await callback.message.edit_text(
        "🔐 <b>Как восстановить доступ</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Если бот показывает, что с аккаунтом что-то не так:\n\n"
        "1. Откройте:\n"
        "👤 Аккаунты → Восстановить аккаунты\n\n"
        "2. Найдите нужный номер и посмотрите,\n"
        "что именно нужно сделать дальше.\n\n"
        "3. Если бот пишет «Нужен повторный вход»:\n"
        "• загрузите свежие файлы аккаунта\n"
        "• затем нажмите «Проверить доступ»\n\n"
        "4. Если бот пишет «Ограничен»:\n"
        "• сначала проверьте аккаунт вручную\n"
        "• потом снова вернитесь в «Проверить доступ»\n\n"
        "После успешной проверки аккаунт снова можно отправлять на подготовку.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад к инструкции", callback_data="help_back")],
        ]),
    )


@router.callback_query(F.data == "help_back")
async def cb_help_back(callback: CallbackQuery):
    await callback.answer()
    await callback.message.edit_text(
        "📖 <b>Как пользоваться</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Короткие инструкции по каждому шагу работы в боте.\n\n"
        "<b>Выберите раздел:</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=help_kb(),
    )


# ============================================================
# Callback-хендлеры (inline кнопки)
# ============================================================

@router.callback_query(F.data == "back_main")
async def cb_back_main(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.answer()


# --- Аккаунты ---

@router.callback_query(F.data == "acc_list")
async def cb_acc_list(callback: CallbackQuery, db_user: User = None):
    await callback.answer()
    accounts = await account_mgr.load_accounts(
        user_id=_tenant_read_scope_user_id(db_user)
    )
    if not accounts:
        await callback.message.edit_text(
            "👤 <b>Мои аккаунты</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "<i>Пока нет добавленных аккаунтов.\n"
            "Сначала загрузите файл аккаунта.</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=accounts_kb(),
        )
        return

    lines = [
        "👤 <b>Мои аккаунты</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        f"Всего: <b>{len(accounts)}</b>",
        "",
    ]
    for idx, acc in enumerate(accounts[:20], start=1):
        readiness = account_blockers(
            acc,
            sessions_dir=settings.sessions_path,
            strict_proxy=bool(settings.STRICT_PROXY_PER_ACCOUNT),
        )
        report = _build_account_recovery_report(acc)
        lines.append(
            f"{idx}. <code>{escape(acc.phone)}</code>\n"
            f"   статус: <b>{escape(_friendly_account_status(report))}</b>\n"
            f"   этап: <b>{escape(_friendly_lifecycle_label(acc.lifecycle_stage))}</b>\n"
            f"   дальше: <b>{escape(_account_recovery_next_step(acc, readiness.blockers, report['session_present']))}</b>"
        )
    if len(accounts) > 20:
        lines.append("")
        lines.append(f"<i>Показаны первые 20 из {len(accounts)}.</i>")

    await callback.message.edit_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=accounts_kb(),
    )


@router.callback_query(F.data == "acc_add")
async def cb_acc_add(callback: CallbackQuery):
    await callback.answer()
    await callback.message.edit_text(
        "➕ <b>Загрузить аккаунт</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "Отправьте в этот чат оба файла аккаунта.\n"
        "1. <b>phone_number.session</b>\n"
        "2. <b>phone_number.json</b>\n\n"
        "Порядок не важен: бот соберёт комплект сам.\n"
        "После загрузки бот покажет, готов ли комплект.\n\n"
        "Пока вы не откроете «Проверить доступ», аккаунт не используется.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back_accounts")],
        ]),
    )


@router.callback_query(F.data == "acc_onboarding_start")
async def cb_acc_onboarding_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(AccountFlowStates.waiting_onboarding_phone)
    await callback.message.edit_text(
        "🚀 <b>Начать настройку аккаунта</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "Введите номер телефона аккаунта, который хотите вести шаг за шагом.\n"
        "Формат: <code>+79991234567</code>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back_accounts")],
        ]),
    )


@router.message(AccountFlowStates.waiting_onboarding_phone, F.text)
async def process_onboarding_phone(message: Message, state: FSMContext, db_user: User = None):
    phone = _extract_first_phone_from_text(message.text or "")
    if not phone:
        await message.answer("Введите корректный номер, пример: +79991234567")
        return
    await state.clear()
    await _start_onboarding_for_phone(message, phone=phone, db_user=db_user)


async def _start_onboarding_for_phone(message_or_callback: Message | CallbackQuery, *, phone: str, db_user: User | None):
    normalized_phone = _normalize_phone(phone)
    if not normalized_phone:
        if isinstance(message_or_callback, CallbackQuery):
            await message_or_callback.answer("Некорректный номер", show_alert=True)
        else:
            await message_or_callback.answer("Введите корректный номер, пример: +79991234567")
        return

    async with async_session() as session:
        query = select(Account).where(Account.phone == normalized_phone)
        if db_user is not None and not db_user.is_admin:
            query = query.where(Account.user_id == db_user.id)
        result = await session.execute(query)
        account = result.scalar_one_or_none()

    if account is None:
        target = message_or_callback.message if isinstance(message_or_callback, CallbackQuery) else message_or_callback
        await target.answer(
            "❌ <b>Аккаунт не найден</b>\n\n"
            f"Телефон: <code>{escape(normalized_phone)}</code>\n"
            "Сначала загрузите файлы аккаунта и обновите список.",
            parse_mode=ParseMode.HTML,
            reply_markup=accounts_kb(),
        )
        return

    try:
        await ops_api_post(
            f"/v1/accounts/{quote(normalized_phone, safe='')}/onboarding/start",
            {
                "user_id": _tenant_write_scope_user_id(db_user),
                "mode": "bot",
                "channel": "bot",
                "actor": "bot_onboarding_start",
                "notes": "Оператор открыл пошаговую настройку аккаунта.",
            },
            timeout=30,
        )
    except Exception:
        target = message_or_callback.message if isinstance(message_or_callback, CallbackQuery) else message_or_callback
        await target.answer(
            "❌ <b>Не удалось начать настройку</b>\n\n"
            "Сервис временно недоступен. Попробуйте ещё раз.",
            parse_mode=ParseMode.HTML,
            reply_markup=accounts_kb(),
        )
        return

    refreshed = await _load_account_by_id(account.id)
    if refreshed is None:
        return
    snapshot = await _fetch_onboarding_status(refreshed.phone, db_user=db_user, limit_steps=8)
    target = message_or_callback.message if isinstance(message_or_callback, CallbackQuery) else message_or_callback
    await target.answer(
        _render_onboarding_detail(
            _build_account_recovery_report(refreshed),
            snapshot,
            notice="Настройка запущена. Следующий базовый шаг — проверка доступа.",
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=_onboarding_open_kb(refreshed.id, report=_build_account_recovery_report(refreshed), run=(snapshot or {}).get("run")),
    )


@router.callback_query(F.data == "acc_onboarding_continue")
async def cb_acc_onboarding_continue(callback: CallbackQuery, db_user: User = None):
    await callback.answer()
    runs = await _fetch_onboarding_runs(
        db_user=db_user,
        status="active",
        limit=20,
    )
    await callback.message.edit_text(
        _render_onboarding_runs_list(runs, title="📘 <b>Продолжить настройку</b>"),
        parse_mode=ParseMode.HTML,
        reply_markup=_onboarding_runs_kb(runs),
    )


@router.callback_query(F.data == "acc_onboarding_history")
async def cb_acc_onboarding_history(callback: CallbackQuery, db_user: User = None):
    await callback.answer()
    runs = await _fetch_onboarding_runs(
        db_user=db_user,
        status=None,
        limit=20,
    )
    await callback.message.edit_text(
        _render_onboarding_runs_list(runs, title="📝 <b>История шагов</b>"),
        parse_mode=ParseMode.HTML,
        reply_markup=_onboarding_runs_kb(runs),
    )


@router.callback_query(F.data.startswith("acc_onboarding_open:"))
async def cb_acc_onboarding_open(callback: CallbackQuery, db_user: User = None):
    raw_id = _parse_callback_arg(callback.data, "acc_onboarding_open:")
    if not raw_id or not raw_id.isdigit():
        await callback.answer("Некорректный аккаунт", show_alert=True)
        return
    account = await _load_account_by_id(int(raw_id))
    if account is None or not _is_owner_or_admin(db_user, account):
        await callback.answer("Аккаунт не найден", show_alert=True)
        return
    await callback.answer()
    snapshot = await _fetch_onboarding_status(account.phone, db_user=db_user, limit_steps=10)
    report = _build_account_recovery_report(account)
    await callback.message.edit_text(
        _render_onboarding_detail(report, snapshot),
        parse_mode=ParseMode.HTML,
        reply_markup=_onboarding_open_kb(account.id, report=report, run=(snapshot or {}).get("run")),
    )


@router.callback_query(F.data.startswith("acc_onboarding_mark:"))
async def cb_acc_onboarding_mark(callback: CallbackQuery, db_user: User = None):
    payload = _parse_callback_arg(callback.data, "acc_onboarding_mark:") or ""
    if ":" not in payload:
        await callback.answer("Некорректный шаг", show_alert=True)
        return
    raw_id, step_key = payload.split(":", 1)
    if not raw_id.isdigit():
        await callback.answer("Некорректный аккаунт", show_alert=True)
        return
    account = await _load_account_by_id(int(raw_id))
    if account is None or not _is_owner_or_admin(db_user, account):
        await callback.answer("Аккаунт не найден", show_alert=True)
        return
    await _safe_onboarding_step_post(
        account.phone,
        db_user=db_user,
        step_key=step_key,
        actor=f"bot:{step_key}",
        result="done",
        notes="Шаг отмечен оператором вручную.",
    )
    await callback.answer("Шаг сохранён")
    snapshot = await _fetch_onboarding_status(account.phone, db_user=db_user, limit_steps=10)
    refreshed = await _load_account_by_id(account.id) or account
    report = _build_account_recovery_report(refreshed)
    await callback.message.edit_text(
        _render_onboarding_detail(report, snapshot, notice="Шаг сохранён в истории настройки."),
        parse_mode=ParseMode.HTML,
        reply_markup=_onboarding_open_kb(refreshed.id, report=report, run=(snapshot or {}).get("run")),
    )


@router.callback_query(F.data.startswith("acc_onboarding_package:"))
async def cb_acc_onboarding_package(callback: CallbackQuery, db_user: User = None):
    await cb_acc_profile_draft(callback, db_user=db_user)


async def _handle_human_gate_action(
    callback: CallbackQuery,
    *,
    account_id: int,
    db_user: User | None,
    endpoint: str,
    success_notice: str,
    busy_notice: str,
    payload: dict | None = None,
):
    account = await _load_account_by_id(int(account_id))
    if account is None or not _is_owner_or_admin(db_user, account):
        await callback.answer("Аккаунт не найден", show_alert=True)
        return
    try:
        response = await ops_api_post(
            endpoint.format(phone=quote(account.phone, safe="")),
            {
                "user_id": _tenant_write_scope_user_id(db_user),
                "source": "bot",
                "channel": "bot",
                **(payload or {}),
            },
            timeout=60,
        )
    except Exception as exc:
        await callback.answer(busy_notice, show_alert=True)
        refreshed = await _load_account_by_id(account.id) or account
        snapshot = await _fetch_onboarding_status(refreshed.phone, db_user=db_user, limit_steps=10)
        await callback.message.edit_text(
            _render_onboarding_detail(
                _build_account_recovery_report(refreshed),
                snapshot,
                notice=_friendly_packaging_error(exc),
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=_onboarding_open_kb(refreshed.id, report=_build_account_recovery_report(refreshed), run=(snapshot or {}).get("run")),
        )
        return
    refreshed = await _load_account_by_id(account.id) or account
    snapshot = await _fetch_onboarding_status(refreshed.phone, db_user=db_user, limit_steps=10)
    await callback.answer(success_notice)
    await callback.message.edit_text(
        _render_onboarding_detail(
            _build_account_recovery_report(refreshed),
            snapshot,
            notice=success_notice,
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=_onboarding_open_kb(refreshed.id, report=_build_account_recovery_report(refreshed), run=(snapshot or {}).get("run")),
    )


@router.callback_query(F.data.startswith("acc_profile_draft:"))
async def cb_acc_profile_draft(callback: CallbackQuery, db_user: User = None):
    raw_id = _parse_callback_arg(callback.data, "acc_profile_draft:")
    if not raw_id or not raw_id.isdigit():
        await callback.answer("Некорректный аккаунт", show_alert=True)
        return
    await _handle_human_gate_action(
        callback,
        account_id=int(raw_id),
        db_user=db_user,
        endpoint="/v1/accounts/{phone}/profile-draft",
        success_notice="Черновик профиля готов",
        busy_notice="Не удалось сгенерировать черновик профиля",
        payload={"actor": "bot_profile_draft"},
    )


@router.callback_query(F.data.startswith("acc_profile_apply:"))
async def cb_acc_profile_apply(callback: CallbackQuery, db_user: User = None):
    raw_id = _parse_callback_arg(callback.data, "acc_profile_apply:")
    if not raw_id or not raw_id.isdigit():
        await callback.answer("Некорректный аккаунт", show_alert=True)
        return
    await _handle_human_gate_action(
        callback,
        account_id=int(raw_id),
        db_user=db_user,
        endpoint="/v1/accounts/{phone}/profile-apply",
        success_notice="Профиль применён",
        busy_notice="Не удалось применить профиль",
        payload={"actor": "bot_profile_apply"},
    )


@router.callback_query(F.data.startswith("acc_channel_draft:"))
async def cb_acc_channel_draft(callback: CallbackQuery, db_user: User = None):
    raw_id = _parse_callback_arg(callback.data, "acc_channel_draft:")
    if not raw_id or not raw_id.isdigit():
        await callback.answer("Некорректный аккаунт", show_alert=True)
        return
    await _handle_human_gate_action(
        callback,
        account_id=int(raw_id),
        db_user=db_user,
        endpoint="/v1/accounts/{phone}/channel-draft",
        success_notice="Черновик канала готов",
        busy_notice="Не удалось сгенерировать черновик канала",
        payload={"actor": "bot_channel_draft"},
    )


@router.callback_query(F.data.startswith("acc_channel_apply:"))
async def cb_acc_channel_apply(callback: CallbackQuery, db_user: User = None):
    raw_id = _parse_callback_arg(callback.data, "acc_channel_apply:")
    if not raw_id or not raw_id.isdigit():
        await callback.answer("Некорректный аккаунт", show_alert=True)
        return
    await _handle_human_gate_action(
        callback,
        account_id=int(raw_id),
        db_user=db_user,
        endpoint="/v1/accounts/{phone}/channel-apply",
        success_notice="Канал применён",
        busy_notice="Не удалось применить канал",
        payload={"actor": "bot_channel_apply"},
    )


@router.callback_query(F.data.startswith("acc_content_draft:"))
async def cb_acc_content_draft(callback: CallbackQuery, db_user: User = None):
    raw_id = _parse_callback_arg(callback.data, "acc_content_draft:")
    if not raw_id or not raw_id.isdigit():
        await callback.answer("Некорректный аккаунт", show_alert=True)
        return
    await _handle_human_gate_action(
        callback,
        account_id=int(raw_id),
        db_user=db_user,
        endpoint="/v1/accounts/{phone}/content-draft",
        success_notice="Черновик поста готов",
        busy_notice="Не удалось сгенерировать черновик поста",
        payload={"actor": "bot_content_draft"},
    )


@router.callback_query(F.data.startswith("acc_content_apply:"))
async def cb_acc_content_apply(callback: CallbackQuery, db_user: User = None):
    raw_id = _parse_callback_arg(callback.data, "acc_content_apply:")
    if not raw_id or not raw_id.isdigit():
        await callback.answer("Некорректный аккаунт", show_alert=True)
        return
    await _handle_human_gate_action(
        callback,
        account_id=int(raw_id),
        db_user=db_user,
        endpoint="/v1/accounts/{phone}/content-apply",
        success_notice="Пост применён",
        busy_notice="Не удалось применить пост",
        payload={"actor": "bot_content_apply"},
    )


@router.callback_query(F.data.startswith("acc_role_menu:"))
async def cb_acc_role_menu(callback: CallbackQuery, db_user: User = None):
    raw_id = _parse_callback_arg(callback.data, "acc_role_menu:")
    if not raw_id or not raw_id.isdigit():
        await callback.answer("Некорректный аккаунт", show_alert=True)
        return
    account = await _load_account_by_id(int(raw_id))
    if account is None or not _is_owner_or_admin(db_user, account):
        await callback.answer("Аккаунт не найден", show_alert=True)
        return
    await callback.answer()
    await callback.message.edit_text(
        "🎯 <b>Назначить роль аккаунта</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📱 <code>{escape(account.phone)}</code>\n"
        f"Текущая роль: <b>{escape(_friendly_account_role_label(getattr(account, 'account_role', '')))}</b>\n\n"
        "Выберите, как использовать аккаунт дальше.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💬 Кандидат для работы", callback_data=f"acc_role_assign:{account.id}:comment_candidate")],
            [InlineKeyboardButton(text="🔍 Использовать для поиска", callback_data=f"acc_role_assign:{account.id}:parser_candidate")],
            [InlineKeyboardButton(text="✅ Перевести в рабочий пул", callback_data=f"acc_role_assign:{account.id}:execution_ready")],
            [InlineKeyboardButton(text="⚠️ Требует внимания", callback_data=f"acc_role_assign:{account.id}:needs_attention")],
            [InlineKeyboardButton(text="◀️ К настройке", callback_data=f"acc_onboarding_open:{account.id}")],
        ]),
    )


@router.callback_query(F.data.startswith("acc_role_assign:"))
async def cb_acc_role_assign(callback: CallbackQuery, db_user: User = None):
    payload = _parse_callback_arg(callback.data, "acc_role_assign:") or ""
    parts = payload.split(":")
    if len(parts) != 2 or not parts[0].isdigit():
        await callback.answer("Некорректная роль", show_alert=True)
        return
    account_id = int(parts[0])
    role = parts[1]
    await _handle_human_gate_action(
        callback,
        account_id=account_id,
        db_user=db_user,
        endpoint="/v1/accounts/{phone}/assign-role",
        success_notice="Роль обновлена",
        busy_notice="Не удалось назначить роль",
        payload={"role": role, "actor": "bot_assign_role"},
    )


@router.callback_query(F.data == "acc_connect_all")
async def cb_acc_connect(callback: CallbackQuery):
    if settings.DISTRIBUTED_QUEUE_MODE:
        await callback.answer("Подключение выполняется автоматически", show_alert=True)
        await callback.message.edit_text(
            "🔌 <b>Подключение аккаунтов</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "Этот шаг выполняется сервисом автоматически.\n"
            "Просто вернитесь в список аккаунтов и проверьте доступ.",
            parse_mode=ParseMode.HTML,
            reply_markup=accounts_kb(),
        )
        return

    await callback.answer("🔌 Подключение...")
    await callback.message.edit_text("⏳ Подключаю аккаунты...")

    results = await account_mgr.connect_all()
    connected = sum(1 for status in results.values() if status == "connected")
    failed = sum(1 for status in results.values() if status != "connected")
    if not results:
        text = (
            "🔌 <b>Подключение аккаунтов</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "<i>Нет аккаунтов в базе.</i>"
        )
    else:
        text = (
            "🔌 <b>Подключение аккаунтов</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Подключено: <b>{connected}</b>\n"
            f"Ошибок: <b>{failed}</b>"
        )

    await callback.message.edit_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=accounts_kb(),
    )


@router.callback_query(F.data == "acc_health")
async def cb_acc_health(callback: CallbackQuery, db_user: User = None):
    await callback.answer()
    accounts = await account_mgr.load_accounts(user_id=_tenant_read_scope_user_id(db_user))
    if not accounts:
        await callback.message.edit_text(
            "🔐 <b>Проверить доступ</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "<i>Нет аккаунтов для проверки.</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=accounts_kb(),
        )
        return

    rows: list[list[InlineKeyboardButton]] = []
    for account in sorted(accounts, key=lambda acc: ((acc.lifecycle_stage != "uploaded"), -(acc.id or 0)))[:10]:
        rows.append([
            InlineKeyboardButton(
                text=f"🔎 {account.phone} • {_friendly_health_label(account.health_status)}",
                callback_data=f"acc_health_pick:{account.id}",
            )
        ])
    rows.extend([
        [InlineKeyboardButton(text="✍️ Ввести номер", callback_data="acc_health_phone_prompt")],
        [InlineKeyboardButton(text="🧾 Полный аудит аккаунтов", callback_data="acc_health_all")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_accounts")],
    ])
    await callback.message.edit_text(
        "🔐 <b>Проверить доступ</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "Для пошаговой настройки выберите один аккаунт.\n"
        "Если нужен общий пересчёт статусов по всему пулу, запустите полный аудит отдельно.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(F.data == "acc_health_phone_prompt")
async def cb_acc_health_phone_prompt(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(AccountFlowStates.waiting_health_phone)
    await callback.message.edit_text(
        "🔐 <b>Проверить один аккаунт</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "Введите номер телефона аккаунта.\n"
        "Формат: <code>+79991234567</code>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="acc_health")],
        ]),
    )


@router.message(AccountFlowStates.waiting_health_phone, F.text)
async def process_account_health_phone(message: Message, state: FSMContext, db_user: User = None):
    phone = _extract_first_phone_from_text(message.text or "")
    if not phone:
        await message.answer("Введите корректный номер, пример: +79991234567")
        return
    await state.clear()
    await _run_single_account_health_check(message, phone=phone, db_user=db_user)


async def _run_single_account_health_check(
    message_or_callback: Message | CallbackQuery,
    *,
    phone: str,
    db_user: User | None,
    account_id: int | None = None,
):
    normalized_phone = _normalize_phone(phone)
    if not normalized_phone:
        if isinstance(message_or_callback, CallbackQuery):
            await message_or_callback.answer("Некорректный номер", show_alert=True)
        else:
            await message_or_callback.answer("Введите корректный номер, пример: +79991234567")
        return

    async with async_session() as session:
        query = select(Account).where(Account.phone == normalized_phone)
        if db_user is not None and not db_user.is_admin:
            query = query.where(Account.user_id == db_user.id)
        result = await session.execute(query)
        account = result.scalar_one_or_none()

    if account is None:
        text = (
            "❌ <b>Аккаунт не найден</b>\n\n"
            f"Телефон: <code>{escape(normalized_phone)}</code>\n"
            "Сначала загрузите файлы и обновите список аккаунтов."
        )
        if isinstance(message_or_callback, CallbackQuery):
            await message_or_callback.answer("Аккаунт не найден", show_alert=True)
            await message_or_callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=accounts_kb())
        else:
            await message_or_callback.answer(text, parse_mode=ParseMode.HTML, reply_markup=accounts_kb())
        return

    target_message = message_or_callback.message if isinstance(message_or_callback, CallbackQuery) else message_or_callback
    await target_message.answer(
        f"⏳ Проверяю доступ для <code>{escape(normalized_phone)}</code>...",
        parse_mode=ParseMode.HTML,
    )
    try:
        report = await ops_api_post(
            f"/v1/accounts/{quote(normalized_phone, safe='')}/auth-refresh",
            {
                "user_id": _tenant_write_scope_user_id(db_user),
                "actor": "bot_acc_health_one",
                "source": "bot",
                "channel": "bot",
                "notes": "Проверка доступа для одного аккаунта из onboarding flow",
            },
            timeout=45,
        )
    except Exception:
        await target_message.answer(
            "❌ <b>Не удалось проверить аккаунт</b>\n\n"
            "Сервис временно недоступен. Попробуйте ещё раз чуть позже.",
            parse_mode=ParseMode.HTML,
            reply_markup=accounts_kb(),
        )
        return

    refreshed = await _load_account_by_id(account.id if account_id is None else account_id)
    if refreshed is None:
        await target_message.answer(
            "❌ <b>Не удалось обновить данные аккаунта</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=accounts_kb(),
        )
        return
    onboarding = await _fetch_onboarding_status(refreshed.phone, db_user=db_user, limit_steps=8)
    proxy_preflight = dict(report.get("proxy_preflight") or {})
    proxy_pool = dict(proxy_preflight.get("pool") or {})
    notice_lines = [
        "Последняя проверка: "
        f"<b>{escape(_friendly_probe_status(bool((report.get('results') or [{}])[0].get('authorized')), str((report.get('results') or [{}])[0].get('probe_status') or 'unknown')))}</b>"
    ]
    if proxy_preflight:
        proxy_action = str(proxy_preflight.get("action") or "")
        proxy_reason = str(proxy_preflight.get("reason") or "")
        if proxy_action == "kept":
            notice_lines.append("Прокси: <b>живой и сохранён за аккаунтом</b>")
        elif proxy_action == "rebound":
            notice_lines.append("Прокси: <b>заменён на живой уникальный</b>")
        elif proxy_action == "missing":
            notice_lines.append("Прокси: <b>не удалось подобрать живой</b>")
        elif proxy_reason:
            notice_lines.append(f"Прокси: <b>{escape(proxy_reason)}</b>")
    conflicts = list(report.get("api_credential_conflicts") or [])
    if conflicts:
        notice_lines.append(
            "⚠️ Та же пара app_id/app_hash уже используется: "
            f"<b>{escape(', '.join(conflicts))}</b>"
        )
    if proxy_pool.get("low_stock"):
        notice_lines.append(
            "⚠️ Свободных прокси мало. "
            f"Загрузите ещё <b>{int(proxy_pool.get('recommended_topup', 0))}</b>."
        )
    detail = _render_onboarding_detail(
        _build_account_recovery_report(refreshed),
        onboarding,
        notice="\n".join(notice_lines),
    )
    await target_message.answer(
        detail,
        parse_mode=ParseMode.HTML,
        reply_markup=_onboarding_open_kb(refreshed.id, report=_build_account_recovery_report(refreshed), run=(onboarding or {}).get("run")),
    )


@router.callback_query(F.data.startswith("acc_health_pick:"))
async def cb_acc_health_pick(callback: CallbackQuery, db_user: User = None):
    raw_id = _parse_callback_arg(callback.data, "acc_health_pick:")
    if not raw_id or not raw_id.isdigit():
        await callback.answer("Некорректный аккаунт", show_alert=True)
        return
    account = await _load_account_by_id(int(raw_id))
    if not account or not _is_owner_or_admin(db_user, account):
        await callback.answer("Аккаунт не найден", show_alert=True)
        return
    await callback.answer("Проверяю доступ...")
    await _run_single_account_health_check(callback, phone=account.phone, db_user=db_user, account_id=account.id)


@router.callback_query(F.data == "acc_health_all")
async def cb_acc_health_all(callback: CallbackQuery, db_user: User = None):
    await callback.answer("🧾 Запускаю полный аудит...")
    await callback.message.edit_text(
        "🧾 <b>Полный аудит аккаунтов</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "⏳ Проверяю весь пул и обновляю статусы...",
        parse_mode=ParseMode.HTML,
    )

    try:
        report = await ops_api_post(
            "/v1/accounts/auth-refresh-all",
            {
                "user_id": _tenant_write_scope_user_id(db_user),
                "set_parser_first_authorized": True,
                "clear_worker_claims": True,
            },
            timeout=180,
        )
    except Exception:
        await callback.message.edit_text(
            "❌ <b>Не удалось обновить состояние аккаунтов</b>\n\n"
            "Сервис проверки временно недоступен. Попробуйте ещё раз чуть позже.",
            parse_mode=ParseMode.HTML,
            reply_markup=accounts_kb(),
        )
        return

    if int(report.get("count", 0)) == 0:
        await callback.message.edit_text(
            "🧾 <b>Полный аудит аккаунтов</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "<i>Нет аккаунтов для проверки.</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=accounts_kb(),
        )
        return

    lines: list[str] = []
    results = list(report.get("results") or [])
    for idx, item in enumerate(results[:30], start=1):
        ok = bool(item.get("authorized"))
        probe_status = str(item.get("probe_status") or "unknown")
        user_status = _friendly_probe_status(ok, probe_status)
        icon = "✅" if ok else ("❌" if user_status == "Нужен повторный вход" else "⚠️")
        phone = escape(str(item.get("phone") or "unknown"))
        lines.append(f"{idx}. {icon} <code>{phone}</code> — <b>{escape(user_status)}</b>")

    header = (
        "🧾 <b>Полный аудит аккаунтов</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Проверено: <b>{int(report.get('count', 0))}</b>\n"
        f"✅ Доступ есть: <b>{int(report.get('authorized', 0))}</b>\n"
        f"❌ Нужен повторный вход: <b>{int(report.get('unauthorized_like', 0))}</b>\n"
        f"🔄 Обновлено после проверки: <b>{int(report.get('reactivated_authorized', 0))}</b>\n\n"
    )

    body = "\n".join(lines)
    if len(results) > 30:
        body += f"\n\n<i>Показаны первые 30 из {len(results)}.</i>"
    if report.get("parser_reassigned"):
        body += (
            "\n\n"
            f"🧩 Аккаунт для поиска обновлён: <code>{escape(str(report.get('parser_phone_after') or ''))}</code>"
        )

    await callback.message.edit_text(
        header + body,
        parse_mode=ParseMode.HTML,
        reply_markup=accounts_kb(),
    )


@router.callback_query(F.data == "acc_audit")
async def cb_acc_audit(callback: CallbackQuery, db_user: User = None):
    await callback.answer()
    payload = await _fetch_account_audit_payload(db_user=db_user)
    await callback.message.edit_text(
        _render_account_audit(payload),
        parse_mode=ParseMode.HTML,
        reply_markup=_account_audit_kb(list(payload.get("items") or [])),
    )


@router.callback_query(F.data.startswith("acc_audit_pick:"))
async def cb_acc_audit_pick(callback: CallbackQuery, db_user: User = None):
    raw_id = _parse_callback_arg(callback.data, "acc_audit_pick:")
    if not raw_id or not raw_id.isdigit():
        await callback.answer("Некорректный аккаунт", show_alert=True)
        return
    payload = await _fetch_account_audit_payload(db_user=db_user)
    items = list(payload.get("items") or [])
    item = next((row for row in items if int(row.get("id") or 0) == int(raw_id)), None)
    if item is None:
        await callback.answer("Аккаунт не найден", show_alert=True)
        return
    await callback.answer()
    await callback.message.edit_text(
        _render_account_audit_detail(item),
        parse_mode=ParseMode.HTML,
        reply_markup=_account_audit_detail_kb(item),
    )


@router.callback_query(F.data == "acc_audit_reconcile")
async def cb_acc_audit_reconcile(callback: CallbackQuery, db_user: User = None):
    await callback.answer("🧹 Исправляю этапы...")
    try:
        payload = await ops_api_post(
            "/v1/accounts/reconcile-lifecycle",
            {
                "user_id": _tenant_write_scope_user_id(db_user),
                "actor": "bot_acc_audit_reconcile",
                "dry_run": False,
            },
            timeout=30,
        ) if settings.OPS_API_URL else None
    except Exception as exc:
        log.warning(f"Не удалось выполнить reconcile через ops-api: {exc}")
        payload = None
    if payload is None:
        from core.account_audit import reconcile_stale_lifecycle

        payload = await reconcile_stale_lifecycle(
            user_id=_tenant_write_scope_user_id(db_user),
            actor="bot_acc_audit_reconcile",
            dry_run=False,
        )
    lines = [
        "🧹 <b>Исправление этапов</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        f"Проверено: <b>{int(payload.get('scanned', 0))}</b>",
        f"Исправлено: <b>{int(payload.get('repaired', 0))}</b>",
        f"Без изменений: <b>{int(payload.get('skipped', 0))}</b>",
    ]
    for item in list(payload.get("items") or [])[:10]:
        lines.append(
            f"<code>{escape(str(item.get('phone') or ''))}</code> — "
            f"{escape(str(item.get('from_stage') or ''))} → <b>{escape(str(item.get('to_stage') or ''))}</b>"
        )
    await callback.message.edit_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🧾 Открыть аудит", callback_data="acc_audit")],
                [InlineKeyboardButton(text="◀️ Назад", callback_data="back_accounts")],
            ]
        ),
    )


@router.callback_query(F.data == "acc_api_audit")
async def cb_acc_api_audit(callback: CallbackQuery, db_user: User = None):
    await callback.answer()
    payload = await _fetch_json_credentials_audit_payload(db_user=db_user)
    await callback.message.edit_text(
        _render_json_credentials_audit(payload),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🧾 Аудит аккаунтов", callback_data="acc_audit")],
                [InlineKeyboardButton(text="◀️ Назад", callback_data="back_accounts")],
            ]
        ),
    )


@router.callback_query(F.data == "acc_session_audit")
async def cb_acc_session_audit(callback: CallbackQuery, db_user: User = None):
    await callback.answer()
    payload = await _fetch_session_topology_audit_payload(db_user=db_user)
    await callback.message.edit_text(
        _render_session_topology_audit(payload),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🧹 Убрать старые копии", callback_data="acc_session_quarantine")],
                [InlineKeyboardButton(text="🧾 Аудит аккаунтов", callback_data="acc_audit")],
                [InlineKeyboardButton(text="◀️ Назад", callback_data="back_accounts")],
            ]
        ),
    )


@router.callback_query(F.data == "acc_session_quarantine")
async def cb_acc_session_quarantine(callback: CallbackQuery, db_user: User = None):
    await callback.answer("🧹 Переношу старые копии...")
    payload: dict | None = None
    if settings.OPS_API_URL:
        try:
            payload = await ops_api_post(
                "/v1/audit/sessions/quarantine",
                {
                    "user_id": _tenant_write_scope_user_id(db_user),
                    "dry_run": False,
                },
                timeout=30,
            )
        except Exception as exc:
            log.warning(f"Не удалось quarantine через ops-api: {exc}")
    if payload is None:
        from core.account_audit import quarantine_session_duplicates

        payload = await quarantine_session_duplicates(
            user_id=_tenant_write_scope_user_id(db_user),
            dry_run=False,
        )
    lines = [
        "🧹 <b>Старые копии перенесены</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        f"Перемещено файлов: <b>{int(payload.get('moved_files', 0))}</b>",
        f"Телефонов затронуто: <b>{len(list(payload.get('moved_phones') or []))}</b>",
        f"Пропущено: <b>{int(payload.get('skipped_count', 0))}</b>",
    ]
    if payload.get("quarantine_dir"):
        lines.append(f"Папка карантина: <code>{escape(str(payload.get('quarantine_dir')))}</code>")
    await callback.message.edit_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🗂 Обновить аудит файлов", callback_data="acc_session_audit")],
                [InlineKeyboardButton(text="◀️ Назад", callback_data="back_accounts")],
            ]
        ),
    )


@router.callback_query(F.data == "acc_reset_prompt")
async def cb_acc_reset_prompt(callback: CallbackQuery):
    await callback.answer()
    await callback.message.edit_text(
        "🧹 <b>Начать с нуля</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "Будут очищены аккаунты, каналы, кандидаты поиска,\n"
        "очереди задач и текущие session/json из рабочего пула.\n\n"
        "Сохранятся логи, quarantine и архив snapshot,\n"
        "чтобы можно было вернуться к диагностике.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="✅ Очистить user-state", callback_data="acc_reset_confirm")],
                [InlineKeyboardButton(text="◀️ Назад", callback_data="back_accounts")],
            ]
        ),
    )


@router.callback_query(F.data == "acc_reset_confirm")
async def cb_acc_reset_confirm(callback: CallbackQuery, db_user: User = None):
    await callback.answer("🧹 Очищаю user-state...")
    try:
        payload = await ops_api_post(
            "/v1/reset/user-state",
            {
                "user_id": _tenant_write_scope_user_id(db_user),
                "actor": "bot_acc_reset_confirm",
                "dry_run": False,
            },
            timeout=120,
        )
    except Exception:
        await callback.message.edit_text(
            "❌ <b>Не удалось очистить состояние</b>\n\n"
            "Сервис временно недоступен. Попробуйте ещё раз позже.",
            parse_mode=ParseMode.HTML,
            reply_markup=accounts_kb(),
        )
        return

    deleted = dict(payload.get("deleted") or {})
    sessions = dict(payload.get("session_archive") or {})
    runtime = dict(payload.get("runtime") or {})
    lines = [
        "🧹 <b>Состояние очищено</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        f"Аккаунтов удалено: <b>{int(deleted.get('accounts', 0))}</b>",
        f"Каналов удалено: <b>{int(deleted.get('channels', 0))}</b>",
        f"Файлов перенесено из рабочего пула: <b>{int(sessions.get('moved_files', 0))}</b>",
        f"Claims очищено: <b>{int(runtime.get('claims_cleared', 0))}</b>",
    ]
    if payload.get("archive_path"):
        lines.append(f"Архив snapshot: <code>{escape(str(payload.get('archive_path')))}</code>")
    await callback.message.edit_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=accounts_kb(),
    )


@router.callback_query(F.data == "acc_recovery_queue")
async def cb_acc_recovery_queue(callback: CallbackQuery, db_user: User = None):
    await callback.answer()
    reports = await _load_account_recovery_reports(db_user, filter_name="priority")
    await callback.message.edit_text(
        _render_manual_recovery_queue(reports, filter_name="priority"),
        parse_mode=ParseMode.HTML,
        reply_markup=_manual_recovery_queue_kb(reports, filter_name="priority"),
    )


@router.callback_query(F.data.startswith("acc_recovery_filter:"))
async def cb_acc_recovery_filter(callback: CallbackQuery, db_user: User = None):
    filter_name = _parse_callback_arg(callback.data, "acc_recovery_filter:") or "priority"
    if filter_name not in _RECOVERY_FILTER_LABELS:
        filter_name = "priority"
    await callback.answer()
    reports = await _load_account_recovery_reports(db_user, filter_name=filter_name)
    await callback.message.edit_text(
        _render_manual_recovery_queue(reports, filter_name=filter_name),
        parse_mode=ParseMode.HTML,
        reply_markup=_manual_recovery_queue_kb(reports, filter_name=filter_name),
    )


@router.callback_query(F.data.startswith("acc_recovery_pick:"))
async def cb_acc_recovery_pick(callback: CallbackQuery, db_user: User = None):
    payload = _parse_callback_arg(callback.data, "acc_recovery_pick:") or ""
    filter_name = "priority"
    raw_id = payload
    if ":" in payload:
        maybe_filter, maybe_id = payload.split(":", 1)
        if maybe_filter in _RECOVERY_FILTER_LABELS:
            filter_name = maybe_filter
            raw_id = maybe_id
    if raw_id is None or not raw_id.isdigit():
        await callback.answer("Некорректный account id", show_alert=True)
        return

    async with async_session() as session:
        query = select(Account).where(Account.id == int(raw_id))
        scope_user_id = _tenant_read_scope_user_id(db_user)
        if scope_user_id is not None:
            query = query.where(Account.user_id == scope_user_id)
        result = await session.execute(query)
        account = result.scalar_one_or_none()

    if account is None:
        await callback.answer("Аккаунт не найден", show_alert=True)
        return

    report = _build_account_recovery_report(account)
    await callback.answer()
    await callback.message.edit_text(
        _render_account_recovery_detail(report),
        parse_mode=ParseMode.HTML,
        reply_markup=_account_recovery_detail_kb(report, filter_name=filter_name),
    )


@router.callback_query(F.data.startswith("acc_recovery_package:"))
async def cb_acc_recovery_package(callback: CallbackQuery, db_user: User = None):
    payload = _parse_callback_arg(callback.data, "acc_recovery_package:") or ""
    filter_name = "priority"
    raw_id = payload
    if ":" in payload:
        maybe_filter, maybe_id = payload.split(":", 1)
        if maybe_filter in _RECOVERY_FILTER_LABELS:
            filter_name = maybe_filter
            raw_id = maybe_id
    if raw_id is None or not raw_id.isdigit():
        await callback.answer("Некорректный account id", show_alert=True)
        return

    async with async_session() as session:
        query = select(Account).where(Account.id == int(raw_id))
        if not (db_user and db_user.is_admin):
            query = query.where(Account.user_id == (db_user.id if db_user else None))
        result = await session.execute(query)
        account = result.scalar_one_or_none()

        if account is None:
            await callback.answer("Аккаунт не найден", show_alert=True)
            return

        report = _build_account_recovery_report(account)
        if not report["package_ready"]:
            await callback.answer("Аккаунт ещё не готов к первому черновику", show_alert=True)
            await callback.message.edit_text(
                _render_account_recovery_detail(report),
                parse_mode=ParseMode.HTML,
                reply_markup=_account_recovery_detail_kb(report, filter_name=filter_name),
            )
            return

        try:
            await ops_api_post(
                f"/v1/accounts/{quote(account.phone, safe='')}/profile-draft",
                {
                    "user_id": _tenant_write_scope_user_id(db_user),
                    "source": "bot",
                    "channel": "bot",
                    "actor": "bot_recovery_profile_draft",
                },
            )
        except Exception as exc:
            await callback.answer("Не удалось сгенерировать черновик", show_alert=True)
            await callback.message.edit_text(
                "❌ <b>Не удалось сгенерировать черновик</b>\n\n"
                + _friendly_packaging_error(exc),
                parse_mode=ParseMode.HTML,
                reply_markup=accounts_kb(),
            )
            return

    refreshed = await _load_account_by_id(account.id) or account
    report = _build_account_recovery_report(refreshed)
    await callback.answer("Черновик профиля готов")
    await callback.message.edit_text(
        _render_account_recovery_detail(report)
        + "\n\n"
        + "Черновик профиля готов. Откройте пошаговую настройку и подтвердите применение.",
        parse_mode=ParseMode.HTML,
        reply_markup=_account_recovery_detail_kb(report, filter_name=filter_name),
    )


def _normalize_phone(raw_phone: str) -> str:
    digits = "".join(ch for ch in raw_phone if ch.isdigit())
    return f"+{digits}" if digits else ""


def _extract_phone_digits_from_file_name(file_name: str, suffix: str) -> str:
    """Extract only phone digits from file names like 79990001122.session/json."""
    if not file_name.lower().endswith(suffix.lower()):
        return ""
    stem = file_name[: -len(suffix)]
    return "".join(ch for ch in stem if ch.isdigit())


def _extract_first_phone_from_text(text: str) -> str:
    """Accept noisy admin input and pick first valid phone-looking token."""
    for token in (text or "").replace("\n", " ").split():
        normalized = _normalize_phone(token)
        if len(normalized) >= 11:
            return normalized
    return _normalize_phone(text or "")


def _bundle_status_label(bundle) -> str:
    if bundle.ready:
        return "комплект готов"
    if bundle.session_present:
        return "ждёт .json"
    if bundle.metadata_present:
        return "ждёт .session"
    return "файлы не загружены"


def _bundle_next_step_text(bundle) -> str:
    if bundle.ready:
        return "Дальше откройте «Аккаунты -> Начать настройку аккаунта» или «Проверить доступ». Пока проверка не выполнена, аккаунт не используется."
    if bundle.session_present:
        return "Добавьте файл .json с тем же номером телефона. До полного комплекта аккаунт не будет использоваться."
    if bundle.metadata_present:
        return "Добавьте файл .session с тем же номером телефона. До полного комплекта аккаунт не будет использоваться."
    return "Загрузите оба файла аккаунта: .session и .json."


def _friendly_packaging_error(exc: Exception) -> str:
    message = str(exc)
    if message == "human_gated_packaging_enabled":
        return "Старая очередь подготовки выключена. Используйте черновики Gemini и ручное подтверждение."
    if message == "metadata_missing":
        return "Для подготовки не хватает файла .json с тем же номером телефона."
    if message == "metadata_api_credentials_missing":
        return "В .json не хватает собственного app_id/app_hash для этого аккаунта."
    if message == "account_not_found":
        return "Аккаунт не найден в списке. Сначала обновите список аккаунтов."
    if message.endswith("_draft_not_found"):
        return "Сначала сгенерируйте черновик на этом шаге."
    if message == "channel_apply_required_first":
        return "Сначала подтвердите канал, а потом переходите к закреплённому посту."
    if message.endswith("_connect_failed"):
        return "Не удалось открыть безопасное подключение к аккаунту. Сначала повторите проверку доступа."
    if message == "already_packaging":
        return "Этот аккаунт уже находится в подготовке."
    if message == "health_restricted":
        return "Аккаунт ответил с ограничением во время подготовки. Сейчас его нельзя отправлять дальше."
    if message == "health_frozen":
        return "Аккаунт заморожен во время проверки подготовки. Нужна ручная перепроверка."
    if message == "health_expired":
        return "Во время подготовки сессия перестала быть валидной. Нужен повторный вход."
    if message.startswith("stage_"):
        stage_name = message.removeprefix("stage_") or "unknown"
        return f"Сейчас аккаунт находится на этапе {stage_name} и не может начать подготовку."
    if message.startswith("status_"):
        status_name = message.removeprefix("status_") or "unknown"
        return f"Сейчас служебный статус аккаунта: {status_name}. Подготовка недоступна."
    if message == "invalid_role":
        return "Эта роль аккаунта сейчас недоступна."
    return "Попробуйте ещё раз чуть позже."


def _friendly_account_role_label(role: str | None) -> str:
    mapping = {
        "parser_candidate": "Кандидат для поиска",
        "parser_active": "Используется для поиска",
        "comment_candidate": "Кандидат для работы",
        "execution_ready": "Рабочий пул",
        "needs_attention": "Требует внимания",
    }
    value = str(role or "").strip()
    return mapping.get(value, value.replace("_", " ") if value else "Роль не выбрана")


def _friendly_onboarding_step_label(step_key: str | None) -> str:
    mapping = {
        "start": "Старт настройки",
        "upload_session": "Загружен .session",
        "upload_metadata": "Загружен .json",
        "upload_bundle": "Комплект файлов собран",
        "auth_check": "Проверка доступа",
        "profile_draft_generated": "Черновик профиля готов",
        "profile_applied": "Профиль подтверждён",
        "profile_apply_failed": "Подтверждение профиля остановлено",
        "channel_draft_generated": "Черновик канала готов",
        "channel_applied": "Канал подтверждён",
        "channel_apply_failed": "Подтверждение канала остановлено",
        "content_draft_generated": "Черновик поста готов",
        "content_applied": "Пост подтверждён",
        "content_apply_failed": "Подтверждение поста остановлено",
        "role_assigned": "Роль назначена",
        "comment_draft_generated": "Черновик комментария готов",
        "comment_reviewed": "Комментарий проверен",
        "comment_approved": "Комментарий подтверждён",
        "packaging_queued": "Подготовка поставлена в очередь",
        "profile_ready": "Профиль готов",
        "channel_ready": "Канал готов",
        "content_ready": "Контент готов",
        "subscriptions_ready": "Подписки готовы",
        "gate_review_requested": "Отправлено на подтверждение",
        "returned_to_warmup": "Возврат на прогрев",
        "final_ready": "Аккаунт готов",
    }
    step = str(step_key or "").strip()
    if not step:
        return "Шаг не записан"
    return mapping.get(step, step.replace("_", " "))


async def _safe_onboarding_step_post(
    phone: str,
    *,
    db_user: User | None,
    step_key: str,
    actor: str,
    source: str = "bot",
    channel: str = "bot",
    result: str = "ok",
    notes: str = "",
    payload: dict | None = None,
    run_status: str | None = None,
) -> None:
    if not settings.OPS_API_URL:
        return
    try:
        await ops_api_post(
            f"/v1/accounts/{quote(phone, safe='')}/onboarding/step",
            {
                "user_id": _tenant_write_scope_user_id(db_user),
                "step_key": step_key,
                "actor": actor,
                "source": source,
                "channel": channel,
                "result": result,
                "notes": notes,
                "payload": payload or {},
                "run_status": run_status,
            },
            timeout=30,
        )
    except Exception as exc:
        exc_text = str(exc).strip()
        if exc_text == "account_not_found" and step_key in {"upload_metadata", "upload_bundle"}:
            log.info(
                "Пропускаю ранний onboarding step %s для %s до появления account row в БД",
                step_key,
                phone,
            )
            return
        log.warning(f"Не удалось записать onboarding step {step_key} для {phone}: {exc}")


async def _fetch_onboarding_status(phone: str, *, db_user: User | None, limit_steps: int = 10) -> dict | None:
    if not settings.OPS_API_URL:
        return None
    query = f"user_id={_tenant_write_scope_user_id(db_user) or ''}&limit_steps={max(1, int(limit_steps))}"
    try:
        return await ops_api_get(f"/v1/accounts/{quote(phone, safe='')}/onboarding?{query}", timeout=20)
    except Exception as exc:
        log.warning(f"Не удалось получить onboarding status для {phone}: {exc}")
        return None


async def _fetch_onboarding_runs(
    *,
    db_user: User | None,
    status: str | None = None,
    limit: int = 20,
) -> list[dict]:
    if not settings.OPS_API_URL:
        return []
    query_parts = [f"user_id={_tenant_write_scope_user_id(db_user) or ''}", f"limit={max(1, int(limit))}"]
    if status:
        query_parts.append(f"status={quote(status, safe='')}")
    try:
        payload = await ops_api_get(f"/v1/onboarding/runs?{'&'.join(query_parts)}", timeout=20)
    except Exception as exc:
        log.warning(f"Не удалось получить список onboarding runs: {exc}")
        return []
    return list(payload.get("items") or [])


def _onboarding_open_kb(account_id: int, *, report: dict, run: dict | None) -> InlineKeyboardMarkup:
    account = report["account"]
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text="🔐 Проверить доступ", callback_data=f"acc_health_pick:{account.id}")],
    ]
    stage = str(account.lifecycle_stage or "uploaded")
    if report["package_ready"] or stage in {"auth_verified", "profile_draft", "profile_applied", "channel_draft", "channel_applied", "content_draft", "content_applied", "execution_ready", "active_commenting"}:
        if stage in {"uploaded", "auth_verified", "packaging_error"}:
            rows.append([InlineKeyboardButton(text="🎨 Подготовить профиль", callback_data=f"acc_profile_draft:{account.id}")])
    elif stage in {"uploaded", "packaging_error"}:
        rows.append([InlineKeyboardButton(text="🔐 Сначала проверить доступ", callback_data=f"acc_health_pick:{account.id}")])
    if stage == "profile_draft":
        rows.append([InlineKeyboardButton(text="✅ Подтвердить профиль", callback_data=f"acc_profile_apply:{account.id}")])
        rows.append([InlineKeyboardButton(text="🔁 Показать новые варианты профиля", callback_data=f"acc_profile_draft:{account.id}")])
    if stage == "profile_applied":
        rows.append([InlineKeyboardButton(text="📣 Подготовить канал", callback_data=f"acc_channel_draft:{account.id}")])
    if stage == "channel_draft":
        rows.append([InlineKeyboardButton(text="✅ Подтвердить канал", callback_data=f"acc_channel_apply:{account.id}")])
        rows.append([InlineKeyboardButton(text="🔁 Показать новые варианты канала", callback_data=f"acc_channel_draft:{account.id}")])
    if stage == "channel_applied":
        rows.append([InlineKeyboardButton(text="📝 Подготовить пост", callback_data=f"acc_content_draft:{account.id}")])
    if stage == "content_draft":
        rows.append([InlineKeyboardButton(text="✅ Подтвердить пост", callback_data=f"acc_content_apply:{account.id}")])
        rows.append([InlineKeyboardButton(text="🔁 Показать новые варианты поста", callback_data=f"acc_content_draft:{account.id}")])
    if stage in {"content_applied", "execution_ready", "active_commenting"}:
        rows.append([InlineKeyboardButton(text="🎯 Назначить роль аккаунта", callback_data=f"acc_role_menu:{account.id}")])
    rows.extend([
        [InlineKeyboardButton(text="📘 Обновить экран", callback_data=f"acc_onboarding_open:{account.id}")],
        [InlineKeyboardButton(text="📝 История шагов", callback_data="acc_onboarding_history")],
        [InlineKeyboardButton(text="◀️ К аккаунтам", callback_data="back_accounts")],
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _render_onboarding_detail(report: dict, snapshot: dict | None, *, notice: str = "") -> str:
    account = report["account"]
    run = dict(snapshot.get("run") or {}) if snapshot else {}
    steps = list(snapshot.get("steps") or []) if snapshot else []
    lines = [
        "🚀 <b>Настройка аккаунта</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        f"📱 <code>{escape(account.phone)}</code>",
        f"Состояние: <b>{escape(_friendly_account_status(report))}</b>",
        f"Этап: <b>{escape(_friendly_lifecycle_label(account.lifecycle_stage))}</b>",
        f"Проверка доступа: <b>{escape(_friendly_health_label(account.health_status))}</b>",
        f"Роль аккаунта: <b>{escape(_friendly_account_role_label(getattr(account, 'account_role', '')))}</b>",
    ]
    if run:
        lines.extend([
            f"Режим: <b>{escape(str(run.get('mode') or 'bot'))}</b>",
            f"Текущий шаг: <b>{escape(_friendly_onboarding_step_label(run.get('current_step')))}</b>",
            f"Последний результат: <b>{escape(str(run.get('last_result') or 'pending'))}</b>",
        ])
    lines.extend([
        "",
        "Что делать дальше:",
        f"<b>{escape(report['next_step'])}</b>",
        "",
        "Как это работает:",
        "• Gemini сначала готовит черновик",
        "• Telegram-действие выполняется только после вашего подтверждения",
    ])
    if steps:
        lines.extend(["", "Последние шаги:"])
        for step in steps[:8]:
            lines.append(
                f"• <b>{escape(_friendly_onboarding_step_label(step.get('step_key')))}</b> — "
                f"{escape(str(step.get('result') or 'ok'))}"
            )
    if notice:
        lines.extend(["", notice])
    return "\n".join(lines)


def _render_onboarding_runs_list(runs: list[dict], *, title: str) -> str:
    if not runs:
        return (
            f"{title}\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "<i>Пока нет сохранённых шагов настройки.</i>"
        )
    lines = [title, "━━━━━━━━━━━━━━━━━━━━", ""]
    for idx, run in enumerate(runs[:15], start=1):
        lines.append(
            f"{idx}. <code>{escape(str(run.get('phone') or ''))}</code>\n"
            f"   шаг: <b>{escape(_friendly_onboarding_step_label(run.get('current_step')))}</b>\n"
            f"   статус: <b>{escape(str(run.get('status') or 'active'))}</b>"
        )
    if len(runs) > 15:
        lines.extend(["", f"<i>Показаны первые 15 из {len(runs)}.</i>"])
    return "\n".join(lines)


def _onboarding_runs_kb(runs: list[dict], *, back_callback: str = "back_accounts") -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for run in runs[:15]:
        account_id = int(run.get("account_id") or 0)
        phone = str(run.get("phone") or "")
        rows.append([
            InlineKeyboardButton(
                text=f"{phone} • {_friendly_onboarding_step_label(run.get('current_step'))}",
                callback_data=f"acc_onboarding_open:{account_id}",
            )
        ])
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data=back_callback)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _upsert_account_from_session_upload(
    *,
    phone: str,
    session_file: str,
    user_id: int | None,
    reset_runtime_state: bool = False,
) -> tuple[bool, str]:
    """Ensure account row exists for uploaded session file."""
    try:
        async with async_session() as session:
            if user_id is not None:
                _account, state = await shared_upsert_account_from_session_upload(
                    session,
                    phone=phone,
                    session_file=session_file,
                    runtime_user_id=int(user_id),
                    tenant_id=None,
                    workspace_id=None,
                    reset_runtime_state=reset_runtime_state,
                )
                await session.commit()
                if state == "created":
                    return True, "добавлен в БД"
                if state == "updated":
                    return True, "обновлён в БД"
                return True, "уже был в БД"

            result = await session.execute(select(Account).where(Account.phone == phone))
            account = result.scalar_one_or_none()
            if account is None:
                session.add(
                    Account(
                        phone=phone,
                        session_file=session_file,
                        user_id=None,
                        status="active",
                        health_status="unknown",
                        lifecycle_stage="uploaded",
                        restriction_reason=None,
                        created_at=utcnow(),
                    )
                )
                await session.commit()
                return True, "добавлен в БД"

            changed = False
            if account.session_file != session_file:
                account.session_file = session_file
                changed = True
            if account.lifecycle_stage in {"orphaned", "", None}:
                account.lifecycle_stage = "uploaded"
                changed = True
            if reset_runtime_state:
                if account.status != "banned" and account.status != "active":
                    account.status = "active"
                    changed = True
                if account.health_status != "unknown":
                    account.health_status = "unknown"
                    changed = True
                if account.lifecycle_stage != "uploaded":
                    account.lifecycle_stage = "uploaded"
                    changed = True
                if account.restriction_reason is not None:
                    account.restriction_reason = None
                    changed = True
                if account.last_health_check is not None:
                    account.last_health_check = None
                    changed = True
                if account.capabilities_json is not None:
                    account.capabilities_json = None
                    changed = True
            if changed:
                await session.commit()
                return True, "обновлён в БД"
            await session.rollback()
            return True, "уже был в БД"
    except Exception as exc:
        log.exception(f"Failed to upsert account for uploaded session {phone}: {exc}")
        return False, f"ошибка БД ({exc.__class__.__name__})"


async def _sync_accounts_with_sessions(user_id: int | None = None) -> dict:
    """Sync DB accounts with files in data/sessions."""
    async with async_session() as session:
        user_ids_result = await session.execute(select(User.id))
        known_user_ids = [int(row[0]) for row in user_ids_result.fetchall()]

    discovery = discover_session_assets(settings.sessions_path, known_user_ids=known_user_ids)
    discovered = {
        phone: asset
        for phone, asset in discovery.assets.items()
        if asset.source_kind == "canonical" and (user_id is None or asset.user_id == user_id)
    }
    metadata_ok = sum(1 for asset in discovered.values() if asset.metadata_path is not None)

    added = 0
    updated = 0
    orphaned = 0

    async with async_session() as session:
        query = select(Account)
        if user_id is not None:
            query = query.where((Account.user_id == user_id) | (Account.user_id.is_(None)))
        result = await session.execute(query)
        existing_accounts = list(result.scalars().all())
        existing_by_phone = {acc.phone: acc for acc in existing_accounts}
        discovered_session_files = {asset.session_file for asset in discovered.values()}

        for phone, asset in discovered.items():
            session_file = asset.session_file
            existing = existing_by_phone.get(phone)
            if existing is None:
                session.add(
                    Account(
                        phone=phone,
                        session_file=session_file,
                        user_id=asset.user_id or user_id,
                        status="active",
                        lifecycle_stage="uploaded",
                        created_at=utcnow(),
                    )
                )
                added += 1
                continue

            if existing.session_file != session_file:
                existing.session_file = session_file
                updated += 1

            target_user_id = asset.user_id or user_id
            if target_user_id is not None and existing.user_id != target_user_id:
                existing.user_id = target_user_id
                updated += 1

            if existing.lifecycle_stage in {"orphaned", "", "packaging_error"}:
                existing.lifecycle_stage = "uploaded"
                updated += 1

        for account in existing_accounts:
            if account.session_file not in discovered_session_files and account.lifecycle_stage != "orphaned":
                account.lifecycle_stage = "orphaned"
                orphaned += 1

        await session.commit()

    return {
        "sessions_found": len(discovered),
        "metadata_ok": metadata_ok,
        "added": added,
        "updated": updated,
        "orphaned": orphaned,
        "duplicates": len(discovery.duplicates),
    }


def _parse_callback_arg(data: str, prefix: str) -> Optional[str]:
    if not data.startswith(prefix):
        return None
    return data[len(prefix):].strip() or None


def _is_owner_or_admin(db_user: Optional[User], account: Account) -> bool:
    if db_user is None:
        return False
    if db_user.is_admin:
        return True
    return account.user_id == db_user.id


async def _load_account_by_id(account_id: int) -> Optional[Account]:
    async with async_session() as session:
        result = await session.execute(select(Account).where(Account.id == account_id))
        return result.scalar_one_or_none()


async def _log_account_stage_note(account_id: int, *, actor: str, reason: str) -> Optional[Account]:
    async with async_session() as session:
        result = await session.execute(select(Account).where(Account.id == account_id))
        account = result.scalar_one_or_none()
        if account is None:
            return None
        session.add(
            AccountStageEvent(
                account_id=account.id,
                phone=account.phone,
                from_stage=account.lifecycle_stage,
                to_stage=account.lifecycle_stage,
                actor=actor,
                reason=reason,
            )
        )
        await session.commit()
        await session.refresh(account)
        return account


async def _policy_decision_counts(window_days: int) -> dict[str, int]:
    since = utcnow() - timedelta(days=max(1, int(window_days)))
    async with async_session() as session:
        result = await session.execute(
            select(PolicyEvent.decision, func.count())
            .where(PolicyEvent.created_at >= since)
            .group_by(PolicyEvent.decision)
        )
        return {str(decision): int(count) for decision, count in result.all()}


async def _active_quarantine_count() -> int:
    async with async_session() as session:
        count = await session.scalar(
            select(func.count(Account.id)).where(
                Account.quarantined_until.is_not(None),
                Account.quarantined_until > utcnow(),
            )
        )
    return int(count or 0)


@router.callback_query(F.data == "acc_sync_sessions")
async def cb_acc_sync_sessions(callback: CallbackQuery, db_user: User = None):
    await callback.answer("Обновляю список...")
    report = await _sync_accounts_with_sessions(user_id=db_user.id if db_user else None)
    await callback.message.edit_text(
        "🔄 <b>Список аккаунтов обновлён</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Файлов аккаунтов найдено: <b>{report['sessions_found']}</b>\n"
        f"Доп. файлов найдено: <b>{report['metadata_ok']}</b>\n"
        f"Добавлено аккаунтов: <b>{report['added']}</b>\n"
        f"Обновлено аккаунтов: <b>{report['updated']}</b>\n"
        f"Нужно перепроверить: <b>{report['orphaned']}</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=accounts_kb(),
    )


@router.callback_query(F.data == "wizard_menu")
async def cb_wizard_menu(callback: CallbackQuery, db_user: User = None):
    if settings.HUMAN_GATED_PACKAGING:
        await callback.answer()
        await callback.message.edit_text(
            "✨ <b>Пошаговая настройка изменилась</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "Старый мастер отключён.\n"
            "Теперь настройка идёт через черновики Gemini и ручное подтверждение каждого шага.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🚀 Начать настройку аккаунта", callback_data="acc_onboarding_start")],
                [InlineKeyboardButton(text="📘 Продолжить настройку", callback_data="acc_onboarding_continue")],
                [InlineKeyboardButton(text="◀️ К аккаунтам", callback_data="back_accounts")],
            ]),
        )
        return
    if not settings.ENABLE_CLIENT_WIZARD:
        await callback.answer("Этот раздел сейчас недоступен", show_alert=True)
        return

    await callback.answer()
    async with async_session() as session:
        query = select(Account).where(Account.status != "banned")
        if db_user is not None and not db_user.is_admin:
            query = query.where(Account.user_id == db_user.id)
        result = await session.execute(query.order_by(Account.created_at.asc()))
        accounts = list(result.scalars().all())

    if not accounts:
        await callback.message.edit_text(
            "✅ <b>Подготовка аккаунта</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "<i>Нет доступных аккаунтов.</i>\n"
            "Сначала загрузите аккаунты и обновите список.",
            parse_mode=ParseMode.HTML,
            reply_markup=accounts_kb(),
        )
        return

    rows: list[list[InlineKeyboardButton]] = []
    for acc in accounts[:20]:
        report = _build_account_recovery_report(acc)
        rows.append([
            InlineKeyboardButton(
                text=f"{acc.phone} • {_friendly_account_status(report)}",
                callback_data=f"wizard_pick:{acc.id}",
            )
        ])
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="back_accounts")])

    await callback.message.edit_text(
        "✅ <b>Подготовка аккаунта</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "Выберите аккаунт и отметьте шаги, которые уже выполнены.\n\n"
        "Путь аккаунта:\n"
        "<code>профиль → канал → контент → подтверждение</code>\n\n"
        "После этого аккаунт станет готов к работе.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(F.data.startswith("wizard_pick:"))
async def cb_wizard_pick(callback: CallbackQuery, db_user: User = None):
    raw_id = _parse_callback_arg(callback.data, "wizard_pick:")
    if not raw_id or not raw_id.isdigit():
        await callback.answer("Некорректный аккаунт", show_alert=True)
        return
    account = await _load_account_by_id(int(raw_id))
    if not account:
        await callback.answer("Аккаунт не найден", show_alert=True)
        return
    if not _is_owner_or_admin(db_user, account):
        await callback.answer("Нет доступа к аккаунту", show_alert=True)
        return

    await callback.answer()
    await _render_wizard_account_view(callback, account)


async def _render_wizard_account_view(callback: CallbackQuery, account: Account):
    await callback.message.edit_text(
        "✅ <b>Подготовка аккаунта</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📱 <code>{account.phone}</code>\n"
        f"Этап: <b>{escape(_friendly_lifecycle_label(account.lifecycle_stage))}</b>\n"
        f"Проверка доступа: <b>{escape(_friendly_health_label(account.health_status))}</b>\n\n"
        "Отмечайте шаги по порядку:\n"
        "1. Профиль готов\n"
        "2. Канал готов\n"
        "3. Контент готов\n"
        "4. Отправить на подтверждение",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="1) ✅ Профиль готов", callback_data=f"wizard_profile:{account.id}")],
            [InlineKeyboardButton(text="2) ✅ Канал готов", callback_data=f"wizard_channel:{account.id}")],
            [InlineKeyboardButton(text="3) ✅ Контент готов", callback_data=f"wizard_content:{account.id}")],
            [InlineKeyboardButton(text="4) 🚦 Отправить на подтверждение", callback_data=f"wizard_warmup:{account.id}")],
            [InlineKeyboardButton(text="◀️ К аккаунтам", callback_data="wizard_menu")],
        ]),
    )


async def _wizard_mark_step(
    callback: CallbackQuery,
    db_user: User | None,
    *,
    prefix: str,
    step_name: str,
    to_stage: str | None = None,
):
    raw_id = _parse_callback_arg(callback.data, prefix)
    if not raw_id or not raw_id.isdigit():
        await callback.answer("Некорректный аккаунт", show_alert=True)
        return
    account = await _load_account_by_id(int(raw_id))
    if not account:
        await callback.answer("Аккаунт не найден", show_alert=True)
        return
    if not _is_owner_or_admin(db_user, account):
        await callback.answer("Нет доступа", show_alert=True)
        return

    if to_stage:
        try:
            response = await ops_api_post(
                f"/v1/accounts/{account.id}/stage",
                {
                    "to_stage": to_stage,
                    "actor": f"wizard:{step_name}",
                    "reason": f"manual wizard step {step_name}",
                    "user_id": _tenant_write_scope_user_id(db_user),
                },
            )
        except Exception:
            await callback.answer("Не удалось обновить этап аккаунта", show_alert=True)
            return
        if not response.get("ok"):
            await callback.answer("Не удалось обновить аккаунт", show_alert=True)
            return
        updated = await _load_account_by_id(account.id)
    else:
        updated = await _log_account_stage_note(
            account.id,
            actor=f"wizard:{step_name}",
            reason=f"manual wizard step {step_name}",
        )
    if not updated:
        await callback.answer("Не удалось обновить аккаунт", show_alert=True)
        return

    step_key = {
        "profile": "profile_ready",
        "channel": "channel_ready",
        "content": "content_ready",
        "warmup": "gate_review_requested",
    }.get(step_name, step_name)
    await _safe_onboarding_step_post(
        updated.phone,
        db_user=db_user,
        step_key=step_key,
        actor=f"wizard:{step_name}",
        result="done",
        notes=f"Шаг '{step_name}' отмечен в мастере подготовки.",
        payload={"lifecycle_stage": updated.lifecycle_stage},
    )

    await callback.answer("Шаг сохранён")
    await _render_wizard_account_view(callback, updated)


@router.callback_query(F.data.startswith("wizard_profile:"))
async def cb_wizard_profile(callback: CallbackQuery, db_user: User = None):
    await _wizard_mark_step(callback, db_user, prefix="wizard_profile:", step_name="profile")


@router.callback_query(F.data.startswith("wizard_channel:"))
async def cb_wizard_channel(callback: CallbackQuery, db_user: User = None):
    await _wizard_mark_step(callback, db_user, prefix="wizard_channel:", step_name="channel")


@router.callback_query(F.data.startswith("wizard_content:"))
async def cb_wizard_content(callback: CallbackQuery, db_user: User = None):
    await _wizard_mark_step(callback, db_user, prefix="wizard_content:", step_name="content")


@router.callback_query(F.data.startswith("wizard_warmup:"))
async def cb_wizard_warmup(callback: CallbackQuery, db_user: User = None):
    await _wizard_mark_step(
        callback,
        db_user,
        prefix="wizard_warmup:",
        step_name="warmup",
        to_stage="gate_review",
    )


@router.callback_query(F.data == "policy_status")
async def cb_policy_status(callback: CallbackQuery, db_user: User = None):
    await callback.answer()
    decisions = await _policy_decision_counts(settings.STRICT_SLO_WINDOW_DAYS)
    quarantined = await _active_quarantine_count()

    parser_phone = _normalize_phone(settings.PARSER_ONLY_PHONE) if settings.PARSER_ONLY_PHONE else ""
    parser_info = "не задан"
    if parser_phone:
        async with async_session() as session:
            result = await session.execute(select(Account).where(Account.phone == parser_phone))
            parser_acc = result.scalar_one_or_none()
        if parser_acc:
            parser_info = (
                f"{parser_acc.phone} "
                f"(health={parser_acc.health_status}, lifecycle={parser_acc.lifecycle_stage})"
            )
        else:
            parser_info = f"{parser_phone} (нет в БД)"

    async with async_session() as session:
        restricted = await session.scalar(
            select(func.count(Account.id)).where(Account.lifecycle_stage == "restricted")
        )
        high_risk = await session.scalar(
            select(func.count(AccountRiskState.id)).where(AccountRiskState.risk_level.in_(["high", "critical"]))
        )

    await callback.message.edit_text(
        "🛡 <b>Compliance Policy Status</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Mode: <b>{settings.COMPLIANCE_MODE}</b>\n"
        f"Manual gate required: <b>{'да' if settings.MANUAL_GATE_REQUIRED else 'нет'}</b>\n"
        f"Client wizard: <b>{'вкл' if settings.ENABLE_CLIENT_WIZARD else 'выкл'}</b>\n"
        f"SLO window: <b>{settings.STRICT_SLO_WINDOW_DAYS}d</b>\n\n"
        f"Parser-only account: <code>{escape(parser_info)}</code>\n\n"
        f"Decisions ({settings.STRICT_SLO_WINDOW_DAYS}d): <code>{escape(str(decisions))}</code>\n"
        f"Active quarantines: <b>{quarantined}</b>\n"
        f"Restricted accounts: <b>{int(restricted or 0)}</b>\n"
        f"High/critical risk: <b>{int(high_risk or 0)}</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=accounts_kb(),
    )


@router.callback_query(F.data == "policy_violations")
async def cb_policy_violations(callback: CallbackQuery, db_user: User = None):
    if not db_user or not db_user.is_admin:
        await callback.answer("Действие недоступно", show_alert=True)
        return
    await callback.answer()
    async with async_session() as session:
        result = await session.execute(
            select(PolicyEvent)
            .where(PolicyEvent.decision.in_(["warn", "block", "quarantine"]))
            .order_by(PolicyEvent.created_at.desc())
            .limit(20)
        )
        events = list(result.scalars().all())

    if not events:
        text = (
            "📛 <b>Policy violations</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "<i>Нарушений не найдено.</i>"
        )
    else:
        lines = [
            "📛 <b>Policy violations (latest 20)</b>",
            "━━━━━━━━━━━━━━━━━━━━",
            "",
        ]
        for ev in events:
            ts = ev.created_at.strftime("%m-%d %H:%M") if ev.created_at else "--"
            lines.append(
                f"{ts} | <b>{ev.decision}</b> | {escape(ev.rule_id)} | "
                f"{escape(ev.phone or 'n/a')}"
            )
        text = "\n".join(lines)

    await callback.message.edit_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=accounts_kb(),
    )


@router.callback_query(F.data == "parser_set_account")
async def cb_parser_set_account(callback: CallbackQuery, state: FSMContext, db_user: User = None):
    if not db_user or not db_user.is_admin:
        await callback.answer("Действие недоступно", show_alert=True)
        return
    await callback.answer()
    await state.set_state(ComplianceStates.waiting_parser_phone)
    await callback.message.edit_text(
        "🧩 <b>Аккаунт для поиска</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "Введите номер телефона аккаунта, который будет искать новые каналы.\n"
        "Формат: <code>+79991234567</code>\n\n"
        "Текущий: "
        f"<code>{escape(settings.PARSER_ONLY_PHONE or 'не задан')}</code>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back_accounts")],
        ]),
    )


@router.message(ComplianceStates.waiting_parser_phone, F.text)
async def process_parser_phone(message: Message, state: FSMContext, db_user: User = None):
    if not db_user or not db_user.is_admin:
        await state.clear()
        return

    phone = _extract_first_phone_from_text(message.text or "")
    if not phone:
        await message.answer("Введите корректный номер, пример: +79991234567")
        return

    async with async_session() as session:
        result = await session.execute(select(Account).where(Account.phone == phone))
        account = result.scalar_one_or_none()

    if not account:
        session_path, _ = canonical_session_paths(settings.sessions_path, db_user.id, phone)
        if session_path.exists():
            await message.answer(
                f"Аккаунт {phone} пока не появился в списке.\n"
                "Файлы уже на месте, откройте «Аккаунты -> Обновить список».",
                parse_mode=ParseMode.HTML,
            )
        else:
            await message.answer(f"Аккаунт {phone} не найден в БД.")
        return

    if account.health_status in {"dead", "restricted"} or account.lifecycle_stage == "restricted":
        await message.answer(
            f"Аккаунт {phone} сейчас нельзя использовать для поиска каналов."
        )
        return

    settings.PARSER_ONLY_PHONE = phone
    _update_env("PARSER_ONLY_PHONE", phone)
    if settings.OPS_API_URL:
        try:
            await ops_api_post(
                f"/v1/accounts/{quote(phone, safe='')}/assign-role",
                {
                    "role": "parser_active",
                    "user_id": _tenant_write_scope_user_id(db_user),
                    "actor": "bot_parser_assign",
                    "source": "bot",
                    "channel": "bot",
                },
                timeout=30,
            )
        except Exception as exc:
            log.warning(f"Не удалось отметить parser role для {phone}: {exc}")

    await state.clear()
    await message.answer(
        "✅ Аккаунт для поиска обновлён\n\n"
        f"Телефон: <code>{phone}</code>\n"
        f"Проверка доступа: <b>{escape(_friendly_health_label(account.health_status))}</b>\n"
        f"Этап: <b>{escape(_friendly_lifecycle_label(account.lifecycle_stage))}</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_kb(),
    )


@router.message(ComplianceStates.waiting_packaging_phone, F.text)
async def process_packaging_phone(message: Message, state: FSMContext, db_user: User = None):
    phone = _extract_first_phone_from_text(message.text or "")
    if not phone:
        await message.answer("Введите корректный номер, пример: +79991234567")
        return
    await state.clear()
    await _start_onboarding_for_phone(message, phone=phone, db_user=db_user)


@router.callback_query(F.data == "gate_review")
async def cb_gate_review(callback: CallbackQuery, db_user: User = None):
    if settings.HUMAN_GATED_PACKAGING:
        await callback.answer()
        await callback.message.edit_text(
            "✅ <b>Финальный шаг</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "Отдельная очередь подтверждения больше не используется.\n"
            "После применения поста выберите роль аккаунта и переведите его в рабочий пул.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📘 Продолжить настройку", callback_data="acc_onboarding_continue")],
                [InlineKeyboardButton(text="◀️ К аккаунтам", callback_data="back_accounts")],
            ]),
        )
        return
    if not db_user or not db_user.is_admin:
        await callback.answer("Действие недоступно", show_alert=True)
        return

    await callback.answer()
    async with async_session() as session:
        result = await session.execute(
            select(Account)
            .where(Account.lifecycle_stage == "gate_review")
            .order_by(Account.last_active_at.desc())
            .limit(20)
        )
        accounts = list(result.scalars().all())

    if not accounts:
        await callback.message.edit_text(
            "✅ <b>Завершить подготовку</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "<i>Сейчас нет аккаунтов, которые ждут подтверждения.</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=accounts_kb(),
        )
        return

    rows: list[list[InlineKeyboardButton]] = []
    lines = [
        "✅ <b>Завершить подготовку</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
    ]
    for acc in accounts:
        report = _build_account_recovery_report(acc)
        lines.append(
            f"• <code>{acc.phone}</code> | "
            f"{escape(_friendly_account_status(report))} | "
            f"этап: <b>{escape(_friendly_lifecycle_label(acc.lifecycle_stage))}</b>"
        )
        rows.append([
            InlineKeyboardButton(text=f"✅ Подтвердить {acc.phone}", callback_data=f"gate_approve:{acc.id}")
        ])
        rows.append([
            InlineKeyboardButton(text=f"↩️ Вернуть на прогрев {acc.phone}", callback_data=f"gate_reject:{acc.id}")
        ])
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="back_accounts")])

    await callback.message.edit_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(F.data.startswith("gate_approve:"))
async def cb_gate_approve(callback: CallbackQuery, db_user: User = None):
    if not db_user or not db_user.is_admin:
        await callback.answer("Действие недоступно", show_alert=True)
        return
    raw_id = _parse_callback_arg(callback.data, "gate_approve:")
    if not raw_id or not raw_id.isdigit():
        await callback.answer("Некорректный аккаунт", show_alert=True)
        return
    account = await _load_account_by_id(int(raw_id))
    if account is None:
        await callback.answer("Аккаунт не найден", show_alert=True)
        return

    if (settings.NEW_ACCOUNT_LAUNCH_MODE or "").strip().lower() == "faster_1d":
        created_at = account.created_at or utcnow()
        age_hours = max(0.0, (utcnow() - created_at).total_seconds() / 3600.0)
        if age_hours < 24:
            remaining = max(1, int(24 - age_hours))
            await callback.answer(
                f"Подтверждение станет доступно после 24 часов жизни аккаунта. Осталось примерно {remaining}ч.",
                show_alert=True,
            )
            return

    if account.health_status in {"frozen", "restricted", "dead"}:
        await callback.answer(
            f"Подтверждение пока недоступно: {_friendly_health_label(account.health_status)}.",
            show_alert=True,
        )
        return

    caps: dict = {}
    if account.capabilities_json:
        try:
            caps = json.loads(account.capabilities_json)
        except Exception:
            caps = {}
    if settings.FROZEN_PROBE_ON_CONNECT and not caps:
        await callback.answer(
            "Подтверждение пока недоступно. Сначала повторно проверьте доступ аккаунта.",
            show_alert=True,
        )
        return
    if caps and not bool(caps.get("can_search", False)):
        await callback.answer(
            "Подтверждение пока недоступно. Аккаунту нужна дополнительная проверка.",
            show_alert=True,
        )
        return

    try:
        response = await ops_api_post(
            f"/v1/accounts/{int(raw_id)}/stage",
            {
                "to_stage": "execution_ready" if settings.HUMAN_GATED_PACKAGING else "active_commenting",
                "actor": "gate_approve",
                "reason": "manual compliance gate approve",
                "status": "active",
                "health_status": "alive",
                "user_id": _tenant_write_scope_user_id(db_user),
            },
        )
    except Exception:
        await callback.answer("Не удалось завершить подготовку", show_alert=True)
        return
    if not response.get("ok"):
        await callback.answer("Аккаунт не найден", show_alert=True)
        return
    await _safe_onboarding_step_post(
        account.phone,
        db_user=db_user,
        step_key="final_ready",
        actor="gate_approve",
        result="completed",
        notes="Аккаунт подтверждён и переведён в рабочее состояние.",
        payload=response,
        run_status="completed",
    )
    await callback.answer(f"{response.get('phone')} готов к работе")
    await cb_gate_review(callback, db_user=db_user)


@router.callback_query(F.data.startswith("gate_reject:"))
async def cb_gate_reject(callback: CallbackQuery, db_user: User = None):
    if not db_user or not db_user.is_admin:
        await callback.answer("Действие недоступно", show_alert=True)
        return
    raw_id = _parse_callback_arg(callback.data, "gate_reject:")
    if not raw_id or not raw_id.isdigit():
        await callback.answer("Некорректный аккаунт", show_alert=True)
        return
    try:
        response = await ops_api_post(
            f"/v1/accounts/{int(raw_id)}/stage",
            {
                "to_stage": "warming_up",
                "actor": "gate_reject",
                "reason": "manual compliance gate reject",
                "status": "cooldown",
                "user_id": _tenant_write_scope_user_id(db_user),
            },
        )
    except Exception:
        await callback.answer("Не удалось вернуть аккаунт на прогрев", show_alert=True)
        return
    if not response.get("ok"):
        await callback.answer("Аккаунт не найден", show_alert=True)
        return
    await _safe_onboarding_step_post(
        response.get("phone") or "",
        db_user=db_user,
        step_key="returned_to_warmup",
        actor="gate_reject",
        result="returned",
        notes="Аккаунт возвращён на прогрев после ручной проверки.",
        payload=response,
    )
    await callback.answer(f"{response.get('phone')} возвращён на прогрев")
    await cb_gate_review(callback, db_user=db_user)


@router.callback_query(F.data == "acc_package")
async def cb_acc_package(callback: CallbackQuery, state: FSMContext, db_user: User = None):
    await callback.answer()
    await state.clear()
    accounts = await account_mgr.load_accounts(user_id=db_user.id if db_user else None)
    if not accounts:
        await callback.message.edit_text(
            "✨ <b>Gemini + ручное подтверждение</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "<i>Пока нет аккаунтов для настройки.</i>\n"
            "Сначала загрузите аккаунты и обновите список.",
            parse_mode=ParseMode.HTML,
            reply_markup=accounts_kb(),
        )
        return

    await callback.message.edit_text(
        "🎨 <b>Подготовить профиль</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Аккаунтов в списке: <b>{len(accounts)}</b>\n"
        "Ручная настройка идёт по шагам:\n"
        "• сначала профиль\n"
        "• потом канал\n"
        "• затем закреплённый пост\n"
        "• после этого выбирается роль аккаунта\n\n"
        "Каждый шаг подтверждается отдельно.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🎨 Начать подготовку", callback_data="acc_onboarding_start")],
            [InlineKeyboardButton(text="📘 Продолжить настройку", callback_data="acc_onboarding_continue")],
            [InlineKeyboardButton(text="📝 История шагов", callback_data="acc_onboarding_history")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back_accounts")],
        ]),
    )


@router.callback_query(F.data == "acc_package_one")
async def cb_acc_package_one(callback: CallbackQuery, state: FSMContext):
    await cb_acc_onboarding_start(callback, state)


@router.callback_query(F.data == "acc_package_run")
async def cb_acc_package_run(callback: CallbackQuery, db_user: User = None):
    await callback.answer("Массовый запуск выключен", show_alert=True)
    await callback.message.edit_text(
        "✨ <b>Массовая подготовка выключена</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "Основной production-path теперь human-gated.\n"
        "Настраивайте аккаунты по одному через пошаговый мастер.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Начать настройку аккаунта", callback_data="acc_onboarding_start")],
            [InlineKeyboardButton(text="📘 Продолжить настройку", callback_data="acc_onboarding_continue")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="acc_package")],
        ]),
    )


@router.callback_query(F.data == "acc_subscribe")
async def cb_acc_subscribe(callback: CallbackQuery):
    await callback.answer()
    connected = session_mgr.get_connected_phones()
    channels = await channel_db.get_all_active()

    if not connected:
        await callback.message.edit_text(
            "📢 <b>Подписка на каналы</b>\n\n"
            "<i>Сначала подключите аккаунты!</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=accounts_kb(),
        )
        return

    await callback.message.edit_text(
        "📢 <b>Подписка на каналы</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Аккаунтов: <b>{len(connected)}</b>\n"
        f"Каналов в базе: <b>{len(channels)}</b>\n\n"
        "Аккаунты будут подписаны на все каналы\n"
        "и их группы обсуждений.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Подписать все аккаунты", callback_data="acc_subscribe_run")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back_accounts")],
        ]),
    )


@router.callback_query(F.data == "acc_subscribe_run")
async def cb_acc_subscribe_run(callback: CallbackQuery):
    await callback.answer("Подписка запущена...")
    await callback.message.edit_text(
        "⏳ Подписываю аккаунты на каналы...\n"
        "<i>Это может занять несколько минут.</i>",
        parse_mode=ParseMode.HTML,
    )

    result = await channel_subscriber.subscribe_all_accounts()

    await callback.message.edit_text(
        "📢 <b>Подписка завершена</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Аккаунтов: <b>{result['accounts']}</b>\n"
        f"Новых подписок: <b>{result['subscribed']}</b>\n"
        f"Уже были подписаны: <b>{result['already']}</b>\n"
        f"Ошибок: <b>{result['failed']}</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=accounts_kb(),
    )


@router.callback_query(F.data == "acc_redirect_channel")
async def cb_acc_redirect_channel(callback: CallbackQuery):
    await callback.answer()
    connected = session_mgr.get_connected_phones()
    bot_link = settings.PRODUCT_BOT_LINK
    current_channel = settings.PRODUCT_CHANNEL_LINK

    if not connected:
        await callback.message.edit_text(
            "📡 <b>Канал-переходник</b>\n\n"
            "<i>Сначала подключите аккаунты!</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=accounts_kb(),
        )
        return

    status_text = (
        f"📡 <b>Канал-переходник</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Для каждого аккаунта будет создан:\n"
        f"• Личный канал с уникальным названием\n"
        f"• Пост со ссылкой на {settings.PRODUCT_NAME}\n"
        f"• Закреплённый пост в канале\n"
        f"• Bio аккаунта со ссылкой на канал\n\n"
        f"<b>Цепочка:</b> Аватарка → Профиль → Канал → {settings.PRODUCT_NAME}\n\n"
        f"Аккаунтов: <b>{len(connected)}</b>\n"
        f"{settings.PRODUCT_NAME}: <code>{escape(bot_link)}</code>\n"
    )
    if current_channel:
        status_text += f"Текущий канал: <code>{escape(current_channel)}</code>\n"

    await callback.message.edit_text(
        status_text,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Создать для всех аккаунтов", callback_data="acc_redirect_run")],
            [InlineKeyboardButton(text="🖼 Аватарка канала", callback_data="acc_channel_avatar")],
            [InlineKeyboardButton(text="📌 Закрепить канал в профиле", callback_data="acc_personal_channel")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back_accounts")],
        ]),
    )


@router.callback_query(F.data == "acc_channel_avatar")
async def cb_acc_channel_avatar(callback: CallbackQuery, state: FSMContext):
    """Меню настройки аватарки канала-переходника."""
    await callback.answer()

    avatar_path = BASE_DIR / settings.PRODUCT_AVATAR_PATH
    exists = avatar_path.exists()
    size_kb = avatar_path.stat().st_size // 1024 if exists else 0

    # Проверяем наличие квадратной версии
    square_path = avatar_path.parent / f"{avatar_path.stem}_square.png" if exists else None
    has_square = square_path and square_path.exists()

    status = "✅ Загружена" if exists else "❌ Не найдена"
    square_info = ""
    if has_square:
        with PILImage.open(square_path) as sq:
            square_info = f"\nКвадратная: <code>{sq.size[0]}x{sq.size[1]}</code> ({square_path.stat().st_size // 1024} KB)"

    text = (
        "🖼 <b>Аватарка канала</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Файл: <code>{avatar_path.name}</code>\n"
        f"Статус: {status}\n"
    )
    if exists:
        with PILImage.open(avatar_path) as orig:
            text += f"Размер: <code>{orig.size[0]}x{orig.size[1]}</code> ({size_kb} KB)"
    text += square_info
    text += (
        "\n\n"
        "📎 <b>Отправьте новое изображение</b> как фото\n"
        "для замены аватарки канала.\n\n"
        "Изображение будет автоматически растянуто\n"
        "до 800x800 для аватарки Telegram."
    )

    buttons = []
    if exists and session_mgr.get_connected_phones():
        buttons.append([InlineKeyboardButton(
            text="🔄 Обновить аватарки всех каналов",
            callback_data="acc_update_avatars_all",
        )])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="acc_redirect_channel")])

    await callback.message.edit_text(
        text, parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await state.set_state(AvatarStates.waiting_photo)


@router.message(AvatarStates.waiting_photo, F.photo)
async def process_avatar_photo(message: Message, state: FSMContext):
    """Получить фото от пользователя, сохранить как аватарку канала."""
    await state.clear()

    # Скачиваем фото максимального размера
    photo = message.photo[-1]
    try:
        file = await message.bot.get_file(photo.file_id)
        data = await message.bot.download_file(file.file_path)
    except Exception as exc:
        await message.answer(f"❌ Не удалось скачать фото: {exc}")
        return

    # Сохраняем как аватарку продукта
    avatar_path = BASE_DIR / settings.PRODUCT_AVATAR_PATH
    avatar_path.parent.mkdir(parents=True, exist_ok=True)
    avatar_path.write_bytes(data.read())

    # Удаляем старую квадратную версию (чтобы пересоздалась)
    square_path = avatar_path.parent / f"{avatar_path.stem}_square.png"
    if square_path.exists():
        square_path.unlink()

    # Генерируем новую квадратную версию
    sq = prepare_square_avatar(avatar_path)

    with PILImage.open(avatar_path) as orig:
        ow, oh = orig.size
    with PILImage.open(sq) as sqimg:
        sw, sh = sqimg.size

    buttons = []
    if session_mgr.get_connected_phones():
        buttons.append([InlineKeyboardButton(
            text="🔄 Обновить аватарки всех каналов",
            callback_data="acc_update_avatars_all",
        )])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="acc_redirect_channel")])

    await message.answer(
        "✅ <b>Аватарка сохранена!</b>\n\n"
        f"Оригинал: <code>{ow}x{oh}</code>\n"
        f"Квадратная: <code>{sw}x{sh}</code> ({sq.stat().st_size // 1024} KB)\n\n"
        "Нажмите «Обновить аватарки» чтобы применить\n"
        "ко всем каналам-переходникам.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@router.message(AvatarStates.waiting_photo)
async def process_avatar_not_photo(message: Message, state: FSMContext):
    """Пользователь прислал не фото."""
    await state.clear()
    await message.answer(
        "❌ Отправьте <b>изображение как фото</b> (не файл).\n"
        "Или нажмите «Назад» для отмены.",
        parse_mode=ParseMode.HTML,
    )


@router.callback_query(F.data == "acc_update_avatars_all")
async def cb_update_avatars_all(callback: CallbackQuery, db_user: User = None):
    """Обновить аватарки на всех каналах-переходниках."""
    await callback.answer("Обновление аватарок запущено...")

    avatar_path = BASE_DIR / settings.PRODUCT_AVATAR_PATH
    if not avatar_path.exists():
        await callback.message.edit_text(
            "❌ Аватарка не найдена. Сначала загрузите изображение.",
            reply_markup=accounts_kb(),
        )
        return

    uid = db_user.id if db_user else None
    connected = session_mgr.get_connected_phones(user_id=uid)
    await callback.message.edit_text(
        f"⏳ <b>Обновляю аватарки каналов...</b>\n\n"
        f"Аккаунтов: {len(connected)}\n"
        f"<i>Это займёт {len(connected) * 5}-{len(connected) * 10} секунд.</i>",
        parse_mode=ParseMode.HTML,
    )

    result = await channel_setup.update_all_avatars(
        avatar_path, user_id=uid,
    )

    await callback.message.edit_text(
        "🖼 <b>Аватарки обновлены</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"✅ Успешно: <b>{result['success']}</b>\n"
        f"❌ Ошибок: <b>{result['failed']}</b>\n"
        f"Всего: <b>{result['total']}</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=accounts_kb(),
    )


@router.callback_query(F.data == "acc_redirect_run")
async def cb_acc_redirect_run(callback: CallbackQuery):
    await callback.answer("Создание каналов запущено...")
    connected = session_mgr.get_connected_phones()

    await callback.message.edit_text(
        f"⏳ <b>Создаю каналы-переходники...</b>\n\n"
        f"Аккаунтов: {len(connected)}\n"
        f"<i>Это займёт {len(connected) * 20}-{len(connected) * 35} секунд.</i>",
        parse_mode=ParseMode.HTML,
    )

    result = await channel_setup.setup_all_accounts()

    lines = [
        "📡 <b>Каналы-переходники созданы</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
    ]

    for r in result["results"]:
        if r["success"]:
            lines.append(
                f"✅ <code>{r['phone']}</code>\n"
                f"   📢 {escape(r['channel_title'])}\n"
                f"   🔗 {escape(r['channel_link'])}"
            )
        else:
            lines.append(
                f"❌ <code>{r['phone']}</code>\n"
                f"   Ошибка: {escape(r.get('error', 'unknown'))}"
            )

    lines.append(
        f"\n<b>Итого:</b> ✅ {result['success']} / ❌ {result['failed']} из {result['total']}"
    )

    await callback.message.edit_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=accounts_kb(),
    )


@router.callback_query(F.data == "acc_personal_channel")
async def cb_acc_personal_channel(callback: CallbackQuery):
    """Показать информацию о закреплении канала-переходника в профиле."""
    await callback.answer()
    connected = session_mgr.get_connected_phones()

    if not connected:
        await callback.message.edit_text(
            "📌 <b>Персональный канал</b>\n\n"
            "<i>Сначала подключите аккаунты!</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=accounts_kb(),
        )
        return

    await callback.message.edit_text(
        f"📌 <b>Персональный канал</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Канал-переходник будет закреплён как виджет\n"
        f"в шапке профиля (как на скриншоте).\n"
        f"Это заменяет текстовую ссылку в bio.\n\n"
        f"Аккаунтов: <b>{len(connected)}</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📌 Закрепить для всех аккаунтов", callback_data="acc_personal_channel_run")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back_accounts")],
        ]),
    )


@router.callback_query(F.data == "acc_personal_channel_run")
async def cb_acc_personal_channel_run(callback: CallbackQuery):
    """Закрепить канал-переходник как виджет в профиле для всех аккаунтов."""
    await callback.answer("Устанавливаю каналы в профили...")
    connected = session_mgr.get_connected_phones()

    await callback.message.edit_text(
        f"⏳ <b>Закрепляю каналы в профилях...</b>\n\n"
        f"Аккаунтов: {len(connected)}\n"
        f"<i>Канал будет отображаться как виджет в шапке профиля.</i>",
        parse_mode=ParseMode.HTML,
    )

    result = await channel_setup.set_personal_channel_all()

    lines = [
        "📌 <b>Персональные каналы установлены</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
    ]

    for r in result["results"]:
        status = "✅" if r["success"] else "❌"
        lines.append(f"{status} <code>{r['phone']}</code>")

    lines.append(
        f"\n<b>Итого:</b> ✅ {result['success']} / ❌ {result['failed']} из {result['total']}"
    )

    await callback.message.edit_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=accounts_kb(),
    )


@router.callback_query(F.data == "back_accounts")
async def cb_back_accounts(callback: CallbackQuery):
    await callback.answer()
    await callback.message.edit_text(
        "👤 <b>Управление аккаунтами</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=accounts_kb(),
    )


# --- Прокси ---

@router.callback_query(F.data == "proxy_list")
async def cb_proxy_list(callback: CallbackQuery, db_user: User = None):
    await callback.answer()
    if not proxy_mgr.proxies and settings.proxy_list_path.exists():
        proxy_mgr.load_from_file()

    summary = await get_proxy_pool_summary(user_id=_tenant_read_scope_user_id(db_user))
    if not proxy_mgr.proxies and int(summary.get("total", 0)) == 0:
        await callback.message.edit_text(
            "📋 <b>Список прокси</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "<i>Прокси не загружены.\n"
            "Загрузите из файла или добавьте вручную.</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=proxy_kb(),
        )
        return

    lines = [
        "📋 <b>Список прокси</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        f"Всего в системе: <b>{int(summary.get('total', 0))}</b>",
        f"Активные: <b>{int(summary.get('active', 0))}</b>",
        f"Проверенно живые: <b>{int(summary.get('healthy', 0))}</b>",
        f"Свободные для новых аккаунтов: <b>{int(summary.get('usable_for_binding', 0))}</b>",
        "",
    ]
    if int(summary.get("duplicate_bound", 0)) > 0:
        lines.append(
            f"⚠️ Один и тот же прокси назначен нескольким аккаунтам: <b>{int(summary.get('duplicate_bound', 0))}</b>"
        )
    if summary.get("low_stock"):
        lines.append(
            "⚠️ Запас прокси заканчивается. "
            f"Загрузите ещё <b>{int(summary.get('recommended_topup', 0))}</b>."
        )
        lines.append("")
    for idx, proxy in enumerate(proxy_mgr.proxies[:20], start=1):
        auth = "auth" if proxy.username else "no-auth"
        lines.append(f"{idx}. <code>{escape(proxy.host)}:{proxy.port}</code> ({proxy.proxy_type}, {auth})")
    if len(proxy_mgr.proxies) > 20:
        lines.append("")
        lines.append(f"<i>Показаны первые 20 из {len(proxy_mgr.proxies)}.</i>")

    await callback.message.edit_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=proxy_kb(),
    )


@router.callback_query(F.data == "proxy_audit")
async def cb_proxy_audit(callback: CallbackQuery, db_user: User = None):
    await callback.answer()
    payload = await _fetch_proxy_audit_payload(db_user=db_user)
    await callback.message.edit_text(
        _render_proxy_audit(payload),
        parse_mode=ParseMode.HTML,
        reply_markup=proxy_kb(),
    )


@router.callback_query(F.data == "proxy_load")
async def cb_proxy_load(callback: CallbackQuery):
    await callback.answer()
    await callback.message.edit_text(
        "📂 <b>Загрузка прокси</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "Отправьте текстовый файл с прокси\n"
        "или список прокси текстом.\n\n"
        "Поддерживаемые форматы:\n"
        "• <code>socks5://user:pass@host:port</code>\n"
        "• <code>host:port:user:pass</code>\n"
        "• <code>host:port</code>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back_proxy")],
        ]),
    )


@router.callback_query(F.data == "proxy_add")
async def cb_proxy_add(callback: CallbackQuery):
    await callback.answer()
    await callback.message.edit_text(
        "➕ <b>Добавить прокси</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "Отправьте прокси в формате:\n"
        "<code>socks5://user:pass@host:port</code>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back_proxy")],
        ]),
    )


@router.callback_query(F.data == "proxy_validate")
async def cb_proxy_validate(callback: CallbackQuery, db_user: User = None):
    await callback.answer("✅ Проверка...")
    if not proxy_mgr.proxies and not settings.proxy_list_path.exists():
        await callback.message.edit_text(
            "✅ <b>Проверка прокси</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "<i>Нет прокси для проверки.</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=proxy_kb(),
        )
        return

    await callback.message.edit_text("⏳ Проверяю прокси, это может занять 1-2 минуты...")
    report = await validate_proxy_pool(user_id=_tenant_write_scope_user_id(db_user))
    summary = report.get("summary") or {}
    await callback.message.edit_text(
        "✅ <b>Проверка прокси</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Проверено: <b>{int(report.get('checked', 0))}</b>\n"
        f"Рабочие: <b>{int(report.get('alive', 0))}</b>\n"
        f"Нерабочие: <b>{int(report.get('failed', 0))}</b>\n"
        f"Отключено: <b>{int(report.get('disabled', 0))}</b>\n"
        f"Свободных для новых аккаунтов: <b>{int(summary.get('usable_for_binding', 0))}</b>\n"
        + (
            f"\n⚠️ Загрузите ещё <b>{int(summary.get('recommended_topup', 0))}</b> прокси."
            if summary.get("low_stock")
            else ""
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=proxy_kb(),
    )


@router.callback_query(F.data == "back_proxy")
async def cb_back_proxy(callback: CallbackQuery):
    await callback.answer()
    await callback.message.edit_text(
        "🌐 <b>Управление прокси</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=proxy_kb(),
    )


# --- Каналы ---

@router.callback_query(F.data == "ch_list")
async def cb_ch_list(callback: CallbackQuery, db_user: User = None):
    await callback.answer()
    user_id = _tenant_read_scope_user_id(db_user)
    channels = await channel_db.get_all_active(user_id=user_id)
    await callback.message.edit_text(
        render_channels(channels),
        parse_mode=ParseMode.HTML,
        reply_markup=channels_kb(),
    )


@router.callback_query(F.data == "ch_add")
async def cb_ch_add(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(ParserStates.waiting_channel)
    await callback.message.edit_text(
        "➕ <b>Добавить канал</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "Отправьте ссылку на канал или @username:\n"
        "<code>@channel_name</code>\n"
        "<code>https://t.me/channel_name</code>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back_channels")],
        ]),
    )


@router.callback_query(F.data == "ch_search")
async def cb_ch_search(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(ParserStates.waiting_keywords)
    await callback.message.edit_text(
        "🔍 <b>Поиск каналов</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "Отправьте ключевые слова для поиска.\n"
        "Найденные каналы сохранятся в список для проверки.\n\n"
        "(через запятую):\n\n"
        "Примеры:\n"
        "• <code>vpn, впн, разблокировка</code>\n"
        "• <code>нейросети, chatgpt, ai</code>\n"
        "• <code>instagram, инстаграм</code>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back_channels")],
        ]),
    )


@router.callback_query(F.data == "ch_stats")
async def cb_ch_stats(callback: CallbackQuery, db_user: User = None):
    await callback.answer()
    user_id = _tenant_read_scope_user_id(db_user)
    stats = await channel_db.get_stats(user_id=user_id)
    by_topic = ", ".join(f"{topic}: {count}" for topic, count in stats["by_topic"].items()) or "нет данных"
    waiting_review = int(stats["by_review"].get("discovered", 0)) + int(stats["by_review"].get("candidate", 0))
    draft_only = int(stats["by_publish_mode"].get("draft_only", 0))
    await callback.message.edit_text(
        "📊 <b>Статистика каналов</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Всего: <b>{stats['total']}</b>\n"
        f"Готовы для работы: <b>{stats['publishable']}</b>\n"
        f"Ждут проверки: <b>{waiting_review}</b>\n"
        f"Только вручную: <b>{draft_only}</b>\n"
        f"Не использовать: <b>{stats['blacklisted']}</b>\n\n"
        f"Тематики: <code>{escape(by_topic)}</code>",
        parse_mode=ParseMode.HTML,
        reply_markup=channels_kb(),
    )


@router.callback_query(F.data == "ch_review")
async def cb_ch_review(callback: CallbackQuery, db_user: User = None):
    await callback.answer()
    channels = await channel_db.get_review_queue(
        user_id=_tenant_write_scope_user_id(db_user),
        limit=10,
    )
    await callback.message.edit_text(
        _render_channel_review_queue(channels),
        parse_mode=ParseMode.HTML,
        reply_markup=_channel_review_queue_kb(channels),
    )


@router.callback_query(F.data.startswith("ch_review_pick:"))
async def cb_ch_review_pick(callback: CallbackQuery, db_user: User = None):
    raw_id = _parse_callback_arg(callback.data, "ch_review_pick:")
    if raw_id is None or not raw_id.isdigit():
        await callback.answer("Некорректный channel id", show_alert=True)
        return
    channel = await channel_db.get_by_db_id(
        int(raw_id),
        user_id=_tenant_write_scope_user_id(db_user),
    )
    if channel is None:
        await callback.answer("Канал не найден", show_alert=True)
        return
    username = f"@{escape(channel.username)}" if channel.username else f"id:{channel.telegram_id}"
    note = escape(channel.review_note or "—")
    await callback.answer()
    await callback.message.edit_text(
        "📝 <b>Проверить канал</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Название: <b>{escape(channel.title)}</b>\n"
        f"Ссылка: <code>{username}</code>\n"
        f"Подписчики: <b>{channel.subscribers}</b>\n"
        f"Тематика: <b>{escape(channel.topic or '—')}</b>\n"
        f"Текущее состояние: <b>{escape(_friendly_channel_state(channel.review_state, channel.publish_mode))}</b>\n\n"
        f"Примечание: <code>{note}</code>",
        parse_mode=ParseMode.HTML,
        reply_markup=_channel_review_action_kb(channel.id),
    )


@router.callback_query(F.data == "ch_blacklist")
async def cb_ch_blacklist(callback: CallbackQuery):
    await callback.answer()
    await callback.message.edit_text(
        "🗑 <b>Чёрный список каналов</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "Для добавления в чёрный список отправьте команду:\n"
        "<code>/blacklist CHANNEL_ID</code>\n\n"
        "Где <code>CHANNEL_ID</code> — локальный ID из базы или telegram_id.",
        parse_mode=ParseMode.HTML,
        reply_markup=channels_kb(),
    )


async def _apply_channel_review_action(
    callback: CallbackQuery,
    *,
    db_user: User | None,
    prefix: str,
    review_state: str,
    publish_mode: str,
    permission_basis: str | None,
    review_note: str,
    answer_text: str,
):
    raw_id = _parse_callback_arg(callback.data, prefix)
    if raw_id is None or not raw_id.isdigit():
        await callback.answer("Некорректный channel id", show_alert=True)
        return
    try:
        response = await ops_api_post(
            f"/v1/channels/{int(raw_id)}/review",
            {
                "review_state": review_state,
                "publish_mode": publish_mode,
                "permission_basis": permission_basis,
                "review_note": review_note,
                "user_id": _tenant_write_scope_user_id(db_user),
            },
        )
    except OpsApiError:
        await callback.answer("Канал не найден или сервис недоступен", show_alert=True)
        return
    if not response.get("ok"):
        await callback.answer("Канал не найден", show_alert=True)
        return
    await sync_to_sheets_snapshot()
    await callback.answer(answer_text)
    await cb_ch_review(callback, db_user=db_user)


@router.callback_query(F.data.startswith("ch_review_candidate:"))
async def cb_ch_review_candidate(callback: CallbackQuery, db_user: User = None):
    await _apply_channel_review_action(
        callback,
        db_user=db_user,
        prefix="ch_review_candidate:",
        review_state="candidate",
        publish_mode="research_only",
        permission_basis="",
        review_note="saved for manual review",
        answer_text="Канал оставлен для проверки",
    )


@router.callback_query(F.data.startswith("ch_review_auto:"))
async def cb_ch_review_auto(callback: CallbackQuery, db_user: User = None):
    await _apply_channel_review_action(
        callback,
        db_user=db_user,
        prefix="ch_review_auto:",
        review_state="approved",
        publish_mode="auto_allowed",
        permission_basis="admin_added",
        review_note="approved for automatic use",
        answer_text="Канал готов для работы",
    )


@router.callback_query(F.data.startswith("ch_review_draft:"))
async def cb_ch_review_draft(callback: CallbackQuery, db_user: User = None):
    await _apply_channel_review_action(
        callback,
        db_user=db_user,
        prefix="ch_review_draft:",
        review_state="approved",
        publish_mode="draft_only",
        permission_basis="admin_added",
        review_note="approved for manual-only use",
        answer_text="Канал оставлен только для ручной проверки",
    )


@router.callback_query(F.data.startswith("ch_review_block:"))
async def cb_ch_review_block(callback: CallbackQuery, db_user: User = None):
    await _apply_channel_review_action(
        callback,
        db_user=db_user,
        prefix="ch_review_block:",
        review_state="blocked",
        publish_mode="research_only",
        permission_basis="",
        review_note="excluded from work",
        answer_text="Канал исключён из работы",
    )


@router.callback_query(F.data == "back_channels")
async def cb_back_channels(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    await callback.message.edit_text(
        "📢 <b>База каналов</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=channels_kb(),
    )


# --- Комментинг ---

async def _show_legacy_disabled(callback: CallbackQuery):
    await callback.message.edit_text(
        "ℹ️ <b>Legacy-комментинг отключён</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "Используйте только меню <b>ДВИЖОК (авто)</b>.\n"
        "Это единый production-путь для distributed режима.",
        parse_mode=ParseMode.HTML,
        reply_markup=commenting_kb(),
    )


@router.callback_query(F.data == "com_legacy_info")
async def cb_com_legacy_info(callback: CallbackQuery):
    await callback.answer()
    await _show_legacy_disabled(callback)


@router.callback_query(F.data == "com_start")
async def cb_com_start(callback: CallbackQuery):
    if (
        (not settings.ENABLE_LEGACY_COMMENTING)
        or (not settings.ENABLE_ADMIN_LEGACY_TOOLS)
        or settings.DISTRIBUTED_QUEUE_MODE
    ):
        await callback.answer("Legacy режим отключён", show_alert=True)
        await _show_legacy_disabled(callback)
        return

    # Проверки
    accounts = await account_mgr.load_accounts()
    channels = await channel_db.get_all_active()

    if not accounts:
        await callback.answer("Нет аккаунтов!", show_alert=True)
        return
    if not channels:
        await callback.answer("Нет каналов!", show_alert=True)
        return

    if channel_monitor.is_running:
        await callback.answer("Уже запущен!", show_alert=True)
        return

    await callback.answer("Запускаю...")

    # Подключить аккаунты
    results = await account_mgr.connect_all()
    connected = sum(1 for s in results.values() if s == "connected")

    if connected == 0:
        await callback.message.edit_text(
            "❌ <b>Не удалось подключить аккаунты</b>\n\n"
            "Проверьте TELEGRAM_API_ID/HASH и прокси.",
            parse_mode=ParseMode.HTML,
            reply_markup=commenting_kb(),
        )
        return

    # Запустить мониторинг
    await channel_monitor.start()

    # Запустить планировщик
    async def process_comment_queue():
        try:
            await comment_poster.process_queue()
        except Exception as exc:
            log.error(f"Ошибка обработки очереди: {exc}")

    task_scheduler.add_commenting_job(process_comment_queue, interval_sec=30)
    task_scheduler.add_daily_reset_job(account_mgr.reset_daily_counters)
    task_scheduler.add_auto_recovery_job(account_mgr.auto_recover, interval_sec=600)
    task_scheduler.start()

    # Уведомление о запуске
    await notifier.system_started(connected, len(channels))

    await callback.message.edit_text(
        "▶️ <b>Комментинг запущен!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👤 Подключено аккаунтов: <b>{connected}</b>\n"
        f"📢 Каналов в базе: <b>{len(channels)}</b>\n"
        f"⏱ Интервал мониторинга: <b>{settings.MONITOR_POLL_INTERVAL_SEC}с</b>\n"
        f"🔄 Emoji Swap: <b>{'Вкл' if comment_poster.emoji_swap_enabled else 'Выкл'}</b>\n\n"
        "Мониторинг каналов и автокомментирование активны.\n"
        "Используйте кнопку ⏸ для остановки.",
        parse_mode=ParseMode.HTML,
        reply_markup=commenting_kb(),
    )


@router.callback_query(F.data == "com_stop")
async def cb_com_stop(callback: CallbackQuery):
    if (
        (not settings.ENABLE_LEGACY_COMMENTING)
        or (not settings.ENABLE_ADMIN_LEGACY_TOOLS)
        or settings.DISTRIBUTED_QUEUE_MODE
    ):
        await callback.answer("Legacy режим отключён", show_alert=True)
        await _show_legacy_disabled(callback)
        return

    if not channel_monitor.is_running:
        await callback.answer("Не запущен", show_alert=True)
        return

    await channel_monitor.stop()
    task_scheduler.stop()

    stats = comment_poster.get_stats()
    await callback.answer("Остановлено")

    # Уведомление об остановке
    await notifier.system_stopped(stats['sent'], stats['failed'])

    await callback.message.edit_text(
        "⏸ <b>Комментинг остановлен</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Отправлено: <b>{stats['sent']}</b>\n"
        f"Ошибок: <b>{stats['failed']}</b>\n"
        f"Пропущено: <b>{stats['skipped']}</b>\n"
        f"Emoji Swap: <b>{stats.get('swapped', 0)}</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=commenting_kb(),
    )


@router.callback_query(F.data == "com_delayed")
async def cb_com_delayed(callback: CallbackQuery, state: FSMContext):
    if (
        (not settings.ENABLE_LEGACY_COMMENTING)
        or (not settings.ENABLE_ADMIN_LEGACY_TOOLS)
        or settings.DISTRIBUTED_QUEUE_MODE
    ):
        await callback.answer("Legacy режим отключён", show_alert=True)
        await _show_legacy_disabled(callback)
        return

    await callback.answer()

    # Проверяем есть ли отложенный запуск
    scheduled = task_scheduler.delayed_start_time
    if scheduled:
        await callback.message.edit_text(
            "⏰ <b>Отложенный запуск</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Запланирован на: <b>{scheduled.strftime('%H:%M:%S')}</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ Отменить", callback_data="com_delayed_cancel")],
                [InlineKeyboardButton(text="◀️ Назад", callback_data="back_commenting")],
            ]),
        )
        return

    if channel_monitor.is_running:
        await callback.message.edit_text(
            "⏰ <b>Комментинг уже запущен!</b>\n\n"
            "Отложенный запуск доступен только когда система остановлена.",
            parse_mode=ParseMode.HTML,
            reply_markup=commenting_kb(),
        )
        return

    await state.set_state(SettingsStates.waiting_delayed_minutes)
    await callback.message.edit_text(
        "⏰ <b>Отложенный запуск</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "Через сколько минут запустить комментинг?\n\n"
        "Введите число от 1 до 1440 (24 часа):",
        parse_mode=ParseMode.HTML,
    )


@router.message(SettingsStates.waiting_delayed_minutes, F.text)
async def process_delayed_minutes(message: Message, state: FSMContext, db_user: User = None):
    if not db_user:
        return
    if (
        (not settings.ENABLE_LEGACY_COMMENTING)
        or (not settings.ENABLE_ADMIN_LEGACY_TOOLS)
        or settings.DISTRIBUTED_QUEUE_MODE
    ):
        await state.clear()
        await message.answer(
            "Legacy режим отключён. Используйте запуск через ДВИЖОК.",
            reply_markup=commenting_kb(),
        )
        return

    try:
        minutes = int(message.text.strip())
        if not 1 <= minutes <= 1440:
            raise ValueError
    except ValueError:
        await message.answer("Введите число от 1 до 1440.")
        return

    await state.clear()

    async def delayed_start_func():
        """Функция для отложенного старта."""
        try:
            results = await account_mgr.connect_all()
            connected = sum(1 for s in results.values() if s == "connected")
            if connected == 0:
                log.warning("Отложенный запуск: нет подключённых аккаунтов")
                return

            await channel_monitor.start()

            async def process_comment_queue():
                try:
                    await comment_poster.process_queue()
                except Exception as exc:
                    log.error(f"Ошибка обработки очереди: {exc}")

            task_scheduler.add_commenting_job(process_comment_queue, interval_sec=30)
            task_scheduler.add_daily_reset_job(account_mgr.reset_daily_counters)
            task_scheduler.add_auto_recovery_job(account_mgr.auto_recover, interval_sec=600)

            channels = await channel_db.get_all_active()
            await notifier.system_started(connected, len(channels))
            log.info(f"Отложенный запуск выполнен: {connected} аккаунтов")
        except Exception as exc:
            log.error(f"Ошибка отложенного запуска: {exc}")

    run_at = task_scheduler.schedule_delayed_start(delayed_start_func, minutes)
    if not task_scheduler.is_running:
        task_scheduler.start()

    await message.answer(
        f"⏰ <b>Отложенный запуск запланирован</b>\n\n"
        f"Запустится через: <b>{minutes} мин</b>\n"
        f"Время: <b>{run_at.strftime('%H:%M:%S')}</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=commenting_kb(),
    )


@router.callback_query(F.data == "com_delayed_cancel")
async def cb_com_delayed_cancel(callback: CallbackQuery):
    if (
        (not settings.ENABLE_LEGACY_COMMENTING)
        or (not settings.ENABLE_ADMIN_LEGACY_TOOLS)
        or settings.DISTRIBUTED_QUEUE_MODE
    ):
        await callback.answer("Legacy режим отключён", show_alert=True)
        await _show_legacy_disabled(callback)
        return

    await callback.answer()
    cancelled = task_scheduler.cancel_delayed_start()
    text = "✅ Отложенный запуск отменён." if cancelled else "Нечего отменять."
    await callback.message.edit_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=commenting_kb(),
    )


@router.callback_query(F.data == "com_stats")
async def cb_com_stats(callback: CallbackQuery, db_user: User = None):
    await callback.answer()
    if _is_distributed_production():
        report = await _collect_runtime_snapshot()
        await callback.message.edit_text(
            _format_runtime_snapshot(report, title="📊 <b>Что происходит сейчас</b>"),
            parse_mode=ParseMode.HTML,
            reply_markup=commenting_kb(),
        )
        return

    monitor_stats = channel_monitor.get_stats()
    poster_stats = comment_poster.get_stats()
    gen_stats = comment_generator.get_stats()

    status = "Работает" if monitor_stats["running"] else "Остановлен"

    await callback.message.edit_text(
        "📊 <b>Что происходит сейчас</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"⚡ Статус: <b>{status}</b>\n\n"
        f"💬 Отправлено: <b>{poster_stats['sent']}</b>\n"
        f"❌ Ошибок: <b>{poster_stats['failed']}</b>\n"
        f"⏭ Пропущено: <b>{poster_stats['skipped']}</b>\n"
        f"👁 Найдено постов: <b>{monitor_stats['total_seen']}</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=commenting_kb(),
    )


@router.callback_query(F.data == "com_history")
async def cb_com_history(callback: CallbackQuery):
    await callback.answer()
    async with async_session() as session:
        rows = (
            await session.execute(
                select(Comment, Account.phone)
                .join(Account, Account.id == Comment.account_id)
                .order_by(Comment.created_at.desc())
                .limit(10)
            )
        ).all()

    if not rows:
        await callback.message.edit_text(
            "📝 <b>История комментариев</b>\n\n<i>Пока нет комментариев.</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=commenting_kb(),
        )
        return

    lines = ["📝 <b>Последние 10 комментариев</b>", "━━━━━━━━━━━━━━━━━━━━", ""]
    for comment, phone in rows:
        time_str = comment.created_at.strftime("%H:%M") if comment.created_at else "?"
        text_preview = escape((comment.text or "")[:60])
        lines.append(
            f"[{comment.scenario}] {time_str} | {escape(phone)}\n"
            f"   {text_preview}..."
        )

    await callback.message.edit_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=commenting_kb(),
    )


class TestCommentStates(StatesGroup):
    waiting_post_text = State()


@router.callback_query(F.data == "com_test")
async def cb_com_test(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(TestCommentStates.waiting_post_text)
    await callback.message.edit_text(
        "🧪 <b>Проверить текст</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "Отправьте текст публикации, и бот покажет\n"
        "примеры сообщений в двух вариантах.\n\n"
        "<i>Сообщение никуда не отправится.</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back_commenting")],
        ]),
    )


@router.message(TestCommentStates.waiting_post_text, F.text)
async def process_test_comment(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    await state.clear()
    progress = await message.answer("🧪 Генерирую тестовые комментарии...")

    post_text = message.text or ""

    # Генерируем оба сценария
    result_a = await comment_generator.generate(post_text, scenario="A")
    result_b = await comment_generator.generate(post_text, scenario="B")

    await progress.edit_text(
        "🧪 <b>Примеры сообщений</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📝 Пост: <i>{escape(post_text[:200])}</i>\n\n"
        f"<b>Вариант A</b>:\n"
        f"<code>{escape(result_a['text'])}</code>\n"
        f"<i>Источник: {result_a['source']}</i>\n\n"
        f"<b>Вариант B</b>:\n"
        f"<code>{escape(result_b['text'])}</code>\n"
        f"<i>Источник: {result_b['source']}</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=commenting_kb(),
    )


@router.callback_query(F.data == "com_scenarios")
async def cb_com_scenarios(callback: CallbackQuery):
    ratio_b = int(settings.SCENARIO_B_RATIO * 100)
    ratio_a = 100 - ratio_b
    await callback.answer()
    await callback.message.edit_text(
        "🎯 <b>Баланс сообщений</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>Вариант A ({ratio_a}%):</b>\n"
        "Более нейтральное сообщение.\n\n"
        f"<b>Вариант B ({ratio_b}%):</b>\n"
        "Более прямой совет или рекомендация.\n\n"
        "Изменить баланс можно в разделе «Продукт».",
        parse_mode=ParseMode.HTML,
        reply_markup=commenting_kb(),
    )


@router.callback_query(F.data == "com_old_posts")
async def cb_com_old_posts(callback: CallbackQuery):
    await callback.answer()
    connected = session_mgr.get_connected_phones()
    if not connected:
        await callback.message.edit_text(
            "📜 <b>Старые посты</b>\n\n"
            "<i>Сначала подключите аккаунты!</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=commenting_kb(),
        )
        return

    await callback.message.edit_text(
        "📜 <b>Режим старых постов</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "Сканирует непрокомментированные посты\n"
        "из каналов и добавляет их в очередь.\n\n"
        "Это дополнение к режиму новых постов.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Сканировать старые посты", callback_data="com_old_posts_run")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back_commenting")],
        ]),
    )


@router.callback_query(F.data == "com_old_posts_run")
async def cb_com_old_posts_run(callback: CallbackQuery):
    await callback.answer("Сканирование...")
    await callback.message.edit_text("⏳ Сканирую каналы на непрокомментированные посты...")

    added = await channel_monitor.scan_old_posts()
    queue_size = (
        await task_queue.queue_size("comment_tasks")
        if _is_distributed_production()
        else channel_monitor.queue.size
    )

    await callback.message.edit_text(
        "📜 <b>Сканирование завершено</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Добавлено в очередь: <b>{added}</b> постов\n"
        f"Общая очередь: <b>{queue_size}</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=commenting_kb(),
    )


@router.callback_query(F.data == "com_autoresponder")
async def cb_com_autoresponder(callback: CallbackQuery):
    await callback.answer()
    status = "Работает" if auto_responder.is_running else "Остановлен"
    stats = auto_responder.get_stats()

    await callback.message.edit_text(
        "💬 <b>Автоответчик ЛС</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Статус: <b>{status}</b>\n"
        f"Ответов отправлено: <b>{stats['replies_sent']}</b>\n"
        f"Сообщений получено: <b>{stats['messages_received']}</b>\n"
        f"Уникальных пользователей: <b>{stats['unique_users']}</b>\n\n"
        "Когда пользователь пишет в ЛС аккаунту,\n"
        f"автоответчик отправляет ссылку на {settings.PRODUCT_NAME}.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="⏸ Остановить" if auto_responder.is_running else "▶️ Запустить",
                callback_data="com_autoresponder_toggle",
            )],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back_commenting")],
        ]),
    )


@router.callback_query(F.data == "com_autoresponder_toggle")
async def cb_com_autoresponder_toggle(callback: CallbackQuery):
    if auto_responder.is_running:
        await auto_responder.stop()
        await callback.answer("Автоответчик остановлен")
    else:
        await auto_responder.start()
        await callback.answer("Автоответчик запущен")

    # Возвращаемся в меню автоответчика
    status = "Работает" if auto_responder.is_running else "Остановлен"
    stats = auto_responder.get_stats()
    await callback.message.edit_text(
        "💬 <b>Автоответчик ЛС</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Статус: <b>{status}</b>\n"
        f"Ответов: <b>{stats['replies_sent']}</b>\n"
        f"Получено: <b>{stats['messages_received']}</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="⏸ Остановить" if auto_responder.is_running else "▶️ Запустить",
                callback_data="com_autoresponder_toggle",
            )],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back_commenting")],
        ]),
    )


# --- Движок нейрокомментирования ---

@router.callback_query(F.data == "engine_menu")
async def cb_engine_menu(callback: CallbackQuery):
    await callback.answer()
    status = "выключен"
    if _is_distributed_production():
        report = await _collect_runtime_snapshot()
        status = "включён" if bool((report.get("autopilot") or {}).get("enabled")) else "выключен"
    else:
        status = "включён" if commenting_engine.is_running else "выключен"
    await callback.message.edit_text(
        f"🚀 <b>Управление автопилотом</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Сейчас автопилот: <b>{status}</b>\n\n"
        "Когда автопилот включён, сервис сам:\n"
        "• отслеживает новые публикации\n"
        "• использует только готовые аккаунты\n"
        "• берёт только каналы, отмеченные для работы\n"
        "• сохраняет историю действий",
        parse_mode=ParseMode.HTML,
        reply_markup=engine_kb(),
    )


@router.callback_query(F.data == "engine_start")
async def cb_engine_start(callback: CallbackQuery):
    if settings.DISTRIBUTED_QUEUE_MODE:
        report = await _collect_runtime_snapshot()
        if bool((report.get("autopilot") or {}).get("enabled")):
            await callback.answer("Автоматический режим уже включён", show_alert=True)
            return
    elif commenting_engine.is_running:
        await callback.answer("Автоматический режим уже включён", show_alert=True)
        return

    if settings.DISTRIBUTED_QUEUE_MODE and settings.MAX_ACCOUNTS_PER_WORKER <= 0:
        await callback.answer("Автоматический режим пока недоступен", show_alert=True)
        await callback.message.edit_text(
            "❌ <b>Автоматический режим недоступен</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "Сервис ещё не готов к запуску.\n"
            "Проверьте внутренние настройки и попробуйте снова.",
            parse_mode=ParseMode.HTML,
            reply_markup=engine_kb(),
        )
        return

    accounts = await account_mgr.load_accounts()
    channels = await channel_db.get_all_active()

    if not accounts:
        await callback.answer("Сначала добавьте аккаунты", show_alert=True)
        return

    await callback.answer("Включаю автоматический режим...")

    try:
        if settings.DISTRIBUTED_QUEUE_MODE:
            await ops_api_post("/v1/autopilot/toggle", {"enabled": True})
            report = await _collect_runtime_snapshot()
            active_commenting = int(report["accounts"]["lifecycle"].get("active_commenting", 0)) + int(report["accounts"]["lifecycle"].get("execution_ready", 0))
            publishable = int(report["channels"].get("publishable", 0))
            await callback.message.edit_text(
                "🚀 <b>Автоматический режим включён</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                f"✅ Готово аккаунтов: <b>{active_commenting}</b>\n"
                f"📢 Готово каналов: <b>{publishable}</b>\n\n"
                "Теперь сервис сам отслеживает новые публикации\n"
                "и использует только готовые аккаунты и каналы.",
                parse_mode=ParseMode.HTML,
                reply_markup=engine_kb(),
            )
            return

        # Legacy local mode: bot itself connects account pool.
        results = await account_mgr.connect_batch(report_every=15)
        connected = sum(1 for s in results.values() if s == "connected")

        if connected == 0:
            await callback.message.edit_text(
                "❌ <b>Не удалось подключить аккаунты</b>\n\n"
                "Проверьте прокси и сессии.",
                parse_mode=ParseMode.HTML,
                reply_markup=engine_kb(),
            )
            return

        await commenting_engine.start()
        await callback.message.edit_text(
            "🚀 <b>Автоматический режим включён</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"✅ Готово аккаунтов: <b>{connected}/{len(accounts)}</b>\n"
            f"📢 Каналов в работе: <b>{len(channels)}</b>\n\n"
            "Система работает самостоятельно.\n"
            "Дальше можно смотреть состояние и историю.",
            parse_mode=ParseMode.HTML,
            reply_markup=engine_kb(),
        )
    except Exception as exc:
        log.error(f"Ошибка запуска engine: {exc}")
        await callback.message.edit_text(
            "❌ <b>Не удалось включить автоматический режим</b>\n\n"
            "Попробуйте ещё раз чуть позже.",
            parse_mode=ParseMode.HTML,
            reply_markup=engine_kb(),
        )


@router.callback_query(F.data == "engine_stop")
async def cb_engine_stop(callback: CallbackQuery):
    if settings.DISTRIBUTED_QUEUE_MODE:
        report = await _collect_runtime_snapshot()
        if not bool((report.get("autopilot") or {}).get("enabled")):
            await callback.answer("Автоматический режим уже выключен", show_alert=True)
            return
        await callback.answer("Останавливаю...")
        await ops_api_post("/v1/autopilot/toggle", {"enabled": False})
        await callback.message.edit_text(
            "⏹ <b>Автоматический режим выключен</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "Сервис больше не берёт новые публикации в работу.\n"
            "Текущий статус можно проверить в разделе «Обзор».",
            parse_mode=ParseMode.HTML,
            reply_markup=engine_kb(),
        )
        return

    if not commenting_engine.is_running:
        await callback.answer("Автоматический режим уже выключен", show_alert=True)
        return

    await callback.answer("Останавливаю...")
    await commenting_engine.stop()

    stats = commenting_engine.get_stats()
    engine_stats = stats["engine"]

    await callback.message.edit_text(
        "⏹ <b>Автоматический режим выключен</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Комментариев: <b>{engine_stats['comments_sent']}</b>\n"
        f"Подготовительных действий: <b>{engine_stats['warmup_actions']}</b>\n"
        f"Ошибок: <b>{engine_stats['errors']}</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=engine_kb(),
    )


@router.callback_query(F.data == "engine_status")
async def cb_engine_status(callback: CallbackQuery, db_user: User = None):
    await callback.answer()

    if _is_distributed_production():
        report = await _collect_runtime_snapshot()
        await callback.message.edit_text(
            _format_runtime_snapshot(report, title="📊 <b>Состояние автоматического режима</b>"),
            parse_mode=ParseMode.HTML,
            reply_markup=engine_kb(),
        )
        return

    stats = commenting_engine.get_stats()
    engine_stats = stats["engine"]
    pool = stats["pool"]
    poster_stats = stats.get("poster", {})

    status = "ЗАПУЩЕН" if stats["running"] else "ОСТАНОВЛЕН"
    monitor = "Да" if stats["monitor_running"] else "Нет"
    mode = "Distributed" if stats.get("distributed_mode") else "Legacy local"

    await callback.message.edit_text(
        f"📊 <b>Состояние автоматического режима</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Статус: <b>{status}</b>\n"
        f"Мониторинг: <b>{monitor}</b>\n\n"
        f"💬 Отправлено: <b>{engine_stats['comments_sent']}</b>\n"
        f"❌ Ошибок: <b>{engine_stats['errors']}</b>\n"
        f"🎨 Подготовительных действий: <b>{engine_stats['warmup_actions']}</b>\n"
        f"🔗 Подключено аккаунтов: <b>{pool['connected']}/{pool['max_concurrent']}</b>\n",
        parse_mode=ParseMode.HTML,
        reply_markup=engine_kb(),
    )


@router.callback_query(F.data == "engine_batch_connect")
async def cb_engine_batch_connect(callback: CallbackQuery):
    if settings.DISTRIBUTED_QUEUE_MODE:
        await callback.answer("В distributed режиме подключение выполняют worker-ы", show_alert=True)
        return

    await callback.answer("Batch-подключение...")

    await callback.message.edit_text(
        "🔌 <b>Batch-подключение аккаунтов...</b>\n\n"
        "Подключение пачками по 15 с задержкой.",
        parse_mode=ParseMode.HTML,
    )

    results = await account_mgr.connect_batch(report_every=15)
    connected = sum(1 for s in results.values() if s == "connected")
    failed = sum(1 for s in results.values() if s != "connected")

    await callback.message.edit_text(
        f"🔌 <b>Batch-подключение завершено</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Подключено: <b>{connected}</b>\n"
        f"Ошибок: <b>{failed}</b>\n"
        f"Всего: <b>{len(results)}</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=engine_kb(),
    )


@router.callback_query(F.data == "engine_subscribe")
async def cb_engine_subscribe(callback: CallbackQuery):
    if settings.DISTRIBUTED_QUEUE_MODE:
        await callback.answer("В distributed режиме действие выполняют worker-ы", show_alert=True)
        return

    connected = session_mgr.get_connected_phones()
    if not connected:
        await callback.answer("Нет подключённых аккаунтов!", show_alert=True)
        return

    await callback.answer("Подписка с решением капч...")

    await callback.message.edit_text(
        f"📢 <b>Подписка на каналы + капча</b>\n\n"
        f"Аккаунтов: {len(connected)}\n"
        f"Решаю математические капчи автоматически.",
        parse_mode=ParseMode.HTML,
    )

    total = await channel_subscriber.subscribe_all_accounts()

    await callback.message.edit_text(
        f"📢 <b>Подписка завершена</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Аккаунтов: <b>{total['accounts']}</b>\n"
        f"Новых подписок: <b>{total['subscribed']}</b>\n"
        f"Уже были: <b>{total['already']}</b>\n"
        f"Ошибок: <b>{total['failed']}</b>\n"
        f"Капч решено: <b>{channel_subscriber.get_stats().get('captcha_solved', 0)}</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=engine_kb(),
    )


@router.callback_query(F.data == "back_commenting")
async def cb_back_commenting(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer()
    await callback.message.edit_text(
        "💬 <b>Автоматический режим</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=commenting_kb(),
    )


# --- Парсер ---

def _parser_filters_text() -> str:
    comments_mode = "только с комментариями" if settings.PARSER_REQUIRE_COMMENTS else "любые"
    language_mode = "только RU" if settings.PARSER_REQUIRE_RUSSIAN else "любой язык"
    return (
        "📊 <b>Фильтры парсера</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👥 Мин. подписчиков: <b>{settings.PARSER_MIN_SUBSCRIBERS}</b>\n"
        f"💬 Комментарии: <b>{comments_mode}</b>\n"
        f"🇷🇺 Язык: <b>{language_mode}</b>\n"
        f"🎯 Stage-1 limit: <b>{settings.PARSER_STAGE1_LIMIT}</b>\n\n"
        "Эти фильтры применяются к поиску по ключам и тематикам."
    )


def _parser_filters_kb() -> InlineKeyboardMarkup:
    comments_toggle_text = (
        "💬 Комментарии: ВКЛ" if settings.PARSER_REQUIRE_COMMENTS else "💬 Комментарии: ВЫКЛ"
    )
    ru_toggle_text = (
        "🇷🇺 Русский: ВКЛ" if settings.PARSER_REQUIRE_RUSSIAN else "🇷🇺 Русский: ВЫКЛ"
    )
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"👥 Мин. подписчиков: {settings.PARSER_MIN_SUBSCRIBERS}", callback_data="parse_filters_set_min_subs")],
        [InlineKeyboardButton(text=comments_toggle_text, callback_data="parse_filters_toggle_comments")],
        [InlineKeyboardButton(text=ru_toggle_text, callback_data="parse_filters_toggle_russian")],
        [InlineKeyboardButton(text=f"🎯 Stage-1 limit: {settings.PARSER_STAGE1_LIMIT}", callback_data="parse_filters_set_stage1")],
        [InlineKeyboardButton(text="♻️ Сбросить по умолчанию", callback_data="parse_filters_reset")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_parser")],
    ])


@router.callback_query(F.data == "parse_filters")
async def cb_parse_filters(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    await callback.message.edit_text(
        _parser_filters_text(),
        parse_mode=ParseMode.HTML,
        reply_markup=_parser_filters_kb(),
    )


@router.callback_query(F.data == "parse_filters_set_min_subs")
async def cb_parse_filters_set_min_subs(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(ParserStates.waiting_filter_min_subscribers)
    await callback.message.edit_text(
        "👥 <b>Мин. подписчиков</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Текущее значение: <b>{settings.PARSER_MIN_SUBSCRIBERS}</b>\n\n"
        "Введите новое число (0..2_000_000):",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="parse_filters")],
        ]),
    )


@router.callback_query(F.data == "parse_filters_set_stage1")
async def cb_parse_filters_set_stage1(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(ParserStates.waiting_filter_stage1_limit)
    await callback.message.edit_text(
        "🎯 <b>Stage-1 limit</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Текущее значение: <b>{settings.PARSER_STAGE1_LIMIT}</b>\n\n"
        "Введите новое число (5..200):",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="parse_filters")],
        ]),
    )


@router.callback_query(F.data == "parse_filters_toggle_comments")
async def cb_parse_filters_toggle_comments(callback: CallbackQuery):
    await callback.answer()
    settings.PARSER_REQUIRE_COMMENTS = not settings.PARSER_REQUIRE_COMMENTS
    _update_env("PARSER_REQUIRE_COMMENTS", str(settings.PARSER_REQUIRE_COMMENTS).lower())
    await callback.message.edit_text(
        _parser_filters_text(),
        parse_mode=ParseMode.HTML,
        reply_markup=_parser_filters_kb(),
    )


@router.callback_query(F.data == "parse_filters_toggle_russian")
async def cb_parse_filters_toggle_russian(callback: CallbackQuery):
    await callback.answer()
    settings.PARSER_REQUIRE_RUSSIAN = not settings.PARSER_REQUIRE_RUSSIAN
    _update_env("PARSER_REQUIRE_RUSSIAN", str(settings.PARSER_REQUIRE_RUSSIAN).lower())
    await callback.message.edit_text(
        _parser_filters_text(),
        parse_mode=ParseMode.HTML,
        reply_markup=_parser_filters_kb(),
    )


@router.callback_query(F.data == "parse_filters_reset")
async def cb_parse_filters_reset(callback: CallbackQuery):
    await callback.answer("Сброшено")
    settings.PARSER_MIN_SUBSCRIBERS = 500
    settings.PARSER_REQUIRE_COMMENTS = True
    settings.PARSER_REQUIRE_RUSSIAN = True
    settings.PARSER_STAGE1_LIMIT = 30
    _update_env("PARSER_MIN_SUBSCRIBERS", "500")
    _update_env("PARSER_REQUIRE_COMMENTS", "true")
    _update_env("PARSER_REQUIRE_RUSSIAN", "true")
    _update_env("PARSER_STAGE1_LIMIT", "30")
    await callback.message.edit_text(
        _parser_filters_text(),
        parse_mode=ParseMode.HTML,
        reply_markup=_parser_filters_kb(),
    )


@router.callback_query(F.data == "parse_keywords")
async def cb_parse_keywords(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(ParserStates.waiting_keywords)
    await callback.message.edit_text(
        "🔍 <b>Поиск по ключевым словам</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "Отправьте ключевые слова через запятую:\n\n"
        "Примеры запросов:\n"
        "• <code>vpn, впн</code>\n"
        "• <code>нейросети, искусственный интеллект</code>\n"
        "• <code>instagram, заблокированные сервисы</code>\n"
        "• <code>chatgpt, midjourney, claude</code>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back_parser")],
        ]),
    )


@router.callback_query(F.data == "parse_topic")
async def cb_parse_topic(callback: CallbackQuery):
    await callback.answer()
    await callback.message.edit_text(
        "📂 <b>Поиск по тематике</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "Выберите тематику:",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔐 VPN и обход блокировок", callback_data="topic_vpn")],
            [InlineKeyboardButton(text="🤖 Нейросети и AI", callback_data="topic_ai")],
            [InlineKeyboardButton(text="📱 Instagram и соцсети", callback_data="topic_social")],
            [InlineKeyboardButton(text="💻 IT и технологии", callback_data="topic_it")],
            [InlineKeyboardButton(text="🎬 Стриминг (Netflix, YouTube)", callback_data="topic_streaming")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back_parser")],
        ]),
    )


@router.callback_query(F.data.startswith("topic_"))
async def cb_topic_select(callback: CallbackQuery, db_user: User = None):
    topic_key = callback.data.replace("topic_", "", 1)
    topic_name = TOPIC_TITLES.get(topic_key, "Неизвестная тема")
    keywords = ChannelDiscovery.PRESET_TOPIC_KEYWORDS.get(topic_key, [])
    if not keywords:
        await callback.answer("Тематика не найдена", show_alert=True)
        return

    await callback.answer(f"Поиск: {topic_name}")
    await callback.message.edit_text(
        f"⏳ <b>Ищу каналы: {topic_name}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Ключи: <code>{escape(', '.join(keywords))}</code>\n"
        "<i>Это может занять до 1-2 минут...</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="parse_topic")],
        ]),
    )

    try:
        response = await ops_api_post(
            "/v1/parser/search",
            {
                "keywords": keywords,
                "topic": topic_key,
                "user_id": _tenant_write_scope_user_id(db_user),
                "min_subscribers": settings.PARSER_MIN_SUBSCRIBERS,
                "require_comments": settings.PARSER_REQUIRE_COMMENTS,
                "require_russian": settings.PARSER_REQUIRE_RUSSIAN,
                "stage1_limit": settings.PARSER_STAGE1_LIMIT,
            },
        )
    except Exception as exc:
        await callback.message.edit_text(
            f"❌ <b>Ошибка поиска</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"<code>{escape(str(exc))}</code>\n\n"
            "Проверьте TELEGRAM_API_ID/HASH, аккаунты и прокси.",
            parse_mode=ParseMode.HTML,
            reply_markup=parser_kb(),
        )
        return

    await callback.message.edit_text(
        f"✅ <b>Поиск запущен: {topic_name}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Ключи: <code>{escape(', '.join(keywords))}</code>\n"
        "Подходящие каналы будут добавлены в список найденных.\n"
        "Через 1-2 минуты откройте:\n"
        "📢 Каналы → 📝 Проверить найденные каналы\n\n"
        f"ID задачи: <code>{escape(str(response.get('task_id') or '—'))}</code>",
        parse_mode=ParseMode.HTML,
        reply_markup=parser_kb(),
    )


@router.callback_query(F.data == "parse_similar")
async def cb_parse_similar(callback: CallbackQuery, db_user: User = None):
    await callback.answer()
    usernames = await channel_db.get_usernames(user_id=_tenant_read_scope_user_id(db_user))
    if not usernames:
        await callback.message.edit_text(
            "🔗 <b>Похожие каналы</b>\n\n"
            "<i>Сначала добавьте каналы в базу!</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=parser_kb(),
        )
        return

    await callback.message.edit_text(
        f"🔗 <b>Поиск похожих каналов</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Каналов в базе: <b>{len(usernames)}</b>\n"
        f"Будет проанализировано до 20 каналов\n"
        f"для поиска похожих по названию.\n\n"
        f"<i>Это может занять 1-2 минуты.</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Найти похожие", callback_data="parse_similar_run")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back_parser")],
        ]),
    )


@router.callback_query(F.data == "parse_similar_run")
async def cb_parse_similar_run(callback: CallbackQuery, db_user: User = None):
    await callback.answer("Поиск запущен...")
    await callback.message.edit_text("⏳ Запускаю поиск похожих каналов...")

    try:
        response = await ops_api_post(
            "/v1/parser/similar",
            {"user_id": _tenant_write_scope_user_id(db_user)},
        )
        await callback.message.edit_text(
            "🔗 <b>Поиск похожих каналов запущен</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "Результаты будут сохранены в список найденных каналов.\n"
            "Через 1-2 минуты откройте:\n"
            "📢 Каналы → 📝 Проверить найденные каналы\n\n"
            f"ID задачи: <code>{escape(str(response.get('task_id') or '—'))}</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=parser_kb(),
        )

    except Exception as exc:
        await callback.message.edit_text(
            f"❌ Ошибка поиска: <code>{escape(str(exc))}</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=parser_kb(),
        )


@router.callback_query(F.data == "parse_export")
async def cb_parse_export(callback: CallbackQuery):
    await callback.answer()
    try:
        count = await channel_db.export_to_txt()
        await callback.message.edit_text(
            f"💾 <b>Экспорт завершён</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Экспортировано каналов: <b>{count}</b>\n"
            f"Файл: <code>data/channels_export.txt</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=parser_kb(),
        )
    except Exception as exc:
        await callback.message.edit_text(
            f"❌ Ошибка экспорта: <code>{escape(str(exc))}</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=parser_kb(),
        )


@router.callback_query(F.data == "parse_digest_send")
async def cb_parse_digest_send(callback: CallbackQuery, db_user: User = None):
    await callback.answer("🗞 Отправляю сводку...")
    try:
        payload = await ops_api_post(
            "/v1/digest/send-summary",
            {"user_id": _tenant_write_scope_user_id(db_user)},
            timeout=30,
        )
    except Exception:
        await callback.message.edit_text(
            "❌ <b>Не удалось отправить сводку</b>\n\n"
            "Проверьте настройки digest-бота и попробуйте ещё раз.",
            parse_mode=ParseMode.HTML,
            reply_markup=parser_kb(),
        )
        return

    if not payload.get("ok"):
        await callback.message.edit_text(
            "❌ <b>Сводка не отправлена</b>\n\n"
            f"Причина: <code>{escape(str(payload.get('error') or 'unknown'))}</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=parser_kb(),
        )
        return

    await callback.message.edit_text(
        "🗞 <b>Сводка отправлена</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Chat ID: <code>{escape(str(payload.get('chat_id') or 'не задан'))}</code>\n"
        f"Message ID: <b>{escape(str(payload.get('message_id') or '—'))}</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=parser_kb(),
    )


@router.callback_query(F.data.startswith("parse_"))
async def cb_parse_other(callback: CallbackQuery):
    await callback.answer("🔜 В разработке")


@router.callback_query(F.data == "back_parser")
async def cb_back_parser(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    await callback.message.edit_text(
        "🔍 <b>Поиск каналов</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=parser_kb(),
    )


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Действие отменено.",
        reply_markup=main_menu_kb(),
    )


@router.message(Command("spambot_start"))
async def cmd_spambot_start(message: Message, db_user: User = None):
    if not db_user or not db_user.is_admin:
        return
    if settings.HUMAN_GATED_PACKAGING or settings.HUMAN_GATED_COMMENTS:
        await message.answer("Этот путь отключён в production flow. Используйте ручную перепроверку аккаунта.")
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or parts[1].strip().upper() != "CONFIRM":
        await message.answer(
            "Ручной запуск SpamBot auto-appeal.\n"
            "Подтверждение: <code>/spambot_start CONFIRM</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    if not settings.AUTO_SPAMBOT_APPEAL_ENABLED:
        await message.answer(
            "AUTO_SPAMBOT_APPEAL_ENABLED=false. Включите флаг в .env для ручного запуска.",
            parse_mode=ParseMode.HTML,
        )
        return
    await spambot_auto_appeal.start()
    await message.answer("✅ SpamBot auto-appeal запущен вручную.")


@router.message(Command("spambot_stop"))
async def cmd_spambot_stop(message: Message, db_user: User = None):
    if not db_user or not db_user.is_admin:
        return
    if settings.HUMAN_GATED_PACKAGING or settings.HUMAN_GATED_COMMENTS:
        await message.answer("Этот путь отключён в production flow.")
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or parts[1].strip().upper() != "CONFIRM":
        await message.answer(
            "Остановка SpamBot auto-appeal.\n"
            "Подтверждение: <code>/spambot_stop CONFIRM</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    await spambot_auto_appeal.stop()
    await message.answer("🛑 SpamBot auto-appeal остановлен.")


@router.message(Command("blacklist"))
async def cmd_blacklist(message: Message, db_user: User = None):
    if not db_user:
        return

    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].lstrip("-").isdigit():
        await message.answer("Использование: <code>/blacklist CHANNEL_ID</code>", parse_mode=ParseMode.HTML)
        return

    channel_id = int(parts[1])
    ok = await channel_db.blacklist_channel_ref(
        channel_id,
        user_id=_tenant_write_scope_user_id(db_user),
    )
    if not ok:
        await message.answer(
            f"Канал <code>{channel_id}</code> не найден в write-scope текущего tenant.",
            parse_mode=ParseMode.HTML,
        )
        return
    await message.answer(
        f"Канал <code>{channel_id}</code> добавлен в чёрный список.",
        parse_mode=ParseMode.HTML,
    )
    await sync_to_sheets_snapshot()


@router.message(ParserStates.waiting_filter_min_subscribers, F.text)
async def process_parser_filter_min_subscribers(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    try:
        value = int((message.text or "").strip())
        if not 0 <= value <= 2_000_000:
            raise ValueError
    except ValueError:
        await message.answer("Введите целое число от 0 до 2_000_000.")
        return

    settings.PARSER_MIN_SUBSCRIBERS = value
    _update_env("PARSER_MIN_SUBSCRIBERS", str(value))
    await state.clear()
    await message.answer(
        _parser_filters_text(),
        parse_mode=ParseMode.HTML,
        reply_markup=_parser_filters_kb(),
    )


@router.message(ParserStates.waiting_filter_stage1_limit, F.text)
async def process_parser_filter_stage1_limit(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    try:
        value = int((message.text or "").strip())
        if not 5 <= value <= 200:
            raise ValueError
    except ValueError:
        await message.answer("Введите целое число от 5 до 200.")
        return

    settings.PARSER_STAGE1_LIMIT = value
    _update_env("PARSER_STAGE1_LIMIT", str(value))
    await state.clear()
    await message.answer(
        _parser_filters_text(),
        parse_mode=ParseMode.HTML,
        reply_markup=_parser_filters_kb(),
    )


@router.message(ParserStates.waiting_keywords, F.text)
async def process_keywords_input(message: Message, state: FSMContext, db_user: User = None):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    keywords = parse_keywords(message.text or "")
    if not keywords:
        await message.answer("Введите ключевые слова через запятую. Пример: <code>vpn, впн, proxy</code>", parse_mode=ParseMode.HTML)
        return

    await state.clear()
    progress = await message.answer(
        "⏳ Запускаю поиск каналов...\n"
        f"Ключи: <code>{escape(', '.join(keywords))}</code>",
        parse_mode=ParseMode.HTML,
    )

    try:
        response = await ops_api_post(
            "/v1/parser/search",
            {
                "keywords": keywords,
                "user_id": _tenant_write_scope_user_id(db_user),
                "min_subscribers": settings.PARSER_MIN_SUBSCRIBERS,
                "require_comments": settings.PARSER_REQUIRE_COMMENTS,
                "require_russian": settings.PARSER_REQUIRE_RUSSIAN,
                "stage1_limit": settings.PARSER_STAGE1_LIMIT,
            },
        )
    except Exception as exc:
        await progress.edit_text(
            "❌ <b>Ошибка поиска каналов</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"<code>{escape(str(exc))}</code>\n\n"
            "Проверьте TELEGRAM_API_ID/HASH, аккаунты и прокси.",
            parse_mode=ParseMode.HTML,
            reply_markup=parser_kb(),
        )
        return

    await progress.edit_text(
        "✅ <b>Поиск запущен</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Ключи: <code>{escape(', '.join(keywords))}</code>\n"
        "Подходящие каналы будут сохранены в список найденных.\n"
        "Через 1-2 минуты откройте:\n"
        "📢 Каналы → 📝 Проверить найденные каналы\n\n"
        f"ID задачи: <code>{escape(str(response.get('task_id') or '—'))}</code>",
        parse_mode=ParseMode.HTML,
        reply_markup=parser_kb(),
    )


@router.message(ParserStates.waiting_channel, F.text)
async def process_add_channel_input(message: Message, state: FSMContext, db_user: User = None):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    ref = normalize_channel_ref(message.text or "")
    if not ref:
        await message.answer("Отправьте @username или ссылку вида <code>https://t.me/channel_name</code>", parse_mode=ParseMode.HTML)
        return

    await state.clear()
    progress = await message.answer("⏳ Отправляю канал на проверку и сохранение...")

    try:
        response = await ops_api_post(
            "/v1/channels/manual-add",
            {
                "ref": ref,
                "user_id": _tenant_write_scope_user_id(db_user),
            },
        )
    except Exception as exc:
        await progress.edit_text(
            "❌ <b>Не удалось добавить канал</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"<code>{escape(str(exc))}</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=channels_kb(),
        )
        return

    await progress.edit_text(
        "✅ <b>Канал отправлен в работу</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "Сервис проверит канал отдельным процессом и добавит его в базу.\n"
        "Через минуту откройте раздел «Каналы» и обновите список.\n\n"
        f"ID задачи: <code>{escape(str(response.get('task_id') or '—'))}</code>",
        parse_mode=ParseMode.HTML,
        reply_markup=channels_kb(),
    )


# --- Настройки ---

# Whitelist ключей, которые разрешено обновлять через бота
_ALLOWED_ENV_KEYS = frozenset({
    "ADMIN_TELEGRAM_ID",
    "MAX_COMMENTS_PER_ACCOUNT_PER_DAY",
    "MIN_DELAY_BETWEEN_COMMENTS_SEC",
    "MAX_DELAY_BETWEEN_COMMENTS_SEC",
    "COMMENT_COOLDOWN_AFTER_ERROR_SEC",
    "MIN_EXISTING_COMMENTS_BEFORE_COMMENT",
    "MIN_COMMENTS_RECHECK_MAX_ATTEMPTS",
    "SCENARIO_B_RATIO",
    "PRODUCT_NAME",
    "PRODUCT_BOT_USERNAME",
    "PRODUCT_BOT_LINK",
    "PRODUCT_CHANNEL_LINK",
    "PRODUCT_SHORT_DESC",
    "PRODUCT_FEATURES",
    "PRODUCT_CATEGORY",
    "PRODUCT_CHANNEL_PREFIX",
    "MONITOR_POLL_INTERVAL_SEC",
    "POST_MAX_AGE_HOURS",
    "LOG_LEVEL",
    "PROXY_ROTATING",
    "PROXY_STICKY_FORMAT",
    "AUTO_SPAMBOT_APPEAL_ENABLED",
    "AUTO_SPAMBOT_APPEAL_INTERVAL_SEC",
    "AUTO_SPAMBOT_CHECK_COOLDOWN_HOURS",
    "AUTO_SPAMBOT_APPEAL_COOLDOWN_HOURS",
    "AUTO_SPAMBOT_APPEAL_MAX_STEPS",
    "AUTO_SPAMBOT_APPEAL_BATCH_SIZE",
    "AUTO_SPAMBOT_APPEAL_EMAIL",
    "AUTO_SPAMBOT_APPEAL_REG_YEAR",
    "COMPLIANCE_MODE",
    "POLICY_RULES_PATH",
    "NEW_ACCOUNT_LAUNCH_MODE",
    "STRICT_PARSER_ONLY",
    "ENABLE_EMOJI_SWAP",
    "FROZEN_PROBE_ON_CONNECT",
    "FROZEN_PROBE_BEFORE_PACKAGING",
    "FROZEN_PROBE_BEFORE_PARSER",
    "PINNED_PHONE_REQUIRED",
    "PARSER_ONLY_PHONE",
    "PARSER_MIN_SUBSCRIBERS",
    "PARSER_REQUIRE_COMMENTS",
    "PARSER_REQUIRE_RUSSIAN",
    "PARSER_STAGE1_LIMIT",
    "MANUAL_GATE_REQUIRED",
    "ENABLE_CLIENT_WIZARD",
    "ENABLE_ADMIN_LEGACY_TOOLS",
    "STRICT_SLO_WINDOW_DAYS",
})


def _update_env(key: str, value: str):
    """Обновить значение в .env файле (только из whitelist)."""
    from pathlib import Path
    # Санитизация: удалить переводы строк и возвраты каретки
    value = value.replace("\n", "").replace("\r", "")
    key = key.replace("\n", "").replace("\r", "").replace("=", "").strip()

    # Whitelist: запрещаем запись произвольных ключей
    if key not in _ALLOWED_ENV_KEYS:
        log.warning(f"Попытка записи запрещённого ключа в .env: {key}")
        return

    env_path = Path(settings.model_config["env_file"])
    if not env_path.exists():
        log.warning(f".env не найден: {env_path}, настройка {key} не сохранена на диск")
        return

    lines = env_path.read_text(encoding="utf-8").splitlines()
    found = False
    for i, line in enumerate(lines):
        if line.strip().startswith(f"{key}=") or line.strip().startswith(f"{key} ="):
            lines[i] = f"{key}={value}"
            found = True
            break
    if not found:
        lines.append(f"{key}={value}")

    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@router.callback_query(F.data == "set_limits")
async def cb_set_limits(callback: CallbackQuery):
    await callback.answer()
    await callback.message.edit_text(
        "⏱ <b>Темп работы</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Максимум действий в день: <b>{settings.MAX_COMMENTS_PER_ACCOUNT_PER_DAY}</b>\n"
        f"Мин. пауза: <b>{settings.MIN_DELAY_BETWEEN_COMMENTS_SEC} сек</b>\n"
        f"Макс. пауза: <b>{settings.MAX_DELAY_BETWEEN_COMMENTS_SEC} сек</b>\n"
        f"Пауза после ошибки: <b>{settings.COMMENT_COOLDOWN_AFTER_ERROR_SEC} сек</b>\n"
        f"Повторных проверок: <b>{settings.MIN_COMMENTS_RECHECK_MAX_ATTEMPTS}</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📊 Изменить лимит", callback_data="set_change_limit")],
            [InlineKeyboardButton(text="⏱ Изменить мин. паузу", callback_data="set_change_min_delay")],
            [InlineKeyboardButton(text="⏱ Изменить макс. паузу", callback_data="set_change_max_delay")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back_settings")],
        ]),
    )


@router.callback_query(F.data == "set_change_limit")
async def cb_set_change_limit(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(SettingsStates.waiting_daily_limit)
    await callback.message.edit_text(
        f"📊 Текущий лимит: <b>{settings.MAX_COMMENTS_PER_ACCOUNT_PER_DAY}</b> коммент/день\n\n"
        "Введите новое значение (число от 5 до 100):",
        parse_mode=ParseMode.HTML,
    )


@router.message(SettingsStates.waiting_daily_limit, F.text)
async def process_set_daily_limit(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    try:
        value = int(message.text.strip())
        if not 5 <= value <= 100:
            raise ValueError
    except ValueError:
        await message.answer("Введите число от 5 до 100.")
        return

    await state.clear()
    settings.MAX_COMMENTS_PER_ACCOUNT_PER_DAY = value
    _update_env("MAX_COMMENTS_PER_ACCOUNT_PER_DAY", str(value))
    await message.answer(
        f"✅ Лимит комментариев/день обновлён: <b>{value}</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_kb(),
    )


@router.callback_query(F.data == "set_change_min_delay")
async def cb_set_change_min_delay(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(SettingsStates.waiting_min_delay)
    await callback.message.edit_text(
        f"⏱ Текущая мин. задержка: <b>{settings.MIN_DELAY_BETWEEN_COMMENTS_SEC} сек</b>\n\n"
        "Введите новое значение (секунды, от 30 до 600):",
        parse_mode=ParseMode.HTML,
    )


@router.message(SettingsStates.waiting_min_delay, F.text)
async def process_set_min_delay(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    try:
        value = int(message.text.strip())
        if not 30 <= value <= 600:
            raise ValueError
    except ValueError:
        await message.answer("Введите число от 30 до 600.")
        return

    await state.clear()
    settings.MIN_DELAY_BETWEEN_COMMENTS_SEC = value
    _update_env("MIN_DELAY_BETWEEN_COMMENTS_SEC", str(value))
    await message.answer(
        f"✅ Мин. задержка обновлена: <b>{value} сек</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_kb(),
    )


@router.callback_query(F.data == "set_change_max_delay")
async def cb_set_change_max_delay(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(SettingsStates.waiting_max_delay)
    await callback.message.edit_text(
        f"⏱ Текущая макс. задержка: <b>{settings.MAX_DELAY_BETWEEN_COMMENTS_SEC} сек</b>\n\n"
        "Введите новое значение (секунды, от 60 до 1800):",
        parse_mode=ParseMode.HTML,
    )


@router.message(SettingsStates.waiting_max_delay, F.text)
async def process_set_max_delay(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    try:
        value = int(message.text.strip())
        if not 60 <= value <= 1800:
            raise ValueError
    except ValueError:
        await message.answer("Введите число от 60 до 1800.")
        return

    await state.clear()
    settings.MAX_DELAY_BETWEEN_COMMENTS_SEC = value
    _update_env("MAX_DELAY_BETWEEN_COMMENTS_SEC", str(value))
    await message.answer(
        f"✅ Макс. задержка обновлена: <b>{value} сек</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_kb(),
    )


@router.callback_query(F.data == "set_product")
async def cb_set_product(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(SettingsStates.waiting_product_link)
    await callback.message.edit_text(
        "🔗 <b>Ссылка на продукт</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Имя: <code>{settings.PRODUCT_NAME}</code>\n"
        f"Бот: <code>{settings.product_bot_mention}</code>\n"
        f"Ссылка: <code>{settings.PRODUCT_BOT_LINK}</code>\n"
        f"Категория: <code>{settings.PRODUCT_CATEGORY}</code>\n"
        f"Описание: <code>{settings.PRODUCT_SHORT_DESC}</code>\n\n"
        "Отправьте новую ссылку на бот или продукт:\n"
        "<code>https://t.me/...</code> или <code>@username</code>",
        parse_mode=ParseMode.HTML,
    )


@router.message(SettingsStates.waiting_product_link, F.text)
async def process_set_product(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    link = message.text.strip()
    if not link.startswith("https://t.me/") and not link.startswith("@"):
        await message.answer("Ссылка должна быть в формате https://t.me/... или @username")
        return

    await state.clear()
    settings.PRODUCT_BOT_LINK = link
    _update_env("PRODUCT_BOT_LINK", link)

    # Auto-derive bot username from link
    parsed_username = Settings._parse_bot_username_from_link(link)
    if parsed_username:
        settings.PRODUCT_BOT_USERNAME = parsed_username
        _update_env("PRODUCT_BOT_USERNAME", parsed_username)

    username_info = f"\nUsername: <code>@{parsed_username}</code>" if parsed_username else ""
    await message.answer(
        f"✅ Ссылка продукта обновлена: <code>{escape(link)}</code>{username_info}",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_kb(),
    )


@router.callback_query(F.data == "set_ai")
async def cb_set_ai(callback: CallbackQuery):
    await callback.answer()
    gen_stats = comment_generator.get_stats()
    await callback.message.edit_text(
        "🤖 <b>Тексты и стиль</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Генерация сообщений доступна: <b>{'Да' if gen_stats['ai_available'] else 'Нет'}</b>\n"
        f"Недавних сообщений: <b>{gen_stats['recent_comments']}</b>\n"
        f"Последний успешный режим: <code>{escape(str(gen_stats.get('last_model_used', 'n/a')))}</code>\n\n"
        f"Доп. режим эмодзи: <b>{'Вкл' if comment_poster.emoji_swap_enabled else 'Выкл'}</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=f"{'⏸ Выключить' if comment_poster.emoji_swap_enabled else '▶️ Включить'} режим эмодзи",
                callback_data="set_toggle_swap",
            )],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back_settings")],
        ]),
    )


@router.callback_query(F.data == "set_toggle_swap")
async def cb_set_toggle_swap(callback: CallbackQuery):
    enable_requested = not comment_poster.emoji_swap_enabled
    strict_mode = (settings.COMPLIANCE_MODE or "").strip().lower() == "strict"
    if enable_requested and strict_mode and not settings.ENABLE_EMOJI_SWAP:
        await policy_engine.check(
            "risky_feature_enabled_in_strict",
            {
                "feature": "emoji_swap",
                "strict_mode": True,
                "emergency_flag": bool(settings.ENABLE_EMOJI_SWAP),
                "requested_enable": True,
            },
            worker_id="bot_admin",
        )
        await callback.answer(
            "Emoji Swap запрещён в strict режиме. "
            "Для emergency задайте ENABLE_EMOJI_SWAP=true в .env.",
            show_alert=True,
        )
        return
    comment_poster.emoji_swap_enabled = not comment_poster.emoji_swap_enabled
    status = "включён" if comment_poster.emoji_swap_enabled else "выключен"
    await callback.answer(f"Emoji Swap {status}")
    # Перерисовать меню
    gen_stats = comment_generator.get_stats()
    await callback.message.edit_text(
        "🤖 <b>Тексты и стиль</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Генерация сообщений доступна: <b>{'Да' if gen_stats['ai_available'] else 'Нет'}</b>\n"
        f"Последний успешный режим: <code>{escape(str(gen_stats.get('last_model_used', 'n/a')))}</code>\n\n"
        f"Доп. режим эмодзи: <b>{'Вкл' if comment_poster.emoji_swap_enabled else 'Выкл'}</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=f"{'⏸ Выключить' if comment_poster.emoji_swap_enabled else '▶️ Включить'} режим эмодзи",
                callback_data="set_toggle_swap",
            )],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back_settings")],
        ]),
    )


@router.callback_query(F.data == "set_sheets")
async def cb_set_sheets(callback: CallbackQuery):
    await callback.answer()
    await callback.message.edit_text(
        "📊 <b>Таблица</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Статус выгрузки: <b>{'Подключена' if sheets_storage.is_enabled else 'Выключена'}</b>\n"
        f"Интервал обновления: <b>{settings.SHEETS_SYNC_INTERVAL_SEC} сек</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить сейчас", callback_data="set_sheets_sync_now")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back_settings")],
        ]),
    )


@router.callback_query(F.data == "set_sheets_sync_now")
async def cb_set_sheets_sync_now(callback: CallbackQuery):
    await callback.answer("Синхронизация...")
    try:
        await sync_to_sheets_snapshot()
        await callback.message.edit_text(
            "✅ Таблица обновлена!",
            parse_mode=ParseMode.HTML,
            reply_markup=settings_kb(),
        )
    except Exception as exc:
        await callback.message.edit_text(
            f"❌ Ошибка синхронизации:\n<code>{escape(str(exc)[:200])}</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=settings_kb(),
        )


@router.callback_query(F.data == "set_scenarios")
async def cb_set_scenarios(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    ratio_b = int(settings.SCENARIO_B_RATIO * 100)
    await state.set_state(SettingsStates.waiting_scenario_ratio)
    await callback.message.edit_text(
        "🔄 <b>Баланс сообщений</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Более нейтральный вариант: <b>{100 - ratio_b}%</b>\n"
        f"Более прямой вариант: <b>{ratio_b}%</b>\n\n"
        "Введите новый процент для более прямого варианта (10-50):",
        parse_mode=ParseMode.HTML,
    )


@router.message(SettingsStates.waiting_scenario_ratio, F.text)
async def process_set_scenario_ratio(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    try:
        value = int(message.text.strip())
        if not 10 <= value <= 50:
            raise ValueError
    except ValueError:
        await message.answer("Введите число от 10 до 50.")
        return

    await state.clear()
    ratio = value / 100.0
    settings.SCENARIO_B_RATIO = ratio
    _update_env("SCENARIO_B_RATIO", str(ratio))
    await message.answer(
        f"✅ Баланс обновлён: A={100 - value}% / B={value}%",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_kb(),
    )


@router.callback_query(F.data == "back_settings")
async def cb_back_settings(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    await callback.message.edit_text(
        "🎯 <b>Продукт</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=settings_kb(),
    )


# ============================================================
# Обработка файлов (.session / .json / .txt)
# ============================================================

@router.message(F.document)
async def handle_document(message: Message, db_user: User = None):
    """Обработка загруженных файлов (.session, .json, .txt)."""
    if not db_user:
        return

    doc = message.document
    file_name = doc.file_name or ""
    lower_name = file_name.lower()

    if lower_name.endswith(".session"):
        # Сохранение .session файла
        await message.answer(
            f"📥 Получен файл: <code>{file_name}</code>\n"
            "⏳ Сохраняю...",
            parse_mode=ParseMode.HTML,
        )

        bot = message.bot
        file = await bot.get_file(doc.file_id)
        # Sanitize: оставляем только цифры для предотвращения path traversal
        session_phone = _extract_phone_digits_from_file_name(file_name, ".session")
        if not session_phone:
            await message.answer("⚠️ Имя файла должно содержать номер телефона (цифры).")
            return
        normalized_file_name = f"{session_phone}.session"
        sessions_dir = canonical_session_dir(settings.sessions_path, db_user.id, create=True)
        save_path = sessions_dir / normalized_file_name
        # Проверка что путь не выходит за пределы sessions_path
        if not save_path.resolve().is_relative_to(settings.sessions_path.resolve()):
            await message.answer("⚠️ Недопустимое имя файла.")
            return
        await bot.download_file(file.file_path, str(save_path))

        account_phone = f"+{session_phone}"
        bundle = get_account_upload_bundle(settings.sessions_path, db_user.id, account_phone)
        db_ok, db_status = await _upsert_account_from_session_upload(
            phone=account_phone,
            session_file=normalized_file_name,
            user_id=db_user.id if db_user else None,
            reset_runtime_state=True,
        )
        await _safe_onboarding_step_post(
            account_phone,
            db_user=db_user,
            step_key="upload_session",
            actor="bot:upload_session",
            result="saved",
            notes=f"Загружен файл {normalized_file_name}.",
            payload={
                "file_name": normalized_file_name,
                "bundle_ready": bundle.ready,
                "db_status": db_status,
            },
        )
        if bundle.ready:
            await _safe_onboarding_step_post(
                account_phone,
                db_user=db_user,
                step_key="upload_bundle",
                actor="bot:upload_bundle",
                result="ready",
                notes="Пара .session + .json собрана.",
            )

        await sync_to_sheets_snapshot()
        if db_ok:
            await message.answer(
                f"✅ Файл .session сохранён\n\n"
                f"📱 Телефон: <code>{account_phone}</code>\n"
                f"📁 Файл: <code>{normalized_file_name}</code>\n\n"
                f"📦 Комплект: <b>{_bundle_status_label(bundle)}</b>\n"
                f"🗂 База: <b>{db_status}</b>\n\n"
                f"{_bundle_next_step_text(bundle)}",
                parse_mode=ParseMode.HTML,
            )
        else:
            await message.answer(
                "⚠️ <b>Файл сессии сохранён, но запись в БД не обновилась.</b>\n\n"
                f"📱 Телефон: <code>{account_phone}</code>\n"
                f"📁 Файл: <code>{normalized_file_name}</code>\n"
                f"🗂 БД: <b>{db_status}</b>\n\n"
                "Откройте «Аккаунты -> Обновить список» и попробуйте ещё раз.",
                parse_mode=ParseMode.HTML,
            )
        log.info(f"Session file saved: {normalized_file_name}")

    elif lower_name.endswith(".json"):
        await message.answer(
            f"📥 Получен файл: <code>{file_name}</code>\n"
            "⏳ Проверяю JSON-метаданные...",
            parse_mode=ParseMode.HTML,
        )

        json_phone = _extract_phone_digits_from_file_name(file_name, ".json")
        if not json_phone:
            await message.answer(
                "⚠️ JSON для аккаунта должен называться как номер телефона.\n"
                "Пример: <code>79991234567.json</code>",
                parse_mode=ParseMode.HTML,
            )
            return

        bot = message.bot
        file = await bot.get_file(doc.file_id)
        normalized_file_name = f"{json_phone}.json"
        sessions_dir = canonical_session_dir(settings.sessions_path, db_user.id, create=True)
        save_path = sessions_dir / normalized_file_name
        if not save_path.resolve().is_relative_to(settings.sessions_path.resolve()):
            await message.answer("⚠️ Недопустимое имя файла.")
            return
        await bot.download_file(file.file_path, str(save_path))

        account_phone = f"+{json_phone}"

        try:
            payload = json.loads(save_path.read_text(encoding="utf-8"))
            normalized_payload = validate_and_normalize_account_metadata(
                payload,
                expected_phone=account_phone,
                expected_session_file=f"{json_phone}.session",
            )
            write_normalized_metadata(save_path, normalized_payload)
        except Exception as exc:
            save_path.unlink(missing_ok=True)
            await message.answer(
                "⚠️ JSON невалидный и не сохранён.\n"
                f"Причина: <code>{escape(str(exc))}</code>",
                parse_mode=ParseMode.HTML,
            )
            return

        paired_session_file = f"{json_phone}.session"
        bundle = get_account_upload_bundle(settings.sessions_path, db_user.id, account_phone)
        paired_session_exists = bundle.session_present
        db_ok = True
        db_status = "ожидает .session файл"
        if paired_session_exists:
            db_ok, db_status = await _upsert_account_from_session_upload(
                phone=account_phone,
                session_file=paired_session_file,
                user_id=db_user.id if db_user else None,
                reset_runtime_state=True,
            )
        await _safe_onboarding_step_post(
            account_phone,
            db_user=db_user,
            step_key="upload_metadata",
            actor="bot:upload_metadata",
            result="saved",
            notes=f"Загружен файл {normalized_file_name}.",
            payload={
                "file_name": normalized_file_name,
                "bundle_ready": bundle.ready,
                "db_status": db_status,
            },
        )
        if bundle.ready:
            await _safe_onboarding_step_post(
                account_phone,
                db_user=db_user,
                step_key="upload_bundle",
                actor="bot:upload_bundle",
                result="ready",
                notes="Пара .session + .json собрана.",
            )

        session_status = "найден" if paired_session_exists else "не найден"
        if db_ok:
            await message.answer(
                "✅ JSON-метаданные сохранены\n\n"
                f"📱 Телефон: <code>{account_phone}</code>\n"
                f"📁 Файл: <code>{normalized_file_name}</code>\n"
                f"📦 Парный .session: <b>{session_status}</b>\n"
                f"📦 Комплект: <b>{_bundle_status_label(bundle)}</b>\n"
                f"🗂 БД: <b>{db_status}</b>\n\n"
                f"{_bundle_next_step_text(bundle)}",
                parse_mode=ParseMode.HTML,
            )
        else:
            await message.answer(
                "⚠️ JSON сохранён, но БД не обновилась.\n\n"
                f"📱 Телефон: <code>{account_phone}</code>\n"
                f"📁 Файл: <code>{normalized_file_name}</code>\n"
                f"🗂 БД: <b>{db_status}</b>\n\n"
                "Откройте «Аккаунты -> Обновить список» и попробуйте ещё раз.",
                parse_mode=ParseMode.HTML,
            )
        log.info(f"Account JSON metadata saved: {normalized_file_name}")

    elif lower_name.endswith(".txt"):
        # Предположительно файл с прокси
        await message.answer(
            f"📥 Получен файл: <code>{file_name}</code>\n"
            "⏳ Обрабатываю как список прокси...",
            parse_mode=ParseMode.HTML,
        )

        bot = message.bot
        file = await bot.get_file(doc.file_id)
        save_path = settings.proxy_list_path
        await bot.download_file(file.file_path, str(save_path))
        loaded = proxy_mgr.load_from_file()
        sync_report = await sync_proxies_from_file(save_path, user_id=db_user.id if db_user else None)
        bind_report = await bind_accounts_to_proxies(user_id=db_user.id if db_user else None)

        await message.answer(
            f"✅ Файл прокси сохранён!\n\n"
            f"Загружено в память: <b>{loaded}</b>\n"
            f"Синхронизировано в БД: <b>{int(sync_report.get('added', 0)) + int(sync_report.get('existing', 0))}</b>\n"
            f"Новых proxy rows: <b>{sync_report.get('added', 0)}</b>\n"
            f"Привязано аккаунтов: <b>{bind_report.get('bound', 0)}</b>\n"
            f"Уже привязано: <b>{bind_report.get('already_bound', 0)}</b>\n\n"
            "Перейдите в 🌐 Прокси → Загрузить из файла",
            parse_mode=ParseMode.HTML,
        )
        log.info(f"Proxy file saved: {file_name}")

    else:
        await message.answer(
            f"⚠️ Неизвестный тип файла: <code>{file_name}</code>\n\n"
            "Поддерживаются:\n"
            "• <code>.session</code> — аккаунты Telegram\n"
            "• <code>.json</code> — метаданные аккаунта\n"
            "• <code>.txt</code> — списки прокси",
            parse_mode=ParseMode.HTML,
        )


# ============================================================
# Запуск бота
# ============================================================

async def start_bot():
    """Запустить Telegram бота."""
    if not settings.ADMIN_BOT_TOKEN:
        log.error("ADMIN_BOT_TOKEN не задан в .env! Бот не может запуститься.")
        raise SystemExit("ADMIN_BOT_TOKEN не задан")

    scheduler: Optional[AsyncIOScheduler] = None
    poller_lock_task: Optional[asyncio.Task] = None
    start_task = asyncio.current_task()
    poller_lock_owner = f"{os.environ.get('HOSTNAME') or 'host'}:{os.getpid()}"
    poller_lock_held = False
    bot = Bot(token=settings.ADMIN_BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    log.info("🤖 Telegram Admin Bot запускается...")

    # Инициализировать notifier
    notifier.set_bot(bot)

    if settings.proxy_list_path.exists():
        proxy_mgr.load_from_file()

    # Восстановить счётчики rate limiter из БД (защита от рестарта)
    await rate_limiter.load_from_db()

    if settings.AUTO_SPAMBOT_APPEAL_ENABLED:
        log.warning(
            "AUTO_SPAMBOT_APPEAL_ENABLED=true, но автозапуск отключён политикой. "
            "Запуск только вручную: /spambot_start CONFIRM"
        )

    try:
        await redis_state.connect()
        while True:
            poller_lock_held = await redis_state.acquire_owner_lock(
                _ADMIN_POLLER_LOCK_KEY,
                poller_lock_owner,
                ttl=_ADMIN_POLLER_LOCK_TTL_SEC,
            )
            if poller_lock_held:
                log.info(f"Admin poller lock acquired: owner={poller_lock_owner}")
                break

            current_owner = await redis_state.get_lock_owner(_ADMIN_POLLER_LOCK_KEY)
            log.warning(
                "Admin poller lock busy "
                f"(current_owner={current_owner or 'unknown'}), retry in "
                f"{_ADMIN_POLLER_LOCK_RENEW_SEC}s"
            )
            await asyncio.sleep(_ADMIN_POLLER_LOCK_RENEW_SEC)

        async def _renew_poller_lock():
            while True:
                await asyncio.sleep(_ADMIN_POLLER_LOCK_RENEW_SEC)
                renewed = await redis_state.renew_owner_lock(
                    _ADMIN_POLLER_LOCK_KEY,
                    poller_lock_owner,
                    ttl=_ADMIN_POLLER_LOCK_TTL_SEC,
                )
                if renewed:
                    continue
                log.error("Admin poller lock lost, cancelling polling task")
                if start_task is not None:
                    start_task.cancel()
                return

        poller_lock_task = asyncio.create_task(_renew_poller_lock())
    except Exception as exc:
        log.warning(
            "Single-poller Redis lock unavailable, continuing without lock protection: "
            f"{exc}"
        )

    # Удалить вебхук если был
    await bot.delete_webhook(drop_pending_updates=True)

    if sheets_storage.is_enabled:
        scheduler = AsyncIOScheduler()
        scheduler.add_job(
            sync_to_sheets_snapshot,
            trigger="interval",
            seconds=max(60, settings.SHEETS_SYNC_INTERVAL_SEC),
            max_instances=1,
            coalesce=True,
            id="sheets_sync",
        )
        scheduler.start()
        await sync_to_sheets_snapshot()
        log.info(f"Google Sheets sync запущен каждые {max(60, settings.SHEETS_SYNC_INTERVAL_SEC)}с")

    try:
        await dp.start_polling(bot)
    finally:
        if poller_lock_task:
            poller_lock_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await poller_lock_task
        if poller_lock_held:
            with contextlib.suppress(Exception):
                await redis_state.release_owner_lock(_ADMIN_POLLER_LOCK_KEY, poller_lock_owner)
        with contextlib.suppress(Exception):
            await redis_state.close()
        if scheduler:
            scheduler.shutdown(wait=False)
        await spambot_auto_appeal.stop()
        await account_mgr.disconnect_all()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(start_bot())
