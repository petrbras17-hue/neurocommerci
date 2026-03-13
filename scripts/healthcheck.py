#!/usr/bin/env python3
"""Container healthcheck for application services."""

from __future__ import annotations

import argparse
import os
import socket
import sys
from urllib.parse import urlparse


ROLE_PROCESS = {
    "bot": "main.py",
    "worker": "worker.py",
    "packager": "packager_worker.py",
    "parser": "parser_service.py",
    "recovery": "recovery_worker.py",
    "scheduler": "scheduler_service.py",
}


def _check_pid1(role: str) -> tuple[bool, str]:
    expected = ROLE_PROCESS.get(role)
    if not expected:
        return True, ""

    try:
        with open("/proc/1/cmdline", "rb") as f:
            cmdline = f.read().decode("utf-8", "ignore")
    except Exception as exc:  # pragma: no cover - container runtime edge
        return False, f"can't read /proc/1/cmdline: {exc}"

    if expected not in cmdline:
        return False, f"unexpected pid1 cmdline for role={role}: {cmdline!r}"
    return True, ""


def _check_tcp(url_value: str, default_port: int) -> tuple[bool, str]:
    if not url_value:
        return True, ""

    parsed = urlparse(url_value)
    host = parsed.hostname
    port = parsed.port or default_port
    if not host:
        return False, f"invalid url (no host): {url_value!r}"

    try:
        with socket.create_connection((host, port), timeout=2):
            return True, ""
    except OSError as exc:
        return False, f"tcp connect failed {host}:{port}: {exc}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Healthcheck for NEURO services")
    parser.add_argument("--role", choices=sorted(ROLE_PROCESS), required=True)
    args = parser.parse_args()

    checks = [
        _check_pid1(args.role),
        _check_tcp(os.environ.get("REDIS_URL", ""), default_port=6379),
        _check_tcp(os.environ.get("DATABASE_URL", ""), default_port=5432),
    ]
    failures = [msg for ok, msg in checks if not ok]

    if failures:
        print("; ".join(failures), file=sys.stderr)
        return 1

    print("ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
