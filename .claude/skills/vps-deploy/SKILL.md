---
name: vps-deploy
description: Pre-deploy checklist to prevent common VPS deployment failures — Docker rebuild, frontend mount, limit sync, Unicode rendering, RLS tenant checks
---

# VPS Deploy — Предполётный чеклист

Этот чеклист ОБЯЗАТЕЛЕН перед любым деплоем на VPS. Не пропускай ни одного пункта.

## Чеклист

### 1. Docker-образ бэкенда пересобран

После `git pull` на VPS **всегда** пересобирай образ:

```bash
docker compose build ops_api
```

Без этого контейнер запустится со старым Python-кодом. Любое изменение в `ops_api.py`, `core/`, `storage/`, `alembic/` требует rebuild.

### 2. Frontend dist примонтирован как volume

В `docker-compose.yml` у сервиса `ops_api` **должен быть** volume mount:

```yaml
volumes:
  - ./frontend/dist:/app/frontend/dist
```

Без этого `COPY` в Dockerfile запекает старый билд в образ, и `npm run build` на хосте ничего не меняет внутри контейнера.

**Проверка:** после деплоя открой любую страницу `/app/*` и убедись, что изменения видны.

### 3. Лимиты синхронизированы между frontend и backend

Если менял любые лимиты/ограничения в бэкенде (пагинация, max upload, rate limit), **проверь**:

- Фронтенд отправляет значения в пределах нового лимита
- Бэкенд в контейнере действительно работает с новым кодом (см. пункт 1)
- Pydantic-модели и query-параметры согласованы

**Типичная ошибка:** поменял `limit` с 200 на 1000 локально, но VPS-контейнер не пересобран — фронт шлёт 500, бэкенд отвечает 422.

### 4. Нет Unicode escape-последовательностей в JSX

Перед билдом фронтенда **проверь**, что в `.tsx`/`.jsx` файлах нет `\uXXXX` escape-последовательностей в строках:

```bash
grep -rn '\\u[0-9a-fA-F]\{4\}' frontend/src/ --include='*.tsx' --include='*.jsx'
```

Если найдены — замени на реальные Unicode-символы. В production-билде Vite escape-последовательности рендерятся как literal текст `\uXXXX`.

### 5. Seed-данные с правильным tenant_id

При вставке seed/каталожных данных **убедись**:

- `tenant_id` совпадает с реальным tenant на VPS
- Платформенные данные (доступные всем) имеют корректную RLS-политику
- После вставки проверь видимость данных от имени целевого tenant:

```sql
SET LOCAL app.current_tenant_id = '<tenant_id>';
SELECT count(*) FROM <table>;
```

### 6. Миграции применены

```bash
docker compose exec ops_api alembic upgrade head
```

Или на хосте, если alembic доступен вне контейнера.

### 7. Финальная проверка

После деплоя — smoke-тест:

1. `GET /health` — DB и Redis OK
2. `GET /auth/me` — авторизация работает
3. Открыть `/app` — фронтенд загружается с актуальными изменениями
4. Проверить конкретную фичу, которую деплоишь
