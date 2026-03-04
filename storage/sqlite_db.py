"""
Dual-mode database — SQLite (dev/single) or PostgreSQL (production/distributed).

Reads DATABASE_URL from config:
  - If set and contains "postgresql" -> asyncpg with connection pool
  - Otherwise -> SQLite with aiosqlite, NullPool, WAL mode

Public API (unchanged):
  - async_session   — async sessionmaker
  - init_db()       — create tables + migrate
  - dispose_engine() — graceful shutdown
"""

import logging
from sqlalchemy import event, text, inspect
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool, AsyncAdaptedQueuePool

from config import settings
from storage.models import Base


log = logging.getLogger(__name__)

_db_url = settings.db_url
_is_postgres = "postgresql" in _db_url


def _build_engine():
    """Create the appropriate engine based on the database URL."""
    if _is_postgres:
        log.info("Database: PostgreSQL mode (asyncpg + pool)")
        return create_async_engine(
            _db_url,
            echo=False,
            poolclass=AsyncAdaptedQueuePool,
            pool_size=20,
            max_overflow=10,
        )
    else:
        log.info("Database: SQLite mode (aiosqlite + NullPool)")
        return create_async_engine(
            _db_url,
            echo=False,
            poolclass=NullPool,
            connect_args={"check_same_thread": False},
        )


engine = _build_engine()
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


# --- SQLite pragmas (only for SQLite) ---

if not _is_postgres:
    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, connection_record):
        """Enable WAL mode and SQLite optimizations on each new connection."""
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()


# --- Migration helpers (SQLite only) ---

def _get_table_columns(inspector, table_name: str) -> set[str]:
    """Get column names for a table."""
    try:
        return {c["name"] for c in inspector.get_columns(table_name)}
    except Exception:
        return set()


def _migrate_existing_db(connection):
    """Add new columns/tables to existing DB without losing data.
    Only runs for SQLite — PostgreSQL uses Alembic migrations.
    """
    inspector = inspect(connection)
    existing_tables = set(inspector.get_table_names())

    # If users table doesn't exist — this is an existing pre-multitenancy DB
    if "users" not in existing_tables:
        log.info("Migration: creating 'users' table")
        # Create users table from ORM model
        Base.metadata.tables["users"].create(connection, checkfirst=True)

        # Create default admin user from existing ADMIN_TELEGRAM_ID
        admin_id = settings.ADMIN_TELEGRAM_ID
        if admin_id:
            connection.execute(text(
                "INSERT OR IGNORE INTO users (telegram_id, username, first_name, is_active, is_admin, "
                "product_name, product_bot_link, product_bot_username, product_avatar_path, "
                "product_short_desc, product_features, product_category, product_channel_prefix, "
                "scenario_b_ratio, max_daily_comments, min_delay, max_delay, max_accounts) "
                "VALUES (:tid, 'admin', 'Admin', 1, 1, "
                ":pname, :plink, :puser, :pavatar, :pdesc, :pfeat, :pcat, :pprefix, "
                ":sb_ratio, :max_daily, :min_d, :max_d, 50)"
            ), {
                "tid": admin_id,
                "pname": settings.PRODUCT_NAME,
                "plink": settings.PRODUCT_BOT_LINK,
                "puser": settings.PRODUCT_BOT_USERNAME,
                "pavatar": settings.PRODUCT_AVATAR_PATH,
                "pdesc": settings.PRODUCT_SHORT_DESC,
                "pfeat": settings.PRODUCT_FEATURES,
                "pcat": settings.PRODUCT_CATEGORY,
                "pprefix": settings.PRODUCT_CHANNEL_PREFIX,
                "sb_ratio": settings.SCENARIO_B_RATIO,
                "max_daily": settings.MAX_COMMENTS_PER_ACCOUNT_PER_DAY,
                "min_d": settings.MIN_DELAY_BETWEEN_COMMENTS_SEC,
                "max_d": settings.MAX_DELAY_BETWEEN_COMMENTS_SEC,
            })
            log.info(f"Migration: created admin user for telegram_id={admin_id}")

    # Add user_id column to existing tables if missing
    migrations = [
        ("accounts", "user_id", "INTEGER DEFAULT 1"),
        ("proxies", "user_id", "INTEGER DEFAULT 1"),
        ("channels", "user_id", "INTEGER DEFAULT 1"),
        # Session health columns
        ("accounts", "api_id", "INTEGER"),
        ("accounts", "health_status", "VARCHAR(20) DEFAULT 'unknown'"),
        ("accounts", "last_health_check", "DATETIME"),
        ("accounts", "session_backup_at", "DATETIME"),
        ("accounts", "account_age_days", "INTEGER DEFAULT 0"),
        # Lifecycle stage for account pipeline tracking
        ("accounts", "lifecycle_stage", "VARCHAR(20) DEFAULT 'uploaded'"),
    ]
    for table, col, col_type in migrations:
        if table in existing_tables:
            cols = _get_table_columns(inspector, table)
            if col not in cols:
                log.info(f"Migration: adding {col} to {table}")
                connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}"))


async def init_db():
    """Create all tables + migrate existing DB (SQLite only)."""
    async with engine.begin() as conn:
        if not _is_postgres:
            # SQLite: run inline migrations
            await conn.run_sync(_migrate_existing_db)
        # Create any missing tables from ORM models
        await conn.run_sync(Base.metadata.create_all)


async def dispose_engine():
    """Gracefully close the engine on application shutdown."""
    await engine.dispose()
