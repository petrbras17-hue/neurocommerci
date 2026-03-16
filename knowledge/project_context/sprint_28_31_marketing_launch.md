# NEURO COMMENTING — Marketing Launch Sprints 28-31

Дата: 2026-03-16
Baseline: main @ 07abe03
VPS: 8686404

---

## Sprint 28 — Лендинг + SEO + Commit (ТЕКУЩИЙ)
**Цель:** Зафиксировать все маркетинговые изменения, пройти тесты, задеплоить.

| # | Задача | Агент | Файлы | Размер |
|---|--------|-------|-------|--------|
| 28.1 | Compile-check всех Python файлов | compile-checker | core/*.py, ops_api.py | S |
| 28.2 | TypeScript check frontend | ts-checker | frontend/src/**/*.tsx | S |
| 28.3 | Pytest полный прогон | test-runner | tests/ | M |
| 28.4 | Проверить HTML шаблоны на битые ссылки | html-checker | templates/marketing/*.html | S |
| 28.5 | Исправить найденные ошибки | fixer | * | M |
| 28.6 | Commit + push + deploy VPS | deployer | git + ssh | M |

---

## Sprint 29 — Вертикальные лендинги + Email Engine
**Цель:** Переписать ecom/edtech/saas страницы с конкретным копи, внедрить email-автоматизацию.

| # | Задача | Агент | Файлы | Размер |
|---|--------|-------|-------|--------|
| 29.1 | Переписать ecom.html — копи для e-commerce | ecom-agent | templates/marketing/ecom.html | M |
| 29.2 | Переписать edtech.html — копи для онлайн-школ | edtech-agent | templates/marketing/edtech.html | M |
| 29.3 | Переписать saas.html — копи для SaaS | saas-agent | templates/marketing/saas.html | M |
| 29.4 | Welcome email sequence (7 писем) в email_service.py | email-welcome-agent | core/email_service.py | L |
| 29.5 | Nurture email sequence (5 писем) | email-nurture-agent | core/email_service.py | L |
| 29.6 | Trial expiry emails (3 письма) | email-trial-agent | core/email_service.py | M |
| 29.7 | Churn prevention emails (3 письма) | email-churn-agent | core/email_service.py | M |
| 29.8 | Email scheduler/cron | email-scheduler-agent | core/email_scheduler.py (new) | L |
| 29.9 | Tests + commit + deploy | deployer | tests/ + git | M |

---

## Sprint 30 — Аналитика + OG Images + Blog Foundation
**Цель:** Подключить трекинг, создать OG-изображения, подготовить блог.

| # | Задача | Агент | Файлы | Размер |
|---|--------|-------|-------|--------|
| 30.1 | PostHog integration (events, pageviews, funnels) | analytics-agent | ops_api.py, templates/ | L |
| 30.2 | OG image генерация (PNG 1200x630 для каждой страницы) | og-image-agent | static/images/ | M |
| 30.3 | Blog engine (Markdown → HTML, /blog route) | blog-agent | core/blog_engine.py, templates/ | L |
| 30.4 | 5 SEO-статей из content_strategy.md | content-writers (x5) | content/blog/ | L |
| 30.5 | RSS feed (/feed.xml) | rss-agent | ops_api.py | S |
| 30.6 | Tests + commit + deploy | deployer | tests/ + git | M |

---

## Sprint 31 — Referral + Lead Scoring + Telegram Channel
**Цель:** Growth mechanics, lead scoring, запуск TG-канала продукта.

| # | Задача | Агент | Файлы | Размер |
|---|--------|-------|-------|--------|
| 31.1 | Referral system (invite link, tracking, rewards) | referral-agent | core/referral_service.py | L |
| 31.2 | Lead scoring model | scoring-agent | core/lead_scoring.py | M |
| 31.3 | 10 постов для TG-канала из social_content_calendar.md | tg-content-agent | content/telegram/ | M |
| 31.4 | Onboarding wizard improvements | onboarding-agent | frontend/src/ | L |
| 31.5 | Tests + commit + deploy | deployer | tests/ + git | M |
