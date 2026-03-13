"""
Liveliness Service — standalone Docker Compose service.

Entry point that runs two async modules:
1. LifelinessAgent — per-account human simulation
2. DigestReporter — Redis pub/sub -> Telegram digest
"""
from __future__ import annotations

import asyncio
import logging
import signal
import sys

import redis.asyncio as aioredis

from config import settings
from core.event_bus import EventBus, init_event_bus, publish_event
from core.liveliness_agent import LifelinessAgent
from core.digest_service import DigestReporter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("liveliness_service")


async def main() -> None:
    log.info("Starting liveliness service...")

    # Connect Redis
    redis_client = aioredis.from_url(
        settings.REDIS_URL,
        decode_responses=True,
    )
    try:
        await redis_client.ping()
        log.info("Redis connected: %s", settings.REDIS_URL)
    except Exception as exc:
        log.error("Redis connection failed: %s", exc)
        sys.exit(1)

    # Init event bus
    event_bus = init_event_bus(redis_client)

    # Create modules
    agent = LifelinessAgent(redis_client=redis_client)
    reporter = DigestReporter(
        event_bus=event_bus,
        batch_window_sec=settings.DIGEST_BATCH_WINDOW_SEC,
        max_per_minute=settings.DIGEST_MAX_PER_MINUTE,
    )

    # Graceful shutdown
    shutdown_event = asyncio.Event()

    def _signal_handler():
        log.info("Shutdown signal received")
        shutdown_event.set()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    # Run both modules
    agent_task = asyncio.create_task(agent.start())
    reporter_task = asyncio.create_task(reporter.start())

    await publish_event("system", {"action": "liveliness_service_started"})

    # Wait for shutdown
    await shutdown_event.wait()

    log.info("Shutting down...")
    await agent.stop()
    agent_task.cancel()
    reporter_task.cancel()

    try:
        await asyncio.gather(agent_task, reporter_task, return_exceptions=True)
    except asyncio.CancelledError:
        pass

    await publish_event("system", {"action": "liveliness_service_stopped"})
    await redis_client.aclose()
    log.info("Liveliness service stopped.")


if __name__ == "__main__":
    asyncio.run(main())
