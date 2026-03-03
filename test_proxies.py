"""
Тест прокси: проверяет HTTP CONNECT через каждый прокси к Telegram DC.
Рабочие прокси сохраняются в data/proxies.txt
Запуск: python test_proxies.py
"""

from __future__ import annotations

import asyncio
import aiohttp
import time
from pathlib import Path
from typing import Optional

INPUT_FILE = Path("data/new_proxies_raw.txt")
OUTPUT_FILE = Path("data/proxies.txt")
TEST_URL = "http://api.ipify.org"  # Простой HTTP endpoint для проверки
TIMEOUT = 10  # секунд
BATCH_SIZE = 50  # одновременных проверок


def parse_proxy(line: str) -> dict | None:
    """Парсит строку proxy в формат для aiohttp."""
    line = line.strip()
    if not line:
        return None
    parts = line.split(":")
    if len(parts) != 4:
        return None
    host, port, user, password = parts
    return {
        "raw": line,
        "url": f"http://{user}:{password}@{host}:{port}",
    }


async def test_proxy(session: aiohttp.ClientSession, proxy: dict) -> tuple[str, bool, str]:
    """Проверяет один прокси."""
    try:
        async with session.get(
            TEST_URL,
            proxy=proxy["url"],
            timeout=aiohttp.ClientTimeout(total=TIMEOUT),
        ) as resp:
            if resp.status == 200:
                ip = await resp.text()
                return proxy["raw"], True, ip.strip()
            return proxy["raw"], False, f"HTTP {resp.status}"
    except asyncio.TimeoutError:
        return proxy["raw"], False, "timeout"
    except Exception as e:
        err = str(e)
        if len(err) > 60:
            err = err[:60] + "..."
        return proxy["raw"], False, err


async def test_batch(proxies: list[dict], batch_num: int, total_batches: int) -> list[tuple[str, bool, str]]:
    """Тестирует пачку прокси."""
    connector = aiohttp.TCPConnector(limit=BATCH_SIZE, ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [test_proxy(session, p) for p in proxies]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        processed = []
        for r in results:
            if isinstance(r, Exception):
                processed.append(("unknown", False, str(r)))
            else:
                processed.append(r)
        return processed


async def main():
    # Загрузить прокси
    lines = INPUT_FILE.read_text().strip().split("\n")
    proxies = []
    for line in lines:
        p = parse_proxy(line)
        if p:
            proxies.append(p)

    print(f"\n  Загружено {len(proxies)} прокси из {INPUT_FILE}")
    print(f"  Тестирование батчами по {BATCH_SIZE}...\n")

    working = []
    failed = 0
    start = time.time()

    # Тестируем батчами
    for i in range(0, len(proxies), BATCH_SIZE):
        batch = proxies[i:i + BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
        total_batches = (len(proxies) + BATCH_SIZE - 1) // BATCH_SIZE

        print(f"  Батч {batch_num}/{total_batches} ({len(batch)} прокси)...", end=" ", flush=True)

        results = await test_batch(batch, batch_num, total_batches)

        batch_ok = 0
        for raw, ok, info in results:
            if ok:
                working.append(raw)
                batch_ok += 1
            else:
                failed += 1

        print(f"OK: {batch_ok}, FAIL: {len(batch) - batch_ok}")

        # Небольшая пауза между батчами
        if i + BATCH_SIZE < len(proxies):
            await asyncio.sleep(1)

    elapsed = time.time() - start

    # Сохранить рабочие
    if working:
        OUTPUT_FILE.write_text("\n".join(working) + "\n")

    print(f"\n  === Результат ===")
    print(f"  Всего: {len(proxies)}")
    print(f"  Рабочих: {len(working)}")
    print(f"  Нерабочих: {failed}")
    print(f"  Время: {elapsed:.1f}с")
    print(f"  Сохранено в: {OUTPUT_FILE}")
    print()


if __name__ == "__main__":
    asyncio.run(main())
