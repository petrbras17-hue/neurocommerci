# NEURO COMMENTING — План построения команды

## Отправная точка: Суперсила AI-native основателя

**Факт**: 1 founder + Claude Code = 33 спринта за 8 дней.
Это эквивалент работы команды из 8-12 инженеров за 2-3 месяца. Этот факт фундаментально меняет стратегию найма: вместо раздувания штата, усиливаем founder leverage через точечные найм-решения.

**Ключевой принцип**: нанимаем людей, которые делают то, что AI пока не может:
- продавать вживую
- строить человеческие отношения с клиентами
- принимать бизнес-решения на основе рыночной интуиции
- представлять компанию публично

---

## Phase 1: Founding Team (0-6 месяцев, 1-3 человека)

### Текущий состав
| Роль | Человек | Статус |
|------|---------|--------|
| CEO / Founder / Solo Engineer | Основатель | Активен |
| AI Engineering Partner | Claude Code (Opus) | 24/7 |

### Кого нанять первым

**Hire #1: Growth Lead / Head of Sales (месяц 1-2)**

Почему первым: продукт уже есть (33 спринта!), нет клиентов. Bottleneck сейчас не код, а revenue.

Профиль:
- 3-5 лет в B2B SaaS продажах на RU/CIS рынке
- Опыт в SMM/маркетинг-тех продуктах
- Умеет продавать от $100 до $500/мес подписки
- Понимает Telegram-экосистему
- Готов быть "первым продажником" (cold outreach, демо, клоузинг)

Обязанности:
- Первые 50 платящих клиентов
- Построение воронки: лид -> демо -> trial -> conversion
- Обратная связь от рынка в продукт
- Контент для лидогенерации (кейсы, посты в Telegram)

Где искать:
- Telegram-сообщества SMM и маркетинга (РФ/CIS)
- LinkedIn (фильтр: SaaS sales + RU market)
- Бывшие менеджеры проектов в SMM-агентствах
- Headhunter.ru, Habr Career

Компенсация:
- Зарплата: 150-250K руб/мес (fix) + до 100% бонус от перевыполнения
- Equity: 1-3% (vesting 4 года, cliff 1 год)
- Опцион на рост до Head of Revenue

**Hire #2: Customer Success / Support (месяц 3-4)**

Почему вторым: когда придут первые клиенты, нужен человек, который:
- Онбордит (показывает интерфейс, помогает настроить аккаунты)
- Удерживает (отвечает в чате, решает проблемы)
- Собирает фидбек (что сломалось, чего не хватает)

Профиль:
- Опыт в саппорте SaaS или tech-продуктов
- Русский native, английский не обязателен на Phase 1
- Технически грамотный (может объяснить прокси, сессии, ферму)
- Терпеливый и дотошный

Компенсация:
- Зарплата: 80-120K руб/мес
- Equity: 0.3-0.5%

### Co-founder vs Employee: стратегическое решение

| Сценарий | Когда выбирать |
|----------|---------------|
| **Co-founder CTO** | Если основатель хочет отойти от кода и сфокусироваться на бизнесе. Equity: 10-20%. Искать через нетворк, не через HH. |
| **Co-founder Growth** | Если основатель хочет остаться техническим, а бизнес делегировать полностью. Equity: 10-15%. |
| **Не брать кофаундера** | Если AI-leverage продолжает работать и основатель справляется с кодом + бизнесом. Тогда нанимаем employees. |

**Рекомендация для NEURO COMMENTING**: НЕ брать кофаундера на Phase 1. Причина: продукт уже построен, AI-leverage работает. Кофаундер имеет смысл когда нужно второе ядро компетенций (deep ML, enterprise sales, regulatory). Сейчас нужны исполнители, а не совладельцы.

### Org Chart Phase 1

```
Founder / CEO
  |
  +-- Claude Code (AI Engineering)
  |
  +-- Growth Lead (продажи + лидген)
  |
  +-- Customer Success (онбординг + саппорт)  [месяц 3-4]
```

### Зарплатные бенчмарки Phase 1 (RU/CIS remote)

| Роль | Fix (руб/мес) | Бонус | Equity |
|------|--------------|-------|--------|
| Growth Lead | 150-250K | До 100% от плана | 1-3% |
| Customer Success | 80-120K | KPI-бонус 20-30% | 0.3-0.5% |
| Общий ФОТ | 230-370K/мес | | |

---

## Phase 2: Seed Team (6-18 месяцев, 5-10 человек)

### Предпосылки входа в Phase 2
- MRR > $10K (50-100 платящих клиентов)
- Product-market fit подтвержден (NPS > 40, retention > 80% M3)
- Seed раунд закрыт ($500K-$1.5M)

### Приоритет найма

| Приоритет | Роль | Когда | Зачем |
|-----------|------|-------|-------|
| 1 | Senior Backend Engineer | Месяц 6-7 | Разделить code ownership, ночные дежурства |
| 2 | Frontend/Product Engineer | Месяц 7-8 | UX-итерации быстрее, A/B тесты |
| 3 | Head of Growth/Marketing | Месяц 8-10 | Масштабировать каналы привлечения |
| 4 | Second Sales Rep | Месяц 10-12 | Удвоить pipeline |
| 5 | DevOps / SRE | Месяц 12-14 | Масштабирование инфры, CI/CD, monitoring |
| 6 | Data Analyst | Месяц 14-16 | Unit economics, cohort analysis, pricing optimization |
| 7 | Second CS / Account Manager | Месяц 16-18 | При 200+ клиентах |

### Org Chart Phase 2

```
Founder / CEO
  |
  +-- Engineering (Founder + 2 engineers + Claude Code)
  |     +-- Senior Backend (Python/FastAPI/PostgreSQL)
  |     +-- Frontend/Product Engineer (React/TypeScript)
  |     +-- DevOps/SRE [позже]
  |
  +-- Go-to-Market (Growth Lead -> Head of GTM)
  |     +-- Sales Rep #2
  |     +-- Head of Growth/Marketing
  |     +-- Data Analyst [позже]
  |
  +-- Customer Success
        +-- CS Lead (бывший первый CS)
        +-- Account Manager [позже]
```

### Инженерный найм: что важно

**Senior Backend Engineer** -- самый критичный технический найм:
- Python 3.10+, FastAPI, SQLAlchemy, asyncio
- PostgreSQL (RLS, партицирование, оптимизация)
- Redis, очереди, background jobs
- Telegram API (Telethon/Pyrogram) -- сильный плюс
- Готовность работать с AI-generated кодом (review, improve, extend)

Где искать:
- Habr Career (Python senior, FastAPI)
- Telegram-чаты Python-разработчиков
- GitHub (контрибьюторы в FastAPI/SQLAlchemy)
- Рефералы через Telegram-dev сообщества

**Критерий**: человек должен быть комфортен с AI-first workflow. Код пишет Claude, человек ревьюит, тестирует, архитектурит. Если кандидат считает AI-код "ненастоящим" -- не подходит.

### Remote-first vs Office

**Решение: Remote-first с опциональными meetups.**

Почему:
1. RU/CIS talent pool шире при remote (Казахстан, Узбекистан, Грузия, Сербия)
2. Экономия на офисе = +1 инженер
3. Продукт уже построен remote-first одним человеком
4. Конкуренты (GramGPT) тоже remote

Операционная модель:
- Async-first коммуникация (Telegram чаты + Notion/Linear)
- Daily standup 15 мин (video)
- Weekly sync 1 час
- Квартальные оффлайн-встречи (2-3 дня, город по ротации)

### Баланс Engineering vs GTM

| Фаза MRR | Engineering | GTM | Ratio |
|-----------|------------|-----|-------|
| $0-10K | 2 (founder + AI) | 2 (sales + CS) | 50/50 |
| $10-30K | 3-4 | 3-4 | 50/50 |
| $30-100K | 4-5 | 5-6 | 45/55 |

**Правило**: пока нет PMF, инженеров и продажников примерно поровну. После PMF -- GTM растет быстрее.

### Зарплатные бенчмарки Phase 2 (RU/CIS remote, 2026)

| Роль | Fix (руб/мес) | Equity |
|------|--------------|--------|
| Senior Backend | 300-450K | 0.5-1.5% |
| Frontend/Product | 250-400K | 0.5-1.0% |
| Head of Growth | 250-400K | 1.0-2.0% |
| Sales Rep #2 | 120-180K + бонус | 0.3-0.5% |
| DevOps/SRE | 250-350K | 0.3-0.7% |
| Data Analyst | 180-280K | 0.2-0.5% |
| Account Manager | 100-150K | 0.2-0.3% |
| **Общий ФОТ** | **~2-3M руб/мес** | |

---

## Phase 3: Series A Team (18-36 месяцев, 20-40 человек)

### Предпосылки входа в Phase 3
- ARR > $1M
- 500+ платящих клиентов
- Series A закрыт ($5-15M)
- PMF подтвержден в 2+ сегментах (SMM-агентства + бренды)
- Unit economics положительная (LTV/CAC > 3)

### Департаменты и структура

```
CEO / Founder
  |
  +-- VP Engineering (внутренний рост или найм)
  |     +-- Platform Team (3-4 чел)
  |     |     Backend, API, Database, Integrations
  |     +-- AI/ML Team (2-3 чел)
  |     |     AI Router, Model optimization, Content generation
  |     +-- Frontend Team (2-3 чел)
  |     |     React, Mobile web, UX
  |     +-- Infrastructure Team (2 чел)
  |     |     DevOps, SRE, Security
  |     +-- QA (1-2 чел)
  |           Manual + Automation
  |
  +-- VP Product
  |     +-- Product Manager (Core)
  |     +-- Product Manager (Growth)
  |     +-- Product Designer / UX
  |     +-- Data Analyst
  |
  +-- VP Sales / Head of Revenue
  |     +-- Sales Team (4-6 чел)
  |     |     Account Executives + SDRs
  |     +-- Partnerships Manager
  |
  +-- Head of Marketing
  |     +-- Content Marketing (2 чел)
  |     +-- Performance Marketing
  |     +-- Community Manager
  |
  +-- Head of Customer Success
  |     +-- CS Managers (3-4 чел)
  |     +-- Technical Support (2 чел)
  |     +-- Onboarding Specialist
  |
  +-- Head of Operations / Finance
        +-- Finance/Accounting
        +-- Legal
        +-- HR/People Ops
```

### VP-level найм: кого и когда

| Роль | Когда | Зачем | Зарплата (USD) | Equity |
|------|-------|-------|---------------|--------|
| VP Engineering | Месяц 18-20 | Масштабировать команду, архитектурные решения | $8-15K/мес | 1.5-3.0% |
| VP Product | Месяц 20-24 | Product strategy, roadmap, metrics | $7-12K/мес | 1.0-2.0% |
| VP Sales | Месяц 22-26 | Предсказуемый revenue engine | $8-15K/мес + бонус | 1.0-2.5% |
| Head of Marketing | Месяц 24-28 | Brand, demand gen, content | $5-10K/мес | 0.5-1.5% |

### Engineering Team Structure

**Platform Team (ядро продукта)**:
- 1 Tech Lead (бывший Senior Backend из Phase 2)
- 2-3 Backend Engineers
- Фокус: API, PostgreSQL, Redis, Telegram integrations, billing

**AI/ML Team**:
- 1 ML Engineer (LLM fine-tuning, prompt engineering)
- 1 AI Engineer (маршрутизация, cost optimization, quality)
- Фокус: улучшение комментариев, антидетект, персонализация

**Frontend Team**:
- 1 Frontend Lead
- 1-2 Frontend Engineers
- Фокус: React, дашборды, real-time UI, мобильная адаптация

**Infrastructure Team**:
- 1 SRE / Platform Engineer
- 1 DevOps / Security Engineer
- Фокус: Kubernetes (если масштаб требует), monitoring, CI/CD, compliance

### Найм по географии (Phase 3)

| Регион | Роли | Преимущества |
|--------|------|-------------|
| Россия (remote) | Backend, AI, Support | Глубокое знание рынка, Telegram-экспертиза |
| Казахстан | Engineering, CS | Русскоязычный, дешевле Москвы, удобный часовой пояс |
| Узбекистан | Junior engineering, QA | Растущий IT-рынок, конкурентные зарплаты |
| Грузия/Армения | Engineering | Русскоязычные инженеры, IT-хабы |
| Сербия/Черногория | Engineering | Если нужен EU-bridge |
| Турция/ОАЭ | Sales, BD | Если выходим на MENA рынок |

---

## Phase 4: Scale Team (36-60 месяцев, 100+ человек)

### Предпосылки
- ARR > $10M
- 5,000+ клиентов
- Series B закрыт ($30-80M)
- Международная экспансия начата

### C-Suite завершение

| Роль | Когда | Фокус |
|------|-------|-------|
| CFO | Месяц 36-40 | Финансовое планирование, fundraising support, unit economics |
| CTO | Месяц 36-42 | Если VP Eng не вырос; архитектура для 10x масштаба |
| COO | Месяц 40-48 | Операционная машина: HR, legal, finance, offices |
| CMO | Месяц 42-48 | Brand на международном уровне |
| CRO | Месяц 48-54 | Unified revenue: sales + CS + expansion |

### Международные офисы

| Хаб | Зачем | Команда | Timeline |
|-----|-------|---------|----------|
| **Дубай (ОАЭ)** | MENA sales + HQ для международных клиентов | 5-10 чел: Sales, BD, CS | Месяц 36-42 |
| **Стамбул** | Турция + Центральная Азия | 3-5 чел: Sales, CS | Месяц 42-48 |
| **Сан-Паулу** | LATAM (Бразилия -- огромный Telegram рынок) | 3-5 чел: Sales, CS, локализация | Месяц 48-54 |
| **Белград** | EU engineering hub (визовый режим, налоги) | 5-10 чел: Engineering | Месяц 42-48 |

### Региональные команды

Каждый международный хаб строится по формуле:
1. Regional Sales Lead (первый найм)
2. Customer Success (второй)
3. Локализация продукта (третий)
4. Маркетинг/community (четвертый)

### Board Advisors (набирать с Phase 2)

| Тип | Зачем | Компенсация |
|-----|-------|-------------|
| Telegram Ecosystem Expert | Навигация в Telegram-бизнесе, связи с командой Telegram | 0.25-0.5% equity |
| SaaS GTM Advisor | Построение sales machine, pricing, expansion | 0.25-0.5% equity |
| RU/CIS Market Advisor | Регуляторика, partnerships, enterprise contacts | 0.25-0.5% equity |
| International Growth Advisor | MENA/LATAM expansion, international fundraising | 0.25-0.5% equity |
| Technical Advisor (AI/ML) | Архитектура AI-систем на масштабе | 0.1-0.25% equity |

### Org Chart Phase 4 (100+ человек)

```
Board of Directors + Advisors
  |
CEO / Founder
  |
  +-- CTO
  |     +-- VP Engineering
  |     |     +-- Platform Team (8-10)
  |     |     +-- AI/ML Team (5-7)
  |     |     +-- Frontend Team (5-6)
  |     |     +-- Mobile Team (3-4)
  |     |     +-- Infrastructure/SRE (4-5)
  |     |     +-- QA/Test Automation (3-4)
  |     +-- VP Product
  |           +-- PM Core (2)
  |           +-- PM Growth (1)
  |           +-- PM Enterprise (1)
  |           +-- Design Team (3-4)
  |           +-- Data/Analytics (3-4)
  |
  +-- CRO (Chief Revenue Officer)
  |     +-- VP Sales
  |     |     +-- Sales RU/CIS (6-8)
  |     |     +-- Sales MENA (3-4)
  |     |     +-- Sales LATAM (2-3)
  |     |     +-- Sales Ops (2)
  |     +-- Head of CS
  |     |     +-- CS Managers (6-8)
  |     |     +-- Technical Support (4-5)
  |     |     +-- Onboarding (2-3)
  |     +-- Head of Partnerships
  |           +-- Agency Partnerships (2-3)
  |           +-- Technology Partnerships (1-2)
  |
  +-- CMO
  |     +-- Content Team (4-5)
  |     +-- Performance Marketing (3-4)
  |     +-- Community (2-3)
  |     +-- Brand/PR (2)
  |
  +-- COO
  |     +-- HR/People Ops (3-4)
  |     +-- Legal (2)
  |     +-- Office Ops (по хабам)
  |
  +-- CFO
        +-- Finance (2-3)
        +-- Accounting (1-2)
        +-- FP&A (1)
```

---

## Culture and Values: AI-First Operating System

### Манифест культуры NEURO COMMENTING

**1. AI -- это член команды, а не инструмент.**
- Claude Code имеет свой "рабочий контекст" (CLAUDE.md, memory, skills)
- Инженеры работают В ПАРЕ с AI, а не вместо или параллельно
- Метрика: сколько спринтов в неделю, а не сколько строк кода написано руками

**2. Скорость > Перфекционизм.**
- 33 спринта за 8 дней -- это ДНК компании
- Ship fast, fix fast, learn fast
- "Работает в проде" > "Идеально в теории"

**3. Remote-first, async-first.**
- Документация вместо митингов
- Telegram-чаты вместо email
- Записи экрана вместо созвонов
- Совпадение 4+ часов рабочего времени -- единственное требование к таймзонам

**4. Радикальная прозрачность.**
- Все метрики (MRR, churn, NPS) видны всей команде
- Change register -- живой документ для всех
- Решения документируются с обоснованием

**5. Ownership > Process.**
- Каждый фичер имеет одного owner
- Owner решает как делать (с AI или без)
- Процесс добавляется только когда что-то сломалось, не заранее

### AI-First Hiring Criteria

Каждый кандидат (даже не-инженер) проверяется на:
1. **AI Comfort**: использует ли AI в повседневной работе?
2. **Adaptability**: готов ли менять workflow под новые AI-возможности?
3. **Review Mindset**: может ли ревьюить AI-output критически?
4. **Multiplier Thinking**: думает ли категориями "как сделать 10x с AI?"

### Remote-First Operating Model

**Инструментарий**:
| Категория | Инструмент |
|-----------|-----------|
| Коммуникация | Telegram (чаты + каналы) |
| Задачи | Linear или Notion |
| Код | GitHub + Claude Code |
| Документация | Markdown в репо + Notion |
| Видео | Google Meet / Zoom (только когда нужно) |
| Design | Figma |
| Analytics | PostHog / Metabase |
| Мониторинг | Sentry + Grafana |

**Ритуалы**:
| Ритуал | Частота | Формат | Длительность |
|--------|---------|--------|-------------|
| Daily standup | Ежедневно | Async текст в Telegram | 2 мин на человека |
| Weekly sync | Еженедельно | Video call | 45-60 мин |
| Sprint review | Каждые 2 недели | Video + демо | 60-90 мин |
| Retro | Ежемесячно | Video | 45 мин |
| All-hands | Ежемесячно | Video + slides | 30 мин |
| Offsite | Ежеквартально | Оффлайн 2-3 дня | -- |

### Compensation Philosophy

**Принцип: Equity-heavy early, cash-heavy later.**

| Фаза | Base Cash | Equity Weight | Логика |
|------|-----------|--------------|--------|
| Phase 1 (pre-seed) | Ниже рынка на 20-30% | Высокий equity | Ранний риск = ранняя награда |
| Phase 2 (seed) | На уровне рынка | Умеренный equity | Привлекаем сильных людей |
| Phase 3 (Series A) | На уровне / выше рынка | Стандартный equity | Конкуренция за таланты |
| Phase 4 (Series B+) | Выше рынка | RSU / опционы | Retention, не привлечение |

**Бенефиты (с Phase 2)**:
- Оплата коворкинга (если нужен)
- Бюджет на обучение: $1000/год
- Бюджет на оборудование: $2000 при старте
- Оплачиваемые оффсайты
- Дополнительные выходные (день рождения + 2 mental health дня)

### Hiring Funnel Metrics (целевые)

| Метрика | Целевое значение |
|---------|-----------------|
| Time to hire (от заявки до оффера) | < 21 день |
| Offer acceptance rate | > 80% |
| 90-day retention | > 90% |
| Source: referral share | > 40% |
| Source: inbound share | > 30% |
| Source: outbound share | < 30% |
| Diversity: география (стран) | 3+ к Phase 2 |
| Interview-to-offer ratio | 5:1 - 8:1 |
| Cost per hire | < $2000 (Phase 1-2), < $5000 (Phase 3+) |

---

## Equity Plan: ESOP и распределение

### Общий пул

| Параметр | Значение |
|----------|---------|
| ESOP Pool (от fully diluted) | 15-20% |
| Рекомендуемый начальный размер | 15% |
| Резерв на расширение (Phase 3+) | +5% (доводим до 20%) |

### Vesting Schedule

**Стандартный**:
- 4 года vesting
- 1 год cliff
- Ежемесячный vesting после cliff
- Single trigger acceleration: нет
- Double trigger acceleration: да (при M&A + увольнении)

**Для кофаундера** (если будет):
- 4 года vesting
- 6 месяцев cliff (ускоренный, т.к. уже есть продукт)
- Ежемесячный vesting после cliff

**Для advisors**:
- 2 года vesting
- 3 месяца cliff
- Ежеквартальный vesting

### Распределение Equity по ролям

| Уровень | Equity Range | Примеры |
|---------|-------------|---------|
| Co-founder | 10-20% | CTO co-founder |
| C-level (Phase 3+) | 1.5-3.0% | VP Engineering, VP Sales |
| Director / Head | 0.5-1.5% | Head of Growth, Tech Lead |
| Senior IC | 0.3-0.7% | Senior Engineer, Senior PM |
| Mid-level | 0.1-0.3% | Engineer, Sales Rep, Designer |
| Junior / Support | 0.05-0.15% | Junior Engineer, CS |
| Advisor | 0.1-0.5% | Board advisors |

### Equity Budget по фазам

| Фаза | Кол-во человек | Средний equity | Общий расход из пула |
|------|---------------|---------------|---------------------|
| Phase 1 | 2-3 | 1.5% | 2-4% |
| Phase 2 | 5-10 | 0.5% | 3-5% |
| Phase 3 | 20-40 | 0.2% | 4-6% |
| Phase 4 | 100+ | 0.05% | 3-5% |
| **Итого** | | | **12-20%** |

### Cap Table эволюция (модель)

| Этап | Founder | ESOP | Investors | Advisors |
|------|---------|------|-----------|----------|
| Начало | 100% | 0% | 0% | 0% |
| ESOP создан | 85% | 15% | 0% | 0% |
| Pre-Seed ($300K) | 76.5% | 13.5% | 10% | 0% |
| Advisors | 75% | 13% | 10% | 2% |
| Seed ($1M) | 60% | 12% | 26% | 2% |
| Series A ($10M) | 48% | 15%* | 35% | 2% |
| Series B ($40M) | 38% | 15% | 45% | 2% |

*ESOP pool пополняется при Series A

**Ключевое правило**: founder сохраняет >50% голосов через Phase 2, >35% через Phase 4. Контроль через dual-class shares если нужно.

---

## Timeline: сводная таблица найма

| Месяц | Найм | Headcount | MRR Target |
|-------|------|-----------|-----------|
| 1-2 | Growth Lead | 2 | $0-2K |
| 3-4 | Customer Success | 3 | $2-5K |
| 6-7 | Senior Backend Engineer | 4 | $5-10K |
| 7-8 | Frontend/Product Engineer | 5 | $8-15K |
| 8-10 | Head of Growth/Marketing | 6 | $10-20K |
| 10-12 | Sales Rep #2 | 7 | $15-30K |
| 12-14 | DevOps/SRE | 8 | $20-40K |
| 14-16 | Data Analyst | 9 | $30-50K |
| 16-18 | Account Manager #2 | 10 | $40-70K |
| 18-20 | VP Engineering | 12 | $70-100K |
| 20-24 | VP Product + PM + Designer | 16 | $80-150K |
| 24-30 | Sales team expansion + CS | 25 | $150-300K |
| 30-36 | AI/ML team + Infra | 35 | $300-500K |
| 36-42 | Dubai office + C-suite | 50 | $500-800K |
| 42-48 | Istanbul + Belgrade hubs | 70 | $800K-1.2M |
| 48-60 | LATAM + full scale | 100+ | $1.2M+ |

---

## Риски и контр-меры

| Риск | Вероятность | Контр-мера |
|------|------------|-----------|
| Не найти Growth Lead с Telegram-экспертизой | Средняя | Расширить поиск на SMM-агентства, предложить обучение продукту |
| Senior Backend не хочет работать с AI-кодом | Высокая | Фильтровать на собеседовании, давать тестовое задание с Claude |
| Кофаундер-конфликт | Средняя | Не брать кофаундера без 6 мес совместной работы |
| Раздувание штата раньше PMF | Высокая | Жесткое правило: каждый найм привязан к MRR milestone |
| Remote-команда теряет культуру | Средняя | Оффсайты, общий Telegram-канал, публичные метрики |
| Ключевой человек уходит с клиентами | Средняя | Non-compete + non-solicit, equity cliff как retention |

---

## Чек-лист: что сделать прямо сейчас

- [ ] Написать вакансию Growth Lead и разместить в 5 Telegram-чатах
- [ ] Подготовить sales deck / product demo для показа кандидатам
- [ ] Определить первый MRR milestone для найма CS ($2K MRR)
- [ ] Создать ESOP pool юридически (через юриста, не шаблоном)
- [ ] Выбрать юрисдикцию компании для equity (РФ ООО, или Delaware C-Corp, или Dubai FZE)
- [ ] Начать вести список потенциальных advisors из Telegram-экосистемы
- [ ] Подготовить onboarding-документ для первого найма (как работаем, инструменты, ритуалы)
