"""No-DB unit tests for the L4 email adapter + the channel-adapter registry.

These exercise the adapter through a FakeTransport (no SMTP, no network, no DB):
  * a successful send returns status='sent' and records on the fake transport;
  * a transport error surfaces as status='failed' (NOT silently swallowed);
  * the idempotency_key is passed through to the transport and stamped on the
    message header;
  * the registry returns the email adapter for 'email' and raises for unknown.
"""
from __future__ import annotations

import pytest

from dispatch import registry
from dispatch.email.adapter import (
    IDEMPOTENCY_HEADER,
    EmailAdapter,
    FakeTransport,
    TransportError,
)


def test_fake_transport_send_returns_success():
    fake = FakeTransport()
    adapter = EmailAdapter(transport=fake, from_addr="from@sender.test")

    result = adapter.send(
        to="dest@example.com",
        subject="Hello",
        body="hi there",
        idempotency_key="idem-1",
    )

    assert result["status"] == "sent"
    assert result["provider_id"]  # non-empty provider id
    assert result["idempotency_key"] == "idem-1"
    # The transport actually recorded exactly one send with the right recipient.
    assert len(fake.sent) == 1
    rec = fake.sent[0]
    assert rec["to"] == "dest@example.com"
    assert rec["subject"] == "Hello"
    assert rec["from"] == "from@sender.test"


def test_transport_error_surfaces_as_failed_status_not_swallowed():
    # Prime the transport to fail. The adapter must report failure, not 'sent',
    # and must not raise (the orchestrator inspects the status).
    fake = FakeTransport(raise_on_send=TransportError("smtp boom"))
    adapter = EmailAdapter(transport=fake, from_addr="from@sender.test")

    result = adapter.send(
        to="dest@example.com",
        subject="Hi",
        body="body",
        idempotency_key="idem-fail",
    )

    assert result["status"] == "failed"
    assert result["provider_id"] is None
    assert "smtp boom" in result["error"]
    # Nothing was recorded as sent.
    assert fake.sent == []


def test_idempotency_key_is_passed_through_to_transport():
    fake = FakeTransport()
    adapter = EmailAdapter(transport=fake, from_addr="from@sender.test")

    adapter.send(
        to="dest@example.com",
        subject=None,
        body="b",
        idempotency_key="idem-XYZ",
    )

    rec = fake.sent[0]
    # Passed to send_message(...) AND stamped on the message header.
    assert rec["idempotency_key"] == "idem-XYZ"
    assert rec["idempotency_header"] == "idem-XYZ"
    # And the header name is the documented one.
    assert IDEMPOTENCY_HEADER == "X-Idempotency-Key"


def test_registry_returns_email_adapter_for_email():
    # Importing dispatch.email self-registers the default email adapter.
    import dispatch.email  # noqa: F401  (ensure registration ran)

    adapter = registry.get_adapter("email")
    assert hasattr(adapter, "send")


def test_registry_unknown_channel_raises():
    with pytest.raises(KeyError):
        registry.get_adapter("carrier-pigeon")
    # has_adapter is a non-raising probe.
    assert registry.has_adapter("carrier-pigeon") is False
    assert registry.has_adapter("email") is True


def test_registry_unknown_channel_returns_default_when_supplied():
    sentinel = object()
    assert registry.get_adapter("nope", default=sentinel) is sentinel
