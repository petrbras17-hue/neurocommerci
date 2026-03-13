"""
Telegram Bot Auth — авторизация через deep link на бот.

Flow:
1. Frontend запрашивает POST /auth/telegram/bot-start → получает auth_code
2. Frontend открывает tg://resolve?domain={bot}&start=auth_{code}
3. Бот ловит /start auth_{code} → сохраняет telegram user data в Redis
4. Frontend polls GET /auth/telegram/bot-check?code={code} → получает auth bundle

State хранится в Redis (общий для ops_api и bot контейнеров).
Ключи: botauth:{code} → JSON с telegram_user, confirmed, created_at
TTL: 5 минут (автоочистка через Redis EXPIRE).
"""

from __future__ import annotations

import json
import logging
import secrets
from typing import Optional

import redis.asyncio as aioredis

log = logging.getLogger(__name__)

AUTH_CODE_TTL_SEC = 300
_REDIS_PREFIX = "botauth:"

# Redis connection — shared across the module
_redis: Optional[aioredis.Redis] = None


async def init_redis(redis_url: str) -> None:
    """Initialize the Redis connection for bot auth. Call once at startup."""
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(redis_url, decode_responses=True)
        log.info("telegram_bot_auth: Redis connected")


async def close_redis() -> None:
    """Close Redis connection."""
    global _redis
    if _redis:
        await _redis.aclose()
        _redis = None


def _key(code: str) -> str:
    return f"{_REDIS_PREFIX}{code}"


async def generate_auth_code() -> str:
    """Generate a new auth code and store it in Redis with TTL."""
    if not _redis:
        raise RuntimeError("Bot auth Redis not initialized")
    code = secrets.token_urlsafe(32)
    data = json.dumps({"confirmed": False, "telegram_user": None})
    await _redis.setex(_key(code), AUTH_CODE_TTL_SEC, data)
    return code


async def get_pending_auth(code: str) -> Optional[dict]:
    """Get pending auth data from Redis. Returns dict or None if expired/missing."""
    if not _redis:
        return None
    raw = await _redis.get(_key(code))
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


async def confirm_auth(code: str, telegram_user: dict) -> bool:
    """Confirm auth code with telegram user data. Returns True if successful."""
    if not _redis:
        return False
    raw = await _redis.get(_key(code))
    if not raw:
        return False
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return False
    if data.get("confirmed"):
        return False  # already confirmed
    data["confirmed"] = True
    data["telegram_user"] = telegram_user
    # Re-set with remaining TTL
    ttl = await _redis.ttl(_key(code))
    if ttl and ttl > 0:
        await _redis.setex(_key(code), ttl, json.dumps(data))
    else:
        await _redis.setex(_key(code), AUTH_CODE_TTL_SEC, json.dumps(data))
    return True


async def consume_pending_auth(code: str) -> Optional[dict]:
    """Consume and return telegram user data if confirmed. Deletes the key."""
    if not _redis:
        return None
    raw = await _redis.get(_key(code))
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if data.get("confirmed") and data.get("telegram_user"):
        await _redis.delete(_key(code))
        return data["telegram_user"]
    return None
