# Telegram ToS Compliance Notes

Checked at: 2026-03-06
Source: https://telegram.org/tos

## Normalized Rules

- `TG-TOS-001`: No spam/scam behavior.
- `TG-TOS-002`: Violations can lead to temporary or permanent ban.
- `TG-TOS-003`: Automation must not simulate abusive messaging patterns.

## Runtime Enforcement Mapping

- Policy engine blocks comments outside approved lifecycle.
- Policy engine quarantines burst-like behavior.
- Restricted accounts are excluded from parser/comment/packaging pipelines.
