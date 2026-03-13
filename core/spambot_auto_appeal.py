"""
Automatic @SpamBot checker + dynamic appeal runner.

Safety:
- Uses official @SpamBot flow only.
- CAPTCHA is never auto-solved; URL is reported for manual completion.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path

from sqlalchemy import select, update
from telethon.tl.types import KeyboardButtonUrl, ReplyInlineMarkup

from config import BASE_DIR, settings
from storage.models import Account
from storage.sqlite_db import async_session
from utils.logger import log
from utils.notifier import notifier
from utils.standalone_helpers import build_client, load_account_json, load_proxy_for_phone

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def _normalize_phone(raw: str) -> str:
    return "".join(ch for ch in str(raw) if ch.isdigit())


def _detect_question_type(text: str) -> str:
    t = (text or "").lower()
    if any(k in t for k in ("email", "e-mail", "почт", "mail address")):
        return "email"
    if (
        ("name" in t and any(k in t for k in ("full", "first", "last", "surname")))
        or ("имя" in t)
        or ("фамил" in t)
    ):
        return "full_name"
    if (
        (
            "year" in t
            and any(
                k in t
                for k in ("register", "joined", "created", "started", "sign up", "signed up")
            )
        )
        or ("valid year" in t)
        or ("в каком году" in t)
        or ("когда вы зарегистр" in t)
    ):
        return "reg_year"
    if (
        ("where" in t and any(k in t for k in ("hear", "heard", "learn", "find")))
        or ("откуда" in t)
        or ("узнали" in t)
    ):
        return "source"
    if any(
        k in t
        for k in (
            "average daily use",
            "how do you use telegram",
            "what do you use telegram for",
            "briefly describe your average daily use",
            "как вы используете telegram",
            "опишите",
            "для чего используете",
        )
    ):
        return "usage"
    if any(
        k in t
        for k in (
            "blocked by mistake",
            "why should",
            "why did this happen",
            "appeal",
            "наруш",
            "ошибк",
            "почему",
        )
    ):
        return "reason"
    return "fallback"


def _find_captcha_url(msg) -> str | None:
    if not msg.reply_markup:
        return None
    if isinstance(msg.reply_markup, ReplyInlineMarkup):
        for row in msg.reply_markup.rows:
            for button in row.buttons:
                if isinstance(button, KeyboardButtonUrl) and "captcha" in button.url.lower():
                    return button.url
    return None


def _pick_button(msg):
    if not msg.buttons:
        return None
    buttons = [b for row in msg.buttons for b in row]
    lower = [(b, (b.text or "").lower()) for b in buttons]

    def pick(keyword: str):
        for b, txt in lower:
            if keyword in txt:
                return b
        return None

    for key in ("this is a mistake", "ошибк", "yes", "да", "confirm", "подтвер", "done", "готов"):
        b = pick(key)
        if b is not None:
            return b
    return None


async def _wait_new_incoming(client, entity, min_msg_id: int, timeout: int = 60):
    start = time.time()
    while time.time() - start < timeout:
        msgs = await client.get_messages(entity, limit=10)
        incoming = [m for m in msgs if not m.out and m.id > min_msg_id]
        if incoming:
            incoming.sort(key=lambda m: m.id)
            return incoming[0]
        await asyncio.sleep(2)
    return None


def _classify_spambot_status(text: str) -> str:
    low = (text or "").lower()
    if any(k in low for k in ("no limits", "good news", "free as a bird", "ограничений нет")):
        return "clean"
    if any(k in low for k in ("already submitted", "will check it as soon as possible", "on review", "submitted")):
        return "under_review"
    if any(k in low for k in ("blocked for violations", "frozen", "limited", "restriction")):
        return "restricted"
    return "unknown"


class SpamBotAutoAppeal:
    """Background loop that checks frozen/restricted accounts and submits appeals."""

    def __init__(self):
        self._task: asyncio.Task | None = None
        self._running = False
        self._state_path = BASE_DIR / "data" / ".spambot_appeal_state.json"
        self._lock = asyncio.Lock()

    async def start(self):
        if self._running or not settings.AUTO_SPAMBOT_APPEAL_ENABLED:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="spambot_auto_appeal")
        def _on_appeal_done(t: asyncio.Task) -> None:
            exc = t.exception() if not t.cancelled() else None
            if exc:
                log.error("spambot_auto_appeal task failed: %s", exc, exc_info=exc)
        self._task.add_done_callback(_on_appeal_done)
        log.info("SpamBot auto-appeal loop started")

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        log.info("SpamBot auto-appeal loop stopped")

    def _load_state(self) -> dict:
        try:
            if self._state_path.exists():
                return json.loads(self._state_path.read_text(encoding="utf-8"))
        except Exception:
            pass
        return {}

    def _save_state(self, state: dict):
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            self._state_path.write_text(
                json.dumps(state, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            log.debug(f"Failed to save SpamBot appeal state: {exc}")

    async def _loop(self):
        while self._running:
            try:
                await self._run_cycle()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.error(f"SpamBot auto-appeal cycle error: {exc}")
            await asyncio.sleep(max(60, settings.AUTO_SPAMBOT_APPEAL_INTERVAL_SEC))

    async def _run_cycle(self):
        async with self._lock:
            state = self._load_state()
            now = time.time()
            batch = max(1, settings.AUTO_SPAMBOT_APPEAL_BATCH_SIZE)

            async with async_session() as session:
                result = await session.execute(
                    select(Account.phone, Account.status, Account.health_status).order_by(Account.id.asc()).limit(10000)
                )
                rows = result.all()

            processed = 0
            for phone_raw, status, health in rows:
                if processed >= batch:
                    break

                phone = _normalize_phone(phone_raw)
                if not phone:
                    continue

                item = state.get(phone, {})
                last_check = float(item.get("last_check_ts", 0))
                check_cooldown = max(1, settings.AUTO_SPAMBOT_CHECK_COOLDOWN_HOURS) * 3600
                if now - last_check < check_cooldown:
                    continue

                # Prioritize suspicious accounts first.
                suspicious = (
                    status in {"error", "banned", "cooldown", "flood_wait"}
                    or (health or "").lower() in {"restricted"}
                )
                if not suspicious and processed > 0:
                    continue

                report = await self._check_and_maybe_appeal(phone)
                item.update(report)
                item["last_check_ts"] = now
                state[phone] = item
                log.info(f"SpamBot auto-appeal: +{phone} -> {report.get('status', 'unknown')}")
                processed += 1

            self._save_state(state)

    async def _check_and_maybe_appeal(self, phone: str) -> dict:
        report: dict = {"status": "unknown"}
        client = None
        try:
            data = load_account_json(phone)
            proxy = load_proxy_for_phone(phone)
            client = build_client(phone, data, proxy)
            await client.connect()
            if not await client.is_user_authorized():
                await self._mark_account(phone, status="error", health_status="dead")
                return {"status": "unauthorized"}

            entity = await client.get_entity("SpamBot")
            await client.send_message(entity, "/start")
            await asyncio.sleep(4)
            latest = await client.get_messages(entity, limit=1)
            if not latest:
                return {"status": "no_response"}

            text = latest[0].text or ""
            current = _classify_spambot_status(text)
            report = {"status": current, "last_spambot_text": text[:500]}

            if current == "clean":
                await self._mark_account(phone, status="active", health_status="alive")
                return report

            if current == "under_review":
                await self._mark_account(phone, status="error", health_status="restricted")
                return report

            if current == "restricted":
                return await self._submit_dynamic_appeal(phone, client, entity, report)

            return report
        except Exception as exc:
            log.debug(f"Auto appeal check failed for {phone}: {exc}")
            report["status"] = "error"
            report["error"] = str(exc)[:400]
            return report
        finally:
            if client:
                try:
                    await client.disconnect()
                except Exception:
                    pass

    async def _find_recent_email(self, client, entity) -> str | None:
        msgs = await client.get_messages(entity, limit=80)
        for m in msgs:
            if not m.out or not m.text:
                continue
            found = EMAIL_RE.search(m.text)
            if found:
                return found.group(0)
        return None

    async def _submit_dynamic_appeal(self, phone: str, client, entity, report: dict) -> dict:
        now = time.time()
        state = self._load_state()
        item = state.get(phone, {})
        last_appeal = float(item.get("last_appeal_ts", 0))
        appeal_cooldown = max(1, settings.AUTO_SPAMBOT_APPEAL_COOLDOWN_HOURS) * 3600
        if now - last_appeal < appeal_cooldown:
            report["status"] = "restricted_cooldown"
            report["last_appeal_ts"] = last_appeal
            return report

        me = await client.get_me()
        full_name = f"{me.first_name or ''} {me.last_name or ''}".strip() or "Telegram User"
        email = settings.AUTO_SPAMBOT_APPEAL_EMAIL or await self._find_recent_email(client, entity)
        if not email:
            report["status"] = "appeal_email_missing"
            return report

        answers = {
            "reason": [
                "I believe this restriction is a mistake. I use Telegram for personal communication and normal channel reading. Please review and remove the restriction.",
                "My account is used for personal chats and normal use only. I did not intentionally violate rules. Please review this case.",
            ],
            "usage": [
                "I use Telegram daily for personal chats with friends/family, reading channels, and normal group discussions.",
                "My usage is regular personal messaging and reading channels, mostly with people I know.",
            ],
            "full_name": [full_name],
            "email": [email],
            "reg_year": [settings.AUTO_SPAMBOT_APPEAL_REG_YEAR],
            "source": [
                "I heard about Telegram from friends.",
                "A friend recommended Telegram to me.",
            ],
            "fallback": [
                "I use Telegram for normal personal communication and ask for a manual review.",
            ],
        }
        used = {k: 0 for k in answers}

        # Start appeal dialog.
        ts = time.time()
        await client.send_message(entity, "/start")
        await asyncio.sleep(3)
        latest = await client.get_messages(entity, limit=1)
        min_id = latest[0].id - 1 if latest else 0

        max_steps = max(6, settings.AUTO_SPAMBOT_APPEAL_MAX_STEPS)
        for _ in range(max_steps):
            msg = await _wait_new_incoming(client, entity, min_msg_id=min_id, timeout=60)
            if not msg:
                report["status"] = "appeal_timeout"
                break
            min_id = max(min_id, msg.id)

            text = (msg.text or "").strip()
            low = text.lower()
            if any(k in low for k in ("successfully submitted", "on review", "will check it as soon as possible")):
                report["status"] = "appeal_submitted"
                report["last_appeal_ts"] = ts
                item["last_appeal_ts"] = ts
                state[phone] = item
                self._save_state(state)
                await self._mark_account(phone, status="error", health_status="restricted")
                return report

            captcha_url = _find_captcha_url(msg)
            if captcha_url:
                report["status"] = "captcha_required"
                report["captcha_url"] = captcha_url
                await notifier.notify(
                    f"🧩 <b>SpamBot CAPTCHA required</b>\n"
                    f"👤 <code>+{phone}</code>\n"
                    f"🔗 {captcha_url}\n"
                    f"После ручного прохождения нажмите Done в @SpamBot.",
                    silent=False,
                )
                await self._mark_account(phone, status="error", health_status="restricted")
                return report

            if msg.buttons:
                button = _pick_button(msg)
                if button is not None:
                    await button.click()
                    await asyncio.sleep(3)
                    continue

            qtype = _detect_question_type(text)
            pool = answers[qtype]
            idx = min(used[qtype], len(pool) - 1)
            answer = pool[idx]
            used[qtype] += 1
            await client.send_message(entity, answer)
            await asyncio.sleep(3)

        if report.get("status") in {"captcha_required", "appeal_timeout"}:
            await self._mark_account(phone, status="error", health_status="restricted")
            return report

        report["status"] = report.get("status") or "appeal_unknown"
        await self._mark_account(phone, status="error", health_status="restricted")
        return report

    async def _mark_account(self, phone: str, status: str, health_status: str):
        phone_norm = f"+{_normalize_phone(phone)}"
        async with async_session() as session:
            await session.execute(
                update(Account)
                .where(Account.phone == phone_norm)
                .values(status=status, health_status=health_status)
            )
            await session.commit()
