"""
Telegram Bot Auth — авторизация через deep link на бот.

Flow:
1. Frontend запрашивает POST /auth/telegram/bot-start → получает auth_code
2. Frontend открывает https://t.me/{bot}?start=auth_{code}
3. Бот ловит /start auth_{code} → сохраняет telegram user data
4. Frontend polls GET /auth/telegram/bot-check?code={code} → получает auth bundle когда бот подтвердил
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import Optional

from aiogram import Bot, Dispatcher, Router
from aiogram.filters import CommandStart
from aiogram.types import Message

log = logging.getLogger(__name__)

# In-memory store for pending auth codes. TTL = 5 minutes.
AUTH_CODE_TTL_SEC = 300
MAX_PENDING_AUTH_CODES = 10_000  # prevent unbounded dict growth under abuse


@dataclass
class PendingAuth:
    created_at: float
    telegram_user: Optional[dict] = None
    confirmed: bool = False


_pending: dict[str, PendingAuth] = {}
_bot: Optional[Bot] = None
_dispatcher: Optional[Dispatcher] = None
_polling_task: Optional[asyncio.Task] = None

router = Router()


def generate_auth_code() -> str:
    """Generate a new auth code and store it as pending."""
    _cleanup_expired()
    if len(_pending) >= MAX_PENDING_AUTH_CODES:
        # Force-evict oldest entries to stay within cap.
        sorted_codes = sorted(_pending, key=lambda k: _pending[k].created_at)
        for old_code in sorted_codes[: len(_pending) - MAX_PENDING_AUTH_CODES + 1]:
            _pending.pop(old_code, None)
    code = secrets.token_urlsafe(32)
    _pending[code] = PendingAuth(created_at=time.time())
    return code


def get_pending_auth(code: str) -> Optional[PendingAuth]:
    """Check if an auth code has been confirmed by the bot."""
    _cleanup_expired()
    return _pending.get(code)


def consume_pending_auth(code: str) -> Optional[dict]:
    """Consume and return the telegram user data if confirmed. Removes the code."""
    pending = _pending.pop(code, None)
    if pending and pending.confirmed and pending.telegram_user:
        return pending.telegram_user
    return None


def _cleanup_expired():
    now = time.time()
    expired = [k for k, v in _pending.items() if now - v.created_at > AUTH_CODE_TTL_SEC]
    for k in expired:
        _pending.pop(k, None)


@router.message(CommandStart(deep_link=True))
async def handle_start_auth(message: Message):
    """Handle /start auth_{code} deep link."""
    args = message.text or ""
    parts = args.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Добро пожаловать в NEURO COMMENTING! Используйте платформу для входа.")
        return

    payload = parts[1]
    if not payload.startswith("auth_"):
        await message.answer("Добро пожаловать в NEURO COMMENTING! Используйте платформу для входа.")
        return

    code = payload[5:]  # strip "auth_" prefix
    pending = _pending.get(code)
    if not pending:
        await message.answer(
            "Ссылка для авторизации устарела или недействительна.\n"
            "Вернитесь на платформу и попробуйте снова."
        )
        return

    if pending.confirmed:
        await message.answer("Вы уже авторизованы! Вернитесь на платформу.")
        return

    user = message.from_user
    if not user:
        return

    pending.telegram_user = {
        "id": user.id,
        "first_name": user.first_name or "",
        "last_name": user.last_name or "",
        "username": user.username or "",
        "language_code": user.language_code or "",
        "is_premium": getattr(user, "is_premium", False) or False,
    }
    pending.confirmed = True

    await message.answer(
        f"Авторизация успешна, {user.first_name}! Вернитесь на платформу — вход произойдёт автоматически."
    )
    log.info("Bot auth confirmed for telegram_id=%s code=%s...", user.id, code[:8])


@router.message(CommandStart())
async def handle_start_plain(message: Message):
    """Handle plain /start without deep link."""
    await message.answer(
        "Добро пожаловать в NEURO COMMENTING!\n\n"
        "Для входа в платформу перейдите на сайт и нажмите «Войти через Telegram бот»."
    )


async def start_bot_polling(token: str) -> None:
    """Start the auth bot in polling mode as a background task."""
    global _bot, _dispatcher, _polling_task

    if not token:
        log.warning("AUTH_BOT_TOKEN not set, Telegram bot auth disabled")
        return

    _bot = Bot(token=token)
    _dispatcher = Dispatcher()
    _dispatcher.include_router(router)

    log.info("Starting Telegram auth bot polling...")
    _polling_task = asyncio.create_task(_run_polling())
    def _on_polling_done(t: asyncio.Task) -> None:
        if not t.cancelled():
            exc = t.exception()
            if exc:
                log.error("auth bot polling task failed: %s", exc, exc_info=exc)
    _polling_task.add_done_callback(_on_polling_done)


async def _run_polling():
    """Run bot polling, suppress errors gracefully."""
    try:
        if _dispatcher and _bot:
            await _dispatcher.start_polling(_bot, handle_signals=False)
    except asyncio.CancelledError:
        log.info("Auth bot polling cancelled")
    except Exception:
        log.exception("Auth bot polling error")


async def stop_bot_polling() -> None:
    """Stop the auth bot gracefully."""
    global _polling_task, _bot, _dispatcher
    if _polling_task:
        _polling_task.cancel()
        try:
            await _polling_task
        except (asyncio.CancelledError, Exception):
            pass
        _polling_task = None
    if _dispatcher:
        await _dispatcher.stop_polling()
        _dispatcher = None
    if _bot:
        await _bot.session.close()
        _bot = None
    log.info("Auth bot stopped")
