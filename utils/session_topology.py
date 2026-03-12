"""Helpers for canonical multi-tenant session storage layout."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import shutil
from typing import Iterable


LEGACY_IGNORED_DIRS = {"_banned", "__pycache__", "_quarantine"}


@dataclass(frozen=True)
class SessionAsset:
    """Discovered session asset on disk."""

    phone: str
    session_path: Path
    metadata_path: Path | None
    user_id: int | None
    source_kind: str  # canonical, flat, legacy_nested
    source_dir: str

    @property
    def session_file(self) -> str:
        return self.session_path.name


@dataclass(frozen=True)
class SessionDiscovery:
    """Result of scanning session files on disk."""

    assets: dict[str, SessionAsset]
    duplicates: dict[str, list[str]]


@dataclass(frozen=True)
class SessionTopologyEntry:
    phone: str
    user_id: int | None
    canonical_session: Path | None
    canonical_metadata: Path | None
    flat_session: Path | None
    flat_metadata: Path | None
    legacy_sessions: tuple[Path, ...]
    legacy_metadata: tuple[Path, ...]
    legacy_dirs: tuple[str, ...]

    @property
    def canonical_complete(self) -> bool:
        return self.canonical_session is not None and self.canonical_metadata is not None

    @property
    def flat_copy(self) -> bool:
        return self.flat_session is not None or self.flat_metadata is not None

    @property
    def legacy_copy(self) -> bool:
        return bool(self.legacy_sessions or self.legacy_metadata)

    @property
    def duplicate_copy(self) -> bool:
        return self.canonical_complete and (self.flat_copy or self.legacy_copy)

    @property
    def safe_to_quarantine(self) -> bool:
        return self.duplicate_copy

    @property
    def status_kind(self) -> str:
        if self.canonical_complete and not self.flat_copy and not self.legacy_copy:
            return "canonical"
        if self.duplicate_copy:
            return "duplicate"
        if self.canonical_session or self.canonical_metadata:
            return "canonical_incomplete"
        if self.flat_copy and not self.legacy_copy:
            return "root_only"
        if self.legacy_copy and not self.flat_copy:
            return "legacy_only"
        if self.flat_copy or self.legacy_copy:
            return "noncanonical_only"
        return "missing"


def normalize_phone(raw_phone: str) -> str:
    digits = "".join(ch for ch in str(raw_phone) if ch.isdigit())
    return f"+{digits}" if digits else ""


def session_stem(phone: str) -> str:
    return "".join(ch for ch in str(phone) if ch.isdigit())


def canonical_session_dir(base_dir: Path, user_id: int, *, create: bool = False) -> Path:
    path = base_dir / str(user_id)
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def canonical_session_paths(base_dir: Path, user_id: int, phone: str) -> tuple[Path, Path]:
    stem = session_stem(phone)
    session_dir = canonical_session_dir(base_dir, user_id)
    return session_dir / f"{stem}.session", session_dir / f"{stem}.json"


def canonical_session_exists(base_dir: Path, user_id: int | None, phone: str) -> bool:
    if user_id is None:
        return False
    session_path, _ = canonical_session_paths(base_dir, user_id, phone)
    return session_path.exists()


def canonical_metadata_exists(base_dir: Path, user_id: int | None, phone: str) -> bool:
    if user_id is None:
        return False
    _, metadata_path = canonical_session_paths(base_dir, user_id, phone)
    return metadata_path.exists()


def discover_session_assets(
    base_dir: Path,
    *,
    known_user_ids: Iterable[int] | None = None,
) -> SessionDiscovery:
    """Discover flat, canonical and legacy-nested session files.

    Priority:
    - canonical tenant dir
    - flat root dir
    - legacy nested dir
    """

    known_users = {int(uid) for uid in (known_user_ids or []) if uid is not None}
    discovered: dict[str, SessionAsset] = {}
    duplicates: dict[str, list[str]] = {}

    def _priority(source_kind: str) -> int:
        return {"canonical": 30, "flat": 20, "legacy_nested": 10}.get(source_kind, 0)

    def _register(asset: SessionAsset) -> None:
        current = discovered.get(asset.phone)
        if current is None:
            discovered[asset.phone] = asset
            return
        duplicates.setdefault(asset.phone, []).append(str(asset.session_path))
        if _priority(asset.source_kind) > _priority(current.source_kind):
            duplicates[asset.phone].append(str(current.session_path))
            discovered[asset.phone] = asset

    def _iter_sessions(path: Path):
        if not path.exists() or not path.is_dir():
            return
        for session_path in sorted(path.glob("*.session")):
            yield session_path

    for user_id in sorted(known_users):
        session_dir = canonical_session_dir(base_dir, user_id)
        for session_path in _iter_sessions(session_dir):
            phone = normalize_phone(session_path.stem)
            if not phone:
                continue
            metadata = session_path.with_suffix(".json")
            _register(
                SessionAsset(
                    phone=phone,
                    session_path=session_path,
                    metadata_path=metadata if metadata.exists() else None,
                    user_id=user_id,
                    source_kind="canonical",
                    source_dir=str(session_dir.name),
                )
            )

    for session_path in _iter_sessions(base_dir):
        phone = normalize_phone(session_path.stem)
        if not phone:
            continue
        metadata = session_path.with_suffix(".json")
        _register(
            SessionAsset(
                phone=phone,
                session_path=session_path,
                metadata_path=metadata if metadata.exists() else None,
                user_id=None,
                source_kind="flat",
                source_dir=".",
            )
        )

    for child in sorted(base_dir.iterdir() if base_dir.exists() else []):
        if not child.is_dir():
            continue
        if child.name in LEGACY_IGNORED_DIRS:
            continue
        if child.name.isdigit() and int(child.name) in known_users:
            continue
        for session_path in _iter_sessions(child):
            phone = normalize_phone(session_path.stem)
            if not phone:
                continue
            metadata = session_path.with_suffix(".json")
            _register(
                SessionAsset(
                    phone=phone,
                    session_path=session_path,
                    metadata_path=metadata if metadata.exists() else None,
                    user_id=None,
                    source_kind="legacy_nested",
                    source_dir=child.name,
                )
            )

    return SessionDiscovery(assets=discovered, duplicates=duplicates)


def _iter_files(path: Path, pattern: str):
    if not path.exists() or not path.is_dir():
        return
    for file_path in sorted(path.glob(pattern)):
        yield file_path


def audit_session_topology(
    base_dir: Path,
    *,
    known_user_ids: Iterable[int] | None = None,
) -> dict:
    """Audit canonical and non-canonical session/json copies on disk."""

    known_users = {int(uid) for uid in (known_user_ids or []) if uid is not None}
    entries: dict[str, dict] = {}

    def _entry(phone: str) -> dict:
        phone = normalize_phone(phone)
        return entries.setdefault(
            phone,
            {
                "phone": phone,
                "user_id": None,
                "canonical_session": None,
                "canonical_metadata": None,
                "flat_session": None,
                "flat_metadata": None,
                "legacy_sessions": [],
                "legacy_metadata": [],
                "legacy_dirs": [],
            },
        )

    for user_id in sorted(known_users):
        session_dir = canonical_session_dir(base_dir, user_id)
        for session_path in _iter_files(session_dir, "*.session"):
            phone = normalize_phone(session_path.stem)
            if not phone:
                continue
            item = _entry(phone)
            item["user_id"] = user_id
            item["canonical_session"] = session_path
        for json_path in _iter_files(session_dir, "*.json"):
            phone = normalize_phone(json_path.stem)
            if not phone:
                continue
            item = _entry(phone)
            item["user_id"] = user_id
            item["canonical_metadata"] = json_path

    for session_path in _iter_files(base_dir, "*.session"):
        phone = normalize_phone(session_path.stem)
        if not phone:
            continue
        _entry(phone)["flat_session"] = session_path

    for json_path in _iter_files(base_dir, "*.json"):
        phone = normalize_phone(json_path.stem)
        if not phone:
            continue
        _entry(phone)["flat_metadata"] = json_path

    for child in sorted(base_dir.iterdir() if base_dir.exists() else []):
        if not child.is_dir():
            continue
        if child.name in LEGACY_IGNORED_DIRS or child.name == "_quarantine":
            continue
        if child.name.isdigit() and int(child.name) in known_users:
            continue
        for session_path in _iter_files(child, "*.session"):
            phone = normalize_phone(session_path.stem)
            if not phone:
                continue
            item = _entry(phone)
            item["legacy_sessions"].append(session_path)
            item["legacy_dirs"].append(child.name)
        for json_path in _iter_files(child, "*.json"):
            phone = normalize_phone(json_path.stem)
            if not phone:
                continue
            item = _entry(phone)
            item["legacy_metadata"].append(json_path)
            item["legacy_dirs"].append(child.name)

    topology_items = [
        SessionTopologyEntry(
            phone=item["phone"],
            user_id=item["user_id"],
            canonical_session=item["canonical_session"],
            canonical_metadata=item["canonical_metadata"],
            flat_session=item["flat_session"],
            flat_metadata=item["flat_metadata"],
            legacy_sessions=tuple(item["legacy_sessions"]),
            legacy_metadata=tuple(item["legacy_metadata"]),
            legacy_dirs=tuple(sorted(set(item["legacy_dirs"]))),
        )
        for item in entries.values()
        if item["phone"]
    ]
    topology_items.sort(key=lambda item: item.phone)

    status_counts: dict[str, int] = {}
    for item in topology_items:
        status_counts[item.status_kind] = status_counts.get(item.status_kind, 0) + 1

    duplicate_phones = [item.phone for item in topology_items if item.duplicate_copy]
    flat_phones = [item.phone for item in topology_items if item.flat_copy]
    legacy_phones = [item.phone for item in topology_items if item.legacy_copy]

    return {
        "items": [
            {
                "phone": item.phone,
                "user_id": item.user_id,
                "status_kind": item.status_kind,
                "canonical_complete": item.canonical_complete,
                "safe_to_quarantine": item.safe_to_quarantine,
                "canonical_session": str(item.canonical_session) if item.canonical_session else None,
                "canonical_metadata": str(item.canonical_metadata) if item.canonical_metadata else None,
                "flat_session": str(item.flat_session) if item.flat_session else None,
                "flat_metadata": str(item.flat_metadata) if item.flat_metadata else None,
                "legacy_sessions": [str(path) for path in item.legacy_sessions],
                "legacy_metadata": [str(path) for path in item.legacy_metadata],
                "legacy_dirs": list(item.legacy_dirs),
            }
            for item in topology_items
        ],
        "summary": {
            "phones_total": len(topology_items),
            "status_counts": dict(sorted(status_counts.items())),
            "canonical_complete": sum(1 for item in topology_items if item.canonical_complete),
            "with_root_copies": len(flat_phones),
            "with_legacy_copies": len(legacy_phones),
            "duplicate_copy_phones": len(duplicate_phones),
            "duplicate_phones": duplicate_phones,
            "safe_to_quarantine": sum(1 for item in topology_items if item.safe_to_quarantine),
        },
    }


def quarantine_noncanonical_assets(
    base_dir: Path,
    *,
    known_user_ids: Iterable[int] | None = None,
    phones: Iterable[str] | None = None,
    dry_run: bool = False,
) -> dict:
    """Move root/legacy copies into a quarantine folder when canonical copies exist."""

    audit = audit_session_topology(base_dir, known_user_ids=known_user_ids)
    requested = {normalize_phone(phone) for phone in (phones or []) if normalize_phone(phone)}
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    quarantine_root = base_dir / "_quarantine" / timestamp
    moved: list[str] = []
    moved_phones: set[str] = set()
    skipped: list[dict[str, str]] = []

    for item in audit["items"]:
        phone = normalize_phone(item.get("phone") or "")
        if requested and phone not in requested:
            continue
        if not bool(item.get("safe_to_quarantine")):
            skipped.append({"phone": phone, "reason": "not_safe_to_quarantine"})
            continue

        file_moves: list[tuple[Path, Path]] = []
        flat_session = item.get("flat_session")
        flat_metadata = item.get("flat_metadata")
        if flat_session:
            source = Path(flat_session)
            file_moves.append((source, quarantine_root / "flat" / source.name))
        if flat_metadata:
            source = Path(flat_metadata)
            file_moves.append((source, quarantine_root / "flat" / source.name))
        for legacy_file in item.get("legacy_sessions") or []:
            source = Path(legacy_file)
            file_moves.append((source, quarantine_root / "legacy" / source.parent.name / source.name))
        for legacy_file in item.get("legacy_metadata") or []:
            source = Path(legacy_file)
            file_moves.append((source, quarantine_root / "legacy" / source.parent.name / source.name))

        if not file_moves:
            skipped.append({"phone": phone, "reason": "no_noncanonical_files"})
            continue

        for source, target in file_moves:
            if not source.exists():
                continue
            # Path traversal protection: ensure source is within base_dir
            try:
                source.resolve().relative_to(base_dir.resolve())
            except ValueError:
                skipped.append({"phone": phone, "reason": f"path_traversal_blocked:{source}"})
                continue
            moved_phones.add(phone)
            moved.append(str(source))
            if dry_run:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(target))

    return {
        "ok": True,
        "dry_run": bool(dry_run),
        "quarantine_dir": str(quarantine_root),
        "moved_files": len(moved),
        "moved_phones": sorted(moved_phones),
        "files": moved,
        "skipped": skipped,
        "skipped_count": len(skipped),
    }
