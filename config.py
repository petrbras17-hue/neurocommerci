"""
NEURO COMMENTING — Центральная конфигурация.
Все настройки загружаются из .env файла.
"""

from pydantic_settings import BaseSettings
from pydantic import Field
from pathlib import Path


BASE_DIR = Path(__file__).parent


class Settings(BaseSettings):
    # --- Telegram API ---
    TELEGRAM_API_ID: int = Field(default=0)
    TELEGRAM_API_HASH: str = Field(default="")

    # --- Admin Bot (опционально) ---
    ADMIN_BOT_TOKEN: str = Field(default="")
    ADMIN_TELEGRAM_ID: int = Field(default=0)

    # --- Google Gemini API ---
    GEMINI_API_KEY: str = Field(default="")
    GEMINI_MODEL: str = Field(default="gemini-3.1-pro-preview")

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

    # --- DartVPN Promotion ---
    DARTVPN_BOT_LINK: str = Field(default="https://t.me/DartVPNBot?start=fly")
    DARTVPN_CHANNEL_LINK: str = Field(default="")
    DARTVPN_AVATAR_PATH: str = Field(default="data/avatars/dartvpn_banner.jpg")
    SCENARIO_B_RATIO: float = Field(default=0.3)

    # --- Warm-up (прогрев новых аккаунтов) ---
    WARMUP_DAY_1_LIMIT: int = Field(default=5)
    WARMUP_DAY_2_LIMIT: int = Field(default=10)
    WARMUP_DAY_3_LIMIT: int = Field(default=20)

    # --- Monitoring ---
    MONITOR_POLL_INTERVAL_SEC: int = Field(default=180)  # 3 мин
    POST_MAX_AGE_HOURS: int = Field(default=2)

    # --- Paths ---
    SESSIONS_DIR: str = Field(default="data/sessions")
    DB_PATH: str = Field(default="data/neuro_commenting.db")
    LOG_LEVEL: str = Field(default="INFO")

    # --- Google Sheets sync ---
    SHEETS_SYNC_INTERVAL_SEC: int = Field(default=300)  # 5 мин

    model_config = {
        "env_file": str(BASE_DIR / ".env"),
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

    @property
    def sessions_path(self) -> Path:
        path = BASE_DIR / self.SESSIONS_DIR
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def db_url(self) -> str:
        db_path = BASE_DIR / self.DB_PATH
        db_path.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite+aiosqlite:///{db_path}"

    @property
    def proxy_list_path(self) -> Path:
        return BASE_DIR / self.PROXY_LIST_FILE

    def validate_critical(self) -> list[str]:
        """Проверить критичные настройки при старте. Возвращает список предупреждений."""
        warnings = []
        if self.TELEGRAM_API_ID == 0:
            warnings.append("TELEGRAM_API_ID не задан (=0)")
        if not self.TELEGRAM_API_HASH:
            warnings.append("TELEGRAM_API_HASH пуст")
        if not self.ADMIN_BOT_TOKEN:
            warnings.append("ADMIN_BOT_TOKEN не задан — бот не запустится")
        if not self.GEMINI_API_KEY:
            warnings.append("GEMINI_API_KEY не задан — AI генерация отключена, используются фоллбэки")
        return warnings


settings = Settings()
