# NEURO COMMENTING — План продолжения разработки

> Этот файл содержит полный план для продолжения разработки.
> Можно скормить целиком в GPT 5.3 Codex, Claude или любую другую модель.

---

## Что уже сделано (Спринт 1 — ГОТОВ)

### Файлы проекта:
```
NEURO COMMENTING/
├── main.py                     ✅ Точка входа (бот: python main.py | CLI: python main.py --cli)
├── config.py                   ✅ Pydantic Settings, все настройки из .env
├── requirements.txt            ✅ Все зависимости (aiogram, telethon, sqlalchemy, gemini, gspread, etc.)
├── .env                        ✅ Заполнены: ADMIN_BOT_TOKEN, GEMINI_API_KEY, GEMINI_MODEL
├── .env.example                ✅ Шаблон
├── .gitignore                  ✅ Защита секретов
├── CLAUDE.md                   ✅ Документация проекта
│
├── config.py                   ✅ Все настройки: Telegram API, Gemini, лимиты, прокси, пути
│
├── core/
│   ├── account_manager.py      ✅ Пул аккаунтов: загрузка из БД, ротация round-robin, статусы, health-check
│   ├── proxy_manager.py        ✅ Загрузка/валидация/назначение прокси, парсинг разных форматов
│   ├── session_manager.py      ✅ Фабрика Telethon клиентов с прокси, connect/disconnect
│   └── rate_limiter.py         ✅ Дневные лимиты, прогрев (5→10→20→35), cooldown, human-like задержки
│
├── storage/
│   ├── models.py               ✅ SQLAlchemy ORM: Account, Proxy, Channel, Post, Comment
│   ├── sqlite_db.py            ✅ Async engine + session factory
│   └── google_sheets.py        ❌ НЕ СДЕЛАНО
│
├── admin/
│   ├── bot_admin.py            ✅ Telegram-бот с полным меню (aiogram 3):
│   │                              - 📊 Дашборд, 👤 Аккаунты, 🌐 Прокси
│   │                              - 📢 Каналы, 💬 Комментинг, 🔍 Парсер
│   │                              - ⚙️ Настройки, 📖 Помощь
│   │                              - Загрузка .session и .txt файлов в чат
│   ├── cli_menu.py             ✅ Базовый CLI (Rich), урезанный
│   └── dashboard.py            ❌ НЕ СДЕЛАНО
│
├── channels/
│   ├── discovery.py            ❌ НЕ СДЕЛАНО — поиск каналов через Telethon
│   ├── monitor.py              ❌ НЕ СДЕЛАНО — мониторинг новых постов
│   ├── analyzer.py             ❌ НЕ СДЕЛАНО — классификатор релевантности
│   └── channel_db.py           ❌ НЕ СДЕЛАНО — CRUD для базы каналов
│
├── comments/
│   ├── generator.py            ❌ НЕ СДЕЛАНО — AI генерация через Gemini
│   ├── poster.py               ❌ НЕ СДЕЛАНО — отправка через Telethon
│   ├── scenarios.py            ❌ НЕ СДЕЛАНО — логика сценариев A/B
│   └── templates.py            ❌ НЕ СДЕЛАНО — промпт-шаблоны
│
├── utils/
│   ├── logger.py               ✅ Loguru: консоль + файл с ротацией
│   ├── anti_ban.py             ❌ НЕ СДЕЛАНО — антибан стратегии
│   └── helpers.py              ❌ НЕ СДЕЛАНО
│
└── data/
    ├── sessions/               ✅ Директория для .session файлов
    ├── avatars/                ✅ Директория для аватарок
    ├── proxies.txt             — Файл прокси (создаётся пользователем)
    └── logs/                   ✅ Директория логов
```

### Что работает:
- Бот запускается: `python main.py`
- Все меню с кнопками отображаются в Telegram
- Загрузка .session и .txt файлов через чат бота
- SQLite БД создаётся автоматически
- Парсинг прокси в разных форматах
- Rate limiter с прогревом аккаунтов

### Что НЕ заполнено в .env (нужно от пользователя):
- `TELEGRAM_API_ID` и `TELEGRAM_API_HASH` — получить на my.telegram.org или от поставщика аккаунтов
- `ADMIN_TELEGRAM_ID` — ID администратора (получит при первом /start в боте)
- `CHANNELS_SPREADSHEET_ID` — ID Google таблицы (после создания)
- `DARTVPN_CHANNEL_LINK` — ссылка на промежуточный канал с постом о DartVPN

---

## Продукт — аналог NeuroCom.store

Делаем аналог сервиса https://neurocom.store — система нейрокомментирования в Telegram.
Продвигаемый продукт: **DartVPN** (https://t.me/DartVPNBot) — легальный VPN-сервис из РФ.

### Ключевые фичи NeuroCom которые нужно реализовать:
1. **Управление через Telegram-бота** (уже есть каркас)
2. **Парсер каналов** — поиск по ключевым словам + фильтры
3. **AI упаковка профилей** — Gemini создаёт имя, био, предлагает аватарку
4. **AI генерация комментариев** — по контексту поста, на русском, естественно
5. **Автоответчик в ЛС** — когда пишут аккаунту, отвечает автоматически
6. **2 сценария**: A (без ссылки, аватарка-магнит) и B (со ссылкой)
7. **200-2000 комментариев/день** на всех аккаунтах

---

## СПРИНТ 2: Парсер каналов + Google Sheets (2 недели)

### Цель: Найти тематические каналы и сохранить в базу

### Задачи:

#### 2.1 channels/discovery.py — Поиск каналов через Telethon
```python
class ChannelDiscovery:
    async def search_by_keywords(keywords: list[str], min_subscribers: int = 500) -> list[ChannelInfo]
    async def check_comments_enabled(channel) -> bool
    async def get_channel_info(username_or_id) -> ChannelInfo
    async def bulk_discover(topic_sets: dict[str, list[str]]) -> list[ChannelInfo]
```

Предустановленные наборы ключевых слов:
- **VPN**: `vpn, впн, разблокировка, обход блокировок, proxy, прокси`
- **AI/Нейросети**: `нейросети, chatgpt, midjourney, claude, ai, искусственный интеллект, генерация`
- **Соцсети**: `instagram, инстаграм, facebook, тикток, youtube`
- **IT**: `it, программирование, devops, технологии, стартап`
- **Стриминг**: `netflix, spotify, подписки, стриминг`

Логика поиска:
1. Использовать один из подключённых аккаунтов (Telethon)
2. `client.search_public_channels(keyword)` — поиск публичных каналов
3. Для каждого канала: получить количество подписчиков, проверить наличие linked discussion group
4. Фильтровать: мин. подписчиков, русский язык, комментарии открыты

#### 2.2 channels/channel_db.py — CRUD для каналов
```python
class ChannelDB:
    async def add_channel(channel_info) -> Channel
    async def get_all_active() -> list[Channel]
    async def get_by_topic(topic: str) -> list[Channel]
    async def blacklist_channel(channel_id: int)
    async def update_last_checked(channel_id: int)
    async def get_stats() -> dict  # общая статистика по каналам
```

#### 2.3 storage/google_sheets.py — Синхронизация с Google Sheets
```python
class GoogleSheetsStorage:
    def __init__(self, credentials_file, spreadsheet_id)
    async def sync_channels(channels: list[Channel])
    async def sync_comments_log(comments: list[Comment])
    async def sync_accounts(accounts: list[Account])
    async def get_daily_stats() -> dict
```

Структура Google таблицы:
- Лист "Каналы": channel_id | username | title | subscribers | topic | status | added_date
- Лист "Комментарии": timestamp | account | channel | text_preview | scenario | status
- Лист "Аккаунты": phone | proxy | status | today_count | total
- Лист "Статистика": date | comments_sent | successful | failed

Для Google Sheets нужен service account credentials.json.

#### 2.4 Интеграция с ботом
Привязать реальную логику к callback-кнопкам в admin/bot_admin.py:
- `ch_search` → вызывать ChannelDiscovery.search_by_keywords()
- `ch_list` → показывать каналы из ChannelDB
- `ch_add` → добавлять канал по @username
- `parse_keywords` → запуск парсера
- `parse_topic` → поиск по предустановленным тематикам
- `topic_vpn`, `topic_ai`, etc. → конкретный набор ключевых слов

#### 2.5 Фоновая синхронизация SQLite → Google Sheets
Через APScheduler каждые 5-10 минут синхронизировать данные.

### DoD Спринта 2:
- Парсер находит каналы по ключевым словам через Telethon
- Каналы сохраняются в SQLite и (опционально) Google Sheets
- В боте работают кнопки парсера и базы каналов
- Фильтры: подписчики, комментарии, тематика

---

## СПРИНТ 3: Мониторинг постов + Очередь (2 недели)

### Цель: Следить за новыми постами в каналах из базы

### Задачи:

#### 3.1 channels/monitor.py — Мониторинг новых постов
```python
class ChannelMonitor:
    async def start_monitoring(channels: list[Channel])
    async def stop_monitoring()
    async def check_new_posts(channel: Channel) -> list[Post]
    async def is_relevant(post_text: str) -> tuple[bool, float]  # (relevant, score)
```

Логика:
1. Поллинг каждые 1-5 минут (настраивается в config)
2. Для каждого канала: получить последние посты после `last_post_checked`
3. Проверить релевантность через keywords + опционально AI
4. Если пост релевантен и < 2 часов — добавить в очередь
5. Дедупликация: не комментировать один пост дважды

#### 3.2 channels/analyzer.py — Классификатор релевантности
```python
class PostAnalyzer:
    def keyword_score(text: str) -> float  # быстрая оценка по ключевым словам
    async def ai_score(text: str) -> float  # глубокая оценка через Gemini
    def is_relevant(text: str, threshold: float = 0.5) -> bool
```

Ключевые слова для скоринга (по весу):
- Высокий: vpn, впн, блокировка, заблокировали, обход
- Средний: нейросеть, chatgpt, instagram, midjourney, ai
- Низкий: технологии, интернет, сервис, подписка

#### 3.3 core/scheduler.py — Планировщик задач
```python
class TaskScheduler:
    def __init__(self)
    def schedule_monitoring(interval_sec: int)
    def schedule_sheets_sync(interval_sec: int)
    def schedule_daily_reset()  # сброс счётчиков в полночь
    def start()
    def stop()
```

Использовать APScheduler с asyncio.

#### 3.4 Интеграция с ботом
- `com_start` → запускает мониторинг + комментирование
- `com_stop` → останавливает
- `com_stats` → показывает: постов обнаружено, в очереди, прокомментировано

### DoD Спринта 3:
- Система поллит каналы из базы каждые N минут
- Новые релевантные посты попадают в очередь
- Дедупликация работает
- Запуск/остановка через кнопки в боте

---

## СПРИНТ 4: AI генерация комментариев через Gemini (2 недели)

### Цель: Генерировать естественные комментарии на русском

### Задачи:

#### 4.1 comments/generator.py — Gemini AI генератор
```python
class CommentGenerator:
    def __init__(self, api_key: str, model: str)
    async def generate(post_text: str, scenario: str, persona: str) -> str
    async def generate_batch(posts: list, scenario: str) -> list[str]
    def validate_comment(text: str, scenario: str) -> bool
```

Интеграция с Google Generative AI:
```python
import google.generativeai as genai
genai.configure(api_key=settings.GEMINI_API_KEY)
model = genai.GenerativeModel(settings.GEMINI_MODEL)
```

#### 4.2 comments/templates.py — Промпт-шаблоны

**Системный промпт (Сценарий A — без ссылки):**
```
Ты русский пользователь Telegram. Напиши короткий комментарий (1-3 предложения)
к посту ниже. Комментарий должен быть:
- На разговорном русском с уместным сленгом
- По теме поста
- Выглядеть как мнение реального человека
- Упомянуть личный опыт использования VPN/сервиса если уместно
- БЕЗ ссылок, БЕЗ рекламы, БЕЗ @упоминаний
- {persona_style} стиль

Пост: {post_text}
```

**Системный промпт (Сценарий B — со ссылкой):**
```
Ты русский пользователь Telegram. Напиши короткий комментарий (1-3 предложения)
к посту ниже. Комментарий должен:
- На разговорном русском
- По теме поста
- Естественно порекомендовать DartVPN бот (@DartVPNBot)
- Сказать что сам пользуешься / друг посоветовал
- Упомянуть плюс: оплата по ГБ, карты Мир, всё легально
- {persona_style} стиль

Пост: {post_text}
```

#### 4.3 comments/scenarios.py — Логика выбора сценария
```python
class ScenarioSelector:
    def select(self) -> Literal["A", "B"]  # взвешенный random по SCENARIO_B_RATIO
    def get_persona(self, account_phone: str) -> str  # стиль для аккаунта
```

Персоны: casual (обычный юзер), techie (технарь), enthusiast (восторженный), brief (краткий).

#### 4.4 Фоллбэк-шаблоны
Если Gemini недоступен — готовые шаблоны комментариев с подстановкой.

#### 4.5 Валидация
- Длина: 20-300 символов
- Нет запрещённых слов (реклама, спам)
- Сценарий A: нет ссылок
- Сценарий B: есть @DartVPNBot

#### 4.6 Интеграция с ботом
- `com_test` → пользователь отправляет текст поста, бот генерирует тестовый комментарий
- Показать оба сценария для сравнения

**ВАЖНО: Каждый вызов Gemini API стоит деньги. Перед вызовом AI спрашивать пользователя подтверждение или использовать флаг auto_approve.**

### DoD Спринта 4:
- AI генерирует уникальные комментарии по контексту поста
- Два сценария работают с правильным балансом A/B
- Персоны дают разный стиль для разных аккаунтов
- Тест-кнопка в боте работает
- Фоллбэк на шаблоны если AI недоступен

---

## СПРИНТ 5: Отправка комментариев + Антибан (2 недели)

### Цель: Полный E2E пайплайн — обнаружил пост → сгенерировал → отправил

### Задачи:

#### 5.1 comments/poster.py — Отправка через Telethon
```python
class CommentPoster:
    async def post_comment(account, channel, post_id, text) -> PostResult
    async def resolve_discussion_group(channel) -> int  # ID группы обсуждений
    async def verify_posted(account, channel, post_id) -> bool
```

**КРИТИЧНО**: Комментарии в Telegram каналах отправляются НЕ в сам канал, а в связанную **группу обсуждений** (discussion group). Нужно:
1. Получить ID канала
2. Через `client(GetFullChannelRequest(channel))` получить `linked_chat_id`
3. Отправить сообщение в linked chat как reply на forwarded post

#### 5.2 utils/anti_ban.py — Антидетект стратегии
```python
class AntiBan:
    async def human_delay() -> float  # Gaussian random delay
    async def warm_up_check(account) -> bool  # можно ли комментировать
    async def simulate_reading(client, channel) -> None  # "прочитать" посты
    async def random_reaction(client, message) -> None  # иногда ставить реакции
```

Стратегии:
- Задержки: нормальное распределение 2-10 мин (не uniform!)
- Прогрев: день 1→5, день 2→10, день 3→20, день 4+→35
- Перемешивание: иногда просто читать, ставить реакции, не комментировать
- Отдых: пауза 2 часа каждые 8-10 комментариев
- Постоянный IP: один прокси = один аккаунт навсегда
- Device fingerprint: Samsung Galaxy S23, Android 14, Telegram 10.8.3 (уже в session_manager.py)

#### 5.3 Обработка ошибок Telethon
```python
# В poster.py
try:
    await client.send_message(discussion_group, text, reply_to=post_msg_id)
except FloodWaitError as e:
    rate_limiter.set_flood_wait(phone, e.seconds)
except UserBannedInChannelError:
    channel_db.blacklist_for_account(channel_id, account_id)
except ChatWriteForbiddenError:
    channel_db.mark_comments_disabled(channel_id)
except AuthKeyUnregisteredError:
    account_manager.handle_error(phone, "banned")
```

#### 5.4 E2E пайплайн
```
Monitor → обнаружил пост → Analyzer → релевантен?
    → да → ScenarioSelector → A или B
    → CommentGenerator → текст
    → AccountManager → следующий аккаунт
    → AntiBan → задержка
    → CommentPoster → отправка
    → Logger → лог в БД + Sheets
```

#### 5.5 Интеграция с ботом
- `com_start` → запускает полный пайплайн
- `com_stop` → graceful shutdown
- `com_stats` → живая статистика: отправлено/ошибок/в очереди
- `com_history` → последние 10 комментариев

### DoD Спринта 5:
- Полный цикл работает автоматически
- Аккаунты ротируются, прокси стабильны
- Антибан: прогрев, задержки, отдых
- Ошибки обрабатываются корректно
- Логирование в SQLite

---

## СПРИНТ 6: AI упаковка профилей + Автоответчик (2 недели)

### Цель: Автоматическая настройка аккаунтов и автоответчик в ЛС

### Задачи:

#### 6.1 Упаковка профилей через Gemini
Через бота (кнопка "🎨 Упаковка профилей"):
1. Gemini генерирует имя, фамилию, bio для каждого аккаунта
2. Предлагает стиль аватарки (описание для генерации или выбор из готовых)
3. Через Telethon: `client(UpdateProfileRequest(first_name, last_name, about))`
4. Загрузка аватарки: `client(UploadProfilePhotoRequest(file))`

#### 6.2 Создание промежуточного канала
Для каждого аккаунта (или общий):
1. Создать канал через Telethon
2. Закрепить пост со ссылкой на @DartVPNBot
3. Указать канал в bio аккаунта

#### 6.3 Автоответчик в ЛС
Когда кто-то пишет купленному аккаунту в личные сообщения:
1. Telethon ловит event `NewMessage` (private)
2. Gemini генерирует ответ с рекомендацией DartVPN
3. Отправляет ссылку на бота

#### 6.4 Интеграция с ботом
- `acc_package` → запуск упаковки для всех аккаунтов
- Показать превью перед применением
- Запросить подтверждение у админа

### DoD Спринта 6:
- Аккаунты автоматически оформлены (имя, bio, аватар, канал)
- Автоответчик работает в ЛС
- Всё управляется через бота

---

## СПРИНТ 7: Расширенный парсер + Google Sheets дашборд + Полировка (2 недели)

### Цель: Продвинутый поиск каналов, полный дашборд, стабильность

### Задачи:

#### 7.1 Расширенный парсер каналов
- Скоринг каналов: subscribers × post_frequency × engagement_rate
- Автодискавери: раз в сутки искать новые каналы по всем тематикам
- Чёрный список: каналы которые банят, нерелевантные
- Экспорт/импорт базы каналов (CSV/JSON)

#### 7.2 Google Sheets дашборд
- Автоматическое форматирование (цвета, графики)
- Ежедневная сводка
- Конверсия: комментарии → переходы (если можно отследить)

#### 7.3 Полировка
- Dry-run режим: `python main.py --dry-run` — всё кроме реальной отправки
- Graceful shutdown: при остановке завершить текущие задачи
- Уведомления в бот: ежедневная сводка, алерты при бане аккаунта
- Docker Compose для деплоя на сервер

#### 7.4 Документация
- README.md с полной инструкцией
- Обновить CLAUDE.md с актуальным статусом

### DoD Спринта 7:
- Система работает автономно 24/7
- Ежедневные отчёты в Google Sheets и в бот
- Каналы обновляются автоматически
- Можно задеплоить на сервер через Docker

---

## Технические заметки для следующей модели

### 1. Telegram API ID/HASH
Пользователь НЕ СМОГ получить API ID на my.telegram.org (глючит). Варианты:
- Получить от поставщика аккаунтов
- Использовать Telegram-бота для получения (@TGApiHashBot или подобные)
- Попробовать с VPN или с телефона

### 2. Gemini API
- Ключ: в .env (`GEMINI_API_KEY`)
- Модель: `gemini-3.1-pro-preview` (самая свежая на март 2026)
- Библиотека: `google-generativeai`
- **ВАЖНО**: спрашивать пользователя перед каждым вызовом AI чтобы не тратить лишнее

### 3. Архитектура
- Основной интерфейс: **Telegram-бот** (aiogram 3), НЕ CLI
- CLI (`--cli` флаг) — для продвинутых настроек
- По образцу NeuroCom.store — всё управление через кнопки в Telegram
- БД: SQLite (рабочая) + Google Sheets (дашборд)

### 4. Комментарии идут в discussion groups
Telegram каналы имеют linked discussion group. Комментарии отправляются туда как reply.
```python
full_channel = await client(GetFullChannelRequest(channel))
discussion_group_id = full_channel.full_chat.linked_chat_id
# отправить в discussion_group_id как reply
```

### 5. Антибан — КРИТИЧНО
- Прогрев: новые аккаунты начинают с 5 комментариев/день
- Задержки: Gaussian distribution, НЕ uniform random
- Один прокси = один аккаунт (не менять!)
- Отдых каждые 8-10 комментариев
- Device model фиксирован в session_manager.py

### 6. Продвигаемый продукт
**DartVPN** — легальный VPN-сервис:
- Бот: https://t.me/DartVPNBot
- Оплата по ГБ
- Карты «Мир» (целевая аудитория — РФ)
- Зарегистрирован, ОКВЭД, налоги

### 7. Venv
```bash
cd "NEURO COMMENTING"
source venv/bin/activate
python main.py
```

---

## Приоритет реализации

1. **Спринт 2** — Парсер каналов (без этого нечего комментировать)
2. **Спринт 4** — AI генерация (ядро продукта)
3. **Спринт 3** — Мониторинг постов
4. **Спринт 5** — Отправка + антибан (E2E)
5. **Спринт 6** — Упаковка профилей + автоответчик
6. **Спринт 7** — Полировка + деплой
