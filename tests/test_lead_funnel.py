from __future__ import annotations

from datetime import datetime

import pytest

from core.lead_funnel import LeadSnapshot, build_lead_notification_text, deliver_lead_funnel


def _lead_snapshot() -> LeadSnapshot:
    return LeadSnapshot(
        lead_id=7,
        name="Petr Braslavskii",
        email="petr@example.com",
        company="Slice Pizza",
        telegram_username="petrbras",
        use_case="saas",
        utm_source="telegram",
        created_at=datetime(2026, 3, 9, 12, 30, 0),
    )


def test_build_lead_notification_text_contains_required_fields() -> None:
    text = build_lead_notification_text(_lead_snapshot())
    assert "Новый lead с лендинга" in text
    assert "Petr Braslavskii" in text
    assert "petr@example.com" in text
    assert "Slice Pizza" in text
    assert "@petrbras" in text
    assert "telegram" in text


@pytest.mark.asyncio
async def test_deliver_lead_funnel_handles_partial_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_sheets(_lead):
        raise RuntimeError("sheets_down")

    async def fake_admin(_lead):
        return {"ok": True, "message_id": 101}

    async def fake_digest(_lead):
        raise RuntimeError("digest_unavailable")

    monkeypatch.setattr("core.lead_funnel.mirror_lead_to_google_sheets", fake_sheets)
    monkeypatch.setattr("core.lead_funnel.send_admin_lead_notification", fake_admin)
    monkeypatch.setattr("core.lead_funnel.send_digest_lead_notification", fake_digest)

    result = await deliver_lead_funnel(_lead_snapshot())

    assert result["lead_id"] == 7
    assert result["google_sheets"]["ok"] is False
    assert result["admin_bot"]["ok"] is True
    assert result["digest_bot"]["ok"] is False
    assert "processed_at" in result


@pytest.mark.asyncio
async def test_deliver_lead_funnel_uses_both_notification_channels(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def fake_sheets(_lead):
        calls.append("sheets")
        return {"ok": True}

    async def fake_admin(_lead):
        calls.append("admin")
        return {"ok": True}

    async def fake_digest(_lead):
        calls.append("digest")
        return {"ok": True}

    monkeypatch.setattr("core.lead_funnel.mirror_lead_to_google_sheets", fake_sheets)
    monkeypatch.setattr("core.lead_funnel.send_admin_lead_notification", fake_admin)
    monkeypatch.setattr("core.lead_funnel.send_digest_lead_notification", fake_digest)

    result = await deliver_lead_funnel(_lead_snapshot())

    assert result["google_sheets"]["ok"] is True
    assert result["admin_bot"]["ok"] is True
    assert result["digest_bot"]["ok"] is True
    assert calls == ["sheets", "admin", "digest"]
