"""
Синхронизация данных проекта с Google Sheets.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import gspread
from google.oauth2 import service_account
from gspread.exceptions import WorksheetNotFound

from utils.helpers import utcnow

from config import BASE_DIR
from utils.logger import log


SHEETS_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


class GoogleSheetsStorage:
    """Хранилище для зеркалирования данных из SQLite в Google Sheets."""

    CHANNELS_HEADERS = ["channel_id", "username", "title", "subscribers", "topic", "status", "added_date"]
    COMMENTS_HEADERS = ["timestamp", "account", "channel", "text_preview", "scenario", "status"]
    ACCOUNTS_HEADERS = ["phone", "proxy", "status", "today_count", "total"]
    STATS_HEADERS = ["date", "comments_sent", "successful", "failed"]
    LEADS_HEADERS = [
        "created_at",
        "name",
        "email",
        "company",
        "telegram_username",
        "use_case",
        "utm_source",
    ]
    BRIEFS_HEADERS = [
        "created_at",
        "tenant_id",
        "workspace_id",
        "product_name",
        "company",
        "offer_summary",
        "target_audience",
        "tone_of_voice",
        "completeness_score",
        "telegram_goals",
        "website_url",
        "channel_url",
        "bot_url",
    ]

    def __init__(self, credentials_file: str, spreadsheet_id: str):
        credentials_path = Path(credentials_file).expanduser()
        if not credentials_path.is_absolute():
            credentials_path = BASE_DIR / credentials_path

        self.credentials_file = credentials_path
        self.spreadsheet_id = (spreadsheet_id or "").strip()
        self._client: Optional[gspread.Client] = None
        self._spreadsheet: Optional[gspread.Spreadsheet] = None

        self._enabled = bool(self.spreadsheet_id and self.credentials_file.exists())
        if not self._enabled:
            log.info("Google Sheets sync отключён: не задан spreadsheet_id или отсутствует credentials.json")

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    async def sync_channels(self, channels: list[Any]):
        if not self._enabled:
            return
        try:
            await asyncio.to_thread(self._sync_channels_sync, channels)
        except Exception as exc:
            log.warning(f"Ошибка синка каналов в Google Sheets: {exc}")

    async def sync_comments_log(self, comments: list[Any]):
        if not self._enabled:
            return
        try:
            await asyncio.to_thread(self._sync_comments_sync, comments)
        except Exception as exc:
            log.warning(f"Ошибка синка комментариев в Google Sheets: {exc}")

    async def sync_accounts(self, accounts: list[Any]):
        if not self._enabled:
            return
        try:
            await asyncio.to_thread(self._sync_accounts_sync, accounts)
        except Exception as exc:
            log.warning(f"Ошибка синка аккаунтов в Google Sheets: {exc}")

    async def append_lead(self, lead: Any):
        if not self._enabled:
            return
        try:
            await asyncio.to_thread(self._append_lead_sync, lead)
        except Exception as exc:
            log.warning(f"Ошибка зеркалирования лида в Google Sheets: {exc}")

    async def append_business_brief(self, brief: Any):
        if not self._enabled:
            return
        try:
            await asyncio.to_thread(self._append_business_brief_sync, brief)
        except Exception as exc:
            log.warning(f"Ошибка зеркалирования business brief в Google Sheets: {exc}")

    async def get_daily_stats(self) -> dict:
        if not self._enabled:
            return {}
        try:
            return await asyncio.to_thread(self._get_daily_stats_sync)
        except Exception as exc:
            log.warning(f"Ошибка чтения дневной статистики из Google Sheets: {exc}")
            return {}

    def _sync_channels_sync(self, channels: list[Any]):
        ws = self._ensure_worksheet("Каналы", self.CHANNELS_HEADERS)
        rows = [self._channel_row(channel) for channel in channels]
        self._replace_worksheet_data(ws, self.CHANNELS_HEADERS, rows)

    def _sync_comments_sync(self, comments: list[Any]):
        ws = self._ensure_worksheet("Комментарии", self.COMMENTS_HEADERS)
        rows = [self._comment_row(comment) for comment in comments]
        self._replace_worksheet_data(ws, self.COMMENTS_HEADERS, rows)

    def _sync_accounts_sync(self, accounts: list[Any]):
        ws = self._ensure_worksheet("Аккаунты", self.ACCOUNTS_HEADERS)
        rows = [self._account_row(account) for account in accounts]
        self._replace_worksheet_data(ws, self.ACCOUNTS_HEADERS, rows)

    def _append_lead_sync(self, lead: Any):
        ws = self._ensure_worksheet("Лиды", self.LEADS_HEADERS)
        ws.append_row(self._lead_row(lead), value_input_option="RAW")

    def _append_business_brief_sync(self, brief: Any):
        ws = self._ensure_worksheet("Брифы", self.BRIEFS_HEADERS)
        ws.append_row(self._business_brief_row(brief), value_input_option="RAW")

    def _get_daily_stats_sync(self) -> dict:
        ws = self._ensure_worksheet("Статистика", self.STATS_HEADERS)
        values = ws.get_all_values()
        if len(values) <= 1:
            return {
                "date": "",
                "comments_sent": 0,
                "successful": 0,
                "failed": 0,
            }

        latest = values[-1]
        return {
            "date": latest[0] if len(latest) > 0 else "",
            "comments_sent": self._safe_int(latest[1] if len(latest) > 1 else 0),
            "successful": self._safe_int(latest[2] if len(latest) > 2 else 0),
            "failed": self._safe_int(latest[3] if len(latest) > 3 else 0),
        }

    def _get_spreadsheet(self) -> gspread.Spreadsheet:
        if self._spreadsheet is not None:
            return self._spreadsheet

        credentials = service_account.Credentials.from_service_account_file(
            str(self.credentials_file),
            scopes=SHEETS_SCOPES,
        )
        self._client = gspread.authorize(credentials)
        self._spreadsheet = self._client.open_by_key(self.spreadsheet_id)
        return self._spreadsheet

    def _ensure_worksheet(self, title: str, headers: list[str]) -> gspread.Worksheet:
        spreadsheet = self._get_spreadsheet()
        try:
            worksheet = spreadsheet.worksheet(title)
        except WorksheetNotFound:
            worksheet = spreadsheet.add_worksheet(
                title=title,
                rows=max(1000, len(headers) + 10),
                cols=max(20, len(headers) + 5),
            )

        existing_headers = worksheet.row_values(1)
        if existing_headers != headers:
            worksheet.clear()
            worksheet.update(range_name="A1", values=[headers], value_input_option="RAW")

        return worksheet

    @staticmethod
    def _replace_worksheet_data(worksheet: gspread.Worksheet, headers: list[str], rows: list[list[Any]]):
        """Атомарная замена данных: сначала пишем, потом чистим остатки."""
        data = [headers] + rows
        # Записать данные поверх существующих (без clear — не теряем данные при ошибке)
        if data:
            worksheet.update(range_name="A1", values=data, value_input_option="RAW")
        # Удалить лишние строки ниже (если старых данных было больше)
        total_rows = worksheet.row_count
        data_end = len(data) + 1
        if total_rows > data_end:
            try:
                worksheet.batch_clear([f"A{data_end}:Z{total_rows}"])
            except Exception:
                pass

    @staticmethod
    def _channel_row(channel: Any) -> list[Any]:
        status = "blacklisted" if bool(_read(channel, "is_blacklisted", False)) else "active"
        created_at = _read(channel, "created_at", utcnow())
        return [
            _read(channel, "telegram_id", _read(channel, "id", "")),
            _read(channel, "username", "") or "",
            _read(channel, "title", "") or "",
            _read(channel, "subscribers", 0),
            _read(channel, "topic", "") or "",
            status,
            _format_dt(created_at),
        ]

    @staticmethod
    def _comment_row(comment: Any) -> list[Any]:
        created_at = _read(comment, "created_at", utcnow())
        text = (_read(comment, "text", "") or "").replace("\n", " ").strip()
        preview = text if len(text) <= 120 else f"{text[:117]}..."
        return [
            _format_dt(created_at),
            _read(comment, "account_phone", _read(comment, "account_id", "")),
            _read(comment, "channel_name", _read(comment, "post_id", "")),
            preview,
            _read(comment, "scenario", ""),
            _read(comment, "status", ""),
        ]

    @staticmethod
    def _account_row(account: Any) -> list[Any]:
        proxy_str = _read(account, "proxy_url", "")
        if not proxy_str:
            proxy_id = _read(account, "proxy_id", "")
            proxy_str = str(proxy_id) if proxy_id is not None else ""

        return [
            _read(account, "phone", ""),
            proxy_str,
            _read(account, "status", ""),
            _read(account, "comments_today", 0),
            _read(account, "total_comments", 0),
        ]

    @staticmethod
    def _lead_row(lead: Any) -> list[Any]:
        created_at = _read(lead, "created_at", utcnow())
        username = _read(lead, "telegram_username", "") or ""
        if isinstance(username, str) and username.startswith("@"):
            username = username[1:]

        return [
            _format_dt(created_at),
            _read(lead, "name", "") or "",
            _read(lead, "email", "") or "",
            _read(lead, "company", "") or "",
            username,
            _read(lead, "use_case", "") or "",
            _read(lead, "utm_source", "") or "",
        ]

    @staticmethod
    def _business_brief_row(brief: Any) -> list[Any]:
        created_at = _read(brief, "created_at", utcnow())
        goals = _read(brief, "telegram_goals", []) or []
        if not isinstance(goals, list):
            goals = [str(goals)]
        return [
            _format_dt(created_at),
            _read(brief, "tenant_id", ""),
            _read(brief, "workspace_id", ""),
            _read(brief, "product_name", "") or "",
            _read(brief, "company", "") or "",
            _read(brief, "offer_summary", "") or "",
            _read(brief, "target_audience", "") or "",
            _read(brief, "tone_of_voice", "") or "",
            _read(brief, "completeness_score", 0),
            ", ".join(str(item) for item in goals if str(item).strip()),
            _read(brief, "website_url", "") or "",
            _read(brief, "channel_url", "") or "",
            _read(brief, "bot_url", "") or "",
        ]

    @staticmethod
    def _safe_int(value: Any) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0


def _read(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def _format_dt(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value or "")
