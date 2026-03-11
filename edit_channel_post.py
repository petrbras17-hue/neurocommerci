"""
Редактирование поста в redirect-канале аккаунта.
Также скрывает номер телефона в настройках приватности.

Запуск: python edit_channel_post.py <phone> [--force] [--hide-phone]
Пример: python edit_channel_post.py 79637415377 --force --hide-phone
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from telethon.tl.functions.account import SetPrivacyRequest
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.functions.messages import (
    DeleteMessagesRequest,
    GetDialogsRequest,
    UpdatePinnedMessageRequest,
)
from telethon.tl.types import (
    InputPeerEmpty,
    InputPrivacyKeyPhoneNumber,
    InputPrivacyValueDisallowAll,
)

from config import settings
from utils.standalone_helpers import load_proxy_for_phone, load_account_json, build_client

LINK = settings.PRODUCT_BOT_LINK  # https://t.me/DartVPNBot?start=fly


def load_post_template() -> str:
    """Загрузить шаблон поста из data/product_posts.json."""
    path = Path("data/product_posts.json")
    if not path.exists():
        print("Файл data/product_posts.json не найден!")
        sys.exit(1)
    data = json.loads(path.read_text())
    template = data.get("post_template", "")
    return template.replace("{LINK}", LINK)


async def hide_phone_number(client):
    """Скрыть номер телефона — никто не видит."""
    print("\nСкрытие номера телефона...")
    await client(SetPrivacyRequest(
        key=InputPrivacyKeyPhoneNumber(),
        rules=[InputPrivacyValueDisallowAll()],
    ))
    print("Номер телефона скрыт (никто не видит).")


async def main(phone: str):
    data = load_account_json(phone)
    proxy = load_proxy_for_phone(phone)
    client = build_client(phone, data, proxy)

    print(f"Подключение к аккаунту {phone}...")
    await client.connect()
    if not await client.is_user_authorized():
        print("Сессия не авторизована! Нужна реавторизация.")
        await client.disconnect()
        return
    me = await client.get_me()
    print(f"Подключён: {me.first_name} {me.last_name or ''} (@{me.username or 'no_username'})")

    # --- Скрытие номера телефона ---
    if "--hide-phone" in sys.argv:
        await hide_phone_number(client)

    # --- Обновление поста в канале ---
    print("\nПоиск каналов...")
    dialogs = await client(GetDialogsRequest(
        offset_date=None,
        offset_id=0,
        offset_peer=InputPeerEmpty(),
        limit=100,
        hash=0,
    ))

    my_channels = []
    for chat in dialogs.chats:
        if hasattr(chat, "broadcast") and chat.broadcast and hasattr(chat, "creator") and chat.creator:
            my_channels.append(chat)

    if not my_channels:
        print("Каналы не найдены!")
        await client.disconnect()
        return

    print(f"Найдено каналов: {len(my_channels)}")
    for i, ch in enumerate(my_channels):
        username = f"@{ch.username}" if ch.username else "private"
        print(f"  [{i}] {ch.title} ({username}, id={ch.id})")

    # Если один канал — выбрать автоматически
    if len(my_channels) == 1:
        channel = my_channels[0]
    else:
        channel = my_channels[0]  # В --force режиме берём первый
        if "--force" not in sys.argv:
            idx = int(input(f"\nВведите номер канала [0-{len(my_channels)-1}]: "))
            channel = my_channels[idx]

    print(f"Выбран канал: {channel.title}")

    # Найти закреплённое сообщение
    full = await client(GetFullChannelRequest(channel))
    pinned_msg_id = full.full_chat.pinned_msg_id

    if pinned_msg_id:
        target_msg = await client.get_messages(channel, ids=pinned_msg_id)
        print(f"Закреплённое сообщение (id={target_msg.id}): {target_msg.text[:80]}...")
    else:
        messages = await client.get_messages(channel, limit=5)
        if not messages:
            print("Канал пуст!")
            await client.disconnect()
            return
        target_msg = messages[0]
        print(f"Последнее сообщение (id={target_msg.id}): {target_msg.text[:80]}...")

    new_text = load_post_template()

    if "--force" not in sys.argv:
        print(f"\n--- НОВЫЙ ТЕКСТ ---")
        print(new_text[:300] + "..." if len(new_text) > 300 else new_text)
        print(f"--- КОНЕЦ ---\n")
        confirm = input("Заменить пост? (y/n): ").strip().lower()
        if confirm != "y":
            print("Отменено.")
            await client.disconnect()
            return

    # Старый пост с медиа — caption ограничен 1024 символами.
    # Удаляем старый пост и отправляем новый текстовый + закрепляем.
    has_media = target_msg.media is not None
    if has_media or len(new_text) > 1024:
        print("Старый пост содержит медиа или текст слишком длинный для caption.")
        print(f"Удаляю старый пост (id={target_msg.id})...")
        await client.delete_messages(channel, [target_msg.id])

        print("Отправляю новый текстовый пост...")
        new_msg = await client.send_message(
            channel,
            new_text,
            parse_mode="html",
        )

        print(f"Закрепляю пост (id={new_msg.id})...")
        await client(UpdatePinnedMessageRequest(
            peer=channel,
            id=new_msg.id,
            silent=True,
        ))
        print(f"Пост обновлён и закреплён! (id={new_msg.id})")
    else:
        # Простое редактирование текста
        await client.edit_message(
            channel,
            target_msg.id,
            new_text,
            parse_mode="html",
        )
        print(f"Пост обновлён! (id={target_msg.id})")

    await client.disconnect()
    print("Готово.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Использование: python edit_channel_post.py <phone> [--force] [--hide-phone]")
        print("Пример: python edit_channel_post.py 79637415377 --force --hide-phone")
        sys.exit(1)
    asyncio.run(main(sys.argv[1]))
