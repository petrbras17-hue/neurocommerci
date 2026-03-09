from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from html import escape
from typing import Any, Optional

from aiogram import Bot

from config import settings
from storage.google_sheets import GoogleSheetsStorage
from utils.helpers import utcnow
from utils.logger import log


@dataclass(frozen=True)
class LeadSnapshot:
    lead_id: int
    name: str
    email: str
    company: str
    telegram_username: Optional[str]
    use_case: str
    utm_source: Optional[str]
    created_at: datetime


def build_lead_notification_text(lead: LeadSnapshot) -> str:
    username = lead.telegram_username or "—"
    if username != "—" and not username.startswith("@"):
        username = f"@{username}"
    utm = lead.utm_source or "—"
    created_at = lead.created_at.strftime("%Y-%m-%d %H:%M:%S")
    return (
        "🔥 <b>Новый lead с лендинга</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"ID: <code>{lead.lead_id}</code>\n"
        f"Имя: <b>{escape(lead.name)}</b>\n"
        f"Email: <code>{escape(lead.email)}</code>\n"
        f"Компания: <b>{escape(lead.company)}</b>\n"
        f"Telegram: {escape(username)}\n"
        f"Use case: <b>{escape(lead.use_case)}</b>\n"
        f"UTM: {escape(utm)}\n"
        f"Создан: <code>{created_at}</code>"
    )


@lru_cache(maxsize=1)
def _lead_sheets_storage() -> GoogleSheetsStorage:
    spreadsheet_id = str(settings.CHANNELS_SPREADSHEET_ID or settings.STATS_SPREADSHEET_ID or "").strip()
    return GoogleSheetsStorage(
        credentials_file=settings.GOOGLE_SHEETS_CREDENTIALS_FILE,
        spreadsheet_id=spreadsheet_id,
    )


async def mirror_lead_to_google_sheets(lead: LeadSnapshot) -> dict[str, Any]:
    storage = _lead_sheets_storage()
    if not storage.is_enabled:
        return {
            "ok": False,
            "skipped": True,
            "error": "sheets_not_configured",
        }
    await storage.append_lead(lead)
    return {"ok": True, "worksheet": "Лиды"}


async def _send_text_via_bot(token: str, chat_id: str, text: str) -> dict[str, Any]:
    if not token:
        return {"ok": False, "skipped": True, "error": "bot_token_missing"}
    if not chat_id or str(chat_id).strip() == "0":
        return {"ok": False, "skipped": True, "error": "chat_id_missing"}

    bot = Bot(token=token)
    try:
        message = await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        return {
            "ok": True,
            "chat_id": str(chat_id),
            "message_id": int(message.message_id),
        }
    finally:
        await bot.session.close()


async def send_admin_lead_notification(lead: LeadSnapshot) -> dict[str, Any]:
    return await _send_text_via_bot(
        token=str(settings.ADMIN_BOT_TOKEN or "").strip(),
        chat_id=str(settings.ADMIN_TELEGRAM_ID or "").strip(),
        text=build_lead_notification_text(lead),
    )


async def send_digest_lead_notification(lead: LeadSnapshot) -> dict[str, Any]:
    return await _send_text_via_bot(
        token=str(settings.DIGEST_BOT_TOKEN or "").strip(),
        chat_id=str(settings.DIGEST_CHAT_ID or "").strip(),
        text=build_lead_notification_text(lead),
    )


async def deliver_lead_funnel(lead: LeadSnapshot) -> dict[str, Any]:
    result: dict[str, Any] = {}

    async def _guard(name: str, fn) -> None:
        try:
            result[name] = await fn(lead)
        except Exception as exc:
            log.warning(f"Lead funnel side effect failed ({name}, lead_id={lead.lead_id}): {exc}")
            result[name] = {
                "ok": False,
                "error": str(exc),
            }

    await _guard("google_sheets", mirror_lead_to_google_sheets)
    await _guard("admin_bot", send_admin_lead_notification)
    await _guard("digest_bot", send_digest_lead_notification)

    result["lead_id"] = int(lead.lead_id)
    result["processed_at"] = utcnow().isoformat()
    return result
