"""
Bootstrap — восстановление данных из переменных окружения при старте на Railway.

При деплое на Railway файлы data/sessions/*.session и data/proxies.txt
не попадают в репозиторий (в .gitignore). Решение:
  - SESSIONS_DATA: base64-encoded tar.gz архив с .session и .json файлами
  - PROXY_DATA: содержимое proxies.txt (одна или несколько строк через ;)

При локальной работе эти переменные не нужны — файлы уже на месте.
"""

from __future__ import annotations

import base64
import io
import os
import tarfile
from pathlib import Path

from utils.logger import log


def restore_sessions(sessions_dir: Path) -> int:
    """Распаковать сессии из SESSIONS_DATA env var.

    Формат: base64-encoded tar.gz с .session и .json файлами.
    Возвращает количество распакованных файлов.
    """
    data_b64 = os.environ.get("SESSIONS_DATA", "")
    if not data_b64:
        return 0

    # Если файлы уже есть — не перезаписывать
    existing = list(sessions_dir.glob("*.session"))
    if existing:
        log.info(f"Сессии уже на месте ({len(existing)} файлов), пропускаем распаковку")
        return len(existing)

    sessions_dir.mkdir(parents=True, exist_ok=True)

    try:
        archive_bytes = base64.b64decode(data_b64)
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as tar:
            # Безопасная распаковка: только файлы, без path traversal
            count = 0
            for member in tar.getmembers():
                # Только .session и .json файлы, без путей с ../
                name = os.path.basename(member.name)
                if not name:
                    continue
                if not (name.endswith(".session") or name.endswith(".json")):
                    continue
                if ".." in member.name or member.name.startswith("/"):
                    log.warning(f"Пропускаю подозрительный путь: {member.name}")
                    continue

                member.name = name  # Плоская распаковка в sessions_dir
                tar.extract(member, path=str(sessions_dir))
                count += 1

            log.info(f"Распаковано {count} файлов сессий из SESSIONS_DATA")
            return count

    except Exception as exc:
        log.error(f"Ошибка распаковки SESSIONS_DATA: {exc}")
        return 0


def restore_proxies(proxies_path: Path) -> bool:
    """Создать proxies.txt из PROXY_DATA env var.

    Формат: строки прокси через ; (точка с запятой).
    Пример: "host:port:user:pass" или "host:port:user:pass;host2:port2:user2:pass2"
    """
    proxy_data = os.environ.get("PROXY_DATA", "")
    if not proxy_data:
        return False

    # Если файл уже есть — не перезаписывать
    if proxies_path.exists() and proxies_path.stat().st_size > 0:
        log.info(f"proxies.txt уже существует ({proxies_path}), пропускаем")
        return True

    proxies_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        lines = proxy_data.replace(";", "\n").strip()
        proxies_path.write_text(lines + "\n", encoding="utf-8")
        line_count = len([l for l in lines.split("\n") if l.strip()])
        log.info(f"Создан proxies.txt из PROXY_DATA ({line_count} прокси)")
        return True
    except Exception as exc:
        log.error(f"Ошибка создания proxies.txt: {exc}")
        return False


def bootstrap():
    """Главная функция bootstrap — вызывается перед стартом бота."""
    from config import settings, BASE_DIR

    sessions_dir = BASE_DIR / settings.SESSIONS_DIR
    proxies_path = BASE_DIR / settings.PROXY_LIST_FILE

    log.info("Bootstrap: проверка данных...")

    sessions_count = restore_sessions(sessions_dir)
    proxies_ok = restore_proxies(proxies_path)

    # Статус
    existing_sessions = list(sessions_dir.glob("*.session")) if sessions_dir.exists() else []
    proxies_exist = proxies_path.exists() and proxies_path.stat().st_size > 0

    log.info(
        f"Bootstrap: сессий={len(existing_sessions)}, "
        f"proxies.txt={'OK' if proxies_exist else 'НЕТ'}"
    )

    if not existing_sessions:
        log.warning("Нет .session файлов! Задайте SESSIONS_DATA env var или добавьте файлы вручную.")

    if not proxies_exist:
        log.warning("Нет proxies.txt! Задайте PROXY_DATA env var или создайте файл вручную.")
