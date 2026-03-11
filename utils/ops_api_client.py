"""Minimal async client for the internal ops API."""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from typing import Any

from config import settings


class OpsApiError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None, payload: dict[str, Any] | None = None):
        super().__init__(message)
        self.status = status
        self.payload = payload or {}


def _build_request(path: str, *, method: str, payload: dict[str, Any] | None = None) -> urllib.request.Request:
    base_url = str(settings.OPS_API_URL or "").rstrip("/")
    if not base_url:
        raise OpsApiError("ops_api_not_configured")
    url = f"{base_url}{path}"
    data = None
    headers = {"Content-Type": "application/json"}
    if settings.OPS_API_TOKEN:
        headers["Authorization"] = f"Bearer {settings.OPS_API_TOKEN}"
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return urllib.request.Request(url, data=data, headers=headers, method=method.upper())


def _read_response(req: urllib.request.Request, *, timeout: float = 10.0) -> dict[str, Any]:
    try:
        raw = urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8")
        return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "ignore")
        payload: dict[str, Any]
        try:
            payload = json.loads(body) if body else {}
        except Exception:
            payload = {"body": body}
        raise OpsApiError(
            str(payload.get("error") or f"http_{exc.code}"),
            status=exc.code,
            payload=payload,
        ) from exc
    except urllib.error.URLError as exc:
        raise OpsApiError(f"ops_api_unreachable: {exc.reason}") from exc


async def ops_api_get(path: str, *, timeout: float = 10.0) -> dict[str, Any]:
    req = _build_request(path, method="GET")
    return await asyncio.to_thread(_read_response, req, timeout=timeout)


async def ops_api_post(
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout: float = 10.0,
) -> dict[str, Any]:
    req = _build_request(path, method="POST", payload=payload)
    return await asyncio.to_thread(_read_response, req, timeout=timeout)
