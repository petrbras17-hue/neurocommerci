# Telethon Operational Limits & Session Safety

Checked at: 2026-03-06
Sources:
- https://docs.telethon.dev/en/stable/quick-references/faq.html#my-account-was-deleted-limited-when-using-the-library
- https://docs.telethon.dev/en/stable/concepts/sessions.html

## Normalized Rules

- `TG-TL-001`: Library must not be used for abusive patterns.
- `TG-TL-002`: Session files are sensitive and must be kept safe.
- `TG-TL-003`: Concurrent duplicate session usage raises account risk.

## Runtime Enforcement Mapping

- Duplicate session detection triggers quarantine policy.
- Session manager + worker pinning reduce concurrent session collisions.
- Compliance logs preserve traceability for each blocked action.
