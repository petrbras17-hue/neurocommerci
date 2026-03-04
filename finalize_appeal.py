"""
Финализация апелляции: подключиться → найти кнопку Done в SpamBot → нажать.
Запуск: python finalize_appeal.py <phone>
"""

import asyncio
import sys

from telethon.tl.types import KeyboardButtonUrl, ReplyInlineMarkup

from utils.standalone_helpers import load_proxy_for_phone, load_account_json, build_client


async def finalize(phone: str):
    data = load_account_json(phone)
    proxy = load_proxy_for_phone(phone)

    print(f"\n  === Финализация апелляции для +{phone} ===")
    print(f"  Прокси: {proxy[1]}:{proxy[2]}")

    client = build_client(phone, data, proxy)

    try:
        print("  1. Подключение...")
        await client.connect()

        if not await client.is_user_authorized():
            print("  ОШИБКА: сессия не авторизована!")
            return

        me = await client.get_me()
        print(f"  Подключён как: {me.first_name} {me.last_name or ''} (+{phone})")

        entity = await client.get_entity("SpamBot")

        # Получить последние сообщения от SpamBot
        print("\n  2. Читаю последние сообщения SpamBot...")
        messages = await client.get_messages(entity, limit=5)

        for msg in messages:
            if msg.out:
                continue
            print(f"\n  SpamBot: {msg.text[:200]}")

            if msg.buttons:
                btn_texts = [b.text for row in msg.buttons for b in row]
                print(f"  Кнопки: {btn_texts}")

                # Ищем "Done" / "Готово"
                for row in msg.buttons:
                    for button in row:
                        if "done" in button.text.lower() or "готов" in button.text.lower():
                            print(f"\n  3. Нажимаю '{button.text}'...")
                            result = await button.click()
                            await asyncio.sleep(5)

                            # Читаем ответ
                            new_msgs = await client.get_messages(entity, limit=3)
                            for nm in new_msgs:
                                if not nm.out and nm.date.timestamp() > msg.date.timestamp():
                                    print(f"\n  SpamBot: {nm.text[:300]}")
                                    if "success" in nm.text.lower() or "успешно" in nm.text.lower():
                                        print("\n  АПЕЛЛЯЦИЯ ПОДАНА УСПЕШНО!")
                                    elif "review" in nm.text.lower() or "рассмотр" in nm.text.lower():
                                        print("\n  Апелляция на рассмотрении!")
                                    else:
                                        print("\n  Ответ получен. Проверь текст выше.")
                                    await client.disconnect()
                                    return

                            print("  SpamBot не ответил после нажатия Done")
                            await client.disconnect()
                            return

            # Проверим URL кнопки в reply_markup
            if msg.reply_markup and isinstance(msg.reply_markup, ReplyInlineMarkup):
                for row in msg.reply_markup.rows:
                    for button in row.buttons:
                        if isinstance(button, KeyboardButtonUrl):
                            print(f"  URL кнопка: {button.url}")

        print("\n  Кнопка 'Done' не найдена. Возможно апелляция уже обработана.")
        print("  Проверяю последнее сообщение SpamBot...")

        # Просто показать последнее сообщение
        if messages:
            last = messages[0]
            if not last.out:
                print(f"\n  Последнее от SpamBot: {last.text[:400]}")

    except Exception as e:
        print(f"\n  ОШИБКА: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await client.disconnect()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Использование: python finalize_appeal.py <phone>")
        sys.exit(1)

    phone = sys.argv[1]
    asyncio.run(finalize(phone))
