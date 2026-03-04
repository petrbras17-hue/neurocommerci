"""
SQLite база данных — инициализация и сессии.
WAL mode + NullPool для корректной работы при конкурентных async-задачах.
"""

import logging
from sqlalchemy import event, text, inspect
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool

from config import settings
from storage.models import Base


log = logging.getLogger(__name__)

engine = create_async_engine(
    settings.db_url,
    echo=False,
    poolclass=NullPool,
    connect_args={"check_same_thread": False},
)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragma(dbapi_conn, connection_record):
    """Включить WAL mode и оптимизации SQLite при каждом новом соединении."""
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


def _get_table_columns(inspector, table_name: str) -> set[str]:
    """Get column names for a table."""
    try:
        return {c["name"] for c in inspector.get_columns(table_name)}
    except Exception:
        return set()


def _migrate_existing_db(connection):
    """Add new columns/tables to existing DB without losing data."""
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
    ]
    for table, col, col_type in migrations:
        if table in existing_tables:
            cols = _get_table_columns(inspector, table)
            if col not in cols:
                log.info(f"Migration: adding {col} to {table}")
                connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}"))


async def init_db():
    """Создать все таблицы + мигрировать существующую БД."""
    async with engine.begin() as conn:
        await conn.run_sync(_migrate_existing_db)
        await conn.run_sync(Base.metadata.create_all)


async def dispose_engine():
    """Корректно закрыть engine при остановке приложения."""
    await engine.dispose()
