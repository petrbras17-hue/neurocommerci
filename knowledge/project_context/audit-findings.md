# Audit Findings

- Реальный runtime расходился с документацией: `DISTRIBUTED_QUEUE_MODE`, legacy UI и strict policy давали разные execution paths.
- Session layout был смешанным: flat root, legacy nested folders и частичный per-user layout.
- Очереди не имели lease/ack/retry/dlq, поэтому задачи могли теряться без объяснимого следа.
- Каналы не имели publish policy state; auto execution path не отличал discovered candidates от approved destinations.
- Account readiness не показывалась явно: owner/session/proxy/stage blockers были скрыты в поведении кода.
