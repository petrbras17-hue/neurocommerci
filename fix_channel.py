"""
Исправление канала-переходника для аккаунта.
Удаляет старый канал, создаёт новый с правильным оформлением.

Запуск: python fix_channel.py <phone>
Пример: python fix_channel.py 79637415377
"""

from __future__ import annotations

import asyncio
import random
import sys
from pathlib import Path

from telethon.tl.functions.channels import (
    CreateChannelRequest,
    DeleteChannelRequest,
    EditPhotoRequest,
)
from telethon.tl.functions.messages import (
    ExportChatInviteRequest,
    GetDialogsRequest,
    UpdatePinnedMessageRequest,
)
from telethon.tl.functions.account import UpdateProfileRequest
from telethon.tl.functions.photos import UploadProfilePhotoRequest
from telethon.tl.types import (
    InputChatUploadedPhoto,
    InputPeerEmpty,
)

from config import settings, BASE_DIR
from utils.standalone_helpers import load_proxy_for_phone, load_account_json, build_client
from utils.channel_setup import prepare_square_avatar

# ─── Конфиг ───

PRODUCT_LINK = settings.PRODUCT_BOT_LINK
CHANNEL_NAME = f"{settings.PRODUCT_NAME} | Канал-переходник"
CHANNEL_DESC = settings.PRODUCT_SHORT_DESC

BANNER_PATH = BASE_DIR / settings.PRODUCT_AVATAR_PATH
PROFILE_AVATARS_DIR = BASE_DIR / "data/avatars/profiles"


def _link(text: str) -> str:
    return f'<a href="{PRODUCT_LINK}">{text}</a>'


POST_TEXT = (
    f'🚀 {_link("DART VPN")} — летай без ограничений!\n'
    '\n'
    'VPN прямо в Telegram — без приложений, без регистрации, без привязки карты.\n'
    '\n'
    f'🏠 <b>Для дома</b>\n'
    'Полный доступ к YouTube в 4K, Netflix, Instagram и всем заблокированным сайтам\n'
    '\n'
    f'📲 <b>Для телефона</b>\n'
    'Мессенджеры без обрывов — Telegram, WhatsApp, Discord. Скорость от 200 Мбит/с\n'
    '\n'
    f'🛡 <b>Безопасность</b>\n'
    'Защита данных, анонимность, стабильная работа больше года\n'
    '\n'
    f'⚡️ <b>Smart Connect</b>\n'
    'Автоматический выбор самого быстрого сервера\n'
    '\n'
    '🎁 5 дней бесплатно + скидка 50%\n'
    '\n'
    '👇👇👇\n'
    '\n'
    f'{_link("ПОПРОБОВАТЬ БЕСПЛАТНО")}\n'
    f'{_link("ПОПРОБОВАТЬ БЕСПЛАТНО")}\n'
    f'{_link("ПОПРОБОВАТЬ БЕСПЛАТНО")}'
)


async def find_old_channels(client) -> list:
    """Найти каналы, созданные аккаунтом (broadcast, где мы creator)."""
    found = []
    result = await client(GetDialogsRequest(
        offset_date=None,
        offset_id=0,
        offset_peer=InputPeerEmpty(),
        limit=100,
        hash=0,
    ))
    for chat in result.chats:
        if getattr(chat, 'broadcast', False) and getattr(chat, 'creator', False):
            found.append(chat)
    return found


async def fix_channel(phone: str):
    data = load_account_json(phone)
    proxy = load_proxy_for_phone(phone)

    print(f"\n  === Исправление канала для +{phone} ===")
    print(f"  Прокси: {proxy[1]}:{proxy[2]}")
    print(f"  Product ссылка: {PRODUCT_LINK}")

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

        # ─── Шаг 2: Удаляем старые каналы ───
        print("\n  2. Ищу старые каналы...")
        old_channels = await find_old_channels(client)

        if old_channels:
            for ch in old_channels:
                print(f"  Найден канал: «{ch.title}» (id={ch.id})")
                try:
                    entity = await client.get_entity(ch.id)
                    await client(DeleteChannelRequest(channel=entity))
                    print(f"  Удалён: «{ch.title}»")
                    await asyncio.sleep(random.uniform(2.0, 4.0))
                except Exception as exc:
                    print(f"  Не удалось удалить «{ch.title}»: {exc}")
        else:
            print("  Старых каналов не найдено")

        await asyncio.sleep(random.uniform(2.0, 4.0))

        # ─── Шаг 3: Ставим аватарку профиля ───
        avatar_path = PROFILE_AVATARS_DIR / f"avatar_{phone}.png"
        if avatar_path.exists():
            print(f"\n  3. Ставлю аватарку профиля...")
            print(f"  Жду 10 сек перед UploadProfilePhoto (антибан)...")
            await asyncio.sleep(10)
            try:
                file = await client.upload_file(str(avatar_path))
                await client(UploadProfilePhotoRequest(file=file))
                print(f"  Аватарка профиля установлена!")
            except Exception as exc:
                err_str = str(exc).upper()
                if "FROZEN" in err_str:
                    print(f"  АККАУНТ ЗАМОРОЖЕН! Аватарка НЕ установлена.")
                else:
                    print(f"  Не удалось установить аватарку профиля: {exc}")
        else:
            print(f"\n  3. Файл аватарки профиля не найден: {avatar_path}")

        await asyncio.sleep(random.uniform(3.0, 5.0))

        # ─── Шаг 4: Создаём новый канал ───
        print(f"\n  4. Создаю канал «{CHANNEL_NAME}»...")
        result = await client(CreateChannelRequest(
            title=CHANNEL_NAME,
            about=CHANNEL_DESC[:255],
            broadcast=True,
        ))

        channel = result.chats[0]
        channel_id = channel.id
        print(f"  Канал создан! ID: {channel_id}")

        await asyncio.sleep(random.uniform(2.0, 4.0))
        entity = await client.get_entity(channel_id)

        # ─── Шаг 5: Аватарка канала (квадратная версия баннера) ───
        if BANNER_PATH.exists():
            print("\n  5. Ставлю аватарку канала (квадратную)...")
            try:
                square_path = prepare_square_avatar(BANNER_PATH)
                file = await client.upload_file(str(square_path))
                await client(EditPhotoRequest(
                    channel=entity,
                    photo=InputChatUploadedPhoto(file=file),
                ))
                print("  Аватарка канала установлена (квадратная)!")
            except Exception as exc:
                print(f"  Не удалось установить аватарку канала: {exc}")
        else:
            print(f"\n  5. Баннер не найден: {BANNER_PATH}")

        await asyncio.sleep(random.uniform(2.0, 3.0))

        # ─── Шаг 6: Публикуем пост с баннером ───
        print("\n  6. Публикую пост с баннером Product...")
        if BANNER_PATH.exists():
            sent = await client.send_file(
                entity,
                str(BANNER_PATH),
                caption=POST_TEXT,
                parse_mode='html',
            )
        else:
            sent = await client.send_message(
                entity,
                POST_TEXT,
                parse_mode='html',
            )
        print(f"  Пост опубликован! msg_id={sent.id}")

        await asyncio.sleep(random.uniform(1.0, 2.0))

        # ─── Шаг 7: Закрепляем пост ───
        print("\n  7. Закрепляю пост...")
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

        # ─── Шаг 8: Получаем ссылку на канал ───
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

        # ─── Шаг 9: Обновляем bio ───
        if channel_link:
            print("\n  8. Обновляю bio аккаунта...")
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
                    print(f"  АККАУНТ ЗАМОРОЖЕН! Bio НЕ обновлён.")
                else:
                    print(f"  Ошибка обновления bio: {exc}")

        # ─── Итог ───
        print("\n  ===================================")
        print(f"  Канал: {CHANNEL_NAME}")
        print(f"  Ссылка: {channel_link}")
        print(f"  Product: {PRODUCT_LINK}")
        print(f"  Пост: с баннером + 3x ПОПРОБОВАТЬ БЕСПЛАТНО")
        print(f"  Аватарка профиля: установлена" if avatar_path.exists() else "  Аватарка профиля: нет файла")
        print(f"  Bio: {'обновлён' if bio_updated else 'НЕ обновлён'}")
        print("  ===================================\n")

    except Exception as e:
        err_str = str(e).upper()
        if "FROZEN" in err_str:
            print(f"\n  АККАУНТ ЗАМОРОЖЕН! Операция отменена.")
        else:
            print(f"\n  ОШИБКА: {e}")
            import traceback
            traceback.print_exc()
    finally:
        await client.disconnect()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Использование: python fix_channel.py <phone>")
        print("Пример: python fix_channel.py 79637415377")
        sys.exit(1)

    phone = sys.argv[1]
    asyncio.run(fix_channel(phone))
