"""
Anti-Detection System — behavioral randomization and human simulation.

Provides typing simulation, random delays, read simulation,
and behavioral fingerprinting to avoid Telegram detection.
"""

from __future__ import annotations

import asyncio
import random
from typing import Any, Optional


# Reaction emojis safe for Telegram
_REACTION_EMOJIS = [
    "\U0001f44d",  # thumbs up
    "\u2764\ufe0f",  # red heart
    "\U0001f525",  # fire
    "\U0001f44f",  # clapping hands
    "\U0001f62e",  # open mouth
    "\U0001f914",  # thinking face
    "\U0001f976",  # cold face
    "\U0001f601",  # grinning
    "\U0001f4af",  # 100
    "\U0001f3e6",  # european bank
    "\U0001f44c",  # ok hand
    "\U0001f60d",  # heart eyes
    "\U0001f621",  # pouting face
    "\U0001f44e",  # thumbs down
]


class AntiDetection:
    """
    Behavioral randomization and human simulation layer.

    Conservative mode: 2x longer delays, more action skips, slower typing.
    Aggressive mode: shorter delays, fewer skips, faster typing.

    mode: "conservative" | "moderate" | "aggressive"
    """

    # Delay multipliers per mode
    _DELAY_MULTIPLIERS = {
        "conservative": 2.0,
        "moderate": 1.3,
        "aggressive": 1.0,
    }

    # Skip probability per mode (probability that an action is randomly skipped)
    _SKIP_PROBABILITIES = {
        "conservative": 0.20,
        "moderate": 0.10,
        "aggressive": 0.05,
    }

    # Typing speed in seconds per character (higher = slower)
    _TYPING_SECS_PER_CHAR = {
        "conservative": 0.10,
        "moderate": 0.07,
        "aggressive": 0.04,
    }

    def __init__(self, mode: str = "conservative") -> None:
        if mode not in ("conservative", "moderate", "aggressive"):
            mode = "aggressive"
        self.mode = mode
        self._multiplier = self._DELAY_MULTIPLIERS[mode]
        self._skip_prob = self._SKIP_PROBABILITIES[mode]
        self._typing_speed = self._TYPING_SECS_PER_CHAR[mode]

    # ------------------------------------------------------------------
    # Typing simulation
    # ------------------------------------------------------------------

    async def simulate_typing(
        self,
        client: Any,
        peer: Any,
        duration_range: tuple[float, float] = (1.0, 5.0),
    ) -> None:
        """Send a typing action to peer for a random duration within duration_range."""
        if peer is None or client is None:
            return
        min_dur, max_dur = duration_range
        duration = random.uniform(min_dur, max_dur) * self._multiplier
        try:
            async with client.action(peer, "typing"):
                await asyncio.sleep(min(duration, 10.0))
        except Exception:
            pass  # Non-critical — don't let typing failure break the caller

    # ------------------------------------------------------------------
    # Read simulation
    # ------------------------------------------------------------------

    async def simulate_reading(
        self,
        client: Any,
        messages: list[Any],
        speed_chars_per_sec: float = 15.0,
    ) -> None:
        """
        Simulate reading a sequence of messages at a realistic speed.

        Waits proportional to the text length of each message.
        """
        if not messages:
            return

        effective_speed = speed_chars_per_sec / self._multiplier  # slower in conservative mode

        for msg in messages:
            text = getattr(msg, "text", None) or getattr(msg, "raw_text", None) or ""
            char_count = max(1, len(str(text)))
            read_time = char_count / effective_speed
            # Add random variance ±30%
            jitter = random.uniform(0.7, 1.3)
            await asyncio.sleep(min(read_time * jitter, 15.0))

    # ------------------------------------------------------------------
    # Generic delays
    # ------------------------------------------------------------------

    async def random_delay(self, min_sec: float, max_sec: float) -> None:
        """Sleep for a random interval [min_sec, max_sec], scaled by mode multiplier."""
        delay = random.uniform(min_sec, max_sec) * self._multiplier
        await asyncio.sleep(delay)

    async def pre_comment_delay(self) -> None:
        """Delay before posting a comment.

        Conservative: 90-180 s
        Moderate:     45-90 s
        Aggressive:   15-45 s
        """
        ranges = {
            "conservative": (90.0, 180.0),
            "moderate": (45.0, 90.0),
            "aggressive": (15.0, 45.0),
        }
        lo, hi = ranges[self.mode]
        await asyncio.sleep(random.uniform(lo, hi))

    async def pre_join_delay(self) -> None:
        """Delay before joining a channel.

        Conservative: 120-300 s
        Moderate:     60-180 s
        Aggressive:   30-90 s
        """
        ranges = {
            "conservative": (120.0, 300.0),
            "moderate": (60.0, 180.0),
            "aggressive": (30.0, 90.0),
        }
        lo, hi = ranges[self.mode]
        await asyncio.sleep(random.uniform(lo, hi))

    async def inter_action_delay(self) -> None:
        """Short delay between any two consecutive actions.

        Conservative: 10-30 s
        Moderate:     5-15 s
        Aggressive:   2-8 s
        """
        ranges = {
            "conservative": (10.0, 30.0),
            "moderate": (5.0, 15.0),
            "aggressive": (2.0, 8.0),
        }
        lo, hi = ranges[self.mode]
        await asyncio.sleep(random.uniform(lo, hi))

    # ------------------------------------------------------------------
    # Behavioral helpers
    # ------------------------------------------------------------------

    def should_skip_action(self, probability: Optional[float] = None) -> bool:
        """
        Randomly decide whether to skip an action for naturalness.

        If probability is None, use the mode-based default.
        Returns True if the action should be skipped.
        """
        p = probability if probability is not None else self._skip_prob
        return random.random() < p

    def randomize_emoji(self) -> str:
        """Return a randomly chosen reaction emoji."""
        return random.choice(_REACTION_EMOJIS)
