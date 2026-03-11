# Sprint 5: Stabilize & Ship

## Summary

Combine security hardening and VPS deployment into one sprint. Fix 8 critical vulnerabilities, 2 performance issues, and deploy Sprints 2-4 to VPS as a stable baseline.

## Security Hardening

### 1. JWT/Token Empty Secret Validation
- **File**: `config.py`, `ops_api.py`
- **Fix**: Startup check — raise `RuntimeError` if `JWT_ACCESS_SECRET`, `JWT_REFRESH_SECRET`, or `OPS_API_TOKEN` are empty in non-test environments.

### 2. Logout RLS Context
- **File**: `core/web_auth.py:623-640`, `ops_api.py:1300`
- **Fix**: Call `apply_session_rls_context(session, tenant_id, user_id)` before querying `RefreshToken` in `logout_web_session`.

### 3. RLS Hardening Migration
- **File**: New `alembic/versions/20260311_13_rls_hardening.py`
- **Fix**: Enable RLS and create isolation policies for `refresh_tokens`, `accounts`, `proxies`. Dialect-guarded for SQLite.

### 4. CORS Restriction
- **File**: `ops_api.py:1083-1084`
- **Fix**: Replace `allow_methods=["*"]` with `["GET","POST","PUT","DELETE","OPTIONS"]`, `allow_headers=["*"]` with `["Authorization","Content-Type"]`.

### 5. /v1/accounts Tenant Filter
- **File**: `ops_api.py:1438-1457`
- **Fix**: Add optional `tenant_id` query parameter; filter when provided.

### 6. Job Cancel Defense-in-Depth
- **File**: `ops_api.py:1823-1827`
- **Fix**: Add `AppJob.tenant_id == tenant_context.tenant_id` to re-fetch WHERE clause.

### 7. Path Traversal Guard
- **File**: `ops_api.py:1909-1918`
- **Fix**: Add `asset_path.resolve().is_relative_to(FRONTEND_DIST_DIR)` check before serving.

### 8. Frontend API Contract Fix
- **File**: `frontend/src/api.ts`
- **Fix**: Align `channelDbApi.get` response shape and `channelDbApi.importChannels` payload shape with backend.

## Performance

### 1. SQL Pagination
- **Files**: `ops_api.py` (accounts, thread, drafts endpoints)
- **Fix**: Push `LIMIT`/`OFFSET` into SQL queries instead of in-memory slicing.

### 2. N+1 Fix
- **File**: `ops_api.py:2516-2538`
- **Fix**: Replace per-row COUNT queries with a single GROUP BY join.

## Deploy

### Commit Strategy
1. Security fixes as one commit
2. Performance fixes as one commit
3. Deploy to VPS: `docker compose build ops_api && docker compose up -d`
4. Run `alembic upgrade head`
5. Smoke test all critical paths

### Acceptance Criteria
- All 8 security issues resolved
- VPS running latest code with all 13 migrations applied
- Landing, auth, assistant, creative flows work end-to-end
- Cross-tenant isolation verified
