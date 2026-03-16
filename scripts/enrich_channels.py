"""
Channel enrichment script — обогащает channel_map_entries аватарками и последними постами.

Источники данных:
  --source tgstat    (по умолчанию) — TGStat API, не требует Telegram-сессии
  --source telethon  — Telethon GetFullChannelRequest + GetHistoryRequest

Аватарки сохраняются в:  storage/channel_avatars/{channel_id}.jpg
URL в БД:               /static/channel-avatars/{channel_id}.jpg

Монтирование в ops_api.py (добавить при необходимости):
  from fastapi.staticfiles import StaticFiles
  CHANNEL_AVATARS_DIR = BASE_DIR / "storage" / "channel_avatars"
  CHANNEL_AVATARS_DIR.mkdir(parents=True, exist_ok=True)
  app.mount(
      "/static/channel-avatars",
      StaticFiles(directory=str(CHANNEL_AVATARS_DIR)),
      name="channel-avatars",
  )

Примеры использования:
  python scripts/enrich_channels.py --batch-size 50 --delay 5
  python scripts/enrich_channels.py --dry-run
  python scripts/enrich_channels.py --source tgstat --only-missing-avatars
  python scripts/enrich_channels.py --source telethon --only-missing-posts

БЕЗОПАСНОСТЬ:
  - НЕ использует аккаунт 77076294082 (KZ прогрев). Telethon-режим использует
    только аккаунты, полученные через SessionManager (legacy pool), исключая
    явно указанный KZ-номер.
  - По умолчанию используется TGSTAT_API_TOKEN (никаких сессий не нужно).
  - Rate limit: 1 запрос / --delay секунд.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import List, Optional, Tuple

# Проект в sys.path при запуске из любого места.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import aiohttp
from sqlalchemy import select, or_

from storage.models import ChannelMapEntry
from storage.sqlite_db import init_db, async_session
from utils.helpers import utcnow
from utils.logger import log


# ---------------------------------------------------------------------------
# Константы
# ---------------------------------------------------------------------------

# Аккаунт на прогреве — НИКОГДА не использовать для обогащения.
_KZ_WARMUP_PHONE = "77076294082"

# TGStat API
_TGSTAT_BASE = "https://api.tgstat.ru"
_TGSTAT_STAT_URL = _TGSTAT_BASE + "/channels/stat"
_TGSTAT_POSTS_URL = _TGSTAT_BASE + "/channels/posts"

# Лимит текста последнего поста
_MAX_POST_TEXT = 500

# Директория аватарок
_AVATARS_DIR = _ROOT / "storage" / "channel_avatars"


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------

def _avatar_disk_path(channel_id: int) -> Path:
    return _AVATARS_DIR / f"{channel_id}.jpg"


def _avatar_url(channel_id: int) -> str:
    return f"/static/channel-avatars/{channel_id}.jpg"


def _truncate(text: Optional[str], limit: int = _MAX_POST_TEXT) -> Optional[str]:
    if not text:
        return None
    return text[:limit].strip() or None


# ---------------------------------------------------------------------------
# TGStat-источник
# ---------------------------------------------------------------------------

async def _tgstat_enrich_one(
    session_http: aiohttp.ClientSession,
    entry: ChannelMapEntry,
    token: str,
    dry_run: bool,
    skip_avatar: bool,
    skip_post: bool,
) -> Tuple[bool, bool]:
    """
    Обогащает одну запись через TGStat API.

    Возвращает (avatar_updated, post_updated).
    """
    identifier = f"@{entry.username}" if entry.username else str(entry.telegram_id or entry.id)

    avatar_updated = False
    post_updated = False

    # --- аватарка (из поля stat.photo) ---
    if not skip_avatar:
        try:
            params = {"token": token, "peer": identifier}
            async with session_http.get(
                _TGSTAT_STAT_URL, params=params, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("status") == "ok":
                        channel_data = data.get("response", {})
                        photo_url = channel_data.get("photo", None)
                        if photo_url and not dry_run:
                            # Скачиваем и сохраняем локально
                            saved = await _download_avatar(session_http, photo_url, entry.id)
                            if saved:
                                entry.avatar_url = _avatar_url(entry.id)
                                avatar_updated = True
                                log.debug(
                                    "enrich_channels: avatar saved for id=%d (%s)",
                                    entry.id, identifier,
                                )
                        elif photo_url and dry_run:
                            log.info(
                                "[dry-run] id=%d: avatar_url would be set from TGStat photo",
                                entry.id,
                            )
                            avatar_updated = True  # считаем потенциальным обновлением
                    else:
                        err = data.get("error", "unknown")
                        log.warning(
                            "enrich_channels: TGStat stat error for %s: %s", identifier, err
                        )
                else:
                    log.warning(
                        "enrich_channels: TGStat stat HTTP %d for %s", resp.status, identifier
                    )
        except Exception as exc:
            log.warning("enrich_channels: TGStat stat failed for %s: %s", identifier, exc)

    # --- последний пост (из /channels/posts) ---
    if not skip_post:
        try:
            params = {
                "token": token,
                "channelId": identifier,
                "limit": 1,
            }
            async with session_http.get(
                _TGSTAT_POSTS_URL, params=params, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("status") == "ok":
                        items = data.get("response", {}).get("items", [])
                        if items:
                            post = items[0]
                            text = _truncate(post.get("text") or post.get("title"))
                            views = post.get("viewsCount") or post.get("views")
                            post_date_ts = post.get("date")
                            from datetime import datetime, timezone
                            post_date = (
                                datetime.fromtimestamp(post_date_ts, tz=timezone.utc)
                                if post_date_ts
                                else None
                            )
                            # Определяем тип медиа
                            media_type = "none"
                            if post.get("hasVideo"):
                                media_type = "video"
                            elif post.get("hasPhoto"):
                                media_type = "photo"
                            elif post.get("hasDocument"):
                                media_type = "document"

                            if not dry_run:
                                entry.last_post_content = text
                                entry.last_post_date = post_date
                                entry.last_post_views = int(views) if views else None
                                entry.last_post_media_type = media_type
                                post_updated = True
                            else:
                                log.info(
                                    "[dry-run] id=%d: last_post would be set (%d chars, %s views, media=%s)",
                                    entry.id,
                                    len(text or ""),
                                    views,
                                    media_type,
                                )
                                post_updated = True
                    else:
                        err = data.get("error", "unknown")
                        log.warning(
                            "enrich_channels: TGStat posts error for %s: %s", identifier, err
                        )
                else:
                    log.warning(
                        "enrich_channels: TGStat posts HTTP %d for %s", resp.status, identifier
                    )
        except Exception as exc:
            log.warning("enrich_channels: TGStat posts failed for %s: %s", identifier, exc)

    return avatar_updated, post_updated


# ---------------------------------------------------------------------------
# Telethon-источник
# ---------------------------------------------------------------------------

async def _get_telethon_client():
    """
    Возвращает первый подходящий Telethon-клиент из legacy SessionManager,
    исключая аккаунт KZ на прогреве.
    """
    try:
        from core.session_manager import SessionManager
        sm = SessionManager()
        phones = sm.get_connected_phones() if hasattr(sm, "get_connected_phones") else []
        for phone in phones:
            if str(phone).strip() == _KZ_WARMUP_PHONE:
                log.info(
                    "enrich_channels: пропускаем KZ прогрев-аккаунт %s", phone
                )
                continue
            client = sm.get_client(phone)
            if client and client.is_connected():
                log.info("enrich_channels: используем Telethon-клиент phone=%s", phone)
                return client
    except Exception as exc:
        log.warning("enrich_channels: не удалось получить Telethon-клиент: %s", exc)
    return None


async def _telethon_enrich_one(
    client,
    entry: ChannelMapEntry,
    dry_run: bool,
    skip_avatar: bool,
    skip_post: bool,
) -> Tuple[bool, bool]:
    """
    Обогащает одну запись через Telethon.

    Возвращает (avatar_updated, post_updated).
    """
    try:
        from telethon import functions as tl_functions
        from telethon.tl.types import Channel as TLChannel
    except ImportError:
        log.warning("enrich_channels: telethon не установлен")
        return False, False

    identifier = entry.username or (
        str(entry.telegram_id) if entry.telegram_id else None
    )
    if not identifier:
        return False, False

    avatar_updated = False
    post_updated = False

    try:
        entity = await client.get_entity(identifier)
    except Exception as exc:
        log.warning(
            "enrich_channels: get_entity '%s' (id=%d) failed: %s",
            identifier, entry.id, exc,
        )
        return False, False

    if not isinstance(entity, TLChannel):
        return False, False

    # --- аватарка ---
    if not skip_avatar:
        try:
            full = await client(tl_functions.channels.GetFullChannelRequest(channel=entity))
            chat_photo = getattr(entity, "photo", None)
            if chat_photo and hasattr(chat_photo, "photo_id"):
                if not dry_run:
                    _AVATARS_DIR.mkdir(parents=True, exist_ok=True)
                    disk_path = _avatar_disk_path(entry.id)
                    await client.download_profile_photo(
                        entity, file=str(disk_path), download_big=True
                    )
                    if disk_path.exists() and disk_path.stat().st_size > 0:
                        entry.avatar_url = _avatar_url(entry.id)
                        avatar_updated = True
                        log.debug(
                            "enrich_channels: avatar saved for id=%d (%s)",
                            entry.id, identifier,
                        )
                    else:
                        log.warning(
                            "enrich_channels: downloaded avatar is empty for id=%d",
                            entry.id,
                        )
                else:
                    log.info(
                        "[dry-run] id=%d: avatar would be downloaded via Telethon",
                        entry.id,
                    )
                    avatar_updated = True
        except Exception as exc:
            log.warning(
                "enrich_channels: avatar fetch failed for '%s' (id=%d): %s",
                identifier, entry.id, exc,
            )

    # --- последний пост ---
    if not skip_post:
        try:
            from telethon.tl.functions.messages import GetHistoryRequest
            from telethon.tl.types import InputPeerChannel

            messages = await client.get_messages(entity, limit=1)
            if messages:
                msg = messages[0]
                text = _truncate(getattr(msg, "message", None) or getattr(msg, "text", None))
                views = getattr(msg, "views", None)
                date = getattr(msg, "date", None)

                # Тип медиа
                media = getattr(msg, "media", None)
                media_type = "none"
                if media is not None:
                    mtype = type(media).__name__.lower()
                    if "photo" in mtype:
                        media_type = "photo"
                    elif "document" in mtype:
                        media_type = "document"
                    elif "video" in mtype:
                        media_type = "video"
                    elif "web" in mtype:
                        media_type = "webpage"
                    else:
                        media_type = "other"

                if not dry_run:
                    entry.last_post_content = text
                    entry.last_post_date = date
                    entry.last_post_views = int(views) if views is not None else None
                    entry.last_post_media_type = media_type
                    post_updated = True
                else:
                    log.info(
                        "[dry-run] id=%d: last_post would be set (%d chars, views=%s, media=%s)",
                        entry.id, len(text or ""), views, media_type,
                    )
                    post_updated = True
        except Exception as exc:
            log.warning(
                "enrich_channels: post fetch failed for '%s' (id=%d): %s",
                identifier, entry.id, exc,
            )

    return avatar_updated, post_updated


# ---------------------------------------------------------------------------
# Загрузка аватарки по URL (для TGStat)
# ---------------------------------------------------------------------------

async def _download_avatar(
    session_http: aiohttp.ClientSession,
    url: str,
    channel_id: int,
) -> bool:
    """
    Скачивает аватарку по URL и сохраняет на диск.

    Возвращает True при успехе.
    """
    try:
        _AVATARS_DIR.mkdir(parents=True, exist_ok=True)
        disk_path = _avatar_disk_path(channel_id)
        async with session_http.get(url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
            if resp.status != 200:
                log.warning(
                    "enrich_channels: avatar download HTTP %d for channel_id=%d",
                    resp.status, channel_id,
                )
                return False
            content = await resp.read()
            if not content:
                return False
            disk_path.write_bytes(content)
            return True
    except Exception as exc:
        log.warning(
            "enrich_channels: avatar download error channel_id=%d: %s", channel_id, exc
        )
        return False


# ---------------------------------------------------------------------------
# Основная логика обогащения
# ---------------------------------------------------------------------------

async def run_enrichment(
    source: str,
    batch_size: int,
    delay: float,
    dry_run: bool,
    only_missing_avatars: bool,
    only_missing_posts: bool,
    tgstat_token: Optional[str],
) -> None:
    """Основной цикл обогащения каналов."""

    await init_db()

    skip_avatar = False
    skip_post = False

    # Если явно указан фильтр — пропускаем другой тип обогащения
    if only_missing_avatars and not only_missing_posts:
        skip_post = True
    if only_missing_posts and not only_missing_avatars:
        skip_avatar = True

    async with async_session() as db:
        # Строим запрос с учётом фильтров
        query = select(ChannelMapEntry).where(
            ChannelMapEntry.tenant_id.is_(None),
            ChannelMapEntry.username.isnot(None),
        )

        if only_missing_avatars and not only_missing_posts:
            query = query.where(ChannelMapEntry.avatar_url.is_(None))
        elif only_missing_posts and not only_missing_avatars:
            query = query.where(ChannelMapEntry.last_post_content.is_(None))
        elif only_missing_avatars and only_missing_posts:
            query = query.where(
                or_(
                    ChannelMapEntry.avatar_url.is_(None),
                    ChannelMapEntry.last_post_content.is_(None),
                )
            )

        query = query.order_by(ChannelMapEntry.id.asc()).limit(batch_size)

        result = await db.execute(query)
        entries: List[ChannelMapEntry] = list(result.scalars().all())

    if not entries:
        print("Нет каналов для обогащения.")
        return

    print(
        f"Найдено {len(entries)} каналов для обогащения "
        f"(источник: {source}, dry_run: {dry_run})"
    )

    total_avatar = 0
    total_post = 0
    total_errors = 0

    # --- TGStat-режим ---
    if source == "tgstat":
        if not tgstat_token:
            print(
                "[ОШИБКА] Для --source tgstat необходим TGSTAT_API_TOKEN в окружении "
                "или --tgstat-token."
            )
            return

        connector = aiohttp.TCPConnector(limit=5, force_close=True)
        async with aiohttp.ClientSession(connector=connector) as http_sess:
            async with async_session() as db:
                for i, entry in enumerate(entries):
                    if i > 0:
                        await asyncio.sleep(delay)

                    label = entry.username or f"id:{entry.id}"
                    print(
                        f"[{i + 1}/{len(entries)}] TGStat: @{label} (id={entry.id})"
                    )

                    # Вновь загружаем запись в активную сессию для merge
                    db_entry = await db.get(ChannelMapEntry, entry.id)
                    if db_entry is None:
                        continue

                    try:
                        a_upd, p_upd = await _tgstat_enrich_one(
                            http_sess,
                            db_entry,
                            tgstat_token,
                            dry_run=dry_run,
                            skip_avatar=skip_avatar,
                            skip_post=skip_post,
                        )
                        if (a_upd or p_upd) and not dry_run:
                            db_entry.last_refreshed_at = utcnow()
                            await db.commit()
                        if a_upd:
                            total_avatar += 1
                        if p_upd:
                            total_post += 1
                    except Exception as exc:
                        log.warning(
                            "enrich_channels: необработанная ошибка id=%d: %s",
                            entry.id, exc,
                        )
                        total_errors += 1
                        try:
                            await db.rollback()
                        except Exception:
                            pass

    # --- Telethon-режим ---
    elif source == "telethon":
        client = await _get_telethon_client()
        if client is None:
            print(
                "[ОШИБКА] Нет доступного Telethon-клиента (кроме KZ-прогрева). "
                "Добавьте второй аккаунт или используйте --source tgstat."
            )
            return

        async with async_session() as db:
            for i, entry in enumerate(entries):
                if i > 0:
                    await asyncio.sleep(delay)

                label = entry.username or f"id:{entry.id}"
                print(
                    f"[{i + 1}/{len(entries)}] Telethon: @{label} (id={entry.id})"
                )

                db_entry = await db.get(ChannelMapEntry, entry.id)
                if db_entry is None:
                    continue

                try:
                    a_upd, p_upd = await _telethon_enrich_one(
                        client,
                        db_entry,
                        dry_run=dry_run,
                        skip_avatar=skip_avatar,
                        skip_post=skip_post,
                    )
                    if (a_upd or p_upd) and not dry_run:
                        db_entry.last_refreshed_at = utcnow()
                        await db.commit()
                    if a_upd:
                        total_avatar += 1
                    if p_upd:
                        total_post += 1
                except Exception as exc:
                    log.warning(
                        "enrich_channels: необработанная ошибка id=%d: %s",
                        entry.id, exc,
                    )
                    total_errors += 1
                    try:
                        await db.rollback()
                    except Exception:
                        pass

    # --- Итог ---
    print(
        f"\nГотово."
        f"\n  Аватарки обновлено: {total_avatar}"
        f"\n  Посты обновлено:    {total_post}"
        f"\n  Ошибок:             {total_errors}"
        f"\n  dry_run:            {dry_run}"
    )
    if dry_run:
        print("  (dry-run: изменения в БД не сохранены, файлы не скачаны)")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Обогащает channel_map_entries аватарками и последними постами. "
            "По умолчанию использует TGStat API (без Telegram-сессии)."
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    p.add_argument(
        "--batch-size",
        type=int,
        default=50,
        help="Количество каналов за один запуск (default: 50).",
    )
    p.add_argument(
        "--delay",
        type=float,
        default=5.0,
        help="Пауза между запросами в секундах (default: 5).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Показать что будет обновлено, не изменяя БД и не скачивая файлы.",
    )
    p.add_argument(
        "--source",
        choices=["tgstat", "telethon"],
        default="tgstat",
        help=(
            "Источник данных:\n"
            "  tgstat   — TGStat API (требует TGSTAT_API_TOKEN, default)\n"
            "  telethon — Telethon GetFullChannelRequest + GetHistoryRequest\n"
            "             (НЕ использует KZ-аккаунт 77076294082)"
        ),
    )
    p.add_argument(
        "--tgstat-token",
        default=None,
        help="TGStat API token (иначе читается из TGSTAT_API_TOKEN env).",
    )
    p.add_argument(
        "--only-missing-avatars",
        action="store_true",
        help="Обогащать только каналы без avatar_url.",
    )
    p.add_argument(
        "--only-missing-posts",
        action="store_true",
        help="Обогащать только каналы без last_post_content.",
    )
    return p


async def _main(args: argparse.Namespace) -> None:
    tgstat_token = args.tgstat_token or os.environ.get("TGSTAT_API_TOKEN", "")
    if args.source == "tgstat" and not tgstat_token and not args.dry_run:
        print(
            "[ОШИБКА] TGSTAT_API_TOKEN не задан. "
            "Передайте --tgstat-token TOKEN или задайте переменную окружения."
        )
        sys.exit(1)

    await run_enrichment(
        source=args.source,
        batch_size=args.batch_size,
        delay=args.delay,
        dry_run=args.dry_run,
        only_missing_avatars=args.only_missing_avatars,
        only_missing_posts=args.only_missing_posts,
        tgstat_token=tgstat_token or None,
    )


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    asyncio.run(_main(args))


if __name__ == "__main__":
    main()
