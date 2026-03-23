"""
Account Factory — автоматическое создание Telegram аккаунтов.

Полный цикл: покупка номера → регистрация → профиль → session → БД.
Использует только безопасные API ID (2040, 21724).

CRITICAL (2023-02-18+): Telegram BLOCKED SMS delivery for third-party apps.
auth.sendCode from Telethon returns SentCodeTypeApp (code sent via Telegram app
push), NOT SentCodeTypeSms. Since virtual numbers from VAK-SMS/etc. have no
Telegram app installed, the code NEVER arrives.

Solution: use auth.resendCode to escalate to the next_type delivery method,
which may be SMS, call, or FragmentSms. Log the exact type chain so we can
diagnose failures. If no SMS-capable next_type exists, the number is unusable
with this API ID and we must abort early instead of wasting the timeout.

CRITICAL (2025-2026): Telegram NOW REQUIRES EMAIL verification BEFORE sending
SMS codes for new registrations. auth.sendCode returns
SentCodeTypeSetUpEmailRequired. The flow becomes:
  1. auth.sendCode → SentCodeTypeSetUpEmailRequired
  2. Create a temporary email (via mail.tm or similar)
  3. Call account.sendVerifyEmailCode with the email
  4. Wait for email with code → extract code
  5. Call account.verifyEmail → returns auth.sentCode with SMS delivery
  6. Now wait for SMS code as before

Uses core/temp_email.py (TempEmailService) to automate email step with
mail.tm (free API, no key needed) or GuerrillaMail as fallback.

Alternative pipeline: buy pre-made tdata/session accounts instead of
registering from scratch via API.
"""

from __future__ import annotations

import asyncio
import json
import random
import string
import warnings
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from telethon import TelegramClient
from telethon.errors import (
    FloodWaitError,
    PhoneNumberBannedError,
    PhoneNumberInvalidError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    SessionPasswordNeededError,
    SendCodeUnavailableError,
)
from telethon.sessions import StringSession
from telethon.tl.functions.account import (
    UpdateProfileRequest,
    SendVerifyEmailCodeRequest,
    VerifyEmailRequest,
)
from telethon.tl.functions.photos import UploadProfilePhotoRequest
from telethon.tl.functions.auth import SendCodeRequest, ResendCodeRequest
from telethon.tl import types as tl_types

from core.temp_email import TempEmailService

from config import settings
from core.sms_provider import (
    BaseSmsProvider,
    VakSmsProvider,
    MultiSmsProvider,
    SmsNumber,
    SmsApiError,
    SmsTimeoutError,
    SmsNoNumbersError,
    create_sms_provider,
)
from utils.logger import log


# ---------------------------------------------------------------------------
# Helpers: classify SentCodeType and CodeType
# ---------------------------------------------------------------------------

# SentCodeType names (from auth.sendCode / auth.resendCode result.type)
_SENT_CODE_TYPE_NAMES = {
    "SentCodeTypeSms": "SMS",
    "SentCodeTypeApp": "Telegram App Push",
    "SentCodeTypeCall": "Phone Call",
    "SentCodeTypeFlashCall": "Flash Call",
    "SentCodeTypeMissedCall": "Missed Call",
    "SentCodeTypeFragmentSms": "Fragment SMS",
    "SentCodeTypeFirebaseSms": "Firebase SMS (official apps only)",
    "SentCodeTypeSmsWord": "SMS Word",
    "SentCodeTypeSmsPhrase": "SMS Phrase",
    "SentCodeTypeEmailCode": "Email",
    "SentCodeTypeSetUpEmailRequired": "Email Setup Required",
}

# Types that deliver codes to the SMS service (VAK-SMS will receive these)
_SMS_RECEIVABLE_TYPES = {
    "SentCodeTypeSms",
    "SentCodeTypeSmsWord",
    "SentCodeTypeSmsPhrase",
    "SentCodeTypeCall",
}

# Types that will NEVER arrive at SMS service
_NON_SMS_TYPES = {
    "SentCodeTypeApp",
    "SentCodeTypeFirebaseSms",
    "SentCodeTypeEmailCode",
    "SentCodeTypeSetUpEmailRequired",
}

# Fragment SMS — requires receiving code via fragment.com, not regular SMS
_FRAGMENT_SMS_TYPE = "SentCodeTypeFragmentSms"


def _get_type_name(obj) -> str:
    """Get the class name of a SentCodeType / CodeType object."""
    return type(obj).__name__ if obj else "None"


def _is_sms_receivable(obj) -> bool:
    """Check if this SentCodeType will deliver code via SMS."""
    return _get_type_name(obj) in _SMS_RECEIVABLE_TYPES


def _is_email_setup_required(obj) -> bool:
    """Check if Telegram requires email setup before SMS delivery."""
    return _get_type_name(obj) == "SentCodeTypeSetUpEmailRequired"


def _is_fragment_sms(obj) -> bool:
    """Check if this SentCodeType is Fragment SMS (requires fragment.com)."""
    return _get_type_name(obj) == _FRAGMENT_SMS_TYPE


def _is_word_or_phrase_type(obj) -> bool:
    """Check if this SentCodeType is SmsWord or SmsPhrase (non-digit code)."""
    return _get_type_name(obj) in {"SentCodeTypeSmsWord", "SentCodeTypeSmsPhrase"}


def _describe_type(obj) -> str:
    """Human-readable description of code delivery type."""
    name = _get_type_name(obj)
    return _SENT_CODE_TYPE_NAMES.get(name, name)


def _extract_word_or_phrase_from_sms(
    sms_text: str, sent_code_type_obj, beginning_hint: str
) -> str:
    """
    Extract the secret word or phrase from an SMS message for
    SentCodeTypeSmsWord / SentCodeTypeSmsPhrase.

    The SMS text typically contains the word/phrase embedded in a sentence.
    The ``beginning`` field on the SentCodeType gives the first letter (word)
    or first word (phrase) as a hint.

    Heuristic: find the token (or substring) that starts with the hint and
    return it.  If no match, return the full SMS text stripped — the caller
    should try it as-is.
    """
    type_name = _get_type_name(sent_code_type_obj)
    sms_text = sms_text.strip()

    if not beginning_hint:
        # No hint provided — return full text and hope for the best
        log.warning(
            f"[Factory] {type_name}: нет beginning-хинта, "
            f"возвращаю полный текст SMS"
        )
        return sms_text

    if type_name == "SentCodeTypeSmsWord":
        # beginning = first letter of the word
        # Find longest single word that starts with that letter
        words = sms_text.split()
        candidates = [
            w for w in words
            if w.lower().startswith(beginning_hint.lower())
        ]
        if candidates:
            # Prefer the longest candidate (the secret word is usually
            # the most distinctive token)
            chosen = max(candidates, key=len)
            log.info(
                f"[Factory] SmsWord: hint='{beginning_hint}', "
                f"extracted='{chosen}' from SMS"
            )
            return chosen

    elif type_name == "SentCodeTypeSmsPhrase":
        # beginning = first word of the phrase
        # The phrase is usually after a colon or in quotes
        lower_text = sms_text.lower()
        hint_lower = beginning_hint.lower()

        # Try to find the hint word and grab everything from it to
        # the end of line / next punctuation
        idx = lower_text.find(hint_lower)
        if idx >= 0:
            phrase_start = sms_text[idx:]
            # Trim at common delimiters
            for delim in ["\n", ".", "!", ";"]:
                pos = phrase_start.find(delim)
                if pos > 0:
                    phrase_start = phrase_start[:pos]
            chosen = phrase_start.strip()
            log.info(
                f"[Factory] SmsPhrase: hint='{beginning_hint}', "
                f"extracted='{chosen}' from SMS"
            )
            return chosen

    log.warning(
        f"[Factory] {type_name}: не удалось извлечь "
        f"слово/фразу (hint='{beginning_hint}'), "
        f"возвращаю полный текст SMS"
    )
    return sms_text


# Безопасные API ID и их fingerprints
SAFE_PROFILES = {
    2040: {
        "name": "Telegram Desktop",
        "devices": [
            {"device": "Desktop", "sdk": "Windows 10", "app_version": "4.16.8 x64"},
            {"device": "Desktop", "sdk": "Windows 11", "app_version": "4.16.5 x64"},
            {"device": "Desktop", "sdk": "macOS 14.3", "app_version": "4.16.8"},
            {"device": "Desktop", "sdk": "Ubuntu 22.04", "app_version": "4.16.8 x64"},
        ],
        "api_hash": "b18441a1ff607e10a989891a5462e627",
    },
    21724: {
        "name": "AndroidX",
        "devices": [
            {"device": "Samsung Galaxy S24", "sdk": "SDK 34", "app_version": "10.14.5"},
            {"device": "Samsung Galaxy S23", "sdk": "SDK 33", "app_version": "10.14.5"},
            {"device": "Google Pixel 8", "sdk": "SDK 34", "app_version": "10.14.5"},
            {"device": "Xiaomi 14", "sdk": "SDK 34", "app_version": "10.14.5"},
            {"device": "OnePlus 12", "sdk": "SDK 34", "app_version": "10.14.5"},
        ],
        "api_hash": "3e0cb5efcd52300aec5994fdfc5bdc16",
    },
}

# Русские имена для профилей
FIRST_NAMES_M = [
    "Александр", "Дмитрий", "Максим", "Артём", "Иван",
    "Кирилл", "Михаил", "Даниил", "Егор", "Андрей",
    "Никита", "Илья", "Алексей", "Тимур", "Роман",
    "Владислав", "Сергей", "Матвей", "Павел", "Марк",
]
FIRST_NAMES_F = [
    "Анна", "Мария", "Екатерина", "Дарья", "Алина",
    "Полина", "Виктория", "Софья", "Анастасия", "Елена",
    "Ольга", "Ксения", "Юлия", "Татьяна", "Наталья",
    "Ирина", "Арина", "Валерия", "Кристина", "Диана",
]
LAST_NAMES_M = [
    "Иванов", "Смирнов", "Кузнецов", "Попов", "Васильев",
    "Петров", "Соколов", "Михайлов", "Новиков", "Фёдоров",
    "Морозов", "Волков", "Алексеев", "Лебедев", "Семёнов",
]
LAST_NAMES_F = [
    "Иванова", "Смирнова", "Кузнецова", "Попова", "Васильева",
    "Петрова", "Соколова", "Михайлова", "Новикова", "Фёдорова",
    "Морозова", "Волкова", "Алексеева", "Лебедева", "Семёнова",
]


@dataclass
class FactoryResult:
    """Результат создания аккаунта."""
    success: bool
    phone: str = ""
    session_file: str = ""
    metadata_file: str = ""
    error: str = ""
    cost_rub: float = 0
    api_id: int = 0
    device_profile: dict = field(default_factory=dict)


class AccountFactory:
    """Фабрика создания Telegram аккаунтов."""

    def __init__(
        self,
        sms_api_key: str = "",
        user_id: int = 1,
        sms_provider: Optional[BaseSmsProvider] = None,
        sms_provider_keys: Optional[dict] = None,
    ):
        # Multi-provider: pass sms_provider_keys={"sms-man": "key", "smspva": "key", ...}
        # Single provider: pass sms_api_key (legacy VAK-SMS)
        # Direct provider: pass sms_provider instance
        if sms_provider:
            self.sms = sms_provider
        elif sms_provider_keys:
            self.sms = create_sms_provider(sms_provider_keys)
        else:
            self.sms = VakSmsProvider(sms_api_key)
        self.user_id = user_id
        self._sessions_dir = Path(settings.SESSIONS_DIR) / str(user_id)
        self._sessions_dir.mkdir(parents=True, exist_ok=True)
        # Email verification service for Telegram's email-before-SMS requirement (2025-2026)
        self._email_svc = TempEmailService()

    def _generate_fingerprint(self, api_id: int) -> dict:
        """Сгенерировать реалистичный device fingerprint."""
        profile = SAFE_PROFILES[api_id]
        device = random.choice(profile["devices"])
        return {
            "device": device["device"],
            "sdk": device["sdk"],
            "app_version": device["app_version"],
            "lang_pack": "ru",
            "system_lang_pack": "ru",
            "app_id": api_id,
            "app_hash": profile["api_hash"],
        }

    def _generate_name(self) -> tuple[str, str]:
        """Сгенерировать случайное русское имя."""
        if random.random() < 0.5:
            return random.choice(FIRST_NAMES_M), random.choice(LAST_NAMES_M)
        return random.choice(FIRST_NAMES_F), random.choice(LAST_NAMES_F)

    def _generate_2fa_password(self) -> str:
        """Сгенерировать 2FA пароль."""
        chars = string.ascii_letters + string.digits + "!@#$%"
        return "".join(random.choices(chars, k=16))

    async def create_account(
        self,
        country: str = "kz",
        api_id: int = 2040,
        proxy_config: Optional[dict] = None,
    ) -> FactoryResult:
        """
        Создать один Telegram аккаунт.

        Шаги:
        1. Купить номер через SMS API
        2. Создать Telethon клиент с safe API ID
        3. Отправить код авторизации
        4. Получить SMS код через API
        5. Зарегистрировать аккаунт
        6. Установить профиль (имя)
        7. Сохранить session + metadata JSON
        8. Создать StringSession backup
        """
        if api_id not in SAFE_PROFILES:
            return FactoryResult(success=False, error=f"Unsafe API ID: {api_id}. Use 2040 or 21724.")

        sms_number: Optional[SmsNumber] = None
        client: Optional[TelegramClient] = None

        try:
            # --- Step 1: Купить номер ---
            log.info(f"[Factory] Шаг 1/7: покупаю номер ({country})...")
            sms_number = await self.sms.buy_number(country)
            phone = sms_number.phone
            log.info(f"[Factory] Номер: +{phone}")

            # Human-like delay
            await asyncio.sleep(random.uniform(2, 5))

            # --- Step 2: Создать клиент ---
            log.info(f"[Factory] Шаг 2/7: создаю клиент (API ID {api_id})...")
            fingerprint = self._generate_fingerprint(api_id)
            session_path = self._sessions_dir / phone

            proxy_tuple = None
            if proxy_config:
                proxy_tuple = (
                    proxy_config.get("type", "socks5"),
                    proxy_config["host"],
                    proxy_config["port"],
                    True,  # rdns
                    proxy_config.get("username"),
                    proxy_config.get("password"),
                )

            client = TelegramClient(
                str(session_path),
                api_id=api_id,
                api_hash=fingerprint["app_hash"],
                proxy=proxy_tuple,
                device_model=fingerprint["device"],
                system_version=fingerprint["sdk"],
                app_version=fingerprint["app_version"],
                lang_code="ru",
                system_lang_code="ru",
                flood_sleep_threshold=0,
                timeout=30,
                connection_retries=3,
            )

            await client.connect()
            log.info(f"[Factory] Клиент подключен")

            # --- Step 3: Отправить код (с умной обработкой типа доставки) ---
            #
            # CRITICAL CONTEXT (Feb 2023+):
            # Telegram BLOCKED SMS for third-party apps. auth.sendCode now
            # returns SentCodeTypeApp (push to Telegram app) for most cases.
            # Virtual numbers have no Telegram app → code never arrives.
            #
            # Solution: check sent_code.type, and if it's NOT SMS-receivable,
            # use auth.resendCode to escalate to next_type (which may be SMS).
            # If resend also returns non-SMS type, the number is unusable.
            #
            log.info(f"[Factory] Шаг 3/7: отправляю код на +{phone}...")

            # Step 3a: Initial SendCode
            sent_code = await client.send_code_request(f"+{phone}")
            phone_code_hash = sent_code.phone_code_hash
            initial_type = _get_type_name(sent_code.type)
            next_type_obj = getattr(sent_code, 'next_type', None)
            timeout_sec = getattr(sent_code, 'timeout', None)

            log.info(
                f"[Factory] Код отправлен: type={_describe_type(sent_code.type)}, "
                f"next_type={_describe_type(next_type_obj)}, "
                f"timeout={timeout_sec}s, "
                f"hash={phone_code_hash[:8]}..."
            )

            # Step 3b: Handle Email Setup Required (NEW — 2025-2026)
            # Telegram now requires email verification BEFORE sending SMS
            if _is_email_setup_required(sent_code.type):
                log.info(
                    f"[Factory] Telegram требует email перед SMS! "
                    f"Запускаю автоматическую верификацию email..."
                )

                try:
                    # Step 3b-1: Create temporary email
                    email_addr, email_session = await self._email_svc.create_email()
                    log.info(f"[Factory] Temp email создан: {email_addr}")

                    await asyncio.sleep(random.uniform(2, 4))

                    # Step 3b-2: Send verify email code to Telegram
                    log.info(f"[Factory] Отправляю email {email_addr} в Telegram...")

                    # Use Telethon raw API for email verification
                    email_code_result = await client(
                        SendVerifyEmailCodeRequest(
                            purpose=tl_types.EmailVerifyPurposeLoginSetup(
                                phone_number=f"+{phone}",
                                phone_code_hash=phone_code_hash,
                            ),
                            email=email_addr,
                        )
                    )
                    email_code_length = getattr(email_code_result, 'length', 6)
                    log.info(
                        f"[Factory] Telegram принял email, ожидаем код "
                        f"(длина: {email_code_length})..."
                    )

                    # Step 3b-3: Wait for the verification code in email
                    email_code = await self._email_svc.wait_for_telegram_code(
                        email_session,
                        timeout=120,
                        poll_interval=3.0,
                    )
                    log.info(f"[Factory] Email код получен: {email_code}")

                    await asyncio.sleep(random.uniform(2, 5))

                    # Step 3b-4: Verify email with Telegram
                    log.info(f"[Factory] Верифицирую email код в Telegram...")
                    verify_result = await client(
                        VerifyEmailRequest(
                            purpose=tl_types.EmailVerifyPurposeLoginSetup(
                                phone_number=f"+{phone}",
                                phone_code_hash=phone_code_hash,
                            ),
                            verification=tl_types.EmailVerificationCode(
                                code=email_code,
                            ),
                        )
                    )

                    # After email verification, Telegram returns EmailVerifiedLogin
                    # (with sent_code containing SMS delivery) or EmailVerified (no sent_code).
                    # For LoginSetup purpose, it should always be EmailVerifiedLogin.
                    verify_type_name = type(verify_result).__name__
                    if hasattr(verify_result, 'sent_code') and verify_result.sent_code is not None:
                        sent_code = verify_result.sent_code
                        phone_code_hash = sent_code.phone_code_hash
                        next_type_obj = getattr(sent_code, 'next_type', None)
                        timeout_sec = getattr(sent_code, 'timeout', None)
                        log.info(
                            f"[Factory] Email верифицирован ({verify_type_name})! "
                            f"Новый тип доставки: {_describe_type(sent_code.type)}, "
                            f"next_type={_describe_type(next_type_obj)}"
                        )
                    else:
                        # EmailVerified without sent_code — unexpected for LoginSetup.
                        # Re-send auth code to trigger SMS delivery after email is verified.
                        log.warning(
                            f"[Factory] Email верифицирован ({verify_type_name}), "
                            f"но sent_code отсутствует. Повторяю auth.sendCode..."
                        )
                        await asyncio.sleep(random.uniform(2, 4))
                        sent_code = await client.send_code_request(f"+{phone}")
                        phone_code_hash = sent_code.phone_code_hash
                        next_type_obj = getattr(sent_code, 'next_type', None)
                        timeout_sec = getattr(sent_code, 'timeout', None)
                        log.info(
                            f"[Factory] Повторный sendCode после email: "
                            f"type={_describe_type(sent_code.type)}, "
                            f"next_type={_describe_type(next_type_obj)}"
                        )

                except TimeoutError as e:
                    log.error(
                        f"[Factory] Email код не пришёл вовремя: {e}"
                    )
                    await self.sms.cancel_number(sms_number.request_id)
                    return FactoryResult(
                        success=False,
                        phone=phone,
                        error=f"email_code_timeout: {e}",
                        cost_rub=0,
                    )
                except Exception as e:
                    log.error(
                        f"[Factory] Email верификация провалилась: {e}",
                        exc_info=True,
                    )
                    await self.sms.cancel_number(sms_number.request_id)
                    return FactoryResult(
                        success=False,
                        phone=phone,
                        error=f"email_verification_failed: {e}",
                        cost_rub=0,
                    )

            # Step 3c: If code was sent via App push (NOT SMS), try resend
            if not _is_sms_receivable(sent_code.type) and not _is_email_setup_required(sent_code.type):
                log.warning(
                    f"[Factory] Код отправлен через {_describe_type(sent_code.type)}, "
                    f"а НЕ SMS! Виртуальный номер не получит код."
                )

                # Step 3c-0: Fragment SMS is a dead end — requires fragment.com
                if _is_fragment_sms(sent_code.type):
                    fragment_url = getattr(sent_code.type, 'url', 'https://fragment.com')
                    log.warning(
                        f"[Factory] Fragment SMS: код отправлен через fragment.com "
                        f"({fragment_url}). Этот метод не поддерживается автоматически. "
                        f"Необходимо получить код вручную на fragment.com или "
                        f"использовать другой API ID."
                    )
                    await self.sms.cancel_number(sms_number.request_id)
                    return FactoryResult(
                        success=False,
                        phone=phone,
                        error=(
                            f"fragment_sms_unsupported: code sent via Fragment SMS "
                            f"({fragment_url}). Cannot be received automatically. "
                            f"Use a different API ID or buy pre-made accounts."
                        ),
                        cost_rub=0,
                    )

                if next_type_obj is not None:
                    # Wait for the timeout before resending (Telegram requires this)
                    wait_time = min(timeout_sec or 60, 120)
                    log.info(
                        f"[Factory] Ждём {wait_time}с перед auth.resendCode "
                        f"(next_type={_describe_type(next_type_obj)})..."
                    )
                    await asyncio.sleep(wait_time)

                    try:
                        resend_result = await client(
                            ResendCodeRequest(f"+{phone}", phone_code_hash)
                        )
                        phone_code_hash = resend_result.phone_code_hash
                        sent_code = resend_result  # Update sent_code for Step 4
                        resend_type = _get_type_name(resend_result.type)
                        resend_next = getattr(resend_result, 'next_type', None)

                        log.info(
                            f"[Factory] Resend результат: type={_describe_type(resend_result.type)}, "
                            f"next_type={_describe_type(resend_next)}"
                        )

                        # Fragment SMS after resend — dead end
                        if _is_fragment_sms(resend_result.type):
                            fragment_url = getattr(resend_result.type, 'url', 'https://fragment.com')
                            log.warning(
                                f"[Factory] Resend вернул Fragment SMS "
                                f"({fragment_url}). Не поддерживается автоматически."
                            )
                            await self.sms.cancel_number(sms_number.request_id)
                            return FactoryResult(
                                success=False,
                                phone=phone,
                                error=(
                                    f"fragment_sms_unsupported: resend escalated to "
                                    f"Fragment SMS ({fragment_url}). Cannot be "
                                    f"received automatically."
                                ),
                                cost_rub=0,
                            )

                        if not _is_sms_receivable(resend_result.type):
                            # Even after resend, code is NOT going via SMS
                            # Try one more resend if there's another next_type
                            if resend_next is not None:
                                resend_timeout = getattr(resend_result, 'timeout', 60)
                                wait_time2 = min(resend_timeout or 60, 120)
                                log.info(
                                    f"[Factory] 2-й resend через {wait_time2}с "
                                    f"(next_type={_describe_type(resend_next)})..."
                                )
                                await asyncio.sleep(wait_time2)

                                try:
                                    resend2 = await client(
                                        ResendCodeRequest(f"+{phone}", phone_code_hash)
                                    )
                                    phone_code_hash = resend2.phone_code_hash
                                    sent_code = resend2  # Update sent_code for Step 4
                                    log.info(
                                        f"[Factory] 2-й resend: type={_describe_type(resend2.type)}"
                                    )

                                    # Fragment SMS after 2nd resend — dead end
                                    if _is_fragment_sms(resend2.type):
                                        fragment_url = getattr(resend2.type, 'url', 'https://fragment.com')
                                        log.warning(
                                            f"[Factory] 2-й resend вернул Fragment SMS "
                                            f"({fragment_url}). Не поддерживается."
                                        )
                                        await self.sms.cancel_number(sms_number.request_id)
                                        return FactoryResult(
                                            success=False,
                                            phone=phone,
                                            error=(
                                                f"fragment_sms_unsupported: 2nd resend "
                                                f"escalated to Fragment SMS. Cannot be "
                                                f"received automatically."
                                            ),
                                            cost_rub=0,
                                        )

                                    if not _is_sms_receivable(resend2.type):
                                        log.error(
                                            f"[Factory] Telegram отказывается слать SMS. "
                                            f"Финальный тип: {_describe_type(resend2.type)}. "
                                            f"Этот API ID ({api_id}) не получит SMS для "
                                            f"third-party клиентов. Попробуйте: "
                                            f"1) Написать sms@telegram.org с #enableSMS, "
                                            f"2) Покупать готовые tdata/session аккаунты."
                                        )
                                        await self.sms.cancel_number(sms_number.request_id)
                                        return FactoryResult(
                                            success=False,
                                            phone=phone,
                                            error=(
                                                f"sms_blocked_by_telegram: code sent via "
                                                f"{_describe_type(resend2.type)}, not SMS. "
                                                f"Third-party apps cannot receive SMS since "
                                                f"Feb 2023. Contact sms@telegram.org with "
                                                f"#enableSMS or buy pre-made accounts."
                                            ),
                                            cost_rub=0,  # cancelled, should refund
                                        )
                                except SendCodeUnavailableError:
                                    log.error(
                                        f"[Factory] SEND_CODE_UNAVAILABLE на 2-м resend. "
                                        f"Все методы доставки исчерпаны."
                                    )
                                    await self.sms.cancel_number(sms_number.request_id)
                                    return FactoryResult(
                                        success=False,
                                        phone=phone,
                                        error="send_code_unavailable: all delivery methods exhausted",
                                        cost_rub=0,
                                    )
                            else:
                                log.error(
                                    f"[Factory] Resend вернул {_describe_type(resend_result.type)} "
                                    f"(не SMS), и нет next_type для следующей попытки."
                                )
                                await self.sms.cancel_number(sms_number.request_id)
                                return FactoryResult(
                                    success=False,
                                    phone=phone,
                                    error=(
                                        f"sms_blocked_by_telegram: resend delivered via "
                                        f"{_describe_type(resend_result.type)}, no SMS fallback. "
                                        f"Third-party apps blocked since Feb 2023."
                                    ),
                                    cost_rub=0,
                                )
                        else:
                            log.info(
                                f"[Factory] Resend переключил на SMS! "
                                f"Тип: {_describe_type(resend_result.type)}"
                            )
                    except SendCodeUnavailableError:
                        log.error(
                            f"[Factory] SEND_CODE_UNAVAILABLE: Telegram заблокировал "
                            f"все методы доставки кода для этого номера/API ID."
                        )
                        await self.sms.cancel_number(sms_number.request_id)
                        return FactoryResult(
                            success=False,
                            phone=phone,
                            error=(
                                "send_code_unavailable: Telegram blocked all delivery "
                                "methods for third-party apps. SMS disabled since Feb 2023."
                            ),
                            cost_rub=0,
                        )
                else:
                    # No next_type at all — code only goes to the app
                    log.error(
                        f"[Factory] Код отправлен через {_describe_type(sent_code.type)}, "
                        f"next_type=None. Нет способа получить SMS. "
                        f"Telegram заблокировал SMS для third-party приложений (Feb 2023+)."
                    )
                    await self.sms.cancel_number(sms_number.request_id)
                    return FactoryResult(
                        success=False,
                        phone=phone,
                        error=(
                            f"sms_not_available: code sent via "
                            f"{_describe_type(sent_code.type)}, no SMS fallback. "
                            f"Third-party SMS blocked since Feb 2023."
                        ),
                        cost_rub=0,
                    )
            else:
                log.info(
                    f"[Factory] Код отправлен через SMS ({_describe_type(sent_code.type)})"
                )

            # --- Step 4: Получить SMS код ---
            # Determine the final delivery type for word/phrase extraction
            final_type = sent_code.type

            if _is_word_or_phrase_type(final_type):
                # SentCodeTypeSmsWord: SMS contains a secret word
                #   - has `beginning` field with first letter as hint
                # SentCodeTypeSmsPhrase: SMS contains a secret phrase
                #   - has `beginning` field with first word as hint
                beginning_hint = getattr(final_type, 'beginning', '') or ''
                type_label = _get_type_name(final_type)
                log.info(
                    f"[Factory] Шаг 4/7: жду SMS с секретным "
                    f"{'словом' if type_label == 'SentCodeTypeSmsWord' else 'фразой'}... "
                    f"(beginning hint: '{beginning_hint}')"
                )
                raw_sms = await self.sms.wait_for_code(
                    sms_number.request_id, timeout_sec=120
                )
                log.info(f"[Factory] Сырой SMS текст: '{raw_sms}'")
                code = _extract_word_or_phrase_from_sms(
                    raw_sms, final_type, beginning_hint
                )
                log.info(
                    f"[Factory] Извлечённый код ({type_label}): '{code}'"
                )
            else:
                log.info(f"[Factory] Шаг 4/7: жду SMS код...")
                code = await self.sms.wait_for_code(
                    sms_number.request_id, timeout_sec=120
                )
                log.info(f"[Factory] Код получен: {code}")

            await asyncio.sleep(random.uniform(3, 7))  # Human-like pause

            # --- Step 5: Зарегистрировать ---
            log.info(f"[Factory] Шаг 5/7: регистрирую аккаунт...")
            first_name, last_name = self._generate_name()

            try:
                # Попробовать sign_in (если номер уже зарегистрирован)
                await client.sign_in(
                    phone=f"+{phone}",
                    code=code,
                    phone_code_hash=phone_code_hash,
                )
                log.info(f"[Factory] Вход в существующий аккаунт +{phone}")
            except SessionPasswordNeededError:
                log.warning(f"[Factory] Аккаунт +{phone} требует 2FA — пропускаем")
                return FactoryResult(
                    success=False,
                    phone=phone,
                    error="2FA required on existing account",
                    cost_rub=sms_number.price,
                )
            except Exception:
                # Новый аккаунт — sign_up
                await client.sign_up(
                    code=code,
                    first_name=first_name,
                    last_name=last_name,
                    phone=f"+{phone}",
                    phone_code_hash=phone_code_hash,
                )
                log.info(f"[Factory] Новый аккаунт создан: {first_name} {last_name}")

            await asyncio.sleep(random.uniform(2, 5))

            # --- Step 6: Обновить профиль ---
            log.info(f"[Factory] Шаг 6/7: обновляю профиль...")
            try:
                await client(UpdateProfileRequest(
                    first_name=first_name,
                    last_name=last_name,
                ))
            except Exception as e:
                log.warning(f"[Factory] Не удалось обновить профиль: {e}")

            # --- Step 7: Сохранить файлы ---
            log.info(f"[Factory] Шаг 7/7: сохраняю session + metadata...")

            # Metadata JSON (формат совместимый с SessionManager)
            metadata = {
                "phone": phone,
                "first_name": first_name,
                "last_name": last_name,
                "device": fingerprint["device"],
                "sdk": fingerprint["sdk"],
                "app_version": fingerprint["app_version"],
                "lang_pack": "ru",
                "system_lang_pack": "ru",
                "app_id": api_id,
                "app_hash": fingerprint["app_hash"],
                "twoFA": "",
                "country": country,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "created_by": "account_factory",
                "sms_provider": getattr(self.sms, 'PROVIDER_NAME', 'unknown'),
                "sms_request_id": sms_number.request_id,
                "cost_rub": sms_number.price,
            }

            json_path = self._sessions_dir / f"{phone}.json"
            json_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")

            # StringSession backup
            string_session = StringSession.save(client.session)
            backup_dir = Path("data/session_backups")
            backup_dir.mkdir(parents=True, exist_ok=True)
            (backup_dir / f"{phone}.backup").write_text(string_session, encoding="utf-8")

            # Disconnect (НЕ log_out!)
            await client.disconnect()
            client = None

            session_file = f"{phone}.session"
            log.info(f"[Factory] ✅ Аккаунт +{phone} создан! Session: {session_file}")

            return FactoryResult(
                success=True,
                phone=phone,
                session_file=session_file,
                metadata_file=f"{phone}.json",
                cost_rub=sms_number.price,
                api_id=api_id,
                device_profile=fingerprint,
            )

        except SendCodeUnavailableError:
            log.error(
                f"[Factory] SEND_CODE_UNAVAILABLE: Telegram не позволяет "
                f"отправить код через SMS для third-party приложений. "
                f"Решение: sms@telegram.org #enableSMS или готовые аккаунты."
            )
            if sms_number:
                await self.sms.cancel_number(sms_number.request_id)
            return FactoryResult(
                success=False,
                phone=sms_number.phone if sms_number else "",
                error="send_code_unavailable: SMS blocked for third-party apps since Feb 2023",
                cost_rub=0,
            )

        except PhoneNumberBannedError:
            log.error(f"[Factory] Номер забанен Telegram")
            if sms_number:
                await self.sms.mark_bad(sms_number.request_id)
            return FactoryResult(success=False, phone=sms_number.phone if sms_number else "", error="phone_banned")

        except PhoneNumberInvalidError:
            log.error(f"[Factory] Невалидный номер")
            if sms_number:
                await self.sms.cancel_number(sms_number.request_id)
            return FactoryResult(success=False, error="phone_invalid")

        except (PhoneCodeExpiredError, PhoneCodeInvalidError) as e:
            log.error(f"[Factory] Ошибка кода: {e}")
            return FactoryResult(
                success=False,
                phone=sms_number.phone if sms_number else "",
                error=f"code_error: {e}",
                cost_rub=sms_number.price if sms_number else 0,
            )

        except SmsTimeoutError:
            log.error(f"[Factory] SMS не пришёл, отменяю номер")
            if sms_number:
                await self.sms.cancel_number(sms_number.request_id)
            return FactoryResult(success=False, error="sms_timeout")

        except SmsApiError as e:
            log.error(f"[Factory] Ошибка SMS API: {e}")
            return FactoryResult(success=False, error=f"sms_api: {e}")

        except FloodWaitError as e:
            log.error(f"[Factory] FloodWait: {e.seconds}с")
            return FactoryResult(
                success=False,
                phone=sms_number.phone if sms_number else "",
                error=f"flood_wait_{e.seconds}s",
                cost_rub=sms_number.price if sms_number else 0,
            )

        except Exception as e:
            log.error(f"[Factory] Неожиданная ошибка: {e}", exc_info=True)
            return FactoryResult(
                success=False,
                phone=sms_number.phone if sms_number else "",
                error=str(e),
                cost_rub=sms_number.price if sms_number else 0,
            )

        finally:
            if client and client.is_connected():
                await client.disconnect()

    async def create_batch(
        self,
        count: int,
        country: str = "kz",
        api_id: int = 2040,
        delay_between: tuple[float, float] = (300, 900),
    ) -> list[FactoryResult]:
        """
        Создать пачку аккаунтов с задержками.
        delay_between: мин/макс секунд между регистрациями (5-15 мин по умолчанию).
        """
        results = []
        for i in range(count):
            log.info(f"[Factory] === Создаю аккаунт {i + 1}/{count} ===")
            result = await self.create_account(country=country, api_id=api_id)
            results.append(result)

            if result.success:
                log.info(f"[Factory] ✅ {i + 1}/{count}: +{result.phone}")
            else:
                log.warning(f"[Factory] ❌ {i + 1}/{count}: {result.error}")

            # Задержка между регистрациями (кроме последнего)
            if i < count - 1:
                delay = random.uniform(*delay_between)
                log.info(f"[Factory] Пауза {delay:.0f}с перед следующим...")
                await asyncio.sleep(delay)

        success_count = sum(1 for r in results if r.success)
        log.info(f"[Factory] Итог: {success_count}/{count} успешно")
        return results

    async def register_in_db(self, result: FactoryResult) -> Optional[int]:
        """Зарегистрировать созданный аккаунт в БД."""
        if not result.success:
            return None

        from storage.sqlite_db import async_session
        from storage.models import Account

        async with async_session() as session:
            account = Account(
                phone=result.phone,
                session_file=result.session_file,
                user_id=self.user_id,
                status="active",
                health_status="alive",
                lifecycle_stage="warming_up",
                api_id=result.api_id,
                account_age_days=0,
                days_active=0,
                warmup_phase="STEALTH",
                warmup_day=0,
                warmup_mode="conservative",
            )
            session.add(account)
            await session.commit()
            await session.refresh(account)
            log.info(f"[Factory] Аккаунт +{result.phone} добавлен в БД (id={account.id})")
            return account.id

    async def check_status(self) -> dict:
        """Проверить статус SMS-сервиса."""
        balance = await self.sms.get_balance()
        kz = await self.sms.get_available_count("kz")
        uz = await self.sms.get_available_count("uz")
        return {
            "balance_rub": balance,
            "kz_available": kz["count"],
            "kz_price": kz["price"],
            "uz_available": uz["count"],
            "uz_price": uz["price"],
            "accounts_affordable": int(balance / max(kz["price"], 1)) if kz["price"] else 0,
        }

    async def close(self):
        await self.sms.close()
        await self._email_svc.close()
