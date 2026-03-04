"""
StoryManager — post stories to keep accounts alive and look natural.

Generates cute animal/nature images via Gemini Imagen and posts as Telegram stories.
Each account gets a story every 24-72h with Gaussian timing.
"""

from __future__ import annotations

import asyncio
import random
from pathlib import Path
from typing import Optional

from config import settings, BASE_DIR
from utils.logger import log


STORY_PROMPTS = [
    "A cute fluffy orange tabby cat sleeping on a sunny windowsill with warm cozy atmosphere, soft light, closeup photo",
    "A golden retriever puppy playing in colorful autumn leaves in a park, joyful, natural light, candid photo",
    "A tiny hedgehog sitting in a cup, adorable, warm brown tones, studio photo with soft background",
    "A beautiful sunset over calm ocean water with pink and orange clouds, serene landscape photo",
    "A fluffy white bunny in a green meadow with wildflowers, spring daylight, cute animal photo",
    "A sleeping grey kitten curled up in a soft knitted blanket, cozy atmosphere, warm tones, closeup",
    "A corgi puppy running on a sandy beach at golden hour, happy expression, action shot, warm tones",
    "A colorful parrot sitting on a branch with tropical flowers, vibrant colors, nature photo",
    "A baby deer (fawn) standing in a forest clearing with morning mist, magical atmosphere",
    "A cute otter floating on its back in calm water, holding hands with another otter, adorable",
    "A fluffy samoyed dog smiling in snow, winter wonderland, bright daylight, happy pet photo",
    "A stack of colorful macarons on a marble table with fresh flowers, aesthetic food photo",
    "A hot cup of coffee with latte art next to an open book and autumn leaves, cozy vibes",
    "A beautiful northern lights aurora borealis over a snowy mountain landscape, night sky",
    "A tiny hamster eating a tiny piece of broccoli, adorable closeup, soft studio lighting",
    "A field of lavender flowers stretching to the horizon, purple haze, golden hour, landscape",
    "A playful bengal cat catching a butterfly in a garden, action shot, natural sunlight",
    "Cherry blossom trees in full bloom along a peaceful path, spring atmosphere, soft pink",
    "A baby elephant playing in water, splashing, joyful, African savanna background",
    "A cozy reading nook with fairy lights, stack of books, and a sleeping cat, warm atmosphere",
]

STORY_CAPTIONS = [
    "",  # No caption — most natural
    "",
    "",
    "",
    "",
    "Просто красота",
    "Настроение",
    "Хороший день",
    "Вот так вот",
    "Утро доброе",
]


class StoryManager:
    """Post stories to keep accounts alive and look natural."""

    def __init__(self):
        self._ai_client = None
        self._story_dir = BASE_DIR / "data" / "stories"
        self._story_dir.mkdir(parents=True, exist_ok=True)

    def _get_ai_client(self):
        """Lazy init Gemini client."""
        if self._ai_client is None and settings.GEMINI_API_KEY:
            from google import genai
            self._ai_client = genai.Client(api_key=settings.GEMINI_API_KEY)
        return self._ai_client

    async def generate_story_image(self) -> Optional[Path]:
        """Generate a cute image for story via Gemini Imagen."""
        client = self._get_ai_client()
        if not client:
            log.debug("StoryManager: no GEMINI_API_KEY, skipping")
            return None

        prompt = random.choice(STORY_PROMPTS)
        save_path = self._story_dir / f"story_{random.randint(10000, 99999)}.png"

        try:
            from google.genai import types

            response = await asyncio.to_thread(
                client.models.generate_images,
                model="imagen-4.0-generate-001",
                prompt=prompt,
                config=types.GenerateImagesConfig(number_of_images=1),
            )

            if not response or not response.generated_images:
                log.warning("StoryManager: Imagen returned empty response")
                return None

            response.generated_images[0].image.save(str(save_path))
            log.debug(f"StoryManager: image generated — {save_path.name}")
            return save_path

        except Exception as exc:
            log.warning(f"StoryManager: image generation failed: {exc}")
            return None

    async def post_story(self, client, phone: str) -> bool:
        """Post a story to the account."""
        image_path = await self.generate_story_image()
        if not image_path:
            return False

        try:
            from telethon.tl.functions.stories import SendStoryRequest
            from telethon.tl.types import (
                InputMediaUploadedPhoto,
                InputPeerSelf,
                MediaAreaGeoPoint,
            )

            file = await client.upload_file(str(image_path))

            caption = random.choice(STORY_CAPTIONS)

            await client(SendStoryRequest(
                peer=InputPeerSelf(),
                media=InputMediaUploadedPhoto(file=file),
                message=caption,
                privacy_rules=[],  # Default privacy
                period=86400,  # 24 hours
                noforwards=False,
                pinned=False,
            ))

            log.info(f"{phone}: story posted")

            # Cleanup image file
            try:
                image_path.unlink()
            except OSError:
                pass

            return True

        except Exception as exc:
            error_str = str(exc)
            if "STORIES_TOO_MUCH" in error_str:
                log.debug(f"{phone}: too many stories, skipping")
            elif "PREMIUM_ACCOUNT_REQUIRED" in error_str:
                log.debug(f"{phone}: stories require premium, skipping")
            else:
                log.warning(f"{phone}: story posting failed: {exc}")
            return False

    async def schedule_stories_loop(self, session_mgr, phones: list[str]):
        """Background loop: post stories for accounts on schedule."""
        log.info(f"StoryManager: starting story loop for {len(phones)} accounts")

        while True:
            random.shuffle(phones)

            for phone in phones:
                # Skip night hours
                from utils.anti_ban import AntibanManager
                if not AntibanManager.is_active_hours():
                    break

                client = session_mgr.get_client(phone)
                if not client or not client.is_connected():
                    continue

                # 30% chance to post story in this cycle
                if random.random() > 0.3:
                    continue

                await self.post_story(client, phone)
                # Delay between accounts
                await asyncio.sleep(random.uniform(300, 1800))

            # Wait 8-24 hours before next cycle
            wait_hours = random.gauss(16, 4)
            wait_hours = max(8, min(24, wait_hours))
            log.debug(f"StoryManager: next cycle in {wait_hours:.1f}h")
            await asyncio.sleep(wait_hours * 3600)
