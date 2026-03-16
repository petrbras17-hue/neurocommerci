# YouTube Content Pipeline — Agent Teams Workflow

Спецификация мультиагентного процесса для YouTube-контента NEURO COMMENTING.

## Архитектура

6 агентов (все с префиксом `yt-`) работают через Claude Agent Teams в tmux:

```
yt-showrunner ──────────────────────────────────────┐
    │                                                │
    ├── yt-web-researcher ──► yt-fact-checker         │
    │                              │                  │
    ├── yt-visual-director ────────┤                  │
    │                              │                  │
    │                        yt-script-architect      │
    │                              │                  │
    │                        yt-pdf-producer          │
    │                              │                  │
    └──────────── final-approval ◄─┘                  │
                                                      │
```

## Фазы

### Фаза 1: Kickoff
- `yt-showrunner` читает `agent-runtime/shared/brief.md`
- Пишет `agent-runtime/state/plan.md`
- Отправляет assignments

### Фаза 2: Ресерч + визуал (ПАРАЛЛЕЛЬНО)
- `yt-web-researcher` → `research-raw.md`, `research-summary.md`
- `yt-visual-director` → `visual-plan.md`

### Фаза 3: Валидация
- `yt-fact-checker` → `verified-claims.md`, `fact-check-notes.md`

### Фаза 4: Сборка нарратива
- `yt-script-architect` → `script-outline.md`, `video-script.md`

### Фаза 5: Упаковка
- `yt-pdf-producer` → `final-report.md`, `final-report.pdf`
- `yt-showrunner` → `final-approval.md`

## Варианты подачи

| Вариант | Описание | Лучше для |
|---------|----------|-----------|
| A: Pipeline | Линейный с параллельным ресерчем | Первое демо, повторяемость |
| B: Доска | Агенты сами забирают задачи | Вау-эффект на YouTube |
| C: Спор | Fact-checker атакует тезисы | Образовательный контент |

Рекомендация: A + элементы B (видимые сообщения) + один конфликт из C.

## Запуск

```bash
tmux new -s yt-content
cd "/Users/braslavskii/NEURO COMMENTING"
claude
# Далее: "Создай Agent Team для YouTube-контента, используй yt-* агентов"
```

## Источник

Адаптировано из проекта `/Users/braslavskii/Agent Teams/`.
