"""
SQLite база данных — инициализация и сессии.
"""

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.pool import StaticPool

from config import settings
from storage.models import Base


# SQLite + aiosqlite: используем StaticPool для единого соединения
# (SQLite не поддерживает настоящий пул, NullPool вызывает проблемы с async)
engine = create_async_engine(
    settings.db_url,
    echo=False,
    poolclass=StaticPool,
    connect_args={"check_same_thread": False},
)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db():
    """Создать все таблицы."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def dispose_engine():
    """Корректно закрыть engine при остановке приложения."""
    await engine.dispose()


