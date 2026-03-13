"""
Profile Factory — AI-powered Telegram profile generation and management.

Generates realistic profiles (names, bios, avatars) using AI,
applies them to Telegram accounts, and manages personal channels.

SAFETY RULES enforced here:
1. NEVER change profile immediately after connecting — wait at least 12-24 h externally.
2. NEVER do multiple profile changes at once — space each step 30-60 s apart.
3. Handle is_frozen_error gracefully — mark account frozen, do not retry.
4. Each account MUST have its own unique proxy (enforced by SessionManager).
5. Use existing SessionManager — never create new Telethon connections.
6. All AI calls go through route_ai_task() — never call Gemini/OpenRouter directly.
"""

from __future__ import annotations

import asyncio
import io
import random
import string
from datetime import datetime, timezone
from typing import Any, Callable, Optional

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.account_capabilities import is_frozen_error
from core.ai_router import route_ai_task
from storage.models import Account, ProfileTemplate
from utils.helpers import utcnow
from utils.logger import log


# Delay range (seconds) between sequential Telegram profile update calls.
_STEP_DELAY_MIN = 30
_STEP_DELAY_MAX = 60

# Delay range (seconds) between accounts during mass operations.
_ACCOUNT_DELAY_MIN = 45
_ACCOUNT_DELAY_MAX = 90

# Maximum Telegram bio length in characters.
_BIO_MAX_LEN = 70


def _random_delay(min_s: int = _STEP_DELAY_MIN, max_s: int = _STEP_DELAY_MAX) -> float:
    return random.uniform(min_s, max_s)


def _random_username_suffix(length: int = 8) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=length))


class ProfileFactory:
    """Generates and applies AI-powered Telegram profiles to accounts."""

    def __init__(
        self,
        session_manager: Any,
        ai_router_func: Optional[Callable] = None,
        redis_client: Any = None,
    ) -> None:
        self.session_manager = session_manager
        # Allow injection of a custom router (useful in tests); default to route_ai_task.
        self._route = ai_router_func if ai_router_func is not None else route_ai_task
        self.redis_client = redis_client

    # ------------------------------------------------------------------
    # Profile generation
    # ------------------------------------------------------------------

    async def generate_profile(
        self,
        template_id: int,
        account_id: int,
        tenant_id: int,
        session: AsyncSession,
    ) -> dict:
        """Generate a profile dict for account_id using template template_id.

        Calls route_ai_task with task_type='profile_generation'.
        Returns a dict with keys: first_name, last_name, bio, username_suggestion.
        """
        template = await session.get(ProfileTemplate, template_id)
        if template is None or template.tenant_id != tenant_id:
            raise RuntimeError(
                f"profile_template_not_found: template_id={template_id} tenant_id={tenant_id}"
            )

        account = await self._load_account(account_id, session)
        if account is None:
            raise RuntimeError(f"account_not_found: account_id={account_id}")

        gender = template.gender or "any"
        geo = template.geo or "RU"
        bio_hint = template.bio_template or ""

        system_instruction = (
            "You are a profile generation agent. "
            "Create a realistic Telegram persona for a human-looking account. "
            "The persona must feel authentic and match the given gender and geography. "
            "Respond ONLY in JSON with the exact keys specified."
        )

        prompt = (
            f"Generate a Telegram profile.\n"
            f"Gender: {gender}\n"
            f"Geography: {geo}\n"
            f"Bio hint / template: {bio_hint}\n\n"
            f"Return JSON with these keys only:\n"
            f"  first_name  — string, 2-20 chars\n"
            f"  last_name   — string, 0-20 chars (may be empty)\n"
            f"  bio         — string, max {_BIO_MAX_LEN} chars, natural personal bio\n"
            f"  username_suggestion — string, lowercase latin+digits, 5-32 chars\n"
        )

        result = await self._route(
            session,
            task_type="profile_generation",
            prompt=prompt,
            system_instruction=system_instruction,
            tenant_id=tenant_id,
            surface="farm",
            max_output_tokens=300,
            temperature=0.7,
        )

        if not result.ok or result.parsed is None:
            raise RuntimeError(
                f"profile_generation_ai_failed: "
                f"reason={result.reason_code} outcome={result.outcome}"
            )

        parsed = result.parsed
        bio = (parsed.get("bio") or "")[:_BIO_MAX_LEN]

        return {
            "first_name": str(parsed.get("first_name", "User"))[:20],
            "last_name": str(parsed.get("last_name", ""))[:20],
            "bio": bio,
            "username_suggestion": str(
                parsed.get("username_suggestion", _random_username_suffix())
            )[:32],
        }

    # ------------------------------------------------------------------
    # Profile application
    # ------------------------------------------------------------------

    async def apply_profile(
        self,
        account_id: int,
        profile: dict,
        tenant_id: int,
        session: AsyncSession,
    ) -> dict:
        """Apply a profile dict to a Telegram account via Telethon.

        Applies changes sequentially with 30-60 s delays between each step.
        Aborts and marks account as frozen on is_frozen_error.

        Returns a result dict with 'success', 'steps', and optional 'error' keys.
        """
        account = await self._load_account(account_id, session)
        if account is None:
            return {"success": False, "error": "account_not_found"}
        if account.tenant_id != tenant_id:
            return {"success": False, "error": "tenant_mismatch"}

        client = self.session_manager.get_client(account.phone)
        if client is None or not client.is_connected():
            return {"success": False, "error": "account_not_connected"}

        steps: list[dict] = []

        # Step 1 — update first_name / last_name
        first_name = (profile.get("first_name") or "").strip()
        last_name = (profile.get("last_name") or "").strip()
        if first_name:
            step_result = await self._apply_name(client, first_name, last_name)
            steps.append({"step": "update_name", **step_result})
            if not step_result["ok"]:
                if step_result.get("frozen"):
                    await self._mark_account_frozen(account_id, session)
                return {"success": False, "steps": steps, "error": step_result.get("error")}

            await asyncio.sleep(_random_delay())

        # Step 2 — update bio
        bio = (profile.get("bio") or "").strip()
        if bio:
            step_result = await self._apply_bio(client, bio)
            steps.append({"step": "update_bio", **step_result})
            if not step_result["ok"]:
                if step_result.get("frozen"):
                    await self._mark_account_frozen(account_id, session)
                return {"success": False, "steps": steps, "error": step_result.get("error")}

            await asyncio.sleep(_random_delay())

        # Step 3 — upload avatar (optional)
        avatar_url = profile.get("avatar_url")
        if avatar_url:
            step_result = await self._apply_avatar(client, avatar_url)
            steps.append({"step": "upload_avatar", **step_result})
            if not step_result["ok"] and step_result.get("frozen"):
                await self._mark_account_frozen(account_id, session)
                return {"success": False, "steps": steps, "error": step_result.get("error")}

        return {"success": True, "steps": steps}

    # ------------------------------------------------------------------
    # Channel creation
    # ------------------------------------------------------------------

    async def create_and_pin_channel(
        self,
        account_id: int,
        template: dict,
        tenant_id: int,
        session: AsyncSession,
    ) -> dict:
        """Create a personal Telegram channel for account_id and pin it.

        Steps:
          1. Create channel with title from template.
          2. Set a random unique username.
          3. Upload channel avatar if avatar_url is present.
          4. Post first post text from template.
        Returns a dict with channel info or an error key.
        """
        account = await self._load_account(account_id, session)
        if account is None:
            return {"success": False, "error": "account_not_found"}
        if account.tenant_id != tenant_id:
            return {"success": False, "error": "tenant_mismatch"}

        client = self.session_manager.get_client(account.phone)
        if client is None or not client.is_connected():
            return {"success": False, "error": "account_not_connected"}

        title = (template.get("channel_name") or template.get("name") or "My Channel").strip()[:128]
        about = (template.get("channel_description") or "").strip()[:255]
        first_post = (template.get("channel_first_post") or "").strip()
        avatar_url = template.get("avatar_url")

        try:
            from telethon import functions as tl_functions
            from telethon.tl.types import InputChannel

            # Create the channel
            create_result = await client(
                tl_functions.channels.CreateChannelRequest(
                    title=title,
                    about=about,
                    megagroup=False,
                )
            )
            channel_obj = create_result.chats[0]
            log.info(
                f"ProfileFactory.create_and_pin_channel: created channel "
                f"id={channel_obj.id} title='{title}' for account_id={account_id}"
            )
        except Exception as exc:
            if is_frozen_error(exc):
                await self._mark_account_frozen(account_id, session)
                return {"success": False, "error": "account_frozen", "detail": str(exc)}
            log.error(f"ProfileFactory.create_and_pin_channel: CreateChannelRequest failed: {exc}")
            return {"success": False, "error": "create_channel_failed", "detail": str(exc)}

        await asyncio.sleep(_random_delay())

        # Set channel username
        username = f"nc_{_random_username_suffix(10)}"
        try:
            await client(
                tl_functions.channels.UpdateUsernameRequest(
                    channel=channel_obj, username=username
                )
            )
        except Exception as exc:
            # Username setting can fail (taken, reserved); log but do not abort.
            log.warning(
                f"ProfileFactory.create_and_pin_channel: UpdateUsernameRequest failed "
                f"(username={username}): {exc}"
            )
            username = None

        await asyncio.sleep(_random_delay())

        # Upload avatar if provided
        if avatar_url:
            try:
                await self._upload_channel_photo(client, channel_obj, avatar_url)
            except Exception as exc:
                log.warning(f"ProfileFactory.create_and_pin_channel: avatar upload failed: {exc}")
            await asyncio.sleep(_random_delay())

        # Post first post
        first_post_msg_id: Optional[int] = None
        if first_post:
            try:
                msg = await client.send_message(channel_obj, first_post)
                first_post_msg_id = msg.id
                log.info(
                    f"ProfileFactory.create_and_pin_channel: first post sent "
                    f"msg_id={first_post_msg_id}"
                )
            except Exception as exc:
                log.warning(
                    f"ProfileFactory.create_and_pin_channel: first post send failed: {exc}"
                )

        return {
            "success": True,
            "channel_id": channel_obj.id,
            "username": username,
            "title": title,
            "first_post_msg_id": first_post_msg_id,
        }

    # ------------------------------------------------------------------
    # Mass operations
    # ------------------------------------------------------------------

    async def mass_generate_profiles(
        self,
        account_ids: list[int],
        template_id: int,
        tenant_id: int,
        session: AsyncSession,
    ) -> list[dict]:
        """Generate and apply profiles for multiple accounts sequentially.

        Delays between accounts to avoid mass detection.
        Returns a list of per-account result dicts.
        """
        results: list[dict] = []
        for i, account_id in enumerate(account_ids):
            if i > 0:
                await asyncio.sleep(_random_delay(_ACCOUNT_DELAY_MIN, _ACCOUNT_DELAY_MAX))
            try:
                profile = await self.generate_profile(
                    template_id=template_id,
                    account_id=account_id,
                    tenant_id=tenant_id,
                    session=session,
                )
                apply_result = await self.apply_profile(
                    account_id=account_id,
                    profile=profile,
                    tenant_id=tenant_id,
                    session=session,
                )
                results.append(
                    {
                        "account_id": account_id,
                        "ok": apply_result.get("success", False),
                        "profile": profile,
                        "apply_result": apply_result,
                    }
                )
            except Exception as exc:
                log.error(
                    f"ProfileFactory.mass_generate_profiles: "
                    f"account_id={account_id} failed: {exc}"
                )
                results.append(
                    {
                        "account_id": account_id,
                        "ok": False,
                        "error": str(exc),
                    }
                )
        return results

    async def mass_create_channels(
        self,
        account_ids: list[int],
        template: dict,
        tenant_id: int,
        session: AsyncSession,
    ) -> list[dict]:
        """Create personal channels for multiple accounts sequentially.

        Delays between accounts to avoid mass detection.
        Returns a list of per-account result dicts.
        """
        results: list[dict] = []
        for i, account_id in enumerate(account_ids):
            if i > 0:
                await asyncio.sleep(_random_delay(_ACCOUNT_DELAY_MIN, _ACCOUNT_DELAY_MAX))
            try:
                result = await self.create_and_pin_channel(
                    account_id=account_id,
                    template=template,
                    tenant_id=tenant_id,
                    session=session,
                )
                results.append({"account_id": account_id, **result})
            except Exception as exc:
                log.error(
                    f"ProfileFactory.mass_create_channels: "
                    f"account_id={account_id} failed: {exc}"
                )
                results.append(
                    {
                        "account_id": account_id,
                        "success": False,
                        "error": str(exc),
                    }
                )
        return results

    # ------------------------------------------------------------------
    # Private Telethon helpers
    # ------------------------------------------------------------------

    async def _apply_name(
        self, client: Any, first_name: str, last_name: str
    ) -> dict:
        try:
            from telethon import functions as tl_functions

            await client(
                tl_functions.account.UpdateProfileRequest(
                    first_name=first_name,
                    last_name=last_name,
                )
            )
            return {"ok": True}
        except Exception as exc:
            frozen = is_frozen_error(exc)
            log.warning(f"ProfileFactory._apply_name failed: {exc} frozen={frozen}")
            return {"ok": False, "frozen": frozen, "error": str(exc)}

    async def _apply_bio(self, client: Any, bio: str) -> dict:
        try:
            from telethon import functions as tl_functions

            await client(
                tl_functions.account.UpdateProfileRequest(about=bio[:_BIO_MAX_LEN])
            )
            return {"ok": True}
        except Exception as exc:
            frozen = is_frozen_error(exc)
            log.warning(f"ProfileFactory._apply_bio failed: {exc} frozen={frozen}")
            return {"ok": False, "frozen": frozen, "error": str(exc)}

    async def _apply_avatar(self, client: Any, avatar_url: str) -> dict:
        try:
            image_bytes = await self._download_image(avatar_url)
            if not image_bytes:
                return {"ok": False, "error": "avatar_download_empty"}

            from telethon import functions as tl_functions

            file = await client.upload_file(io.BytesIO(image_bytes), file_name="avatar.jpg")
            await client(tl_functions.photos.UploadProfilePhotoRequest(file=file))
            return {"ok": True}
        except Exception as exc:
            frozen = is_frozen_error(exc)
            log.warning(f"ProfileFactory._apply_avatar failed: {exc} frozen={frozen}")
            return {"ok": False, "frozen": frozen, "error": str(exc)}

    async def _upload_channel_photo(
        self, client: Any, channel: Any, avatar_url: str
    ) -> None:
        from telethon import types as tl_types
        from telethon import functions as tl_functions

        image_bytes = await self._download_image(avatar_url)
        if not image_bytes:
            raise RuntimeError("avatar_download_empty")
        file = await client.upload_file(io.BytesIO(image_bytes), file_name="ch_avatar.jpg")
        # Use InputChatUploadedPhoto for channel photos (NOT UploadProfilePhotoRequest)
        await client(
            tl_functions.channels.EditPhotoRequest(
                channel=channel,
                photo=tl_types.InputChatUploadedPhoto(file=file),
            )
        )

    @staticmethod
    def _is_safe_url(url: str) -> bool:
        """Block SSRF: reject internal/private network URLs."""
        from urllib.parse import urlparse
        import ipaddress
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        hostname = parsed.hostname or ""
        if hostname in ("localhost", ""):
            return False
        try:
            ip = ipaddress.ip_address(hostname)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return False
        except ValueError:
            pass  # Not an IP literal — hostname is acceptable
        return True

    @staticmethod
    async def _download_image(url: str, timeout: float = 15.0, max_size: int = 10 * 1024 * 1024) -> Optional[bytes]:
        """Download image bytes from a URL, returns None on failure. Max 10MB."""
        if not ProfileFactory._is_safe_url(url):
            log.warning("ProfileFactory._download_image: blocked unsafe URL")
            return None
        try:
            async with httpx.AsyncClient(timeout=timeout) as http:
                resp = await http.get(url, follow_redirects=False)
                resp.raise_for_status()
                if len(resp.content) > max_size:
                    log.warning(f"ProfileFactory._download_image: too large ({len(resp.content)} bytes)")
                    return None
                content_type = resp.headers.get("content-type", "")
                if content_type and not content_type.startswith("image/"):
                    log.warning(f"ProfileFactory._download_image: not an image ({content_type})")
                    return None
                return resp.content
        except Exception as exc:
            log.warning(f"ProfileFactory._download_image failed url={url}: {exc}")
            return None

    # ------------------------------------------------------------------
    # Private DB helpers
    # ------------------------------------------------------------------

    @staticmethod
    async def _load_account(
        account_id: int, session: AsyncSession, *, tenant_id: Optional[int] = None
    ) -> Optional[Account]:
        stmt = select(Account).where(Account.id == account_id)
        if tenant_id is not None:
            stmt = stmt.where(Account.tenant_id == tenant_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def _mark_account_frozen(account_id: int, session: AsyncSession) -> None:
        """Mark account.restriction_reason = 'frozen' and status = 'cooldown'."""
        try:
            await session.execute(
                update(Account)
                .where(Account.id == account_id)
                .values(
                    restriction_reason="frozen",
                    status="cooldown",
                    last_violation_at=utcnow(),
                )
            )
            await session.commit()
            log.warning(f"ProfileFactory: account_id={account_id} marked as frozen")
        except Exception as exc:
            log.error(f"ProfileFactory._mark_account_frozen failed: {exc}")
