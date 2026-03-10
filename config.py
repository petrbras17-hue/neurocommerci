"""
NEURO COMMENTING — Центральная конфигурация.
Все настройки загружаются из .env файла.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, PydanticBaseSettingsSource
from pydantic import Field
from pathlib import Path
import logging


BASE_DIR = Path(__file__).parent

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    # --- Runtime profile ---
    APP_ENV: str = Field(default="development")

    # --- Telegram API ---
    TELEGRAM_API_ID: int = Field(default=0)
    TELEGRAM_API_HASH: str = Field(default="")

    # --- Admin Bot (опционально) ---
    ADMIN_BOT_TOKEN: str = Field(default="")
    ADMIN_BOT_USERNAME: str = Field(default="dartvpn_neurocom_bot")
    ADMIN_TELEGRAM_ID: int = Field(default=0)

    # --- Google Gemini API (исполнитель — генерация текста) ---
    GEMINI_API_KEY: str = Field(default="")
    GEMINI_MODEL: str = Field(default="gemini-3-pro-preview")
    GEMINI_FLASH_MODEL: str = Field(default="gemini-3-flash-preview")
    OPENROUTER_API_KEY: str = Field(default="")
    OPENROUTER_BASE_URL: str = Field(default="https://openrouter.ai/api/v1")
    OPENROUTER_DEFAULT_REFERER: str = Field(default="")
    OPENROUTER_DEFAULT_TITLE: str = Field(default="NEURO COMMENTING")
    AI_DEFAULT_MODE: str = Field(default="hybrid")  # gemini_only | openrouter_only | hybrid
    AI_ALLOWED_PROVIDER_ORDER: str = Field(default="gemini_direct,openrouter")
    AI_BOSS_MODELS: str = Field(default="")
    AI_MANAGER_MODELS: str = Field(default="")
    AI_WORKER_MODELS: str = Field(default="")
    AI_DAILY_BUDGET_USD: float = Field(default=25.0)
    AI_MONTHLY_BUDGET_USD: float = Field(default=500.0)
    AI_BOSS_DAILY_BUDGET_USD: float = Field(default=5.0)
    AI_HARD_STOP_ENABLED: bool = Field(default=True)

    # --- Legacy compatibility: old Claude env vars (no longer used in critical runtime) ---
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
    PROXY_HEALTH_TIMEOUT_SEC: int = Field(default=8)
    PROXY_RECHECK_COOLDOWN_SEC: int = Field(default=900)
    PROXY_FAILURES_BEFORE_DISABLE: int = Field(default=2)
    PROXY_MIN_FREE_POOL: int = Field(default=20)
    PROXY_DELETE_INVALID_AFTER_DAYS: int = Field(default=7)

    # --- Rate Limits ---
    MAX_COMMENTS_PER_ACCOUNT_PER_DAY: int = Field(default=35)
    MIN_DELAY_BETWEEN_COMMENTS_SEC: int = Field(default=120)
    MAX_DELAY_BETWEEN_COMMENTS_SEC: int = Field(default=600)
    COMMENT_COOLDOWN_AFTER_ERROR_SEC: int = Field(default=1800)
    MIN_EXISTING_COMMENTS_BEFORE_COMMENT: int = Field(default=2)  # 2 => мы пишем минимум третьими
    MIN_COMMENTS_RECHECK_MAX_ATTEMPTS: int = Field(default=12)

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
    # Legacy one-release compatibility with old env keys.
    DARTVPN_BOT_LINK: str = Field(default="")
    DARTVPN_CHANNEL_LINK: str = Field(default="")
    DARTVPN_AVATAR_PATH: str = Field(default="")

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
    ONBOARDING_MEMORY_PATH: str = Field(default="knowledge/project_context/account_onboarding_memory.md")

    # --- Google Sheets sync ---
    SHEETS_SYNC_INTERVAL_SEC: int = Field(default=300)  # 5 мин

    # --- Infrastructure ---
    DATABASE_URL: str = Field(default="")  # postgresql+asyncpg://... (empty = use SQLite)
    REDIS_URL: str = Field(default="redis://localhost:6379/0")
    OPS_API_URL: str = Field(default="")
    OPS_API_HOST: str = Field(default="0.0.0.0")
    OPS_API_PORT: int = Field(default=8081)
    OPS_API_TOKEN: str = Field(default="")
    WEBAPP_DEV_ORIGIN: str = Field(default="http://localhost:5173")
    WEBAPP_SESSION_COOKIE_NAME: str = Field(default="nc_refresh_token")
    JWT_ACCESS_SECRET: str = Field(default="")
    JWT_REFRESH_SECRET: str = Field(default="")
    JWT_ALGORITHM: str = Field(default="HS256")
    JWT_ACCESS_TTL_MINUTES: int = Field(default=30)
    JWT_REFRESH_TTL_DAYS: int = Field(default=30)
    DIGEST_BOT_TOKEN: str = Field(default="")
    DIGEST_CHAT_ID: str = Field(default="")
    DIGEST_MAX_ITEMS: int = Field(default=10)
    DIGEST_DAILY_HOUR_UTC: int = Field(default=9)
    DIGEST_DAILY_MINUTE_UTC: int = Field(default=0)
    DIGEST_SCHEDULE_ENABLED: bool = Field(default=True)
    WORKER_ID: str = Field(default="main")  # Worker identifier for distributed mode
    PINNED_PHONE: str = Field(default="")  # Optional: force worker to own exactly one phone
    MAX_ACCOUNTS_PER_WORKER: int = Field(default=50)
    MAX_CONNECTED_CLIENTS_PER_WORKER: int = Field(default=50)
    WORKER_CONNECT_BATCH_SIZE: int = Field(default=5)
    WORKER_DEQUEUE_TIMEOUT_SEC: int = Field(default=5)
    STRICT_PROXY_PER_ACCOUNT: bool = Field(default=True)
    PINNED_PHONE_REQUIRED: bool = Field(default=True)
    PACKAGING_DELAY_SCALE: float = Field(default=1.0)
    PACKAGING_DELAY_MIN_SEC: int = Field(default=30)
    PACKAGING_DELAY_MAX_SEC: int = Field(default=120)
    PACKAGING_ALLOW_BIO_FALLBACK: bool = Field(default=True)
    ENABLE_LEGACY_COMMENTING: bool = Field(default=False)
    ENABLE_EMOJI_SWAP: bool = Field(default=False)
    HUMAN_GATED_PACKAGING: bool = Field(default=True)
    HUMAN_GATED_COMMENTS: bool = Field(default=True)
    HUMAN_GATED_EXTERNAL_REVIEW_REQUIRED: bool = Field(default=True)
    NEW_ACCOUNT_LAUNCH_MODE: str = Field(default="faster_1d")  # conservative | faster_1d
    STRICT_PARSER_ONLY: bool = Field(default=True)
    FROZEN_PROBE_ON_CONNECT: bool = Field(default=True)
    FROZEN_PROBE_BEFORE_PACKAGING: bool = Field(default=True)
    FROZEN_PROBE_BEFORE_PARSER: bool = Field(default=True)
    DISTRIBUTED_QUEUE_MODE: bool = Field(default=False)  # Producer in bot -> consumer in workers
    COMPLIANCE_MODE: str = Field(default="strict")  # off | warn | strict
    POLICY_RULES_PATH: str = Field(default="policy/rules.yaml")
    PARSER_ONLY_PHONE: str = Field(default="")
    PARSER_MIN_SUBSCRIBERS: int = Field(default=500)
    PARSER_REQUIRE_COMMENTS: bool = Field(default=True)
    PARSER_REQUIRE_RUSSIAN: bool = Field(default=True)
    PARSER_STAGE1_LIMIT: int = Field(default=30)
    MANUAL_GATE_REQUIRED: bool = Field(default=True)
    ENABLE_CLIENT_WIZARD: bool = Field(default=True)
    ENABLE_ADMIN_LEGACY_TOOLS: bool = Field(default=True)
    STRICT_SLO_WINDOW_DAYS: int = Field(default=30)
    API_ID_4_STRICT_MODE: bool = Field(default=True)  # Stricter limits for flagged API ID 4
    AUTO_SPAMBOT_APPEAL_ENABLED: bool = Field(default=False)
    AUTO_SPAMBOT_APPEAL_INTERVAL_SEC: int = Field(default=900)
    AUTO_SPAMBOT_CHECK_COOLDOWN_HOURS: int = Field(default=6)
    AUTO_SPAMBOT_APPEAL_COOLDOWN_HOURS: int = Field(default=24)
    AUTO_SPAMBOT_APPEAL_MAX_STEPS: int = Field(default=20)
    AUTO_SPAMBOT_APPEAL_BATCH_SIZE: int = Field(default=5)
    AUTO_SPAMBOT_APPEAL_EMAIL: str = Field(default="")
    AUTO_SPAMBOT_APPEAL_REG_YEAR: str = Field(default="2024")

    model_config = {
        "env_file": str(BASE_DIR / ".env"),
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls,
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ):
        # Mounted /app/.env is the runtime source of truth. This avoids stale
        # Docker Compose env_file values surviving plain container restarts.
        return (
            init_settings,
            dotenv_settings,
            env_settings,
            file_secret_settings,
        )

    _dirs_created: bool = False

    def model_post_init(self, __context) -> None:
        """One-release compatibility: map legacy DARTVPN_* env keys to PRODUCT_*."""
        if self.DARTVPN_BOT_LINK and self.PRODUCT_BOT_LINK == "https://t.me/DartVPNBot?start=fly":
            self.PRODUCT_BOT_LINK = self.DARTVPN_BOT_LINK
        if self.DARTVPN_CHANNEL_LINK and not self.PRODUCT_CHANNEL_LINK:
            self.PRODUCT_CHANNEL_LINK = self.DARTVPN_CHANNEL_LINK
        if self.DARTVPN_AVATAR_PATH and self.PRODUCT_AVATAR_PATH == "data/avatars/dartvpn_banner.jpg":
            self.PRODUCT_AVATAR_PATH = self.DARTVPN_AVATAR_PATH

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
    def onboarding_memory_path(self) -> Path:
        path = BASE_DIR / self.ONBOARDING_MEMORY_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def policy_rules_path(self) -> Path:
        return BASE_DIR / self.POLICY_RULES_PATH

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
        if self.DIGEST_BOT_TOKEN and not str(self.DIGEST_CHAT_ID or "").strip():
            warnings.append("DIGEST_BOT_TOKEN задан, но DIGEST_CHAT_ID пуст — сводки некуда отправлять")
        if not self.GEMINI_API_KEY:
            warnings.append("GEMINI_API_KEY не задан — AI генерация отключена, используются фоллбэки")
        if self.GEMINI_FLASH_MODEL.strip() and self.GEMINI_FLASH_MODEL.strip() == self.GEMINI_MODEL.strip():
            warnings.append("GEMINI_FLASH_MODEL совпадает с GEMINI_MODEL — fallback на Flash фактически выключен")
        if self.DARTVPN_BOT_LINK or self.DARTVPN_CHANNEL_LINK or self.DARTVPN_AVATAR_PATH:
            warnings.append(
                "Используются legacy-переменные DARTVPN_*. "
                "Перейдите на PRODUCT_* (поддержка DARTVPN_* будет удалена)."
            )
        if self.DISTRIBUTED_QUEUE_MODE and self.MAX_ACCOUNTS_PER_WORKER <= 0:
            warnings.append(
                "DISTRIBUTED_QUEUE_MODE=true, но MAX_ACCOUNTS_PER_WORKER<=0. "
                "Worker не сможет claim-ить аккаунты."
            )
        if self.DISTRIBUTED_QUEUE_MODE and self.ENABLE_LEGACY_COMMENTING:
            warnings.append(
                "DISTRIBUTED_QUEUE_MODE=true: legacy-комментинг игнорируется. "
                "Используется только engine distributed path."
            )
        launch_mode = (self.NEW_ACCOUNT_LAUNCH_MODE or "").strip().lower()
        if launch_mode not in {"conservative", "faster_1d"}:
            warnings.append(
                f"NEW_ACCOUNT_LAUNCH_MODE={self.NEW_ACCOUNT_LAUNCH_MODE!r} не поддерживается. "
                "Используйте conservative|faster_1d."
            )
        if self.MIN_EXISTING_COMMENTS_BEFORE_COMMENT < 0:
            warnings.append("MIN_EXISTING_COMMENTS_BEFORE_COMMENT не может быть отрицательным")
        if self.MIN_COMMENTS_RECHECK_MAX_ATTEMPTS < 1:
            warnings.append("MIN_COMMENTS_RECHECK_MAX_ATTEMPTS должен быть >= 1")
        compliance_mode = (self.COMPLIANCE_MODE or "").strip().lower()
        if compliance_mode not in {"off", "warn", "strict"}:
            warnings.append(
                f"COMPLIANCE_MODE={self.COMPLIANCE_MODE!r} не поддерживается. "
                "Используйте off|warn|strict."
            )
        if compliance_mode == "strict" and self.STRICT_PARSER_ONLY and not self.PARSER_ONLY_PHONE:
            warnings.append(
                "STRICT_PARSER_ONLY=true, но PARSER_ONLY_PHONE не задан. "
                "Парсер будет блокироваться до назначения parser-only аккаунта."
            )
        if compliance_mode == "strict" and self.ENABLE_EMOJI_SWAP:
            warnings.append(
                "ENABLE_EMOJI_SWAP=true в strict режиме. "
                "Используйте только как emergency-опцию админа."
            )
        policy_path = BASE_DIR / self.POLICY_RULES_PATH
        if compliance_mode in {"warn", "strict"} and not policy_path.exists():
            warnings.append(
                f"Файл policy rules не найден: {policy_path}. "
                "Compliance engine будет работать с fallback-правилами."
            )
        if self.STRICT_SLO_WINDOW_DAYS < 7:
            warnings.append("STRICT_SLO_WINDOW_DAYS слишком мал (<7)")
        if self.AUTO_SPAMBOT_APPEAL_ENABLED and not self.AUTO_SPAMBOT_APPEAL_EMAIL:
            warnings.append(
                "AUTO_SPAMBOT_APPEAL_ENABLED=true, но AUTO_SPAMBOT_APPEAL_EMAIL пуст. "
                "Будет использован последний email из диалога SpamBot (если найден)."
            )
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
