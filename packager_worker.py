"""
Packager Worker — autonomous account provisioning pipeline.

Listens to Redis queue 'packaging:pending'.
When a new account is uploaded, automatically:
1. Connect + verify alive
2. Hide phone number
3. Generate + set avatar (male/female based on name)
4. Set bio
5. Create redirect channel + post + avatar + pin
6. Set personal channel in profile
7. Mark account as ready

All operations have Gaussian delays for anti-fraud.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from config import settings, BASE_DIR
from core.task_queue import task_queue
from core.redis_state import redis_state
from storage.sqlite_db import init_db, async_session
from storage.models import Account
from core.session_manager import SessionManager
from core.proxy_manager import ProxyManager
from core.rate_limiter import RateLimiter
from core.account_manager import AccountManager
from core.policy_engine import policy_engine
from core.account_capabilities import (
    probe_account_capabilities,
    persist_probe_result,
)
from utils.logger import log
from utils.helpers import utcnow
from utils.proxy_bindings import get_bound_proxy_config

from sqlalchemy import update, select

from telethon.tl.functions.account import SetPrivacyRequest, UpdateProfileRequest
from telethon.tl.functions.photos import UploadProfilePhotoRequest
from telethon.tl.types import (
    InputPrivacyKeyPhoneNumber,
    InputPrivacyValueDisallowAll,
    InputPrivacyValueAllowContacts,
)


PACKAGING_LEASE_SEC = 900
PACKAGING_MAX_ATTEMPTS = 2


# ─── Gender detection for Russian names ───────────────────────
FEMALE_ENDINGS = {"а", "я", "ья"}
MALE_NAMES = {
    "никита", "илья", "лука", "фома", "кузьма", "савва", "данила",
}
FEMALE_NAMES = {
    "алина", "анна", "мария", "екатерина", "ольга", "наталья", "елена",
    "ирина", "татьяна", "светлана", "юлия", "дарья", "полина", "софья",
    "виктория", "кристина", "настя", "ксения", "диана", "валерия",
    "александра", "вероника", "арина", "милана", "таисия", "алёна",
    "карина", "ruth", "susan",
}

def detect_gender(first_name: str) -> str:
    """Detect gender from Russian first name. Returns 'male' or 'female'."""
    name = first_name.strip().lower()
    if name in FEMALE_NAMES:
        return "female"
    if name in MALE_NAMES:
        return "male"
    # Heuristic: Russian female names typically end in а/я
    if name and name[-1] in {"а", "я"} and name not in MALE_NAMES:
        return "female"
    return "male"


# ─── Avatar prompts ───────────────────────────────────────────
MALE_AVATAR_PROMPTS = [
    "A handsome young man in his mid-20s with short dark hair and light stubble, wearing a casual grey hoodie, holding a modern smartphone, smiling confidently at camera. Background is a soft blurred modern office with warm lighting. Realistic portrait photo, natural skin, warm tones.",
    "Athletic young man, 25, short brown hair, wearing a white t-shirt, taking a selfie at a sunny beach. Natural tan, blue ocean background. Realistic portrait photo, summer vibes, warm color tones.",
    "A confident young man, 27, with neat dark hair, wearing a navy blazer over a casual shirt, photographed in a stylish cafe. Warm ambient light, bokeh background. Realistic portrait, natural look.",
    "A friendly young man, 24, with wavy brown hair, wearing a casual denim jacket, taking a selfie on a city street with autumn leaves. Golden hour lighting, warm tones. Realistic portrait photo.",
    "A sporty young man, 26, with short dark hair, wearing a black polo shirt, photographed on a rooftop terrace with city skyline. Sunset golden light, warm tones. Realistic portrait photo.",
    "A charming young man, 25, with light brown hair and green eyes, wearing a light sweater, photographed in a park with green trees. Natural daylight, warm tones. Realistic portrait photo.",
    "A stylish young man, 28, with neat dark hair, wearing a casual linen shirt, photographed near a modern building. Soft afternoon light, warm tones. Realistic portrait photo.",
    "A relaxed young man, 23, with curly dark hair, wearing an oversized hoodie, photographed in a cozy apartment with plants. Soft window light, warm tones. Realistic portrait photo.",
    "A confident young man, 26, with short blonde hair, wearing a grey crewneck, taking a selfie at a modern workspace. Natural light from large windows. Realistic portrait photo.",
    "A friendly young man, 25, with dark hair and a warm smile, wearing a casual flannel shirt, photographed at a mountain viewpoint. Golden hour, panoramic background. Realistic portrait photo.",
]

# Female prompts are in utils/account_packager.py AVATAR_PROMPTS


MALE_BIOS = [
    "IT и полезные сервисы",
    "Технологии, цифровая жизнь",
    "Digital лайфхаки и обзоры",
    "Нейросети, VPN, полезности",
    "IT-специалист | Лайфхаки",
    "Люблю технологии и интернет",
    "Цифровая свобода и приватность",
    "Обзоры полезных сервисов",
    "Интернет без границ",
    "Tech-энтузиаст",
]

FEMALE_BIOS = [
    "Делюсь полезностями",
    "Лайфхаки и красота",
    "Технологии для жизни",
    "Интернет и лайфхаки",
    "Полезные находки каждый день",
    "Digital жизнь и советы",
    "Всё интересное и полезное",
    "Стиль, технологии, жизнь",
    "Онлайн-находки",
    "Красота и технологии",
]


def gaussian_delay(mean_min: float = 20, std_min: float = 5,
                    min_min: float = 10, max_min: float = 45) -> float:
    """Gaussian delay in seconds, clamped to [min, max] minutes."""
    raw = random.gauss(mean_min, std_min)
    clamped = max(min_min, min(max_min, raw))
    scale = max(0.1, float(settings.PACKAGING_DELAY_SCALE))
    seconds = clamped * 60 * scale
    min_sec = max(1, int(settings.PACKAGING_DELAY_MIN_SEC))
    max_sec = max(min_sec, int(settings.PACKAGING_DELAY_MAX_SEC))
    return max(min_sec, min(max_sec, seconds))


class PackagerWorker:
    """Autonomous account provisioning pipeline."""

    def __init__(self):
        self.session_mgr = SessionManager()
        self.proxy_mgr = ProxyManager()
        self.rate_limiter = RateLimiter()
        self.account_mgr = AccountManager(
            session_manager=self.session_mgr,
            proxy_manager=self.proxy_mgr,
            rate_limiter=self.rate_limiter,
        )
        self._running = False

    @staticmethod
    def _normalize_phone(raw_phone: str) -> str:
        digits = "".join(ch for ch in str(raw_phone) if ch.isdigit())
        return f"+{digits}" if digits else ""

    @staticmethod
    def _session_stem_candidates(phone: str, session_file: str | None = None) -> list[str]:
        stems: list[str] = []
        if session_file:
            stems.append(Path(session_file).stem)
        digits = "".join(ch for ch in phone if ch.isdigit())
        if digits:
            stems.append(digits)
        plain = phone.lstrip("+")
        if plain:
            stems.append(plain)
        # Keep order, remove duplicates
        seen: set[str] = set()
        unique: list[str] = []
        for stem in stems:
            if stem and stem not in seen:
                seen.add(stem)
                unique.append(stem)
        return unique

    async def _load_account(self, phone: str) -> Account | None:
        async with async_session() as session:
            result = await session.execute(
                select(Account).where(Account.phone == phone)
            )
            return result.scalar_one_or_none()

    async def _update_lifecycle(
        self,
        phone: str,
        stage: str,
        *,
        status: str | None = None,
        health_status: str | None = None,
    ):
        values = {"lifecycle_stage": stage}
        if status is not None:
            values["status"] = status
        if health_status is not None:
            values["health_status"] = health_status
        async with async_session() as session:
            await session.execute(
                update(Account)
                .where(Account.phone == phone)
                .values(**values)
            )
            await session.commit()

    def _resolve_metadata_path(self, phone: str, account: Account | None, task: dict) -> Path:
        """Resolve metadata json path from DB session_file and phone fallback."""
        session_file = (task.get("session_file") or (account.session_file if account else None))
        stems = self._session_stem_candidates(phone, session_file=session_file)
        search_dirs: list[Path] = []
        if account and account.user_id is not None:
            search_dirs.append(BASE_DIR / "data" / "sessions" / str(account.user_id))
        search_dirs.append(BASE_DIR / "data" / "sessions")

        for base in search_dirs:
            for stem in stems:
                candidate = base / f"{stem}.json"
                if candidate.exists():
                    return candidate

        # Return the most likely path for readable error output
        fallback_stem = stems[0] if stems else phone.lstrip("+")
        return (search_dirs[0] if search_dirs else (BASE_DIR / "data" / "sessions")) / f"{fallback_stem}.json"

    async def _reconnect_backends(self):
        """Reconnect Redis backends after transient network/container restarts."""
        try:
            await task_queue.close()
        except Exception:
            pass
        try:
            await redis_state.close()
        except Exception:
            pass

        await asyncio.sleep(2)

        await task_queue.connect()
        await redis_state.connect()

    async def start(self):
        """Main entry point — listen to Redis queue."""
        log.info("PackagerWorker: starting...")
        await init_db()
        await task_queue.connect()
        await redis_state.connect()
        self.proxy_mgr.load_from_file()
        self._running = True

        if settings.HUMAN_GATED_PACKAGING:
            log.info("PackagerWorker: human-gated packaging enabled, legacy autonomous pipeline is disabled")
            while self._running:
                task = await task_queue.reserve(
                    "packaging:pending",
                    consumer_id="packager",
                    timeout=10,
                    lease_sec=PACKAGING_LEASE_SEC,
                )
                if not task:
                    continue
                task_id = str(task.get("_task_id") or "")
                if task_id:
                    await task_queue.dead_letter(
                        "packaging:pending",
                        task_id,
                        task,
                        reason="human_gated_packaging_enabled",
                    )
            return

        log.info("PackagerWorker: listening for packaging tasks...")
        while self._running:
            try:
                task = await task_queue.reserve(
                    "packaging:pending",
                    consumer_id="packager",
                    timeout=10,
                    lease_sec=PACKAGING_LEASE_SEC,
                )
            except Exception as exc:
                log.warning(f"PackagerWorker: dequeue failed ({exc}), reconnecting backends...")
                try:
                    await self._reconnect_backends()
                except Exception as reconnect_exc:
                    log.error(f"PackagerWorker: backend reconnect failed: {reconnect_exc}")
                    await asyncio.sleep(5)
                continue

            if task is None:
                continue

            task_id = str(task.get("_task_id") or "")
            phone = task.get("phone")
            if not phone:
                log.warning("PackagerWorker: task missing 'phone'")
                if task_id:
                    await task_queue.dead_letter(
                        "packaging:pending",
                        task_id,
                        task,
                        reason="missing_phone",
                    )
                continue
            phone = self._normalize_phone(phone)
            if not phone:
                log.warning(f"PackagerWorker: invalid phone in task: {task}")
                if task_id:
                    await task_queue.dead_letter(
                        "packaging:pending",
                        task_id,
                        task,
                        reason="invalid_phone",
                    )
                continue

            log.info(f"PackagerWorker: starting pipeline for {phone}")
            try:
                await self._update_lifecycle(phone, "packaging")
                await self._publish_progress(phone, "Подключаюсь...")
                await self.package_account(phone, task)
                await self._publish_progress(phone, "Готов к работе!")
                if task_id:
                    await task_queue.ack("packaging:pending", task_id)
            except Exception as exc:
                log.error(f"PackagerWorker: pipeline failed for {phone}: {exc}")
                await self._update_lifecycle(phone, "packaging_error")
                await self._publish_progress(phone, f"Ошибка: {exc}")
                attempts = int(task.get("_attempts", 0)) + 1
                payload = dict(task)
                payload["_attempts"] = attempts
                fatal_markers = (
                    "Policy blocked packaging",
                    "Metadata not found",
                    "Account not found",
                    "Strict proxy mode",
                    "frozen_probe_failed",
                )
                if task_id and attempts <= PACKAGING_MAX_ATTEMPTS and not any(
                    marker in str(exc) for marker in fatal_markers
                ):
                    await task_queue.requeue(
                        "packaging:pending",
                        task_id,
                        payload,
                        reason=f"retry_after_error:{type(exc).__name__}",
                    )
                elif task_id:
                    await task_queue.dead_letter(
                        "packaging:pending",
                        task_id,
                        payload,
                        reason=f"packaging_failed:{type(exc).__name__}",
                    )

    async def stop(self):
        self._running = False
        await task_queue.close()
        await redis_state.close()

    async def _publish_progress(self, phone: str, message: str):
        """Send progress update via Redis PUB/SUB."""
        try:
            await task_queue.publish("packaging:progress", {
                "phone": phone,
                "message": message,
            })
        except Exception:
            pass

    async def package_account(self, phone: str, task: dict):
        """Full packaging pipeline."""
        account = await self._load_account(phone)
        if account is None:
            raise RuntimeError("Account not found in DB")
        decision = await policy_engine.check(
            "packaging_account_candidate",
            {
                "account": {
                    "phone": account.phone,
                    "health_status": account.health_status,
                    "lifecycle_stage": account.lifecycle_stage,
                    "status": account.status,
                }
            },
            phone=phone,
            worker_id="packager",
        )
        if decision.action in {"block", "quarantine"}:
            raise RuntimeError(
                f"Policy blocked packaging ({decision.rule_id}, action={decision.action})"
            )

        user_id = task.get("user_id")
        if user_id is None and account is not None:
            user_id = account.user_id
        if account.user_id is not None and user_id is not None and int(account.user_id) != int(user_id):
            raise RuntimeError(
                f"Ownership mismatch: account.user_id={account.user_id} task.user_id={user_id}"
            )
        if user_id is None:
            raise RuntimeError("Ownership missing: account.user_id is not set")

        # Load metadata
        meta_path = self._resolve_metadata_path(phone, account, task)
        if not meta_path.exists():
            raise FileNotFoundError(f"Metadata not found: {meta_path}")

        with open(meta_path) as f:
            meta = json.load(f)

        first_name = meta.get("first_name", "User")
        gender = task.get("gender") or detect_gender(first_name)
        log.info(f"PackagerWorker: {phone} — {first_name} ({gender})")

        # Step 1: Connect
        proxy = await get_bound_proxy_config(phone)
        if proxy is None and not settings.STRICT_PROXY_PER_ACCOUNT:
            proxy = self.proxy_mgr.assign_to_account(phone)
        if settings.STRICT_PROXY_PER_ACCOUNT and proxy is None:
            await policy_engine.check(
                "proxy_assignment",
                {"strict_proxy": True, "proxy_assigned": False, "phone": phone},
                phone=phone,
                worker_id="packager",
            )
            raise RuntimeError("Strict proxy mode: no unique proxy assigned")
        client = await self.session_mgr.connect_client(phone, proxy=proxy, user_id=user_id)
        if not client:
            raise RuntimeError("Failed to connect")

        me = await client.get_me()
        if not me:
            raise RuntimeError("get_me() returned None — dead session")

        if settings.FROZEN_PROBE_BEFORE_PACKAGING:
            probe = await probe_account_capabilities(client, run_search_probe=True)
            reason = str(probe.get("reason", "ok"))
            mark_restricted = reason in {"frozen", "restricted"}
            await persist_probe_result(
                phone,
                probe,
                mark_restricted_on_failure=mark_restricted,
                restriction_reason=reason if mark_restricted else None,
            )
            if mark_restricted or not probe.get("can_profile_edit", False) or not probe.get("can_create_channel", False):
                await policy_engine.check(
                    "frozen_probe_failed",
                    {
                        "phone": phone,
                        "reason": reason,
                        "capabilities": probe,
                        "source": "packager",
                    },
                    phone=phone,
                    worker_id="packager",
                )
                raise RuntimeError(
                    f"frozen_probe_failed: account capability check blocked packaging (reason={reason})"
                )

        # Check frozen
        bv = getattr(me, "bot_verification", None)
        if bv:
            raise RuntimeError(f"Account FROZEN: {bv}")

        await self._publish_progress(phone, f"Подключён: {me.first_name}")

        # Step 2: Hide phone (short delay first)
        await asyncio.sleep(gaussian_delay(mean_min=8, std_min=3, min_min=3, max_min=15))
        await self._publish_progress(phone, "Скрываю номер...")
        await self._hide_phone(client, phone)

        # Step 3: Generate + set avatar
        await asyncio.sleep(gaussian_delay(mean_min=15, std_min=5, min_min=8, max_min=30))
        await self._publish_progress(phone, "Генерирую аватарку...")
        await self._set_avatar(client, phone, gender)

        # Step 4: Set bio
        await asyncio.sleep(gaussian_delay(mean_min=10, std_min=3, min_min=5, max_min=20))
        await self._publish_progress(phone, "Устанавливаю био...")
        await self._set_bio(client, phone, gender)

        # Step 5: Create channel + post + pin
        await asyncio.sleep(gaussian_delay(mean_min=20, std_min=5, min_min=10, max_min=40))
        await self._publish_progress(phone, "Создаю канал...")
        channel_result = await self._create_channel(phone)
        if not channel_result.get("success"):
            raise RuntimeError(
                f"Channel setup failed: {channel_result.get('error') or 'unknown error'}"
            )
        if not bool(channel_result.get("personal_channel_set")):
            raise RuntimeError(
                "Personal channel widget not set; lifecycle stays packaging_error "
                "(PACKAGING_ALLOW_BIO_FALLBACK=false policy)."
            )

        # Step 6: Mark ready in DB
        async with async_session() as session:
            await session.execute(
                update(Account).where(Account.phone == phone).values(
                    status="active",
                    health_status="alive",
                    lifecycle_stage="warming_up",
                    last_active_at=utcnow(),
                )
            )
            await session.commit()

        # Step 7: Backup session
        await self._backup_session(client, phone)

        # Disconnect (NOT log_out!)
        await self.session_mgr.disconnect_client(phone)
        log.info(f"PackagerWorker: {phone} packaging complete!")

    async def _hide_phone(self, client, phone: str):
        """Hide phone number from non-contacts."""
        try:
            await client(SetPrivacyRequest(
                key=InputPrivacyKeyPhoneNumber(),
                rules=[
                    InputPrivacyValueAllowContacts(),
                    InputPrivacyValueDisallowAll(),
                ],
            ))
            log.info(f"{phone}: phone number hidden")
        except Exception as exc:
            log.warning(f"{phone}: failed to hide phone: {exc}")

    async def _set_avatar(self, client, phone: str, gender: str):
        """Generate and set profile avatar."""
        try:
            from config import settings as cfg
            if not cfg.GEMINI_API_KEY:
                log.warning(f"{phone}: no GEMINI_API_KEY, skipping avatar")
                return

            from google import genai
            from google.genai import types

            prompts = MALE_AVATAR_PROMPTS if gender == "male" else None
            if prompts is None:
                # Use female prompts from account_packager
                from utils.account_packager import AVATAR_PROMPTS
                prompts = AVATAR_PROMPTS

            prompt = random.choice(prompts)
            ai_client = genai.Client(api_key=cfg.GEMINI_API_KEY)

            response = await asyncio.to_thread(
                ai_client.models.generate_images,
                model="imagen-4.0-generate-001",
                prompt=prompt,
                config=types.GenerateImagesConfig(number_of_images=1),
            )

            if not response or not response.generated_images:
                log.warning(f"{phone}: Imagen returned empty response")
                return

            avatar_dir = BASE_DIR / "data" / "avatars" / "profiles"
            avatar_dir.mkdir(parents=True, exist_ok=True)
            avatar_path = avatar_dir / f"avatar_{phone}.png"
            response.generated_images[0].image.save(str(avatar_path))

            file = await client.upload_file(str(avatar_path))
            await client(UploadProfilePhotoRequest(file=file))
            log.info(f"{phone}: avatar set ({gender})")

        except Exception as exc:
            log.warning(f"{phone}: avatar generation failed: {exc}")

    async def _set_bio(self, client, phone: str, gender: str):
        """Set profile bio."""
        try:
            bios = MALE_BIOS if gender == "male" else FEMALE_BIOS
            bio = random.choice(bios)
            await client(UpdateProfileRequest(about=bio[:70]))
            log.info(f"{phone}: bio set — {bio}")
        except Exception as exc:
            log.warning(f"{phone}: failed to set bio: {exc}")

    async def _create_channel(self, phone: str) -> dict:
        """Create redirect channel using existing ChannelSetup."""
        try:
            from utils.channel_setup import ChannelSetup
            channel_setup = ChannelSetup(self.session_mgr, self.account_mgr)

            result = await channel_setup.create_redirect_channel(phone)
            if result["success"]:
                log.info(
                    f"{phone}: channel created — {result['channel_link']} | "
                    f"personal_channel_set={result.get('personal_channel_set')} | "
                    f"bio_fallback_used={result.get('bio_fallback_used')}"
                )
            else:
                log.warning(f"{phone}: channel creation failed — {result.get('error')}")
            return result
        except Exception as exc:
            log.warning(f"{phone}: channel creation error: {exc}")
            return {
                "success": False,
                "error": str(exc),
                "channel_link": "",
                "personal_channel_set": False,
                "bio_fallback_used": False,
            }

    async def _backup_session(self, client, phone: str):
        """Export StringSession backup."""
        try:
            from telethon.sessions import StringSession
            string_session = StringSession.save(client.session)
            backup_dir = BASE_DIR / "data" / "session_backups"
            backup_dir.mkdir(parents=True, exist_ok=True)
            (backup_dir / f"{phone}.backup").write_text(string_session)
            log.info(f"{phone}: session backup saved")
        except Exception as exc:
            log.warning(f"{phone}: backup failed: {exc}")


async def main():
    worker = PackagerWorker()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(worker.stop()))

    await worker.start()


if __name__ == "__main__":
    asyncio.run(main())
