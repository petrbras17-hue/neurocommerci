# NEURO COMMENTING — Руководство пользователя

## Что это

Система автоматического комментирования в Telegram-каналах для продвижения **DartVPN** (`https://t.me/DartVPNBot?start=fly`).

Аналог NeuroCom.store: бот управляет армией аккаунтов, которые мониторят каналы, генерируют AI-комментарии и направляют трафик на DartVPN через каналы-переходники.

---

## Быстрый старт

### 1. Подготовка

```bash
cd "NEURO COMMENTING"
source venv/bin/activate
```

Заполни `.env`:
```env
TELEGRAM_API_ID=...
TELEGRAM_API_HASH=...
ADMIN_BOT_TOKEN=...        # Токен бота @dartvpn_neurocom_bot
ADMIN_TELEGRAM_ID=...      # Твой Telegram ID
GEMINI_API_KEY=...          # Google Gemini API ключ
PROXY_TYPE=socks5           # или http
```

### 2. Прокси (обязательно!)

Добавь прокси в `data/proxies.txt` — **минимум 1 прокси на 1 аккаунт**:
```
host:port:user:pass
socks5://user:pass@host:port
```

**Без прокси Telegram заблокирует сессии** при подключении нескольких аккаунтов с одного IP.

### 3. Сессии аккаунтов

Положи `.session` и `.json` файлы от поставщика в `data/sessions/`.

Если сессии невалидны — реавторизуй:
```bash
python reauth_sessions.py
```
Скрипт запросит SMS-код для каждого аккаунта. Если есть 2FA пароль в JSON — использует автоматически.

### 4. Запуск бота

```bash
python main.py
```

Открой бот в Telegram и управляй через кнопки.

---

## Полный пайплайн настройки

### Шаг 1: Подключение аккаунтов
**Бот** → Аккаунты → Подключить все

Или через скрипт: `python run_setup.py` (всё автоматом).

### Шаг 2: Упаковка профилей
**Бот** → Аккаунты → Упаковка профилей → Упаковать все

Что делает:
- Генерирует **женское имя/фамилию** через Gemini AI (10 стилей: beauty, casual, student, tech, lifestyle, blogger, fitness, business, creative, friendly)
- Генерирует **username** (транслитерация + проверка доступности)
- Генерирует **аватарку** через Gemini Imagen 4.0 (молодая брюнетка, селфи)
- Устанавливает **bio** с намёком на VPN/технологии

25 фоллбэк-профилей на случай если AI недоступен.

### Шаг 3: Каналы-переходники
**Бот** → Аккаунты → Каналы-переходники → Создать все

Для каждого аккаунта:
1. Создаёт приватный Telegram-канал
2. Ставит аватарку DartVPN
3. Публикует пост с **2 скрытыми HTML-ссылками** на DartVPN
4. Закрепляет пост
5. Добавляет ссылку на канал в bio аккаунта

10 уникальных шаблонов постов — каждый аккаунт получает свой.

### Шаг 4: Подписка на каналы
**Бот** → Аккаунты → Подписать на каналы

Подписывает все аккаунты на целевые каналы из БД.

### Шаг 5: Парсинг каналов
**Бот** → Каналы → Парсинг по тематике

Тематики: VPN, AI, соцсети, IT, стриминг. Парсит каналы, фильтрует по размеру, добавляет в мониторинг.

### Шаг 6: Мониторинг и комментирование
**Бот** → Комментирование → Запуск

Система:
1. Мониторит каналы на новые посты (каждые 3 мин)
2. Генерирует AI-комментарии через Gemini
3. Отправляет в discussion группы каналов
4. **Сценарий A** (70%): комментарий без ссылки (трафик через аватарку → профиль → канал-переходник → DartVPN)
5. **Сценарий B** (30%): комментарий со скрытой ссылкой на DartVPN

---

## Утилиты (CLI-скрипты)

| Скрипт | Описание |
|--------|----------|
| `python main.py` | Запуск Telegram-бота (основной) |
| `python main.py --cli` | CLI интерфейс |
| `python run_setup.py` | Автоматическая упаковка + каналы (без бота) |
| `python reauth_sessions.py` | Реавторизация сессий (SMS-коды) |
| `python setup_bot.py` | Настройка профиля бота (аватарка, описание) |

---

## Антибан-система

- **Прогрев**: 5 → 10 → 20 → 35 комментариев за 4 дня
- **Задержки**: 2-10 мин между комментариями (Gaussian distribution)
- **Пауза после ошибки**: 30 мин cooldown
- **Пассивные действия**: просмотры, реакции, чтение каналов
- **Антибан-задержки**: 15-30с между аккаунтами при упаковке/создании каналов
- **Device fingerprint**: каждый аккаунт использует параметры устройства из JSON
- **1 прокси = 1 аккаунт**: обязательное правило

---

## Структура проекта

```
main.py                  — точка входа
config.py                — настройки (pydantic-settings, из .env)
run_setup.py             — прямой запуск упаковки + каналов
reauth_sessions.py       — реавторизация сессий
setup_bot.py             — настройка профиля бота

core/
  session_manager.py     — Telethon клиенты с device fingerprint
  account_manager.py     — управление аккаунтами, round-robin
  proxy_manager.py       — парсинг и назначение прокси
  rate_limiter.py        — лимиты, прогрев, cooldown
  scheduler.py           — APScheduler, отложенный запуск

admin/
  bot_admin.py           — Telegram-бот (aiogram 3), полное меню
  cli_menu.py            — CLI (минимальный)

utils/
  account_packager.py    — AI-упаковка: женские профили + аватарки + username
  channel_setup.py       — каналы-переходники + закреплённые посты DartVPN
  channel_subscriber.py  — подписка аккаунтов на каналы
  passive_actions.py     — пассивные действия (просмотры, реакции)
  auto_responder.py      — автоответчик в ЛС
  anti_ban.py            — антибан-менеджер
  bootstrap.py           — восстановление из env (Railway)
  helpers.py             — утилиты (utcnow)
  logger.py              — loguru конфиг
  notifier.py            — уведомления админу

channels/
  discovery.py           — парсинг каналов по тематикам
  monitor.py             — мониторинг новых постов
  analyzer.py            — анализ постов для комментирования
  channel_db.py          — CRUD каналов в БД

comments/
  generator.py           — AI-генерация комментариев (Gemini)
  scenarios.py           — сценарии A/B
  templates.py           — промпты и фоллбэки
  poster.py              — отправка комментариев

storage/
  models.py              — SQLAlchemy модели (Account, Proxy, Comment, Post, DbChannel)
  sqlite_db.py           — async SQLite + WAL mode
  google_sheets.py       — синхронизация с Google Sheets

data/
  sessions/              — .session + .json файлы аккаунтов
  avatars/               — аватарки (DartVPN баннер, профили, бот)
  proxies.txt            — список прокси
  neuro_commenting.db    — SQLite база
```

---

## Настройки .env (полный список)

```env
# Telegram API
TELEGRAM_API_ID=...
TELEGRAM_API_HASH=...

# Admin Bot
ADMIN_BOT_TOKEN=...
ADMIN_TELEGRAM_ID=...

# Google Gemini
GEMINI_API_KEY=...
GEMINI_MODEL=gemini-3.1-pro-preview

# Google Sheets (опционально)
GOOGLE_SHEETS_CREDENTIALS_FILE=credentials.json
CHANNELS_SPREADSHEET_ID=...
STATS_SPREADSHEET_ID=...

# Proxy
PROXY_TYPE=socks5
PROXY_LIST_FILE=data/proxies.txt
PROXY_ROTATING=false

# Rate Limits
MAX_COMMENTS_PER_ACCOUNT_PER_DAY=35
MIN_DELAY_BETWEEN_COMMENTS_SEC=120
MAX_DELAY_BETWEEN_COMMENTS_SEC=600
COMMENT_COOLDOWN_AFTER_ERROR_SEC=1800

# DartVPN
DARTVPN_BOT_LINK=https://t.me/DartVPNBot?start=fly
DARTVPN_CHANNEL_LINK=
DARTVPN_AVATAR_PATH=data/avatars/dartvpn_banner.jpg
SCENARIO_B_RATIO=0.3

# Warmup (прогрев)
WARMUP_DAY_1_LIMIT=5
WARMUP_DAY_2_LIMIT=10
WARMUP_DAY_3_LIMIT=20

# Monitoring
MONITOR_POLL_INTERVAL_SEC=180
POST_MAX_AGE_HOURS=2
```

---

## Railway деплой

Проект настроен для Railway:
- `Dockerfile` в корне
- Автодеплой при пуше в main
- Env vars задаются в Railway Dashboard
- Сессии восстанавливаются из env переменных (bootstrap)

---

## Частые проблемы

### Сессии "не авторизованы"
Telegram сбросил сессии. Причина: несколько аккаунтов с одного IP без прокси.
**Решение**: добавь прокси → `python reauth_sessions.py`

### Imagen "model not found"
Модель обновлена до `imagen-4.0-generate-001`. Убедись что Gemini API ключ имеет доступ к Imagen.

### "Нет доступных прокси"
Добавь прокси в `data/proxies.txt`. Формат: `host:port:user:pass` или `socks5://user:pass@host:port`.

### FloodWait
Telegram ограничивает запросы. Система автоматически ждёт и пропускает. Если FloodWait > 1 часа — аккаунт переходит в cooldown.
