"""
NEURO COMMENTING — Telegram Bot Admin Panel.
Основной интерфейс управления системой через кнопки в Telegram.
По образцу NeuroCom: всё управление через бота.
"""

import asyncio
from datetime import datetime
from html import escape
from typing import Optional

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
from sqlalchemy import select, func

from channels.channel_db import ChannelDB
from channels.discovery import ChannelDiscovery
from channels.monitor import ChannelMonitor
from channels.analyzer import PostAnalyzer
from comments.generator import CommentGenerator
from comments.poster import CommentPoster
from config import settings
from core.account_manager import AccountManager
from core.proxy_manager import ProxyManager
from core.rate_limiter import RateLimiter
from core.scheduler import TaskScheduler
from core.session_manager import SessionManager
from storage.google_sheets import GoogleSheetsStorage
from storage.models import Account, Comment, Post, Channel as DbChannel
from storage.sqlite_db import async_session
from utils.logger import log
from utils.channel_subscriber import ChannelSubscriber
from utils.channel_setup import ChannelSetup
from utils.account_packager import AccountPackager
from utils.auto_responder import AutoResponder
from utils.notifier import notifier

router = Router()


class AdminCheckMiddleware(BaseMiddleware):
    """Middleware: блокирует callback_query от не-админов."""

    async def __call__(self, handler, event: TelegramObject, data: dict):
        if isinstance(event, CallbackQuery):
            user_id = event.from_user.id if event.from_user else 0
            if not is_admin(user_id):
                await event.answer("⛔ Доступ запрещён.", show_alert=True)
                return
        return await handler(event, data)


router.callback_query.middleware(AdminCheckMiddleware())

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
account_packager = AccountPackager(session_mgr)
auto_responder = AutoResponder(session_mgr)

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


class SettingsStates(StatesGroup):
    waiting_daily_limit = State()
    waiting_min_delay = State()
    waiting_max_delay = State()
    waiting_scenario_ratio = State()
    waiting_dartvpn_link = State()
    waiting_delayed_minutes = State()


# ============================================================
# Клавиатуры
# ============================================================

def main_menu_kb() -> ReplyKeyboardMarkup:
    """Главное меню — reply-кнопки внизу экрана."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Дашборд"), KeyboardButton(text="👤 Аккаунты")],
            [KeyboardButton(text="🌐 Прокси"), KeyboardButton(text="📢 Каналы")],
            [KeyboardButton(text="💬 Комментинг"), KeyboardButton(text="🔍 Парсер каналов")],
            [KeyboardButton(text="⚙️ Настройки"), KeyboardButton(text="📖 Помощь")],
        ],
        resize_keyboard=True,
    )


def accounts_kb() -> InlineKeyboardMarkup:
    """Меню аккаунтов."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Список аккаунтов", callback_data="acc_list")],
        [InlineKeyboardButton(text="➕ Добавить аккаунт", callback_data="acc_add")],
        [InlineKeyboardButton(text="🔌 Подключить все", callback_data="acc_connect_all")],
        [InlineKeyboardButton(text="❤️ Проверить здоровье", callback_data="acc_health")],
        [InlineKeyboardButton(text="🎨 Упаковка профилей (AI)", callback_data="acc_package")],
        [InlineKeyboardButton(text="📡 Канал-переходник", callback_data="acc_redirect_channel")],
        [InlineKeyboardButton(text="📢 Подписать на каналы", callback_data="acc_subscribe")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_main")],
    ])


def proxy_kb() -> InlineKeyboardMarkup:
    """Меню прокси."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Список прокси", callback_data="proxy_list")],
        [InlineKeyboardButton(text="📂 Загрузить из файла", callback_data="proxy_load")],
        [InlineKeyboardButton(text="✅ Проверить все", callback_data="proxy_validate")],
        [InlineKeyboardButton(text="➕ Добавить прокси", callback_data="proxy_add")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_main")],
    ])


def channels_kb() -> InlineKeyboardMarkup:
    """Меню каналов."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 База каналов", callback_data="ch_list")],
        [InlineKeyboardButton(text="➕ Добавить канал", callback_data="ch_add")],
        [InlineKeyboardButton(text="🔍 Найти каналы (парсер)", callback_data="ch_search")],
        [InlineKeyboardButton(text="📊 Статистика каналов", callback_data="ch_stats")],
        [InlineKeyboardButton(text="🗑 Чёрный список", callback_data="ch_blacklist")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_main")],
    ])


def commenting_kb() -> InlineKeyboardMarkup:
    """Меню комментинга."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="▶️ Запустить комментинг", callback_data="com_start")],
        [InlineKeyboardButton(text="⏸ Остановить", callback_data="com_stop")],
        [InlineKeyboardButton(text="⏰ Отложенный запуск", callback_data="com_delayed")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="com_stats")],
        [InlineKeyboardButton(text="📝 История комментариев", callback_data="com_history")],
        [InlineKeyboardButton(text="🎯 Настроить сценарии A/B", callback_data="com_scenarios")],
        [InlineKeyboardButton(text="🧪 Тестовый комментарий", callback_data="com_test")],
        [InlineKeyboardButton(text="📜 Старые посты", callback_data="com_old_posts")],
        [InlineKeyboardButton(text="💬 Автоответчик ЛС", callback_data="com_autoresponder")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_main")],
    ])


def parser_kb() -> InlineKeyboardMarkup:
    """Меню парсера каналов."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Поиск по ключевым словам", callback_data="parse_keywords")],
        [InlineKeyboardButton(text="📂 Поиск по тематике", callback_data="parse_topic")],
        [InlineKeyboardButton(text="🔗 Похожие каналы", callback_data="parse_similar")],
        [InlineKeyboardButton(text="📊 Фильтры (подписчики, активность)", callback_data="parse_filters")],
        [InlineKeyboardButton(text="💾 Экспорт в TXT", callback_data="parse_export")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_main")],
    ])


def settings_kb() -> InlineKeyboardMarkup:
    """Меню настроек."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏱ Лимиты и задержки", callback_data="set_limits")],
        [InlineKeyboardButton(text="🔗 Ссылка DartVPN", callback_data="set_dartvpn")],
        [InlineKeyboardButton(text="🤖 Модель AI", callback_data="set_ai")],
        [InlineKeyboardButton(text="📊 Google Sheets", callback_data="set_sheets")],
        [InlineKeyboardButton(text="🔄 Сценарий A/B баланс", callback_data="set_scenarios")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_main")],
    ])


# ============================================================
# Проверка доступа
# ============================================================

def is_admin(user_id: int) -> bool:
    """Проверить что пользователь — администратор."""
    if settings.ADMIN_TELEGRAM_ID == 0:
        return False  # Если ID не задан — запретить доступ (безопасность)
    return user_id == settings.ADMIN_TELEGRAM_ID


def _set_admin_id(user_id: int):
    """Закрепить admin ID при первом запуске (записать в .env и settings)."""
    settings.ADMIN_TELEGRAM_ID = user_id
    _update_env("ADMIN_TELEGRAM_ID", str(user_id))
    log.info(f"Admin ID закреплён: {user_id}")


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


def get_topic_from_keywords(keywords: list[str]) -> Optional[str]:
    lowered = {kw.lower() for kw in keywords}
    for topic_key, topic_keywords in ChannelDiscovery.PRESET_TOPIC_KEYWORDS.items():
        if lowered.intersection({word.lower() for word in topic_keywords}):
            return topic_key
    return None


async def run_keyword_search(keywords: list[str], topic: Optional[str] = None) -> tuple[list, int]:
    found = await channel_discovery.search_by_keywords(keywords=keywords)
    saved = 0
    for channel_info in found:
        if topic:
            channel_info.topic = topic
        elif not channel_info.topic:
            channel_info.topic = get_topic_from_keywords(keywords)
        await channel_db.add_channel(channel_info)
        saved += 1
    return found, saved


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
        lines.append(
            f"{idx}. <b>{title_text}</b>\n"
            f"   {username} | 👥 {channel.subscribers} | 🧩 {channel.topic or '—'}"
        )
    if len(channels) > 20:
        lines.append("")
        lines.append(f"<i>Показаны первые 20 из {len(channels)} каналов.</i>")
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
async def cmd_start(message: Message):
    # Авто-регистрация админа при первом запуске
    if settings.ADMIN_TELEGRAM_ID == 0:
        _set_admin_id(message.from_user.id)
        log.info(f"Первый запуск! Admin ID закреплён: {message.from_user.id}")

    if not is_admin(message.from_user.id):
        await message.answer("⛔ Доступ запрещён.")
        return

    await message.answer(
        "🚀 <b>NEURO COMMENTING</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Система автокомментирования в Telegram\n"
        "для продвижения <b>DartVPN</b>\n\n"
        f"👤 Админ: <code>{message.from_user.id}</code>\n"
        f"🤖 AI: <code>{settings.GEMINI_MODEL}</code>\n"
        f"🔗 Бот: {settings.DARTVPN_BOT_LINK}\n\n"
        "Выберите раздел в меню ниже 👇",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_kb(),
    )


# ============================================================
# Хендлеры reply-кнопок (главное меню)
# ============================================================

@router.message(F.text == "📊 Дашборд")
async def menu_dashboard(message: Message):
    if not is_admin(message.from_user.id):
        return

    now = datetime.now().strftime("%d.%m.%Y %H:%M")
    accounts = await account_mgr.load_accounts()
    channel_stats = await channel_db.get_stats()
    async with async_session() as session:
        comments_total = await session.scalar(select(func.count(Comment.id)))
        comments_today = await session.scalar(
            select(func.count(Comment.id)).where(
                func.date(Comment.created_at) == utcnow().date()
            )
        )

    monitor_stats = channel_monitor.get_stats()
    poster_stats = comment_poster.get_stats()

    if channel_monitor.is_running:
        status = "Работает"
    elif accounts:
        status = "Готов к запуску"
    else:
        status = "Ожидание настройки"

    await message.answer(
        f"📊 <b>Дашборд</b> | {now}\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"⚡ <b>Статус:</b> {status}\n\n"
        f"👤 <b>Аккаунты:</b> {len(accounts)}\n"
        f"🌐 <b>Прокси:</b> {len(proxy_mgr.proxies)}\n"
        f"📢 <b>Каналов:</b> {channel_stats['active']}\n"
        f"📋 <b>Очередь:</b> {monitor_stats['queue_size']} постов\n\n"
        f"💬 <b>Сегодня:</b> {comments_today or 0} комментариев\n"
        f"📈 <b>Всего:</b> {comments_total or 0} комментариев\n"
        f"📨 <b>Сессия:</b> отправлено {poster_stats['sent']}, ошибок {poster_stats['failed']}",
        parse_mode=ParseMode.HTML,
    )


@router.message(F.text == "👤 Аккаунты")
async def menu_accounts(message: Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer(
        "👤 <b>Управление аккаунтами</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "Здесь вы управляете Telegram-аккаунтами\n"
        "для комментирования.\n\n"
        "📌 Аккаунты покупаются у поставщиков\n"
        "📌 Каждый аккаунт пишет 30-40 комментариев/день\n"
        "📌 Нужны .session файлы + прокси",
        parse_mode=ParseMode.HTML,
        reply_markup=accounts_kb(),
    )


@router.message(F.text == "🌐 Прокси")
async def menu_proxy(message: Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer(
        "🌐 <b>Управление прокси</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "Прокси необходимы для каждого аккаунта.\n"
        "Формат: <code>type://user:pass@host:port</code>\n"
        "или: <code>host:port:user:pass</code>",
        parse_mode=ParseMode.HTML,
        reply_markup=proxy_kb(),
    )


@router.message(F.text == "📢 Каналы")
async def menu_channels(message: Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer(
        "📢 <b>База каналов</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "Каналы в которых аккаунты будут\n"
        "оставлять комментарии.\n\n"
        "🎯 Тематики: VPN, нейросети, Instagram,\n"
        "заблокированные сервисы, AI-инструменты",
        parse_mode=ParseMode.HTML,
        reply_markup=channels_kb(),
    )


@router.message(F.text == "💬 Комментинг")
async def menu_commenting(message: Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer(
        "💬 <b>Комментинг</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "Управление процессом комментирования.\n\n"
        "📌 <b>Сценарий A (70%):</b> Текст без ссылки\n"
        "   → Аватарка-магнит → Профиль → Канал → DartVPN\n\n"
        "📌 <b>Сценарий B (30%):</b> Текст со ссылкой\n"
        f"   → Прямая ссылка на {settings.DARTVPN_BOT_LINK}",
        parse_mode=ParseMode.HTML,
        reply_markup=commenting_kb(),
    )


@router.message(F.text == "🔍 Парсер каналов")
async def menu_parser(message: Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer(
        "🔍 <b>Парсер каналов</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "Поиск тематических каналов для комментирования.\n\n"
        "Фильтры:\n"
        "• Ключевые слова\n"
        "• Мин. подписчиков\n"
        "• Наличие комментариев\n"
        "• Тематика (VPN, AI, сервисы)\n"
        "• Язык (русский)",
        parse_mode=ParseMode.HTML,
        reply_markup=parser_kb(),
    )


@router.message(F.text == "⚙️ Настройки")
async def menu_settings(message: Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer(
        "⚙️ <b>Настройки</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🤖 AI модель: <code>{settings.GEMINI_MODEL}</code>\n"
        f"🔗 DartVPN: {settings.DARTVPN_BOT_LINK}\n"
        f"📊 Лимит/день: {settings.MAX_COMMENTS_PER_ACCOUNT_PER_DAY}\n"
        f"⏱ Задержка: {settings.MIN_DELAY_BETWEEN_COMMENTS_SEC}-{settings.MAX_DELAY_BETWEEN_COMMENTS_SEC} сек\n"
        f"🎯 Сценарий B: {int(settings.SCENARIO_B_RATIO * 100)}%\n"
        f"🔥 Прогрев: {settings.WARMUP_DAY_1_LIMIT}→{settings.WARMUP_DAY_2_LIMIT}→{settings.WARMUP_DAY_3_LIMIT}→{settings.MAX_COMMENTS_PER_ACCOUNT_PER_DAY}",
        parse_mode=ParseMode.HTML,
        reply_markup=settings_kb(),
    )


def help_kb() -> InlineKeyboardMarkup:
    """Меню инструкции."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Быстрый старт (пошагово)", callback_data="help_quickstart")],
        [InlineKeyboardButton(text="👤 Аккаунты: покупка и загрузка", callback_data="help_accounts")],
        [InlineKeyboardButton(text="📡 Канал-переходник", callback_data="help_redirect")],
        [InlineKeyboardButton(text="💬 Сценарии комментирования", callback_data="help_scenarios")],
        [InlineKeyboardButton(text="🛡 Антибан-система", callback_data="help_antiban")],
        [InlineKeyboardButton(text="🤖 AI и настройки", callback_data="help_ai")],
        [InlineKeyboardButton(text="📊 Статистика и Sheets", callback_data="help_stats")],
        [InlineKeyboardButton(text="❓ FAQ и лимиты", callback_data="help_faq")],
    ])


@router.message(F.text == "📖 Помощь")
async def menu_help(message: Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer(
        "📖 <b>NEURO COMMENTING — Инструкция</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Система автоматического комментирования\n"
        "в Telegram для продвижения DartVPN.\n\n"
        "Бот мониторит тематические каналы (VPN,\n"
        "нейросети, блокировки), находит новые посты\n"
        "и оставляет AI-сгенерированные комментарии\n"
        "от имени купленных аккаунтов.\n\n"
        "<b>Выберите раздел:</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=help_kb(),
    )


@router.callback_query(F.data == "help_quickstart")
async def cb_help_quickstart(callback: CallbackQuery):
    await callback.answer()
    await callback.message.edit_text(
        "🚀 <b>БЫСТРЫЙ СТАРТ — пошагово</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "<b>ШАГ 1. Настройка .env</b>\n"
        "Заполните файл <code>.env</code>:\n"
        "• <code>TELEGRAM_API_ID</code> — получить на my.telegram.org\n"
        "• <code>TELEGRAM_API_HASH</code> — там же\n"
        "• <code>ADMIN_BOT_TOKEN</code> — от @BotFather\n"
        "• <code>GEMINI_API_KEY</code> — Google AI Studio\n\n"
        "API ID/HASH — это НЕ аккаунт, а приложение.\n"
        "Одна пара на все аккаунты.\n\n"
        "<b>ШАГ 2. Загрузите прокси</b>\n"
        "🌐 Прокси → Загрузить из файла\n"
        "Формат: <code>socks5://user:pass@host:port</code>\n\n"
        "<b>ШАГ 3. Добавьте аккаунты</b>\n"
        "👤 Аккаунты → Добавить аккаунт\n"
        "Загрузите .session файлы в <code>data/sessions/</code>\n"
        "(подробнее в разделе «Аккаунты»)\n\n"
        "<b>ШАГ 4. Упакуйте аккаунты</b>\n"
        "👤 Аккаунты → Упаковка профилей (AI)\n\n"
        "<b>ШАГ 5. Каналы-переходники</b>\n"
        "👤 Аккаунты → Канал-переходник\n\n"
        "<b>ШАГ 6. Найдите каналы</b>\n"
        "🔍 Парсер → Поиск по тематике\n\n"
        "<b>ШАГ 7. Подпишите аккаунты</b>\n"
        "👤 Аккаунты → Подписать на каналы\n\n"
        "<b>ШАГ 8. Запустите!</b>\n"
        "💬 Комментинг → Запустить\n"
        "Готово! Бот работает автономно 24/7.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад к инструкции", callback_data="help_back")],
        ]),
    )


@router.callback_query(F.data == "help_accounts")
async def cb_help_accounts(callback: CallbackQuery):
    await callback.answer()
    await callback.message.edit_text(
        "👤 <b>АККАУНТЫ: покупка и загрузка</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "<b>Что покупать?</b>\n"
        "Telegram-аккаунты в формате <code>.session</code>\n"
        "(Telethon / Pyrogram сессии).\n"
        "Цена: ~100-150 руб за штуку.\n\n"
        "<b>Что попросить у продавца:</b>\n"
        "1. Файлы <code>.session</code> (Telethon формат)\n"
        "2. Номера телефонов аккаунтов\n"
        "3. Прокси (SOCKS5) — если есть\n\n"
        "<b>Как загрузить в бота:</b>\n"
        "1. Скопируйте <code>.session</code> файлы в папку:\n"
        "   <code>data/sessions/</code>\n"
        "   Имя файла = номер телефона\n"
        "   Пример: <code>+79161234567.session</code>\n\n"
        "2. В боте: 👤 Аккаунты → Добавить\n"
        "   Введите номер телефона\n\n"
        "3. Подключите: Аккаунты → Подключить все\n\n"
        "<b>Сколько аккаунтов нужно?</b>\n"
        "• Минимум: 3 аккаунта\n"
        "• Оптимально: 5-10\n"
        "• Максимум: без ограничений\n\n"
        "<b>Производительность:</b>\n"
        "• 1 акк = 30-35 комментариев/день\n"
        "• 3 акк = 90-105 комментариев/день\n"
        "• 5 акк = 150-175 комментариев/день\n"
        "• 10 акк = 300-350 комментариев/день\n\n"
        "<b>Срок жизни аккаунта:</b>\n"
        "При правильной настройке 7-30 дней.\n"
        "Прогрев: 5→10→20→35 за 4 дня.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад к инструкции", callback_data="help_back")],
        ]),
    )


@router.callback_query(F.data == "help_redirect")
async def cb_help_redirect(callback: CallbackQuery):
    await callback.answer()
    await callback.message.edit_text(
        "📡 <b>КАНАЛ-ПЕРЕХОДНИК</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "<b>Что это?</b>\n"
        "Личный канал аккаунта с одним постом\n"
        "со ссылкой на @DartVPNBot.\n"
        "Ссылка на канал ставится в bio аккаунта.\n\n"
        "<b>Зачем?</b>\n"
        "Ключевой элемент Сценария A (70%).\n"
        "Путь пользователя:\n\n"
        "💬 <i>Комментарий</i> (без ссылки)\n"
        "  ↓ клик на аватарку\n"
        "👤 <i>Профиль</i> (bio + канал)\n"
        "  ↓ клик на канал в bio\n"
        "📡 <i>Канал-переходник</i>\n"
        "  ↓ закреплённый пост\n"
        "🤖 <i>@DartVPNBot</i>\n\n"
        "<b>Что делает автоматически:</b>\n"
        "1. AI генерирует уникальное название\n"
        "2. Создаётся канал от имени аккаунта\n"
        "3. Публикуется пост с DartVPN ссылкой\n"
        "4. Пост закрепляется\n"
        "5. Bio обновляется ссылкой на канал\n\n"
        "<b>Как создать:</b>\n"
        "👤 Аккаунты → 📡 Канал-переходник\n\n"
        "<b>Почему не прямая ссылка?</b>\n"
        "Telegram банит аккаунты за спам ссылками.\n"
        "Канал-переходник выглядит как обычный\n"
        "личный канал пользователя.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад к инструкции", callback_data="help_back")],
        ]),
    )


@router.callback_query(F.data == "help_scenarios")
async def cb_help_scenarios(callback: CallbackQuery):
    await callback.answer()
    await callback.message.edit_text(
        "💬 <b>СЦЕНАРИИ КОММЕНТИРОВАНИЯ</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "<b>Сценарий A — Воронка (70%)</b>\n"
        "Комментарий БЕЗ ссылки.\n"
        "Обычная реакция на пост.\n"
        "Конверсия через профиль → канал.\n"
        "Пример: <i>«ну наконец-то 😅»</i>\n\n"
        "<b>Сценарий B — Рекомендация (30%)</b>\n"
        "Комментарий С @DartVPNBot.\n"
        "Личный опыт, не реклама.\n"
        "Пример: <i>«перешёл на @DartVPNBot 👍»</i>\n\n"
        "<b>Emoji Swap (60% от B)</b>\n"
        "Сначала эмодзи (👀, 🔥, 💯).\n"
        "Через 60 сек → замена на текст.\n"
        "Обходит спам-фильтр Telegram.\n\n"
        "<b>Режим «Старые посты»</b>\n"
        "Сканирует архив каналов (до 50 постов)\n"
        "и комментирует непрокомментированные.\n\n"
        "<b>Автоответчик ЛС</b>\n"
        "Авто-ответ на входящие ЛС с DartVPN.\n\n"
        "<b>Правила комментариев:</b>\n"
        "• Максимум 30 слов\n"
        "• Русский разговорный язык\n"
        "• 1-2 эмодзи\n"
        "• 5 стилей: casual, formal, slang, tech, skeptic",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад к инструкции", callback_data="help_back")],
        ]),
    )


@router.callback_query(F.data == "help_antiban")
async def cb_help_antiban(callback: CallbackQuery):
    await callback.answer()
    await callback.message.edit_text(
        "🛡 <b>АНТИБАН-СИСТЕМА</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "<b>1. Прогрев аккаунтов</b>\n"
        "День 1→5 | День 2→10 | День 3→20 | День 4+→35\n\n"
        "<b>2. Задержка печати</b>\n"
        "2-30 сек, имитация скорости человека.\n\n"
        "<b>3. Пассивные действия (25%)</b>\n"
        "Просмотр, реакция (👍❤️🔥), прочтение.\n\n"
        "<b>4. Отдых после серии</b>\n"
        "15-45 мин после 8-10 комментариев.\n\n"
        "<b>5. Emoji→Link Swap</b>\n"
        "Эмодзи → 60 сек → текст.\n\n"
        "<b>6. Авто-восстановление</b>\n"
        "Каждые 10 мин восстанавливает аккаунты\n"
        "после истечения cooldown.\n\n"
        "<b>7. Ротация аккаунтов</b>\n"
        "Round-robin + свой прокси (IP) на каждый.\n\n"
        "<b>8. Паттерн B: макс 2 из 5</b>\n"
        "Не больше 2 промо из 5 последних.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад к инструкции", callback_data="help_back")],
        ]),
    )


@router.callback_query(F.data == "help_ai")
async def cb_help_ai(callback: CallbackQuery):
    await callback.answer()
    await callback.message.edit_text(
        "🤖 <b>AI И НАСТРОЙКИ</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "<b>Генерация комментариев</b>\n"
        "Google Gemini API. Каждый комментарий\n"
        "уникален — AI читает пост и пишет ответ.\n\n"
        "<b>Если AI недоступен</b>\n"
        "28 готовых фоллбэк-комментариев.\n\n"
        "<b>Упаковка профилей</b>\n"
        "AI генерирует имя, фамилию, bio.\n"
        "4 стиля: casual, expert, business, student\n\n"
        "<b>Настройки (⚙️):</b>\n"
        "• Лимиты: дневной лимит, задержки\n"
        "• Сценарий A/B: доля B (10-50%)\n"
        "• Модель AI: вкл/выкл Emoji Swap\n"
        "• Ссылка DartVPN\n"
        "• Google Sheets синхронизация\n\n"
        "<b>Отложенный запуск:</b>\n"
        "💬 Комментинг → Отложенный запуск\n"
        "Старт через 1-1440 минут (до 24 часов).",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад к инструкции", callback_data="help_back")],
        ]),
    )


@router.callback_query(F.data == "help_stats")
async def cb_help_stats(callback: CallbackQuery):
    await callback.answer()
    await callback.message.edit_text(
        "📊 <b>СТАТИСТИКА И GOOGLE SHEETS</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "<b>Дашборд (📊)</b>\n"
        "Статус, аккаунты, каналы, очередь,\n"
        "комментарии за сегодня, ошибки.\n\n"
        "<b>История комментариев</b>\n"
        "💬 Комментинг → История\n"
        "Последние 10 комментариев.\n\n"
        "<b>Google Sheets</b>\n"
        "Синхронизация каждые 5 минут:\n"
        "Каналы, Комментарии, Аккаунты, Статистика.\n\n"
        "Настройка:\n"
        "1. Сервисный аккаунт Google\n"
        "2. Скачать credentials.json\n"
        "3. Указать в .env\n\n"
        "<b>Экспорт каналов</b>\n"
        "🔍 Парсер → Экспорт в TXT\n\n"
        "<b>Уведомления:</b>\n"
        "• 💬 Комментарий отправлен\n"
        "• ⚠️ Ошибки (FloodWait, бан)\n"
        "• 🚫 Бан аккаунта\n"
        "• 🚀 Запуск / ⏸ Остановка",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад к инструкции", callback_data="help_back")],
        ]),
    )


@router.callback_query(F.data == "help_faq")
async def cb_help_faq(callback: CallbackQuery):
    await callback.answer()
    await callback.message.edit_text(
        "❓ <b>FAQ И ЛИМИТЫ</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "<b>Стоимость запуска?</b>\n"
        "• Аккаунты: ~100-150 руб/шт\n"
        "• Прокси: ~1000-1500 руб/мес\n"
        "• Gemini API: бесплатный тариф\n"
        "Итого на 5 акк: ~1500-2500 руб/мес\n\n"
        "<b>Какие прокси?</b>\n"
        "SOCKS5 или HTTP, мобильные/резидентные.\n"
        "<code>socks5://user:pass@host:port</code>\n\n"
        "<b>Забанили аккаунт?</b>\n"
        "Замените на новый. Бот автоматически\n"
        "исключит забаненный из ротации.\n"
        "FloodWait — авто-восстановление.\n\n"
        "<b>Частота комментариев?</b>\n"
        "2-10 мин между комментариями.\n"
        "30-35/день на аккаунт.\n\n"
        "<b>Можно на ночь?</b>\n"
        "Да! 24/7 автономно. Есть отложенный запуск.\n\n"
        "<b>Что такое API ID/HASH?</b>\n"
        "Идентификатор приложения Telegram.\n"
        "my.telegram.org/auth — одна пара на все акк.\n\n"
        "<b>Лимиты:</b>\n"
        "• Каналов/аккаунт: 160\n"
        "• Комментариев/день/акк: 35\n"
        "• Длина: 30 слов\n"
        "• Возраст поста: до 2 часов\n"
        "• Аккаунтов: без ограничений",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад к инструкции", callback_data="help_back")],
        ]),
    )


@router.callback_query(F.data == "help_back")
async def cb_help_back(callback: CallbackQuery):
    await callback.answer()
    await callback.message.edit_text(
        "📖 <b>NEURO COMMENTING — Инструкция</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Система автоматического комментирования\n"
        "в Telegram для продвижения DartVPN.\n\n"
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
    await callback.message.delete()
    await callback.answer()


# --- Аккаунты ---

@router.callback_query(F.data == "acc_list")
async def cb_acc_list(callback: CallbackQuery):
    await callback.answer()
    accounts = await account_mgr.load_accounts()
    if not accounts:
        await callback.message.edit_text(
            "👤 <b>Список аккаунтов</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "<i>Пока нет добавленных аккаунтов.\n"
            "Добавьте аккаунт, загрузив .session файл.</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=accounts_kb(),
        )
        return

    lines = [
        "👤 <b>Список аккаунтов</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        f"Всего: <b>{len(accounts)}</b>",
        "",
    ]
    for idx, acc in enumerate(accounts[:20], start=1):
        lines.append(
            f"{idx}. <code>{escape(acc.phone)}</code> | "
            f"статус: <b>{escape(acc.status)}</b> | "
            f"сегодня: {acc.comments_today}"
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
        "➕ <b>Добавление аккаунта</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "Отправьте <b>.session файл</b> в этот чат.\n\n"
        "Файл должен быть получен от поставщика аккаунтов\n"
        "или создан через Telethon.\n\n"
        "📌 Формат: <code>phone_number.session</code>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back_accounts")],
        ]),
    )


@router.callback_query(F.data == "acc_connect_all")
async def cb_acc_connect(callback: CallbackQuery):
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
async def cb_acc_health(callback: CallbackQuery):
    await callback.answer()
    await callback.message.edit_text(
        "❤️ <b>Здоровье аккаунтов</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "<i>Нет аккаунтов для проверки.</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=accounts_kb(),
    )


@router.callback_query(F.data == "acc_package")
async def cb_acc_package(callback: CallbackQuery):
    await callback.answer()
    connected = session_mgr.get_connected_phones()
    if not connected:
        await callback.message.edit_text(
            "🎨 <b>Упаковка профилей (AI)</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "<i>Сначала подключите аккаунты!</i>\n"
            "👤 Аккаунты → Подключить все",
            parse_mode=ParseMode.HTML,
            reply_markup=accounts_kb(),
        )
        return

    await callback.message.edit_text(
        "🎨 <b>Упаковка профилей (женские)</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Подключено аккаунтов: <b>{len(connected)}</b>\n\n"
        "AI сгенерирует:\n"
        "  — Женское имя и фамилию\n"
        "  — Username (латиницей)\n"
        "  — Аватарку (Gemini Imagen)\n"
        "  — Bio с намёком на VPN/тех\n\n"
        "Стили: beauty, casual, student, tech, lifestyle\n"
        "blogger, fitness, business, creative, friendly",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Упаковать все аккаунты", callback_data="acc_package_run")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back_accounts")],
        ]),
    )


@router.callback_query(F.data == "acc_package_run")
async def cb_acc_package_run(callback: CallbackQuery):
    await callback.answer("Упаковка запущена...")
    await callback.message.edit_text(
        "⏳ AI генерирует женские профили и аватарки...\n"
        "Это может занять несколько минут."
    )

    results = await account_packager.package_all_accounts()
    lines = [
        "🎨 <b>Результаты упаковки</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
    ]
    for r in results:
        status = "✅" if r["applied"] else "❌"
        profile = r["profile"]
        name = f"{profile['first_name']} {profile.get('last_name', '')}".strip()
        username_str = f" (@{r['username']})" if r.get("username") else ""
        avatar_str = " 📷" if r.get("avatar_applied") else ""
        lines.append(
            f"{status} <code>{r['phone']}</code>\n"
            f"   {name}{username_str}{avatar_str}\n"
            f"   <i>{profile.get('bio', '')[:50]}</i>"
        )

    applied = sum(1 for r in results if r["applied"])
    usernames = sum(1 for r in results if r.get("username_applied"))
    avatars = sum(1 for r in results if r.get("avatar_applied"))
    lines.append(
        f"\nПрофили: <b>{applied}/{len(results)}</b> | "
        f"Username: <b>{usernames}/{len(results)}</b> | "
        f"Аватарки: <b>{avatars}/{len(results)}</b>"
    )

    await callback.message.edit_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=accounts_kb(),
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
    bot_link = settings.DARTVPN_BOT_LINK or "https://t.me/DartVPNBot"
    current_channel = settings.DARTVPN_CHANNEL_LINK

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
        f"• Пост со ссылкой на DartVPN бот\n"
        f"• Закреплённый пост в канале\n"
        f"• Bio аккаунта со ссылкой на канал\n\n"
        f"<b>Цепочка:</b> Аватарка → Профиль → Канал → DartVPN\n\n"
        f"Аккаунтов: <b>{len(connected)}</b>\n"
        f"DartVPN: <code>{escape(bot_link)}</code>\n"
    )
    if current_channel:
        status_text += f"Текущий канал: <code>{escape(current_channel)}</code>\n"

    await callback.message.edit_text(
        status_text,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Создать для всех аккаунтов", callback_data="acc_redirect_run")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back_accounts")],
        ]),
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
async def cb_proxy_list(callback: CallbackQuery):
    await callback.answer()
    if not proxy_mgr.proxies and settings.proxy_list_path.exists():
        proxy_mgr.load_from_file()

    if not proxy_mgr.proxies:
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
        f"Всего: <b>{len(proxy_mgr.proxies)}</b>",
        "",
    ]
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
async def cb_proxy_validate(callback: CallbackQuery):
    await callback.answer("✅ Проверка...")
    if not proxy_mgr.proxies:
        await callback.message.edit_text(
            "✅ <b>Проверка прокси</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "<i>Нет прокси для проверки.</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=proxy_kb(),
        )
        return

    await callback.message.edit_text("⏳ Проверяю прокси, это может занять 1-2 минуты...")
    results = await proxy_mgr.validate_all()
    ok_count = sum(1 for item in results.values() if item)
    fail_count = len(results) - ok_count
    await callback.message.edit_text(
        "✅ <b>Проверка прокси</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Рабочие: <b>{ok_count}</b>\n"
        f"Нерабочие: <b>{fail_count}</b>",
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
async def cb_ch_list(callback: CallbackQuery):
    await callback.answer()
    channels = await channel_db.get_all_active()
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
        "Отправьте ключевые слова для поиска\n"
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
async def cb_ch_stats(callback: CallbackQuery):
    await callback.answer()
    stats = await channel_db.get_stats()
    by_topic = ", ".join(f"{topic}: {count}" for topic, count in stats["by_topic"].items()) or "нет данных"
    await callback.message.edit_text(
        "📊 <b>Статистика каналов</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Всего: <b>{stats['total']}</b>\n"
        f"Активные: <b>{stats['active']}</b>\n"
        f"С комментариями: <b>{stats['with_comments']}</b>\n"
        f"В чёрном списке: <b>{stats['blacklisted']}</b>\n\n"
        f"Тематики: <code>{escape(by_topic)}</code>",
        parse_mode=ParseMode.HTML,
        reply_markup=channels_kb(),
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


@router.callback_query(F.data.startswith("ch_"))
async def cb_ch_other(callback: CallbackQuery):
    await callback.answer("🔜 В разработке")


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

@router.callback_query(F.data == "com_start")
async def cb_com_start(callback: CallbackQuery):
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
async def process_delayed_minutes(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
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
    await callback.answer()
    cancelled = task_scheduler.cancel_delayed_start()
    text = "✅ Отложенный запуск отменён." if cancelled else "Нечего отменять."
    await callback.message.edit_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=commenting_kb(),
    )


@router.callback_query(F.data == "com_stats")
async def cb_com_stats(callback: CallbackQuery):
    await callback.answer()
    monitor_stats = channel_monitor.get_stats()
    poster_stats = comment_poster.get_stats()
    gen_stats = comment_generator.get_stats()

    status = "Работает" if monitor_stats["running"] else "Остановлен"

    await callback.message.edit_text(
        "📊 <b>Статистика комментинга</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"⚡ Статус: <b>{status}</b>\n\n"
        f"📨 Отправлено: <b>{poster_stats['sent']}</b>\n"
        f"❌ Ошибок: <b>{poster_stats['failed']}</b>\n"
        f"⏭ Пропущено: <b>{poster_stats['skipped']}</b>\n\n"
        f"📋 Очередь: <b>{monitor_stats['queue_size']}</b> постов\n"
        f"👁 Обнаружено постов: <b>{monitor_stats['total_seen']}</b>\n\n"
        f"🤖 AI модель: <code>{gen_stats['model']}</code>\n"
        f"🧠 AI доступен: <b>{'Да' if gen_stats['ai_available'] else 'Нет (фоллбэки)'}</b>",
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
        "🧪 <b>Тестовый комментарий</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "Отправьте текст поста — AI сгенерирует\n"
        "тестовый комментарий (сценарий A и B).\n\n"
        "<i>Комментарий НЕ будет отправлен, только показан.</i>",
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
        "🧪 <b>Тестовые комментарии</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📝 Пост: <i>{escape(post_text[:200])}</i>\n\n"
        f"<b>Сценарий A</b> (без ссылки):\n"
        f"<code>{escape(result_a['text'])}</code>\n"
        f"<i>Источник: {result_a['source']}</i>\n\n"
        f"<b>Сценарий B</b> (со ссылкой):\n"
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
        "🎯 <b>Сценарии комментирования</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>Сценарий A ({ratio_a}%):</b>\n"
        "Текст без ссылки. Яркая аватарка → профиль →\n"
        "закреплённый канал → пост со ссылкой на DartVPN\n\n"
        f"<b>Сценарий B ({ratio_b}%):</b>\n"
        "Текст со ссылкой на @DartVPNBot + рекомендация\n\n"
        "Изменить баланс можно в Настройках.",
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

    await callback.message.edit_text(
        "📜 <b>Сканирование завершено</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Добавлено в очередь: <b>{added}</b> постов\n"
        f"Общая очередь: <b>{channel_monitor.queue.size}</b>",
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
        "автоответчик отправляет ссылку на DartVPN.",
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


@router.callback_query(F.data == "back_commenting")
async def cb_back_commenting(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer()
    await callback.message.edit_text(
        "💬 <b>Комментинг</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=commenting_kb(),
    )


# --- Парсер ---

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
async def cb_topic_select(callback: CallbackQuery):
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
        found, saved = await run_keyword_search(keywords=keywords, topic=topic_key)
        await sync_to_sheets_snapshot()
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

    text = render_channels(found, title=f"✅ <b>{topic_name}</b>")
    text += f"\n\nСохранено в базу: <b>{saved}</b>"
    await callback.message.edit_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=parser_kb(),
    )


@router.callback_query(F.data == "parse_similar")
async def cb_parse_similar(callback: CallbackQuery):
    await callback.answer()
    usernames = await channel_db.get_usernames()
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
async def cb_parse_similar_run(callback: CallbackQuery):
    await callback.answer("Поиск запущен...")
    await callback.message.edit_text("⏳ Ищу похожие каналы...")

    try:
        usernames = await channel_db.get_usernames()
        found = await channel_discovery.find_similar_channels(usernames)

        if not found:
            await callback.message.edit_text(
                "🔗 Похожих каналов не найдено.",
                parse_mode=ParseMode.HTML,
                reply_markup=parser_kb(),
            )
            return

        # Сохранить в БД
        saved = 0
        for ch in found[:50]:
            try:
                await channel_db.add_channel(ch)
                saved += 1
            except Exception as exc:
                log.warning(f"Ошибка сохранения канала {getattr(ch, 'title', '?')}: {exc}")

        lines = [
            f"🔗 <b>Найдено {len(found)} похожих каналов</b>",
            f"Сохранено в базу: <b>{saved}</b>",
            "",
        ]
        for ch in found[:15]:
            un = f"@{ch.username}" if ch.username else str(ch.telegram_id)
            lines.append(f"  {un} — {escape(ch.title)} ({ch.subscribers:,})")

        if len(found) > 15:
            lines.append(f"\n  ... и ещё {len(found) - 15}")

        await callback.message.edit_text(
            "\n".join(lines),
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


@router.callback_query(F.data.startswith("parse_"))
async def cb_parse_other(callback: CallbackQuery):
    await callback.answer("🔜 В разработке")


@router.callback_query(F.data == "back_parser")
async def cb_back_parser(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    await callback.message.edit_text(
        "🔍 <b>Парсер каналов</b>",
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


@router.message(Command("blacklist"))
async def cmd_blacklist(message: Message):
    if not is_admin(message.from_user.id):
        return

    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].lstrip("-").isdigit():
        await message.answer("Использование: <code>/blacklist CHANNEL_ID</code>", parse_mode=ParseMode.HTML)
        return

    channel_id = int(parts[1])
    await channel_db.blacklist_channel(channel_id)
    await message.answer(f"Канал <code>{channel_id}</code> добавлен в чёрный список.", parse_mode=ParseMode.HTML)
    await sync_to_sheets_snapshot()


@router.message(ParserStates.waiting_keywords, F.text)
async def process_keywords_input(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    keywords = parse_keywords(message.text or "")
    if not keywords:
        await message.answer("Введите ключевые слова через запятую. Пример: <code>vpn, впн, proxy</code>", parse_mode=ParseMode.HTML)
        return

    await state.clear()
    progress = await message.answer(
        "⏳ Выполняю поиск каналов...\n"
        f"Ключи: <code>{escape(', '.join(keywords))}</code>",
        parse_mode=ParseMode.HTML,
    )

    try:
        found, saved = await run_keyword_search(keywords=keywords)
        await sync_to_sheets_snapshot()
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

    text = render_channels(found, title="✅ <b>Результаты поиска</b>")
    text += f"\n\nСохранено в базу: <b>{saved}</b>"
    await progress.edit_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=parser_kb(),
    )


@router.message(ParserStates.waiting_channel, F.text)
async def process_add_channel_input(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    ref = normalize_channel_ref(message.text or "")
    if not ref:
        await message.answer("Отправьте @username или ссылку вида <code>https://t.me/channel_name</code>", parse_mode=ParseMode.HTML)
        return

    await state.clear()
    progress = await message.answer("⏳ Проверяю канал и сохраняю в базу...")

    try:
        info = await channel_discovery.get_channel_info(ref)
        await channel_db.add_channel(info)
        await sync_to_sheets_snapshot()
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
        "✅ <b>Канал добавлен</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Название: <b>{escape(info.title)}</b>\n"
        f"Username: <code>@{escape(info.username or 'n/a')}</code>\n"
        f"Подписчики: <b>{info.subscribers}</b>\n"
        f"Комментарии: <b>{'включены' if info.comments_enabled else 'выключены'}</b>",
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
    "SCENARIO_B_RATIO",
    "DARTVPN_BOT_LINK",
    "DARTVPN_CHANNEL_LINK",
    "MONITOR_POLL_INTERVAL_SEC",
    "POST_MAX_AGE_HOURS",
    "LOG_LEVEL",
    "PROXY_ROTATING",
    "PROXY_STICKY_FORMAT",
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
        "⏱ <b>Лимиты и задержки</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📊 Макс. комментариев/день: <b>{settings.MAX_COMMENTS_PER_ACCOUNT_PER_DAY}</b>\n"
        f"⏱ Мин. задержка: <b>{settings.MIN_DELAY_BETWEEN_COMMENTS_SEC} сек</b>\n"
        f"⏱ Макс. задержка: <b>{settings.MAX_DELAY_BETWEEN_COMMENTS_SEC} сек</b>\n"
        f"❄️ Cooldown после ошибки: <b>{settings.COMMENT_COOLDOWN_AFTER_ERROR_SEC} сек</b>\n"
        f"🔥 Прогрев: {settings.WARMUP_DAY_1_LIMIT}→{settings.WARMUP_DAY_2_LIMIT}→{settings.WARMUP_DAY_3_LIMIT}→{settings.MAX_COMMENTS_PER_ACCOUNT_PER_DAY}",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📊 Изменить лимит/день", callback_data="set_change_limit")],
            [InlineKeyboardButton(text="⏱ Изменить мин. задержку", callback_data="set_change_min_delay")],
            [InlineKeyboardButton(text="⏱ Изменить макс. задержку", callback_data="set_change_max_delay")],
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


@router.callback_query(F.data == "set_dartvpn")
async def cb_set_dartvpn(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(SettingsStates.waiting_dartvpn_link)
    await callback.message.edit_text(
        "🔗 <b>Ссылка DartVPN</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Текущая: <code>{settings.DARTVPN_BOT_LINK}</code>\n\n"
        "Отправьте новую ссылку (например https://t.me/DartVPNBot):",
        parse_mode=ParseMode.HTML,
    )


@router.message(SettingsStates.waiting_dartvpn_link, F.text)
async def process_set_dartvpn(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    link = message.text.strip()
    if not link.startswith("https://t.me/") and not link.startswith("@"):
        await message.answer("Ссылка должна быть в формате https://t.me/... или @username")
        return

    await state.clear()
    settings.DARTVPN_BOT_LINK = link
    _update_env("DARTVPN_BOT_LINK", link)
    await message.answer(
        f"✅ Ссылка DartVPN обновлена: <code>{escape(link)}</code>",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_kb(),
    )


@router.callback_query(F.data == "set_ai")
async def cb_set_ai(callback: CallbackQuery):
    await callback.answer()
    gen_stats = comment_generator.get_stats()
    await callback.message.edit_text(
        "🤖 <b>Настройки AI</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Модель: <code>{settings.GEMINI_MODEL}</code>\n"
        f"AI доступен: <b>{'Да' if gen_stats['ai_available'] else 'Нет'}</b>\n"
        f"Недавних комментариев: <b>{gen_stats['recent_comments']}</b>\n\n"
        f"Emoji→Link Swap: <b>{'Вкл' if comment_poster.emoji_swap_enabled else 'Выкл'}</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=f"{'⏸ Выключить' if comment_poster.emoji_swap_enabled else '▶️ Включить'} Emoji Swap",
                callback_data="set_toggle_swap",
            )],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back_settings")],
        ]),
    )


@router.callback_query(F.data == "set_toggle_swap")
async def cb_set_toggle_swap(callback: CallbackQuery):
    comment_poster.emoji_swap_enabled = not comment_poster.emoji_swap_enabled
    status = "включён" if comment_poster.emoji_swap_enabled else "выключен"
    await callback.answer(f"Emoji Swap {status}")
    # Перерисовать меню
    gen_stats = comment_generator.get_stats()
    await callback.message.edit_text(
        "🤖 <b>Настройки AI</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Модель: <code>{settings.GEMINI_MODEL}</code>\n"
        f"AI доступен: <b>{'Да' if gen_stats['ai_available'] else 'Нет'}</b>\n\n"
        f"Emoji→Link Swap: <b>{'Вкл' if comment_poster.emoji_swap_enabled else 'Выкл'}</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=f"{'⏸ Выключить' if comment_poster.emoji_swap_enabled else '▶️ Включить'} Emoji Swap",
                callback_data="set_toggle_swap",
            )],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back_settings")],
        ]),
    )


@router.callback_query(F.data == "set_sheets")
async def cb_set_sheets(callback: CallbackQuery):
    await callback.answer()
    await callback.message.edit_text(
        "📊 <b>Google Sheets</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Статус: <b>{'Подключён' if sheets_storage.is_enabled else 'Отключён'}</b>\n"
        f"Spreadsheet ID: <code>{settings.CHANNELS_SPREADSHEET_ID[:20]}...</code>\n"
        f"Интервал синхронизации: <b>{settings.SHEETS_SYNC_INTERVAL_SEC} сек</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Синхронизировать сейчас", callback_data="set_sheets_sync_now")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back_settings")],
        ]),
    )


@router.callback_query(F.data == "set_sheets_sync_now")
async def cb_set_sheets_sync_now(callback: CallbackQuery):
    await callback.answer("Синхронизация...")
    try:
        await sync_to_sheets_snapshot()
        await callback.message.edit_text(
            "✅ Google Sheets синхронизирован!",
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
        "🔄 <b>Баланс сценариев A/B</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Сценарий A (без ссылки): <b>{100 - ratio_b}%</b>\n"
        f"Сценарий B (со ссылкой): <b>{ratio_b}%</b>\n\n"
        "Введите новый процент для Сценария B (10-50):",
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
        "⚙️ <b>Настройки</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=settings_kb(),
    )


# ============================================================
# Обработка файлов (.session)
# ============================================================

@router.message(F.document)
async def handle_document(message: Message):
    """Обработка загруженных файлов (.session, .txt)."""
    if not is_admin(message.from_user.id):
        return

    doc = message.document
    file_name = doc.file_name or ""

    if file_name.endswith(".session"):
        # Сохранение .session файла
        await message.answer(
            f"📥 Получен файл: <code>{file_name}</code>\n"
            "⏳ Сохраняю...",
            parse_mode=ParseMode.HTML,
        )

        bot = message.bot
        file = await bot.get_file(doc.file_id)
        raw_phone = file_name.replace(".session", "")
        # Sanitize: оставляем только цифры для предотвращения path traversal
        session_phone = "".join(c for c in raw_phone if c.isdigit())
        if not session_phone:
            await message.answer("⚠️ Имя файла должно содержать номер телефона (цифры).")
            return
        normalized_file_name = f"{session_phone}.session"
        save_path = settings.sessions_path / normalized_file_name
        # Проверка что путь не выходит за пределы sessions_path
        if not save_path.resolve().is_relative_to(settings.sessions_path.resolve()):
            await message.answer("⚠️ Недопустимое имя файла.")
            return
        await bot.download_file(file.file_path, str(save_path))

        account_phone = f"+{session_phone}"
        known_accounts = await account_mgr.load_accounts()
        exists = any(acc.phone.lstrip("+") == account_phone.lstrip("+") for acc in known_accounts)
        if not exists:
            try:
                await account_mgr.add_account(account_phone, normalized_file_name)
                db_status = "добавлен в БД"
            except Exception:
                db_status = "уже был в БД"
        else:
            db_status = "уже был в БД"

        await sync_to_sheets_snapshot()
        await message.answer(
            f"✅ Аккаунт сохранён!\n\n"
            f"📱 Телефон: <code>{account_phone}</code>\n"
            f"📁 Файл: <code>{normalized_file_name}</code>\n\n"
            f"🗂 Статус: <b>{db_status}</b>\n\n"
            "Теперь подключите аккаунт через меню 👤 Аккаунты",
            parse_mode=ParseMode.HTML,
        )
        log.info(f"Session file saved: {normalized_file_name}")

    elif file_name.endswith(".txt"):
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

        await message.answer(
            f"✅ Файл прокси сохранён!\n\n"
            f"Загружено прокси: <b>{loaded}</b>\n\n"
            "Перейдите в 🌐 Прокси → Загрузить из файла",
            parse_mode=ParseMode.HTML,
        )
        log.info(f"Proxy file saved: {file_name}")

    else:
        await message.answer(
            f"⚠️ Неизвестный тип файла: <code>{file_name}</code>\n\n"
            "Поддерживаются:\n"
            "• <code>.session</code> — аккаунты Telegram\n"
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
        if scheduler:
            scheduler.shutdown(wait=False)
        await account_mgr.disconnect_all()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(start_bot())
