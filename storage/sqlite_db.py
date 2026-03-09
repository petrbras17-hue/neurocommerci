from __future__ import annotations

"""
Dual-mode database — SQLite (dev/single) or PostgreSQL (production/distributed).

Reads DATABASE_URL from config:
  - If set and contains "postgresql" -> asyncpg with connection pool
  - Otherwise -> SQLite with aiosqlite, NullPool, WAL mode

Public API (unchanged):
  - async_session   — async sessionmaker
  - init_db()       — create tables + inline migrate
  - dispose_engine() — graceful shutdown
"""

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
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


def _alembic_config() -> Config:
    cfg = Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parent.parent / "alembic"))
    cfg.set_main_option("sqlalchemy.url", settings.db_url.replace("+asyncpg", "+psycopg2"))
    return cfg


def _run_postgres_migrations() -> None:
    """Apply Alembic migrations in PostgreSQL mode before app startup."""
    if not _is_postgres:
        return
    try:
        command.upgrade(_alembic_config(), "head")
    except Exception as exc:
        log.error(f"Alembic upgrade failed: {exc}")
        raise


async def apply_session_rls_context(session: AsyncSession, tenant_id: int, user_id: int | None = None) -> None:
    """Bind PostgreSQL RLS settings with SET LOCAL semantics inside an active transaction."""
    if not _is_postgres:
        return
    if not session.in_transaction():
        raise RuntimeError("tenant_rls_context_requires_active_transaction")
    await session.execute(
        text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
        {"tenant_id": str(int(tenant_id))},
    )
    if user_id is not None:
        await session.execute(
            text("SELECT set_config('app.user_id', :user_id, true)"),
            {"user_id": str(int(user_id))},
        )


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


def _add_column_if_missing(connection, table: str, col: str, sqlite_type: str, pg_type: str):
    """Add column when missing for SQLite/PostgreSQL."""
    cols = _get_table_columns(inspect(connection), table)
    if col in cols:
        return

    dialect = connection.dialect.name
    col_type = pg_type if dialect == "postgresql" else sqlite_type
    if dialect == "postgresql":
        sql = f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {col_type}"
    else:
        sql = f"ALTER TABLE {table} ADD COLUMN {col} {col_type}"
    log.info(f"Migration: adding {col} to {table} ({dialect})")
    connection.execute(text(sql))


def _sqlite_channel_unique_indexes(connection) -> list[tuple[str, tuple[str, ...]]]:
    rows = connection.execute(text("PRAGMA index_list('channels')")).fetchall()
    indexes: list[tuple[str, tuple[str, ...]]] = []
    for row in rows:
        # SQLite row layout: seq, name, unique, origin, partial
        if not int(row[2] or 0):
            continue
        index_name = str(row[1])
        info_rows = connection.execute(text(f"PRAGMA index_info('{index_name}')")).fetchall()
        columns = tuple(str(info[2]) for info in info_rows if len(info) >= 3)
        indexes.append((index_name, columns))
    return indexes


def _channels_has_tenant_identity(connection) -> bool:
    dialect = connection.dialect.name
    if dialect == "sqlite":
        indexes = _sqlite_channel_unique_indexes(connection)
        return any(columns == ("user_id", "telegram_id") for _name, columns in indexes)

    inspector = inspect(connection)
    unique_constraints = inspector.get_unique_constraints("channels")
    return any(
        tuple((item.get("column_names") or [])) == ("user_id", "telegram_id")
        for item in unique_constraints
    )


def _rebuild_sqlite_channels_table(connection):
    log.info("Migration: rebuilding channels table for tenant-scoped identity (sqlite)")
    connection.execute(text("PRAGMA foreign_keys=OFF"))
    try:
        connection.execute(text("DROP TABLE IF EXISTS channels__new"))
        connection.execute(
            text(
                """
                CREATE TABLE channels__new (
                    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    telegram_id BIGINT NOT NULL,
                    username VARCHAR(255),
                    title VARCHAR(500) NOT NULL,
                    subscribers INTEGER DEFAULT 0,
                    topic VARCHAR(100),
                    comments_enabled BOOLEAN DEFAULT 1,
                    discussion_group_id BIGINT,
                    review_state VARCHAR(20) DEFAULT 'discovered',
                    publish_mode VARCHAR(20) DEFAULT 'research_only',
                    permission_basis VARCHAR(32) DEFAULT '',
                    review_note VARCHAR(500),
                    is_active BOOLEAN DEFAULT 1,
                    is_blacklisted BOOLEAN DEFAULT 0,
                    last_post_checked INTEGER DEFAULT 0,
                    last_checked_at DATETIME,
                    created_at DATETIME,
                    CONSTRAINT uq_channels_user_telegram UNIQUE (user_id, telegram_id),
                    FOREIGN KEY(user_id) REFERENCES users (id)
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO channels__new (
                    id, user_id, telegram_id, username, title, subscribers, topic,
                    comments_enabled, discussion_group_id, review_state, publish_mode,
                    permission_basis, review_note, is_active, is_blacklisted,
                    last_post_checked, last_checked_at, created_at
                )
                SELECT
                    id,
                    user_id,
                    telegram_id,
                    username,
                    title,
                    subscribers,
                    topic,
                    comments_enabled,
                    discussion_group_id,
                    COALESCE(review_state, 'discovered'),
                    COALESCE(publish_mode, 'research_only'),
                    COALESCE(permission_basis, ''),
                    review_note,
                    COALESCE(is_active, 1),
                    COALESCE(is_blacklisted, 0),
                    COALESCE(last_post_checked, 0),
                    last_checked_at,
                    created_at
                FROM channels
                """
            )
        )
        connection.execute(text("DROP TABLE channels"))
        connection.execute(text("ALTER TABLE channels__new RENAME TO channels"))
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_channels_user_id ON channels (user_id)"
            )
        )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_channels_user_telegram_id "
                "ON channels (user_id, telegram_id)"
            )
        )
    finally:
        connection.execute(text("PRAGMA foreign_keys=ON"))


def _migrate_channels_tenant_identity(connection):
    """Move channels identity from global telegram_id uniqueness to per-user uniqueness."""
    inspector = inspect(connection)
    existing_tables = set(inspector.get_table_names())
    if "channels" not in existing_tables:
        return
    if _channels_has_tenant_identity(connection):
        return

    dialect = connection.dialect.name
    if dialect == "sqlite":
        _rebuild_sqlite_channels_table(connection)
        return

    log.info("Migration: updating channels unique constraints for tenant-scoped identity (postgresql)")
    for constraint in inspector.get_unique_constraints("channels"):
        cols = tuple(constraint.get("column_names") or [])
        name = constraint.get("name")
        if cols == ("telegram_id",) and name:
            connection.execute(text(f'ALTER TABLE channels DROP CONSTRAINT IF EXISTS "{name}"'))
    for index in inspector.get_indexes("channels"):
        cols = tuple(index.get("column_names") or [])
        name = index.get("name")
        if cols == ("telegram_id",) and index.get("unique") and name:
            connection.execute(text(f'DROP INDEX IF EXISTS "{name}"'))
    if not _channels_has_tenant_identity(connection):
        connection.execute(
            text(
                "ALTER TABLE channels "
                "ADD CONSTRAINT uq_channels_user_telegram UNIQUE (user_id, telegram_id)"
            )
        )


def _migrate_existing_db(connection):
    """Add new columns/tables to existing DB without losing data."""
    inspector = inspect(connection)
    existing_tables = set(inspector.get_table_names())
    dialect = connection.dialect.name

    # If users table doesn't exist — this is an existing pre-multitenancy DB
    if "users" not in existing_tables:
        log.info("Migration: creating 'users' table")
        # Create users table from ORM model
        Base.metadata.tables["users"].create(connection, checkfirst=True)

        # Create default admin user from existing ADMIN_TELEGRAM_ID
        admin_id = settings.ADMIN_TELEGRAM_ID
        if admin_id:
            if dialect == "sqlite":
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
            else:
                connection.execute(text(
                    "INSERT INTO users (telegram_id, username, first_name, is_active, is_admin, "
                    "product_name, product_bot_link, product_bot_username, product_avatar_path, "
                    "product_short_desc, product_features, product_category, product_channel_prefix, "
                    "scenario_b_ratio, max_daily_comments, min_delay, max_delay, max_accounts) "
                    "VALUES (:tid, 'admin', 'Admin', true, true, "
                    ":pname, :plink, :puser, :pavatar, :pdesc, :pfeat, :pcat, :pprefix, "
                    ":sb_ratio, :max_daily, :min_d, :max_d, 50) "
                    "ON CONFLICT (telegram_id) DO NOTHING"
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
            log.info(f"Migration: ensured admin user telegram_id={admin_id}")

    # Add columns to existing tables if missing.
    migrations = [
        ("accounts", "user_id", "INTEGER DEFAULT 1", "INTEGER DEFAULT 1"),
        ("proxies", "user_id", "INTEGER DEFAULT 1", "INTEGER DEFAULT 1"),
        ("proxies", "health_status", "VARCHAR(20) DEFAULT 'unknown'", "VARCHAR(20) DEFAULT 'unknown'"),
        ("proxies", "consecutive_failures", "INTEGER DEFAULT 0", "INTEGER DEFAULT 0"),
        ("proxies", "last_error", "VARCHAR(255)", "VARCHAR(255)"),
        ("channels", "user_id", "INTEGER DEFAULT 1", "INTEGER DEFAULT 1"),
        ("proxies", "last_success_at", "DATETIME", "TIMESTAMP"),
        ("proxies", "invalidated_at", "DATETIME", "TIMESTAMP"),
        # Session health columns
        ("accounts", "api_id", "INTEGER", "INTEGER"),
        ("accounts", "health_status", "VARCHAR(20) DEFAULT 'unknown'", "VARCHAR(20) DEFAULT 'unknown'"),
        ("accounts", "last_health_check", "DATETIME", "TIMESTAMP"),
        ("accounts", "session_backup_at", "DATETIME", "TIMESTAMP"),
        ("accounts", "account_age_days", "INTEGER DEFAULT 0", "INTEGER DEFAULT 0"),
        # Lifecycle stage for account pipeline tracking
        ("accounts", "lifecycle_stage", "VARCHAR(20) DEFAULT 'uploaded'", "VARCHAR(20) DEFAULT 'uploaded'"),
        ("accounts", "account_role", "VARCHAR(32) DEFAULT 'comment_candidate'", "VARCHAR(32) DEFAULT 'comment_candidate'"),
        # Compliance/risk fields
        ("accounts", "risk_score", "FLOAT DEFAULT 0", "DOUBLE PRECISION DEFAULT 0"),
        ("accounts", "risk_level", "VARCHAR(20) DEFAULT 'low'", "VARCHAR(20) DEFAULT 'low'"),
        ("accounts", "last_violation_at", "DATETIME", "TIMESTAMP"),
        ("accounts", "violation_count_24h", "INTEGER DEFAULT 0", "INTEGER DEFAULT 0"),
        ("accounts", "quarantined_until", "DATETIME", "TIMESTAMP"),
        ("accounts", "restriction_reason", "VARCHAR(64)", "VARCHAR(64)"),
        ("accounts", "last_probe_at", "DATETIME", "TIMESTAMP"),
        ("accounts", "capabilities_json", "TEXT", "TEXT"),
        # Channel review / publish pipeline
        ("channels", "review_state", "VARCHAR(20) DEFAULT 'discovered'", "VARCHAR(20) DEFAULT 'discovered'"),
        ("channels", "publish_mode", "VARCHAR(20) DEFAULT 'research_only'", "VARCHAR(20) DEFAULT 'research_only'"),
        ("channels", "permission_basis", "VARCHAR(32) DEFAULT ''", "VARCHAR(32) DEFAULT ''"),
        ("channels", "review_note", "VARCHAR(500)", "VARCHAR(500)"),
    ]
    for table, col, sqlite_type, pg_type in migrations:
        if table in existing_tables:
            _add_column_if_missing(connection, table, col, sqlite_type, pg_type)

    _migrate_channels_tenant_identity(connection)


async def init_db():
    """Create all tables + migrate existing DB (SQLite/PostgreSQL)."""
    if _is_postgres:
        _run_postgres_migrations()
    async with engine.begin() as conn:
        # Run inline migrations for both SQLite and PostgreSQL.
        await conn.run_sync(_migrate_existing_db)
        # Create any missing tables from ORM models
        await conn.run_sync(Base.metadata.create_all)


async def dispose_engine():
    """Gracefully close the engine on application shutdown."""
    await engine.dispose()
