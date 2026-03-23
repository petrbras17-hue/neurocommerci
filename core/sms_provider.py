"""
SMS Provider — мульти-провайдер SMS активаций для Telegram.

Поддерживаемые провайдеры (по приоритету):
  1. SMS-Man   — api.sms-man.com     — real SIM + virtual, 195 стран, rental
  2. SmsPVA    — smspva.com          — real SIM опция, 99% delivery
  3. 5SIM      — 5sim.net            — дешёвые, JWT auth, 180+ стран
  4. Grizzly   — grizzlysms.com      — Telegram-фокус, maxPrice, refund on 2FA
  5. VAK-SMS   — vak-sms.com         — текущий (fallback), prolongation

Multi-provider fallback: если один не отдаёт код — автоматически пробуем следующий.
"""

from __future__ import annotations

import asyncio
import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, List, Type

import aiohttp

from utils.logger import log


# ─── Data classes ────────────────────────────────────────────────────

@dataclass
class SmsNumber:
    """Купленный номер."""
    phone: str           # e.g. "77001234567"
    request_id: str      # ID операции у провайдера
    country: str         # kz, uz, kg, ru
    price: float         # цена
    service: str         # tg
    provider: str = ""   # имя провайдера


# ─── Exceptions ──────────────────────────────────────────────────────

class SmsApiError(Exception):
    """Ошибка API SMS-сервиса."""
    pass


class SmsTimeoutError(SmsApiError):
    """Таймаут ожидания SMS."""
    pass


class SmsNoNumbersError(SmsApiError):
    """Нет доступных номеров у провайдера."""
    pass


# ─── Abstract base ──────────────────────────────────────────────────

class BaseSmsProvider(ABC):
    """Общий интерфейс SMS-провайдера."""

    PROVIDER_NAME: str = "unknown"

    def __init__(self, api_key: str):
        self._api_key = api_key
        self._session: Optional[aiohttp.ClientSession] = None

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30)
            )
        return self._session

    @abstractmethod
    async def get_balance(self) -> float:
        ...

    @abstractmethod
    async def buy_number(self, country: str = "kz") -> SmsNumber:
        ...

    @abstractmethod
    async def wait_for_code(
        self,
        request_id: str,
        timeout_sec: int = 120,
        poll_interval: float = 3.0,
    ) -> str:
        ...

    @abstractmethod
    async def cancel_number(self, request_id: str) -> None:
        ...

    async def mark_bad(self, request_id: str) -> None:
        """По умолчанию то же, что cancel."""
        await self.cancel_number(request_id)

    async def request_another_sms(self, request_id: str) -> None:
        """Запросить повторную отправку (не все провайдеры поддерживают)."""
        log.warning(f"{self.PROVIDER_NAME}: повторная отправка не поддерживается")

    async def prolong_number(self, phone: str) -> SmsNumber:
        """Продлить аренду номера (не все провайдеры поддерживают)."""
        raise SmsApiError(f"{self.PROVIDER_NAME}: prolongation не поддерживается")

    async def get_available_count(self, country: str = "kz") -> dict:
        """Количество доступных номеров."""
        return {"count": -1, "price": 0}

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()


# ═══════════════════════════════════════════════════════════════════
# 1. SMS-Man  (РЕКОМЕНДОВАН КАК ОСНОВНОЙ)
# ═══════════════════════════════════════════════════════════════════

class SmsManProvider(BaseSmsProvider):
    """
    SMS-Man API клиент.
    API docs: https://sms-man.com/api
    Base URL: https://api.sms-man.com/control/
    Auth: token query param
    Telegram application_id: varies (use get-limits to find)
    Kazakhstan country_id: 2
    """

    PROVIDER_NAME = "sms-man"
    BASE_URL = "https://api.sms-man.com/control"

    # SMS-Man application IDs — may change, fetch via get-limits if needed
    TELEGRAM_APP_ID = "6"  # Telegram
    COUNTRY_MAP = {
        "kz": "2",    # Kazakhstan
        "ru": "1",    # Russia
        "uz": "58",   # Uzbekistan
        "kg": "65",   # Kyrgyzstan
        "in": "14",   # India
        "id": "4",    # Indonesia
        "tr": "62",   # Turkey
    }

    async def _get(self, method: str, **params) -> dict:
        session = await self._ensure_session()
        params["token"] = self._api_key
        url = f"{self.BASE_URL}/{method}"
        async with session.get(url, params=params) as resp:
            text = await resp.text()
            if resp.status != 200:
                raise SmsApiError(f"SMS-Man HTTP {resp.status}: {text}")
            data = json.loads(text)
            if isinstance(data, dict) and "error_code" in data:
                error_msg = data.get("error_msg", data.get("error_code", "unknown"))
                if "no_numbers" in str(error_msg).lower() or "no free phones" in str(error_msg).lower():
                    raise SmsNoNumbersError(f"SMS-Man: {error_msg}")
                raise SmsApiError(f"SMS-Man: {error_msg}")
            return data

    async def get_balance(self) -> float:
        data = await self._get("get-balance")
        return float(data.get("balance", 0))

    async def get_available_count(self, country: str = "kz") -> dict:
        country_id = self.COUNTRY_MAP.get(country, "2")
        try:
            data = await self._get(
                "get-limits",
                country_id=country_id,
                application_id=self.TELEGRAM_APP_ID,
            )
            # Response: {app_id: {"count": N}}
            tg_data = data.get(self.TELEGRAM_APP_ID, {})
            return {"count": tg_data.get("count", 0), "price": 0}
        except SmsApiError as e:
            log.warning(f"SMS-Man: не удалось получить количество номеров: {e}")
            return {"count": -1, "price": 0}

    async def buy_number(self, country: str = "kz") -> SmsNumber:
        country_id = self.COUNTRY_MAP.get(country, "2")
        data = await self._get(
            "get-number",
            country_id=country_id,
            application_id=self.TELEGRAM_APP_ID,
        )
        phone = str(data["number"]).lstrip("+")
        request_id = str(data["request_id"])
        log.info(f"SMS-Man: куплен номер +{phone} ({country}), id={request_id}")
        return SmsNumber(
            phone=phone,
            request_id=request_id,
            country=country,
            price=0,
            service="tg",
            provider=self.PROVIDER_NAME,
        )

    async def wait_for_code(
        self,
        request_id: str,
        timeout_sec: int = 120,
        poll_interval: float = 3.0,
    ) -> str:
        elapsed = 0.0
        while elapsed < timeout_sec:
            data = await self._get("get-sms", request_id=request_id)
            sms_code = data.get("sms_code")
            if sms_code and sms_code not in ("null", "None", ""):
                log.info(f"SMS-Man: получен код для {request_id}: {sms_code}")
                return str(sms_code)
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval
        raise SmsTimeoutError(
            f"SMS-Man: код не получен за {timeout_sec}с (id={request_id})"
        )

    async def cancel_number(self, request_id: str) -> None:
        await self._get("set-status", request_id=request_id, status="reject")
        log.info(f"SMS-Man: номер отменён {request_id}")

    async def request_another_sms(self, request_id: str) -> None:
        await self._get("set-status", request_id=request_id, status="retrysms")
        log.info(f"SMS-Man: запрошена повторная отправка для {request_id}")


# ═══════════════════════════════════════════════════════════════════
# 2. SmsPVA  (РЕАЛЬНЫЕ SIM, 99% доставка)
# ═══════════════════════════════════════════════════════════════════

class SmsPvaProvider(BaseSmsProvider):
    """
    SmsPVA API клиент.
    API docs: https://smspva.com/new_theme_api.html
    Base URL: https://smspva.com/priemnik.php
    Auth: apikey query param
    Telegram service: opt29
    Kazakhstan country: KZ
    """

    PROVIDER_NAME = "smspva"
    BASE_URL = "https://smspva.com/priemnik.php"

    TELEGRAM_SERVICE = "opt29"
    COUNTRY_MAP = {
        "kz": "KZ",
        "ru": "RU",
        "uz": "UZ",
        "kg": "KG",
        "in": "IN",
        "id": "ID",
        "tr": "TR",
        "us": "US",
        "uk": "UK",
    }

    async def _get(self, metod: str, **params) -> dict:
        session = await self._ensure_session()
        params["metod"] = metod
        params["apikey"] = self._api_key
        async with session.get(self.BASE_URL, params=params) as resp:
            text = await resp.text()
            if resp.status != 200:
                raise SmsApiError(f"SmsPVA HTTP {resp.status}: {text}")
            data = json.loads(text)
            response_code = data.get("response")
            if response_code == 2:
                raise SmsNoNumbersError("SmsPVA: номера недоступны")
            if response_code == 5:
                raise SmsApiError("SmsPVA: rate limit exceeded")
            if response_code not in (1, "1", None):
                raise SmsApiError(f"SmsPVA error response={response_code}: {text}")
            return data

    async def get_balance(self) -> float:
        data = await self._get("get_balance")
        return float(data.get("balance", 0))

    async def get_available_count(self, country: str = "kz") -> dict:
        country_code = self.COUNTRY_MAP.get(country, "KZ")
        try:
            data = await self._get(
                "get_count_new",
                service=self.TELEGRAM_SERVICE,
                country=country_code,
            )
            return {
                "count": data.get("online", data.get("total", 0)),
                "price": 0,
            }
        except SmsApiError as e:
            log.warning(f"SmsPVA: не удалось получить количество номеров: {e}")
            return {"count": -1, "price": 0}

    async def buy_number(self, country: str = "kz") -> SmsNumber:
        country_code = self.COUNTRY_MAP.get(country, "KZ")
        data = await self._get(
            "get_number",
            service=self.TELEGRAM_SERVICE,
            country=country_code,
        )
        phone = str(data.get("number", "")).lstrip("+")
        request_id = str(data.get("id", ""))
        if not phone or not request_id:
            raise SmsApiError(f"SmsPVA: invalid response: {data}")
        log.info(f"SmsPVA: куплен номер +{phone} ({country}), id={request_id}")
        return SmsNumber(
            phone=phone,
            request_id=request_id,
            country=country,
            price=0,
            service="tg",
            provider=self.PROVIDER_NAME,
        )

    async def wait_for_code(
        self,
        request_id: str,
        timeout_sec: int = 120,
        poll_interval: float = 5.0,   # SmsPVA рекомендует 4-5 сек
    ) -> str:
        elapsed = 0.0
        consecutive_errors = 0
        max_consecutive_errors = 5
        while elapsed < timeout_sec:
            try:
                data = await self._get(
                    "get_sms",
                    service=self.TELEGRAM_SERVICE,
                    id=request_id,
                )
                consecutive_errors = 0
                sms = data.get("sms")
                if sms and str(sms) not in ("null", "None", "", "0"):
                    # Извлечь цифровой код из SMS текста
                    code = self._extract_code(str(sms))
                    log.info(f"SmsPVA: получен код для {request_id}: {code}")
                    return code
            except SmsApiError as e:
                consecutive_errors += 1
                log.warning(
                    f"SmsPVA: ошибка при ожидании кода ({consecutive_errors}/{max_consecutive_errors}): {e}"
                )
                if consecutive_errors >= max_consecutive_errors:
                    raise SmsApiError(
                        f"SmsPVA: {max_consecutive_errors} consecutive errors while waiting for code"
                    ) from e
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval
        raise SmsTimeoutError(
            f"SmsPVA: код не получен за {timeout_sec}с (id={request_id})"
        )

    @staticmethod
    def _extract_code(sms_text: str) -> str:
        """Извлечь цифровой код из текста SMS."""
        match = re.search(r'\b(\d{4,6})\b', sms_text)
        return match.group(1) if match else sms_text

    async def cancel_number(self, request_id: str) -> None:
        await self._get("denial", service=self.TELEGRAM_SERVICE, id=request_id)
        log.info(f"SmsPVA: номер отменён {request_id}")


# ═══════════════════════════════════════════════════════════════════
# 3. 5SIM  (ДЕШЁВЫЕ, JWT AUTH)
# ═══════════════════════════════════════════════════════════════════

class FiveSimProvider(BaseSmsProvider):
    """
    5SIM API клиент.
    API docs: https://5sim.net/docs
    Base URL: https://5sim.net/v1
    Auth: Authorization: Bearer <api_key>
    Telegram product: telegram
    Kazakhstan country: kazakhstan
    """

    PROVIDER_NAME = "5sim"
    BASE_URL = "https://5sim.net/v1"

    COUNTRY_MAP = {
        "kz": "kazakhstan",
        "ru": "russia",
        "uz": "uzbekistan",
        "kg": "kyrgyzstan",
        "in": "india",
        "id": "indonesia",
        "tr": "turkey",
        "us": "usa",
        "uk": "england",
    }

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        session = await self._ensure_session()
        headers = {"Authorization": f"Bearer {self._api_key}"}
        url = f"{self.BASE_URL}{path}"
        async with session.request(method, url, headers=headers, **kwargs) as resp:
            text = await resp.text()
            if resp.status == 404:
                raise SmsNoNumbersError(f"5SIM: no numbers available (404)")
            if resp.status != 200:
                raise SmsApiError(f"5SIM HTTP {resp.status}: {text}")
            return json.loads(text)

    async def get_balance(self) -> float:
        data = await self._request("GET", "/user/profile")
        return float(data.get("balance", 0))

    async def get_available_count(self, country: str = "kz") -> dict:
        country_name = self.COUNTRY_MAP.get(country, "kazakhstan")
        try:
            data = await self._request(
                "GET", f"/guest/prices?country={country_name}&product=telegram"
            )
            # data is nested: {country: {product: {operator: {count, cost}}}}
            tg = data.get(country_name, {}).get("telegram", {})
            total_count = sum(op.get("count", 0) for op in tg.values())
            min_cost = min((op.get("cost", 999) for op in tg.values()), default=0)
            return {"count": total_count, "price": min_cost}
        except SmsApiError as e:
            log.warning(f"5SIM: не удалось получить количество номеров: {e}")
            return {"count": -1, "price": 0}

    async def buy_number(self, country: str = "kz") -> SmsNumber:
        country_name = self.COUNTRY_MAP.get(country, "kazakhstan")
        data = await self._request(
            "GET", f"/user/buy/activation/{country_name}/any/telegram"
        )
        phone = str(data.get("phone", "")).lstrip("+")
        request_id = str(data.get("id", ""))
        price = float(data.get("price", 0))
        if not phone or not request_id:
            raise SmsApiError(f"5SIM: invalid response: {data}")
        log.info(f"5SIM: куплен номер +{phone} ({country}), id={request_id}")
        return SmsNumber(
            phone=phone,
            request_id=request_id,
            country=country,
            price=price,
            service="tg",
            provider=self.PROVIDER_NAME,
        )

    async def wait_for_code(
        self,
        request_id: str,
        timeout_sec: int = 120,
        poll_interval: float = 3.0,
    ) -> str:
        elapsed = 0.0
        while elapsed < timeout_sec:
            data = await self._request("GET", f"/user/check/{request_id}")
            status = data.get("status", "")
            sms_list = data.get("sms", [])
            if status == "RECEIVED" and sms_list:
                code_text = sms_list[-1].get("code", "") or sms_list[-1].get("text", "")
                code = self._extract_code(str(code_text))
                log.info(f"5SIM: получен код для {request_id}: {code}")
                # Финализировать
                try:
                    await self._request("GET", f"/user/finish/{request_id}")
                except SmsApiError as e:
                    log.warning(f"5SIM: не удалось финализировать {request_id}: {e}")
                return code
            if status in ("CANCELED", "TIMEOUT", "BANNED"):
                raise SmsApiError(f"5SIM: activation {status}")
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval
        raise SmsTimeoutError(
            f"5SIM: код не получен за {timeout_sec}с (id={request_id})"
        )

    @staticmethod
    def _extract_code(sms_text: str) -> str:
        match = re.search(r'\b(\d{4,6})\b', sms_text)
        return match.group(1) if match else sms_text

    async def cancel_number(self, request_id: str) -> None:
        await self._request("GET", f"/user/cancel/{request_id}")
        log.info(f"5SIM: номер отменён {request_id}")


# ═══════════════════════════════════════════════════════════════════
# 4. Grizzly SMS  (TELEGRAM-ФОКУС, maxPrice, refund)
# ═══════════════════════════════════════════════════════════════════

class GrizzlySmsProvider(BaseSmsProvider):
    """
    Grizzly SMS API клиент.
    API docs: https://grizzlysms.com/docs
    Base URL: https://api.grizzlysms.com/stubs/handler_api.php
    Auth: api_key query param
    Telegram service: go
    Kazakhstan country: 2
    """

    PROVIDER_NAME = "grizzly"
    BASE_URL = "https://api.grizzlysms.com/stubs/handler_api.php"

    TELEGRAM_SERVICE = "go"
    COUNTRY_MAP = {
        "kz": "2",
        "ru": "0",
        "uz": "40",
        "kg": "68",
        "in": "22",
        "id": "6",
        "tr": "62",
        "us": "187",
        "uk": "16",
    }

    async def _get(self, **params) -> str:
        session = await self._ensure_session()
        params["api_key"] = self._api_key
        async with session.get(self.BASE_URL, params=params) as resp:
            text = await resp.text()
            if resp.status != 200:
                raise SmsApiError(f"Grizzly HTTP {resp.status}: {text}")
            if "BAD_KEY" in text:
                raise SmsApiError("Grizzly: invalid API key")
            if "NO_NUMBERS" in text:
                raise SmsNoNumbersError("Grizzly: no numbers available")
            if "NO_BALANCE" in text:
                raise SmsApiError("Grizzly: insufficient balance")
            if "ERROR" in text.upper() and "ACCESS" not in text:
                raise SmsApiError(f"Grizzly: {text}")
            return text.strip()

    async def get_balance(self) -> float:
        text = await self._get(action="getBalance")
        # Response: ACCESS_BALANCE:123.45
        if "ACCESS_BALANCE:" in text:
            return float(text.split(":")[1])
        return 0.0

    async def get_available_count(self, country: str = "kz") -> dict:
        country_code = self.COUNTRY_MAP.get(country, "2")
        try:
            text = await self._get(
                action="getNumberStatus",
                country=country_code,
            )
            data = json.loads(text)
            count = data.get(f"{self.TELEGRAM_SERVICE}_1", 0)
            return {"count": count, "price": 0}
        except (SmsApiError, json.JSONDecodeError) as e:
            log.warning(f"Grizzly: не удалось получить количество номеров: {e}")
            return {"count": -1, "price": 0}

    async def buy_number(self, country: str = "kz") -> SmsNumber:
        country_code = self.COUNTRY_MAP.get(country, "2")
        text = await self._get(
            action="getNumberV2",
            service=self.TELEGRAM_SERVICE,
            country=country_code,
            maxPrice="5",  # макс $5 за номер
        )
        # Ответ может быть JSON (V2) или ACCESS_NUMBER:id:phone
        try:
            data = json.loads(text)
            phone = str(data.get("phoneNumber", "")).lstrip("+")
            request_id = str(data.get("activationId", ""))
        except json.JSONDecodeError:
            # Формат: ACCESS_NUMBER:38496653:66846426435
            if "ACCESS_NUMBER:" not in text:
                raise SmsApiError(f"Grizzly: unexpected response: {text}")
            parts = text.split(":")
            request_id = parts[1]
            phone = parts[2]

        if not phone or not request_id:
            raise SmsApiError(f"Grizzly: invalid response: {text}")
        log.info(f"Grizzly: куплен номер +{phone} ({country}), id={request_id}")
        return SmsNumber(
            phone=phone,
            request_id=request_id,
            country=country,
            price=0,
            service="tg",
            provider=self.PROVIDER_NAME,
        )

    async def wait_for_code(
        self,
        request_id: str,
        timeout_sec: int = 120,
        poll_interval: float = 3.0,
    ) -> str:
        # Сначала отправить setStatus=1 (ожидаем SMS)
        try:
            await self._get(action="setStatus", id=request_id, status="1")
        except SmsApiError as e:
            log.warning(f"Grizzly: не удалось установить статус ожидания для {request_id}: {e}")

        elapsed = 0.0
        while elapsed < timeout_sec:
            text = await self._get(action="getStatus", id=request_id)
            # STATUS_OK:123456
            if "STATUS_OK:" in text:
                code = text.split(":")[-1].strip()
                log.info(f"Grizzly: получен код для {request_id}: {code}")
                # Финализировать (status=6 = done)
                try:
                    await self._get(action="setStatus", id=request_id, status="6")
                except SmsApiError as e:
                    log.warning(f"Grizzly: не удалось финализировать {request_id}: {e}")
                return code
            if "STATUS_CANCEL" in text:
                raise SmsApiError("Grizzly: activation cancelled")
            # STATUS_WAIT_CODE — продолжаем ждать
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval
        raise SmsTimeoutError(
            f"Grizzly: код не получен за {timeout_sec}с (id={request_id})"
        )

    async def cancel_number(self, request_id: str) -> None:
        await self._get(action="setStatus", id=request_id, status="8")
        log.info(f"Grizzly: номер отменён {request_id}")

    async def request_another_sms(self, request_id: str) -> None:
        await self._get(action="setStatus", id=request_id, status="3")
        log.info(f"Grizzly: запрошена повторная отправка для {request_id}")


# ═══════════════════════════════════════════════════════════════════
# 5. VAK-SMS  (ТЕКУЩИЙ, FALLBACK)
# ═══════════════════════════════════════════════════════════════════

class VakSmsProvider(BaseSmsProvider):
    """VAK-SMS API v1 клиент — legacy fallback."""

    PROVIDER_NAME = "vak-sms"
    BASE_URL = "https://vak-sms.com/api"

    async def _get_raw(self, endpoint: str, **params) -> dict:
        session = await self._ensure_session()
        params["apiKey"] = self._api_key
        url = f"{self.BASE_URL}/{endpoint}/"
        async with session.get(url, params=params) as resp:
            text = await resp.text()
            if resp.status != 200:
                raise SmsApiError(f"VAK-SMS HTTP {resp.status}: {text}")
            data = json.loads(text)
            if isinstance(data, str):
                if "noNumber" in data:
                    raise SmsNoNumbersError(f"VAK-SMS: {data}")
                raise SmsApiError(data)
            if isinstance(data, dict) and "error" in data:
                error = data["error"]
                if "noNumber" in error:
                    raise SmsNoNumbersError(f"VAK-SMS: {error}")
                raise SmsApiError(f"VAK-SMS: {error}")
            return data

    async def get_balance(self) -> float:
        data = await self._get_raw("getBalance")
        return float(data.get("balance", 0))

    async def get_available_count(self, country: str = "kz") -> dict:
        data = await self._get_raw(
            "getCountNumber", service="tg", country=country, price="true"
        )
        return {
            "count": data.get("tg", 0),
            "price": data.get("price", 0),
        }

    async def buy_number(self, country: str = "kz") -> SmsNumber:
        data = await self._get_raw("getNumber", service="tg", country=country)
        phone = str(data["tel"])
        request_id = data["idNum"]
        price_info = await self.get_available_count(country)
        log.info(f"VAK-SMS: куплен номер +{phone} ({country}), id={request_id}")
        return SmsNumber(
            phone=phone,
            request_id=request_id,
            country=country,
            price=price_info.get("price", 0),
            service="tg",
            provider=self.PROVIDER_NAME,
        )

    async def wait_for_code(
        self,
        request_id: str,
        timeout_sec: int = 120,
        poll_interval: float = 3.0,
    ) -> str:
        elapsed = 0.0
        while elapsed < timeout_sec:
            data = await self._get_raw("getSmsCode", idNum=request_id)
            code = data.get("smsCode")
            if code and code != "null":
                if isinstance(code, list):
                    code = code[-1]
                log.info(f"VAK-SMS: получен код для {request_id}: {code}")
                return str(code)
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval
        raise SmsTimeoutError(
            f"VAK-SMS: код не получен за {timeout_sec}с (id={request_id})"
        )

    async def request_another_sms(self, request_id: str) -> None:
        await self._get_raw("setStatus", status="send", idNum=request_id)
        log.info(f"VAK-SMS: запрошена повторная отправка для {request_id}")

    async def cancel_number(self, request_id: str) -> None:
        await self._get_raw("setStatus", status="end", idNum=request_id)
        log.info(f"VAK-SMS: номер отменён {request_id}")

    async def mark_bad(self, request_id: str) -> None:
        await self._get_raw("setStatus", status="bad", idNum=request_id)

    async def prolong_number(self, phone: str) -> SmsNumber:
        data = await self._get_raw("prolongNumber", service="tg", tel=phone)
        log.info(f"VAK-SMS: продлён номер +{phone}")
        return SmsNumber(
            phone=phone,
            request_id=data["idNum"],
            country="",
            price=0,
            service="tg",
            provider=self.PROVIDER_NAME,
        )


# ═══════════════════════════════════════════════════════════════════
# Multi-Provider Fallback Manager
# ═══════════════════════════════════════════════════════════════════

# Порядок провайдеров по надёжности для Kazakhstan + Telegram (март 2026)
DEFAULT_PROVIDER_ORDER: List[str] = [
    "sms-man",   # 195 стран, зрелый API, rental, 24/7 support
    "smspva",    # real SIM, 99% delivery rate
    "5sim",      # дешёвые, JWT, 180+ стран
    "grizzly",   # Telegram-фокус, maxPrice, refund on 2FA
    "vak-sms",   # текущий (проблемы с доставкой)
]

PROVIDER_CLASSES: dict[str, Type[BaseSmsProvider]] = {
    "sms-man": SmsManProvider,
    "smspva": SmsPvaProvider,
    "5sim": FiveSimProvider,
    "grizzly": GrizzlySmsProvider,
    "vak-sms": VakSmsProvider,
}


class MultiSmsProvider(BaseSmsProvider):
    """
    Мульти-провайдер с автоматическим fallback.

    Пробует провайдеров по порядку до успешного получения кода.
    """

    PROVIDER_NAME = "multi"

    def __init__(self, provider_keys: dict[str, str], order: Optional[List[str]] = None):
        """
        Args:
            provider_keys: {"sms-man": "key1", "smspva": "key2", ...}
            order: порядок провайдеров (по умолчанию DEFAULT_PROVIDER_ORDER)
        """
        super().__init__("")
        self._order = order or DEFAULT_PROVIDER_ORDER
        self._providers: dict[str, BaseSmsProvider] = {}

        for name in self._order:
            key = provider_keys.get(name)
            if key:
                cls = PROVIDER_CLASSES.get(name)
                if cls:
                    self._providers[name] = cls(key)
                    log.info(f"MultiSMS: инициализирован провайдер {name}")

        if not self._providers:
            raise SmsApiError("MultiSMS: ни один провайдер не сконфигурирован!")

        # Текущий активный провайдер (для buy_number -> wait_for_code)
        self._current_provider: Optional[BaseSmsProvider] = None
        self._current_provider_name: str = ""

    @property
    def active_provider(self) -> Optional[BaseSmsProvider]:
        return self._current_provider

    @property
    def active_provider_name(self) -> str:
        return self._current_provider_name

    async def get_balance(self) -> float:
        """Баланс первого доступного провайдера."""
        for name in self._order:
            provider = self._providers.get(name)
            if provider:
                try:
                    return await provider.get_balance()
                except SmsApiError:
                    continue
        return 0.0

    async def get_all_balances(self) -> dict[str, float]:
        """Балансы всех провайдеров."""
        result = {}
        for name, provider in self._providers.items():
            try:
                result[name] = await provider.get_balance()
            except SmsApiError:
                result[name] = -1.0
        return result

    async def buy_number(self, country: str = "kz") -> SmsNumber:
        """Купить номер у первого провайдера с доступными номерами."""
        errors = []
        for name in self._order:
            provider = self._providers.get(name)
            if not provider:
                continue
            try:
                log.info(f"MultiSMS: пробуем {name} для {country}...")
                number = await provider.buy_number(country)
                self._current_provider = provider
                self._current_provider_name = name
                log.info(
                    f"MultiSMS: номер куплен через {name}: +{number.phone}"
                )
                return number
            except SmsNoNumbersError as e:
                log.warning(f"MultiSMS: {name} — нет номеров: {e}")
                errors.append(f"{name}: no numbers")
            except SmsApiError as e:
                log.warning(f"MultiSMS: {name} — ошибка: {e}")
                errors.append(f"{name}: {e}")

        raise SmsNoNumbersError(
            f"MultiSMS: ни один провайдер не смог выдать номер. "
            f"Ошибки: {'; '.join(errors)}"
        )

    async def buy_number_with_code(
        self,
        country: str = "kz",
        timeout_sec: int = 120,
        poll_interval: float = 3.0,
    ) -> tuple[SmsNumber, str]:
        """
        Купить номер И дождаться кода, с полным fallback.

        Если код не приходит на номер от первого провайдера,
        отменяем и пробуем следующего.

        Returns:
            (SmsNumber, code) — успешная пара
        """
        errors = []
        for name in self._order:
            provider = self._providers.get(name)
            if not provider:
                continue
            try:
                log.info(f"MultiSMS: полный цикл через {name} для {country}...")
                number = await provider.buy_number(country)
                self._current_provider = provider
                self._current_provider_name = name
                log.info(f"MultiSMS: номер от {name}: +{number.phone}, ждём код...")

                code = await provider.wait_for_code(
                    number.request_id, timeout_sec, poll_interval
                )
                log.info(
                    f"MultiSMS: КОД ПОЛУЧЕН через {name}: "
                    f"+{number.phone} -> {code}"
                )
                return number, code

            except SmsTimeoutError:
                log.warning(f"MultiSMS: {name} — таймаут ожидания кода")
                errors.append(f"{name}: timeout")
                # Отменить номер и попробовать следующий
                try:
                    await provider.cancel_number(number.request_id)
                except Exception as e:
                    log.warning(f"MultiSMS: не удалось отменить номер {number.request_id} у {name}: {e}")
            except SmsNoNumbersError as e:
                log.warning(f"MultiSMS: {name} — нет номеров: {e}")
                errors.append(f"{name}: no numbers")
            except SmsApiError as e:
                log.warning(f"MultiSMS: {name} — ошибка: {e}")
                errors.append(f"{name}: {e}")

        raise SmsTimeoutError(
            f"MultiSMS: код не получен ни от одного провайдера. "
            f"Ошибки: {'; '.join(errors)}"
        )

    async def wait_for_code(
        self,
        request_id: str,
        timeout_sec: int = 120,
        poll_interval: float = 3.0,
    ) -> str:
        """Ждать код от текущего активного провайдера."""
        if not self._current_provider:
            raise SmsApiError("MultiSMS: нет активного провайдера (buy_number сначала)")
        return await self._current_provider.wait_for_code(
            request_id, timeout_sec, poll_interval
        )

    async def cancel_number(self, request_id: str) -> None:
        if self._current_provider:
            await self._current_provider.cancel_number(request_id)

    async def mark_bad(self, request_id: str) -> None:
        if self._current_provider:
            await self._current_provider.mark_bad(request_id)

    async def request_another_sms(self, request_id: str) -> None:
        if self._current_provider:
            await self._current_provider.request_another_sms(request_id)

    async def prolong_number(self, phone: str) -> SmsNumber:
        """Пробуем продлить через все провайдеры (VAK-SMS поддерживает)."""
        for name in self._order:
            provider = self._providers.get(name)
            if not provider:
                continue
            try:
                result = await provider.prolong_number(phone)
                self._current_provider = provider
                self._current_provider_name = name
                return result
            except SmsApiError:
                continue
        raise SmsApiError("MultiSMS: ни один провайдер не поддерживает prolongation")

    async def get_available_count(self, country: str = "kz") -> dict:
        """Суммарное количество номеров по всем провайдерам."""
        total_count = 0
        for name in self._order:
            provider = self._providers.get(name)
            if not provider:
                continue
            try:
                info = await provider.get_available_count(country)
                count = info.get("count", 0)
                if count > 0:
                    total_count += count
            except SmsApiError:
                continue
        return {"count": total_count, "price": 0}

    async def close(self):
        for provider in self._providers.values():
            await provider.close()

    async def test_all_providers(self, country: str = "kz") -> dict[str, dict]:
        """
        Тест всех провайдеров: баланс + наличие номеров.
        Возвращает словарь с результатами для каждого.
        """
        results = {}
        for name in self._order:
            provider = self._providers.get(name)
            if not provider:
                results[name] = {"status": "not_configured"}
                continue
            try:
                balance = await provider.get_balance()
                available = await provider.get_available_count(country)
                results[name] = {
                    "status": "ok",
                    "balance": balance,
                    "available_numbers": available.get("count", 0),
                    "provider_class": provider.__class__.__name__,
                }
            except SmsApiError as e:
                results[name] = {"status": "error", "error": str(e)}
        return results


# ─── Factory function ────────────────────────────────────────────

def create_sms_provider(
    provider_keys: dict[str, str],
    order: Optional[List[str]] = None,
    single_provider: Optional[str] = None,
) -> BaseSmsProvider:
    """
    Создать SMS провайдер.

    Args:
        provider_keys: {"sms-man": "key1", "smspva": "key2", "vak-sms": "key3", ...}
        order: порядок fallback
        single_provider: если указан, используем только его (без fallback)

    Returns:
        BaseSmsProvider instance
    """
    if single_provider:
        key = provider_keys.get(single_provider)
        if not key:
            raise SmsApiError(f"API key not found for provider: {single_provider}")
        cls = PROVIDER_CLASSES.get(single_provider)
        if not cls:
            raise SmsApiError(f"Unknown provider: {single_provider}")
        return cls(key)

    return MultiSmsProvider(provider_keys, order)
