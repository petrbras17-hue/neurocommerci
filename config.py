"""
NEURO COMMENTING — Центральная конфигурация.
Все настройки загружаются из .env файла.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings
from pydantic import Field
from pathlib import Path
import logging


BASE_DIR = Path(__file__).parent

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    # --- Telegram API ---
    TELEGRAM_API_ID: int = Field(default=0)
    TELEGRAM_API_HASH: str = Field(default="")

    # --- Admin Bot (опционально) ---
    ADMIN_BOT_TOKEN: str = Field(default="")
    ADMIN_TELEGRAM_ID: int = Field(default=0)

    # --- Google Gemini API (исполнитель — генерация текста) ---
    GEMINI_API_KEY: str = Field(default="")
    GEMINI_MODEL: str = Field(default="gemini-3.1-pro-preview")

    # --- Anthropic Claude API (дирижёр — анализ, стратегия, контроль) ---
    ANTHROPIC_API_KEY: str = Field(default="")
    CLAUDE_MODEL: str = Field(default="claude-sonnet-4-6")

    # --- Google Sheets ---
    GOOGLE_SHEETS_CREDENTIALS_FILE: str = Field(default="credentials.json")
    CHANNELS_SPREADSHEET_ID: str = Field(default="")
    STATS_SPREADSHEET_ID: str = Field(default="")

    # --- Proxy ---
    PROXY_LIST_FILE: str = Field(default="data/proxies.txt")
    PROXY_TYPE: str = Field(default="socks5")  # socks5 | http
    PROXY_ROTATING: bool = Field(default=False)  # Режим ротируемых прокси (sticky sessions)
    PROXY_STICKY_FORMAT: str = Field(default="{user}-session-{session_id}")  # Формат sticky username

    # --- Rate Limits ---
    MAX_COMMENTS_PER_ACCOUNT_PER_DAY: int = Field(default=35)
    MIN_DELAY_BETWEEN_COMMENTS_SEC: int = Field(default=120)
    MAX_DELAY_BETWEEN_COMMENTS_SEC: int = Field(default=600)
    COMMENT_COOLDOWN_AFTER_ERROR_SEC: int = Field(default=1800)

    # --- Product Promotion (generic — configure for any product) ---
    PRODUCT_NAME: str = Field(default="DartVPN")
    PRODUCT_BOT_USERNAME: str = Field(default="DartVPNBot")  # without @
    PRODUCT_BOT_LINK: str = Field(default="https://t.me/DartVPNBot?start=fly")
    PRODUCT_CHANNEL_LINK: str = Field(default="")
    PRODUCT_AVATAR_PATH: str = Field(default="data/avatars/dartvpn_banner.jpg")
    PRODUCT_SHORT_DESC: str = Field(default="VPN с оплатой за гигабайты")
    PRODUCT_FEATURES: str = Field(default="оплата по ГБ, карта Мир, работает в Telegram")
    PRODUCT_CATEGORY: str = Field(default="VPN")  # VPN / AI / Bot / Service
    PRODUCT_CHANNEL_PREFIX: str = Field(default="dartvpn")
    SCENARIO_B_RATIO: float = Field(default=0.3)

    # --- Warm-up (14-дневный прогрев новых аккаунтов) ---
    WARMUP_LIGHT_LIMIT: int = Field(default=3)      # Дни 5-7: 1-3 коммента
    WARMUP_MODERATE_LIMIT: int = Field(default=8)    # Дни 8-14: 5-8 комментов

    # --- Session Health & Keep-Alive ---
    SESSION_HEALTH_CHECK_HOURS: int = Field(default=4)  # Проверка авторизации
    KEEP_ALIVE_INTERVAL_HOURS: int = Field(default=6)   # Периодический get_me / read
    ACCOUNT_SLEEP_START_HOUR: int = Field(default=23)    # Начало "сна" (UTC)
    ACCOUNT_SLEEP_END_HOUR: int = Field(default=7)       # Конец "сна" (UTC)
    SESSION_BACKUP_KEY: str = Field(default="")          # Fernet key для шифрования бэкапов

    # --- Monitoring ---
    MONITOR_POLL_INTERVAL_SEC: int = Field(default=180)  # 3 мин
    POST_MAX_AGE_HOURS: int = Field(default=2)

    # --- Paths ---
    SESSIONS_DIR: str = Field(default="data/sessions")
    PROFILE_AVATARS_DIR: str = Field(default="data/avatars/profiles")
    DB_PATH: str = Field(default="data/neuro_commenting.db")
    LOG_LEVEL: str = Field(default="INFO")

    # --- Google Sheets sync ---
    SHEETS_SYNC_INTERVAL_SEC: int = Field(default=300)  # 5 мин

    # --- Infrastructure ---
    DATABASE_URL: str = Field(default="")  # postgresql+asyncpg://... (empty = use SQLite)
    REDIS_URL: str = Field(default="redis://localhost:6379/0")
    WORKER_ID: str = Field(default="main")  # Worker identifier for distributed mode
    MAX_ACCOUNTS_PER_WORKER: int = Field(default=50)
    API_ID_4_STRICT_MODE: bool = Field(default=True)  # Stricter limits for flagged API ID 4

    model_config = {
        "env_file": str(BASE_DIR / ".env"),
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

    _dirs_created: bool = False

    def _ensure_dirs(self) -> None:
        """Create data directories once on first access."""
        if Settings._dirs_created:
            return
        (BASE_DIR / self.SESSIONS_DIR).mkdir(parents=True, exist_ok=True)
        (BASE_DIR / self.PROFILE_AVATARS_DIR).mkdir(parents=True, exist_ok=True)
        (BASE_DIR / self.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
        Settings._dirs_created = True

    @property
    def sessions_path(self) -> Path:
        self._ensure_dirs()
        return BASE_DIR / self.SESSIONS_DIR

    @property
    def profile_avatars_path(self) -> Path:
        self._ensure_dirs()
        return BASE_DIR / self.PROFILE_AVATARS_DIR

    @property
    def db_url(self) -> str:
        if self.DATABASE_URL:
            return self.DATABASE_URL
        self._ensure_dirs()
        return f"sqlite+aiosqlite:///{BASE_DIR / self.DB_PATH}"

    @property
    def proxy_list_path(self) -> Path:
        return BASE_DIR / self.PROXY_LIST_FILE

    @property
    def product_bot_mention(self) -> str:
        """@BotUsername style mention."""
        return f"@{self.PRODUCT_BOT_USERNAME}"

    @staticmethod
    def _parse_bot_username_from_link(link: str) -> str | None:
        """Extract bot username from t.me link. Returns None if can't parse."""
        # https://t.me/BotName?start=fly -> BotName
        # https://t.me/BotName -> BotName
        if "t.me/" not in link:
            return None
        try:
            after_tme = link.split("t.me/")[1]
            username = after_tme.split("?")[0].split("/")[0].strip()
            return username if username else None
        except (IndexError, AttributeError):
            return None

    def validate_critical(self) -> list[str]:
        """Проверить критичные настройки при старте. Возвращает список предупреждений."""
        warnings = []
        if self.TELEGRAM_API_ID == 0:
            warnings.append("TELEGRAM_API_ID не задан (=0)")
        if self.TELEGRAM_API_ID == 4:
            warnings.append(
                "TELEGRAM_API_ID=4 ПОМЕЧЕН Telegram как опасный! "
                "Используйте 2040 (Desktop) или 21724 (AndroidX) для новых аккаунтов"
            )
        if not self.TELEGRAM_API_HASH:
            warnings.append("TELEGRAM_API_HASH пуст")
        if not self.ADMIN_BOT_TOKEN:
            warnings.append("ADMIN_BOT_TOKEN не задан — бот не запустится")
        if not self.GEMINI_API_KEY:
            warnings.append("GEMINI_API_KEY не задан — AI генерация отключена, используются фоллбэки")
        # Product config consistency: username must match the link
        if self.PRODUCT_BOT_LINK and self.PRODUCT_BOT_USERNAME:
            parsed = self._parse_bot_username_from_link(self.PRODUCT_BOT_LINK)
            if parsed and parsed != self.PRODUCT_BOT_USERNAME:
                msg = (
                    f"PRODUCT_BOT_USERNAME ({self.PRODUCT_BOT_USERNAME}) "
                    f"не совпадает с username из PRODUCT_BOT_LINK ({parsed})"
                )
                warnings.append(msg)
                logger.warning(msg)
        return warnings


settings = Settings()
