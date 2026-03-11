# Telegram API Terms Compliance Notes

Checked at: 2026-03-06
Source: https://core.telegram.org/api/terms

## Normalized Rules

- `TG-API-001`: Use API within allowed functionality boundaries.
- `TG-API-002`: Do not interfere with platform abuse mitigation.
- `TG-API-003`: Respect API limits and expected usage patterns.

## Runtime Enforcement Mapping

- Fail-fast parser checks for restricted/dead accounts.
- Strict proxy policy prevents risky shared-IP operation.
- Risk scoring escalates accounts to cooldown/restricted automatically.
