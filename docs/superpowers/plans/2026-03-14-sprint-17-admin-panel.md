# Sprint 17: Admin Panel Foundation — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build admin panel with ADMIN/CLIENT mode toggle, account onboarding wizard (tdata/session/ZIP upload → proxy bind → verify → harden), proxy manager, and operations log — all through web UI.

**Architecture:** Add `is_platform_admin` to auth_users. Admin sidebar shows when toggled. New `/v1/admin/onboarding/*` endpoints handle account lifecycle. Server-side tdata conversion via opentele. All new tables have `workspace_id` for future multi-tenant. WebSocket not in this sprint (Sprint 19).

**Tech Stack:** Python/FastAPI, SQLAlchemy/Alembic, React/TypeScript, opentele, Telethon, SOCKS5 proxy support

---

## File Map

### New Files
| File | Purpose |
|------|---------|
| `alembic/versions/20260314_35_admin_panel_foundation.py` | Migration: is_platform_admin, admin_accounts, admin_proxies, admin_operations_log |
| `core/admin_onboarding.py` | Account onboarding service: upload, convert tdata, verify, harden, bind proxy |
| `core/admin_proxy_service.py` | Proxy management: import, test HTTP/SOCKS5/HTTPS CONNECT, bind |
| `frontend/src/pages/admin/AdminDashboardPage.tsx` | Command center with stats + quick actions |
| `frontend/src/pages/admin/AccountOnboardingWizard.tsx` | 6-step wizard component |
| `frontend/src/pages/admin/AdminProxyManagerPage.tsx` | Proxy import/test/bind UI |
| `frontend/src/pages/admin/AdminOperationsLogPage.tsx` | Operations log viewer |
| `frontend/src/components/admin/AdminModeToggle.tsx` | ADMIN/CLIENT toggle component |
| `tests/test_admin_onboarding.py` | Backend tests for admin endpoints |

### Modified Files
| File | Changes |
|------|---------|
| `storage/models.py` | Add `is_platform_admin` to AuthUser, add AdminAccount, AdminProxy, AdminOperationLog models |
| `ops_api.py` | Add `/v1/admin/onboarding/*` and `/v1/admin/proxies/*` endpoints |
| `frontend/src/auth.tsx` | Add `is_platform_admin` to AuthBundle |
| `frontend/src/layout/AppShell.tsx` | Add admin mode toggle + conditional admin nav groups |
| `frontend/src/App.tsx` | Add admin page routes |
| `frontend/src/api.ts` | Add admin API functions |

---

## Task 1: Database Migration — Admin Foundation Tables

**Files:**
- Create: `alembic/versions/20260314_35_admin_panel_foundation.py`
- Modify: `storage/models.py`

- [ ] **Step 1: Write the migration**

```python
"""Admin panel foundation — is_platform_admin, admin_accounts, admin_proxies, admin_operations_log"""

revision = '20260314_35'
down_revision = '20260314_34'

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

def upgrade():
    # Add is_platform_admin to auth_users
    op.add_column('auth_users', sa.Column('is_platform_admin', sa.Boolean(), server_default='false', nullable=False))

    # Admin-managed accounts (mirrors data/sessions structure but in PostgreSQL)
    op.create_table('admin_accounts',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('workspace_id', sa.Integer(), nullable=False),
        sa.Column('phone', sa.String(20), nullable=False, unique=True),
        sa.Column('country', sa.String(5)),
        sa.Column('display_name', sa.String(255)),
        sa.Column('username', sa.String(255)),
        sa.Column('bio', sa.Text()),
        sa.Column('api_id', sa.Integer()),
        sa.Column('api_hash', sa.String(64)),
        sa.Column('dc_id', sa.Integer()),
        sa.Column('session_path', sa.String(512)),  # relative path to .session file
        sa.Column('proxy_id', sa.Integer()),
        sa.Column('two_fa_password', sa.String(128)),
        sa.Column('status', sa.String(32), server_default='uploaded'),  # uploaded/verified/hardened/warmup/ready/frozen/appeal/banned/dead
        sa.Column('lifecycle_phase', sa.String(32), server_default='day0'),
        sa.Column('source', sa.String(32)),  # tdata/session_json/phone_code
        sa.Column('metadata', JSONB, server_default='{}'),
        sa.Column('security_hardened_at', sa.DateTime(timezone=True)),
        sa.Column('warmup_started_at', sa.DateTime(timezone=True)),
        sa.Column('profile_change_earliest', sa.DateTime(timezone=True)),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_admin_accounts_workspace', 'admin_accounts', ['workspace_id'])
    op.create_index('ix_admin_accounts_status', 'admin_accounts', ['status'])

    # Admin-managed proxies
    op.create_table('admin_proxies',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('workspace_id', sa.Integer(), nullable=False),
        sa.Column('host', sa.String(255), nullable=False),
        sa.Column('port', sa.Integer(), nullable=False),
        sa.Column('username', sa.String(255)),
        sa.Column('password', sa.String(255)),
        sa.Column('proxy_type', sa.String(10), server_default='socks5'),  # socks5/http
        sa.Column('country', sa.String(5)),
        sa.Column('status', sa.String(16), server_default='untested'),  # untested/alive/dead
        sa.Column('bound_account_id', sa.Integer()),  # FK to admin_accounts
        sa.Column('last_tested_at', sa.DateTime(timezone=True)),
        sa.Column('last_ip', sa.String(45)),  # IP returned from test
        sa.Column('supports_https_connect', sa.Boolean()),
        sa.Column('metadata', JSONB, server_default='{}'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_admin_proxies_workspace', 'admin_proxies', ['workspace_id'])
    op.create_index('ix_admin_proxies_status', 'admin_proxies', ['status'])

    # Operations log
    op.create_table('admin_operations_log',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('workspace_id', sa.Integer(), nullable=False),
        sa.Column('account_id', sa.Integer()),
        sa.Column('proxy_id', sa.Integer()),
        sa.Column('module', sa.String(32), nullable=False),  # onboarding/proxy/warmup/security/appeal
        sa.Column('action', sa.String(64), nullable=False),  # upload/verify/harden/test_proxy/bind/etc
        sa.Column('status', sa.String(16), nullable=False),  # started/success/error
        sa.Column('detail', sa.Text()),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_admin_ops_log_workspace', 'admin_operations_log', ['workspace_id'])
    op.create_index('ix_admin_ops_log_created', 'admin_operations_log', ['created_at'])

    # RLS policies
    for table in ['admin_accounts', 'admin_proxies', 'admin_operations_log']:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY {table}_tenant_isolation ON {table}
            FOR ALL
            USING (workspace_id::text = current_setting('app.current_workspace_id', true))
            WITH CHECK (workspace_id::text = current_setting('app.current_workspace_id', true))
        """)

def downgrade():
    op.drop_table('admin_operations_log')
    op.drop_table('admin_proxies')
    op.drop_table('admin_accounts')
    op.drop_column('auth_users', 'is_platform_admin')
```

- [ ] **Step 2: Add ORM models to storage/models.py**

Add after existing models:

```python
class AdminAccount(Base):
    __tablename__ = "admin_accounts"
    id = Column(Integer, primary_key=True)
    workspace_id = Column(Integer, nullable=False, index=True)
    phone = Column(String(20), nullable=False, unique=True)
    country = Column(String(5))
    display_name = Column(String(255))
    username = Column(String(255))
    bio = Column(Text)
    api_id = Column(Integer)
    api_hash = Column(String(64))
    dc_id = Column(Integer)
    session_path = Column(String(512))
    proxy_id = Column(Integer)
    two_fa_password = Column(String(128))
    status = Column(String(32), server_default="uploaded")
    lifecycle_phase = Column(String(32), server_default="day0")
    source = Column(String(32))
    metadata_ = Column("metadata", JSONB, server_default="{}")
    security_hardened_at = Column(DateTime(timezone=True))
    warmup_started_at = Column(DateTime(timezone=True))
    profile_change_earliest = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())

class AdminProxy(Base):
    __tablename__ = "admin_proxies"
    id = Column(Integer, primary_key=True)
    workspace_id = Column(Integer, nullable=False, index=True)
    host = Column(String(255), nullable=False)
    port = Column(Integer, nullable=False)
    username = Column(String(255))
    password = Column(String(255))
    proxy_type = Column(String(10), server_default="socks5")
    country = Column(String(5))
    status = Column(String(16), server_default="untested")
    bound_account_id = Column(Integer)
    last_tested_at = Column(DateTime(timezone=True))
    last_ip = Column(String(45))
    supports_https_connect = Column(Boolean)
    metadata_ = Column("metadata", JSONB, server_default="{}")
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class AdminOperationLog(Base):
    __tablename__ = "admin_operations_log"
    id = Column(Integer, primary_key=True)
    workspace_id = Column(Integer, nullable=False, index=True)
    account_id = Column(Integer)
    proxy_id = Column(Integer)
    module = Column(String(32), nullable=False)
    action = Column(String(64), nullable=False)
    status = Column(String(16), nullable=False)
    detail = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
```

Add `is_platform_admin` to AuthUser:
```python
is_platform_admin = Column(Boolean, server_default="false", nullable=False)
```

- [ ] **Step 3: Run migration locally**

Run: `cd "/Users/braslavskii/NEURO COMMENTING" && ./.venv/bin/alembic upgrade head`
Expected: Migration applies cleanly

- [ ] **Step 4: Commit**

```bash
git add alembic/versions/20260314_35_admin_panel_foundation.py storage/models.py
git commit -m "feat(sprint-17): add admin panel foundation tables and is_platform_admin"
```

---

## Task 2: Admin Onboarding Backend Service

**Files:**
- Create: `core/admin_onboarding.py`

- [ ] **Step 1: Write admin_onboarding.py**

Core functions:
- `upload_tdata(workspace_id, tdata_path) -> AdminAccount` — convert tdata via opentele, save session, create DB record
- `upload_session_json(workspace_id, session_bytes, json_bytes) -> AdminAccount` — save .session+.json, create DB record
- `verify_account(account_id, proxy) -> dict` — connect via Telethon, check auth, return get_me()
- `harden_account(account_id, proxy) -> dict` — kill sessions, set 2FA, privacy (with human-like delays)
- `log_operation(workspace_id, account_id, module, action, status, detail)` — write to admin_operations_log

Each function uses proper error handling, human-like delays for Telegram operations, and logs every step.

- [ ] **Step 2: Compile check**

Run: `./.venv/bin/python -c "import py_compile; py_compile.compile('core/admin_onboarding.py', doraise=True)"`
Expected: No errors

- [ ] **Step 3: Commit**

```bash
git add core/admin_onboarding.py
git commit -m "feat(sprint-17): add admin onboarding service (upload, verify, harden)"
```

---

## Task 3: Admin Proxy Service

**Files:**
- Create: `core/admin_proxy_service.py`

- [ ] **Step 1: Write admin_proxy_service.py**

Core functions:
- `import_proxies(workspace_id, lines: list[str]) -> list[AdminProxy]` — parse host:port:user:pass lines, create DB records
- `test_proxy(proxy_id) -> dict` — test HTTP + SOCKS5 + HTTPS CONNECT, update status, return results
- `bind_proxy_to_account(proxy_id, account_id) -> bool` — set 1:1 binding, enforce uniqueness
- `unbind_proxy(proxy_id) -> bool` — remove binding
- `get_free_proxies(workspace_id, country=None) -> list` — list unbound alive proxies

- [ ] **Step 2: Compile check**

Run: `./.venv/bin/python -c "import py_compile; py_compile.compile('core/admin_proxy_service.py', doraise=True)"`

- [ ] **Step 3: Commit**

```bash
git add core/admin_proxy_service.py
git commit -m "feat(sprint-17): add admin proxy service (import, test, bind)"
```

---

## Task 4: Backend API Endpoints

**Files:**
- Modify: `ops_api.py`

- [ ] **Step 1: Add platform admin middleware**

```python
def require_platform_admin(ctx: TenantContext):
    """Require is_platform_admin=True on auth_users."""
    # Query auth_users for is_platform_admin
    # Return 403 if not platform admin
```

- [ ] **Step 2: Add onboarding endpoints**

```
POST /v1/admin/onboarding/upload-tdata      — multipart file upload (ZIP with tdata/)
POST /v1/admin/onboarding/upload-session     — multipart (.session + .json)
POST /v1/admin/onboarding/upload-zip         — multipart (ZIP with multiple accounts)
GET  /v1/admin/onboarding/accounts           — list admin accounts with filters
GET  /v1/admin/onboarding/accounts/{id}      — single account detail
POST /v1/admin/onboarding/accounts/{id}/verify   — connect and check auth
POST /v1/admin/onboarding/accounts/{id}/harden   — security hardening
DELETE /v1/admin/onboarding/accounts/{id}    — remove account
```

- [ ] **Step 3: Add proxy endpoints**

```
POST /v1/admin/proxies/import               — bulk import text
GET  /v1/admin/proxies                       — list proxies with filters
POST /v1/admin/proxies/{id}/test             — test single proxy
POST /v1/admin/proxies/test-all              — test all proxies
POST /v1/admin/proxies/{id}/bind/{account_id} — bind proxy to account
POST /v1/admin/proxies/{id}/unbind           — unbind proxy
DELETE /v1/admin/proxies/{id}                — remove proxy
```

- [ ] **Step 4: Add operations log endpoint**

```
GET /v1/admin/operations-log                 — paginated log with filters
```

- [ ] **Step 5: Add admin dashboard stats endpoint**

```
GET /v1/admin/onboarding/stats               — account counts by status, proxy counts, recent ops
```

- [ ] **Step 6: Compile check and commit**

```bash
git add ops_api.py
git commit -m "feat(sprint-17): add admin onboarding + proxy + log API endpoints"
```

---

## Task 5: Frontend — Admin Mode Toggle + Auth

**Files:**
- Modify: `frontend/src/auth.tsx` — add `is_platform_admin` to AuthBundle
- Create: `frontend/src/components/admin/AdminModeToggle.tsx`
- Modify: `frontend/src/layout/AppShell.tsx` — add toggle + admin nav groups

- [ ] **Step 1: Update auth.tsx**

Add `is_platform_admin: boolean` to AuthBundle type.
Parse from JWT response in login/refresh flows.

- [ ] **Step 2: Create AdminModeToggle component**

```tsx
// Toggle button: ADMIN (red) / CLIENT (green)
// Stores mode in localStorage
// Only visible when is_platform_admin === true
```

- [ ] **Step 3: Update AppShell.tsx**

Add admin nav groups (only shown in ADMIN mode):
- ONBOARDING: Upload Accounts, Proxy Manager, Bind Proxy↔Account
- OPERATIONS: Connect & Verify, Security Hardening, Warmup Control, SpamBot Appeal
- MONITORING: Live Dashboard, Operations Log, Health Overview

- [ ] **Step 4: tsc check**

Run: `cd frontend && npx tsc --noEmit`
Expected: Clean

- [ ] **Step 5: Commit**

```bash
git add frontend/src/auth.tsx frontend/src/components/admin/ frontend/src/layout/AppShell.tsx
git commit -m "feat(sprint-17): add admin/client mode toggle and admin nav groups"
```

---

## Task 6: Frontend — Admin Dashboard Page

**Files:**
- Create: `frontend/src/pages/admin/AdminDashboardPage.tsx`

- [ ] **Step 1: Build AdminDashboardPage**

Stats cards at top:
- Accounts: N uploaded / N verified / N hardened / N ready / N frozen / N dead
- Proxies: N alive / N dead / N free / N bound
- Recent operations (last 20)

Quick action buttons:
- Upload Account, Import Proxies, Test All Proxies

Account cards below with per-account quick actions:
- Verify, Harden, Start Warmup, Appeal

- [ ] **Step 2: tsc check + commit**

---

## Task 7: Frontend — Account Onboarding Wizard

**Files:**
- Create: `frontend/src/pages/admin/AccountOnboardingWizard.tsx`

- [ ] **Step 1: Build 6-step wizard**

Step 1 — Upload: drag-drop zone for tdata ZIP / .session+.json / bulk ZIP
Step 2 — Proxy: manual input (host:port:user:pass) OR auto-assign from pool
Step 3 — Connect & Verify: button → shows phone, name, DC, authorized status
Step 4 — Security Hardening: checkboxes (all checked by default), "Harden" button with progress
Step 5 — Warmup: mode selector (conservative/moderate/aggressive), schedule picker
Step 6 — Ready: success summary, link to dashboard

Each step calls backend API and shows real-time progress.

- [ ] **Step 2: tsc check + commit**

---

## Task 8: Frontend — Proxy Manager Page

**Files:**
- Create: `frontend/src/pages/admin/AdminProxyManagerPage.tsx`

- [ ] **Step 1: Build proxy manager**

- Import area: textarea for bulk paste (host:port:user:pass per line)
- Proxy list: table with host, port, type, status, bound account, last tested
- Per-proxy actions: Test, Bind, Unbind, Delete
- Bulk actions: Test All, Delete Dead

- [ ] **Step 2: tsc check + commit**

---

## Task 9: Frontend — Operations Log Page + Routes

**Files:**
- Create: `frontend/src/pages/admin/AdminOperationsLogPage.tsx`
- Modify: `frontend/src/App.tsx` — add admin routes
- Modify: `frontend/src/api.ts` — add admin API functions

- [ ] **Step 1: Build operations log page**

- Scrollable table: timestamp, account, module, action, status, detail
- Filters: by module, by account, by status
- Auto-refresh every 10 seconds

- [ ] **Step 2: Add routes to App.tsx**

```tsx
<Route path="/admin/dashboard" element={<AdminDashboardPage />} />
<Route path="/admin/onboarding" element={<AccountOnboardingWizard />} />
<Route path="/admin/proxies" element={<AdminProxyManagerPage />} />
<Route path="/admin/operations-log" element={<AdminOperationsLogPage />} />
```

- [ ] **Step 3: Add admin API functions to api.ts**

```typescript
export const adminApi = {
  getStats: () => get('/v1/admin/onboarding/stats'),
  uploadTdata: (file: File) => postMultipart('/v1/admin/onboarding/upload-tdata', file),
  uploadSession: (session: File, json: File) => postMultipart('/v1/admin/onboarding/upload-session', {session, json}),
  listAccounts: (params?) => get('/v1/admin/onboarding/accounts', params),
  verifyAccount: (id: number) => post(`/v1/admin/onboarding/accounts/${id}/verify`),
  hardenAccount: (id: number) => post(`/v1/admin/onboarding/accounts/${id}/harden`),
  importProxies: (text: string) => post('/v1/admin/proxies/import', {lines: text.split('\n')}),
  listProxies: (params?) => get('/v1/admin/proxies', params),
  testProxy: (id: number) => post(`/v1/admin/proxies/${id}/test`),
  bindProxy: (proxyId: number, accountId: number) => post(`/v1/admin/proxies/${proxyId}/bind/${accountId}`),
  getOperationsLog: (params?) => get('/v1/admin/operations-log', params),
}
```

- [ ] **Step 4: tsc check + commit**

---

## Task 10: Tests

**Files:**
- Create: `tests/test_admin_onboarding.py`

- [ ] **Step 1: Write tests**

```python
# test_platform_admin_flag — verify is_platform_admin column and middleware
# test_upload_session — upload .session+.json, verify DB record created
# test_proxy_import — import 3 proxies, verify DB records
# test_proxy_test — mock curl test, verify status update
# test_proxy_bind — bind proxy to account, verify 1:1 uniqueness
# test_operations_log — verify operations are logged
# test_non_admin_rejected — verify 403 for non-admin users
```

- [ ] **Step 2: Run tests**

Run: `./.venv/bin/pytest tests/test_admin_onboarding.py -v`
Expected: All pass

- [ ] **Step 3: Run all existing tests**

Run: `./.venv/bin/pytest tests/ -q`
Expected: No regressions

- [ ] **Step 4: Commit**

```bash
git add tests/test_admin_onboarding.py
git commit -m "test(sprint-17): add admin onboarding tests"
```

---

## Task 11: Set Platform Admin Flag + Final Verification

- [ ] **Step 1: Set yourself as platform admin**

```sql
UPDATE auth_users SET is_platform_admin = true WHERE email = '<your-email>';
```

- [ ] **Step 2: Build frontend**

Run: `cd frontend && npm run build`

- [ ] **Step 3: Full compile check**

Run: `./.venv/bin/python -c "import py_compile; py_compile.compile('ops_api.py', doraise=True)"`
Run: `cd frontend && npx tsc --noEmit`

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "feat(sprint-17): admin panel foundation complete — upload/verify/harden/proxy/log"
```

---

## Sprint 17 Definition of Done

- [ ] `is_platform_admin` column on `auth_users`
- [ ] Admin/Client mode toggle in sidebar
- [ ] Account upload via UI (tdata/session+json/ZIP)
- [ ] Proxy import, test, bind via UI
- [ ] Account verify (connect + is_authorized) via UI
- [ ] Account harden (kill sessions, 2FA, privacy) via UI
- [ ] Operations log page
- [ ] Admin dashboard with stats
- [ ] All tests pass
- [ ] tsc clean
- [ ] No existing test regressions
