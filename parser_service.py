"""Dedicated consumer for channel_discovery tasks."""

from __future__ import annotations

import asyncio
import os
import signal

from channels.channel_db import ChannelDB
from channels.discovery import ChannelDiscovery
from config import settings
from core.account_manager import AccountManager
from core.digest_service import send_parser_task_digest
from core.ops_service import store_task_status
from core.proxy_manager import ProxyManager
from core.rate_limiter import RateLimiter
from core.redis_state import redis_state
from core.session_manager import SessionManager
from core.task_queue import task_queue
from storage.sqlite_db import init_db
from utils.logger import log


PARSER_LEASE_SEC = 1800
PARSER_MAX_ATTEMPTS = 2


class ParserService:
    def __init__(self):
        self._worker_id = str(os.environ.get("WORKER_ID") or "parser")
        self._metrics = {"processed": 0, "failed": 0, "queue_empty_loops": 0}
        self._running = False
        self.proxy_mgr = ProxyManager()
        self.session_mgr = SessionManager()
        self.rate_limiter = RateLimiter()
        self.account_mgr = AccountManager(self.session_mgr, self.proxy_mgr, self.rate_limiter)
        self.discovery = ChannelDiscovery(self.session_mgr, self.account_mgr, self.proxy_mgr)
        self.channel_db = ChannelDB()

    async def _heartbeat(self):
        await redis_state.worker_heartbeat(
            self._worker_id,
            0,
            metrics=dict(self._metrics),
        )

    async def start(self):
        await init_db()
        await task_queue.connect()
        await redis_state.connect()
        if settings.proxy_list_path.exists():
            self.proxy_mgr.load_from_file()
        self._running = True
        log.info("ParserService: listening for channel discovery tasks...")

        while self._running:
            await self._heartbeat()
            try:
                task = await task_queue.reserve(
                    "channel_discovery",
                    consumer_id=self._worker_id,
                    timeout=10,
                    lease_sec=PARSER_LEASE_SEC,
                )
            except Exception as exc:
                log.warning(f"ParserService: reserve failed ({exc})")
                await asyncio.sleep(5)
                continue

            if task is None:
                self._metrics["queue_empty_loops"] += 1
                continue

            task_id = str(task.get("_task_id") or "")
            try:
                report = await self._run_task(task)
                digest_report = None
                if int(report.get("saved", 0) or 0) > 0:
                    try:
                        digest_report = await send_parser_task_digest(task, report)
                    except Exception as exc:
                        digest_report = {"ok": False, "error": f"digest_error:{exc.__class__.__name__}"}
                if digest_report is not None:
                    report["digest"] = digest_report
                self._metrics["processed"] += 1
                if task_id:
                    await store_task_status(
                        "parser_tasks",
                        task_id,
                        {"ok": True, "task_id": task_id, "state": "done", "report": report},
                    )
                    await task_queue.ack("channel_discovery", task_id)
            except Exception as exc:
                self._metrics["failed"] += 1
                attempts = int(task.get("_attempts", 0)) + 1
                payload = dict(task)
                payload["_attempts"] = attempts
                if task_id:
                    await store_task_status(
                        "parser_tasks",
                        task_id,
                        {
                            "ok": False,
                            "task_id": task_id,
                            "state": "failed" if attempts > PARSER_MAX_ATTEMPTS else "retry",
                            "error": str(exc),
                            "attempts": attempts,
                        },
                    )
                if task_id and attempts <= PARSER_MAX_ATTEMPTS:
                    await task_queue.requeue(
                        "channel_discovery",
                        task_id,
                        payload,
                        reason=f"retry_after_error:{type(exc).__name__}",
                    )
                elif task_id:
                    await task_queue.dead_letter(
                        "channel_discovery",
                        task_id,
                        payload,
                        reason=f"parser_failed:{type(exc).__name__}",
                    )
                log.error(f"ParserService: task failed: {exc}")

    async def _run_task(self, task: dict) -> dict:
        kind = str(task.get("kind") or "").strip()
        if kind == "keyword_search":
            return await self._keyword_search(task)
        if kind == "similar_search":
            return await self._similar_search(task)
        if kind == "manual_add":
            return await self._manual_add(task)
        raise RuntimeError(f"unknown_parser_task_kind:{kind or 'empty'}")

    async def _keyword_search(self, task: dict) -> dict:
        keywords = [str(item).strip() for item in list(task.get("keywords") or []) if str(item).strip()]
        if not keywords:
            raise RuntimeError("keywords_required")
        found = await self.discovery.search_by_keywords(
            keywords=keywords,
            min_subscribers=int(task.get("min_subscribers") or settings.PARSER_MIN_SUBSCRIBERS),
            require_comments=bool(
                settings.PARSER_REQUIRE_COMMENTS
                if task.get("require_comments") is None
                else task.get("require_comments")
            ),
            require_russian=bool(
                settings.PARSER_REQUIRE_RUSSIAN
                if task.get("require_russian") is None
                else task.get("require_russian")
            ),
            stage1_limit=int(task.get("stage1_limit") or settings.PARSER_STAGE1_LIMIT),
        )
        saved = 0
        for channel_info in found:
            if task.get("topic") and not channel_info.topic:
                channel_info.topic = str(task.get("topic"))
            await self.channel_db.add_channel(
                {
                    **channel_info.__dict__,
                    "review_state": "discovered",
                    "publish_mode": "research_only",
                    "permission_basis": "",
                    "review_note": "queued keyword search",
                },
                user_id=task.get("user_id"),
            )
            saved += 1
        items = [
            {
                "title": item.title,
                "username": item.username,
                "subscribers": item.subscribers,
            }
            for item in found[: max(1, int(settings.DIGEST_MAX_ITEMS))]
        ]
        return {
            "kind": "keyword_search",
            "keywords": keywords,
            "found": len(found),
            "saved": saved,
            "items": items,
            "filter_stats": dict(self.discovery.last_filter_stats or {}),
        }

    async def _similar_search(self, task: dict) -> dict:
        user_id = task.get("user_id")
        usernames = await self.channel_db.get_usernames(user_id=user_id)
        if not usernames:
            return {
                "kind": "similar_search",
                "found": 0,
                "saved": 0,
                "reason": "no_source_channels",
            }
        found = await self.discovery.find_similar_channels(
            usernames,
            min_subscribers=int(task.get("min_subscribers") or settings.PARSER_MIN_SUBSCRIBERS),
        )
        saved = 0
        for channel_info in found[:50]:
            await self.channel_db.add_channel(
                {
                    **channel_info.__dict__,
                    "review_state": "discovered",
                    "publish_mode": "research_only",
                    "permission_basis": "",
                    "review_note": "queued similar search",
                },
                user_id=user_id,
            )
            saved += 1
        items = [
            {
                "title": item.title,
                "username": item.username,
                "subscribers": item.subscribers,
            }
            for item in found[: max(1, int(settings.DIGEST_MAX_ITEMS))]
        ]
        return {
            "kind": "similar_search",
            "source_channels": len(usernames),
            "found": len(found),
            "saved": saved,
            "items": items,
        }

    async def _manual_add(self, task: dict) -> dict:
        ref = str(task.get("ref") or "").strip()
        if not ref:
            raise RuntimeError("channel_ref_required")
        info = await self.discovery.get_channel_info(ref)
        channel = await self.channel_db.add_channel(
            {
                **info.__dict__,
                "review_state": "approved",
                "publish_mode": "auto_allowed",
                "permission_basis": "admin_added",
                "review_note": "manual add via ops api",
            },
            user_id=task.get("user_id"),
        )
        return {
            "kind": "manual_add",
            "saved": 1,
            "channel_id": channel.id,
            "title": info.title,
            "username": info.username,
            "subscribers": info.subscribers,
            "items": [
                {
                    "title": info.title,
                    "username": info.username,
                    "subscribers": info.subscribers,
                }
            ],
        }

    async def stop(self):
        self._running = False
        await task_queue.close()
        await redis_state.close()


async def main():
    service = ParserService()
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(service.stop()))
    await service.start()


if __name__ == "__main__":
    asyncio.run(main())
