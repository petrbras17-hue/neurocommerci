"""
Anti-Detection System — behavioral randomization and human simulation.

Provides typing simulation, random delays, read simulation,
online-status toggling, random reactions, and behavioral fingerprinting
to avoid Telegram detection.

v2 additions (Sprint 8):
  - toggle_online_status()      — randomized online/offline pattern
  - simulate_channel_browse()   — scroll + read multiple posts
  - send_random_reaction()      — react to a random recent post
  - per_account_interval()      — unique timing jitter per account
  - is_night_hours()            — time-of-day awareness (00:00–07:00)
"""

from __future__ import annotations

import asyncio
import random
from datetime import timezone
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

    # ------------------------------------------------------------------
    # Online status toggling (Sprint 8)
    # ------------------------------------------------------------------

    async def toggle_online_status(
        self,
        client: Any,
        cycles: int = 3,
    ) -> None:
        """
        Simulate human-like online/offline presence toggling.

        Sends UpdateStatus(offline=False/True) with random delays between
        transitions, mimicking a user who glances at their phone and puts it down.

        Args:
            client:  Telethon TelegramClient.
            cycles:  Number of on/off cycles to perform.
        """
        if client is None:
            return
        try:
            from telethon.functions.account import UpdateStatusRequest
        except ImportError:
            return

        delays = {
            "conservative": (30.0, 90.0),
            "moderate": (15.0, 45.0),
            "aggressive": (5.0, 20.0),
        }
        lo, hi = delays[self.mode]

        for _ in range(cycles):
            try:
                await client(UpdateStatusRequest(offline=False))
                await asyncio.sleep(random.uniform(lo / 2, hi / 2))
                await client(UpdateStatusRequest(offline=True))
                await asyncio.sleep(random.uniform(lo, hi) * self._multiplier)
            except Exception:
                break  # Non-critical

    # ------------------------------------------------------------------
    # Channel browsing simulation (Sprint 8)
    # ------------------------------------------------------------------

    async def simulate_channel_browse(
        self,
        client: Any,
        channel_entity: Any,
        posts_to_read: int = 5,
    ) -> None:
        """
        Simulate browsing a channel: read a few recent posts with realistic pauses.

        Args:
            client:           Telethon TelegramClient.
            channel_entity:   The channel/chat entity.
            posts_to_read:    How many recent posts to "read".
        """
        if client is None or channel_entity is None:
            return
        try:
            msgs = await client.get_messages(channel_entity, limit=posts_to_read)
            for msg in msgs:
                text = getattr(msg, "text", None) or ""
                # Realistic reading speed: 200–300 chars/sec
                chars = max(1, len(text))
                speed = random.uniform(200.0, 300.0) / self._multiplier
                await asyncio.sleep(min(chars / speed + random.uniform(0.5, 2.0), 10.0))
        except Exception:
            pass  # Non-critical

    # ------------------------------------------------------------------
    # Random reaction on recent posts (Sprint 8)
    # ------------------------------------------------------------------

    async def send_random_reaction(
        self,
        client: Any,
        channel_entity: Any,
        skip_probability: float = 0.6,
    ) -> bool:
        """
        React to a random recent post in the channel.

        Args:
            client:           Telethon TelegramClient.
            channel_entity:   Channel/chat entity.
            skip_probability: Probability of skipping (default 60% — not every visit).

        Returns:
            True if a reaction was sent, False otherwise.
        """
        if client is None or channel_entity is None:
            return False
        if random.random() < skip_probability:
            return False
        try:
            from telethon.tl.functions.messages import SendReactionRequest
            from telethon.tl.types import ReactionEmoji
        except ImportError:
            return False
        try:
            msgs = await client.get_messages(channel_entity, limit=10)
            if not msgs:
                return False
            target = random.choice(msgs)
            emoji_char = self.randomize_emoji()
            await client(
                SendReactionRequest(
                    peer=channel_entity,
                    msg_id=target.id,
                    reaction=[ReactionEmoji(emoticon=emoji_char)],
                )
            )
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Per-account interval jitter (Sprint 8)
    # ------------------------------------------------------------------

    def per_account_interval(
        self,
        base_min_sec: float,
        base_max_sec: float,
        account_id: int,
    ) -> float:
        """
        Return a per-account randomized interval within [base_min, base_max].

        Uses account_id as a deterministic seed offset so different accounts
        get slightly different timing profiles, reducing fingerprinting.

        Args:
            base_min_sec:  Minimum interval in seconds.
            base_max_sec:  Maximum interval in seconds.
            account_id:    Account ID used to seed jitter offset.

        Returns:
            Float seconds to wait.
        """
        # Stable per-account offset: ±15% of base range
        range_width = base_max_sec - base_min_sec
        account_seed_offset = (account_id % 100) / 100.0  # 0.00–0.99
        # Shift the midpoint slightly based on account_id
        midpoint = (base_min_sec + base_max_sec) / 2.0
        offset = (account_seed_offset - 0.5) * range_width * 0.30
        adjusted_min = max(base_min_sec, midpoint + offset - range_width * 0.35)
        adjusted_max = min(base_max_sec, midpoint + offset + range_width * 0.35)
        return random.uniform(adjusted_min, adjusted_max) * self._multiplier

    # ------------------------------------------------------------------
    # Time-of-day awareness (Sprint 8)
    # ------------------------------------------------------------------

    @staticmethod
    def is_night_hours(account_utc_offset_hours: int = 3) -> bool:
        """
        Return True if the current time (adjusted for account timezone) falls
        between 00:00 and 07:00 — the quiet hours.

        Args:
            account_utc_offset_hours:  UTC offset for the account's timezone.
                                       Default 3 = Moscow time (UTC+3).

        Returns:
            True if current local hour is in [0, 7).
        """
        from datetime import datetime
        utc_now = datetime.now(timezone.utc)
        local_hour = (utc_now.hour + account_utc_offset_hours) % 24
        return 0 <= local_hour < 7

    def night_activity_multiplier(self, account_utc_offset_hours: int = 3) -> float:
        """
        Return a multiplier for activity reduction during night hours.

        Night (00:00–07:00 local): 0.2 — drastically reduced activity.
        Day:                        1.0 — normal activity.
        """
        if self.is_night_hours(account_utc_offset_hours):
            return 0.2
        return 1.0
