#!/usr/bin/env python3
"""
Синхронизация channel_map_entries из PostgreSQL в Meilisearch.

Читает все записи из таблицы channel_map_entries и загружает их в поисковый
индекс Meilisearch. Поддерживает --dry-run для проверки без записи.

Использование:
    python scripts/sync_channels_to_meili.py [--batch-size 1000] [--dry-run]

Env vars:
    DATABASE_URL     — строка подключения к PostgreSQL (из .env)
    MEILI_URL        — адрес Meilisearch (default: http://localhost:7700)
    MEILI_MASTER_KEY — мастер-ключ Meilisearch (опционально)
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path
from typing import List

# Добавляем корень проекта в sys.path для импорта core/storage
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()


def _patch_db_url(url: str) -> str:
    """Приводит DATABASE_URL к asyncpg-формату если нужно."""
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


async def fetch_all_channels(session_factory: any) -> List[dict]:
    """
    Читает все записи channel_map_entries из PostgreSQL.
    Возвращает список dict-документов, готовых для индексации.
    """
    from sqlalchemy import select
    from core.search_service import channel_to_doc

    # Импортируем модель здесь, чтобы sys.path был уже настроен
    from storage.models import ChannelMapEntry

    docs: List[dict] = []
    async with session_factory() as session:
        # Читаем все записи без RLS-фильтра — скрипт выполняется с правами оператора
        result = await session.execute(
            select(ChannelMapEntry).order_by(ChannelMapEntry.id)
        )
        rows = result.scalars().all()
        for row in rows:
            docs.append(channel_to_doc(row))

    return docs


async def run_sync(
    batch_size: int = 1000,
    dry_run: bool = False,
    db_url: str = "",
    meili_url: str = "",
    meili_key: str = "",
) -> None:
    """
    Основная логика синхронизации.

    1. Читает все каналы из PostgreSQL
    2. Трансформирует в Meilisearch-документы
    3. Загружает батчами в индекс
    4. Выводит итоговую статистику
    """
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker

    from core.search_service import MeiliSearchService

    start_time = time.monotonic()

    # ── Настройка DB ─────────────────────────────────────────────────────────
    if not db_url:
        print("[ERROR] DATABASE_URL не задан")
        sys.exit(1)

    db_url = _patch_db_url(db_url)
    engine = create_async_engine(db_url, echo=False, pool_pre_ping=True)
    SessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # ── Читаем каналы из БД ───────────────────────────────────────────────────
    print("Читаем каналы из PostgreSQL...")
    try:
        docs = await fetch_all_channels(SessionLocal)
    except Exception as exc:
        print(f"[ERROR] Не удалось прочитать каналы из БД: {exc}")
        raise
    finally:
        await engine.dispose()

    total_channels = len(docs)
    print(f"Найдено каналов: {total_channels}")

    if total_channels == 0:
        print("Каналов нет — нечего синхронизировать.")
        return

    # ── Dry-run: только показать первые 5 документов ─────────────────────────
    if dry_run:
        print("\n[DRY-RUN] Первые 5 документов:")
        for doc in docs[:5]:
            print(f"  id={doc['id']} title={doc.get('title')!r} "
                  f"category={doc.get('category')!r} "
                  f"members={doc.get('member_count')} "
                  f"geo={doc.get('_geo')}")
        print(f"\n[DRY-RUN] Всего документов: {total_channels}. Индексация пропущена.")
        return

    # ── Настройка Meilisearch ────────────────────────────────────────────────
    svc = MeiliSearchService(url=meili_url or None, master_key=meili_key or None)

    print(f"Проверяем доступность Meilisearch ({svc._url})...")
    if not await svc.health():
        print("[ERROR] Meilisearch недоступен. Проверьте MEILI_URL и запущен ли сервис.")
        await svc.close()
        sys.exit(1)
    print("Meilisearch: OK")

    # ── Создаём/обновляем индекс ──────────────────────────────────────────────
    print("Настраиваем индекс каналов...")
    await svc.setup_index()

    # ── Батч-индексация ───────────────────────────────────────────────────────
    print(f"Загружаем {total_channels} документов (batch_size={batch_size})...")
    synced = 0
    for i in range(0, total_channels, batch_size):
        chunk = docs[i : i + batch_size]
        try:
            await svc.bulk_index(chunk)
            synced += len(chunk)
            pct = synced / total_channels * 100
            print(f"  [{synced}/{total_channels}] {pct:.1f}% — батч отправлен")
        except Exception as exc:
            print(f"[ERROR] Ошибка при загрузке батча {i}-{i + len(chunk)}: {exc}")
            raise

    # ── Итоговая статистика ───────────────────────────────────────────────────
    elapsed = time.monotonic() - start_time
    print(f"\nСинхронизация завершена за {elapsed:.1f}с")
    print(f"  Загружено документов: {synced}")

    try:
        stats = await svc.get_index_stats()
        print(f"  Документов в индексе: {stats.get('numberOfDocuments', '?')}")
        print(f"  Статус индексации:    {stats.get('isIndexing', False)}")
    except Exception as exc:
        print(f"  [WARN] Не удалось получить статистику индекса: {exc}")

    await svc.close()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Синхронизация channel_map_entries из PostgreSQL в Meilisearch"
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=1000,
        metavar="N",
        help="Количество документов в одном батче (default: 1000)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Показать документы без загрузки в Meilisearch",
    )
    parser.add_argument(
        "--meili-url",
        default="",
        help="URL Meilisearch (переопределяет MEILI_URL из .env)",
    )
    parser.add_argument(
        "--meili-key",
        default="",
        help="Мастер-ключ Meilisearch (переопределяет MEILI_MASTER_KEY из .env)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    db_url = os.getenv("DATABASE_URL", "")
    meili_url = args.meili_url or os.getenv("MEILI_URL", "http://localhost:7700")
    meili_key = args.meili_key or os.getenv("MEILI_MASTER_KEY", "")

    if not db_url and not args.dry_run:
        print("[ERROR] DATABASE_URL не задан. Добавьте в .env или передайте через переменную окружения.")
        sys.exit(1)

    asyncio.run(
        run_sync(
            batch_size=args.batch_size,
            dry_run=args.dry_run,
            db_url=db_url,
            meili_url=meili_url,
            meili_key=meili_key,
        )
    )


if __name__ == "__main__":
    main()
