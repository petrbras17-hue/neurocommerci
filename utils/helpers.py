"""
Утилиты общего назначения.
"""

from datetime import datetime, timezone


def utcnow() -> datetime:
    """Текущее UTC время (naive datetime, без tzinfo).

    Замена deprecated datetime.utcnow() — совместимо с Python 3.12+.
    Возвращает naive datetime для совместимости с SQLite и существующими моделями.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)
