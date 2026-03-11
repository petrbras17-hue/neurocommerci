# NEURO COMMENTING — Детальный план исполнения спринтов

Last updated: 2026-03-11
Автор: Claude Opus 4.6

## Контекст

Продукт: Telegram Growth OS (SaaS платформа для нейрокомментинга)
Конкурент: GramGPT.io ($130/мес, 12 модулей, 50 потоков)
Цель: превзойти GramGPT по функционалу и UX, запустить в прод

## Текущее состояние

- VPS: 176.124.221.253, nginx + Let's Encrypt SSL
- Домен: https://176-124-221-253.sslip.io/
- Лендинг `/` — старый GPT-5.4 лендинг (нужно переделать)
- Платформа `/app` — Dark Terminal дизайн, 15+ страниц React
- Backend: FastAPI + PostgreSQL + Redis, все миграции применены
- HEAD: `814b933`, PR #2 открыт
- Sprint 5 завершён (security + deploy)
- Все API ключи настроены на VPS

## Что пользователь хочет

1. **Рабочую платформу** — каждая кнопка, анимация, переход должны работать
2. **Лендинг** — переделать полностью, сбор контактов + автоматизация (ТГ уведомления + таблица)
3. **Биллинг — ПОСЛЕДНИЙ спринт**, сначала продукт
4. **Тестирование на DartVPN** — реальный бот, реальные аккаунты
5. **PR для code review** — уже создан (#2)

## Пересмотренный Roadmap

### Sprint 6: Platform Launch & Landing Redesign
**Приоритет**: Сделать платформу рабочей + переделать лендинг

#### 6A: Telegram Login Widget
- BotFather `/setdomain` для `176-124-221-253.sslip.io`
- Проверить что Telegram Login Widget рендерится в `/app/login`
- Полный auth flow: login → dashboard → /auth/me возвращает данные
- Если widget не работает — fallback на ручной ввод telegram_id для тестирования

#### 6B: Landing Page Redesign
- Полностью новый лендинг в Dark Terminal стиле
- Форма сбора контактов (имя, телефон, email, телеграм)
- POST /api/leads → сохранение в PostgreSQL
- Автоуведомление в Telegram (ADMIN_TELEGRAM_ID + DIGEST_CHAT_ID)
- Зеркало в Google Sheets (если credentials настроены)
- SEO: /, /ecom, /edtech, /saas страницы
- Мобильная адаптация

#### 6C: Platform Functionality Check
- Dashboard: статистика загружается, карточки кликабельны
- Accounts: загрузка .session + .json, привязка прокси, аудит
- Assistant: brief → message → thread полный цикл
- Context: отображение данных, confirm flow
- Creative: generate → approve flow, отображение вариантов
- Channel Map: поиск, фильтры, карточки каналов
- Parser: запуск парсинга, результаты
- Farm: создание фермы, потоки, запуск/стоп
- Campaigns: CRUD, запуск, история

#### 6D: Error Handling & UX
- ErrorBoundary на каждой странице (уже есть, проверить)
- Loading skeletons на всех страницах
- Toast/snackbar для успешных действий
- Retry кнопки при ошибках API
- 404 страница
- Empty states (когда нет данных)

### Sprint 7: DartVPN Testing & Polish
**Приоритет**: Прогнать весь flow на реальном боте DartVPN

#### 7A: Real Account Testing
- Загрузить DartVPN аккаунт (79637415377 — разморожен)
- Привязать прокси KZ
- Запустить assistant brief для DartVPN
- Пройти context → creative → approve
- Проверить что AI генерирует адекватные комментарии для VPN

#### 7B: Farm Testing
- Создать ферму для DartVPN
- Добавить каналы через парсер (VPN/технологии тематика)
- Запустить один поток
- Проверить что комментарий публикуется в канал
- Мониторинг через farm events

#### 7C: End-to-End Flow
- Полный цикл: регистрация → настройка → парсинг → создание контента → публикация
- Записать все баги и edge cases
- Исправить найденные проблемы

### Sprint 8: Production Hardening
**Приоритет**: Стабильность для реальных пользователей

#### 8A: Monitoring
- Sentry для error tracking (нужен SENTRY_DSN)
- Structured logging в JSON
- Health dashboard

#### 8B: Infrastructure
- Автоматический бэкап PostgreSQL (daily cron)
- Redis-backed rate limiting
- Connection pool tuning
- CI/CD: все тест-сьюты на push

#### 8C: Performance
- SQL pagination на всех list endpoints
- Lazy loading для тяжёлых компонентов
- Image optimization
- Bundle splitting

### Sprint 9: Campaigns + Analytics
**Приоритет**: Первый реальный продуктовый цикл

- Campaign execution engine
- Parser integration с campaign targeting
- Analytics events pipeline
- Usage dashboards
- ROI calculations

### Sprint 10: Billing & Subscriptions (ПОСЛЕДНИЙ)
**Приоритет**: Монетизация только когда продукт доказан

- Plans + subscriptions модель
- 14-day trial
- YooKassa (RU/CIS)
- Plan enforcement middleware
- Subscription management UI

## Технические детали для Claude Code

### Как начать новую сессию
```
Прочитай файлы в этом порядке:
1. CLAUDE.md
2. knowledge/project_context/sprint_execution_plan.md (этот файл)
3. knowledge/project_context/sprint_master_plan.md
4. knowledge/project_context/change_register.md
5. README.md

Затем проверь:
- git log --oneline -5
- git status
- ssh deploy@176.124.221.253 "cd /opt/neuro-commenting && docker compose ps"
```

### Ключевые файлы для каждого спринта
- **Landing**: `ops_api.py` (marketing routes), `templates/marketing/`
- **Auth**: `core/web_auth.py`, `frontend/src/auth.ts`
- **Dashboard**: `frontend/src/pages/DashboardPage.tsx`, `ops_api.py` (stats endpoints)
- **Accounts**: `core/web_accounts.py`, `frontend/src/pages/AccountsPage.tsx`
- **Assistant**: `core/assistant_service.py`, `frontend/src/pages/AssistantPage.tsx`
- **Farm**: `core/farm_orchestrator.py`, `frontend/src/pages/FarmPage.tsx`
- **AI**: `core/ai_router.py`, `core/ai_orchestrator.py`
- **Styles**: `frontend/src/styles.css` (Dark Terminal theme)

### Deploy checklist
```bash
# Локально
git add <files> && git commit && git push origin main

# На VPS
ssh deploy@176.124.221.253
cd /opt/neuro-commenting
git pull origin main
docker compose build ops_api
docker compose up -d ops_api
# Если новые миграции:
docker compose exec ops_api python -c "from alembic.config import Config; from alembic import command; cfg = Config('/app/alembic.ini'); command.upgrade(cfg, 'head')"
```

### ENV переменные (все настроены на VPS)
- OPS_API_TOKEN, JWT_ACCESS_SECRET, JWT_REFRESH_SECRET
- GEMINI_API_KEY, OPENROUTER_API_KEY
- ADMIN_BOT_TOKEN, ADMIN_TELEGRAM_ID
- DIGEST_BOT_TOKEN, DIGEST_CHAT_ID
- DATABASE_URL, REDIS_URL, DB_PASSWORD
- GOOGLE_SHEETS_CREDENTIALS_FILE (проверить наличие)

### Нужно от основателя
- [ ] BotFather /setdomain → 176-124-221-253.sslip.io
- [ ] Подтвердить что Telegram Login Widget работает
- [ ] Предоставить текст для нового лендинга (или разрешить AI генерацию)
- [ ] YooKassa credentials (для Sprint 10, не сейчас)
- [ ] Sentry DSN (для Sprint 8)
