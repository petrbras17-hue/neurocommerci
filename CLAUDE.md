# NEURO COMMENTING

## Описание
Аналог NeuroCom.store — система автоматического комментирования в Telegram для продвижения DartVPN (https://t.me/DartVPNBot).
Легальный VPN-сервис, зарегистрированный в РФ, оплата по ГБ, карты «Мир».

## Стек
- Python 3.9+, aiogram 3 (Telegram-бот), Telethon (userbot), Google Gemini API (gemini-3.1-pro-preview)
- SQLite + SQLAlchemy (async), Google Sheets (gspread), APScheduler
- Rich (CLI), Loguru (логирование)

## Запуск
```bash
cd "NEURO COMMENTING"
source venv/bin/activate
python main.py          # Telegram-бот (основной)
python main.py --cli    # CLI в терминале
```

## Структура
```
main.py              — точка входа (бот или CLI через --cli)
config.py            — pydantic-settings, все из .env
core/                — аккаунты, прокси, сессии, rate limiter
channels/            — поиск каналов, мониторинг, анализ (НЕ НАЧАТО)
comments/            — AI генерация, сценарии A/B, отправка (НЕ НАЧАТО)
storage/             — SQLite модели + Google Sheets (Sheets НЕ НАЧАТО)
admin/bot_admin.py   — ОСНОВНОЙ интерфейс: Telegram-бот с кнопками
admin/cli_menu.py    — CLI интерфейс (урезанный)
utils/               — логирование, антибан (антибан НЕ НАЧАТО)
data/sessions/       — .session файлы Telegram аккаунтов
```

## Текущий статус
- **Спринт 1 (ГОТОВ)**: config, модели БД, прокси, сессии, аккаунты, rate limiter, Telegram-бот с полным меню
- **Спринт 2-7 (НЕ НАЧАТЫ)**: см. CONTINUE_PLAN.md

## Важные правила
- Основной интерфейс = Telegram-бот (НЕ CLI)
- Никогда не коммитить .env, credentials.json, .session файлы
- Перед каждым вызовом Gemini API спрашивать подтверждение (экономия)
- Комментарии отправляются в discussion groups каналов (linked_chat_id)
- Антибан: прогрев (5→10→20→35 за 4 дня), Gaussian delays, 1 прокси = 1 аккаунт

## Подробный план продолжения
**Читай файл CONTINUE_PLAN.md** — там 6 спринтов с кодом, классами и пояснениями.
