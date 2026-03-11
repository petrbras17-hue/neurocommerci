# Telegram API Error Handling Compliance Notes

Checked at: 2026-03-06
Source: https://core.telegram.org/api/errors#error-handling

## Normalized Rules

- `TG-ERR-001`: `FLOOD_WAIT_X` requires waiting before retry.
- `TG-ERR-002`: Error handling must be explicit and deterministic.
- `TG-ERR-003`: Repeated failures should trigger risk controls and backoff.

## Runtime Enforcement Mapping

- FloodWait events emit policy warnings and risk increments.
- Worker dequeues and retries are bounded and backoff-based.
- Escalation path: active -> cooldown -> restricted.
