from __future__ import annotations

import json
import shutil
import tempfile
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from config import settings
from core.task_queue import task_queue
from storage.sqlite_db import init_db, reconfigure_database


ORIGINAL_DB_URL = settings.db_url
USE_TEMP_SQLITE = "postgresql" not in ORIGINAL_DB_URL


class InMemoryLeaseQueue:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.pending: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.processing: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
        self.dlq: dict[str, list[dict[str, Any]]] = defaultdict(list)

    async def connect(self) -> None:
        return None

    async def close(self) -> None:
        self.reset()

    async def enqueue(self, queue_name: str, payload: dict[str, Any]) -> str:
        body = dict(payload)
        task_id = str(body.get("_task_id") or str(uuid.uuid4())[:8])
        body["_task_id"] = task_id
        body["_attempts"] = int(body.get("_attempts", 0))
        self.pending[queue_name].append(body)
        return task_id

    async def recover_expired_leases(self, queue_name: str) -> int:
        return 0

    async def reserve(self, queue_name: str, *, consumer_id: str, timeout: int = 0, lease_sec: int = 300) -> dict[str, Any] | None:
        _ = timeout
        _ = lease_sec
        items = self.pending[queue_name]
        if not items:
            return None
        payload = dict(items.pop(0))
        self.processing[queue_name][payload["_task_id"]] = payload
        payload["_lease"] = {"consumer_id": consumer_id, "lease_sec": lease_sec}
        return payload

    async def ack(self, queue_name: str, task_id: str) -> bool:
        return self.processing[queue_name].pop(task_id, None) is not None

    async def dead_letter(self, queue_name: str, task_id: str, payload: dict[str, Any], *, reason: str) -> bool:
        self.processing[queue_name].pop(task_id, None)
        body = dict(payload)
        body["_dead_letter_reason"] = reason
        self.dlq[queue_name].append(body)
        return True

    async def queue_sizes(self, queue_name: str) -> dict[str, int]:
        return {
            "pending": len(self.pending[queue_name]),
            "inflight": len(self.processing[queue_name]),
            "dlq": len(self.dlq[queue_name]),
            "leases": len(self.processing[queue_name]),
        }


@pytest_asyncio.fixture(scope="session", loop_scope="session", autouse=True)
async def _session_test_runtime() -> dict[str, Any]:
    patcher = pytest.MonkeyPatch()
    temp_dir: str | None = None

    if USE_TEMP_SQLITE:
        temp_dir = tempfile.mkdtemp(prefix="neurocomment-tests-")
        db_path = Path(temp_dir) / "test.sqlite3"
        await reconfigure_database(f"sqlite+aiosqlite:///{db_path}")

    await init_db()

    fake_queue = InMemoryLeaseQueue()
    patcher.setattr(task_queue, "connect", fake_queue.connect)
    patcher.setattr(task_queue, "close", fake_queue.close)
    patcher.setattr(task_queue, "enqueue", fake_queue.enqueue)
    patcher.setattr(task_queue, "reserve", fake_queue.reserve)
    patcher.setattr(task_queue, "ack", fake_queue.ack)
    patcher.setattr(task_queue, "dead_letter", fake_queue.dead_letter)
    patcher.setattr(task_queue, "recover_expired_leases", fake_queue.recover_expired_leases)
    patcher.setattr(task_queue, "queue_sizes", fake_queue.queue_sizes)

    yield {"queue": fake_queue, "temp_dir": temp_dir}

    patcher.undo()
    if temp_dir:
        shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture(autouse=True)
def _reset_fake_queue(_session_test_runtime: dict[str, Any]) -> None:
    queue = _session_test_runtime["queue"]
    queue.reset()
