"""
Решение математических капч от антифрод-ботов Telegram.

При вступлении в каналы/группы бот-антифрод может прислать
уравнение (2+3=?) с inline-кнопками вариантов ответа.
Этот модуль парсит уравнение и нажимает правильную кнопку.
"""

from __future__ import annotations

import asyncio
import operator
import random
import re
from typing import Optional

from utils.logger import log


# Поддерживаемые операции
_OPS = {
    "+": operator.add,
    "-": operator.sub,
    "*": operator.mul,
    "×": operator.mul,
    "x": operator.mul,
}


def solve_math(text: str) -> Optional[int]:
    """
    Найти и решить математическое уравнение в тексте.
    Поддерживает: '2+3=?', '15 - 7 = ?', '3 × 4 = ?', '2*6=?'.
    """
    # Паттерн: число оператор число = ?
    m = re.search(r"(\d+)\s*([+\-*×x])\s*(\d+)\s*=\s*\??", text)
    if not m:
        return None
    a, op_char, b = int(m.group(1)), m.group(2), int(m.group(3))
    op_func = _OPS.get(op_char)
    if not op_func:
        return None
    return op_func(a, b)


async def solve_captcha_buttons(client, message) -> bool:
    """
    Найти уравнение в сообщении и нажать кнопку с правильным ответом.
    Возвращает True если капча решена.
    """
    text = message.text or message.raw_text or ""
    answer = solve_math(text)
    if answer is None:
        log.debug(f"Капча: не найдено уравнение в тексте: {text[:100]}")
        return False

    log.info(f"Капча: уравнение найдено, ответ = {answer}")

    for row in (message.buttons or []):
        for button in row:
            btn_text = button.text.strip()
            if btn_text == str(answer):
                # Небольшая задержка — человек не нажимает мгновенно
                await asyncio.sleep(random.uniform(1.5, 4.0))
                await button.click()
                log.info(f"Капча: нажата кнопка '{btn_text}'")
                return True

    log.warning(f"Капча: ответ {answer} не найден среди кнопок")
    return False


async def check_and_solve_captcha(
    client,
    entity,
    timeout: float = 10.0,
) -> bool:
    """
    Проверить входящие сообщения на капчу после вступления в канал/группу.
    Ждёт до timeout секунд, проверяет последние сообщения.
    Возвращает True если капча была и решена.
    """
    # Подождать чтобы бот-антифрод успел прислать капчу
    await asyncio.sleep(random.uniform(2.0, 4.0))

    try:
        async for msg in client.iter_messages(entity, limit=5):
            # Ищем сообщение с inline-кнопками содержащими числа
            if not msg.buttons:
                continue
            has_digit_buttons = any(
                btn.text.strip().isdigit()
                for row in msg.buttons
                for btn in row
            )
            if has_digit_buttons:
                solved = await solve_captcha_buttons(client, msg)
                if solved:
                    # Подождать подтверждение от бота
                    await asyncio.sleep(random.uniform(1.0, 2.0))
                    return True
    except Exception as exc:
        log.debug(f"Ошибка проверки капчи: {exc}")

    return False
