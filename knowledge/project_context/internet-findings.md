# Internet Findings

- Самые полезные внешние паттерны для проекта лежат в слоях Telethon session storage, multi-client orchestration, Redis/Postgres runtime state и Telegram control-plane design.
- Для production нельзя полагаться на однофайловые userbot-репозитории как на архитектурный эталон.
- Важный operational вывод: одна `.session` не должна использоваться несколькими независимыми runtime paths одновременно.
- Релевантные ссылки:
  - Telethon sessions docs
  - Telethon issues по duplicate session / multiple clients
  - SQLAlchemy-backed session storage examples
  - Docker/Postgres/Redis Telegram service templates
