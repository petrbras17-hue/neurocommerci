"""Dedicated consumer for recovery_tasks queue."""

from __future__ import annotations

import asyncio
import os
import signal

from config import settings
from core.ops_service import store_task_status
from core.redis_state import redis_state
from core.task_queue import task_queue
from scripts.reconcile_accounts_with_sessions import reconcile
from scripts.session_auth_audit import audit_sessions
from storage.sqlite_db import init_db
from utils.logger import log
from utils.proxy_bindings import bind_accounts_to_proxies, sync_proxies_from_file


RECOVERY_LEASE_SEC = 1800
RECOVERY_MAX_ATTEMPTS = 2


class RecoveryWorker:
    def __init__(self):
        self._running = False
        self._worker_id = str(os.environ.get("WORKER_ID") or "recovery")
        self._metrics = {"processed": 0, "failed": 0, "queue_empty_loops": 0}

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
        self._running = True
        log.info("RecoveryWorker: listening for recovery tasks...")

        while self._running:
            await self._heartbeat()
            try:
                task = await task_queue.reserve(
                    "recovery_tasks",
                    consumer_id=self._worker_id,
                    timeout=10,
                    lease_sec=RECOVERY_LEASE_SEC,
                )
            except Exception as exc:
                log.warning(f"RecoveryWorker: reserve failed ({exc})")
                await asyncio.sleep(5)
                continue

            if task is None:
                self._metrics["queue_empty_loops"] += 1
                continue

            task_id = str(task.get("_task_id") or "")
            try:
                report = await self._run_recovery(task)
                self._metrics["processed"] += 1
                if task_id:
                    await store_task_status(
                        "recovery_tasks",
                        task_id,
                        {
                            "ok": True,
                            "task_id": task_id,
                            "state": "done",
                            "report": report,
                        },
                    )
                    await task_queue.ack("recovery_tasks", task_id)
            except Exception as exc:
                self._metrics["failed"] += 1
                attempts = int(task.get("_attempts", 0)) + 1
                payload = dict(task)
                payload["_attempts"] = attempts
                if task_id:
                    await store_task_status(
                        "recovery_tasks",
                        task_id,
                        {
                            "ok": False,
                            "task_id": task_id,
                            "state": "failed" if attempts > RECOVERY_MAX_ATTEMPTS else "retry",
                            "error": str(exc),
                            "attempts": attempts,
                        },
                    )
                if task_id and attempts <= RECOVERY_MAX_ATTEMPTS:
                    await task_queue.requeue(
                        "recovery_tasks",
                        task_id,
                        payload,
                        reason=f"retry_after_error:{type(exc).__name__}",
                    )
                elif task_id:
                    await task_queue.dead_letter(
                        "recovery_tasks",
                        task_id,
                        payload,
                        reason=f"recovery_failed:{type(exc).__name__}",
                    )
                log.error(f"RecoveryWorker: task failed: {exc}")

    async def _run_recovery(self, task: dict) -> dict:
        user_id = task.get("user_id")
        report = {
            "reconcile": await reconcile(
                user_id=user_id,
                dry_run=False,
                migrate_layout=bool(task.get("migrate_layout", False)),
            ),
            "proxy_sync": await sync_proxies_from_file(settings.proxy_list_path, user_id=user_id),
            "proxy_bind": await bind_accounts_to_proxies(user_id=user_id),
            "session_auth_audit": await audit_sessions(
                user_id=user_id,
                mark_unauthorized=True,
                reactivate_authorized=True,
                authorized_stage=str(task.get("authorized_stage") or "active_commenting"),
                authorized_status=str(task.get("authorized_status") or "active"),
                authorized_health=str(task.get("authorized_health") or "alive"),
                set_parser_first_authorized=bool(task.get("set_parser_first_authorized", True)),
                clear_worker_claims=bool(task.get("clear_worker_claims", True)),
                stage_actor="recovery_worker",
            ),
        }
        return report

    async def stop(self):
        self._running = False
        await task_queue.close()
        await redis_state.close()


async def main():
    worker = RecoveryWorker()
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(worker.stop()))
    await worker.start()


if __name__ == "__main__":
    asyncio.run(main())
