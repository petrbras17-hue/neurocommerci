# RUNBOOK

## Назначение

Этот документ фиксирует внутренний порядок эксплуатации сервиса:

- запуск контейнеров;
- загрузка и замена `.session/.json`;
- recovery после замены сессий;
- packaging и manual gate;
- ведение каналов и parser research flow;
- ежедневные проверки runtime;
- разбор типовых blocker-ов.

`QUICK_START.md` оставляет быстрый вводный сценарий.
`RUNBOOK.md` нужен для повседневной эксплуатации и инцидентов.

## Сервисы

- `bot`: control-plane и Telegram admin UI
- `db`: PostgreSQL
- `redis`: queue/leases/worker state
- `packager`: packaging worker
- `worker_a`, `worker_b`: pinned workers
- `worker`: dynamic worker

## Базовый запуск

```bash
docker compose up -d bot db redis packager worker_a worker_b
docker compose ps
```

Проверка статуса:

```bash
docker compose exec -T bot python scripts/runtime_status.py --json
docker compose logs --tail=120 bot packager worker worker_a worker_b
```

## Канонический layout сессий

Все актуальные session assets должны лежать в tenant layout:

- `data/sessions/<user_id>/<phone>.session`
- `data/sessions/<user_id>/<phone>.json`

Для `user_id=1`:

- `data/sessions/1/79637415377.session`
- `data/sessions/1/79637415377.json`

Если layout старый:

```bash
python3 scripts/reconcile_accounts_with_sessions.py --dry-run --json
python3 scripts/reconcile_accounts_with_sessions.py --migrate-layout --json
```

## Recovery после замены сессий

После каждой замены `.session/.json`:

```bash
docker compose exec -T bot python scripts/recover_after_session_reload.py \
  --user-id 1 \
  --set-parser-first-authorized \
  --clear-worker-claims \
  --json
```

Затем:

```bash
docker compose exec -T bot python scripts/runtime_status.py --json
docker compose logs --tail=120 bot packager worker worker_a worker_b
```

Ожидаемый результат recovery:

- `status` переходит в `active`
- `health_status` переходит в `alive`
- parser reassigned только если старый parser-account невалиден
- stale worker claims очищены

## Manual Recovery Queue в боте

В Telegram admin-боте:

1. `Аккаунты -> 🧯 Manual Recovery Queue`
2. Открыть карточку проблемного аккаунта
3. Посмотреть:
   - `status`
   - `health`
   - `lifecycle`
   - `primary blocker`
   - `next step`
4. Если аккаунт уже восстановлен и готов к packaging:
   - нажать `🎨 Queue packaging`
5. Если аккаунт ещё не восстановлен:
   - заменить `.session/.json`
   - нажать `🔐 Refresh auth audit`
   - вернуться в очередь

Recovery queue нужна как операторский экран, а не как замена recovery-команды.

## Packaging flow

Через бот:

1. `Аккаунты -> 🎨 Упаковка профилей (AI)`
2. `🎯 Упаковать 1 аккаунт`
3. Ввести номер телефона

Через CLI:

```bash
docker compose exec -T bot python scripts/enqueue_packaging_phone.py \
  --phone +79991234567 \
  --json
```

Проверка:

```bash
docker compose logs -f packager
docker compose exec -T bot python scripts/runtime_status.py --json
```

Ожидаемый переход:

- `uploaded` -> `packaging`
- затем `warming_up` или `gate_review`

## Wizard и Gate

После packaging:

1. `Аккаунты -> Wizard (manual gate)`
2. Пройти шаги:
   - `profile`
   - `channel`
   - `content`
   - `warmup`
3. Аккаунт перейдёт в `gate_review`
4. Админ:
   - `Аккаунты -> Gate review -> APPROVE`

Только после approve:

- `lifecycle_stage=active_commenting`

## Каналы

Для своих или разрешённых направлений:

1. `Каналы -> Добавить канал`
2. Канал сохраняется как `approved/auto_allowed`
3. Канал попадает в БД и учёт

Проверка:

```bash
docker compose exec -T bot python scripts/runtime_status.py --json
```

Смотри:

- `channels.review_state`
- `channels.publish_mode`
- `channels.publishable`

## Parser research flow

Parser используется только как research/enrichment path:

1. `Парсер каналов -> Поиск по ключевым словам`
2. или `Парсер каналов -> Поиск по тематике`
3. результаты сохраняются как `discovered/research_only`
4. затем оператор вручную переводит канал через review flow

Для channel review:

1. `Каналы -> 🧭 Review queue`
2. выбрать канал
3. назначить одно из состояний:
   - `candidate`
   - `approved/draft_only`
   - `approved/auto_allowed`
   - `blocked`

## Ежедневная проверка

```bash
docker compose ps
docker compose exec -T bot python scripts/runtime_status.py --json
docker compose logs --tail=120 bot packager worker worker_a worker_b
```

Смотреть обязательно:

- `workers.active`
- `workers.claims_total`
- `workers.claim_conflicts`
- `accounts.status`
- `accounts.lifecycle`
- `accounts.blockers`
- `channels.publishable`
- `policy.parser_health`

## Разбор blocker-ов

### `status_error + health_expired`

Причина:

- сессия больше не авторизована

Действие:

1. заменить `.session/.json`
2. прогнать recovery
3. проверить `runtime_status`

### `status_error + health_restricted`

Причина:

- аккаунт ограничен

Действие:

1. ручная проверка аккаунта вне проекта
2. после восстановления заменить session assets
3. прогнать recovery

### `stage_uploaded`

Причина:

- аккаунт ещё не проходил packaging

Действие:

1. убедиться, что `status=active` и `health=alive`
2. поставить в packaging

### `stage_packaging_error`

Причина:

- packaging завершился ошибкой

Действие:

1. посмотреть `docker compose logs --tail=120 packager`
2. исправить причину
3. повторить packaging

### `no_proxy_binding`

Причина:

- аккаунт без выделенного прокси

Действие:

```bash
docker compose exec -T bot python scripts/sync_proxies_and_bind.py --json
```

### `parser blocked`

Причина:

- текущий parser-account невалиден

Действие:

1. заменить session assets parser-account
2. прогнать recovery с `--set-parser-first-authorized`
3. проверить `policy.parser_health`

## Когда считать сервис готовым

Минимальный рабочий baseline:

- есть хотя бы один `active/alive`
- есть хотя бы один аккаунт в `active_commenting`
- `workers.claims_total > 0`
- `channels.publishable > 0`
- `parser_health.blocked = false`

Если один из этих пунктов не выполнен, service stack жив, но продукт ещё не готов к рабочему режиму.
