# NEURO COMMENTING — Сводный маркетинговый отчёт

Дата: 2026-03-16
Метод: 15 параллельных AI-агентов (Agent Teams)
Общий объём: 488 KB, 15 документов

---

## ОБЩАЯ ОЦЕНКА ГОТОВНОСТИ К ЗАПУСКУ

### 3.6 / 10 — НЕ ГОТОВ

| Область | Оценка | Статус |
|---------|--------|--------|
| Продукт | 7/10 | Сильный (27 спринтов, 200+ API, все модули) |
| Лендинг | 4.1/10 | Слабый (абстрактный копи, конфликт цен, плейсхолдеры) |
| SEO | 3.5/10 | Критичный (neurocommenting.com, нет canonical, нет Schema.org) |
| Бренд | 3/10 | Нет логотипа, нет айдентики, нет brand voice (теперь создан) |
| Биллинг | 2/10 | Нет боевых ключей Stripe/YooKassa |
| Legal | 2/10 | Плейсхолдеры ИНН/ООО, нет юрлица |
| Аналитика | 1/10 | Нет трекинга (GA4/PostHog), нет метрик |
| Поддержка | 1/10 | Нет FAQ-бота, нет email support |

---

## TOP-3 БЛОКЕРА ДЛЯ REVENUE

1. **Нет юрлица (ИП/ООО)** — без него невозможно подключить ЮKassa
2. **Нет собственного домена** — neurocommenting.com не индексируется и не вызывает доверия
3. **Нет боевых платёжных ключей** — кнопка "Подключить" ведёт в app, но оплата невозможна

---

## КОНКУРЕНТНАЯ ПОЗИЦИЯ

| Фактор | NEURO COMMENTING | GramGPT.io |
|--------|-----------------|------------|
| Тип | Multi-tenant SaaS | Десктопный комбайн |
| Цена | 4 990-79 990 ₽/мес (рекомендация) | ~$130/мес |
| Модули | 20+ | 12 |
| Уникальные фичи | 3D Channel Map, AI Persona, Content Factory, Agency White Label, Self-Healing | Проверенный трек-рекорд, community |
| Потоки | до 50 | до 50 |
| Brand awareness | 0 | Высокий в нише |
| Вердикт | **Превосходит по функциям, проигрывает по brand** | Лидер рынка, но desktop-only |

**Ключевое отстроение:** "Telegram Growth OS для команд и агентств" vs "инструмент одиночки"

---

## РЕКОМЕНДОВАННЫЕ ТАРИФЫ

| Тариф | Цена/мес | Аккаунты | Потоки | Ключевые модули |
|-------|---------|----------|--------|-----------------|
| Starter | 4 990 ₽ | 5 | 5 | Commenter, Parser, Warmup |
| Growth | 12 990 ₽ | 20 | 20 | + Farm, Channel Map, Content Factory |
| Pro | 29 990 ₽ | 50 | 50 | + Chatting, Dialogs, API, Priority Support |
| Agency | 79 990 ₽ | 200 | 50 | + White Label, 50 клиентов, Revenue Share |

- **Trial:** 14 дней на Growth-уровне
- **Annual discount:** -20%
- **Gross margin:** 68-83%
- **Безубыточность:** 10 клиентов на Growth

---

## ВОРОНКА ПРОДАЖ

```
Awareness (Telegram, SEO, Ads) → 10 000 визитов/мес
    ↓ 3% конверсия
Lead Capture (форма, lead magnet) → 300 лидов/мес
    ↓ 15% конверсия
Trial (14 дней Growth) → 45 trial/мес
    ↓ 20% конверсия
Paid (подписка) → 9 клиентов/мес
    ↓ 95% retention
Expansion (upsell, referral) → MRR рост +15%/мес
```

**Прогноз MRR:** 25K₽ (месяц 1) → 250K₽ (месяц 6)

---

## СОЗДАННЫЕ МАРКЕТИНГОВЫЕ АКТИВЫ

| # | Файл | Что внутри |
|---|------|-----------|
| 1 | `brand_voice.md` | Brand Voice, 5 messaging pillars, tone matrix, do/don't |
| 2 | `competitor_analysis.md` | 12+ конкурентов, SWOT, battlecard vs GramGPT |
| 3 | `landing_cro_audit.md` | CRO аудит 4.1/10, 20 action items с примерами |
| 4 | `seo_audit.md` | SEO аудит 35/100, keyword map 25+ слов, 28 задач |
| 5 | `copywriting_pack.md` | 3 hero, 8 модулей, 3 тарифа, FAQ, отзывы, ads |
| 6 | `email_sequences.md` | 21 письмо в 5 последовательностях, A/B subjects |
| 7 | `sales_funnel.md` | Full funnel TOFU→BOFU, lead scoring, прогноз 6 мес |
| 8 | `social_content_calendar.md` | 28 TG-постов, 20 Twitter, 5 LinkedIn, 50 идей |
| 9 | `ad_creatives.md` | 5 Яндекс + 5 TG Ads + 3 VK + 6 баннеров + ретаргет |
| 10 | `launch_playbook.md` | Pre/Launch/Post план, 7 блокеров, бюджет 50K₽ |
| 11 | `pricing_strategy.md` | 4 тарифа, unit economics, 5 A/B тестов |
| 12 | `content_strategy.md` | 5 столбов, 20 статей, 5 lead magnets, 10 видео |
| 13 | `marketing_audit.md` | Полный аудит 3.6/10, TOP-10 действий |
| 14 | `agency_proposal.md` | КП, Pitch Deck 12 слайдов, One-Pager |
| 15 | `growth_strategy.md` | Реферальная программа, 3 growth loops, gamification |

---

## ПЛАН ДЕЙСТВИЙ (приоритет)

### Неделя 1 — БЛОКЕРЫ (без этого запуск невозможен)
- [ ] Зарегистрировать ИП
- [ ] Купить домен (neurocommenting.ru / neurogrowth.ru)
- [ ] Настроить DNS + SSL
- [ ] Убрать плейсхолдеры ИНН из footer

### Неделя 2 — ЛЕНДИНГ
- [ ] Переписать Hero Section (из `copywriting_pack.md`)
- [ ] Унифицировать тарифы (из `pricing_strategy.md`)
- [ ] Добавить Schema.org JSON-LD (из `seo_audit.md`)
- [ ] Добавить sitemap для всех страниц
- [ ] Исправить OG-image (SVG → PNG)

### Неделя 3 — ПЛАТЕЖИ + АНАЛИТИКА
- [ ] Подключить YooKassa (боевые ключи)
- [ ] Подключить PostHog / GA4
- [ ] Настроить email-последовательности (из `email_sequences.md`)
- [ ] Создать Telegram-канал продукта

### Неделя 4 — SOFT LAUNCH
- [ ] Опубликовать 5 beta-тестеров
- [ ] Запустить Telegram Ads (бюджет 500₽/день)
- [ ] Начать контент-календарь (из `social_content_calendar.md`)
- [ ] Подать статью на vc.ru
