"""
Создание канала-переходника с рекламным постом продукта.
Запуск: python create_channel.py <phone>
Пример: python create_channel.py 79637415377
"""

from __future__ import annotations

import asyncio
import random
import sys
from pathlib import Path

from telethon.tl.functions.channels import CreateChannelRequest, EditPhotoRequest
from telethon.tl.functions.messages import (
    ExportChatInviteRequest,
    UpdatePinnedMessageRequest,
)
from telethon.tl.functions.account import UpdateProfileRequest
from telethon.tl.types import InputChatUploadedPhoto

from config import settings, BASE_DIR
from utils.standalone_helpers import load_proxy_for_phone, load_account_json, build_client
from utils.channel_setup import get_fallback_channels, prepare_square_avatar

AVATAR_PATH = BASE_DIR / settings.PRODUCT_AVATAR_PATH


def _get_content() -> dict:
    """Получить контент из первого шаблона channel_setup (актуальный по текущим settings)."""
    channels = get_fallback_channels()
    return channels[0]


async def create_channel(phone: str):
    data = load_account_json(phone)
    proxy = load_proxy_for_phone(phone)

    # Получаем контент динамически (актуальный по текущим settings)
    content = _get_content()
    channel_name = content["name"]
    channel_desc = content["desc"]
    post_text = content["post"]

    print(f"\n  === Создание канала-переходника для +{phone} ===")
    print(f"  Прокси: {proxy[1]}:{proxy[2]}")
    print(f"  {settings.PRODUCT_NAME} ссылка: {settings.PRODUCT_BOT_LINK}")

    client = build_client(phone, data, proxy)

    bio_updated = False

    try:
        print("\n  1. Подключение...")
        await client.connect()

        if not await client.is_user_authorized():
            print("  ОШИБКА: сессия не авторизована!")
            return

        me = await client.get_me()
        name = f"{me.first_name or ''} {me.last_name or ''}".strip()
        print(f"  Подключён как: {name} (+{phone})")

        # ─── Шаг 2: Создаём канал ───
        print(f"\n  2. Создаю канал «{channel_name}»...")
        result = await client(CreateChannelRequest(
            title=channel_name,
            about=channel_desc[:255],
            broadcast=True,
        ))

        channel = result.chats[0]
        channel_id = channel.id
        print(f"  Канал создан! ID: {channel_id}")

        # Антибан пауза
        await asyncio.sleep(random.uniform(2.0, 4.0))

        entity = await client.get_entity(channel_id)

        # ─── Шаг 3: Аватарка продукта (квадратная) ───
        if AVATAR_PATH.exists():
            print(f"\n  3. Ставлю аватарку {settings.PRODUCT_NAME} (квадратную)...")
            try:
                square_path = prepare_square_avatar(AVATAR_PATH)
                file = await client.upload_file(str(square_path))
                await client(EditPhotoRequest(
                    channel=entity,
                    photo=InputChatUploadedPhoto(file=file),
                ))
                print("  Аватарка установлена (квадратная)!")
            except Exception as exc:
                print(f"  Не удалось установить аватарку: {exc}")
        else:
            print(f"\n  3. Аватарка не найдена: {AVATAR_PATH}")

        await asyncio.sleep(random.uniform(1.0, 3.0))

        # ─── Шаг 4: Публикуем рекламный пост ───
        print(f"\n  4. Публикую рекламный пост {settings.PRODUCT_NAME}...")
        sent = await client.send_message(
            entity,
            post_text,
            parse_mode='html',
        )
        print(f"  Пост опубликован! msg_id={sent.id}")

        await asyncio.sleep(random.uniform(1.0, 2.0))

        # ─── Шаг 5: Закрепляем пост ───
        print("\n  5. Закрепляю пост...")
        try:
            await client(UpdatePinnedMessageRequest(
                peer=entity,
                id=sent.id,
                silent=True,
            ))
            print("  Пост закреплён!")
        except Exception as exc:
            print(f"  Не удалось закрепить: {exc}")

        await asyncio.sleep(random.uniform(1.0, 2.0))

        # ─── Шаг 6: Получаем ссылку на канал ───
        channel_link = ""
        if hasattr(channel, "username") and channel.username:
            channel_link = f"https://t.me/{channel.username}"
        else:
            try:
                invite = await client(ExportChatInviteRequest(
                    peer=entity,
                    legacy_revoke_permanent=True,
                ))
                channel_link = invite.link
            except Exception as exc:
                print(f"  Не удалось создать invite-link: {exc}")

        if channel_link:
            print(f"\n  Ссылка на канал: {channel_link}")
        else:
            print("\n  Не удалось получить ссылку на канал")

        # ─── Шаг 7: Обновляем bio (ОСТОРОЖНО — это write-операция) ───
        print("\n  6. Обновляю bio аккаунта...")
        print("  ⚠️  UpdateProfileRequest — рискованная операция для разм. аккаунта")

        if channel_link:
            print("  Жду 10 сек перед UpdateProfile (антибан)...")
            await asyncio.sleep(10)

            bio_text = f"👇 Подробнее в канале\n{channel_link}"
            if len(bio_text) > 70:
                bio_text = channel_link[:70]

            try:
                await client(UpdateProfileRequest(about=bio_text[:70]))
                print(f"  Bio обновлён: {bio_text[:70]}")
                bio_updated = True
            except Exception as exc:
                err_str = str(exc).upper()
                if "FROZEN" in err_str:
                    print(f"  ❄️  АККАУНТ ЗАМОРОЖЕН! Bio НЕ обновлён.")
                else:
                    print(f"  Ошибка обновления bio: {exc}")
        else:
            print("  Нет ссылки на канал — bio не обновлён")

        # ─── Итог ───
        print("\n  ===================================")
        print(f"  Канал: {channel_name}")
        print(f"  Ссылка: {channel_link}")
        print(f"  {settings.PRODUCT_NAME}: {settings.PRODUCT_BOT_LINK}")
        print(f"  Пост: закреплён")
        print(f"  Bio: {'обновлён' if bio_updated else 'НЕ обновлён'}")
        print("  ===================================\n")

    except Exception as e:
        err_str = str(e).upper()
        if "FROZEN" in err_str:
            print(f"\n  ❄️  АККАУНТ ЗАМОРОЖЕН! Операция отменена.")
        else:
            print(f"\n  ОШИБКА: {e}")
            import traceback
            traceback.print_exc()
    finally:
        await client.disconnect()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Использование: python create_channel.py <phone>")
        print("Пример: python create_channel.py 79637415377")
        sys.exit(1)

    phone = sys.argv[1]
    asyncio.run(create_channel(phone))
