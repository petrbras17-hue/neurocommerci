from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator

import jwt
import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from storage.models import Account, Tenant, Workspace
from storage.sqlite_db import apply_session_rls_context, async_session, dispose_engine, init_db


@dataclass
class TenantContext:
    user_id: int
    tenant_id: int
    workspace_id: int | None
    role: str
    token_type: str


def _bearer_token(request: Request) -> str:
    header = request.headers.get("Authorization", "").strip()
    if not header.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing_bearer_token")
    return header.split(" ", 1)[1].strip()


def _decode_jwt(token: str) -> TenantContext:
    try:
        payload = jwt.decode(
            token,
            settings.JWT_ACCESS_SECRET,
            algorithms=[settings.JWT_ALGORITHM],
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_token") from exc

    token_type = str(payload.get("type") or "")
    if token_type != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_token_type")

    try:
        user_id = int(payload["sub"])
        tenant_id = int(payload["tenant_id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_token_payload") from exc

    workspace_raw = payload.get("workspace_id")
    workspace_id = int(workspace_raw) if workspace_raw is not None else None
    return TenantContext(
        user_id=user_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        role=str(payload.get("role") or "member"),
        token_type=token_type,
    )


async def require_internal_token(request: Request) -> None:
    token = _bearer_token(request)
    if token != settings.OPS_API_TOKEN:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_internal_token")


async def get_tenant_context(request: Request) -> TenantContext:
    token = _bearer_token(request)
    tenant_context = _decode_jwt(token)

    async with async_session() as session:
        async with session.begin():
            await apply_session_rls_context(
                session,
                tenant_id=tenant_context.tenant_id,
                user_id=tenant_context.user_id,
            )
            tenant = await session.get(Tenant, tenant_context.tenant_id)
            if tenant is None:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="tenant_not_found")
            if tenant.status == "suspended":
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="tenant_suspended")

    request.state.tenant_context = tenant_context
    return tenant_context


async def tenant_session(
    tenant_context: TenantContext = Depends(get_tenant_context),
) -> AsyncIterator[AsyncSession]:
    async with async_session() as session:
        async with session.begin():
            await apply_session_rls_context(
                session,
                tenant_id=tenant_context.tenant_id,
                user_id=tenant_context.user_id,
            )
            yield session


@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_db()
    try:
        yield
    finally:
        await dispose_engine()


app = FastAPI(title="NEURO COMMENTING Ops API", lifespan=lifespan)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/v1/accounts")
async def list_accounts(_: None = Depends(require_internal_token)) -> dict[str, object]:
    async with async_session() as session:
        rows = (
            await session.execute(
                select(Account).order_by(Account.id)
            )
        ).scalars().all()

    items = [
        {
            "id": row.id,
            "phone": row.phone,
            "status": row.status,
            "health_status": row.health_status,
            "lifecycle_stage": row.lifecycle_stage,
        }
        for row in rows
    ]
    return {"items": items, "total": len(items)}


@app.get("/v1/workspaces")
async def list_workspaces(
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, object]:
    rows = (
        await session.execute(select(Workspace).order_by(Workspace.id))
    ).scalars().all()

    items = [
        {
            "id": row.id,
            "name": row.name,
            "settings": row.settings or {},
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]
    return {"items": items, "total": len(items)}


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8081)
