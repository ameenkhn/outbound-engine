"""Unit tests for the WhatsApp (AiSensy) + Resend email channel adapters.

No network, no credentials: WhatsApp uses FakeWhatsAppTransport, Resend patches
httpx.post. Asserts the send() protocol contract (sent/failed dicts, idempotency
passthrough) and the registry wiring.
"""
from __future__ import annotations

import pytest

from dispatch import registry
from dispatch.whatsapp.adapter import (
    WhatsAppAdapter,
    FakeWhatsAppTransport,
    AiSensyTransport,
    TransportError as WATransportError,
)


def test_whatsapp_send_success_records_template():
    fake = FakeWhatsAppTransport()
    a = WhatsAppAdapter(transport=fake)
    r = a.send(to="+919876543210", subject=None, body="Hi Maya — loved your yoga content!",
               idempotency_key="job-1")
    assert r["status"] == "sent"
    assert r["provider_id"].startswith("fake-wa-")
    assert r["idempotency_key"] == "job-1"
    assert fake.sent[0]["to"] == "+919876543210"
    assert fake.sent[0]["params"] == ["Hi Maya — loved your yoga content!"]
    assert fake.sent[0]["idempotency_key"] == "job-1"


def test_whatsapp_send_failure_reports_failed_not_raise():
    fake = FakeWhatsAppTransport(raise_on_send=RuntimeError("boom"))
    a = WhatsAppAdapter(transport=fake)
    r = a.send(to="+919876543210", subject=None, body="x", idempotency_key="job-2")
    assert r["status"] == "failed"
    assert r["provider_id"] is None
    assert "boom" in r["error"]


def test_whatsapp_registered():
    import dispatch.whatsapp.adapter  # noqa: F401 — ensures registration ran
    assert registry.get_adapter("whatsapp") is not None


def test_aisensy_transport_requires_config(monkeypatch):
    monkeypatch.delenv("AISENSY_API_KEY", raising=False)
    monkeypatch.delenv("AISENSY_CAMPAIGN", raising=False)
    with pytest.raises(WATransportError):
        AiSensyTransport().send_template(to="+919876543210", params=["hi"], idempotency_key="k")


def test_resend_transport_posts_and_returns_id(monkeypatch):
    import httpx
    from dispatch.email.adapter import ResendTransport, EmailAdapter

    class _Resp:
        status_code = 200
        text = ""
        def json(self):
            return {"id": "re_abc123"}

    captured = {}
    def _post(url, json=None, headers=None, timeout=None):
        captured["url"] = url; captured["json"] = json; captured["headers"] = headers
        return _Resp()
    monkeypatch.setattr(httpx, "post", _post)
    monkeypatch.setenv("RESEND_API_KEY", "test-key")
    monkeypatch.setenv("EMAIL_FROM", "hi@exly.in")

    a = EmailAdapter(transport=ResendTransport())
    r = a.send(to="maya@yoga.in", subject="Quick idea", body="hello there", idempotency_key="k1")

    assert r["status"] == "sent"
    assert r["provider_id"] == "re_abc123"
    assert captured["json"]["to"] == ["maya@yoga.in"]
    assert captured["json"]["subject"] == "Quick idea"
    assert captured["headers"]["Idempotency-Key"] == "k1"
    assert "Bearer test-key" in captured["headers"]["Authorization"]


def test_resend_transport_reports_failure(monkeypatch):
    import httpx
    from dispatch.email.adapter import ResendTransport, EmailAdapter

    class _Resp:
        status_code = 422
        text = "invalid from"
        def json(self):
            return {}
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp())
    monkeypatch.setenv("RESEND_API_KEY", "test-key")
    monkeypatch.setenv("EMAIL_FROM", "hi@exly.in")

    r = EmailAdapter(transport=ResendTransport()).send(
        to="x@y.in", subject="hi", body="b", idempotency_key="k2")
    assert r["status"] == "failed"
    assert "422" in r["error"]
