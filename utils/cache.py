"""
SettingsCache — кэш с автоинвалидацией при изменении настроек.

Заменяет ad-hoc кэши с паттерном "key-check-then-rebuild"
(poster._ProductCache, templates._system_prompt_cache, channel_setup._fallback_channels_cache и т.д.)
"""

from __future__ import annotations

from typing import Callable, Generic, TypeVar

T = TypeVar("T")

_UNSET = object()


class SettingsCache(Generic[T]):
    """Key-based cache that auto-invalidates when settings change.

    Usage:
        cache = SettingsCache(
            key_fn=lambda: f"{settings.X}|{settings.Y}",
            build_fn=lambda: expensive_build(),
        )
        value = cache.get()  # builds on first call, returns cached on subsequent
    """

    __slots__ = ("_key_fn", "_build_fn", "_key", "_value")

    def __init__(self, key_fn: Callable[[], str], build_fn: Callable[[], T]):
        self._key_fn = key_fn
        self._build_fn = build_fn
        self._key: object = _UNSET
        self._value: T | None = None

    def get(self) -> T:
        key = self._key_fn()
        if key != self._key:
            self._value = self._build_fn()
            self._key = key
        return self._value  # type: ignore[return-value]

    def invalidate(self) -> None:
        self._key = _UNSET
