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
from utils.logger import log

from sqlalchemy import update

from telethon.tl.functions.account import SetPrivacyRequest, UpdateProfileRequest
from telethon.tl.functions.photos import UploadProfilePhotoRequest
from telethon.tl.types import (
    InputPrivacyKeyPhoneNumber,
    InputPrivacyValueDisallowAll,
    InputPrivacyValueAllowContacts,
)


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
    return clamped * 60


class PackagerWorker:
    """Autonomous account provisioning pipeline."""

    def __init__(self):
        self.session_mgr = SessionManager()
        self.proxy_mgr = ProxyManager()
        self._running = False

    async def start(self):
        """Main entry point — listen to Redis queue."""
        log.info("PackagerWorker: starting...")
        await init_db()
        await task_queue.connect()
        await redis_state.connect()
        self.proxy_mgr.load_from_file()
        self._running = True

        log.info("PackagerWorker: listening for packaging tasks...")
        while self._running:
            task = await task_queue.dequeue("packaging:pending", timeout=10)
            if task is None:
                continue

            phone = task.get("phone")
            if not phone:
                log.warning("PackagerWorker: task missing 'phone'")
                continue

            log.info(f"PackagerWorker: starting pipeline for {phone}")
            try:
                await self._publish_progress(phone, "Подключаюсь...")
                await self.package_account(phone, task)
                await self._publish_progress(phone, "Готов к работе!")
            except Exception as exc:
                log.error(f"PackagerWorker: pipeline failed for {phone}: {exc}")
                await self._publish_progress(phone, f"Ошибка: {exc}")

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
        # Load metadata
        meta_path = BASE_DIR / "data" / "sessions" / f"{phone}.json"
        if not meta_path.exists():
            raise FileNotFoundError(f"Metadata not found: {meta_path}")

        with open(meta_path) as f:
            meta = json.load(f)

        first_name = meta.get("first_name", "User")
        gender = task.get("gender") or detect_gender(first_name)
        log.info(f"PackagerWorker: {phone} — {first_name} ({gender})")

        # Step 1: Connect
        proxy = self.proxy_mgr.assign_to_account(phone)
        client = await self.session_mgr.connect_client(phone, proxy=proxy)
        if not client:
            raise RuntimeError("Failed to connect")

        me = await client.get_me()
        if not me:
            raise RuntimeError("get_me() returned None — dead session")

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
        await self._create_channel(phone)

        # Step 6: Mark ready in DB
        async with async_session() as session:
            await session.execute(
                update(Account).where(Account.phone == phone).values(
                    status="active",
                    health_status="alive",
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

    async def _create_channel(self, phone: str):
        """Create redirect channel using existing ChannelSetup."""
        try:
            from core.account_manager import AccountManager
            account_mgr = AccountManager(self.session_mgr, self.proxy_mgr)

            from utils.channel_setup import ChannelSetup
            channel_setup = ChannelSetup(self.session_mgr, account_mgr)

            result = await channel_setup.create_redirect_channel(phone)
            if result["success"]:
                log.info(f"{phone}: channel created — {result['channel_link']}")
            else:
                log.warning(f"{phone}: channel creation failed — {result.get('error')}")
        except Exception as exc:
            log.warning(f"{phone}: channel creation error: {exc}")

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
