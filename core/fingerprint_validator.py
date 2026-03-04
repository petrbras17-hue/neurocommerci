"""
FingerprintValidator — проверка консистентности device fingerprint и API ID.

Валидирует, что device_model/system_version/app_version соответствуют семейству API ID.
Помечает несоответствия для отображения в админке.
"""

from __future__ import annotations

import re

from utils.logger import log


# Семейства API ID → ожидаемые паттерны fingerprint
API_FAMILIES = {
    4: {
        "name": "Android (OLD — FLAGGED)",
        "flagged": True,
        "device_pattern": r".*",  # Любое Android-устройство
        "sdk_pattern": r"SDK \d+",
        "app_version_pattern": r"\d+\.\d+",
    },
    6: {
        "name": "Android",
        "flagged": False,
        "device_pattern": r".*",
        "sdk_pattern": r"SDK \d+",
        "app_version_pattern": r"\d+\.\d+",
    },
    2040: {
        "name": "Telegram Desktop",
        "flagged": False,
        "device_pattern": r"(Desktop|PC|Linux|macOS|Windows|Ubuntu|Fedora).*",
        "sdk_pattern": r"(Windows|macOS|Linux|Ubuntu).*",
        "app_version_pattern": r"\d+\.\d+",
    },
    10840: {
        "name": "iOS",
        "flagged": False,
        "device_pattern": r"(iPhone|iPad|iPod).*",
        "sdk_pattern": r"(iOS|iPadOS) \d+",
        "app_version_pattern": r"\d+\.\d+",
    },
    2834: {
        "name": "macOS",
        "flagged": False,
        "device_pattern": r"(Mac|macOS|iMac|MacBook).*",
        "sdk_pattern": r"macOS \d+",
        "app_version_pattern": r"\d+\.\d+",
    },
    21724: {
        "name": "AndroidX",
        "flagged": False,
        "device_pattern": r".*",
        "sdk_pattern": r"SDK \d+",
        "app_version_pattern": r"\d+\.\d+",
    },
    2496: {
        "name": "Web",
        "flagged": False,
        "device_pattern": r".*",
        "sdk_pattern": r".*",
        "app_version_pattern": r".*",
    },
}

# Безопасные API ID для новых аккаунтов
SAFE_API_IDS = {2040, 21724, 6, 10840, 2834, 2496}
FLAGGED_API_IDS = {4}

# Рекомендуемые fingerprint для новых аккаунтов
RECOMMENDED_FINGERPRINTS = {
    2040: {
        "device": "Desktop",
        "sdk": "Windows 10",
        "app_version": "4.16.8 x64",
        "lang_pack": "ru",
        "system_lang_pack": "ru",
    },
    21724: {
        "device": "Samsung Galaxy S23",
        "sdk": "SDK 33",
        "app_version": "10.14.5",
        "lang_pack": "ru",
        "system_lang_pack": "ru-ru",
    },
}


class FingerprintValidator:
    """Валидатор device fingerprint для Telegram аккаунтов."""

    def validate(self, api_id: int, device_params: dict) -> list[str]:
        """
        Проверить fingerprint на консистентность с API ID.
        Возвращает список предупреждений (пустой = всё ОК).
        """
        warnings = []

        if api_id in FLAGGED_API_IDS:
            warnings.append(
                f"API ID {api_id} помечен Telegram как опасный. "
                "Сессия под повышенным риском отзыва."
            )

        family = API_FAMILIES.get(api_id)
        if not family:
            warnings.append(f"Неизвестный API ID {api_id}")
            return warnings

        device = device_params.get("device", "")
        sdk = device_params.get("sdk", "")

        # Проверка device_model
        if family.get("device_pattern"):
            if not re.match(family["device_pattern"], device, re.IGNORECASE):
                warnings.append(
                    f"device_model '{device}' не соответствует API ID {api_id} ({family['name']})"
                )

        # Для Desktop API ID — Android устройство это несоответствие
        if api_id == 2040 and re.match(r"SDK \d+", sdk):
            warnings.append(
                f"SDK формат '{sdk}' выглядит как Android, но API ID {api_id} — Desktop"
            )

        # Для Android API ID — Desktop device это несоответствие
        if api_id in (4, 6, 21724) and re.match(r"(Desktop|Windows|macOS)", device, re.IGNORECASE):
            warnings.append(
                f"device_model '{device}' выглядит как Desktop, но API ID {api_id} — Android"
            )

        return warnings

    def is_api_id_safe(self, api_id: int) -> bool:
        """Безопасен ли API ID для использования."""
        return api_id in SAFE_API_IDS

    def get_recommended_fingerprint(self, api_id: int = 2040) -> dict:
        """Получить рекомендуемый fingerprint для нового аккаунта."""
        return dict(RECOMMENDED_FINGERPRINTS.get(api_id, RECOMMENDED_FINGERPRINTS[2040]))

    def validate_account(self, device_params: dict) -> dict:
        """
        Полная валидация аккаунта. Возвращает dict с результатами.
        """
        api_id = device_params.get("app_id") or 0
        warnings = self.validate(api_id, device_params)

        return {
            "api_id": api_id,
            "api_family": API_FAMILIES.get(api_id, {}).get("name", "Unknown"),
            "is_flagged": api_id in FLAGGED_API_IDS,
            "is_safe": api_id in SAFE_API_IDS,
            "warnings": warnings,
            "valid": len(warnings) == 0,
        }


# Глобальный экземпляр
fingerprint_validator = FingerprintValidator()
