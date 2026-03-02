"""
SQLite база данных — инициализация и сессии.
"""

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

from config import settings
from storage.models import Base


engine = create_async_engine(settings.db_url, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db():
    """Создать все таблицы."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


