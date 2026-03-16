# NEURO COMMENTING — Scrum Plan: Sprints 28-33

## AI/Vibe-Coding инструменты + DevOps автоматизация

Дата создания: 2026-03-16
Ответственный: Claude Code Scrum Master
Базовый коммит: `8686404` (main)
VPS: `https://176-124-221-253.sslip.io/`

---

## Общий обзор

| Спринт | Название | Фокус | Длительность |
|--------|----------|-------|-------------|
| 28 | FastAPI-MCP + Trusted Tools | Наши 200+ эндпоинтов как MCP-инструменты + безопасная автоматизация | 1 неделя |
| 29 | Worktree-изоляция + GitHub Actions | Параллельные агенты в worktree + BugBot CI | 1 неделя |
| 30 | Context Hub + Playwright E2E | Актуальные доки при кодинге + полный браузерный тестинг | 1 неделя |
| 31 | Code Context MCP + Agent Orchestrator | Семантический поиск по 90+ модулям + масштабирование до 30 агентов | 1 неделя |
| 32 | Graphiti + AG-UI Protocol | Графы знаний для каналов + real-time интерфейс фермы | 1 неделя |
| 33 | A2A Protocol + Мониторинг агентов | Агент-к-агенту для фермы + Omnara дашборд | 1 неделя |

---

## Sprint 28 — FastAPI-MCP + Trusted Tools

**Цель спринта:** Превратить наши 200+ FastAPI-эндпоинтов в MCP-инструменты, чтобы Claude Code мог напрямую взаимодействовать с API при разработке, и настроить trusted tools для безопасной автоматизации рутинных операций.

### User Stories

| ID | Роль | История | Приоритет |
|----|------|---------|-----------|
| US-28.1 | Как разработчик | Я хочу вызывать любой API-эндпоинт проекта прямо из Claude Code через MCP, чтобы тестировать и отлаживать без curl/Postman | MUST |
| US-28.2 | Как оператор | Я хочу чтобы Claude Code автоматически одобрял безопасные инструменты (чтение файлов, grep, compile-check), чтобы не тыкать "Allow" 100 раз за сессию | MUST |
| US-28.3 | Как разработчик | Я хочу видеть документацию каждого MCP-инструмента (описание, параметры, примеры) прямо в Claude Code, чтобы не лезть в ops_api.py | SHOULD |
| US-28.4 | Как DevOps | Я хочу чтобы MCP-сервер запускался автоматически при старте Claude Code сессии, чтобы не настраивать вручную | SHOULD |

### Задачи

#### Задача 28.1 — Установка и настройка FastAPI-MCP (M = 4ч)

**Описание:** Установить `fastapi-mcp` и подключить к нашему FastAPI-приложению.

**Команды установки:**
```bash
cd "/Users/braslavskii/NEURO COMMENTING"
pip install fastapi-mcp
echo "fastapi-mcp" >> requirements.txt
```

**Файлы для изменения:**

1. `/Users/braslavskii/NEURO COMMENTING/ops_api.py` — добавить MCP-маунт в конец файла:
```python
# --- MCP Server mount (development only) ---
from fastapi_mcp import FastApiMcp

mcp = FastApiMcp(
    app,
    name="neuro-commenting-mcp",
    description="NEURO COMMENTING SaaS API — 200+ endpoints",
    describe_all_responses=True,
    describe_full_response_schema=True,
)
mcp.mount()
```

2. `/Users/braslavskii/NEURO COMMENTING/.mcp.json` — добавить SSE-транспорт:
```json
{
  "mcpServers": {
    "neuro-api": {
      "type": "sse",
      "url": "http://localhost:8000/mcp",
      "timeout": 30000
    },
    "playwright": { ... },
    "sequential-thinking": { ... },
    "pencil": { ... }
  }
}
```

3. `/Users/braslavskii/NEURO COMMENTING/config.py` — добавить флаг:
```python
MCP_ENABLED: bool = os.getenv("MCP_ENABLED", "false").lower() == "true"
```

**Критерии приёмки:**
- [ ] `pip install fastapi-mcp` без ошибок
- [ ] `python -m py_compile ops_api.py` проходит
- [ ] При `MCP_ENABLED=true python ops_api.py` доступен `GET /mcp` (SSE endpoint)
- [ ] Claude Code видит MCP-сервер `neuro-api` в `/mcp`
- [ ] Минимум 10 эндпоинтов доступны как MCP tools (проверить `/health`, `/auth/me`, `/v1/channel-map/categories`)

#### Задача 28.2 — Фильтрация MCP-эндпоинтов по безопасности (M = 4ч)

**Описание:** Не все 200+ эндпоинтов безопасны для автоматического вызова. Разделить на read-only (автоматически доступны) и write (требуют подтверждения).

**Файлы для создания:**

1. `/Users/braslavskii/NEURO COMMENTING/core/mcp_filter.py`:
```python
"""MCP endpoint safety classification.

READ_SAFE — GET-эндпоинты, которые не меняют состояние.
WRITE_DANGEROUS — POST/PUT/DELETE, которые могут навредить (delete farm, cancel subscription).
WRITE_SAFE — POST, которые безопасны (start-brief, generate drafts).
"""

READ_SAFE_PREFIXES = [
    "/health", "/v1/channel-map/", "/v1/web/accounts",
    "/v1/assistant/thread", "/v1/creative/drafts",
    "/v1/context", "/v1/farm", "/v1/parser/jobs",
    "/v1/health/", "/v1/quarantine/", "/v1/billing/plans",
    "/v1/billing/usage", "/auth/me",
]

WRITE_DANGEROUS_PREFIXES = [
    "/v1/farm/{id}/stop", "/v1/farm/{id}/delete",
    "/v1/billing/cancel", "/v1/admin/",
    "/v1/accounts/batch-settings",
]

def classify_endpoint(method: str, path: str) -> str:
    if method == "GET":
        return "read_safe"
    for prefix in WRITE_DANGEROUS_PREFIXES:
        if path.startswith(prefix.split("{")[0]):
            return "write_dangerous"
    return "write_safe"
```

2. Обновить `ops_api.py` — передать `operation_filter` в FastApiMcp:
```python
from core.mcp_filter import classify_endpoint

def mcp_operation_filter(operation) -> bool:
    """Expose only safe operations as MCP tools."""
    # В development — все; в production — только read_safe
    if settings.APP_ENV == "development":
        return True
    method = operation.get("method", "GET").upper()
    path = operation.get("path", "")
    return classify_endpoint(method, path) == "read_safe"

mcp = FastApiMcp(
    app,
    name="neuro-commenting-mcp",
    operation_filter=mcp_operation_filter,
    ...
)
```

**Критерии приёмки:**
- [ ] В development-режиме Claude Code видит все 200+ MCP tools
- [ ] В production-режиме видны только GET-эндпоинты
- [ ] `python -m py_compile core/mcp_filter.py` проходит
- [ ] DELETE /v1/farm/* не доступен как автоматический MCP tool в production

#### Задача 28.3 — Trusted Tools конфигурация (S = 2ч)

**Описание:** Настроить автоматическое одобрение безопасных инструментов, чтобы не кликать "Allow" на каждый Read/Grep/Glob.

**Файлы для изменения:**

1. `/Users/braslavskii/NEURO COMMENTING/.claude/settings.local.json` — расширить `permissions.allow`:
```json
{
  "permissions": {
    "allow": [
      "Read",
      "Glob",
      "Grep",
      "Bash(python -m py_compile *)",
      "Bash(python -c *)",
      "Bash(pytest tests/*)",
      "Bash(cd frontend && npx tsc --noEmit)",
      "Bash(git status*)",
      "Bash(git diff*)",
      "Bash(git log*)",
      "Bash(git branch*)",
      "Bash(ls *)",
      "Bash(wc *)",
      "Bash(pip install *)",
      "Bash(npm install*)",
      "Bash(alembic history*)",
      "Bash(alembic current*)",
      "Bash(curl -s http://localhost:8000/*)",
      "mcp__neuro-api__*(method:GET)",
      "WebFetch(domain:ai-easy.ru)",
      "WebFetch(domain:hard-tm.su)"
    ],
    "deny": [
      "Bash(rm -rf *)",
      "Bash(git push --force*)",
      "Bash(git reset --hard*)",
      "Bash(DROP TABLE*)",
      "Bash(docker rm -f*)"
    ]
  }
}
```

**Критерии приёмки:**
- [ ] Read/Grep/Glob автоматически одобряются без промпта
- [ ] `pytest tests/*` автоматически одобряется
- [ ] `rm -rf` блокируется
- [ ] `git push --force` блокируется
- [ ] MCP GET-запросы к `neuro-api` автоматически одобряются

#### Задача 28.4 — MCP-интеграционные тесты (M = 4ч)

**Описание:** Написать тесты, проверяющие что MCP-сервер корректно экспонирует эндпоинты и фильтрует опасные.

**Файлы для создания:**

1. `/Users/braslavskii/NEURO COMMENTING/tests/test_mcp_integration.py`:
```python
"""Tests for FastAPI-MCP integration."""
import pytest
from core.mcp_filter import classify_endpoint

def test_get_endpoints_are_read_safe():
    assert classify_endpoint("GET", "/health") == "read_safe"
    assert classify_endpoint("GET", "/v1/channel-map/categories") == "read_safe"
    assert classify_endpoint("GET", "/auth/me") == "read_safe"

def test_dangerous_writes_classified():
    assert classify_endpoint("DELETE", "/v1/farm/1/delete") == "write_dangerous"
    assert classify_endpoint("POST", "/v1/admin/tenants") == "write_dangerous"

def test_safe_writes_classified():
    assert classify_endpoint("POST", "/v1/assistant/start-brief") == "write_safe"
    assert classify_endpoint("POST", "/v1/creative/generate") == "write_safe"

def test_billing_cancel_is_dangerous():
    assert classify_endpoint("POST", "/v1/billing/cancel") == "write_dangerous"
```

**Критерии приёмки:**
- [ ] `pytest tests/test_mcp_integration.py -v` — все тесты зелёные
- [ ] Тесты покрывают минимум 5 read_safe, 3 write_dangerous, 3 write_safe

#### Задача 28.5 — Документация MCP Skill (S = 2ч)

**Описание:** Создать Claude Code skill для работы с нашим MCP API.

**Файлы для создания:**

1. `/Users/braslavskii/NEURO COMMENTING/.claude/skills/neuro-mcp/SKILL.md`:
```markdown
---
name: neuro-mcp
description: "Interact with NEURO COMMENTING API via MCP tools"
---

## Skill: neuro-mcp

This skill uses the `neuro-api` MCP server to interact with the
NEURO COMMENTING platform API directly from Claude Code.

### Available categories

- **Health**: `GET /health` — DB + Redis + scheduler status
- **Channel Map**: `GET /v1/channel-map/*` — categories, viewport, clusters, countries
- **Accounts**: `GET /v1/web/accounts` — list tenant accounts
- **Farm**: `GET /v1/farm` — list farms, `GET /v1/farm/{id}` — farm details
- **Assistant**: `GET /v1/assistant/thread` — conversation thread
- **Billing**: `GET /v1/billing/plans` — available plans, `GET /v1/billing/usage`
- **Parser**: `GET /v1/parser/jobs` — parsing jobs
- **Health Scores**: `GET /v1/health/scores` — account health

### Usage pattern

1. First call `GET /health` to verify API is running
2. Use `GET /auth/me` with a valid JWT to check auth context
3. Browse data with GET endpoints — they are auto-approved
4. For write operations, describe what you want to do and get confirmation

### Required setup

- `MCP_ENABLED=true` in `.env`
- `python ops_api.py` running locally on port 8000
- `.mcp.json` configured with `neuro-api` SSE server
```

**Критерии приёмки:**
- [ ] Skill видна в `/skills` команде Claude Code
- [ ] Инструкция достаточна для новой сессии Claude Code

### Зависимости Sprint 28

| Зависимость | Статус | Блокирует |
|------------|--------|-----------|
| `pip install fastapi-mcp` | Доступен в PyPI | 28.1 |
| Локальный `ops_api.py` запущен | Всегда доступен | 28.1, 28.4 |
| `.mcp.json` уже существует | Есть (playwright + sequential-thinking + pencil) | 28.1 |

### Риски Sprint 28

| Риск | Вероятность | Влияние | Митигация |
|------|-------------|---------|-----------|
| `fastapi-mcp` несовместим с нашей версией FastAPI | Низкая | Высокое | Проверить совместимость перед установкой; fallback — ручной MCP-сервер через `mcp` SDK |
| 200+ tools перегружают контекст Claude Code | Средняя | Среднее | `operation_filter` ограничит количество экспонируемых tools |
| MCP SSE transport нестабилен для длинных сессий | Низкая | Низкое | Добавить reconnect logic или переключиться на streamable-http |
| Случайный вызов write-эндпоинта через MCP | Средняя | Высокое | Двухуровневая фильтрация: `operation_filter` + `permissions.deny` в settings |

---

## Sprint 29 — Worktree-изоляция + GitHub Actions CI

**Цель спринта:** Настроить git worktree для параллельной работы нескольких агентов без конфликтов, и добавить BugBot для автоматического code review на каждый PR.

### User Stories

| ID | Роль | История | Приоритет |
|----|------|---------|-----------|
| US-29.1 | Как тимлид | Я хочу запускать 3-5 агентов параллельно, каждый в своём worktree, чтобы за 1 час делать работу за 5 часов | MUST |
| US-29.2 | Как разработчик | Я хочу автоматический code review от BugBot на каждый PR, чтобы ловить баги до мержа | MUST |
| US-29.3 | Как оператор | Я хочу агент `worktree-worker`, который автоматически создаёт worktree, работает, коммитит и убирает за собой | SHOULD |
| US-29.4 | Как DevOps | Я хочу SuperClaude git checkpoint перед каждым рискованным изменением, чтобы откатываться без потерь | HIGH |

### Задачи

#### Задача 29.1 — Worktree-инфраструктура (L = 8ч)

**Описание:** Создать скрипты и агента для автоматического создания/удаления worktree.

**Файлы для создания:**

1. `/Users/braslavskii/NEURO COMMENTING/scripts/worktree-create.sh`:
```bash
#!/bin/bash
# Usage: ./scripts/worktree-create.sh <task-name>
# Creates a git worktree for isolated agent work
set -euo pipefail

TASK_NAME="${1:?Usage: worktree-create.sh <task-name>}"
WORKTREE_BASE="/Users/braslavskii/NEURO COMMENTING/.worktrees"
BRANCH_NAME="agent/${TASK_NAME}"
WORKTREE_PATH="${WORKTREE_BASE}/${TASK_NAME}"

mkdir -p "$WORKTREE_BASE"

# Create branch from current HEAD
git -C "/Users/braslavskii/NEURO COMMENTING" branch "$BRANCH_NAME" HEAD 2>/dev/null || true
git -C "/Users/braslavskii/NEURO COMMENTING" worktree add "$WORKTREE_PATH" "$BRANCH_NAME"

# Symlink shared resources that shouldn't be duplicated
ln -sf "/Users/braslavskii/NEURO COMMENTING/.venv" "$WORKTREE_PATH/.venv"
ln -sf "/Users/braslavskii/NEURO COMMENTING/node_modules" "$WORKTREE_PATH/frontend/node_modules" 2>/dev/null || true
ln -sf "/Users/braslavskii/NEURO COMMENTING/.env" "$WORKTREE_PATH/.env"

echo "Worktree created: $WORKTREE_PATH"
echo "Branch: $BRANCH_NAME"
```

2. `/Users/braslavskii/NEURO COMMENTING/scripts/worktree-cleanup.sh`:
```bash
#!/bin/bash
# Usage: ./scripts/worktree-cleanup.sh <task-name>
set -euo pipefail

TASK_NAME="${1:?Usage: worktree-cleanup.sh <task-name>}"
WORKTREE_BASE="/Users/braslavskii/NEURO COMMENTING/.worktrees"
BRANCH_NAME="agent/${TASK_NAME}"
WORKTREE_PATH="${WORKTREE_BASE}/${TASK_NAME}"

git -C "/Users/braslavskii/NEURO COMMENTING" worktree remove "$WORKTREE_PATH" --force 2>/dev/null || true
git -C "/Users/braslavskii/NEURO COMMENTING" branch -D "$BRANCH_NAME" 2>/dev/null || true

echo "Worktree cleaned: $TASK_NAME"
```

3. `/Users/braslavskii/NEURO COMMENTING/scripts/worktree-merge.sh`:
```bash
#!/bin/bash
# Usage: ./scripts/worktree-merge.sh <task-name>
# Merges agent worktree branch back into main
set -euo pipefail

TASK_NAME="${1:?Usage: worktree-merge.sh <task-name>}"
BRANCH_NAME="agent/${TASK_NAME}"

cd "/Users/braslavskii/NEURO COMMENTING"
git checkout main
git merge --no-ff "$BRANCH_NAME" -m "Merge agent/${TASK_NAME} into main"

# Cleanup after successful merge
./scripts/worktree-cleanup.sh "$TASK_NAME"
echo "Merged and cleaned: $TASK_NAME"
```

4. Добавить в `.gitignore`:
```
.worktrees/
```

**Критерии приёмки:**
- [ ] `./scripts/worktree-create.sh test-task` создаёт worktree в `.worktrees/test-task/`
- [ ] Worktree содержит полный рабочий код с symlink на .venv и .env
- [ ] `python -m py_compile ops_api.py` работает из worktree
- [ ] `./scripts/worktree-cleanup.sh test-task` удаляет worktree и ветку
- [ ] `./scripts/worktree-merge.sh` мержит обратно в main

#### Задача 29.2 — Агент worktree-worker (M = 4ч)

**Описание:** Создать Claude Code агента, который работает в изолированном worktree.

**Файлы для создания:**

1. `/Users/braslavskii/NEURO COMMENTING/.claude/agents/worktree-worker.md`:
```markdown
---
name: worktree-worker
description: "Execute a sprint task in an isolated git worktree. Creates worktree, implements task, commits, reports back."
tools: Read, Edit, Write, Bash, Grep, Glob
model: sonnet
---

You are a worktree-isolated worker agent for NEURO COMMENTING.

## Workflow

1. **SETUP**: The coordinator gives you a task name and description.
   Run: `bash "/Users/braslavskii/NEURO COMMENTING/scripts/worktree-create.sh" <task-name>`
   Your working directory is now: `/Users/braslavskii/NEURO COMMENTING/.worktrees/<task-name>/`

2. **IMPLEMENT**: Work ONLY in your worktree directory.
   - Read the relevant source files
   - Make changes
   - Run `python -m py_compile` on all changed .py files
   - Run relevant tests

3. **COMMIT**: When done:
   ```bash
   cd /Users/braslavskii/NEURO COMMENTING/.worktrees/<task-name>
   git add -A
   git commit -m "<descriptive message>"
   ```

4. **REPORT**: Return a summary:
   - Files changed (list)
   - Tests passed (count)
   - Compile status
   - Any blockers

## Rules
- NEVER modify files in the main repo directory
- NEVER run git push
- ALWAYS work in your assigned worktree path
- If you encounter a merge conflict, STOP and report to coordinator
- Use the shared .venv for Python execution
```

**Критерии приёмки:**
- [ ] Агент виден в `/agents` команде
- [ ] Агент успешно создаёт worktree, коммитит изменения, отчитывается
- [ ] Агент НЕ модифицирует файлы в основной рабочей директории

#### Задача 29.3 — GitHub Actions: BugBot Code Review (L = 8ч)

**Описание:** Добавить Claude Code GitHub Action для автоматического code review на PR.

**Файлы для создания:**

1. `/Users/braslavskii/NEURO COMMENTING/.github/workflows/claude-review.yml`:
```yaml
name: Claude Code Review

on:
  pull_request:
    types: [opened, synchronize, reopened]

permissions:
  contents: read
  pull-requests: write
  issues: write

jobs:
  claude-review:
    runs-on: ubuntu-latest
    if: ${{ !contains(github.event.pull_request.labels.*.name, 'skip-review') }}
    steps:
      - name: Checkout
        uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Claude Code Review
        uses: anthropics/claude-code-action@v1
        with:
          anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
          model: "claude-sonnet-4-20250514"
          direct_prompt: |
            Review this PR for the NEURO COMMENTING project.

            Context: Multi-tenant SaaS (Python+FastAPI+React+PostgreSQL).
            All tables use RLS. AI calls go through route_ai_task().

            Check for:
            1. RLS bypass (missing tenant_id filter)
            2. SQL injection (raw string interpolation)
            3. Cross-tenant data leaks
            4. Missing rate limiting on new endpoints
            5. Unhandled exceptions that leak stack traces
            6. Frontend: XSS, unescaped user input
            7. Missing py_compile / tsc checks

            Format: Use GitHub review comments on specific lines.
            Language: Russian for comments, English for code suggestions.
          timeout_minutes: 10
```

2. `/Users/braslavskii/NEURO COMMENTING/.github/workflows/claude-fix.yml`:
```yaml
name: Claude Code Fix

on:
  issues:
    types: [opened, labeled]
  issue_comment:
    types: [created]

permissions:
  contents: write
  pull-requests: write
  issues: write

jobs:
  claude-fix:
    runs-on: ubuntu-latest
    if: |
      (github.event_name == 'issues' && contains(github.event.issue.labels.*.name, 'claude-fix')) ||
      (github.event_name == 'issue_comment' && contains(github.event.comment.body, '@claude'))
    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install deps
        run: pip install -r requirements.txt

      - name: Claude Code Fix
        uses: anthropics/claude-code-action@v1
        with:
          anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
          model: "claude-sonnet-4-20250514"
          trigger_phrase: "@claude"
          timeout_minutes: 15
```

**Необходимые GitHub Secrets:**
```
ANTHROPIC_API_KEY — API ключ для Claude (из настроек Anthropic Console)
```

**Критерии приёмки:**
- [ ] При создании PR автоматически запускается claude-review job
- [ ] BugBot оставляет line-level комментарии на PR
- [ ] При `@claude fix this` в issue создаётся PR с фиксом
- [ ] Review проверяет RLS/tenant isolation специфику проекта
- [ ] Label `skip-review` позволяет пропустить ревью

#### Задача 29.4 — Git Checkpoints (SuperClaude) (S = 2ч)

**Описание:** Добавить автоматические git checkpoint перед рискованными операциями.

**Файлы для создания:**

1. `/Users/braslavskii/NEURO COMMENTING/.claude/hooks/git-checkpoint.sh`:
```bash
#!/bin/bash
# Pre-tool hook: create a WIP checkpoint before risky edits
# Triggered by Edit|Write|MultiEdit on ops_api.py, storage/models.py, alembic/

TOOL_INPUT="$CLAUDE_TOOL_INPUT"
TARGET_FILE=$(echo "$TOOL_INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('file_path',''))" 2>/dev/null)

RISKY_FILES=("ops_api.py" "storage/models.py" "config.py" "docker-compose.yml")
RISKY_DIRS=("alembic/versions/" "core/web_auth.py" "core/billing_service.py")

IS_RISKY=false
for f in "${RISKY_FILES[@]}"; do
  [[ "$TARGET_FILE" == *"$f" ]] && IS_RISKY=true
done
for d in "${RISKY_DIRS[@]}"; do
  [[ "$TARGET_FILE" == *"$d"* ]] && IS_RISKY=true
done

if $IS_RISKY; then
  cd "/Users/braslavskii/NEURO COMMENTING"
  # Only create checkpoint if there are uncommitted changes to the risky file
  if git diff --name-only | grep -q "$(basename "$TARGET_FILE")" 2>/dev/null; then
    CHECKPOINT_MSG="WIP checkpoint before editing $(basename "$TARGET_FILE")"
    git stash push -m "$CHECKPOINT_MSG" -- "$TARGET_FILE" 2>/dev/null
    git stash pop 2>/dev/null
    # Alternative: lightweight tag
    git tag -f "checkpoint/$(date +%Y%m%d-%H%M%S)" 2>/dev/null || true
  fi
fi

exit 0
```

2. Обновить `/Users/braslavskii/NEURO COMMENTING/.claude/settings.json` — добавить хук:
```json
"PreToolUse": [
  {
    "matcher": "Edit|Write|MultiEdit",
    "hooks": [
      {
        "type": "command",
        "command": "\"$CLAUDE_PROJECT_DIR\"/.claude/hooks/protect-files.sh"
      },
      {
        "type": "command",
        "command": "\"$CLAUDE_PROJECT_DIR\"/.claude/hooks/git-checkpoint.sh"
      }
    ]
  }
]
```

**Критерии приёмки:**
- [ ] При редактировании `ops_api.py` создаётся git tag `checkpoint/*`
- [ ] При редактировании обычных файлов checkpoint НЕ создаётся
- [ ] `git tag -l 'checkpoint/*'` показывает checkpoint-теги
- [ ] Хук не ломает существующий protect-files.sh

#### Задача 29.5 — Обновление parallel-coordinator (S = 2ч)

**Описание:** Обновить существующего агента `parallel-coordinator` для поддержки worktree.

**Файлы для изменения:**

1. `/Users/braslavskii/NEURO COMMENTING/.claude/agents/parallel-coordinator.md` — добавить worktree-секцию:

Добавить после "## Dispatch rules":
```markdown
## Worktree dispatch

For truly parallel execution (3-5 simultaneous agents):

1. For each independent task, run:
   ```bash
   bash scripts/worktree-create.sh <task-name>
   ```
2. Dispatch worktree-worker agent with:
   - `worktree_path`: `/Users/braslavskii/NEURO COMMENTING/.worktrees/<task-name>/`
   - `task_description`: what to implement
   - `acceptance_criteria`: how to verify
3. After all workers complete, merge in order:
   ```bash
   bash scripts/worktree-merge.sh <task-1>
   bash scripts/worktree-merge.sh <task-2>
   # ... resolve conflicts if any
   ```
4. Run full test suite on main after all merges
```

**Критерии приёмки:**
- [ ] parallel-coordinator документирует worktree workflow
- [ ] Агент может dispatch 3+ worktree-worker параллельно

### Зависимости Sprint 29

| Зависимость | Статус | Блокирует |
|------------|--------|-----------|
| Git worktree поддержка | Встроено в git | 29.1 |
| GitHub repo с Actions | Нужен push access | 29.3 |
| `ANTHROPIC_API_KEY` в GitHub Secrets | Нужно добавить | 29.3 |
| Sprint 28 (MCP) | Не блокирует | — |

### Риски Sprint 29

| Риск | Вероятность | Влияние | Митигация |
|------|-------------|---------|-----------|
| Worktree с symlinks ломает imports | Средняя | Среднее | Тестировать `py_compile` из worktree до начала работы |
| Merge conflicts между worktree | Высокая | Среднее | Координатор мержит последовательно, разрешает конфликты |
| GitHub Actions бесплатный лимит | Низкая | Низкое | 2000 мин/мес для бесплатного плана, достаточно |
| BugBot галлюцинирует false positive | Средняя | Низкое | Label `skip-review` + Sonnet (не Opus) для баланса цена/качество |

---

## Sprint 30 — Context Hub + Playwright E2E

**Цель спринта:** Обеспечить актуальные доки зависимостей при кодинге через Context7 MCP и настроить полноценное браузерное тестирование всех 35 страниц через Playwright MCP.

### User Stories

| ID | Роль | История | Приоритет |
|----|------|---------|-----------|
| US-30.1 | Как разработчик | Я хочу получать актуальную документацию FastAPI/SQLAlchemy/Alembic/React прямо в Claude Code, чтобы не гуглить устаревшие примеры | MUST |
| US-30.2 | Как QA | Я хочу автоматический E2E-тест всех 35 фронтенд-страниц через реальный браузер, чтобы ловить рантайм-краши | MUST |
| US-30.3 | Как оператор | Я хочу E2E smoke-тест VPS после каждого деплоя, чтобы не проверять 200+ эндпоинтов вручную | SHOULD |
| US-30.4 | Как разработчик | Я хочу gitmcp.io для любой зависимости (Telethon, react-globe.gl), чтобы Claude знал актуальное API | NICE |

### Задачи

#### Задача 30.1 — Context7 MCP для документации (M = 4ч)

**Описание:** Подключить Context7 MCP для получения актуальных доков при кодинге.

**Файлы для изменения:**

1. `/Users/braslavskii/NEURO COMMENTING/.mcp.json` — добавить context7:
```json
{
  "mcpServers": {
    "context7": {
      "command": "npx",
      "args": ["-y", "@upstash/context7-mcp@latest"],
      "timeout": 30000
    },
    "neuro-api": { ... },
    "playwright": { ... },
    "sequential-thinking": { ... },
    "pencil": { ... }
  }
}
```

**Команды установки:**
```bash
# Проверить что context7 работает
npx -y @upstash/context7-mcp@latest --help
```

2. `/Users/braslavskii/NEURO COMMENTING/.claude/skills/context-hub/SKILL.md`:
```markdown
---
name: context-hub
description: "Fetch latest docs for project dependencies via Context7"
---

## Skill: context-hub

Use Context7 MCP to fetch up-to-date documentation for our stack.

### Key libraries to query

| Library | Use for |
|---------|---------|
| `fastapi` | API routing, middleware, dependencies |
| `sqlalchemy` | ORM models, async sessions, queries |
| `alembic` | Migration generation, upgrade/downgrade |
| `pydantic` | Request/response validation |
| `react` | Frontend components, hooks |
| `react-globe.gl` | Channel map 3D globe |
| `telethon` | Telegram client API |
| `redis` / `aioredis` | Cache, pub/sub, task queue |

### Usage

1. Before implementing a feature, resolve the library ID:
   `mcp__context7__resolve-library-id` with query "fastapi"
2. Then query docs:
   `mcp__context7__query-docs` with the resolved ID and your question
3. Use the returned docs as authoritative reference
```

**Критерии приёмки:**
- [ ] Context7 MCP доступен в `/mcp`
- [ ] `resolve-library-id("fastapi")` возвращает ID
- [ ] `query-docs` по FastAPI возвращает актуальную документацию
- [ ] Skill `context-hub` виден в `/skills`

#### Задача 30.2 — gitmcp.io для Telethon и react-globe.gl (S = 2ч)

**Описание:** Подключить gitmcp.io для получения доков из GitHub-репозиториев зависимостей.

**Файлы для изменения:**

1. `/Users/braslavskii/NEURO COMMENTING/.mcp.json` — добавить gitmcp серверы:
```json
{
  "mcpServers": {
    "telethon-docs": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "https://gitmcp.io/LonamiWebs/Telethon"],
      "timeout": 30000
    },
    "react-globe-docs": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "https://gitmcp.io/vasturiano/react-globe.gl"],
      "timeout": 30000
    }
  }
}
```

**Критерии приёмки:**
- [ ] Telethon docs доступны через MCP
- [ ] react-globe.gl docs доступны через MCP
- [ ] Claude Code может ответить на вопрос "как отправить реакцию через Telethon" используя MCP-доки

#### Задача 30.3 — Playwright E2E: полный frontend smoke (XL = 16ч)

**Описание:** Написать E2E-тесты для всех 35 фронтенд-страниц через Playwright MCP.

**Файлы для создания:**

1. `/Users/braslavskii/NEURO COMMENTING/tests/e2e/playwright_smoke.py`:
```python
"""E2E smoke test for all 35 frontend pages via Playwright.

Usage:
    Run via Claude Code with Playwright MCP:
    1. Start ops_api.py locally
    2. Build frontend: cd frontend && npm run build
    3. Use Playwright MCP browser_navigate to each page
    4. Verify no console errors, no blank screens
"""

# Page inventory (35 pages as of Sprint 27)
PAGES = [
    # Public
    {"path": "/", "name": "Landing", "auth": False},
    {"path": "/ecom", "name": "Ecom Landing", "auth": False},
    {"path": "/edtech", "name": "EdTech Landing", "auth": False},
    {"path": "/saas", "name": "SaaS Landing", "auth": False},
    {"path": "/pricing", "name": "Pricing", "auth": False},
    {"path": "/terms", "name": "Terms", "auth": False},
    {"path": "/privacy", "name": "Privacy", "auth": False},
    {"path": "/refund", "name": "Refund", "auth": False},
    # Auth
    {"path": "/app/login", "name": "Login", "auth": False},
    # Protected
    {"path": "/app", "name": "App Root", "auth": True},
    {"path": "/app/dashboard", "name": "Dashboard", "auth": True},
    {"path": "/app/accounts", "name": "Accounts", "auth": True},
    {"path": "/app/proxies", "name": "Proxies", "auth": True},
    {"path": "/app/farm", "name": "Farm", "auth": True},
    {"path": "/app/farm-monitor", "name": "Farm Monitor", "auth": True},
    {"path": "/app/parser", "name": "Parser", "auth": True},
    {"path": "/app/profiles", "name": "Profiles", "auth": True},
    {"path": "/app/channel-map", "name": "Channel Map", "auth": True},
    {"path": "/app/assistant", "name": "Assistant", "auth": True},
    {"path": "/app/context", "name": "Context", "auth": True},
    {"path": "/app/creative", "name": "Creative", "auth": True},
    {"path": "/app/campaigns", "name": "Campaigns", "auth": True},
    {"path": "/app/analytics", "name": "Analytics", "auth": True},
    {"path": "/app/billing", "name": "Billing", "auth": True},
    {"path": "/app/settings", "name": "Settings", "auth": True},
    {"path": "/app/health", "name": "Health", "auth": True},
    {"path": "/app/warmup", "name": "Warmup", "auth": True},
    {"path": "/app/comments", "name": "Comments", "auth": True},
    {"path": "/app/reactions", "name": "Reactions", "auth": True},
    {"path": "/app/chatting", "name": "Chatting", "auth": True},
    {"path": "/app/dialogs", "name": "Dialogs", "auth": True},
    {"path": "/app/user-parser", "name": "User Parser", "auth": True},
    {"path": "/app/folders", "name": "Folders", "auth": True},
    {"path": "/app/session-topology", "name": "Session Topology", "auth": True},
    {"path": "/app/admin", "name": "Admin", "auth": True},
    {"path": "/app/agency", "name": "Agency", "auth": True},
    {"path": "/app/onboarding", "name": "Onboarding", "auth": True},
    {"path": "/app/platform-health", "name": "Platform Health", "auth": True},
    {"path": "/app/account-activity", "name": "Account Activity", "auth": True},
]

# Acceptance criteria per page:
# 1. HTTP 200 (or 302 redirect to /app/login for auth pages without token)
# 2. No JavaScript console errors
# 3. Page renders non-empty content (not blank white screen)
# 4. Page title or h1 contains expected text
```

2. `/Users/braslavskii/NEURO COMMENTING/.claude/commands/e2e-smoke.md`:
```markdown
---
name: e2e-smoke
description: "Run full E2E smoke test on all frontend pages via Playwright MCP"
---

Run a full E2E smoke test on all 35+ frontend pages.

Steps:
1. Read `tests/e2e/playwright_smoke.py` for the page inventory
2. Use Playwright MCP `browser_navigate` to `http://localhost:8000/`
3. Verify landing page loads (check for "NEURO COMMENTING" text)
4. For each public page, navigate and verify:
   - No console errors (`browser_console_messages`)
   - Page content is non-empty (`browser_snapshot`)
5. For protected pages:
   - First login via `/auth/login` with test credentials
   - Then navigate to each protected page
6. Report results as a table: Page | Status | Console Errors | Notes

Test credentials (development):
- Email: test@neuro.com
- Password: testpass123
```

**Критерии приёмки:**
- [ ] Playwright MCP `browser_navigate` работает на localhost:8000
- [ ] Все public-страницы отдают 200
- [ ] Protected-страницы без auth редиректят на /app/login
- [ ] После логина все protected-страницы рендерят контент
- [ ] Нет JavaScript console errors на загруженных страницах
- [ ] Отчёт генерируется как Markdown-таблица

#### Задача 30.4 — VPS post-deploy E2E smoke (M = 4ч)

**Описание:** Адаптировать E2E smoke для VPS через Playwright MCP.

**Файлы для создания:**

1. `/Users/braslavskii/NEURO COMMENTING/.claude/commands/vps-e2e.md`:
```markdown
---
name: vps-e2e
description: "Run E2E smoke test against live VPS via Playwright"
---

Run the E2E smoke test against the live VPS at https://176-124-221-253.sslip.io/

Same as /e2e-smoke but targeting production URL.
IMPORTANT: Do NOT run write operations (POST/PUT/DELETE) against VPS.
Only verify page loads and read-only endpoints.
```

**Критерии приёмки:**
- [ ] E2E smoke запускается на VPS URL
- [ ] Public страницы загружаются через HTTPS
- [ ] `/health` endpoint отвечает с DB + Redis OK

### Зависимости Sprint 30

| Зависимость | Статус | Блокирует |
|------------|--------|-----------|
| Context7 npm пакет | Доступен | 30.1 |
| gitmcp.io сервис | Публичный | 30.2 |
| Playwright MCP уже в .mcp.json | Есть | 30.3 |
| Локальный ops_api.py + фронтенд | Всегда доступен | 30.3 |

### Риски Sprint 30

| Риск | Вероятность | Влияние | Митигация |
|------|-------------|---------|-----------|
| Context7 не знает нашу версию FastAPI | Низкая | Низкое | Fallback на gitmcp.io для FastAPI repo |
| Playwright MCP таймауты на тяжёлых страницах (Channel Map) | Средняя | Среднее | Увеличить timeout до 30с для WebGL-страниц |
| gitmcp.io downtime | Низкая | Низкое | Не блокирует — вспомогательный инструмент |
| 35 страниц слишком много для одного E2E прогона | Средняя | Низкое | Разбить на группы: public, auth, protected-core, protected-advanced |

---

## Sprint 31 — Code Context MCP + Agent Orchestrator

**Цель спринта:** Добавить семантический поиск по 90+ core-модулям для быстрой навигации и масштабировать координацию агентов до 30+ параллельных воркеров.

### User Stories

| ID | Роль | История | Приоритет |
|----|------|---------|-----------|
| US-31.1 | Как разработчик | Я хочу искать по семантике ("где мы проверяем tenant isolation") а не по regex, чтобы находить код за секунды вместо минут | MUST |
| US-31.2 | Как тимлид | Я хочу запускать 10-30 агентов параллельно для массовых спринтов (миграции, рефакторинг), чтобы делать за час работу за день | MUST |
| US-31.3 | Как разработчик | Я хочу Claude Squad для параллельных Claude Code сессий с общим контекстом, чтобы работать над несколькими файлами одновременно | HIGH |

### Задачи

#### Задача 31.1 — Code Context MCP Server (L = 8ч)

**Описание:** Развернуть MCP-сервер для семантического поиска по кодовой базе (90+ Python-файлов, 35+ TSX-компонентов).

**Команды установки:**
```bash
# Вариант 1: Sourcegraph-based
npm install -g @anthropic-ai/mcp-code-context

# Вариант 2: Lightweight — tree-sitter + embedding
pip install code-context-mcp
```

**Файлы для изменения:**

1. `/Users/braslavskii/NEURO COMMENTING/.mcp.json` — добавить code-context:
```json
{
  "mcpServers": {
    "code-context": {
      "command": "npx",
      "args": [
        "-y", "@anthropic-ai/mcp-code-context",
        "--root", "/Users/braslavskii/NEURO COMMENTING",
        "--include", "core/**/*.py,frontend/src/**/*.tsx,ops_api.py,storage/models.py,config.py",
        "--exclude", "node_modules,__pycache__,.venv,.worktrees,data"
      ],
      "timeout": 60000
    }
  }
}
```

2. `/Users/braslavskii/NEURO COMMENTING/.claude/skills/code-search/SKILL.md`:
```markdown
---
name: code-search
description: "Semantic code search across 90+ Python modules and 35+ React components"
---

## Skill: code-search

Use the `code-context` MCP server for semantic code search.

### Example queries

- "Where do we check tenant isolation before database queries?"
- "Which modules import route_ai_task?"
- "How does the billing service enforce plan limits?"
- "Find all FloodWait handlers in Telethon code"
- "Where is RLS context set in transactions?"

### Indexed modules (90+ files)

- `core/*.py` — 90 business logic modules
- `ops_api.py` — 15,500-line API router
- `storage/models.py` — ORM models
- `config.py` — settings
- `frontend/src/pages/*.tsx` — 35 React pages
- `frontend/src/components/**/*.tsx` — shared components
- `tests/*.py` — 37 test suites
```

**Критерии приёмки:**
- [ ] `code-context` MCP доступен в `/mcp`
- [ ] Семантический поиск "tenant isolation" находит `web_auth.py`, `conftest.py`, ORM фильтры
- [ ] Поиск "FloodWait" находит все обработчики в `farm_thread.py`, `mass_reactions.py`, `neuro_chatting.py`
- [ ] Индексация 90+ файлов завершается за <30 секунд
- [ ] Не индексирует `.venv`, `node_modules`, `data/`

#### Задача 31.2 — Agent Orchestrator Pattern (XL = 16ч)

**Описание:** Создать мета-агента, который может координировать до 30 параллельных воркеров через worktree.

**Файлы для создания:**

1. `/Users/braslavskii/NEURO COMMENTING/.claude/agents/agent-orchestrator.md`:
```markdown
---
name: agent-orchestrator
description: "Scale to 10-30 parallel agents for massive sprints. Splits work, dispatches worktree-workers, reconciles, tests."
tools: Read, Edit, Write, Bash, Grep, Glob, SendMessage
model: opus
---

You are the high-scale agent orchestrator for NEURO COMMENTING.

## Capability

You can coordinate up to 30 parallel worktree-workers for massive sprints.

## Planning phase

1. Read sprint requirements
2. Decompose into independent tasks (aim for 5-15 tasks)
3. Identify dependency chains:
   - Level 0: tasks with zero deps (run first, in parallel)
   - Level 1: tasks that depend on Level 0 outputs
   - Level 2: integration tasks that depend on Level 1
4. For each task, specify:
   - Task ID (e.g., T-31.2.1)
   - Worktree name
   - Files to create/modify
   - Agent type (worktree-worker)
   - Acceptance criteria
   - Estimated size (S/M/L/XL)

## Execution phase

For each dependency level:
1. Create all worktrees in parallel:
   ```bash
   for task in tasks_at_this_level; do
     bash scripts/worktree-create.sh "$task" &
   done
   wait
   ```
2. Dispatch worktree-worker agents (via SendMessage or parallel sessions)
3. Wait for all workers at this level to complete
4. Merge results:
   ```bash
   for task in completed_tasks; do
     bash scripts/worktree-merge.sh "$task"
   done
   ```
5. Run integration tests after each merge level
6. If conflicts: stop, resolve, re-test

## Reconciliation phase

After all levels complete:
- [ ] `python -m py_compile` on all changed .py files
- [ ] `cd frontend && npx tsc --noEmit`
- [ ] `pytest tests/ -x --timeout=60`
- [ ] Update change register
- [ ] Create single summary commit if needed

## Limits

- Max 30 simultaneous worktrees
- Each worktree uses ~200MB disk (symlinked .venv)
- Total: ~6GB for 30 worktrees
- Monitor with: `git worktree list`

## Anti-patterns

- NEVER dispatch workers that modify the same file
- NEVER skip integration tests between merge levels
- NEVER merge without compile checks
- If >3 merge conflicts at one level, STOP and re-plan
```

2. `/Users/braslavskii/NEURO COMMENTING/.claude/commands/mass-sprint.md`:
```markdown
---
name: mass-sprint
description: "Execute a mass sprint with 10-30 parallel agents"
---

Execute a mass sprint using the agent-orchestrator.

Input: Sprint plan with tasks decomposed into independent units.
Output: All tasks implemented, tested, merged, change register updated.

Usage:
1. Define the sprint tasks as a numbered list
2. The orchestrator will decompose into dependency levels
3. Each level runs in parallel via worktree-workers
4. Results merge back into main after each level
5. Full test suite runs at the end
```

**Критерии приёмки:**
- [ ] Agent orchestrator может создать 10 worktree параллельно
- [ ] Каждый worktree-worker получает изолированное рабочее пространство
- [ ] Merge обратно в main без конфликтов (при правильной декомпозиции)
- [ ] Интеграционные тесты проходят после финального merge

#### Задача 31.3 — Claude Squad интеграция (M = 4ч)

**Описание:** Настроить Claude Squad для параллельных Claude Code сессий.

**Команды установки:**
```bash
# Установить Claude Squad
npm install -g claude-squad
# Или через Go
go install github.com/smtg-ai/claude-squad@latest
```

**Файлы для создания:**

1. `/Users/braslavskii/NEURO COMMENTING/.claude-squad.yml`:
```yaml
# Claude Squad configuration
project_root: "/Users/braslavskii/NEURO COMMENTING"
max_instances: 5
worktree_base: ".worktrees"
shared_context:
  - "CLAUDE.md"
  - "knowledge/project_context/change_register.md"
  - "knowledge/project_context/claude_code_master_context.md"

profiles:
  backend:
    model: sonnet
    focus: "core/*.py, ops_api.py, storage/models.py"
    agent: saas-backend-implementer

  frontend:
    model: sonnet
    focus: "frontend/src/**/*.tsx"
    agent: worktree-worker

  tests:
    model: sonnet
    focus: "tests/*.py"
    agent: qa-tenant-auditor

  review:
    model: opus
    focus: "*"
    agent: saas-code-reviewer

  devops:
    model: sonnet
    focus: ".github/*, docker-compose.yml, scripts/*"
    agent: vps-release-auditor
```

**Критерии приёмки:**
- [ ] `claude-squad` запускается с конфигом проекта
- [ ] 3+ параллельные сессии работают без конфликтов
- [ ] Каждая сессия работает в своём worktree
- [ ] Shared context (CLAUDE.md) доступен всем сессиям

### Зависимости Sprint 31

| Зависимость | Статус | Блокирует |
|------------|--------|-----------|
| Sprint 29 (worktree-инфраструктура) | MUST be done | 31.2, 31.3 |
| code-context MCP пакет | Проверить доступность | 31.1 |
| claude-squad | npm/Go пакет | 31.3 |

### Риски Sprint 31

| Риск | Вероятность | Влияние | Митигация |
|------|-------------|---------|-----------|
| Семантический поиск неточен для Python | Средняя | Среднее | Fallback на tree-sitter + regex через Grep tool |
| 30 worktree исчерпывают диск | Низкая | Высокое | Мониторить `df -h`, symlink .venv экономит ~1.5GB на worktree |
| Claude Squad конфликтует с .claude/settings.json | Средняя | Среднее | Тестировать с 2 сессиями сначала, потом масштабировать |
| Merge из 10+ worktree создаёт каскад конфликтов | Высокая | Высокое | Строгая декомпозиция: один файл = один воркер, НИКОГДА два воркера на один файл |

---

## Sprint 32 — Graphiti + AG-UI Protocol

**Цель спринта:** Построить временной граф знаний для каналов/контента и добавить real-time agent-to-user интерфейс для мониторинга фермы.

### User Stories

| ID | Роль | История | Приоритет |
|----|------|---------|-----------|
| US-32.1 | Как аналитик | Я хочу граф связей между каналами (пересечения аудитории, тематические кластеры, динамика роста), чтобы находить лучшие цели для комментирования | MUST |
| US-32.2 | Как оператор | Я хочу видеть real-time статус каждого потока фермы (live лог, текущий канал, задержки) в интерфейсе, чтобы не дёргать API вручную | MUST |
| US-32.3 | Как маркетолог | Я хочу AI-рекомендации "похожие каналы" на основе графа, чтобы расширять целевую базу автоматически | SHOULD |

### Задачи

#### Задача 32.1 — Graphiti temporal knowledge graph (XL = 16ч)

**Описание:** Развернуть Graphiti для построения графа связей между каналами, аккаунтами, темами.

**Команды установки:**
```bash
pip install graphiti-core
echo "graphiti-core" >> requirements.txt
```

**Файлы для создания:**

1. `/Users/braslavskii/NEURO COMMENTING/core/knowledge_graph.py`:
```python
"""Temporal knowledge graph for channel/content intelligence.

Uses Graphiti for:
- Channel → Channel edges (audience overlap, topic similarity)
- Channel → Topic edges (category, tags)
- Account → Channel edges (subscribed, commented, banned)
- Temporal: all edges have valid_from/valid_to for trend analysis
"""

from __future__ import annotations
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class ChannelNode:
    channel_id: int
    username: str
    title: str
    subscribers: int
    category: str
    region: str
    language: str
    spam_score: float = 0.0
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChannelEdge:
    source_id: int
    target_id: int
    edge_type: str  # "topic_similar", "audience_overlap", "cross_promoted"
    weight: float = 0.0
    valid_from: datetime = field(default_factory=datetime.utcnow)
    valid_to: Optional[datetime] = None
    properties: dict[str, Any] = field(default_factory=dict)


class KnowledgeGraphService:
    """Wrapper around Graphiti for channel intelligence."""

    def __init__(self, neo4j_uri: str = "", neo4j_password: str = ""):
        self._uri = neo4j_uri
        self._password = neo4j_password
        self._graph = None

    async def initialize(self):
        """Initialize Graphiti graph (lazy, only when needed)."""
        try:
            from graphiti_core import Graphiti
            self._graph = Graphiti(self._uri, self._password)
            await self._graph.build_indices_and_constraints()
            logger.info("KnowledgeGraph initialized")
        except ImportError:
            logger.warning("graphiti-core not installed, knowledge graph disabled")
        except Exception as e:
            logger.error(f"KnowledgeGraph init failed: {e}")

    async def add_channel(self, node: ChannelNode) -> None:
        if not self._graph:
            return
        await self._graph.add_episode(
            name=f"channel_{node.channel_id}",
            episode_body=(
                f"Channel @{node.username} ({node.title}) has {node.subscribers} subscribers "
                f"in category {node.category}, region {node.region}, language {node.language}."
            ),
            source_description="channel_indexer",
        )

    async def add_edge(self, edge: ChannelEdge) -> None:
        if not self._graph:
            return
        await self._graph.add_episode(
            name=f"edge_{edge.source_id}_{edge.target_id}",
            episode_body=(
                f"Channel {edge.source_id} is related to channel {edge.target_id} "
                f"via {edge.edge_type} with weight {edge.weight}."
            ),
            source_description="channel_intelligence",
        )

    async def find_similar_channels(
        self, channel_id: int, limit: int = 10
    ) -> list[dict[str, Any]]:
        """Find channels similar to the given one via graph traversal."""
        if not self._graph:
            return []
        results = await self._graph.search(
            query=f"channels similar to channel {channel_id}",
            num_results=limit,
        )
        return [
            {"channel_id": r.name, "relevance": r.score, "summary": r.content}
            for r in results
        ]

    async def get_channel_trends(
        self, channel_id: int, days: int = 30
    ) -> dict[str, Any]:
        """Get temporal trends for a channel (subscriber growth, topic shifts)."""
        if not self._graph:
            return {}
        results = await self._graph.search(
            query=f"trends for channel {channel_id} over last {days} days",
            num_results=5,
        )
        return {"trends": [r.content for r in results]}

    async def close(self):
        if self._graph:
            await self._graph.close()
```

2. `/Users/braslavskii/NEURO COMMENTING/config.py` — добавить настройки:
```python
# Knowledge Graph (Graphiti + Neo4j)
NEO4J_URI: str = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_PASSWORD: str = os.getenv("NEO4J_PASSWORD", "")
KNOWLEDGE_GRAPH_ENABLED: bool = os.getenv("KNOWLEDGE_GRAPH_ENABLED", "false").lower() == "true"
```

3. `/Users/braslavskii/NEURO COMMENTING/ops_api.py` — добавить 3 эндпоинта:
```python
# GET /v1/channel-map/similar/{channel_id} — похожие каналы через граф
# GET /v1/channel-map/trends/{channel_id} — тренды канала
# POST /v1/channel-map/graph/sync — синхронизация БД → граф
```

4. `/Users/braslavskii/NEURO COMMENTING/docker-compose.yml` — добавить Neo4j:
```yaml
neo4j:
  image: neo4j:5
  ports:
    - "7474:7474"
    - "7687:7687"
  environment:
    NEO4J_AUTH: neo4j/${NEO4J_PASSWORD}
  volumes:
    - neo4j_data:/data
  restart: unless-stopped
```

**Критерии приёмки:**
- [ ] `pip install graphiti-core` без ошибок
- [ ] `python -m py_compile core/knowledge_graph.py` проходит
- [ ] Neo4j контейнер запускается через docker-compose
- [ ] `add_channel` + `find_similar_channels` работает при запущенном Neo4j
- [ ] При `KNOWLEDGE_GRAPH_ENABLED=false` все вызовы gracefully no-op

#### Задача 32.2 — AG-UI Protocol для фермы (L = 8ч)

**Описание:** Добавить real-time streaming интерфейс для мониторинга фермы по протоколу AG-UI.

**Команды установки:**
```bash
pip install ag-ui-protocol
echo "ag-ui-protocol" >> requirements.txt
```

**Файлы для создания:**

1. `/Users/braslavskii/NEURO COMMENTING/core/agui_farm_agent.py`:
```python
"""AG-UI Protocol agent for real-time farm monitoring.

Streams farm thread events to the frontend via SSE.
"""

from __future__ import annotations
import asyncio
import json
import logging
from typing import AsyncGenerator

from core.redis_state import get_redis

logger = logging.getLogger(__name__)


async def farm_event_stream(
    tenant_id: int, farm_id: int
) -> AsyncGenerator[str, None]:
    """SSE stream of farm events for AG-UI frontend.

    Event types:
    - thread_status: thread state change (idle→monitoring→commenting)
    - comment_posted: successful comment with text and channel
    - error: FloodWait, mute, ban
    - health_update: account health score change
    - metrics: throughput, queue depth, active threads
    """
    redis = await get_redis()
    channel_name = f"farm:{tenant_id}:{farm_id}:events"
    pubsub = redis.pubsub()
    await pubsub.subscribe(channel_name)

    try:
        yield f"data: {json.dumps({'type': 'connected', 'farm_id': farm_id})}\n\n"

        async for message in pubsub.listen():
            if message["type"] == "message":
                event_data = message["data"]
                if isinstance(event_data, bytes):
                    event_data = event_data.decode("utf-8")
                yield f"data: {event_data}\n\n"
    except asyncio.CancelledError:
        pass
    finally:
        await pubsub.unsubscribe(channel_name)
        await pubsub.close()
```

2. `/Users/braslavskii/NEURO COMMENTING/ops_api.py` — SSE endpoint:
```python
# GET /v1/farm/{farm_id}/live — SSE stream для AG-UI
@app.get("/v1/farm/{farm_id}/live")
async def farm_live_stream(farm_id: int, ...):
    return StreamingResponse(
        farm_event_stream(tenant_id, farm_id),
        media_type="text/event-stream",
    )
```

3. `/Users/braslavskii/NEURO COMMENTING/frontend/src/hooks/useFarmStream.ts`:
```typescript
import { useState, useEffect, useRef } from 'react';

interface FarmEvent {
  type: 'connected' | 'thread_status' | 'comment_posted' | 'error' | 'health_update' | 'metrics';
  data?: any;
  timestamp?: string;
}

export function useFarmStream(farmId: number | null) {
  const [events, setEvents] = useState<FarmEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    if (!farmId) return;

    const token = localStorage.getItem('access_token');
    const es = new EventSource(`/v1/farm/${farmId}/live?token=${token}`);
    esRef.current = es;

    es.onmessage = (e) => {
      const event: FarmEvent = JSON.parse(e.data);
      if (event.type === 'connected') {
        setConnected(true);
      }
      setEvents(prev => [...prev.slice(-200), event]); // keep last 200
    };

    es.onerror = () => setConnected(false);

    return () => { es.close(); };
  }, [farmId]);

  return { events, connected };
}
```

**Критерии приёмки:**
- [ ] `GET /v1/farm/{id}/live` отдаёт SSE stream
- [ ] События из Redis pubsub транслируются в SSE
- [ ] Frontend `useFarmStream` подключается и получает события
- [ ] При отключении клиента подписка корректно закрывается
- [ ] Буфер ограничен 200 событиями (нет memory leak)

#### Задача 32.3 — MCP-server-chart для аналитики (M = 4ч)

**Описание:** Подключить MCP-сервер для генерации графиков/визуализаций.

**Файлы для изменения:**

1. `/Users/braslavskii/NEURO COMMENTING/.mcp.json` — добавить chart MCP:
```json
{
  "mcpServers": {
    "chart": {
      "command": "npx",
      "args": ["-y", "mcp-server-chart"],
      "timeout": 30000
    }
  }
}
```

2. `/Users/braslavskii/NEURO COMMENTING/.claude/skills/analytics-charts/SKILL.md`:
```markdown
---
name: analytics-charts
description: "Generate data visualizations for analytics dashboard"
---

## Skill: analytics-charts

Use the `chart` MCP server to generate visualizations.

### Available chart types
- Line chart (subscriber growth, comment velocity)
- Bar chart (comments per channel, account health distribution)
- Pie chart (category breakdown, status distribution)
- Heatmap (activity hours, geographic distribution)
- Scatter (spam_score vs subscribers)

### Data sources
Query from our API via neuro-api MCP:
- `GET /v1/analytics/events` — event timeline
- `GET /v1/channel-map/categories` — category stats
- `GET /v1/health/scores` — account health
- `GET /v1/farm/stats/live` — farm metrics
```

**Критерии приёмки:**
- [ ] chart MCP сервер доступен в `/mcp`
- [ ] Можно сгенерировать line chart из данных `/v1/analytics/events`
- [ ] Графики рендерятся как PNG/SVG

### Зависимости Sprint 32

| Зависимость | Статус | Блокирует |
|------------|--------|-----------|
| Neo4j (Docker) | Нужно добавить в compose | 32.1 |
| `graphiti-core` pip | Проверить совместимость с Python 3.11 | 32.1 |
| Redis pub/sub (уже есть) | Работает | 32.2 |
| `mcp-server-chart` npm | Проверить доступность | 32.3 |

### Риски Sprint 32

| Риск | Вероятность | Влияние | Митигация |
|------|-------------|---------|-----------|
| Neo4j добавляет ~500MB RAM к VPS | Средняя | Среднее | Отложить VPS-деплой Neo4j, использовать только локально; на VPS — fallback на PostgreSQL ltree |
| Graphiti API нестабилен (v0.x) | Средняя | Среднее | Обернуть в `KnowledgeGraphService` с graceful no-op при сбое |
| SSE теряет события при переподключении | Средняя | Низкое | Добавить `Last-Event-Id` + event replay из Redis |
| AG-UI protocol ещё экспериментальный | Высокая | Низкое | Реализовать как простой SSE без полной AG-UI спецификации, мигрировать позже |

---

## Sprint 33 — A2A Protocol + Мониторинг агентов

**Цель спринта:** Добавить agent-to-agent коммуникацию для мульти-агентной фермы и дашборд мониторинга всех Claude Code агентов/воркеров.

### User Stories

| ID | Роль | История | Приоритет |
|----|------|---------|-----------|
| US-33.1 | Как архитектор | Я хочу A2A-протокол для коммуникации между FarmOrchestrator, FarmThread, SmartCommenter и AntiDetection, чтобы агенты координировались без центрального контроллера | MUST |
| US-33.2 | Как оператор | Я хочу дашборд мониторинга всех Claude Code агентов (worktree-workers, reviewers, orchestrator), чтобы видеть что работает, что зависло | MUST |
| US-33.3 | Как DevOps | Я хочу VibeKit security layer для API-вызовов агентов, чтобы ни один агент не мог сделать что-то за пределами своих прав | SHOULD |

### Задачи

#### Задача 33.1 — A2A Protocol для фермы (XL = 16ч)

**Описание:** Реализовать Agent-to-Agent коммуникацию через A2A-совместимый протокол для координации фермы.

**Файлы для создания:**

1. `/Users/braslavskii/NEURO COMMENTING/core/a2a_bus.py`:
```python
"""Agent-to-Agent message bus for farm coordination.

Implements a lightweight A2A-compatible message bus over Redis.

Agent types:
- farm_orchestrator: dispatches channels to threads
- farm_thread: executes commenting on assigned channels
- smart_commenter: generates AI comments
- anti_detection: manages delays and detection avoidance
- health_scorer: monitors account health

Message format (A2A-compatible):
{
    "id": "uuid",
    "from_agent": "farm_thread_1",
    "to_agent": "smart_commenter",  # or "*" for broadcast
    "type": "request" | "response" | "event",
    "action": "generate_comment",
    "payload": { ... },
    "timestamp": "ISO8601",
    "correlation_id": "uuid"  # for request-response pairs
}
"""

from __future__ import annotations
import asyncio
import json
import logging
import uuid
from datetime import datetime
from typing import Any, Callable, Coroutine, Optional

from core.redis_state import get_redis

logger = logging.getLogger(__name__)


class A2ABus:
    """Redis-backed agent-to-agent message bus."""

    def __init__(self, agent_id: str, agent_type: str):
        self.agent_id = agent_id
        self.agent_type = agent_type
        self._handlers: dict[str, Callable] = {}
        self._pending: dict[str, asyncio.Future] = {}
        self._running = False

    def on(self, action: str, handler: Callable[..., Coroutine]) -> None:
        """Register handler for incoming action."""
        self._handlers[action] = handler

    async def send(
        self,
        to_agent: str,
        action: str,
        payload: dict[str, Any],
        expect_response: bool = False,
        timeout: float = 30.0,
    ) -> Optional[dict[str, Any]]:
        """Send message to another agent."""
        redis = await get_redis()
        msg_id = str(uuid.uuid4())
        message = {
            "id": msg_id,
            "from_agent": self.agent_id,
            "to_agent": to_agent,
            "type": "request" if expect_response else "event",
            "action": action,
            "payload": payload,
            "timestamp": datetime.utcnow().isoformat(),
            "correlation_id": msg_id,
        }

        channel = f"a2a:{to_agent}" if to_agent != "*" else "a2a:broadcast"
        await redis.publish(channel, json.dumps(message))

        if expect_response:
            future: asyncio.Future[dict[str, Any]] = asyncio.Future()
            self._pending[msg_id] = future
            try:
                return await asyncio.wait_for(future, timeout=timeout)
            except asyncio.TimeoutError:
                self._pending.pop(msg_id, None)
                logger.warning(f"A2A timeout: {action} -> {to_agent}")
                return None

        return None

    async def respond(
        self, correlation_id: str, to_agent: str, payload: dict[str, Any]
    ) -> None:
        """Send response to a previous request."""
        redis = await get_redis()
        message = {
            "id": str(uuid.uuid4()),
            "from_agent": self.agent_id,
            "to_agent": to_agent,
            "type": "response",
            "action": "response",
            "payload": payload,
            "timestamp": datetime.utcnow().isoformat(),
            "correlation_id": correlation_id,
        }
        await redis.publish(f"a2a:{to_agent}", json.dumps(message))

    async def start_listening(self) -> None:
        """Start listening for messages on agent's channel."""
        redis = await get_redis()
        pubsub = redis.pubsub()
        await pubsub.subscribe(f"a2a:{self.agent_id}", "a2a:broadcast")
        self._running = True

        try:
            async for message in pubsub.listen():
                if not self._running:
                    break
                if message["type"] != "message":
                    continue

                data = json.loads(message["data"])

                # Handle responses to our requests
                if data["type"] == "response":
                    cid = data.get("correlation_id")
                    if cid and cid in self._pending:
                        self._pending.pop(cid).set_result(data["payload"])
                    continue

                # Handle incoming requests/events
                handler = self._handlers.get(data["action"])
                if handler:
                    try:
                        result = await handler(data)
                        if data["type"] == "request":
                            await self.respond(
                                data["correlation_id"],
                                data["from_agent"],
                                result or {},
                            )
                    except Exception as e:
                        logger.error(f"A2A handler error: {data['action']}: {e}")
        finally:
            await pubsub.unsubscribe()
            await pubsub.close()

    async def stop(self) -> None:
        self._running = False
```

2. `/Users/braslavskii/NEURO COMMENTING/tests/test_a2a_bus.py`:
```python
"""Tests for A2A message bus."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from core.a2a_bus import A2ABus


def test_a2a_bus_init():
    bus = A2ABus("thread_1", "farm_thread")
    assert bus.agent_id == "thread_1"
    assert bus.agent_type == "farm_thread"


def test_a2a_bus_register_handler():
    bus = A2ABus("thread_1", "farm_thread")
    handler = AsyncMock()
    bus.on("generate_comment", handler)
    assert "generate_comment" in bus._handlers


def test_a2a_bus_no_duplicate_handlers():
    bus = A2ABus("thread_1", "farm_thread")
    handler1 = AsyncMock()
    handler2 = AsyncMock()
    bus.on("generate_comment", handler1)
    bus.on("generate_comment", handler2)
    assert bus._handlers["generate_comment"] is handler2
```

**Критерии приёмки:**
- [ ] `python -m py_compile core/a2a_bus.py` проходит
- [ ] A2ABus.send() публикует сообщение в Redis
- [ ] A2ABus.start_listening() получает сообщения из Redis
- [ ] Request-response pattern работает с correlation_id
- [ ] Broadcast на `a2a:broadcast` доставляется всем агентам
- [ ] Timeout при отсутствии ответа возвращает None
- [ ] `pytest tests/test_a2a_bus.py -v` — все тесты зелёные

#### Задача 33.2 — Интеграция A2A в ферму (L = 8ч)

**Описание:** Подключить A2ABus к FarmOrchestrator и FarmThread.

**Файлы для изменения:**

1. `/Users/braslavskii/NEURO COMMENTING/core/farm_orchestrator.py` — добавить A2ABus:
```python
# В __init__:
self.bus = A2ABus(f"orchestrator_{farm_id}", "farm_orchestrator")
self.bus.on("thread_completed", self._handle_thread_completed)
self.bus.on("thread_error", self._handle_thread_error)
self.bus.on("redistribute_request", self._handle_redistribute)
```

2. `/Users/braslavskii/NEURO COMMENTING/core/farm_thread.py` — добавить A2ABus:
```python
# В __init__:
self.bus = A2ABus(f"thread_{thread_id}", "farm_thread")

# При генерации комментария — A2A request к smart_commenter:
comment = await self.bus.send(
    "smart_commenter",
    "generate_comment",
    {"post_text": post.text, "channel": channel.username, "style": self.config.style},
    expect_response=True,
    timeout=15.0,
)
```

**Критерии приёмки:**
- [ ] FarmOrchestrator создаёт A2ABus при старте
- [ ] FarmThread отправляет A2A request для генерации комментария
- [ ] SmartCommenter получает request и возвращает response
- [ ] При таймауте FarmThread fallback на прямой route_ai_task

#### Задача 33.3 — Omnara мониторинг агентов (L = 8ч)

**Описание:** Добавить дашборд мониторинга Claude Code агентов и farm-агентов.

**Файлы для создания:**

1. `/Users/braslavskii/NEURO COMMENTING/core/agent_monitor.py`:
```python
"""Agent monitoring service.

Tracks:
- Claude Code agents (worktree-workers, reviewers, orchestrator)
- Farm agents (orchestrator, threads, commenter)
- Agent lifecycle (created, running, completed, failed)
- Performance metrics (duration, tokens, cost)
"""

from __future__ import annotations
import logging
from datetime import datetime
from typing import Any

from core.redis_state import get_redis

logger = logging.getLogger(__name__)

AGENT_STATUS_KEY = "agents:status"
AGENT_METRICS_KEY = "agents:metrics"


async def register_agent(
    agent_id: str,
    agent_type: str,
    status: str = "running",
    metadata: dict[str, Any] | None = None,
) -> None:
    """Register or update agent status in Redis."""
    redis = await get_redis()
    data = {
        "agent_id": agent_id,
        "agent_type": agent_type,
        "status": status,
        "started_at": datetime.utcnow().isoformat(),
        "metadata": metadata or {},
    }
    import json
    await redis.hset(AGENT_STATUS_KEY, agent_id, json.dumps(data))
    await redis.expire(AGENT_STATUS_KEY, 86400)  # 24h TTL


async def update_agent_status(agent_id: str, status: str, **kwargs: Any) -> None:
    """Update agent status."""
    redis = await get_redis()
    import json
    raw = await redis.hget(AGENT_STATUS_KEY, agent_id)
    if raw:
        data = json.loads(raw)
        data["status"] = status
        data.update(kwargs)
        await redis.hset(AGENT_STATUS_KEY, agent_id, json.dumps(data))


async def list_agents(agent_type: str | None = None) -> list[dict[str, Any]]:
    """List all registered agents."""
    redis = await get_redis()
    import json
    all_agents = await redis.hgetall(AGENT_STATUS_KEY)
    result = []
    for _k, v in all_agents.items():
        data = json.loads(v)
        if agent_type and data.get("agent_type") != agent_type:
            continue
        result.append(data)
    return result


async def get_agent_metrics() -> dict[str, Any]:
    """Get aggregate agent metrics."""
    agents = await list_agents()
    return {
        "total": len(agents),
        "running": sum(1 for a in agents if a["status"] == "running"),
        "completed": sum(1 for a in agents if a["status"] == "completed"),
        "failed": sum(1 for a in agents if a["status"] == "failed"),
        "by_type": {},
    }
```

2. `/Users/braslavskii/NEURO COMMENTING/ops_api.py` — добавить 2 эндпоинта:
```python
# GET /v1/agents — список всех агентов (farm + claude code)
# GET /v1/agents/metrics — агрегированные метрики
```

3. `/Users/braslavskii/NEURO COMMENTING/frontend/src/pages/AgentMonitorPage.tsx`:
```typescript
// Дашборд мониторинга агентов
// - Таблица всех агентов (ID, тип, статус, длительность)
// - Счётчики: running / completed / failed
// - Auto-refresh каждые 5 секунд
// - Фильтры по типу агента
```

**Критерии приёмки:**
- [ ] `GET /v1/agents` возвращает список агентов с метаданными
- [ ] `GET /v1/agents/metrics` возвращает агрегированные метрики
- [ ] AgentMonitorPage рендерится без ошибок
- [ ] Farm-агенты автоматически регистрируются при старте
- [ ] Статусы обновляются в реальном времени (5с polling)

#### Задача 33.4 — VibeKit security layer (M = 4ч)

**Описание:** Добавить security layer для API-вызовов агентов.

**Файлы для создания:**

1. `/Users/braslavskii/NEURO COMMENTING/core/agent_security.py`:
```python
"""Agent security layer.

Each agent type has a permission set:
- farm_thread: can read channels, post comments, update health
- smart_commenter: can call AI router, read channel context
- health_scorer: can read/write health scores
- worktree_worker: can read/write files in own worktree only

No agent can:
- Access other tenants' data
- Delete accounts or farms
- Modify billing
- Push to git
"""

from __future__ import annotations
import logging
from typing import Any

logger = logging.getLogger(__name__)

AGENT_PERMISSIONS = {
    "farm_orchestrator": {
        "allow": [
            "farm:start", "farm:stop", "farm:pause", "farm:resume",
            "thread:create", "thread:assign", "channel:read",
        ],
        "deny": ["billing:*", "admin:*", "auth:*"],
    },
    "farm_thread": {
        "allow": [
            "channel:read", "comment:post", "health:update",
            "ai:route_task", "anti_detection:simulate",
        ],
        "deny": ["farm:delete", "account:delete", "billing:*", "admin:*"],
    },
    "smart_commenter": {
        "allow": [
            "ai:route_task", "channel:read", "comment:generate",
        ],
        "deny": ["comment:post", "account:*", "billing:*"],
    },
    "health_scorer": {
        "allow": ["health:read", "health:write", "account:read"],
        "deny": ["account:write", "billing:*", "admin:*"],
    },
}


def check_agent_permission(
    agent_type: str, action: str
) -> bool:
    """Check if agent type is allowed to perform action."""
    perms = AGENT_PERMISSIONS.get(agent_type)
    if not perms:
        logger.warning(f"Unknown agent type: {agent_type}")
        return False

    # Check deny first (deny wins)
    for deny_pattern in perms.get("deny", []):
        if _matches(deny_pattern, action):
            return False

    # Check allow
    for allow_pattern in perms.get("allow", []):
        if _matches(allow_pattern, action):
            return True

    return False  # default deny


def _matches(pattern: str, action: str) -> bool:
    """Simple wildcard matching: 'billing:*' matches 'billing:cancel'."""
    if pattern == action:
        return True
    if pattern.endswith(":*"):
        prefix = pattern[:-2]
        return action.startswith(prefix + ":")
    return False
```

2. `/Users/braslavskii/NEURO COMMENTING/tests/test_agent_security.py`:
```python
from core.agent_security import check_agent_permission

def test_farm_thread_can_post_comment():
    assert check_agent_permission("farm_thread", "comment:post") is True

def test_farm_thread_cannot_delete_farm():
    assert check_agent_permission("farm_thread", "farm:delete") is False

def test_smart_commenter_cannot_post():
    assert check_agent_permission("smart_commenter", "comment:post") is False

def test_unknown_agent_denied():
    assert check_agent_permission("unknown_agent", "anything") is False

def test_health_scorer_can_read_health():
    assert check_agent_permission("health_scorer", "health:read") is True

def test_health_scorer_cannot_admin():
    assert check_agent_permission("health_scorer", "admin:create") is False
```

**Критерии приёмки:**
- [ ] `python -m py_compile core/agent_security.py` проходит
- [ ] `pytest tests/test_agent_security.py -v` — все тесты зелёные
- [ ] A2ABus проверяет permissions перед выполнением handler
- [ ] Denied actions логируются с warning уровнем

### Зависимости Sprint 33

| Зависимость | Статус | Блокирует |
|------------|--------|-----------|
| Sprint 29 (worktree) | Для мониторинга Claude Code agents | 33.3 |
| Redis pub/sub (уже есть) | Работает | 33.1 |
| FarmOrchestrator + FarmThread (Sprint 5) | Реализованы | 33.2 |

### Риски Sprint 33

| Риск | Вероятность | Влияние | Митигация |
|------|-------------|---------|-----------|
| A2A через Redis медленнее прямых вызовов | Средняя | Среднее | Профилировать; для hot path (<5ms) оставить прямые вызовы |
| Мониторинг агентов увеличивает Redis load | Низкая | Низкое | TTL 24h на ключи, не чаще 1 записи в 5 секунд |
| VibeKit перестраховывает и блокирует легитимные действия | Средняя | Среднее | Логировать все deny, настраивать permissions итеративно |
| A2A deadlock между агентами | Низкая | Высокое | Timeout 30s на все request-response, no circular dependencies |

---

## Общая карта зависимостей

```
Sprint 28 (FastAPI-MCP + Trusted Tools)
    ↓
Sprint 29 (Worktree + GitHub Actions)  ← Sprint 28 не блокирует
    ↓
Sprint 30 (Context Hub + Playwright E2E)  ← Sprint 28 MCP enhances this
    ↓
Sprint 31 (Code Context + Agent Orchestrator)  ← Sprint 29 worktree REQUIRED
    ↓
Sprint 32 (Graphiti + AG-UI)  ← независим от 31
    ↓
Sprint 33 (A2A + Мониторинг)  ← Sprint 29 + Sprint 32 рекомендованы
```

## Суммарные оценки

| Спринт | Задач | Оценка (часов) | Критический путь |
|--------|-------|----------------|-----------------|
| 28 | 5 | 16ч (2S + 2M + 0L) | FastAPI-MCP установка |
| 29 | 5 | 24ч (2S + 1M + 2L) | Worktree инфраструктура |
| 30 | 4 | 26ч (1S + 2M + 0L + 1XL) | Playwright E2E на 35 страниц |
| 31 | 3 | 28ч (0S + 1M + 1L + 1XL) | Agent Orchestrator pattern |
| 32 | 3 | 28ч (0S + 1M + 1L + 1XL) | Graphiti + Neo4j |
| 33 | 4 | 36ч (0S + 1M + 2L + 1XL) | A2A интеграция в ферму |
| **ИТОГО** | **24** | **158ч** | — |

## Env-переменные для добавления

```bash
# Sprint 28
MCP_ENABLED=true

# Sprint 32
NEO4J_URI=bolt://localhost:7687
NEO4J_PASSWORD=<secure>
KNOWLEDGE_GRAPH_ENABLED=false

# Sprint 33
A2A_BUS_ENABLED=true
AGENT_MONITOR_ENABLED=true
```

## Новые зависимости (requirements.txt)

```
# Sprint 28
fastapi-mcp

# Sprint 32
graphiti-core
neo4j

# Sprint 33
# (нет новых — всё на Redis)
```

## Новые npm зависимости (package.json)

```
# Sprint 30 (.mcp.json only, не в package.json)
@upstash/context7-mcp
mcp-remote

# Sprint 31
claude-squad (глобальная установка)
```
