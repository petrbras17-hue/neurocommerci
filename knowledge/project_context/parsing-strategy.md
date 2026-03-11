# Parsing Strategy — Market Intelligence Pipeline

## Overview

NEURO COMMENTING использует два параллельных пути сбора данных для анализа рынка, конкурентов и тематического контента.

## Путь 1: Web-обёртки (Firecrawl → Pinecone)

**Когда использовать**: для любого публичного веб-контента, статей, лендингов конкурентов, аналитики каналов.

**Скрипт**: `scripts/web_parser.py`

### Команды:
```bash
# Спарсить страницу
python scripts/web_parser.py scrape "URL"

# Поиск + парсинг результатов
python scripts/web_parser.py search "запрос" --limit 5

# Обход сайта
python scripts/web_parser.py crawl "URL" --limit 10

# Семантический поиск по всей базе
python scripts/web_parser.py find "запрос"
```

### Рабочие источники для Telegram-аналитики:
- tgstat.ru — аналитика каналов
- vc.ru, habr.com, spark.ru — статьи авторов
- forbes.ru, rbc.ru — обзоры рынка
- Сайты конкурентов (neurocom.store, ai-easy.ru, traffsoft)

## Путь 2: Telegram сессии (Telethon → Pinecone)

**Когда использовать**: для парсинга постов, комментариев и метаданных напрямую из Telegram каналов.

**Модули проекта**:
- `channels/monitor.py` — мониторинг каналов
- `channels/discovery.py` — обнаружение новых каналов
- `channels/channel_db.py` — хранение данных каналов

### Процесс:
1. Подписать аккаунт на целевые каналы (через задачу вступления)
2. Парсить посты через Telethon GetHistoryRequest
3. Извлечь текст + метаданные (views, date, reactions)
4. Векторизовать: chunk → Gemini embedding → Pinecone upsert
5. Source metadata: `source="telegram"`, channel_id, post_id

### Правила безопасности:
- 1 аккаунт = 1 IP
- Rate limiting между запросами
- Активные часы: 8:00-23:00 MSK
- НЕ менять профиль аккаунта

## YouTube дополнение

**Скрипт**: `scripts/social_parser.py`

```bash
# Транскрибировать видео
python scripts/social_parser.py youtube --url "URL" --subs-only

# Поиск по YouTube контенту
python scripts/social_parser.py search --query "запрос"
```

## Единая точка поиска

Все данные хранятся в одном Pinecone индексе `social-content`.
Один запрос `find` или `search` ищет по ВСЕМ источникам:
- web (статьи, лендинги)
- web_search (результаты поиска)
- web_crawl (обход сайтов)
- youtube (транскрипты видео)
- telegram (посты каналов — после интеграции)

Фильтр по источнику: `--source youtube` или `--source web`

## Env переменные
- `PINECONE_API_KEY` — вектор БД
- `FIRECRAWL_API_KEY` — веб-парсинг
- `GEMINI_API_KEY` — embeddings
- `YOUTUBE_API_KEY` — YouTube Data API (опционально)
