"""
Логирование через Loguru.
"""

import sys
from pathlib import Path

from loguru import logger

from config import settings, BASE_DIR


def setup_logger():
    """Настроить логирование: консоль + файл."""
    logger.remove()

    # Консоль
    logger.add(
        sys.stderr,
        level=settings.LOG_LEVEL,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan> - <level>{message}</level>",
    )

    # Файл (ротация 10MB, хранить 7 дней)
    log_dir = BASE_DIR / "data" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    logger.add(
        str(log_dir / "neuro_commenting_{time:YYYY-MM-DD}.log"),
        level="DEBUG",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
        rotation="10 MB",
        retention="7 days",
        compression="zip",
        encoding="utf-8",
    )

    return logger


log = setup_logger()
