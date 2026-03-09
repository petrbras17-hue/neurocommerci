"""Helpers for updating the project .env file."""

from __future__ import annotations

from pathlib import Path

from config import settings


def update_env_file(key: str, value: str, *, env_path: Path | None = None) -> Path:
    path = env_path or Path(settings.model_config["env_file"])
    if not path.exists():
        raise FileNotFoundError(f".env not found: {path}")

    lines = path.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    found = False
    for line in lines:
        if line.strip().startswith(f"{key}="):
            out.append(f"{key}={value}")
            found = True
        else:
            out.append(line)
    if not found:
        out.append(f"{key}={value}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return path
