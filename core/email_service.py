"""
Sprint 14 — Email Notification Service.

Provides async fire-and-forget email delivery using smtplib in a thread
executor (no additional async SMTP dependency required). Falls back to
structured log output when SMTP_ENABLED is False.

Usage:
    from core.email_service import send_email, send_template

    # fire and forget — never await if you want non-blocking
    asyncio.create_task(send_template("welcome", to="user@example.com", name="Иван"))
"""
from __future__ import annotations

import asyncio
import email.mime.text
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

from config import settings

log = logging.getLogger("uvicorn.error")


# ---------------------------------------------------------------------------
# Email templates (plain text, Russian)
# ---------------------------------------------------------------------------


def _render_template(template: str, **ctx: Any) -> tuple[str, str]:
    """Return (subject, body) for the given template name and context."""
    product = "NEURO COMMENTING"

    if template == "welcome":
        name = ctx.get("name", "")
        subject = f"Добро пожаловать в {product}!"
        body = (
            f"Привет{', ' + name if name else ''}!\n\n"
            f"Ваш аккаунт в {product} успешно создан.\n\n"
            "Начните прямо сейчас:\n"
            "1. Активируйте пробный период (3 дня бесплатно)\n"
            "2. Загрузите Telegram-аккаунты\n"
            "3. Создайте первую ферму комментариев\n\n"
            "Если у вас есть вопросы — просто ответьте на это письмо.\n\n"
            f"С уважением,\nКоманда {product}"
        )

    elif template == "trial_started":
        name = ctx.get("name", "")
        trial_days = ctx.get("trial_days", 3)
        plan_name = ctx.get("plan_name", "Starter")
        subject = f"Ваш пробный период {plan_name} активирован — {trial_days} дня бесплатно"
        body = (
            f"Привет{', ' + name if name else ''}!\n\n"
            f"Ваш бесплатный пробный период ({trial_days} дня) на план {plan_name} активирован.\n\n"
            "Что доступно в пробном периоде:\n"
            "- Полный доступ ко всем функциям плана\n"
            "- Загрузка аккаунтов и прокси\n"
            "- AI-генерация комментариев\n"
            "- Аналитика и отчёты\n\n"
            "После окончания пробного периода для продолжения потребуется оплата.\n\n"
            f"С уважением,\nКоманда {product}"
        )

    elif template == "trial_expiring":
        name = ctx.get("name", "")
        hours_left = ctx.get("hours_left", 24)
        subject = f"Ваш пробный период заканчивается через {hours_left} часов"
        body = (
            f"Привет{', ' + name if name else ''}!\n\n"
            f"Ваш пробный период в {product} заканчивается через {hours_left} часов.\n\n"
            "Чтобы не потерять доступ к данным и продолжить работу —\n"
            "оформите подписку прямо сейчас.\n\n"
            "Перейдите в раздел «Биллинг» в личном кабинете.\n\n"
            f"С уважением,\nКоманда {product}"
        )

    elif template == "payment_success":
        name = ctx.get("name", "")
        amount = ctx.get("amount", 0)
        currency = ctx.get("currency", "RUB")
        plan_name = ctx.get("plan_name", "")
        period_end = ctx.get("period_end", "")
        subject = f"Оплата {amount} {currency} прошла успешно"
        body = (
            f"Привет{', ' + name if name else ''}!\n\n"
            f"Оплата подписки{' на план ' + plan_name if plan_name else ''} "
            f"на сумму {amount} {currency} успешно обработана.\n\n"
        )
        if period_end:
            body += f"Подписка активна до: {period_end}\n\n"
        body += (
            "Спасибо, что выбрали нас!\n\n"
            f"С уважением,\nКоманда {product}"
        )

    elif template == "payment_failed":
        name = ctx.get("name", "")
        subject = "Не удалось обработать платёж"
        body = (
            f"Привет{', ' + name if name else ''}!\n\n"
            f"К сожалению, платёж в {product} не прошёл.\n\n"
            "Возможные причины:\n"
            "- Недостаточно средств на карте\n"
            "- Карта заблокирована банком\n"
            "- Истёк срок действия карты\n\n"
            "Попробуйте повторить оплату или использовать другой способ оплаты.\n\n"
            f"С уважением,\nКоманда {product}"
        )

    elif template == "subscription_cancelled":
        name = ctx.get("name", "")
        period_end = ctx.get("period_end", "")
        subject = "Подписка отменена"
        body = (
            f"Привет{', ' + name if name else ''}!\n\n"
            f"Ваша подписка в {product} отменена.\n\n"
        )
        if period_end:
            body += f"Доступ к сервису сохраняется до: {period_end}\n\n"
        body += (
            "Если вы отменили подписку по ошибке или хотите возобновить —\n"
            "перейдите в раздел «Биллинг» в личном кабинете.\n\n"
            f"С уважением,\nКоманда {product}"
        )

    else:
        subject = f"{product} — уведомление"
        body = ctx.get("body", "")

    return subject, body


# ---------------------------------------------------------------------------
# Core send functions
# ---------------------------------------------------------------------------


def _send_sync(to: str, subject: str, body: str) -> None:
    """Synchronous SMTP send — runs inside a thread executor."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = settings.SMTP_FROM_EMAIL or settings.SMTP_USER
    msg["To"] = to
    msg.attach(MIMEText(body, "plain", "utf-8"))

    host = settings.SMTP_HOST
    port = settings.SMTP_PORT
    user = settings.SMTP_USER
    password = settings.SMTP_PASSWORD

    use_ssl = port == 465
    try:
        if use_ssl:
            with smtplib.SMTP_SSL(host, port, timeout=10) as smtp:
                smtp.login(user, password)
                smtp.sendmail(msg["From"], [to], msg.as_string())
        else:
            with smtplib.SMTP(host, port, timeout=10) as smtp:
                smtp.ehlo()
                if port != 25:
                    smtp.starttls()
                    smtp.ehlo()
                smtp.login(user, password)
                smtp.sendmail(msg["From"], [to], msg.as_string())
        log.info("Email sent to=%s subject=%r", to, subject)
    except Exception as exc:  # noqa: BLE001
        log.warning("Email send failed to=%s: %s", to, exc)


async def send_email(to: str, subject: str, body: str) -> None:
    """
    Send a plain-text email asynchronously.

    If SMTP_ENABLED is False, log the content instead of sending.
    Never raises — errors are logged.
    """
    if not to or "@" not in to:
        log.debug("Email skipped: invalid to=%r", to)
        return

    if not settings.SMTP_ENABLED:
        log.info(
            "Email (SMTP_ENABLED=False) to=%s subject=%r body_preview=%r",
            to,
            subject,
            body[:120],
        )
        return

    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, _send_sync, to, subject, body)
    except Exception as exc:  # noqa: BLE001
        log.warning("Email executor error to=%s: %s", to, exc)


async def send_template(
    template: str,
    to: str,
    **ctx: Any,
) -> None:
    """
    Render a named template and send it.

    Fire-and-forget: wrap in asyncio.create_task() for non-blocking use.
    """
    try:
        subject, body = _render_template(template, **ctx)
        await send_email(to=to, subject=subject, body=body)
    except Exception as exc:  # noqa: BLE001
        log.warning("send_template(%r) to=%s failed: %s", template, to, exc)


def schedule_email(template: str, to: str, **ctx: Any) -> None:
    """
    Schedule a template email as a background asyncio task.

    Safe to call from any async context — swallows all errors.
    """
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(send_template(template, to, **ctx))
        else:
            log.debug("No running event loop — email to=%s skipped", to)
    except Exception as exc:  # noqa: BLE001
        log.debug("schedule_email failed: %s", exc)
