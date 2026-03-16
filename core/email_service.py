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

    # -----------------------------------------------------------------------
    # Welcome Sequence — 7 emails over 14-day trial
    # -----------------------------------------------------------------------

    elif template == "welcome_day_0":
        name = ctx.get("name", "")
        greeting = f"Привет, {name}!" if name else "Привет!"
        subject = "Добро пожаловать в NEURO COMMENTING — ваш Telegram на стероидах"
        plain = (
            f"{greeting}\n\n"
            "Вы только что получили доступ к инструменту, который делает за ночь то, "
            "что SMM-щик делает за месяц.\n\n"
            "NEURO COMMENTING — это боевой комбайн:\n"
            "- AI сам пишет комментарии, которые не отличить от живых\n"
            "- Парсер находит каналы вашей аудитории за минуты\n"
            "- Фермы работают 24/7 без вашего участия\n\n"
            "Ваш первый шаг (3 минуты):\n"
            "1. Зайдите в личный кабинет\n"
            "2. Загрузите Telegram-аккаунт (session + json файл)\n"
            "3. Привяжите прокси — мы подскажем какой\n\n"
            "У вас 14 дней trial. Без ограничений. Без карты. Полный доступ ко всему.\n\n"
            f"С уважением,\nКоманда {product}"
        )
        html_body = f"""
<p style="margin:0 0 16px;">{greeting}</p>
<p style="margin:0 0 16px;">
  Вы только что получили доступ к инструменту, который делает за ночь то,
  что SMM-щик делает за месяц.
</p>
<p style="margin:0 0 12px;font-weight:600;color:#e0e0e0;">
  {_accent('NEURO COMMENTING')} — это боевой комбайн:
</p>
<ul style="margin:0 0 16px;padding-left:20px;color:#e0e0e0;">
  <li style="margin-bottom:6px;">AI сам пишет комментарии, которые не отличить от живых</li>
  <li style="margin-bottom:6px;">Парсер находит каналы вашей аудитории за минуты</li>
  <li style="margin-bottom:6px;">Фермы работают 24/7 без вашего участия</li>
</ul>
<p style="margin:0 0 12px;font-weight:600;color:#00ff88;">Ваш первый шаг (3 минуты):</p>
<ol style="margin:0 0 16px;padding-left:20px;color:#e0e0e0;">
  <li style="margin-bottom:8px;">Зайдите в <a href="{base_url}/app/dashboard" style="color:#00ff88;text-decoration:none;">личный кабинет</a></li>
  <li style="margin-bottom:8px;">Загрузите Telegram-аккаунт (session + json файл)</li>
  <li style="margin-bottom:8px;">Привяжите прокси — мы подскажем какой</li>
</ol>
<p style="margin:0 0 16px;background:#0d1f15;border-left:3px solid #00ff88;padding:12px 16px;border-radius:4px;color:#e0e0e0;">
  У вас {_accent('14 дней trial')}. Без ограничений. Без карты. Полный доступ ко всему.
</p>
<p style="margin:0 0 0;color:#888;font-size:13px;">
  Не теряйте время — конкуренты уже внутри.
</p>
{_cta_button(f'{base_url}/app/dashboard', 'Начать настройку')}"""

    elif template == "welcome_day_1":
        name = ctx.get("name", "")
        subject = "Ваш аккаунт ждёт — загрузите его за 2 минуты"
        plain = (
            f"{name}, день второй — и пора вооружиться.\n\n"
            "Что нужно:\n"
            "- .session файл вашего Telegram-аккаунта\n"
            "- .json файл с метаданными\n"
            "- Один приватный прокси (SOCKS5, 1 IP = 1 аккаунт)\n\n"
            "После загрузки система автоматически:\n"
            "- Проверит аккаунт на бан\n"
            "- Привяжет прокси\n"
            "- Создаст AI-персону\n"
            "- Запустит прогрев (если включите)\n\n"
            f"С уважением,\nКоманда {product}"
        )
        html_body = f"""
<p style="margin:0 0 16px;">{name + ', д' if name else 'Д'}ень второй — и пора вооружиться.</p>
<p style="margin:0 0 16px;">
  Если вы ещё не загрузили Telegram-аккаунт — сейчас самое время.
  Без него всё остальное бесполезно.
</p>
<p style="margin:0 0 8px;font-weight:600;color:#e0e0e0;">Что нужно:</p>
<ul style="margin:0 0 16px;padding-left:20px;color:#e0e0e0;">
  <li style="margin-bottom:6px;"><code style="color:#00ff88;">.session</code> файл вашего Telegram-аккаунта</li>
  <li style="margin-bottom:6px;"><code style="color:#00ff88;">.json</code> файл с метаданными</li>
  <li style="margin-bottom:6px;">Один приватный прокси (SOCKS5, {_accent('1 IP = 1 аккаунт')})</li>
</ul>
<p style="margin:0 0 8px;font-weight:600;color:#e0e0e0;">После загрузки система автоматически:</p>
<ul style="margin:0 0 16px;padding-left:20px;color:#e0e0e0;">
  <li style="margin-bottom:6px;">Проверит аккаунт на бан</li>
  <li style="margin-bottom:6px;">Привяжет прокси</li>
  <li style="margin-bottom:6px;">Создаст AI-персону</li>
  <li style="margin-bottom:6px;">Запустит прогрев (если включите)</li>
</ul>
<p style="margin:0 0 16px;background:#0d1f15;border-left:3px solid #00ff88;padding:12px 16px;border-radius:4px;color:#e0e0e0;">
  Прогрев — это как разминка перед боем. 3-7 дней аккаунт читает каналы, ставит реакции,
  подписывается. После этого — комментирует как живой человек.
</p>
{_cta_button(f'{base_url}/app/accounts', 'Загрузить аккаунт')}"""

    elif template == "welcome_day_3":
        name = ctx.get("name", "")
        subject = "Пора запустить ферму — вот пошаговый план"
        plain = (
            f"{name}, если аккаунт загружен и прогрет — время запускать ферму.\n\n"
            "Ферма — это автоматическая машина комментирования:\n"
            "1. Выбираете каналы — через парсер или карту каналов\n"
            "2. Назначаете аккаунты — система сама распределит нагрузку\n"
            "3. Выбираете стиль AI\n"
            "4. Жмёте Старт — ферма работает, вы отдыхаете\n\n"
            "Антидетект встроен: случайные задержки, имитация набора текста, "
            "гауссово распределение активности.\n\n"
            f"С уважением,\nКоманда {product}"
        )
        html_body = f"""
<p style="margin:0 0 16px;">{name + ', е' if name else 'Е'}сли аккаунт загружен и прогрет — время запускать ферму.</p>
<p style="margin:0 0 12px;font-weight:600;color:#e0e0e0;">
  {_accent('Ферма')} — это автоматическая машина комментирования:
</p>
<ol style="margin:0 0 16px;padding-left:20px;color:#e0e0e0;">
  <li style="margin-bottom:8px;"><strong>Выбираете каналы</strong> — через парсер или карту каналов</li>
  <li style="margin-bottom:8px;"><strong>Назначаете аккаунты</strong> — система сама распределит нагрузку</li>
  <li style="margin-bottom:8px;"><strong>Выбираете стиль AI</strong> — дерзкий, экспертный, провокационный, мемный или свой</li>
  <li style="margin-bottom:8px;"><strong>Жмёте "Старт"</strong> — ферма работает, вы отдыхаете</li>
</ol>
<p style="margin:0 0 8px;font-weight:600;color:#e0e0e0;">Антидетект встроен:</p>
<ul style="margin:0 0 16px;padding-left:20px;color:#e0e0e0;">
  <li style="margin-bottom:6px;">Случайные задержки между комментариями (30-120 сек)</li>
  <li style="margin-bottom:6px;">Имитация набора текста</li>
  <li style="margin-bottom:6px;">Имитация чтения постов</li>
  <li style="margin-bottom:6px;">Гауссово распределение активности (как живой человек)</li>
</ul>
<p style="margin:0 0 16px;color:#888;font-size:13px;">
  Запустите ферму на 3-5 каналов для начала. Посмотрите как AI работает. Потом масштабируйте.
</p>
{_cta_button(f'{base_url}/app/farm', 'Создать ферму')}"""

    elif template == "welcome_day_5":
        name = ctx.get("name", "")
        subject = "Вот как AI пишет комментарии, которые не отличить от живых"
        plain = (
            f"{name}, давайте поговорим про самое интересное — как AI пишет комментарии.\n\n"
            "Это не ChatGPT. Наш Smart Commenter анализирует пост и подбирает стиль.\n\n"
            "10 встроенных стилей: экспертный, провокационный, мемный, emoji-first, "
            "storytelling, аналитический, вопросительный, поддерживающий, дискуссионный, кастомный.\n\n"
            "Правила антифрода:\n"
            "- Никогда не комментирует первым\n"
            "- Emoji-first трюк\n"
            "- Анализирует тон поста\n"
            "- A/B тестирует стили\n\n"
            f"С уважением,\nКоманда {product}"
        )
        html_body = f"""
<p style="margin:0 0 16px;">{name + ', д' if name else 'Д'}авайте поговорим про самое интересное — как AI пишет комментарии.</p>
<p style="margin:0 0 16px;">
  Это не ChatGPT, который генерит "Отличная статья! Спасибо за информацию!".
  Наш {_accent('Smart Commenter')} анализирует пост и подбирает стиль.
</p>
<table style="width:100%;border-collapse:collapse;margin:0 0 16px;font-size:13px;">
  <tr style="border-bottom:1px solid #1e1e22;">
    <td style="padding:8px;color:#00ff88;font-weight:600;">Экспертный</td>
    <td style="padding:8px;color:#aaa;">"По опыту с 200+ клиентами — ROI на Telegram-рекламу выше email в 3x..."</td>
  </tr>
  <tr style="border-bottom:1px solid #1e1e22;">
    <td style="padding:8px;color:#00ff88;font-weight:600;">Провокационный</td>
    <td style="padding:8px;color:#aaa;">"Спорное мнение: 90% каналов сливают бюджет на контент, который никто не читает"</td>
  </tr>
  <tr style="border-bottom:1px solid #1e1e22;">
    <td style="padding:8px;color:#00ff88;font-weight:600;">Мемный</td>
    <td style="padding:8px;color:#aaa;">"я: не буду подписываться. также я: *подписывается*"</td>
  </tr>
  <tr style="border-bottom:1px solid #1e1e22;">
    <td style="padding:8px;color:#00ff88;font-weight:600;">Emoji-first</td>
    <td style="padding:8px;color:#aaa;">"Ого, это реально работает? Надо попробовать"</td>
  </tr>
  <tr>
    <td style="padding:8px;color:#00ff88;font-weight:600;">+ ещё 6</td>
    <td style="padding:8px;color:#aaa;">Storytelling, аналитический, вопросительный, поддерживающий, дискуссионный, кастомный</td>
  </tr>
</table>
<p style="margin:0 0 8px;font-weight:600;color:#e0e0e0;">Правила антифрода:</p>
<ul style="margin:0 0 16px;padding-left:20px;color:#e0e0e0;">
  <li style="margin-bottom:6px;">Никогда не комментирует первым (правило "never-first")</li>
  <li style="margin-bottom:6px;">Emoji-first трюк — начинает с эмодзи для органичности</li>
  <li style="margin-bottom:6px;">Анализирует тон поста и подстраивается</li>
  <li style="margin-bottom:6px;">A/B тестирует стили и оптимизирует конверсию</li>
</ul>
{_cta_button(f'{base_url}/app/comments', 'Настроить стили AI')}"""

    elif template == "welcome_day_7":
        name = ctx.get("name", "")
        total_comments = ctx.get("total_comments", 0)
        total_channels = ctx.get("total_channels", 0)
        has_comments = total_comments > 0
        subject = "Неделя прошла — вот что вы (не) сделали"
        plain = (
            f"{name}, прошла неделя с момента регистрации.\n\n"
        )
        if has_comments:
            plain += (
                f"Комментариев написано: {total_comments}\n"
                f"Каналов обработано: {total_channels}\n\n"
            )
        else:
            plain += (
                "Похоже, вы ещё не запустили ферму. У вас осталось 7 дней trial.\n"
                "Самый быстрый путь:\n"
                "1. Загрузите аккаунт (2 мин)\n"
                "2. Включите автопрогрев (1 клик)\n"
                "3. Добавьте 5 каналов через парсер (3 мин)\n"
                "4. Запустите ферму в conservative режиме (1 мин)\n\n"
            )
        plain += f"С уважением,\nКоманда {product}"
        if has_comments:
            stats_html = f"""
<p style="margin:0 0 8px;font-weight:600;color:#e0e0e0;">Ваши результаты:</p>
<table style="width:100%;border-collapse:collapse;margin:0 0 16px;">
  <tr style="border-bottom:1px solid #1e1e22;">
    <td style="padding:8px;color:#aaa;">Комментариев написано</td>
    <td style="padding:8px;color:#00ff88;font-weight:600;text-align:right;">{total_comments}</td>
  </tr>
  <tr>
    <td style="padding:8px;color:#aaa;">Каналов обработано</td>
    <td style="padding:8px;color:#00ff88;font-weight:600;text-align:right;">{total_channels}</td>
  </tr>
</table>
<p style="margin:0 0 8px;font-weight:600;color:#e0e0e0;">Что дальше на этой неделе:</p>
<ul style="margin:0 0 16px;padding-left:20px;color:#e0e0e0;">
  <li style="margin-bottom:6px;">Попробуйте контент-фабрику — один пост превращается в 6 форматов</li>
  <li style="margin-bottom:6px;">Откройте карту каналов — найдите конкурентов визуально</li>
  <li style="margin-bottom:6px;">Настройте AI-ассистента — он поможет с брифом и стратегией</li>
</ul>"""
            cta_label = "Открыть аналитику"
            cta_url = f"{base_url}/app/analytics"
        else:
            stats_html = f"""
<p style="margin:0 0 16px;background:#1f0d0d;border-left:3px solid #ff4444;padding:12px 16px;border-radius:4px;color:#e0e0e0;">
  Похоже, вы ещё не запустили ферму. У вас осталось {_accent('7 дней trial')}.
</p>
<p style="margin:0 0 8px;font-weight:600;color:#e0e0e0;">Самый быстрый путь:</p>
<ol style="margin:0 0 16px;padding-left:20px;color:#e0e0e0;">
  <li style="margin-bottom:6px;">Загрузите аккаунт (2 мин)</li>
  <li style="margin-bottom:6px;">Включите автопрогрев (1 клик)</li>
  <li style="margin-bottom:6px;">Добавьте 5 каналов через парсер (3 мин)</li>
  <li style="margin-bottom:6px;">Запустите ферму в conservative режиме (1 мин)</li>
</ol>
<p style="margin:0 0 0;color:#888;font-size:13px;">
  Итого: {_accent('7 минут')} от нуля до работающей фермы.
</p>"""
            cta_label = "Запустить ферму"
            cta_url = f"{base_url}/app/farm"
        html_body = f"""
<p style="margin:0 0 16px;">{name + ', п' if name else 'П'}рошла неделя с момента регистрации.</p>
{stats_html}
{_cta_button(cta_url, cta_label)}"""

    elif template == "welcome_day_10":
        name = ctx.get("name", "")
        subject = "Вы используете 20% платформы. Вот остальные 80%"
        plain = (
            f"{name}, большинство пользователей на trial используют только ферму и парсер.\n\n"
            "5 фич, которые вы скорее всего пропустили:\n"
            "1. Карта каналов — глобус с 5000+ каналов\n"
            "2. Контент-фабрика — один текст в 6 форматов за 30 сек\n"
            "3. Нейрочатинг — AI ведёт диалоги в личке\n"
            "4. Массовые реакции — параллельно с комментированием\n"
            "5. AI-ассистент — стратегия и креативы\n\n"
            f"С уважением,\nКоманда {product}"
        )
        html_body = f"""
<p style="margin:0 0 16px;">{name + ', б' if name else 'Б'}ольшинство пользователей на trial используют только ферму и парсер.
  Но платформа умеет сильно больше.</p>
<p style="margin:0 0 12px;font-weight:600;color:#00ff88;">5 фич, которые вы скорее всего пропустили:</p>

<div style="margin:0 0 12px;background:#0d1f15;border-left:3px solid #00ff88;padding:12px 16px;border-radius:4px;">
  <p style="margin:0 0 4px;font-weight:600;color:#00ff88;">1. Карта каналов (Channel Map)</p>
  <p style="margin:0;color:#aaa;font-size:13px;">Глобус с 5000+ каналов RU/CIS. Фильтры по категориям, подписчикам, географии. Это Google Maps для Telegram-аудитории.</p>
</div>
<div style="margin:0 0 12px;background:#0d1f15;border-left:3px solid #00ff88;padding:12px 16px;border-radius:4px;">
  <p style="margin:0 0 4px;font-weight:600;color:#00ff88;">2. Контент-фабрика</p>
  <p style="margin:0;color:#aaa;font-size:13px;">Один текст &rarr; 6 форматов за 30 секунд: Telegram, Twitter/X, LinkedIn, YouTube, Reels, Email.</p>
</div>
<div style="margin:0 0 12px;background:#0d1f15;border-left:3px solid #00ff88;padding:12px 16px;border-radius:4px;">
  <p style="margin:0 0 4px;font-weight:600;color:#00ff88;">3. Нейрочатинг</p>
  <p style="margin:0;color:#aaa;font-size:13px;">AI ведёт диалоги в личке от имени ваших аккаунтов. Семантический матчинг + Unified Inbox.</p>
</div>
<div style="margin:0 0 12px;background:#0d1f15;border-left:3px solid #00ff88;padding:12px 16px;border-radius:4px;">
  <p style="margin:0 0 4px;font-weight:600;color:#00ff88;">4. Массовые реакции</p>
  <p style="margin:0;color:#aaa;font-size:13px;">Проставляет реакции на посты в целевых каналах. Работает параллельно с комментированием.</p>
</div>
<div style="margin:0 0 12px;background:#0d1f15;border-left:3px solid #00ff88;padding:12px 16px;border-radius:4px;">
  <p style="margin:0 0 4px;font-weight:600;color:#00ff88;">5. AI-ассистент</p>
  <p style="margin:0;color:#aaa;font-size:13px;">Расскажите про бизнес &rarr; получите стратегию &rarr; сгенерируйте креативы &rarr; запустите.</p>
</div>

<p style="margin:12px 0 0;color:#888;font-size:13px;">
  Попробуйте хотя бы одну из этих фич до конца trial. Это то, за что платят на Pro-плане.
</p>
{_cta_button(f'{base_url}/app/channel-map', 'Открыть карту каналов')}"""

    elif template == "welcome_day_13":
        name = ctx.get("name", "")
        trial_days_left = ctx.get("trial_days_left", 1)
        subject = "Завтра ваш trial закончится. Вот что будет дальше"
        plain = (
            f"{name}, через 24 часа ваш 14-дневный trial закончится.\n\n"
            "Что произойдёт:\n"
            "- Фермы остановятся\n"
            "- AI перестанет генерировать комментарии\n"
            "- Парсер и карта каналов станут read-only\n"
            "- Ваши данные сохранятся на 30 дней\n\n"
            "Три плана:\n"
            "- Starter $49/мес — 5 аккаунтов, 2 фермы, 10 потоков\n"
            "- Pro $99/мес — 20 аккаунтов, 10 ферм, 50 потоков\n"
            "- Agency $199/мес — 50 аккаунтов, без лимита ферм\n\n"
            "Оплата: Stripe (международная карта) или ЮKassa (РФ карта).\n\n"
            f"С уважением,\nКоманда {product}"
        )
        html_body = f"""
<p style="margin:0 0 16px;">{name + ', ч' if name else 'Ч'}ерез {_accent('24 часа')} ваш 14-дневный trial закончится.</p>

<p style="margin:0 0 8px;font-weight:600;color:#ff4444;">Что произойдёт:</p>
<ul style="margin:0 0 16px;padding-left:20px;color:#e0e0e0;">
  <li style="margin-bottom:6px;">Фермы остановятся</li>
  <li style="margin-bottom:6px;">AI перестанет генерировать комментарии</li>
  <li style="margin-bottom:6px;">Парсер и карта каналов станут read-only</li>
  <li style="margin-bottom:6px;">Ваши данные, аккаунты и настройки сохранятся на {_accent('30 дней')}</li>
</ul>

<p style="margin:0 0 8px;font-weight:600;color:#e0e0e0;">Что НЕ произойдёт:</p>
<ul style="margin:0 0 16px;padding-left:20px;color:#888;">
  <li style="margin-bottom:4px;">Мы не удалим ваши данные</li>
  <li style="margin-bottom:4px;">Мы не отвяжем прокси</li>
  <li style="margin-bottom:4px;">Мы не сбросим AI-стили</li>
</ul>

<p style="margin:0 0 8px;font-weight:600;color:#00ff88;">Три плана — выбирайте свой:</p>
<table style="width:100%;border-collapse:collapse;margin:0 0 16px;font-size:13px;">
  <tr style="border-bottom:1px solid #1e1e22;">
    <td style="padding:8px;color:#e0e0e0;font-weight:600;">Starter</td>
    <td style="padding:8px;color:#00ff88;">$49/мес</td>
    <td style="padding:8px;color:#aaa;">5 акк, 2 фермы, 10 потоков</td>
  </tr>
  <tr style="border-bottom:1px solid #1e1e22;">
    <td style="padding:8px;color:#e0e0e0;font-weight:600;">Pro</td>
    <td style="padding:8px;color:#00ff88;">$99/мес</td>
    <td style="padding:8px;color:#aaa;">20 акк, 10 ферм, 50 потоков, контент-фабрика</td>
  </tr>
  <tr>
    <td style="padding:8px;color:#e0e0e0;font-weight:600;">Agency</td>
    <td style="padding:8px;color:#00ff88;">$199/мес</td>
    <td style="padding:8px;color:#aaa;">50 акк, без лимита, white label</td>
  </tr>
</table>
<p style="margin:0 0 0;color:#888;font-size:13px;">
  Оплата: Stripe (международная карта) или ЮKassa (РФ карта).
</p>
{_cta_button(billing_url, 'Выбрать план')}"""

    else:
        subject = f"{product} — уведомление"
        plain = ctx.get("body", "")
        html_body = f'<p style="margin:0;">{plain}</p>'

    html = _html_wrap(subject, html_body)
    return subject, plain, html


# ---------------------------------------------------------------------------
# Welcome sequence convenience — maps day number to template name
# ---------------------------------------------------------------------------

WELCOME_SEQUENCE_DAYS: dict[int, str] = {
    0: "welcome_day_0",
    1: "welcome_day_1",
    3: "welcome_day_3",
    5: "welcome_day_5",
    7: "welcome_day_7",
    10: "welcome_day_10",
    13: "welcome_day_13",
}


async def send_welcome_sequence_email(
    to: str,
    day: int,
    **ctx: Any,
) -> None:
    """
    Send a specific Welcome Sequence email by day number.

    Supported days: 0, 1, 3, 5, 7, 10, 13.
    Extra context kwargs (name, total_comments, trial_days_left, etc.)
    are forwarded to the template renderer.
    """
    template = WELCOME_SEQUENCE_DAYS.get(day)
    if template is None:
        log.warning("send_welcome_sequence_email: unknown day=%d", day)
        return
    await send_template(template, to=to, **ctx)


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
