"""
Pyrogram-based commenting engine.

Minimal, focused implementation inspired by the "50-line neurocommenting bot"
approach. Uses Pyrogram's native get_discussion_message() for cleaner
channel comment handling.

Key advantages over Telethon approach:
- get_discussion_message() auto-resolves discussion groups
- message.reply() handles threading automatically
- No need to manually track discussion_group_id
"""

import asyncio
import random
import time
from typing import Optional

from loguru import logger as log
from pyrogram import Client
from pyrogram.errors import (
    FloodWait,
    ChannelPrivate,
    MsgIdInvalid,
    ChatWriteForbidden,
    UserBannedInChannel,
    SlowmodeWait,
    PeerIdInvalid,
)

from config import settings


class PyrogramCommenter:
    """
    Lightweight commenting engine using Pyrogram.

    Usage:
        commenter = PyrogramCommenter(
            session_name="data/sessions/account1",
            api_id=21724,
            api_hash="...",
            proxy={"scheme": "socks5", "hostname": "...", "port": 1080}
        )
        async with commenter:
            result = await commenter.comment_on_post(
                channel="@somechannel",
                post_id=123,
                text="Great post!",
            )
    """

    def __init__(
        self,
        session_name: str,
        api_id: int,
        api_hash: str,
        proxy: Optional[dict] = None,
        device_model: str = "Samsung Galaxy S24",
        system_version: str = "SDK 34",
        app_version: str = "12.5.2",
        lang_code: str = "ru",
    ):
        self._client = Client(
            name=session_name,
            api_id=api_id,
            api_hash=api_hash,
            proxy=proxy,
            device_model=device_model,
            system_version=system_version,
            app_version=app_version,
            lang_code=lang_code,
            system_lang_code=f"{lang_code}-{lang_code}",
        )
        self._connected = False

    async def __aenter__(self):
        await self._client.start()
        self._connected = True
        me = await self._client.get_me()
        log.info(f"Pyrogram connected: {me.first_name} ({me.phone_number})")
        return self

    async def __aexit__(self, *args):
        if self._connected:
            await self._client.stop()
            self._connected = False

    async def comment_on_post(
        self,
        channel: str | int,
        post_id: int,
        text: str,
        delay_before_comment: float = 0,
        simulate_reading: bool = True,
        min_existing_comments: int = 0,
    ) -> dict:
        """
        Post a comment on a channel post via discussion group.

        Args:
            channel: Channel username (@channel) or ID
            post_id: Message ID of the post to comment on
            text: Comment text
            delay_before_comment: Seconds to wait before commenting
            simulate_reading: If True, simulate reading the post first
            min_existing_comments: Wait until this many comments exist

        Returns:
            dict with keys: status, message_id, error
        """
        try:
            # Step 1: Get the discussion message (auto-resolves discussion group)
            discussion_msg = await self._client.get_discussion_message(
                channel, post_id
            )

            if not discussion_msg:
                return {"status": "failed", "error": "no_discussion_group"}

            # Step 2: Check existing comments count
            if min_existing_comments > 0:
                replies = await self._client.get_discussion_replies_count(
                    discussion_msg.chat.id, discussion_msg.id
                )
                if replies < min_existing_comments:
                    log.debug(
                        f"Post {post_id}: {replies}/{min_existing_comments} "
                        f"comments, waiting..."
                    )
                    return {
                        "status": "waiting",
                        "error": "not_enough_comments",
                        "current_comments": replies,
                    }

            # Step 3: Simulate reading the post (human-like behavior)
            if simulate_reading:
                read_time = random.uniform(3.0, 8.0)
                log.debug(f"Simulating reading for {read_time:.1f}s")
                await asyncio.sleep(read_time)

                # Mark as read
                try:
                    await self._client.read_chat_history(discussion_msg.chat.id)
                except Exception:
                    pass  # non-critical

            # Step 4: Optional delay before commenting
            if delay_before_comment > 0:
                jitter = random.uniform(0.8, 1.2)
                actual_delay = delay_before_comment * jitter
                log.debug(f"Delay before comment: {actual_delay:.1f}s")
                await asyncio.sleep(actual_delay)

            # Step 5: Simulate typing
            typing_duration = max(1.5, len(text) / 15)  # ~15 chars/sec
            typing_duration = min(typing_duration, 10.0)  # cap at 10s
            async with self._client.action(
                discussion_msg.chat.id, "typing"
            ):
                await asyncio.sleep(typing_duration)

            # Step 6: Send the comment
            sent = await discussion_msg.reply(text)

            log.info(
                f"Comment posted on {channel} post#{post_id}: "
                f"{text[:50]}... (msg_id={sent.id})"
            )

            return {
                "status": "success",
                "message_id": sent.id,
                "chat_id": discussion_msg.chat.id,
            }

        except FloodWait as e:
            log.warning(f"FloodWait: {e.value}s on {channel}")
            return {
                "status": "flood_wait",
                "error": f"wait_{e.value}s",
                "retry_after": e.value,
            }

        except SlowmodeWait as e:
            log.warning(f"SlowmodeWait: {e.value}s on {channel}")
            return {
                "status": "slowmode",
                "error": f"slowmode_{e.value}s",
                "retry_after": e.value,
            }

        except ChatWriteForbidden:
            log.error(f"Cannot write to {channel} discussion")
            return {"status": "forbidden", "error": "write_forbidden"}

        except UserBannedInChannel:
            log.error(f"Account banned in {channel}")
            return {"status": "banned", "error": "user_banned"}

        except ChannelPrivate:
            log.error(f"Channel {channel} is private or account not member")
            return {"status": "failed", "error": "channel_private"}

        except MsgIdInvalid:
            log.warning(f"Post {post_id} not found in {channel}")
            return {"status": "failed", "error": "post_not_found"}

        except PeerIdInvalid:
            log.error(f"Invalid channel: {channel}")
            return {"status": "failed", "error": "invalid_channel"}

        except Exception as e:
            log.error(f"Comment failed on {channel}#{post_id}: {e}")
            return {"status": "error", "error": str(e)}

    async def monitor_and_comment(
        self,
        channel: str | int,
        text_generator,
        delay_after_post: tuple[int, int] = (60, 300),
        min_existing_comments: int = 2,
        max_comments_per_day: int = 15,
    ):
        """
        Monitor a channel for new posts and comment with AI-generated text.

        Args:
            channel: Channel username or ID
            text_generator: Async callable(post_text) -> comment_text
            delay_after_post: (min, max) seconds to wait after a new post
            min_existing_comments: Don't be first to comment
            max_comments_per_day: Daily limit per account
        """
        comments_today = 0
        today = time.strftime("%Y-%m-%d")

        log.info(f"Monitoring {channel} for new posts...")

        @self._client.on_message()
        async def on_new_post(client, message):
            nonlocal comments_today, today

            # Reset daily counter
            current_day = time.strftime("%Y-%m-%d")
            if current_day != today:
                comments_today = 0
                today = current_day

            # Check daily limit
            if comments_today >= max_comments_per_day:
                log.debug("Daily comment limit reached")
                return

            # Only process posts from the target channel
            if not message.chat:
                return

            chat_id = message.chat.id
            chat_username = getattr(message.chat, "username", None)
            if chat_username:
                chat_username = f"@{chat_username}"

            target = str(channel).lstrip("@")
            current = (chat_username or "").lstrip("@")

            if str(chat_id) != str(channel) and current != target:
                return

            # Don't comment on service messages
            if not message.text and not message.caption:
                return

            post_text = message.text or message.caption or ""
            post_id = message.id

            log.info(f"New post in {channel}: {post_text[:80]}...")

            # Wait before commenting (don't be first!)
            delay = random.randint(*delay_after_post)
            log.debug(f"Waiting {delay}s before commenting...")
            await asyncio.sleep(delay)

            # Generate comment
            comment_text = await text_generator(post_text)
            if not comment_text:
                log.warning("Text generator returned empty comment")
                return

            # Post comment
            result = await self.comment_on_post(
                channel=channel,
                post_id=post_id,
                text=comment_text,
                simulate_reading=True,
                min_existing_comments=min_existing_comments,
            )

            if result["status"] == "success":
                comments_today += 1
                log.info(
                    f"Comment #{comments_today} posted "
                    f"(limit: {max_comments_per_day}/day)"
                )
            elif result["status"] == "waiting":
                # Not enough comments yet, retry later
                log.debug("Not enough comments, will retry in 120s")
                await asyncio.sleep(120)
                result = await self.comment_on_post(
                    channel=channel,
                    post_id=post_id,
                    text=comment_text,
                    simulate_reading=False,
                    min_existing_comments=0,  # don't wait this time
                )
                if result["status"] == "success":
                    comments_today += 1
