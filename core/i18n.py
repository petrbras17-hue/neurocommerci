"""
Internationalization engine for NEURO COMMENTING.
Supports: ru, en, es, pt, tr, de, fr, zh, ar
Auto-detects language by: Accept-Language header -> GeoIP -> default (en)
"""

from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Request, Response

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SUPPORTED_LANGUAGES = ["ru", "en", "es", "pt", "tr", "de", "fr", "zh", "ar"]
DEFAULT_LANGUAGE = "en"
LANG_COOKIE_NAME = "nc_lang"
LANG_COOKIE_MAX_AGE = 365 * 24 * 60 * 60  # 1 year

I18N_DIR = Path(__file__).resolve().parent.parent / "i18n"

# Country code -> language mapping (ISO 3166-1 alpha-2 -> language code)
COUNTRY_TO_LANG: Dict[str, str] = {
    # Russian
    "RU": "ru", "KZ": "ru", "BY": "ru", "UA": "ru", "KG": "ru",
    "TJ": "ru", "UZ": "ru", "MD": "ru", "AM": "ru", "AZ": "ru",
    "GE": "ru",
    # English
    "US": "en", "GB": "en", "AU": "en", "CA": "en", "NZ": "en",
    "IE": "en", "ZA": "en", "SG": "en", "IN": "en", "PH": "en",
    "NG": "en", "KE": "en", "GH": "en",
    # Spanish
    "ES": "es", "MX": "es", "AR": "es", "CO": "es", "CL": "es",
    "PE": "es", "VE": "es", "EC": "es", "GT": "es", "CU": "es",
    "DO": "es", "HN": "es", "PY": "es", "SV": "es", "NI": "es",
    "CR": "es", "PA": "es", "UY": "es", "BO": "es",
    # Portuguese
    "BR": "pt", "PT": "pt", "AO": "pt", "MZ": "pt",
    # Turkish
    "TR": "tr",
    # German
    "DE": "de", "AT": "de", "CH": "de", "LI": "de", "LU": "de",
    # French
    "FR": "fr", "BE": "fr", "SN": "fr", "CI": "fr", "CM": "fr",
    "MG": "fr", "ML": "fr", "BF": "fr", "NE": "fr", "TD": "fr",
    "GN": "fr", "RW": "fr", "HT": "fr", "TG": "fr", "BJ": "fr",
    "CD": "fr", "CG": "fr", "GA": "fr", "DJ": "fr", "KM": "fr",
    "MC": "fr",
    # Chinese
    "CN": "zh", "TW": "zh", "HK": "zh", "MO": "zh",
    # Arabic
    "SA": "ar", "AE": "ar", "EG": "ar", "IQ": "ar", "MA": "ar",
    "DZ": "ar", "SD": "ar", "YE": "ar", "SY": "ar", "TN": "ar",
    "JO": "ar", "LY": "ar", "LB": "ar", "OM": "ar", "KW": "ar",
    "QA": "ar", "BH": "ar", "MR": "ar", "PS": "ar",
}

# Accept-Language prefix -> language code
_ACCEPT_LANG_PREFIX: Dict[str, str] = {
    "ru": "ru",
    "en": "en",
    "es": "es",
    "pt": "pt",
    "tr": "tr",
    "de": "de",
    "fr": "fr",
    "zh": "zh",
    "ar": "ar",
}


# ---------------------------------------------------------------------------
# Translation loading
# ---------------------------------------------------------------------------

@lru_cache(maxsize=20)
def _load_translations_file(lang: str) -> Dict[str, Any]:
    """Load and cache a single translation JSON file."""
    filepath = I18N_DIR / f"{lang}.json"
    if not filepath.is_file():
        logger.warning("Translation file not found: %s", filepath)
        return {}
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("Failed to load translation file %s: %s", filepath, exc)
        return {}


def get_translations(lang: str) -> Dict[str, Any]:
    """
    Load translations for a given language.
    Falls back to DEFAULT_LANGUAGE if the requested language is not available.
    """
    if lang not in SUPPORTED_LANGUAGES:
        lang = DEFAULT_LANGUAGE
    data = _load_translations_file(lang)
    if not data and lang != DEFAULT_LANGUAGE:
        data = _load_translations_file(DEFAULT_LANGUAGE)
    return data


def get_supported_languages() -> List[Dict[str, str]]:
    """Return list of supported languages with metadata."""
    result = []
    for lang_code in SUPPORTED_LANGUAGES:
        data = _load_translations_file(lang_code)
        meta = data.get("meta", {})
        result.append({
            "code": lang_code,
            "name": meta.get("name", lang_code),
            "dir": meta.get("dir", "ltr"),
            "flag": meta.get("flag", ""),
        })
    return result


# ---------------------------------------------------------------------------
# Translation accessor
# ---------------------------------------------------------------------------

def t(key: str, lang: str, **kwargs: Any) -> str:
    """
    Get a translation by dot-separated key with optional variable substitution.

    Usage:
        t("hero.headline", "ru")
        t("pricing.starter.price", "en", currency="USD")
    """
    data = get_translations(lang)
    parts = key.split(".")
    current: Any = data
    for part in parts:
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return key  # Key not found, return the key itself
    if current is None:
        # Fallback to default language
        if lang != DEFAULT_LANGUAGE:
            return t(key, DEFAULT_LANGUAGE, **kwargs)
        return key
    if not isinstance(current, str):
        return str(current)
    if kwargs:
        try:
            return current.format(**kwargs)
        except (KeyError, IndexError):
            return current
    return current


class TranslationProxy:
    """
    Dict-like proxy for use in Jinja2 templates.
    Supports nested dot-access via dict key lookup: t["hero"]["headline"]
    Also supports t.hero.headline via attribute access.
    """

    def __init__(self, data: Dict[str, Any], lang: str, fallback: Optional[Dict[str, Any]] = None):
        self._data = data
        self._lang = lang
        self._fallback = fallback or {}

    def __getitem__(self, key: str) -> Any:
        value = self._data.get(key)
        if value is None:
            value = self._fallback.get(key)
        if value is None:
            return key
        if isinstance(value, dict):
            fallback_sub = self._fallback.get(key) if isinstance(self._fallback.get(key), dict) else {}
            return TranslationProxy(value, self._lang, fallback_sub)
        return value

    def __getattr__(self, key: str) -> Any:
        if key.startswith("_"):
            return object.__getattribute__(self, key)
        return self.__getitem__(key)

    def __str__(self) -> str:
        return str(self._data)

    def __repr__(self) -> str:
        return f"TranslationProxy(lang={self._lang}, keys={list(self._data.keys())})"

    def get(self, key: str, default: Any = None) -> Any:
        value = self._data.get(key)
        if value is None:
            value = self._fallback.get(key, default)
        if isinstance(value, dict):
            return TranslationProxy(value, self._lang)
        return value


def get_translation_proxy(lang: str) -> TranslationProxy:
    """Get a TranslationProxy for use in Jinja2 templates."""
    data = get_translations(lang)
    fallback = get_translations(DEFAULT_LANGUAGE) if lang != DEFAULT_LANGUAGE else {}
    return TranslationProxy(data, lang, fallback)


# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------

def _parse_accept_language(header: str) -> Optional[str]:
    """
    Parse Accept-Language header and return the best matching supported language.
    Example: "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7"
    """
    if not header:
        return None
    langs_with_quality = []
    for part in header.split(","):
        part = part.strip()
        if not part:
            continue
        if ";q=" in part:
            lang_tag, q_str = part.split(";q=", 1)
            try:
                quality = float(q_str.strip())
            except ValueError:
                quality = 0.0
        else:
            lang_tag = part
            quality = 1.0
        lang_tag = lang_tag.strip().lower()
        langs_with_quality.append((lang_tag, quality))

    # Sort by quality descending
    langs_with_quality.sort(key=lambda x: x[1], reverse=True)

    for lang_tag, _ in langs_with_quality:
        # Try exact match first (e.g., "pt-br" -> "pt")
        prefix = lang_tag.split("-")[0]
        if prefix in _ACCEPT_LANG_PREFIX:
            return _ACCEPT_LANG_PREFIX[prefix]

    return None


def _detect_from_geoip(request: Request) -> Optional[str]:
    """
    Detect language from GeoIP headers.
    Supports: CF-IPCountry (Cloudflare), X-Country-Code (custom nginx),
    X-Vercel-IP-Country (Vercel).
    """
    country_code = (
        request.headers.get("CF-IPCountry")
        or request.headers.get("X-Country-Code")
        or request.headers.get("X-Vercel-IP-Country")
        or ""
    ).strip().upper()
    if country_code and country_code in COUNTRY_TO_LANG:
        return COUNTRY_TO_LANG[country_code]
    return None


def detect_language(request: Request) -> str:
    """
    Detect the best language for the request.
    Priority:
    1. Query parameter ?lang=XX
    2. Cookie nc_lang
    3. Accept-Language header
    4. GeoIP (CF-IPCountry, X-Country-Code, etc.)
    5. Default: "en"
    """
    # 1. Query parameter
    lang_param = request.query_params.get("lang", "").strip().lower()
    if lang_param in SUPPORTED_LANGUAGES:
        return lang_param

    # 2. Cookie
    cookie_lang = request.cookies.get(LANG_COOKIE_NAME, "").strip().lower()
    if cookie_lang in SUPPORTED_LANGUAGES:
        return cookie_lang

    # 3. Accept-Language header
    accept_lang = request.headers.get("Accept-Language", "")
    detected = _parse_accept_language(accept_lang)
    if detected:
        return detected

    # 4. GeoIP
    geo_lang = _detect_from_geoip(request)
    if geo_lang:
        return geo_lang

    # 5. Default
    return DEFAULT_LANGUAGE


def set_language_cookie(response: Response, lang: str) -> None:
    """Set the language preference cookie on a response."""
    if lang not in SUPPORTED_LANGUAGES:
        lang = DEFAULT_LANGUAGE
    response.set_cookie(
        key=LANG_COOKIE_NAME,
        value=lang,
        max_age=LANG_COOKIE_MAX_AGE,
        httponly=False,  # Readable by JS for client-side lang switch
        samesite="lax",
        path="/",
    )


# ---------------------------------------------------------------------------
# Cache invalidation (for development / hot reload)
# ---------------------------------------------------------------------------

def clear_translation_cache() -> None:
    """Clear all cached translation files. Useful during development."""
    _load_translations_file.cache_clear()
