# Runtime агентов — YouTube Content Pipeline

Эта папка делает взаимодействие YouTube-контент-агентов явным и проверяемым.

## Папки

- `shared/`: промежуточные артефакты, которыми пользуются несколько агентов
- `messages/`: handoff-сообщения, блокеры, вопросы и approvals
- `state/`: план, статус-доска и служебные заметки процесса
- `outputs/`: финальные результаты

## Агенты команды

| Роль | Агент | Файл |
|------|-------|------|
| Супервайзер | yt-showrunner | `.claude/agents/yt-showrunner.md` |
| Ресерчер | yt-web-researcher | `.claude/agents/yt-web-researcher.md` |
| Фактчекер | yt-fact-checker | `.claude/agents/yt-fact-checker.md` |
| Сценарист | yt-script-architect | `.claude/agents/yt-script-architect.md` |
| Визуалист | yt-visual-director | `.claude/agents/yt-visual-director.md` |
| PDF-продюсер | yt-pdf-producer | `.claude/agents/yt-pdf-producer.md` |

## Workflow

1. `yt-showrunner` читает brief и публикует план
2. `yt-web-researcher` и `yt-visual-director` стартуют параллельно
3. `yt-web-researcher` передает тезисы `yt-fact-checker`
4. `yt-fact-checker` одобряет/ревизирует/отклоняет
5. `yt-script-architect` пишет нарратив из одобренных тезисов + визуальных хуков
6. `yt-pdf-producer` упаковывает финальный пакет
7. `yt-showrunner` утверждает или запускает revision loop

## Запуск

```bash
# Внутри tmux
tmux new -s yt-content
claude
# Затем дать lead-инструкцию на создание Agent Team
```

Требуется `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` в настройках.
