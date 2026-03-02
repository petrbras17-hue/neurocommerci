"""
SQLite база данных — инициализация и сессии.
WAL mode + NullPool для корректной работы при конкурентных async-задачах.
"""

from sqlalchemy import event
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool

from config import settings
from storage.models import Base


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


async def init_db():
    """Создать все таблицы."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def dispose_engine():
    """Корректно закрыть engine при остановке приложения."""
    await engine.dispose()
