"""
DeviceIdentity — unique device fingerprint generator and manager.

Generates realistic, unique device fingerprints for Telegram accounts.
Each account gets a permanent fingerprint stored in its metadata JSON.

Every fingerprint is validated for:
- API ID family consistency (Android API ID -> Android device)
- SDK level realism (device shipped with that SDK)
- App version currency (not suspiciously outdated)
- Collision avoidance (no two accounts share the same fingerprint)

Usage:
    mgr = DeviceIdentityManager()
    mgr.load_used_fingerprints(sessions_dir)

    # Generate a new unique identity
    identity = mgr.generate(api_id=21724, lang_profile="kz")

    # Validate before connecting
    warnings = mgr.validate(identity)

    # Pass to TelegramClient
    client = TelegramClient(session, **identity.to_telethon_kwargs())
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from utils.logger import log


# ---------------------------------------------------------------------------
# DeviceIdentity dataclass
# ---------------------------------------------------------------------------


@dataclass
class DeviceIdentity:
    """Complete device identity for one Telegram account."""

    api_id: int
    api_hash: str
    device_model: str
    system_version: str
    app_version: str
    lang_code: str = "ru"
    system_lang_code: str = "ru-ru"
    lang_pack: str = "ru"

    def fingerprint_key(self) -> str:
        """Unique key for collision detection."""
        return f"{self.device_model}|{self.system_version}|{self.app_version}"

    def to_telethon_kwargs(self) -> dict:
        """Return kwargs for TelegramClient constructor."""
        return {
            "api_id": self.api_id,
            "api_hash": self.api_hash,
            "device_model": self.device_model,
            "system_version": self.system_version,
            "app_version": self.app_version,
            "lang_code": self.lang_code,
            "system_lang_code": self.system_lang_code,
        }

    def to_metadata_dict(self) -> dict:
        """Return dict matching the account metadata JSON schema."""
        return {
            "app_id": self.api_id,
            "app_hash": self.api_hash,
            "device": self.device_model,
            "sdk": self.system_version,
            "app_version": self.app_version,
            "lang_pack": self.lang_code,
            "system_lang_pack": self.system_lang_code,
        }

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "DeviceIdentity":
        valid_fields = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in valid_fields})

    @classmethod
    def from_metadata(cls, meta: dict) -> "DeviceIdentity":
        """Create from account metadata JSON (legacy format)."""
        return cls(
            api_id=meta.get("app_id", 0),
            api_hash=meta.get("app_hash", ""),
            device_model=meta.get("device", meta.get("device_model", "")),
            system_version=meta.get("sdk", meta.get("system_version", "")),
            app_version=meta.get("app_version", ""),
            lang_code=meta.get("lang_pack", "ru"),
            system_lang_code=meta.get("system_lang_pack", "ru-ru"),
            lang_pack=meta.get("lang_pack", "ru"),
        )


# ---------------------------------------------------------------------------
# Device pools — real devices with correct SDK levels
# ---------------------------------------------------------------------------

ANDROID_DEVICES: list[dict] = [
    # Samsung Galaxy — Flagship (2022-2026)
    {"device_model": "Samsung Galaxy S25 Ultra", "system_version": "SDK 35"},
    {"device_model": "Samsung Galaxy S25+", "system_version": "SDK 35"},
    {"device_model": "Samsung Galaxy S25", "system_version": "SDK 35"},
    {"device_model": "Samsung Galaxy S24 Ultra", "system_version": "SDK 34"},
    {"device_model": "Samsung Galaxy S24+", "system_version": "SDK 34"},
    {"device_model": "Samsung Galaxy S24", "system_version": "SDK 34"},
    {"device_model": "Samsung Galaxy S23 Ultra", "system_version": "SDK 33"},
    {"device_model": "Samsung Galaxy S23+", "system_version": "SDK 33"},
    {"device_model": "Samsung Galaxy S23", "system_version": "SDK 33"},
    {"device_model": "Samsung Galaxy S22 Ultra", "system_version": "SDK 31"},
    {"device_model": "Samsung Galaxy S22+", "system_version": "SDK 31"},
    {"device_model": "Samsung Galaxy S22", "system_version": "SDK 31"},
    # Samsung Galaxy — Mid-range
    {"device_model": "Samsung Galaxy A55", "system_version": "SDK 34"},
    {"device_model": "Samsung Galaxy A54", "system_version": "SDK 33"},
    {"device_model": "Samsung Galaxy A34", "system_version": "SDK 33"},
    {"device_model": "Samsung Galaxy A15", "system_version": "SDK 34"},
    # Samsung Galaxy — Foldable
    {"device_model": "Samsung Galaxy Z Fold5", "system_version": "SDK 33"},
    {"device_model": "Samsung Galaxy Z Flip5", "system_version": "SDK 33"},
    # Google Pixel (2022-2025)
    {"device_model": "Google Pixel 9 Pro XL", "system_version": "SDK 35"},
    {"device_model": "Google Pixel 9 Pro", "system_version": "SDK 35"},
    {"device_model": "Google Pixel 9", "system_version": "SDK 35"},
    {"device_model": "Google Pixel 8 Pro", "system_version": "SDK 34"},
    {"device_model": "Google Pixel 8", "system_version": "SDK 34"},
    {"device_model": "Google Pixel 8a", "system_version": "SDK 34"},
    {"device_model": "Google Pixel 7 Pro", "system_version": "SDK 33"},
    {"device_model": "Google Pixel 7", "system_version": "SDK 33"},
    {"device_model": "Google Pixel 7a", "system_version": "SDK 33"},
    # Xiaomi — Flagship
    {"device_model": "Xiaomi 15 Pro", "system_version": "SDK 35"},
    {"device_model": "Xiaomi 15", "system_version": "SDK 35"},
    {"device_model": "Xiaomi 14 Ultra", "system_version": "SDK 34"},
    {"device_model": "Xiaomi 14 Pro", "system_version": "SDK 34"},
    {"device_model": "Xiaomi 14", "system_version": "SDK 34"},
    {"device_model": "Xiaomi 13 Pro", "system_version": "SDK 33"},
    {"device_model": "Xiaomi 13", "system_version": "SDK 33"},
    {"device_model": "Xiaomi 13T Pro", "system_version": "SDK 33"},
    {"device_model": "Xiaomi 13T", "system_version": "SDK 33"},
    # Redmi / POCO
    {"device_model": "Redmi Note 13 Pro+", "system_version": "SDK 33"},
    {"device_model": "Redmi Note 13 Pro", "system_version": "SDK 33"},
    {"device_model": "Redmi Note 13", "system_version": "SDK 33"},
    {"device_model": "Redmi Note 12 Pro+", "system_version": "SDK 33"},
    {"device_model": "POCO F6 Pro", "system_version": "SDK 34"},
    {"device_model": "POCO F6", "system_version": "SDK 34"},
    {"device_model": "POCO X6 Pro", "system_version": "SDK 33"},
    # OnePlus
    {"device_model": "OnePlus 13", "system_version": "SDK 35"},
    {"device_model": "OnePlus 12", "system_version": "SDK 34"},
    {"device_model": "OnePlus 12R", "system_version": "SDK 34"},
    {"device_model": "OnePlus 11", "system_version": "SDK 33"},
    {"device_model": "OnePlus Nord 4", "system_version": "SDK 34"},
    {"device_model": "OnePlus Nord CE 4", "system_version": "SDK 34"},
    # Realme / vivo / OPPO — popular in CIS
    {"device_model": "realme 12 Pro+", "system_version": "SDK 34"},
    {"device_model": "realme GT 5 Pro", "system_version": "SDK 34"},
    {"device_model": "vivo V30 Pro", "system_version": "SDK 34"},
    {"device_model": "vivo V30", "system_version": "SDK 34"},
    {"device_model": "OPPO Reno 11 Pro", "system_version": "SDK 34"},
    # --- NEW DEVICES BELOW (2026-03 expansion) ---
    # Samsung Galaxy S24 series (different SDK levels for updated devices)
    {"device_model": "Samsung Galaxy S24 Ultra 5G", "system_version": "SDK 35"},
    {"device_model": "Samsung Galaxy S24+ 5G", "system_version": "SDK 35"},
    {"device_model": "Samsung Galaxy S24 5G", "system_version": "SDK 35"},
    # Samsung Galaxy S23 FE
    {"device_model": "Samsung Galaxy S23 FE", "system_version": "SDK 33"},
    {"device_model": "Samsung Galaxy S23 FE 5G", "system_version": "SDK 34"},
    # Samsung Galaxy A-series expansion
    {"device_model": "Samsung Galaxy A55 5G", "system_version": "SDK 34"},
    {"device_model": "Samsung Galaxy A54 5G", "system_version": "SDK 33"},
    {"device_model": "Samsung Galaxy A35", "system_version": "SDK 34"},
    {"device_model": "Samsung Galaxy A35 5G", "system_version": "SDK 34"},
    {"device_model": "Samsung Galaxy A25", "system_version": "SDK 34"},
    {"device_model": "Samsung Galaxy A25 5G", "system_version": "SDK 34"},
    {"device_model": "Samsung Galaxy A16", "system_version": "SDK 34"},
    {"device_model": "Samsung Galaxy A16 5G", "system_version": "SDK 34"},
    {"device_model": "Samsung Galaxy A15 5G", "system_version": "SDK 34"},
    {"device_model": "Samsung Galaxy A06", "system_version": "SDK 34"},
    {"device_model": "Samsung Galaxy A05s", "system_version": "SDK 33"},
    # Samsung Galaxy M-series
    {"device_model": "Samsung Galaxy M55", "system_version": "SDK 34"},
    {"device_model": "Samsung Galaxy M55 5G", "system_version": "SDK 34"},
    {"device_model": "Samsung Galaxy M35", "system_version": "SDK 34"},
    {"device_model": "Samsung Galaxy M35 5G", "system_version": "SDK 34"},
    {"device_model": "Samsung Galaxy M15", "system_version": "SDK 34"},
    {"device_model": "Samsung Galaxy M15 5G", "system_version": "SDK 34"},
    # Samsung Galaxy Foldable expansion
    {"device_model": "Samsung Galaxy Z Fold6", "system_version": "SDK 34"},
    {"device_model": "Samsung Galaxy Z Flip6", "system_version": "SDK 34"},
    {"device_model": "Samsung Galaxy Z Fold4", "system_version": "SDK 31"},
    # Samsung Galaxy Tab
    {"device_model": "Samsung Galaxy Tab S10 Ultra", "system_version": "SDK 34"},
    {"device_model": "Samsung Galaxy Tab S9 FE", "system_version": "SDK 33"},
    # Xiaomi 14 series expansion
    {"device_model": "Xiaomi 14 Ultra 5G", "system_version": "SDK 34"},
    {"device_model": "Xiaomi 14 Pro 5G", "system_version": "SDK 34"},
    {"device_model": "Xiaomi 14 5G", "system_version": "SDK 34"},
    {"device_model": "Xiaomi 14 Civi", "system_version": "SDK 34"},
    # Xiaomi 13 series expansion
    {"device_model": "Xiaomi 13 Ultra", "system_version": "SDK 33"},
    {"device_model": "Xiaomi 13 Lite", "system_version": "SDK 33"},
    {"device_model": "Xiaomi 13T Pro 5G", "system_version": "SDK 34"},
    {"device_model": "Xiaomi 13T 5G", "system_version": "SDK 34"},
    # Xiaomi 12 series
    {"device_model": "Xiaomi 12T Pro", "system_version": "SDK 33"},
    {"device_model": "Xiaomi 12T", "system_version": "SDK 33"},
    {"device_model": "Xiaomi 12 Pro", "system_version": "SDK 31"},
    # Redmi Note 13 expansion
    {"device_model": "Redmi Note 13 Pro+ 5G", "system_version": "SDK 34"},
    {"device_model": "Redmi Note 13 Pro 5G", "system_version": "SDK 34"},
    {"device_model": "Redmi Note 13 5G", "system_version": "SDK 34"},
    {"device_model": "Redmi Note 13 4G", "system_version": "SDK 33"},
    # Redmi Note 12 expansion
    {"device_model": "Redmi Note 12 Pro", "system_version": "SDK 33"},
    {"device_model": "Redmi Note 12", "system_version": "SDK 33"},
    {"device_model": "Redmi Note 12 5G", "system_version": "SDK 33"},
    {"device_model": "Redmi 12C", "system_version": "SDK 33"},
    {"device_model": "Redmi 12", "system_version": "SDK 33"},
    # Redmi 14 / A series
    {"device_model": "Redmi 14C", "system_version": "SDK 34"},
    {"device_model": "Redmi A3", "system_version": "SDK 34"},
    {"device_model": "Redmi A2+", "system_version": "SDK 33"},
    # POCO expansion
    {"device_model": "POCO F6 Pro 5G", "system_version": "SDK 34"},
    {"device_model": "POCO F6 5G", "system_version": "SDK 34"},
    {"device_model": "POCO X6 Pro 5G", "system_version": "SDK 34"},
    {"device_model": "POCO X6", "system_version": "SDK 34"},
    {"device_model": "POCO M6 Pro", "system_version": "SDK 33"},
    {"device_model": "POCO C65", "system_version": "SDK 33"},
    # OnePlus expansion
    {"device_model": "OnePlus 13 5G", "system_version": "SDK 35"},
    {"device_model": "OnePlus 12 5G", "system_version": "SDK 34"},
    {"device_model": "OnePlus 12R 5G", "system_version": "SDK 34"},
    {"device_model": "OnePlus 11R", "system_version": "SDK 33"},
    {"device_model": "OnePlus Nord 4 5G", "system_version": "SDK 34"},
    {"device_model": "OnePlus Nord CE 4 Lite", "system_version": "SDK 34"},
    {"device_model": "OnePlus Nord CE 3", "system_version": "SDK 33"},
    {"device_model": "OnePlus Nord N30", "system_version": "SDK 33"},
    {"device_model": "OnePlus Open", "system_version": "SDK 34"},
    # Google Pixel expansion
    {"device_model": "Google Pixel 9 Pro Fold", "system_version": "SDK 35"},
    {"device_model": "Google Pixel 8a 5G", "system_version": "SDK 34"},
    {"device_model": "Google Pixel 7a 5G", "system_version": "SDK 33"},
    {"device_model": "Google Pixel 6a", "system_version": "SDK 31"},
    {"device_model": "Google Pixel 6 Pro", "system_version": "SDK 31"},
    {"device_model": "Google Pixel 6", "system_version": "SDK 31"},
    # Realme expansion
    {"device_model": "realme GT 6", "system_version": "SDK 34"},
    {"device_model": "realme GT 6T", "system_version": "SDK 34"},
    {"device_model": "realme GT 5 Pro 5G", "system_version": "SDK 34"},
    {"device_model": "realme 12 Pro+ 5G", "system_version": "SDK 34"},
    {"device_model": "realme 12 Pro", "system_version": "SDK 34"},
    {"device_model": "realme 12", "system_version": "SDK 34"},
    {"device_model": "realme 11 Pro+", "system_version": "SDK 33"},
    {"device_model": "realme 11 Pro", "system_version": "SDK 33"},
    {"device_model": "realme C67", "system_version": "SDK 33"},
    {"device_model": "realme C55", "system_version": "SDK 33"},
    {"device_model": "realme C53", "system_version": "SDK 33"},
    {"device_model": "realme Narzo 70 Pro", "system_version": "SDK 34"},
    {"device_model": "realme Narzo 60x", "system_version": "SDK 33"},
    # OPPO expansion
    {"device_model": "OPPO Reno 12 Pro", "system_version": "SDK 34"},
    {"device_model": "OPPO Reno 12", "system_version": "SDK 34"},
    {"device_model": "OPPO Reno 12 F", "system_version": "SDK 34"},
    {"device_model": "OPPO Reno 11", "system_version": "SDK 34"},
    {"device_model": "OPPO Reno 11 F", "system_version": "SDK 34"},
    {"device_model": "OPPO Find X7 Ultra", "system_version": "SDK 34"},
    {"device_model": "OPPO Find X7", "system_version": "SDK 34"},
    {"device_model": "OPPO Find N3 Flip", "system_version": "SDK 33"},
    {"device_model": "OPPO A60", "system_version": "SDK 34"},
    {"device_model": "OPPO A79 5G", "system_version": "SDK 33"},
    {"device_model": "OPPO A18", "system_version": "SDK 33"},
    # Huawei expansion
    {"device_model": "Huawei Pura 70 Ultra", "system_version": "SDK 34"},
    {"device_model": "Huawei Pura 70 Pro", "system_version": "SDK 34"},
    {"device_model": "Huawei Pura 70", "system_version": "SDK 34"},
    {"device_model": "Huawei Nova 12 Ultra", "system_version": "SDK 34"},
    {"device_model": "Huawei Nova 12 Pro", "system_version": "SDK 34"},
    {"device_model": "Huawei Nova 12", "system_version": "SDK 34"},
    {"device_model": "Huawei Nova 12i", "system_version": "SDK 33"},
    {"device_model": "Huawei Nova 11 Pro", "system_version": "SDK 33"},
    {"device_model": "Huawei Mate 60 Pro", "system_version": "SDK 33"},
    {"device_model": "Huawei Mate 60", "system_version": "SDK 33"},
    {"device_model": "Huawei P60 Pro", "system_version": "SDK 33"},
    # Vivo expansion
    {"device_model": "vivo X100 Pro", "system_version": "SDK 34"},
    {"device_model": "vivo X100", "system_version": "SDK 34"},
    {"device_model": "vivo X100 Ultra", "system_version": "SDK 34"},
    {"device_model": "vivo V30 Pro 5G", "system_version": "SDK 34"},
    {"device_model": "vivo V30 5G", "system_version": "SDK 34"},
    {"device_model": "vivo V30e", "system_version": "SDK 34"},
    {"device_model": "vivo V29", "system_version": "SDK 33"},
    {"device_model": "vivo Y28", "system_version": "SDK 34"},
    {"device_model": "vivo Y27", "system_version": "SDK 33"},
    {"device_model": "vivo T3 Ultra", "system_version": "SDK 34"},
    {"device_model": "vivo T3x", "system_version": "SDK 34"},
    {"device_model": "vivo iQOO 13", "system_version": "SDK 35"},
    {"device_model": "vivo iQOO Neo 9 Pro", "system_version": "SDK 34"},
    # Honor expansion
    {"device_model": "Honor 200 Pro", "system_version": "SDK 34"},
    {"device_model": "Honor 200", "system_version": "SDK 34"},
    {"device_model": "Honor 200 Lite", "system_version": "SDK 34"},
    {"device_model": "Honor Magic6 Pro", "system_version": "SDK 34"},
    {"device_model": "Honor Magic6", "system_version": "SDK 34"},
    {"device_model": "Honor Magic6 Lite", "system_version": "SDK 34"},
    {"device_model": "Honor Magic V3", "system_version": "SDK 34"},
    {"device_model": "Honor Magic V2", "system_version": "SDK 33"},
    {"device_model": "Honor 90", "system_version": "SDK 33"},
    {"device_model": "Honor 90 Lite", "system_version": "SDK 33"},
    {"device_model": "Honor X9b", "system_version": "SDK 34"},
    {"device_model": "Honor X8b", "system_version": "SDK 34"},
    {"device_model": "Honor X7b", "system_version": "SDK 33"},
    # Nothing
    {"device_model": "Nothing Phone (2a)", "system_version": "SDK 34"},
    {"device_model": "Nothing Phone (2a) Plus", "system_version": "SDK 34"},
    {"device_model": "Nothing Phone (2)", "system_version": "SDK 33"},
    {"device_model": "Nothing Phone (1)", "system_version": "SDK 31"},
    # Motorola
    {"device_model": "Motorola Edge 50 Pro", "system_version": "SDK 34"},
    {"device_model": "Motorola Edge 50 Ultra", "system_version": "SDK 34"},
    {"device_model": "Motorola Edge 50 Fusion", "system_version": "SDK 34"},
    {"device_model": "Motorola Edge 40 Pro", "system_version": "SDK 33"},
    {"device_model": "Motorola Edge 40", "system_version": "SDK 33"},
    {"device_model": "Motorola Moto G84", "system_version": "SDK 33"},
    {"device_model": "Motorola Moto G54", "system_version": "SDK 33"},
    {"device_model": "Motorola Moto G34", "system_version": "SDK 34"},
    {"device_model": "Motorola Razr 40 Ultra", "system_version": "SDK 33"},
    # Tecno — popular in CIS budget segment
    {"device_model": "Tecno Spark 20 Pro+", "system_version": "SDK 33"},
    {"device_model": "Tecno Spark 20 Pro", "system_version": "SDK 33"},
    {"device_model": "Tecno Spark 20", "system_version": "SDK 33"},
    {"device_model": "Tecno Camon 30 Pro", "system_version": "SDK 34"},
    {"device_model": "Tecno Camon 30", "system_version": "SDK 34"},
    {"device_model": "Tecno Pova 6 Pro", "system_version": "SDK 34"},
    {"device_model": "Tecno Phantom V Fold", "system_version": "SDK 33"},
    # Infinix — popular in CIS budget segment
    {"device_model": "Infinix Note 40 Pro", "system_version": "SDK 34"},
    {"device_model": "Infinix Note 40", "system_version": "SDK 34"},
    {"device_model": "Infinix Hot 40 Pro", "system_version": "SDK 33"},
    {"device_model": "Infinix Hot 40", "system_version": "SDK 33"},
    {"device_model": "Infinix Zero 30", "system_version": "SDK 33"},
    {"device_model": "Infinix GT 20 Pro", "system_version": "SDK 34"},
    # Sony Xperia
    {"device_model": "Sony Xperia 1 VI", "system_version": "SDK 34"},
    {"device_model": "Sony Xperia 10 VI", "system_version": "SDK 34"},
    {"device_model": "Sony Xperia 5 V", "system_version": "SDK 33"},
    # ASUS
    {"device_model": "ASUS ROG Phone 8 Pro", "system_version": "SDK 34"},
    {"device_model": "ASUS ROG Phone 8", "system_version": "SDK 34"},
    {"device_model": "ASUS Zenfone 11 Ultra", "system_version": "SDK 34"},
    {"device_model": "ASUS Zenfone 10", "system_version": "SDK 33"},
    # ZTE / Nubia
    {"device_model": "ZTE Nubia Z60 Ultra", "system_version": "SDK 34"},
    {"device_model": "ZTE Nubia Z50S Pro", "system_version": "SDK 33"},
    {"device_model": "ZTE Blade V50 Design", "system_version": "SDK 33"},
]

# Current Telegram Android app versions (keep updated quarterly)
# Last updated: March 2026 — latest stable: 12.5.2
ANDROID_APP_VERSIONS: list[str] = [
    "12.4.3", "12.5.0", "12.5.1", "12.5.2",
]

DESKTOP_DEVICES: list[dict] = [
    # Windows 10 — multiple Telegram Desktop versions
    # Last updated: March 2026 — latest stable: 6.6.2
    {"device_model": "Desktop", "system_version": "Windows 10", "app_version": "6.6.2 x64"},
    {"device_model": "Desktop", "system_version": "Windows 10", "app_version": "6.6.0 x64"},
    {"device_model": "Desktop", "system_version": "Windows 10", "app_version": "6.5.1 x64"},
    # Windows 11 — multiple Telegram Desktop versions
    {"device_model": "Desktop", "system_version": "Windows 11", "app_version": "6.6.2 x64"},
    {"device_model": "Desktop", "system_version": "Windows 11", "app_version": "6.6.0 x64"},
    {"device_model": "Desktop", "system_version": "Windows 11", "app_version": "6.5.1 x64"},
    # Windows 11 specific builds
    {"device_model": "Desktop", "system_version": "Windows 11 23H2", "app_version": "6.6.2 x64"},
    {"device_model": "Desktop", "system_version": "Windows 11 23H2", "app_version": "6.6.0 x64"},
    {"device_model": "Desktop", "system_version": "Windows 11 23H2", "app_version": "6.5.1 x64"},
    {"device_model": "Desktop", "system_version": "Windows 11 24H2", "app_version": "6.6.2 x64"},
    {"device_model": "Desktop", "system_version": "Windows 11 24H2", "app_version": "6.6.0 x64"},
    # Windows 10 specific builds
    {"device_model": "Desktop", "system_version": "Windows 10 22H2", "app_version": "6.6.2 x64"},
    {"device_model": "Desktop", "system_version": "Windows 10 22H2", "app_version": "6.6.0 x64"},
    {"device_model": "Desktop", "system_version": "Windows 10 22H2", "app_version": "6.5.1 x64"},
    # macOS Sequoia (15.x)
    {"device_model": "Desktop", "system_version": "macOS 15.4", "app_version": "6.6.2"},
    {"device_model": "Desktop", "system_version": "macOS 15.4", "app_version": "6.6.0"},
    {"device_model": "Desktop", "system_version": "macOS 15.3", "app_version": "6.6.2"},
    {"device_model": "Desktop", "system_version": "macOS 15.3", "app_version": "6.6.0"},
    {"device_model": "Desktop", "system_version": "macOS 15.3", "app_version": "6.5.1"},
    {"device_model": "Desktop", "system_version": "macOS 15.2", "app_version": "6.6.2"},
    {"device_model": "Desktop", "system_version": "macOS 15.2", "app_version": "6.6.0"},
    {"device_model": "Desktop", "system_version": "macOS 15.2", "app_version": "6.5.1"},
    {"device_model": "Desktop", "system_version": "macOS 15.1", "app_version": "6.6.0"},
    {"device_model": "Desktop", "system_version": "macOS 15.1", "app_version": "6.5.1"},
    {"device_model": "Desktop", "system_version": "macOS 15.0", "app_version": "6.5.1"},
    # macOS Sonoma (14.x)
    {"device_model": "Desktop", "system_version": "macOS 14.7", "app_version": "6.6.2"},
    {"device_model": "Desktop", "system_version": "macOS 14.7", "app_version": "6.6.0"},
    {"device_model": "Desktop", "system_version": "macOS 14.7", "app_version": "6.5.1"},
    {"device_model": "Desktop", "system_version": "macOS 14.6", "app_version": "6.6.0"},
    {"device_model": "Desktop", "system_version": "macOS 14.6", "app_version": "6.5.1"},
    {"device_model": "Desktop", "system_version": "macOS 14.5", "app_version": "6.6.0"},
    {"device_model": "Desktop", "system_version": "macOS 14.5", "app_version": "6.5.1"},
    # macOS Ventura (13.x) — still widely used
    {"device_model": "Desktop", "system_version": "macOS 13.7", "app_version": "6.6.0"},
    {"device_model": "Desktop", "system_version": "macOS 13.6", "app_version": "6.5.1"},
    # Ubuntu
    {"device_model": "Desktop", "system_version": "Ubuntu 24.04", "app_version": "6.6.2 x64"},
    {"device_model": "Desktop", "system_version": "Ubuntu 24.04", "app_version": "6.6.0 x64"},
    {"device_model": "Desktop", "system_version": "Ubuntu 24.04", "app_version": "6.5.1 x64"},
    {"device_model": "Desktop", "system_version": "Ubuntu 22.04", "app_version": "6.6.2 x64"},
    {"device_model": "Desktop", "system_version": "Ubuntu 22.04", "app_version": "6.6.0 x64"},
    {"device_model": "Desktop", "system_version": "Ubuntu 22.04", "app_version": "6.5.1 x64"},
    # Fedora
    {"device_model": "Desktop", "system_version": "Fedora 40", "app_version": "6.6.2 x64"},
    {"device_model": "Desktop", "system_version": "Fedora 40", "app_version": "6.6.0 x64"},
    {"device_model": "Desktop", "system_version": "Fedora 40", "app_version": "6.5.1 x64"},
    {"device_model": "Desktop", "system_version": "Fedora 39", "app_version": "6.6.0 x64"},
    {"device_model": "Desktop", "system_version": "Fedora 39", "app_version": "6.5.1 x64"},
    # Arch / Manjaro — developer audience
    {"device_model": "Desktop", "system_version": "Arch Linux", "app_version": "6.6.2 x64"},
    {"device_model": "Desktop", "system_version": "Arch Linux", "app_version": "6.6.0 x64"},
    {"device_model": "Desktop", "system_version": "Manjaro", "app_version": "6.6.2 x64"},
    {"device_model": "Desktop", "system_version": "Manjaro", "app_version": "6.6.0 x64"},
]

# Language profiles by phone number country
LANG_PROFILES: dict[str, dict] = {
    "ru": {"lang_code": "ru", "system_lang_code": "ru-ru", "lang_pack": "ru"},
    "kz": {"lang_code": "ru", "system_lang_code": "ru-kz", "lang_pack": "ru"},
    "uz": {"lang_code": "ru", "system_lang_code": "ru-uz", "lang_pack": "ru"},
    "ua": {"lang_code": "uk", "system_lang_code": "uk-ua", "lang_pack": "uk"},
    "ge": {"lang_code": "ka", "system_lang_code": "ka-ge", "lang_pack": "en"},
    "am": {"lang_code": "hy", "system_lang_code": "hy-am", "lang_pack": "en"},
    "en": {"lang_code": "en", "system_lang_code": "en-us", "lang_pack": "en"},
}

# Safe API ID configurations
API_CONFIGS: dict[int, dict] = {
    21724: {"name": "AndroidX", "api_hash": "3e0cb5efcd52300aec5994fdfc5bdc16"},
    2040: {"name": "Desktop", "api_hash": "b18441a1ff607e10a989891a5462e627"},
}

# Flagged API IDs — never use for new accounts
FLAGGED_API_IDS: set[int] = {4}


# ---------------------------------------------------------------------------
# DeviceIdentityManager
# ---------------------------------------------------------------------------


class DeviceIdentityManager:
    """
    Generates and manages unique device identities for Telegram accounts.

    Thread-safe for single-threaded asyncio. Each generate() call atomically
    reserves a fingerprint so concurrent coroutines won't collide.

    Usage:
        mgr = DeviceIdentityManager()
        mgr.load_used_fingerprints(sessions_dir)
        identity = mgr.generate(api_id=21724, lang_profile="kz")
        warnings = mgr.validate(identity)
    """

    def __init__(self) -> None:
        self._used_fingerprints: set[str] = set()

    def load_used_fingerprints(self, sessions_dir: Path) -> int:
        """
        Scan existing account metadata to populate used fingerprints.

        Returns:
            Number of fingerprints loaded.
        """
        count = 0
        for json_file in sessions_dir.rglob("*.json"):
            try:
                meta = json.loads(json_file.read_text(encoding="utf-8"))
                device = meta.get("device", meta.get("device_model", ""))
                sdk = meta.get("sdk", meta.get("system_version", ""))
                app_ver = meta.get("app_version", "")
                if device and sdk:
                    self._used_fingerprints.add(f"{device}|{sdk}|{app_ver}")
                    count += 1
            except (json.JSONDecodeError, OSError):
                continue
        log.info(f"[DeviceIdentity] Loaded {count} existing fingerprints")
        return count

    def generate(
        self,
        api_id: int = 21724,
        lang_profile: str = "ru",
        exclude_fingerprints: Optional[set[str]] = None,
    ) -> DeviceIdentity:
        """
        Generate a unique DeviceIdentity that doesn't collide with existing ones.

        Args:
            api_id: 21724 (AndroidX) or 2040 (Desktop).
            lang_profile: Key from LANG_PROFILES (ru, kz, uz, ua, en).
            exclude_fingerprints: Additional fingerprint keys to avoid.

        Returns:
            DeviceIdentity with unique device_model + system_version + app_version.

        Raises:
            ValueError: If api_id is not supported or pool is exhausted.
        """
        if api_id not in API_CONFIGS:
            raise ValueError(
                f"Unsupported api_id: {api_id}. Use one of: {list(API_CONFIGS.keys())}"
            )

        excluded = (exclude_fingerprints or set()) | self._used_fingerprints
        api_hash = API_CONFIGS[api_id]["api_hash"]
        lang = LANG_PROFILES.get(lang_profile, LANG_PROFILES["ru"])

        candidates = (
            self._build_android_candidates(api_hash, lang)
            if api_id == 21724
            else self._build_desktop_candidates(api_hash, lang)
        )

        available = [c for c in candidates if c.fingerprint_key() not in excluded]

        if not available:
            raise ValueError(
                f"No unique fingerprints left for api_id={api_id}. "
                f"Pool: {len(candidates)}, excluded: {len(excluded)}. "
                "Add more device entries or app_version variants."
            )

        # Pool exhaustion warning: log if remaining < 10% of total
        total_pool = len(candidates)
        remaining = len(available)
        pct_remaining = (remaining / total_pool * 100) if total_pool > 0 else 0
        if pct_remaining < 10:
            log.warning(
                f"[DeviceIdentity] WARNING: Pool nearly exhausted! "
                f"Only {remaining}/{total_pool} fingerprints remaining "
                f"({pct_remaining:.1f}%) for api_id={api_id}. "
                "Consider adding more devices or app versions."
            )

        identity = random.choice(available)
        self._used_fingerprints.add(identity.fingerprint_key())
        log.info(
            f"[DeviceIdentity] Generated: {identity.device_model} / "
            f"{identity.system_version} / {identity.app_version}"
        )
        return identity

    def generate_deterministic(
        self,
        unique_id: str,
        api_id: int = 21724,
        lang_profile: str = "ru",
    ) -> DeviceIdentity:
        """
        Generate a deterministic identity from a unique_id.

        Same unique_id always produces the same device. Useful for ensuring
        consistency without storing metadata separately (like opentele's
        API.Generate(unique_id=...)).

        Args:
            unique_id: Any string (phone number, session filename, etc.)
            api_id: 21724 or 2040.
            lang_profile: Language profile key.

        Returns:
            DeviceIdentity that is always the same for the same unique_id.
        """
        api_hash = API_CONFIGS[api_id]["api_hash"]
        lang = LANG_PROFILES.get(lang_profile, LANG_PROFILES["ru"])

        candidates = (
            self._build_android_candidates(api_hash, lang)
            if api_id == 21724
            else self._build_desktop_candidates(api_hash, lang)
        )

        hash_val = int(hashlib.sha256(unique_id.encode()).hexdigest(), 16)
        index = hash_val % len(candidates)
        return candidates[index]

    def validate(self, identity: DeviceIdentity) -> list[str]:
        """
        Validate that a DeviceIdentity is realistic and consistent.

        Returns:
            List of warning strings. Empty list = valid.
        """
        warnings: list[str] = []

        # --- API ID safety ---
        if identity.api_id in FLAGGED_API_IDS:
            warnings.append(
                f"API ID {identity.api_id} is FLAGGED by Telegram. "
                "Session at high risk of revocation."
            )
        elif identity.api_id not in API_CONFIGS:
            warnings.append(f"Unknown API ID: {identity.api_id}")

        # --- Device/API ID family consistency ---
        if identity.api_id in (4, 6, 21724):
            if identity.device_model == "Desktop":
                warnings.append(
                    f"device_model='Desktop' but api_id={identity.api_id} is Android family"
                )
            if not identity.system_version.startswith("SDK"):
                warnings.append(
                    f"system_version='{identity.system_version}' "
                    "doesn't look like Android SDK format"
                )
        elif identity.api_id == 2040:
            if identity.system_version.startswith("SDK"):
                warnings.append(
                    f"system_version='{identity.system_version}' looks like Android "
                    "but api_id=2040 is Desktop"
                )

        # --- SDK level realism ---
        if identity.api_id in (21724, 6, 4) and identity.system_version.startswith("SDK"):
            try:
                sdk_level = int(identity.system_version.split()[-1])
                if sdk_level < 28:
                    warnings.append(
                        f"SDK {sdk_level} (Android <9) is very old. "
                        "Most Telegram users are on SDK 31+."
                    )
                if sdk_level > 36:
                    warnings.append(
                        f"SDK {sdk_level} does not exist yet. Use 28-35."
                    )
            except ValueError:
                warnings.append(
                    f"Cannot parse SDK level from '{identity.system_version}'"
                )

        # --- App version currency ---
        if identity.api_id in (21724, 6):
            try:
                major = int(identity.app_version.split(".")[0])
                if major < 10:
                    warnings.append(
                        f"app_version '{identity.app_version}' is very old "
                        "for Android Telegram (current: 12.x)"
                    )
            except (ValueError, IndexError):
                pass
        elif identity.api_id == 2040:
            try:
                major = int(identity.app_version.split(".")[0])
                if major < 5:
                    warnings.append(
                        f"app_version '{identity.app_version}' is very old "
                        "for Desktop Telegram (current: 6.x)"
                    )
            except (ValueError, IndexError):
                pass

        return warnings

    def is_fingerprint_used(self, identity: DeviceIdentity) -> bool:
        """Check if this fingerprint is already assigned to another account."""
        return identity.fingerprint_key() in self._used_fingerprints

    def reserve_fingerprint(self, identity: DeviceIdentity) -> None:
        """Manually reserve a fingerprint (e.g., for imported accounts)."""
        self._used_fingerprints.add(identity.fingerprint_key())

    def get_pool_stats(self) -> dict:
        """Return statistics about the fingerprint pool."""
        android_total = len(ANDROID_DEVICES) * len(ANDROID_APP_VERSIONS)
        desktop_total = len(DESKTOP_DEVICES)
        total = android_total + desktop_total
        used = len(self._used_fingerprints)
        remaining = max(0, total - used)
        pct_remaining = (remaining / total * 100) if total > 0 else 0
        return {
            "android_pool_size": android_total,
            "desktop_pool_size": desktop_total,
            "total_pool_size": total,
            "used_count": used,
            "remaining": remaining,
            "pct_remaining": round(pct_remaining, 1),
            "available_android": max(0, android_total - used),
            "available_desktop": max(0, desktop_total - used),
        }

    def pool_stats(self) -> dict:
        """
        Return concise pool usage statistics.

        Returns:
            {"total": N, "used": M, "remaining": N-M, "pct_remaining": float}
        """
        android_total = len(ANDROID_DEVICES) * len(ANDROID_APP_VERSIONS)
        desktop_total = len(DESKTOP_DEVICES)
        total = android_total + desktop_total
        used = len(self._used_fingerprints)
        remaining = max(0, total - used)
        pct_remaining = (remaining / total * 100) if total > 0 else 0
        return {
            "total": total,
            "used": used,
            "remaining": remaining,
            "pct_remaining": round(pct_remaining, 1),
        }

    # --- Private helpers ---

    def _build_android_candidates(
        self, api_hash: str, lang: dict
    ) -> list[DeviceIdentity]:
        candidates = []
        for device in ANDROID_DEVICES:
            for app_ver in ANDROID_APP_VERSIONS:
                candidates.append(
                    DeviceIdentity(
                        api_id=21724,
                        api_hash=api_hash,
                        device_model=device["device_model"],
                        system_version=device["system_version"],
                        app_version=app_ver,
                        **lang,
                    )
                )
        return candidates

    def _build_desktop_candidates(
        self, api_hash: str, lang: dict
    ) -> list[DeviceIdentity]:
        candidates = []
        for device in DESKTOP_DEVICES:
            candidates.append(
                DeviceIdentity(
                    api_id=2040,
                    api_hash=api_hash,
                    device_model=device["device_model"],
                    system_version=device["system_version"],
                    app_version=device["app_version"],
                    **lang,
                )
            )
        return candidates


# ---------------------------------------------------------------------------
# Audit helper
# ---------------------------------------------------------------------------


def audit_all_fingerprints(sessions_dir: Path) -> dict:
    """
    Audit all account fingerprints for collisions, realism, and risk.

    Returns a dict with:
        total_accounts: int
        collisions: list of {fingerprint, accounts, count}
        warnings: list of {phone, warnings}
        api_id_4_accounts: list of phone numbers
        pool_stats: dict
    """
    results: dict = {
        "total_accounts": 0,
        "collisions": [],
        "warnings": [],
        "api_id_4_accounts": [],
        "pool_stats": {},
    }

    fingerprint_map: dict[str, list[str]] = {}
    mgr = DeviceIdentityManager()

    for json_file in sessions_dir.rglob("*.json"):
        try:
            meta = json.loads(json_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue

        phone = meta.get("phone", json_file.stem)
        results["total_accounts"] += 1

        api_id = meta.get("app_id", 0)
        if api_id == 4:
            results["api_id_4_accounts"].append(phone)

        try:
            identity = DeviceIdentity.from_metadata(meta)
            warns = mgr.validate(identity)
            if warns:
                results["warnings"].append({"phone": phone, "warnings": warns})

            fp_key = identity.fingerprint_key()
            fingerprint_map.setdefault(fp_key, []).append(phone)
        except Exception as e:
            results["warnings"].append({"phone": phone, "warnings": [str(e)]})

    for fp_key, phones in fingerprint_map.items():
        if len(phones) > 1:
            results["collisions"].append(
                {
                    "fingerprint": fp_key,
                    "accounts": phones,
                    "count": len(phones),
                }
            )

    results["pool_stats"] = mgr.get_pool_stats()
    return results


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

device_identity_manager = DeviceIdentityManager()
