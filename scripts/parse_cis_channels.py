"""
CLI script: parse CIS Telegram channels by keywords and index them into the
platform-level channel_map_entries catalog.

Usage:
    python scripts/parse_cis_channels.py --keywords "маркетинг" "ecommerce" --limit 200
    python scripts/parse_cis_channels.py --keywords-file data/parsing_keywords.txt
    python scripts/parse_cis_channels.py --keywords-file data/parsing_keywords.txt --limit 500 --min-subscribers 5000

Filters applied:
    - min_subscribers >= 1000 (configurable via --min-subscribers)
    - has_comments = True
    - language in (ru, uk, kz, uz, by)

Deduplication:
    - Channels already in channel_map_entries are skipped (resume-safe).

Progress:
    - Prints "Indexed N/total channels..." every 10 entries.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import Any, Optional

# Ensure project root is on the path when run directly.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.channel_indexer import ChannelIndexer, _detect_language, _CIS_LANGUAGES
from storage.models import ChannelMapEntry
from storage.sqlite_db import init_db, async_session
from utils.helpers import utcnow
from utils.logger import log


# ---------------------------------------------------------------------------
# Language detection — CIS filter
# ---------------------------------------------------------------------------

def _is_cis_language(title: str, about: str) -> bool:
    """Return True if the channel appears to be in a CIS language."""
    lang = _detect_language(title, about)
    return lang in _CIS_LANGUAGES


# ---------------------------------------------------------------------------
# Existing-username lookup (deduplication / resume support)
# ---------------------------------------------------------------------------

async def _load_existing_usernames(session: AsyncSession) -> set[str]:
    """Load all already-indexed platform catalog usernames."""
    result = await session.execute(
        select(ChannelMapEntry.username).where(
            ChannelMapEntry.tenant_id.is_(None),
            ChannelMapEntry.username.isnot(None),
        )
    )
    return {row[0].lower() for row in result.all() if row[0]}


# ---------------------------------------------------------------------------
# Core parsing function
# ---------------------------------------------------------------------------

async def parse_cis_channels(
    keywords: list[str],
    min_subscribers: int = 1000,
    max_results: int = 500,
    delay_between_keywords: float = 2.5,
    session_manager: Any = None,
) -> int:
    """
    Search Telegram by keywords, filter, deduplicate, and save to
    channel_map_entries.

    Returns the count of newly indexed channels.
    """
    await init_db()

    indexer = ChannelIndexer(session_manager=session_manager)
    client = await indexer._get_client()

    async with async_session() as session:
        existing_usernames = await _load_existing_usernames(session)
        print(
            f"Resume point: {len(existing_usernames)} channels already indexed. "
            f"Skipping duplicates."
        )

        if client is None:
            print(
                "[WARNING] No connected Telethon session found. "
                "Running in stub mode — results will be empty."
            )
            log.warning("parse_cis_channels: no Telethon client available")
            return 0

        try:
            from telethon import functions as tl_functions
            from telethon.tl.types import Channel as TLChannel
        except ImportError:
            print("[ERROR] telethon is not installed.")
            return 0

        discovered: dict[int, dict] = {}  # telegram_id -> channel_dict
        total_keywords = len(keywords)

        for i, keyword in enumerate(keywords):
            print(f"[{i + 1}/{total_keywords}] Searching: '{keyword}'")
            if i > 0:
                await asyncio.sleep(delay_between_keywords)

            try:
                result = await client(
                    tl_functions.contacts.SearchRequest(q=keyword, limit=50)
                )
            except Exception as exc:
                log.warning(f"parse_cis_channels: keyword '{keyword}' search failed: {exc}")
                continue

            for chat in getattr(result, "chats", []):
                if not isinstance(chat, TLChannel):
                    continue
                if not getattr(chat, "broadcast", False):
                    continue

                tg_id = int(chat.id)
                if tg_id in discovered:
                    continue

                username = getattr(chat, "username", None)
                if username and username.lower() in existing_usernames:
                    continue  # already in catalog

                # Basic subscriber check without full channel fetch
                subs = getattr(chat, "participants_count", 0) or 0
                if subs < min_subscribers:
                    continue

                title = (getattr(chat, "title", "") or "").strip()

                # Language filter (stage-1 heuristic on title only)
                import re as _re
                _RU = _re.compile(r"[А-Яа-яЁё]")
                if not _RU.search(title) and not _RU.search(keyword):
                    continue

                discovered[tg_id] = {
                    "_entity": chat,
                    "telegram_id": tg_id,
                    "username": username,
                    "title": title,
                    "member_count": subs,
                }

                if len(discovered) >= max_results:
                    print(f"Reached max_results={max_results} limit.")
                    break

            if len(discovered) >= max_results:
                break

        print(
            f"Stage-1 complete: {len(discovered)} candidate channels "
            f"(after dedup + language + subscriber filter)."
        )

        # Stage-2: fetch full info and upsert
        newly_indexed = 0
        candidates = list(discovered.values())
        total = len(candidates)

        for j, ch in enumerate(candidates):
            entity = ch["_entity"]
            username = ch.get("username")

            try:
                from telethon import functions as tl_func2
                full = await client(tl_func2.channels.GetFullChannelRequest(channel=entity))
                full_chat = full.full_chat

                about = (getattr(full_chat, "about", "") or "").strip()
                linked_chat_id = getattr(full_chat, "linked_chat_id", None)
                participants_count = getattr(full_chat, "participants_count", None)
                if participants_count is None:
                    participants_count = ch["member_count"]

                # has_comments filter
                if not linked_chat_id:
                    continue

                # language filter (CIS)
                title_str = ch["title"]
                lang = _detect_language(title_str, about)
                if lang not in _CIS_LANGUAGES:
                    continue

                # Estimate post frequency
                post_freq: Optional[float] = None
                last_post_at: Optional[datetime] = None
                try:
                    messages = await client.get_messages(entity, limit=20)
                    from core.channel_indexer import _estimate_post_frequency
                    post_freq = _estimate_post_frequency(messages)
                    if messages:
                        last_post_at = getattr(messages[0], "date", None)
                except Exception:
                    pass

                now = utcnow()
                data = {
                    "telegram_id": ch["telegram_id"],
                    "username": username or f"id{ch['telegram_id']}",
                    "title": title_str or f"channel_{ch['telegram_id']}",
                    "description": about or None,
                    "language": lang,
                    "region": "cis",
                    "member_count": int(participants_count),
                    "has_comments": True,
                    "comments_enabled": True,
                    "post_frequency_daily": post_freq,
                    "source": "parsed",
                    "last_indexed_at": now,
                    "last_refreshed_at": now,
                }

                await indexer._upsert_entry(data, session)
                newly_indexed += 1

                if username:
                    existing_usernames.add(username.lower())

                if newly_indexed % 10 == 0 or newly_indexed == total:
                    print(f"Indexed {newly_indexed}/{total} channels...")

                await asyncio.sleep(1.5)

            except Exception as exc:
                log.warning(
                    f"parse_cis_channels: stage-2 failed for '{username}': {exc}"
                )
                await asyncio.sleep(0.5)

        print(f"\nDone. Newly indexed: {newly_indexed} channels.")
        return newly_indexed


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _load_keywords_from_file(path: str) -> list[str]:
    with open(path, "r", encoding="utf-8") as fh:
        lines = [line.strip() for line in fh if line.strip() and not line.startswith("#")]
    return lines


async def _main(args: argparse.Namespace) -> None:
    keywords: list[str] = []

    if args.keywords_file:
        keywords = _load_keywords_from_file(args.keywords_file)
        print(f"Loaded {len(keywords)} keywords from {args.keywords_file}")
    elif args.keywords:
        keywords = args.keywords
    else:
        print("[ERROR] Provide --keywords or --keywords-file")
        sys.exit(1)

    if not keywords:
        print("[ERROR] No keywords found.")
        sys.exit(1)

    # Attempt to load a session manager if the main runtime is available.
    session_manager = None
    try:
        from core.session_manager import SessionManager
        session_manager = SessionManager()
    except Exception:
        pass

    count = await parse_cis_channels(
        keywords=keywords,
        min_subscribers=args.min_subscribers,
        max_results=args.limit,
        session_manager=session_manager,
    )
    print(f"Total newly indexed: {count}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Parse CIS Telegram channels and index into channel_map_entries."
    )
    parser.add_argument(
        "--keywords",
        nargs="+",
        metavar="KEYWORD",
        help="One or more search keywords.",
    )
    parser.add_argument(
        "--keywords-file",
        metavar="FILE",
        help="Path to a text file with one keyword per line.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=500,
        help="Maximum number of channels to index (default: 500).",
    )
    parser.add_argument(
        "--min-subscribers",
        type=int,
        default=1000,
        help="Minimum subscriber count (default: 1000).",
    )
    args = parser.parse_args()
    asyncio.run(_main(args))


if __name__ == "__main__":
    main()
