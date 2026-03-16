# NEURO COMMENTING — Полная воронка продаж

Последнее обновление: 2026-03-16

---

## 1. Full Funnel Map

```
AWARENESS          INTEREST           CONSIDERATION      DECISION           ONBOARDING         EXPANSION
─────────────────────────────────────────────────────────────────────────────────────────────────────────

Telegram каналы    Лендинг            Демо-звонок        Trial 3 дня        Первый запуск      Upsell Pro/Agency
Яндекс.Директ     /ecom /edtech      Бот-нурсинг        Регистрация        Wizard              Рефералы
SEO-статьи         /saas /pricing     Email-серия        Выбор тарифа       Загрузка аккаунтов  Агентская программа
Рефералы           Лид-форма          Кейсы              Stripe/YooKassa    Первый комментарий  Annual upgrade
YouTube            POST /api/leads    Вебинар            POST /auth/register Warmup             Доп. аккаунты
Партнёры           Telegram бот       Телеграм-чат       Billing checkout   AI-ассистент        White label
```

### Ключевые переходы между этапами

| Переход | Триггер | Endpoint / Действие |
|---------|---------|---------------------|
| Visitor -> Lead | Заполнение формы на лендинге | `POST /api/leads` -> Google Sheets + Telegram notify |
| Lead -> Registered | Регистрация email/password или Telegram | `POST /auth/register` или `POST /auth/telegram/verify` |
| Registered -> Trial | Автоматический 3-дневный trial на Starter | `create_trial()` в billing_service.py |
| Trial -> Paid | Оплата через Stripe или YooKassa | Webhook -> `create_subscription()` |
| Paid -> Expanded | Апгрейд тарифа или покупка агентского пакета | `PUT /v1/billing/subscription` |
| Paid -> Churned | Отмена подписки | `cancel_subscription()` -> `subscription_cancelled` email |
| Churned -> Reactivated | Winback-кампания | Повторный checkout |

---

## 2. TOFU — Top of Funnel (Привлечение)

### 2.1 Каналы привлечения

| Канал | Бюджет/мес | Целевой CPA лида | Описание |
|-------|-----------|-------------------|----------|
| **Telegram каналы (нативная реклама)** | 50-150K руб | 300-800 руб | Посевы в маркетинг/ecom/edtech каналах. Тексты в стиле кейса: "Как мы получили 1247 комментариев за 30 дней" |
| **Яндекс.Директ** | 100-300K руб | 500-1500 руб | Поиск: "продвижение в телеграм", "комментинг телеграм", "рост подписчиков телеграм". РСЯ: таргетинг на маркетологов |
| **SEO (органика)** | 30-50K руб (контент) | 100-300 руб (долгосрок) | Статьи: "Как продвигать бренд в Telegram в 2026", "Нейрокомментинг: что это и зачем", "AI для Telegram-маркетинга" |
| **YouTube** | 20-40K руб (продакшн) | 200-600 руб | Разборы: "Разобрал комментинг-стратегию бренда X", демо продукта, comparison с GramGPT |
| **Рефералы** | Revenue share 20% | 0 (organic) | Реферальная программа: 20% от первого платежа приведённого клиента |
| **Партнёры/агентства** | Rev share 15-30% | 0 | White label и агентский пакет для перепродажи клиентам |

### 2.2 Lead Magnets

| Lead Magnet | Формат | Точка входа |
|-------------|--------|-------------|
| "Гайд: 10 стратегий роста Telegram-канала через комментинг" | PDF, 15-20 стр | Форма на лендинге, Telegram-бот |
| "Калькулятор ROI нейрокомментинга" | Интерактив на сайте | `/roi-calculator` (будущий эндпоинт) |
| "Шаблоны комментариев для 12 ниш" | Google Doc / PDF | Telegram-бот после подписки |
| "Кейс: 0 -> 5000 подписчиков за 60 дней" | Лонгрид / видео | SEO-статья, YouTube |
| "Бесплатный аудит вашего Telegram-канала" | Живой разбор (15 мин) | Calendly через бота |

### 2.3 Контент-план (первые 90 дней)

**Неделя 1-4: Awareness**
- 8 SEO-статей (long-tail keywords)
- 4 поста в маркетинговых Telegram-каналах
- 2 YouTube-видео (демо + comparison)
- Запуск Яндекс.Директ (поисковые кампании)

**Неделя 5-8: Social Proof**
- 4 кейса клиентов (даже если внутренние/тестовые)
- 2 вебинара "Как продвигать бренд в Telegram с AI"
- Гостевые посты в 3-5 маркетинговых изданиях
- Запуск РСЯ-кампаний

**Неделя 9-12: Scale**
- A/B тесты лендинга (заголовки, CTA, социальное доказательство)
- Ретаргетинг на посетителей лендинга
- Запуск реферальной программы
- Первые партнёрские интеграции

---

## 3. MOFU — Middle of Funnel (Нурсинг и квалификация)

### 3.1 Email Nurture Sequence

Текущие email-шаблоны в системе: `trial_started`, `payment_success`, `payment_failed`, `subscription_cancelled`. Нужно добавить nurture-серию.

| День | Тема письма | Цель | Триггер |
|------|-------------|------|---------|
| 0 | "Добро пожаловать! Вот ваш план на первые 24 часа" | Активация | `POST /auth/register` |
| 1 | "3 ошибки, которые убивают комментинг в Telegram" | Обучение | Cron / event |
| 3 | "Как бренд X получил 300% ROI на комментинге" | Social proof | Cron / event |
| 5 | "Ваш персональный аудит готов — посмотрите результаты" | Вовлечение | Если не заходил 3 дня |
| 7 | "До конца триала 7 дней — вот что вы можете успеть" | Urgency (если trial 14 дн) | Cron |
| 10 | "Топ-3 стратегии для вашей ниши: {ecom/edtech/saas}" | Персонализация | Cron / по use_case лида |
| 13 | "Завтра заканчивается триал — специальное предложение" | Конверсия | 1 день до конца trial |
| 14 | "Триал закончился. Вот что вы потеряете" | Loss aversion | День окончания trial |
| 21 | "Мы скучаем. -20% на первый месяц" | Winback | 7 дней после окончания trial |

### 3.2 Telegram Bot Nurture

Бот (`@dartvpn_neurocom_bot`) как канал нурсинга:

| Этап | Сообщение бота | Триггер |
|------|---------------|---------|
| После лида | "Спасибо за заявку! Вот ссылка на демо-запись (5 мин)" | Webhook от `POST /api/leads` |
| После регистрации | "Отлично! Вот 3 шага для быстрого старта" + inline-кнопки | `POST /auth/register` event |
| День 2 | "Вы загрузили аккаунты? Вот инструкция за 2 минуты" | Cron check: 0 аккаунтов |
| День 5 | "Ваш первый фарм готов к запуску. Нажмите Start" | Cron check: есть аккаунты, нет фармов |
| Перед концом trial | "Осталось 24 часа. Оплатите -> сохраните все настройки" | 1 день до trial_ends_at |

### 3.3 Демо-звонки

**Квалификация перед демо** (из лид-формы):
- Компания заполнена -> PQL (Product Qualified Lead)
- use_case = "ecom" или "agency" -> высокий приоритет
- utm_source = "yandex" -> платный трафик, выше intent

**Структура демо (15 мин)**:
1. Боль клиента (2 мин) — "Сколько вы сейчас тратите на продвижение в TG?"
2. Демо Channel Map (3 мин) — показать карту каналов в нише клиента
3. Демо AI-комментинг (5 мин) — живой запуск фарма с 1 аккаунтом
4. Результаты и ROI (3 мин) — "При 50 комментариях/день вы получите X подписчиков"
5. CTA (2 мин) — "Начните бесплатный триал прямо сейчас"

---

## 4. BOFU — Bottom of Funnel (Конверсия)

### 4.1 Тарифная сетка

Текущая модель из billing_service.py:

| Тариф | Цена/мес (руб) | Цена/мес (USD) | Аккаунты | Каналы | Комментов/день | AI-тир | Фармы |
|-------|----------------|----------------|----------|--------|----------------|--------|-------|
| **Free** | 0 | 0 | 1 | 10 | 10 | worker | 1 |
| **Starter** | 3 990 | $49 | 3 | 50 | 100 | worker | 2 |
| **Pro** | 7 990 | $99 | 10 | 200 | 500 | manager | 5 |
| **Business** | 14 990 | $179 | 25 | 500 | 1 500 | manager | 10 |
| **Agency** | 19 990 | $249 | 50 | 1 000 | 5 000 | boss | 20 |
| **Enterprise** | По запросу | Custom | Unlimited | Unlimited | Unlimited | boss | Unlimited |

**Trial**: 3 дня на плане Starter (настройка `BILLING_TRIAL_DAYS` в config).

### 4.2 Стратегия ценообразования

**Якорение**: Agency ($249) рядом с Pro ($99) делает Pro выгодным.
**Рекомендация**: Pro помечен как "recommended" на странице /pricing.
**Годовой план**: -20% при оплате за год (11 990 -> 9 590 руб/мес для Pro).

### 4.3 Обработка возражений

| Возражение | Ответ | Доказательство |
|-----------|-------|----------------|
| "Дорого" | "50 комментариев = 2-5 новых подписчиков/день. При LTV подписчика 50 руб, ROI за месяц = 300%+" | Калькулятор ROI |
| "Не знаю, работает ли" | "3 дня бесплатно. Увидите первые результаты за 24 часа" | Trial без карты |
| "GramGPT дешевле" | "GramGPT = $130 за 12 модулей. Наш Pro = $99 с AI manager tier и 500 комментариев/день" | Таблица сравнения |
| "Боюсь бана" | "Автономный прогрев, антифрод, health scoring, автоматический карантин" | Кейс KZ-аккаунта: 0 банов за 2 недели |
| "Сложно настроить" | "Wizard за 5 минут: загрузил сессии -> привязал прокси -> запустил фарм" | Видео-демо 5 мин |
| "Нет времени" | "AI делает 95% работы. Вы только выбираете стратегию и нишу" | Скриншот AI-ассистента |

### 4.4 Ускорители конверсии

- **Scarcity**: "Осталось 5 мест на Agency тариф в этом месяце"
- **Social proof**: Счётчик "247 брендов уже используют NC" на лендинге
- **Guarantee**: "Если за 14 дней не получите первых подписчиков — вернём деньги"
- **Urgency**: "-30% на первый месяц при оплате в течение 48 часов после триала"
- **Payment flexibility**: Stripe (карты, Apple Pay) + YooKassa (карты RU, SBP, ЮMoney)

---

## 5. Post-Sale — Онбординг, Upsell, Рефералы

### 5.1 Onboarding Flow (первые 7 дней)

| День | Milestone | Метрика успеха | Интервенция при неактивности |
|------|-----------|----------------|------------------------------|
| 0 | Регистрация + Trial активирован | `auth_users` row created | — |
| 0 | Onboarding Wizard завершён | Workspace + team created | Бот: "Нужна помощь с настройкой?" |
| 1 | Загружены аккаунты (min 1 session) | `accounts.count >= 1` | Email: "Инструкция по загрузке за 2 мин" |
| 1 | Привязан прокси | `proxy_id IS NOT NULL` | Бот: "Без прокси аккаунт будет забанен" |
| 2 | Запущен warmup | `warmup_sessions.count >= 1` | Email: "Прогрев — обязательный шаг" |
| 3 | Создан первый фарм | `farm_configs.count >= 1` | Бот: "Готовы запустить первый фарм?" |
| 5 | Отправлен первый комментарий | `comments.count >= 1` | Звонок от менеджера |
| 7 | 50+ комментариев | Retention signal | Email: "Вот ваши результаты за неделю" |

### 5.2 Upsell / Cross-sell матрица

| Текущий тариф | Триггер upsell | Предложение | Канал |
|---------------|---------------|-------------|-------|
| Free | 10/10 комментариев использовано | "Апгрейд до Starter: 100 комментов/день" | In-app banner + email |
| Starter | 80%+ лимита аккаунтов | "Pro: до 10 аккаунтов + AI manager" | In-app + бот |
| Pro | 5+ фармов создано | "Business: 25 аккаунтов + 1500 комм/день" | Email + менеджер |
| Business | Запрос на white label | "Agency: white label + 50 аккаунтов" | Менеджер |
| Любой | 11 месяцев подряд оплачено | "Годовой план: -20%" | Email + in-app |

### 5.3 Реферальная программа

**Механика**: Каждый платящий клиент получает уникальную ссылку.
- Приведённый клиент: -15% на первый месяц
- Реферер: 20% от первого платежа приведённого клиента (кредит на баланс)
- Агентства: 15-30% revenue share на весь срок жизни клиента

**Реализация**:
- utm_source tracking уже есть в `LeadSnapshot`
- Нужен endpoint: `POST /v1/referral/generate-link`
- Нужна таблица: `referrals (referrer_tenant_id, referred_tenant_id, commission_percent, status)`

### 5.4 Churn Prevention

| Сигнал | Метрика | Интервенция |
|--------|---------|-------------|
| Не заходил 7 дней | Last login > 7d | Email: "Мы заметили, что вы не заходили" |
| 0 комментариев за 3 дня | comments_today = 0 x3 | Бот: "Ваши фармы остановлены?" |
| Отменил подписку | `subscription.status = cancelled` | Email winback series (день 1, 7, 14, 30) |
| Health score падает | `avg_health < 50` | In-app alert + предложение помощи |
| Trial заканчивается без оплаты | `trial_ends_at - now < 24h` AND no payment | Push + email + бот |

---

## 6. Metrics Framework

### 6.1 TOFU метрики

| Метрика | Описание | Источник | Целевое значение |
|---------|----------|----------|------------------|
| **Website Visitors** | Уникальные посетители лендинга/мес | Яндекс.Метрика / PostHog | 5 000+ (месяц 3) |
| **Lead Form CR** | Visitors -> Leads | `POST /api/leads` / visitors | 3-5% |
| **Leads/месяц** | Новые лиды | `leads` table count | 150-250 (месяц 3) |
| **CPA Lead** | Стоимость привлечения лида | Ad spend / leads | < 800 руб |
| **Channel Mix** | % лидов по каналам | `utm_source` в leads | Organic > 40% к месяцу 6 |

### 6.2 MOFU метрики

| Метрика | Описание | Источник | Целевое значение |
|---------|----------|----------|------------------|
| **Lead -> Register CR** | Лиды -> регистрации | `auth_users` / `leads` | 20-30% |
| **Email Open Rate** | Открытия nurture-писем | Email service logs | > 35% |
| **Email Click Rate** | Клики в nurture-писмах | Email service logs | > 8% |
| **Demo Show Rate** | Записались -> пришли на демо | Calendly / CRM | > 60% |
| **Demo -> Trial CR** | Демо -> регистрация на триал | Manual tracking | > 50% |

### 6.3 BOFU метрики

| Метрика | Описание | Источник | Целевое значение |
|---------|----------|----------|------------------|
| **Register -> Trial CR** | Регистрации -> триал активирован | `subscriptions.status=trialing` | 80-90% (автоматический) |
| **Trial -> Paid CR** | Триал -> первая оплата | `payment_events.event_type=payment_succeeded` | 15-25% |
| **ARPU** | Средний доход на пользователя/мес | Revenue / active subs | 6 000-8 000 руб |
| **ACV** | Средний годовой контракт | ARPU x 12 x retention | 60 000-80 000 руб |
| **Time to First Payment** | Дни от регистрации до оплаты | `auth_users.created_at` -> первый payment | < 5 дней |

### 6.4 Post-Sale метрики

| Метрика | Описание | Источник | Целевое значение |
|---------|----------|----------|------------------|
| **Monthly Churn** | % отменённых подписок/мес | `subscription_cancelled` events | < 5% |
| **Net Revenue Retention** | Доход от когорты с учётом upsell/churn | Revenue calculations | > 110% |
| **NPS** | Net Promoter Score | Опрос (in-app / email) | > 40 |
| **Time to Value** | Дни до первого комментария | `comments` first row timestamp | < 3 дня |
| **DAU/MAU** | Ratio ежедневных к месячным юзерам | Auth logs | > 30% |
| **Referral Rate** | % клиентов, приведших реферала | Referral table | > 10% |
| **LTV** | Lifetime Value | ARPU / churn rate | > 40 000 руб |
| **LTV:CAC** | Ratio LTV к стоимости привлечения | LTV / CAC | > 3:1 |

### 6.5 Unit Economics Dashboard

```
CAC (Cost of Acquisition)
= (Ad spend + Sales cost + Content cost) / New paying customers
Целевой: < 10 000 руб

LTV (Lifetime Value)
= ARPU / Monthly Churn Rate
При ARPU = 7 000 руб, Churn = 5%: LTV = 140 000 руб

LTV:CAC = 140 000 / 10 000 = 14:1 (отлично)

Payback Period
= CAC / ARPU
= 10 000 / 7 000 = 1.4 месяца
```

---

## 7. Conversion Benchmarks — SaaS B2B в RU/CIS

### 7.1 Отраслевые бенчмарки (источники: OpenView, ChartMogul, SaaStr, RU-market данные)

| Переход | Pessimistic | Realistic | Optimistic | Наш целевой |
|---------|-------------|-----------|------------|-------------|
| Visitor -> Lead | 1-2% | 3-5% | 7-10% | **4%** |
| Lead -> Register | 10-15% | 20-30% | 35-45% | **25%** |
| Register -> Trial | 60-70% | 80-90% | 95%+ | **85%** |
| Trial -> Paid | 8-12% | 15-25% | 30-40% | **20%** |
| Monthly Churn (paid) | 7-10% | 4-6% | 2-3% | **5%** |
| Annual Churn (paid) | 50-70% | 35-50% | 20-30% | **45%** |
| Expansion Revenue | 5-10% | 15-25% | 30-50% | **20%** |
| Referral Rate | 2-5% | 8-12% | 15-25% | **10%** |

### 7.2 RU/CIS специфика

- **Средний чек ниже** мирового B2B SaaS на 40-60%: целевой ARPU $80-100 (мир $150-200)
- **Чувствительность к цене** выше: нужна рублёвая опция через YooKassa
- **Telegram как канал** продаж работает лучше email: конверсия бот-нурсинга > email в 2-3x
- **Trust barrier** высокий: нужны кейсы на русском, отзывы, гарантия возврата
- **Длинный sales cycle** для Agency/Enterprise: 2-6 недель (vs 1-2 недели для Starter/Pro)
- **СБП и ЮMoney** критичны: 30-40% RU-платежей идут через них, не через карты

### 7.3 Воронка первых 6 месяцев (прогноз)

| Месяц | Visitors | Leads | Registers | Trials | Paid | MRR (руб) |
|-------|----------|-------|-----------|--------|------|-----------|
| 1 | 1 000 | 40 | 10 | 8 | 2 | 12 000 |
| 2 | 2 500 | 100 | 25 | 21 | 4 | 36 000 |
| 3 | 5 000 | 200 | 50 | 42 | 8 | 72 000 |
| 4 | 8 000 | 320 | 80 | 68 | 14 | 126 000 |
| 5 | 12 000 | 480 | 120 | 102 | 20 | 190 000 |
| 6 | 15 000 | 600 | 150 | 127 | 25 | 250 000 |

Допущения: CR visitor->lead 4%, lead->register 25%, register->trial 85%, trial->paid 20%, ARPU 7 500 руб, churn 5%/мес.

---

## 8. Lead Scoring Model

### 8.1 Демографический скоринг (Profile Score)

| Фактор | Значение | Баллы |
|--------|----------|-------|
| **Компания указана** | Да | +15 |
| **Компания указана** | Нет | 0 |
| **Use case** | ecom | +20 |
| **Use case** | agency | +25 |
| **Use case** | edtech | +15 |
| **Use case** | saas | +15 |
| **Use case** | другое / пусто | +5 |
| **Telegram username** | Указан | +10 |
| **Telegram username** | Не указан | 0 |
| **UTM source** | yandex / google (paid) | +10 |
| **UTM source** | referral | +15 |
| **UTM source** | telegram | +10 |
| **UTM source** | organic / direct | +5 |
| **Email домен** | Корпоративный (@company.ru) | +15 |
| **Email домен** | Бесплатный (@gmail, @mail.ru) | +5 |

### 8.2 Поведенческий скоринг (Engagement Score)

| Действие | Баллы |
|----------|-------|
| Зарегистрировался | +20 |
| Завершил Onboarding Wizard | +15 |
| Загрузил >= 1 аккаунт | +20 |
| Привязал прокси | +10 |
| Запустил warmup | +15 |
| Создал фарм | +20 |
| Отправил >= 10 комментариев | +25 |
| Использовал AI-ассистент | +10 |
| Посетил /pricing | +10 |
| Открыл 3+ nurture-писем | +10 |
| Пришёл на демо | +20 |
| Повторный вход через 3+ дня | +10 |
| Пригласил team member | +15 |

### 8.3 Категории лидов

| Категория | Баллы | Действие |
|-----------|-------|----------|
| **Cold** | 0-25 | Nurture email + бот серия |
| **Warm** | 26-50 | Email + персональное сообщение в Telegram |
| **Hot** | 51-75 | Приглашение на демо + предложение помочь с настройкой |
| **Sales-Ready** | 76-100 | Прямой звонок/сообщение от менеджера |
| **Product-Qualified (PQL)** | 100+ | Приоритетная обработка + персональный onboarding |

### 8.4 PQL (Product Qualified Lead) определение

Лид становится PQL когда выполнены ВСЕ условия:
1. Зарегистрирован (auth_users row)
2. Загрузил >= 1 аккаунт
3. Отправил >= 5 комментариев
4. Заходил >= 3 дней из последних 7

PQL = самая ценная категория. Конверсия PQL -> Paid обычно 40-60% (vs 15-25% для обычного trial).

### 8.5 Реализация скоринга

Текущее состояние: `LeadSnapshot` в `lead_funnel.py` содержит базовые поля (name, email, company, telegram_username, use_case, utm_source). Для полноценного скоринга нужно:

1. **Таблица `lead_scores`**: `lead_id`, `profile_score`, `engagement_score`, `total_score`, `category`, `updated_at`
2. **Event tracking**: логировать поведенческие события в `usage_events` или отдельную таблицу
3. **Cron-job**: пересчитывать скоры ежечасно
4. **API endpoint**: `GET /v1/internal/leads/scored` — для CRM/менеджера
5. **Telegram notify**: alert в админ-бот при score > 75

---

## 9. Технические GAP-ы для реализации воронки

### Что уже есть в системе

| Компонент | Статус | Файл/Endpoint |
|-----------|--------|---------------|
| Лид-форма на лендинге | Работает | `POST /api/leads` -> `lead_funnel.py` |
| Google Sheets mirror | Работает | `mirror_lead_to_google_sheets()` |
| Telegram нотификации | Работает | `send_admin_lead_notification()`, `send_digest_lead_notification()` |
| Email/password регистрация | Работает | `POST /auth/register` |
| Telegram auth | Работает | `POST /auth/telegram/verify` |
| Trial автоматический | Работает | `create_trial()` -> 3 дня Starter |
| Stripe webhooks | Работает | `handle_stripe_webhook()` |
| YooKassa webhooks | Работает | `handle_yookassa_webhook()` |
| Email-рассылки | 6 шаблонов | `core/email_service.py` |
| Billing page | Работает | BillingPage.tsx |
| Pricing page | Работает | `/pricing` (Jinja2) |

### Что нужно доработать

| Компонент | Приоритет | Описание |
|-----------|-----------|----------|
| **Nurture email series** | P0 | 9 писем (см. раздел 3.1), cron-триггер по дням после регистрации |
| **Lead scoring** | P1 | Таблица + cron + API (см. раздел 8.5) |
| **Referral system** | P1 | Таблица referrals + endpoint generate-link + tracking |
| **In-app upsell banners** | P1 | Компонент при приближении к лимиту плана |
| **Trial extension to 14 days** | P1 | Изменить `BILLING_TRIAL_DAYS` с 3 на 14 |
| **Winback email series** | P2 | 4 письма после отмены подписки |
| **ROI калькулятор** | P2 | Интерактивная страница на лендинге |
| **Churn prediction** | P2 | ML-модель на usage_events |
| **CRM интеграция** | P3 | Webhook в AmoCRM / Bitrix24 |
| **PostHog/Amplitude** | P3 | Product analytics для поведенческих метрик |

---

## 10. Приоритетный план внедрения (следующие 4 спринта)

### Sprint A: Nurture Foundation (1 неделя)
- Расширить `core/email_service.py` до 15 шаблонов
- Добавить cron-job nurture scheduler
- Увеличить trial до 14 дней
- Добавить onboarding milestone tracking

### Sprint B: Lead Scoring + CRM (1 неделя)
- Таблица `lead_scores` + migration
- Скоринг-движок (profile + engagement)
- `GET /v1/internal/leads/scored`
- Telegram alert при score > 75

### Sprint C: Referral + Upsell (1 неделя)
- Таблица `referrals` + migration
- `POST /v1/referral/generate-link`
- In-app upsell banners при приближении к лимитам
- Referral page в frontend

### Sprint D: Analytics + Optimization (1 неделя)
- PostHog / встроенная аналитика для TOFU метрик
- A/B тесты лендинга (headline, CTA)
- Winback email series
- Churn signals dashboard
