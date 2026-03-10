# Sprint 3/4 Live Audit

Date: 2026-03-10  
Environment: VPS `176.124.221.253`, public domain `https://176-124-221-253.sslip.io`  
Branch: `sprint/3-telegram-first-auth-shell`  
Latest live-audit commits:
- `b38bb86` — assistant digest integration
- `6254409` — creative draft rendering fix

## Audit scope

This audit was run in isolated `TEST` / `AUDIT` mode only.

Included:
- Telegram-first web login
- operator-first shell routes
- Gemini assistant endpoints
- business context storage
- Google Sheets mirror
- Telegram digest notifications

Excluded:
- real `.session + .json` account uploads
- Telegram-side account execution
- packager / worker execution
- any production account actions

## Test markers used

- `TEST-AUDIT-20260310-001833`
- `TEST-FULL-20260310-002215`

These markers were used in test tenants, context rows, drafts, and digest payloads.

## What works end-to-end on VPS

### 1. Public web shell

Public routes return `200`:
- `/app/dashboard`
- `/app/accounts`
- `/app/assistant`
- `/app/context`
- `/app/creative`

Verified through browser automation on the public `sslip.io` domain.

### 2. Telegram-first auth

Confirmed working live:
- Telegram login widget renders
- profile completion flow succeeds
- `/auth/telegram/verify` works
- `/auth/complete-profile` works
- `/auth/me` returns current user/tenant/workspace state

### 3. Safe operator shell

Verified without adding real accounts:
- `/app/dashboard` shows safe onboarding summary and next-step guidance
- `/app/accounts` shows empty-safe state
- notes/timeline UX is available without triggering Telegram-side account actions
- UI clearly separates:
  - what the system does
  - what the operator does manually

### 4. Assistant flow

Confirmed live over public HTTPS:
1. start brief
2. send assistant message
3. context stored in DB
4. context confirm succeeds
5. creative draft generation succeeds
6. creative draft approval succeeds

### 5. Google Sheets mirror

Confirmed live in worksheet `Брифы`.

Rows present:
- `TEST-AUDIT-20260310-001833`
- `TEST-FULL-20260310-002215`

This proves context-confirm mirror works against the real spreadsheet.

### 6. Telegram digest notifications

Confirmed live through `@neurosvodka_bot` into `NeuroSvodka`:
- digest integration test message delivered
- context-confirm digest notification delivered

`DIGEST_BOT_TOKEN` and `DIGEST_CHAT_ID=-1003597102248` are present on VPS.

### 7. Tenant safety

Confirmed by existing Sprint 1 and Sprint 3 backend tests:
- no cross-tenant read/write in assistant/context/drafts web surfaces
- public marketing routes remain public
- internal ops routes remain internal-token only

## What works, but only in degraded mode

### Gemini path

Gemini is configured and reachable on VPS:
- `GEMINI_API_KEY` present
- `GEMINI_MODEL=gemini-3.1-pro-preview`

But live assistant behavior shows the model contract is brittle:
- assistant replies often fall back to deterministic server-side text
- creative generation often falls back to deterministic variant generation

Evidence:
- last live assistant reply matched the exact fallback string:
  - `Я обновил контекст там, где это удалось определить из вашего сообщения...`
- generated creative variants matched fallback template structure
- previous live logs showed Gemini JSON parse failures for assistant/creative requests

Conclusion:
- Gemini integration is wired and callable
- Sprint 4 assistant MVP works functionally
- but **not yet natively/stably Gemini-first in production quality**

## What was fixed during this audit

### Assistant digest integration

Added:
- digest send on `context confirm`
- company included in brief mirror snapshot

Result:
- DB truth + Sheets + Telegram digest now complete the same flow

### `/app/creative` runtime crash

Bug:
- populated creative page crashed because frontend expected `variants: string[]`
- backend returns structured variants like `{title, content}`

Fix:
- frontend now normalizes both string and object variants

Result:
- populated `/app/creative` renders correctly
- browser console now shows `0` errors on the route

## What is still not production-ready

### 1. Gemini structured output contract

Current problem:
- assistant and creative generation depend on strict JSON parsing
- malformed model output causes fallback behavior

Needed next:
- stronger response schema
- safer parser / repair layer
- explicit telemetry per request:
  - provider
  - model
  - latency
  - parsed_ok
  - fallback_used

### 2. VPS test isolation

Current problem:
- runtime on VPS is healthy
- but server-side pytest is not yet isolated from the live compose environment enough for clean repeatable post-deploy verification

Needed next:
- dedicated test DB / test compose profile
- isolated post-deploy verification job

### 3. Assistant observability

Current gap:
- no clean runtime surface yet that tells operator:
  - Gemini native success rate
  - fallback rate
  - Sheets mirror success/failure
  - digest send success/failure

Needed next:
- assistant metrics and audit log surface in web UI or internal endpoint

## Final verdict

### Can Sprint 3 be considered closed?

Yes.

Reason:
- Telegram-first auth works publicly
- safe operator shell works publicly
- `/app/dashboard` and `/app/accounts` are useful without touching real accounts
- no Telegram-side account execution is required for Sprint 3 acceptance

### Can Sprint 4 be considered MVP working?

Partially yes.

Reason:
- assistant, context, creative, Sheets mirror, and digest notification all work live end-to-end
- but Gemini generation is still **production-fragile** and frequently falls back to deterministic logic

### Main blockers before the next sprint

1. Harden Gemini response parsing and telemetry
2. Add explicit assistant observability
3. Improve VPS test isolation
4. Keep real account execution out of acceptance until that layer is redesigned separately

## Recommended next sprint focus

Do not move to billing first.

Best next step:
- stabilize the assistant layer as a premium operator tool:
  - robust Gemini contracts
  - context quality controls
  - creative review ergonomics
  - audit/telemetry

Only after that:
- continue with billing and commercial rollout
