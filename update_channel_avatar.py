"""
Обновление аватарки канала-переходника (без пересоздания канала).

Запуск: python update_channel_avatar.py <phone>
Пример: python update_channel_avatar.py 79637415377
"""

from __future__ import annotations

import asyncio
import sys

from telethon.tl.functions.channels import EditPhotoRequest
from telethon.tl.functions.messages import GetDialogsRequest
from telethon.tl.types import InputChatUploadedPhoto, InputPeerEmpty

from config import BASE_DIR, settings
from utils.standalone_helpers import build_client, load_account_json, load_proxy_for_phone
from utils.channel_setup import prepare_square_avatar


async def update_avatar(phone: str):
    data = load_account_json(phone)
    proxy = load_proxy_for_phone(phone)

    print(f"\n  === Обновление аватарки канала для +{phone} ===")

    banner_path = BASE_DIR / settings.PRODUCT_AVATAR_PATH
    if not banner_path.exists():
        print(f"  Баннер не найден: {banner_path}")
        return

    square_path = prepare_square_avatar(banner_path)
    print(f"  Квадратная аватарка: {square_path} ({square_path.stat().st_size // 1024} KB)")

    client = build_client(phone, data, proxy)

    try:
        await client.connect()
        if not await client.is_user_authorized():
            print("  ОШИБКА: сессия не авторизована!")
            return

        me = await client.get_me()
        name = f"{me.first_name or ''} {me.last_name or ''}".strip()
        print(f"  Подключён как: {name} (+{phone})")

        # Найти каналы (broadcast, где мы creator)
        result = await client(GetDialogsRequest(
            offset_date=None, offset_id=0, offset_peer=InputPeerEmpty(),
            limit=100, hash=0,
        ))
        channels = [ch for ch in result.chats
                    if getattr(ch, 'broadcast', False) and getattr(ch, 'creator', False)]

        if not channels:
            print("  Каналов не найдено!")
            return

        # Загружаем файл один раз (не в цикле)
        file = await client.upload_file(str(square_path))

        for ch in channels:
            print(f"\n  Обновляю аватарку канала «{ch.title}» (id={ch.id})...")
            try:
                await client(EditPhotoRequest(
                    channel=ch,
                    photo=InputChatUploadedPhoto(file=file),
                ))
                print(f"  Аватарка обновлена для «{ch.title}»!")
            except Exception as exc:
                print(f"  Ошибка: {exc}")
                # Перезагрузить файл если Telegram аннулировал ссылку
                try:
                    file = await client.upload_file(str(square_path))
                except Exception:
                    pass

        print("\n  Готово!")

    except Exception as e:
        print(f"\n  ОШИБКА: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await client.disconnect()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Использование: python update_channel_avatar.py <phone>")
        print("Пример: python update_channel_avatar.py 79637415377")
        sys.exit(1)

    asyncio.run(update_avatar(sys.argv[1]))
