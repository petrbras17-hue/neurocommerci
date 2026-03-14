"""
Sprint 14 — Email Notification Service.

Provides async fire-and-forget email delivery using smtplib in a thread
executor (no additional async SMTP dependency required). Falls back to
structured log output when SMTP_ENABLED is False.

Usage:
    from core.email_service import schedule_email

    # fire and forget — never await if you want non-blocking
    schedule_email("welcome", to="user@example.com", name="Иван")
"""
from __future__ import annotations

import asyncio
import logging
import re
import smtplib
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

from config import settings

log = logging.getLogger("uvicorn.error")


# ---------------------------------------------------------------------------
# Email validation helper
# ---------------------------------------------------------------------------


def is_valid_email(email: str) -> bool:
    """Return True if email looks valid. Rejects empty or malformed strings."""
    if not email:
        return False
    return bool(re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', email))


# ---------------------------------------------------------------------------
# HTML template builder helpers
# ---------------------------------------------------------------------------

_DOMAIN = "176-124-221-253.sslip.io"


def _html_wrap(subject: str, body_html: str) -> str:
    """Wrap body_html in a full Dark Terminal branded HTML email scaffold."""
    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{subject}</title>
</head>
<body style="margin:0;padding:0;background-color:#0a0a0b;font-family:'Segoe UI',Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background-color:#0a0a0b;">
    <tr>
      <td align="center" style="padding:32px 16px;">
        <table width="600" cellpadding="0" cellspacing="0"
               style="max-width:600px;background-color:#111113;border:1px solid #1e1e22;border-radius:8px;">

          <!-- Header -->
          <tr>
            <td style="padding:28px 32px 20px;border-bottom:1px solid #1e1e22;">
              <span style="font-size:13px;font-weight:700;letter-spacing:3px;
                           text-transform:uppercase;color:#00ff88;
                           font-family:'Courier New',Courier,monospace;">
                NEURO COMMENTING
              </span>
              <span style="display:block;font-size:11px;color:#555;
                           font-family:'Courier New',Courier,monospace;
                           margin-top:4px;letter-spacing:1px;">
                Telegram Growth OS
              </span>
            </td>
          </tr>

          <!-- Body -->
          <tr>
            <td style="padding:28px 32px;color:#e0e0e0;font-size:15px;line-height:1.7;">
              {body_html}
            </td>
          </tr>

          <!-- Footer -->
          <tr>
            <td style="padding:20px 32px 28px;border-top:1px solid #1e1e22;">
              <p style="margin:0;font-size:12px;color:#444;line-height:1.6;">
                Вы получили это письмо, потому что зарегистрированы на
                <a href="https://{_DOMAIN}" style="color:#00ff88;text-decoration:none;">{_DOMAIN}</a>.<br>
                Если вы не регистрировались — просто проигнорируйте это сообщение.
              </p>
            </td>
          </tr>

        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


def _accent(text: str) -> str:
    """Wrap text in accent colour span."""
    return f'<span style="color:#00ff88;font-weight:600;">{text}</span>'


def _cta_button(url: str, label: str) -> str:
    """Render a Dark Terminal CTA button."""
    return (
        f'<a href="{url}" style="display:inline-block;margin-top:20px;padding:12px 28px;'
        f'background-color:#00ff88;color:#0a0a0b;font-weight:700;font-size:14px;'
        f'text-decoration:none;border-radius:4px;letter-spacing:0.5px;">{label}</a>'
    )


# ---------------------------------------------------------------------------
# Email templates
# ---------------------------------------------------------------------------


def _render_template(template: str, **ctx: Any) -> tuple[str, str, str]:
    """Return (subject, plain_body, html_body) for the given template and context."""
    product = "NEURO COMMENTING"
    base_url = f"https://{_DOMAIN}"
    billing_url = f"{base_url}/app/billing"

    if template == "welcome":
        name = ctx.get("name", "")
        greeting = f"Привет{', ' + name if name else ''}!"
        subject = f"Добро пожаловать в {product}!"
        plain = (
            f"{greeting}\n\n"
            f"Ваш аккаунт в {product} успешно создан.\n\n"
            "Начните прямо сейчас:\n"
            "1. Активируйте пробный период (3 дня бесплатно)\n"
            "2. Загрузите Telegram-аккаунты\n"
            "3. Создайте первую ферму комментариев\n\n"
            "Если у вас есть вопросы — просто ответьте на это письмо.\n\n"
            f"С уважением,\nКоманда {product}"
        )
        html_body = f"""
<p style="margin:0 0 16px;">{greeting}</p>
<p style="margin:0 0 16px;">
  Ваш аккаунт в {_accent(product)} успешно создан.<br>
  Начните работу прямо сейчас:
</p>
<ol style="margin:0 0 16px;padding-left:20px;color:#e0e0e0;">
  <li style="margin-bottom:8px;">Активируйте пробный период <strong style="color:#00ff88;">3 дня бесплатно</strong></li>
  <li style="margin-bottom:8px;">Загрузите Telegram-аккаунты и прокси</li>
  <li style="margin-bottom:8px;">Создайте первую ферму комментариев</li>
</ol>
{_cta_button(billing_url, "Активировать пробный период")}
<p style="margin:24px 0 0;color:#888;font-size:13px;">
  Если у вас есть вопросы — просто ответьте на это письмо.
</p>"""

    elif template == "trial_started":
        name = ctx.get("name", "")
        trial_days = ctx.get("trial_days", 3)
        plan_name = ctx.get("plan_name", "Starter")
        greeting = f"Привет{', ' + name if name else ''}!"
        subject = f"Ваш пробный период {plan_name} активирован — {trial_days} дней бесплатно"
        plain = (
            f"{greeting}\n\n"
            f"Ваш бесплатный пробный период ({trial_days} дней) на план {plan_name} активирован.\n\n"
            "Что доступно в пробном периоде:\n"
            "- Полный доступ ко всем функциям плана\n"
            "- Загрузка аккаунтов и прокси\n"
            "- AI-генерация комментариев\n"
            "- Аналитика и отчёты\n\n"
            "После окончания пробного периода для продолжения потребуется оплата.\n\n"
            f"С уважением,\nКоманда {product}"
        )
        html_body = f"""
<p style="margin:0 0 16px;">{greeting}</p>
<p style="margin:0 0 16px;">
  Ваш бесплатный пробный период
  ({_accent(f'{trial_days} дней')}) на план {_accent(plan_name)} активирован.
</p>
<p style="margin:0 0 8px;font-weight:600;color:#e0e0e0;">Что доступно в пробном периоде:</p>
<ul style="margin:0 0 16px;padding-left:20px;color:#e0e0e0;">
  <li style="margin-bottom:6px;">Полный доступ ко всем функциям плана</li>
  <li style="margin-bottom:6px;">Загрузка аккаунтов и прокси</li>
  <li style="margin-bottom:6px;">AI-генерация комментариев</li>
  <li style="margin-bottom:6px;">Аналитика и отчёты</li>
</ul>
<p style="margin:0 0 16px;color:#888;font-size:13px;">
  После окончания пробного периода для продолжения потребуется оплата.
</p>
{_cta_button(f'{base_url}/app', 'Открыть платформу')}"""

    elif template == "trial_expiring":
        name = ctx.get("name", "")
        hours_left = ctx.get("hours_left", 24)
        days_left = ctx.get("days_left", None)
        upgrade_url = ctx.get("upgrade_url", billing_url)
        greeting = f"Привет{', ' + name if name else ''}!"
        # Build a time-remaining label: prefer days_left if provided.
        if days_left is not None:
            time_label = f"{days_left} дней"
        else:
            time_label = f"{hours_left} часов"
        subject = f"Ваш пробный период заканчивается через {time_label}"
        plain = (
            f"{greeting}\n\n"
            f"Ваш пробный период в {product} заканчивается через {time_label}.\n\n"
            "Чтобы не потерять доступ к данным и продолжить работу —\n"
            "оформите подписку прямо сейчас:\n"
            f"{upgrade_url}\n\n"
            f"С уважением,\nКоманда {product}"
        )
        html_body = f"""
<p style="margin:0 0 16px;">{greeting}</p>
<p style="margin:0 0 16px;">
  Ваш пробный период в {_accent(product)} заканчивается
  через {_accent(time_label)}.
</p>
<p style="margin:0 0 16px;">
  Чтобы не потерять доступ к данным и продолжить работу —
  оформите подписку прямо сейчас.
</p>
{_cta_button(upgrade_url, 'Оформить подписку')}
<p style="margin:20px 0 0;color:#888;font-size:13px;">
  После окончания пробного периода ваши данные сохранятся ещё 7 дней.
</p>"""

    elif template == "payment_success":
        name = ctx.get("name", "")
        amount = ctx.get("amount", 0)
        currency = ctx.get("currency", "RUB")
        plan_name = ctx.get("plan_name", "")
        period_end = ctx.get("period_end", "")
        greeting = f"Привет{', ' + name if name else ''}!"
        plan_str = f" на план {plan_name}" if plan_name else ""
        period_str = f"Подписка активна до: {period_end}" if period_end else ""
        subject = f"Оплата {amount} {currency} прошла успешно"
        plain = (
            f"{greeting}\n\n"
            f"Оплата подписки{plan_str} на сумму {amount} {currency} успешно обработана.\n\n"
        )
        if period_str:
            plain += f"{period_str}\n\n"
        plain += f"Спасибо, что выбрали нас!\n\nС уважением,\nКоманда {product}"
        period_html = (
            f'<p style="margin:0 0 16px;color:#888;font-size:13px;">{period_str}</p>'
            if period_str else ""
        )
        html_body = f"""
<p style="margin:0 0 16px;">{greeting}</p>
<p style="margin:0 0 16px;">
  Оплата подписки{plan_str} на сумму
  {_accent(f'{amount} {currency}')} успешно обработана.
</p>
{period_html}
<p style="margin:0 0 0;color:#888;font-size:13px;">
  Спасибо, что выбрали нас!
</p>
{_cta_button(f'{base_url}/app', 'Открыть платформу')}"""

    elif template == "payment_failed":
        name = ctx.get("name", "")
        plan_name = ctx.get("plan_name", "")
        greeting = f"Привет{', ' + name if name else ''}!"
        plan_str = f" за план {plan_name}" if plan_name else ""
        subject = "Не удалось обработать платёж"
        plain = (
            f"{greeting}\n\n"
            f"К сожалению, платёж{plan_str} в {product} не прошёл.\n\n"
            "Возможные причины:\n"
            "- Недостаточно средств на карте\n"
            "- Карта заблокирована банком\n"
            "- Истёк срок действия карты\n\n"
            "Попробуйте повторить оплату или использовать другой способ оплаты.\n\n"
            f"С уважением,\nКоманда {product}"
        )
        html_body = f"""
<p style="margin:0 0 16px;">{greeting}</p>
<p style="margin:0 0 16px;">
  К сожалению, платёж{plan_str} в {_accent(product)} не прошёл.
</p>
<p style="margin:0 0 8px;font-weight:600;color:#e0e0e0;">Возможные причины:</p>
<ul style="margin:0 0 16px;padding-left:20px;color:#e0e0e0;">
  <li style="margin-bottom:6px;">Недостаточно средств на карте</li>
  <li style="margin-bottom:6px;">Карта заблокирована банком</li>
  <li style="margin-bottom:6px;">Истёк срок действия карты</li>
</ul>
<p style="margin:0 0 16px;color:#888;font-size:13px;">
  Попробуйте повторить оплату или использовать другой способ оплаты.
</p>
{_cta_button(billing_url, 'Обновить способ оплаты')}"""

    elif template == "subscription_cancelled":
        name = ctx.get("name", "")
        period_end = ctx.get("period_end", "")
        greeting = f"Привет{', ' + name if name else ''}!"
        subject = "Подписка отменена"
        plain = (
            f"{greeting}\n\n"
            f"Ваша подписка в {product} отменена.\n\n"
        )
        if period_end:
            plain += f"Доступ к сервису сохраняется до: {period_end}\n\n"
        plain += (
            "Если вы отменили подписку по ошибке или хотите возобновить —\n"
            "перейдите в раздел «Биллинг» в личном кабинете.\n\n"
            f"С уважением,\nКоманда {product}"
        )
        period_html = (
            f'<p style="margin:0 0 16px;color:#888;font-size:13px;">'
            f'Доступ к сервису сохраняется до: {_accent(period_end)}.</p>'
            if period_end else ""
        )
        html_body = f"""
<p style="margin:0 0 16px;">{greeting}</p>
<p style="margin:0 0 16px;">
  Ваша подписка в {_accent(product)} отменена.
</p>
{period_html}
<p style="margin:0 0 16px;color:#888;font-size:13px;">
  Если вы отменили подписку по ошибке или хотите возобновить —
  перейдите в раздел «Биллинг».
</p>
{_cta_button(billing_url, 'Возобновить подписку')}"""

    else:
        subject = f"{product} — уведомление"
        plain = ctx.get("body", "")
        html_body = f'<p style="margin:0;">{plain}</p>'

    html = _html_wrap(subject, html_body)
    return subject, plain, html


# ---------------------------------------------------------------------------
# Core send functions
# ---------------------------------------------------------------------------


def _send_sync(to: str, subject: str, plain: str, html: str) -> None:
    """
    Synchronous SMTP send — runs inside a thread executor.
    On connection failure, retries once after 5 seconds.
    On second failure, logs and gives up (fire-and-forget).
    """
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = settings.SMTP_FROM_EMAIL or settings.SMTP_USER
    msg["To"] = to
    msg.attach(MIMEText(plain, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))

    host = settings.SMTP_HOST
    port = settings.SMTP_PORT
    user = settings.SMTP_USER
    password = settings.SMTP_PASSWORD
    use_ssl = port == 465

    def _try_send() -> None:
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

    try:
        _try_send()
        log.info("Email sent to=%s subject=%r", to, subject)
    except (smtplib.SMTPException, OSError) as exc:
        log.warning("Email send attempt 1 failed to=%s: %s — retrying in 5s", to, exc)
        time.sleep(5)
        try:
            _try_send()
            log.info("Email sent (retry) to=%s subject=%r", to, subject)
        except Exception as exc2:  # noqa: BLE001
            log.error("Email send attempt 2 failed to=%s: %s — giving up", to, exc2)
    except Exception as exc:  # noqa: BLE001
        log.warning("Email send failed to=%s: %s", to, exc)


async def send_email(to: str, subject: str, body: str, html: str = "") -> None:
    """
    Send an email asynchronously (plain text + optional HTML).

    If SMTP_ENABLED is False, log the content instead of sending.
    Never raises — errors are logged.
    """
    if not is_valid_email(to):
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

    if not html:
        # Fallback: wrap plain text in a minimal HTML body.
        plain_escaped = body.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        html = _html_wrap(subject, f'<pre style="white-space:pre-wrap;color:#e0e0e0;">{plain_escaped}</pre>')

    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, _send_sync, to, subject, body, html)
    except Exception as exc:  # noqa: BLE001
        log.warning("Email executor error to=%s: %s", to, exc)


async def send_template(
    template: str,
    to: str,
    **ctx: Any,
) -> None:
    """
    Render a named template and send it (plain text + HTML).

    Fire-and-forget: wrap in asyncio.create_task() for non-blocking use.
    """
    try:
        subject, plain, html = _render_template(template, **ctx)
        await send_email(to=to, subject=subject, body=plain, html=html)
    except Exception as exc:  # noqa: BLE001
        log.warning("send_template(%r) to=%s failed: %s", template, to, exc)


def schedule_email(template: str, to: str, **ctx: Any) -> None:
    """
    Schedule a template email as a background asyncio task.

    Safe to call from any async context — swallows all errors.
    """
    if not is_valid_email(to):
        log.debug("schedule_email skipped: invalid to=%r", to)
        return
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(send_template(template, to, **ctx))
        else:
            log.debug("No running event loop — email to=%s skipped", to)
    except Exception as exc:  # noqa: BLE001
        log.debug("schedule_email failed: %s", exc)
