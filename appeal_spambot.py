"""
Апелляция через @SpamBot для замороженного аккаунта.
Запуск: python appeal_spambot.py <phone>
Пример: python appeal_spambot.py 79637415377
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

from telethon import TelegramClient
from telethon.tl.types import (
    KeyboardButtonCallback,
    KeyboardButtonUrl,
    ReplyInlineMarkup,
)

from config import settings

SESSIONS_DIR = settings.sessions_path
PROXIES_FILE = Path("data/proxies.txt")

# Ответы на вопросы SpamBot — в порядке SpamBot'а:
# 1. Почему заблокировали (описание)
# 2. Полное имя
# 3. Контактный email
# 4. Год регистрации
# 5. Откуда узнали
APPEAL_ANSWERS_ORDERED = [
    "I believe my account was blocked by mistake. I did not violate any rules. I use Telegram only for personal messaging with friends and reading news channels.",
    None,  # Заполняется из текущего имени в ТГ
    "alina.moroz.2024@mail.ru",
    "2024",
    "A friend recommended Telegram to me",
]


def load_proxy(index: int = 0):
    """Загружает прокси по индексу из файла."""
    lines = PROXIES_FILE.read_text().strip().split("\n")
    if index >= len(lines):
        index = 0
    parts = lines[index].strip().split(":")
    return (3, parts[0], int(parts[1]), True, parts[2], parts[3])


def load_account(phone: str) -> dict:
    """Загружает JSON аккаунта."""
    json_path = SESSIONS_DIR / f"{phone}.json"
    if not json_path.exists():
        print(f"  Файл {json_path} не найден!")
        sys.exit(1)
    return json.loads(json_path.read_text())


async def wait_for_response(client, entity, after_date=None, timeout=30):
    """Ждёт новое сообщение от SpamBot."""
    start = time.time()
    while time.time() - start < timeout:
        messages = await client.get_messages(entity, limit=3)
        for msg in messages:
            if after_date and msg.date.timestamp() <= after_date:
                continue
            if msg.out:  # Наше сообщение — пропускаем
                continue
            return msg
        await asyncio.sleep(2)
    return None


async def click_button_by_text(msg, text_fragment: str):
    """Ищет кнопку с текстом и нажимает."""
    if not msg.buttons:
        return None
    for row in msg.buttons:
        for button in row:
            if text_fragment.lower() in button.text.lower():
                result = await button.click()
                return result
    return None


async def find_url_button(msg) -> str | None:
    """Ищет URL-кнопку в сообщении."""
    if not msg.reply_markup:
        return None
    if isinstance(msg.reply_markup, ReplyInlineMarkup):
        for row in msg.reply_markup.rows:
            for button in row.buttons:
                if isinstance(button, KeyboardButtonUrl):
                    return button.url
    return None


async def appeal(phone: str):
    data = load_account(phone)
    proxy = load_proxy(1)  # Берём второй прокси (первый для другого аккаунта)

    # Имя для ответа
    first = data.get("first_name", "")
    last = data.get("last_name", "")

    # Текущее имя в ТГ может отличаться (если было изменено до заморозки)

    print(f"\n  === Апелляция @SpamBot для +{phone} ===")
    print(f"  Имя из JSON: {first} {last}")
    print(f"  Прокси: #{2}")

    client = TelegramClient(
        str(SESSIONS_DIR / phone),
        api_id=data.get("app_id") or settings.TELEGRAM_API_ID,
        api_hash=data.get("app_hash") or settings.TELEGRAM_API_HASH,
        proxy=proxy,
        device_model=data.get("device", "Samsung Galaxy S23"),
        system_version=data.get("sdk", "SDK 29"),
        app_version=data.get("app_version", "12.4.3"),
        lang_code=data.get("lang_pack", "ru"),
        system_lang_code=data.get("system_lang_pack", "ru-ru"),
        timeout=30,
        connection_retries=5,
        retry_delay=5,
    )

    try:
        print("\n  1. Подключение...")
        await client.connect()

        if not await client.is_user_authorized():
            print("  ОШИБКА: сессия не авторизована!")
            return

        me = await client.get_me()
        current_name = f"{me.first_name or ''} {me.last_name or ''}".strip()
        print(f"  Подключён как: {current_name} (+{phone})")

        # Для ответа на вопрос "name" используем текущее имя в ТГ
        APPEAL_ANSWERS_ORDERED[1] = current_name

        print("\n  2. Отправляю /start в @SpamBot...")
        entity = await client.get_entity("SpamBot")
        now_ts = time.time()
        await client.send_message(entity, "/start")
        await asyncio.sleep(3)

        # Получаем ответ SpamBot
        msg = await wait_for_response(client, entity, after_date=now_ts - 5)
        if not msg:
            print("  ОШИБКА: SpamBot не ответил за 30 сек")
            return

        print(f"  SpamBot ответил: {msg.text[:120]}...")

        # Если есть кнопки — ищем "mistake" или "ошибка"
        if msg.buttons:
            btn_texts = []
            for row in msg.buttons:
                for b in row:
                    btn_texts.append(b.text)
            print(f"  Кнопки: {btn_texts}")

            # Ищем кнопку про ошибку
            print("\n  3. Нажимаю 'This is a mistake'...")
            now_ts = time.time()
            result = await click_button_by_text(msg, "mistake")
            if result is None:
                result = await click_button_by_text(msg, "ошибк")
            if result is None:
                print("  ОШИБКА: кнопка 'mistake' не найдена!")
                print(f"  Доступные кнопки: {btn_texts}")
                return

            await asyncio.sleep(3)
            msg = await wait_for_response(client, entity, after_date=now_ts - 5)
            if msg:
                print(f"  SpamBot: {msg.text[:120]}...")
                if msg.buttons:
                    btn_texts = [b.text for row in msg.buttons for b in row]
                    print(f"  Кнопки: {btn_texts}")
        else:
            print("  Нет кнопок, SpamBot возможно ответил текстом")

        # Ищем кнопку "Yes" / "Да"
        if msg and msg.buttons:
            print("\n  4. Нажимаю 'Yes'...")
            now_ts = time.time()
            result = await click_button_by_text(msg, "yes")
            if result is None:
                result = await click_button_by_text(msg, "да")
            await asyncio.sleep(3)
            msg = await wait_for_response(client, entity, after_date=now_ts - 5)
            if msg:
                print(f"  SpamBot: {msg.text[:150]}...")

        # SpamBot задаёт вопросы подряд — отвечаем по порядку
        # Но сначала проверяем: если SpamBot повторяет вопрос (email невалидный),
        # мы не переходим к следующему ответу
        answer_idx = 0
        max_attempts = 10  # защита от бесконечного цикла
        attempts = 0
        last_question = ""

        while answer_idx < len(APPEAL_ANSWERS_ORDERED) and attempts < max_attempts:
            attempts += 1
            answer = APPEAL_ANSWERS_ORDERED[answer_idx]

            print(f"\n  5.{answer_idx + 1}. Отвечаю: '{answer}'")
            now_ts = time.time()
            await client.send_message(entity, answer)
            await asyncio.sleep(4)
            msg = await wait_for_response(client, entity, after_date=now_ts - 5)
            if msg:
                print(f"  SpamBot: {msg.text[:150]}...")
                if msg.buttons:
                    btn_texts = [b.text for row in msg.buttons for b in row]
                    print(f"  Кнопки: {btn_texts}")

                # Если SpamBot повторяет тот же вопрос — наш ответ невалиден
                current_q = msg.text[:50]
                if current_q == last_question:
                    print(f"  ⚠️  SpamBot повторяет вопрос — ответ отклонён!")
                    # Спрашиваем пользователя
                    user_answer = input(f"  Введи ответ вручную: ")
                    if user_answer.strip():
                        APPEAL_ANSWERS_ORDERED[answer_idx] = user_answer.strip()
                    continue  # Повторяем с тем же индексом

                last_question = current_q
                answer_idx += 1
            else:
                print("  SpamBot не ответил, продолжаю...")
                answer_idx += 1

        # Ищем кнопку "Confirm"
        if msg and msg.buttons:
            print("\n  6. Нажимаю 'Confirm'...")
            now_ts = time.time()
            result = await click_button_by_text(msg, "confirm")
            if result is None:
                result = await click_button_by_text(msg, "подтвер")
            await asyncio.sleep(5)
            msg = await wait_for_response(client, entity, after_date=now_ts - 5)
            if msg:
                print(f"  SpamBot: {msg.text[:200]}...")

                # Ищем URL с CAPTCHA
                captcha_url = await find_url_button(msg)
                if captcha_url:
                    print(f"\n  ===================================")
                    print(f"  CAPTCHA URL: {captcha_url}")
                    print(f"  ===================================")
                    print(f"\n  Открой эту ссылку в браузере и реши капчу!")
                    print(f"  После решения напиши 'done' и нажми Enter...")

                    # Ждём пользователя
                    user_input = input("\n  > ")

                    if user_input.strip().lower() in ("done", "готово", "д", "y"):
                        # Нажимаем "Done"
                        print("\n  7. Нажимаю 'Done'...")
                        now_ts = time.time()

                        # Обновляем сообщения
                        messages = await client.get_messages(entity, limit=3)
                        for m in messages:
                            if m.buttons and not m.out:
                                result = await click_button_by_text(m, "done")
                                if result is None:
                                    result = await click_button_by_text(m, "готов")
                                break

                        await asyncio.sleep(5)
                        msg = await wait_for_response(client, entity, after_date=now_ts - 5)
                        if msg:
                            print(f"\n  SpamBot: {msg.text[:300]}")
                            if "successfully" in msg.text.lower() or "успешно" in msg.text.lower():
                                print(f"\n  АПЕЛЛЯЦИЯ ПОДАНА УСПЕШНО!")
                            else:
                                print(f"\n  Ответ получен. Проверь текст выше.")
                        else:
                            print("  SpamBot не ответил. Проверь вручную.")
                else:
                    print("  URL с капчой не найден в сообщении")
                    # Может апелляция уже подана или другой сценарий
                    if msg.buttons:
                        btn_texts = [b.text for row in msg.buttons for b in row]
                        print(f"  Кнопки: {btn_texts}")

        print("\n  === Готово ===\n")

    except Exception as e:
        print(f"\n  ОШИБКА: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await client.disconnect()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Использование: python appeal_spambot.py <phone>")
        print("Пример: python appeal_spambot.py 79637415377")
        sys.exit(1)

    phone = sys.argv[1]
    asyncio.run(appeal(phone))
