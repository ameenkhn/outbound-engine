"""WhatsApp channel adapter for L4 (AiSensy BSP).

Fills the orchestrator's send seam for ``channel_type='whatsapp'`` — the same
``send(*, to, subject, body, idempotency_key) -> dict`` protocol every channel
adapter implements (see ``dispatch/registry.py``).

WHY A TEMPLATE, NOT FREE TEXT
-----------------------------
WhatsApp's Business Platform only lets you *initiate* a conversation with a
**pre-approved template**, and only to users on an **opt-in** basis. You cannot
cold-blast arbitrary text — that gets the number banned. So this adapter sends a
template *campaign* (approved in the AiSensy dashboard); the ``body`` we pass
becomes the template's first parameter (e.g. the personalized line). Opt-in and
suppression are enforced *before* dispatch by the orchestrator (6A re-check) and
the channels' ``opted_in`` gate — the adapter just sends.

Layering (mirrors the email adapter):

    WhatsAppAdapter.send(to, body, idempotency_key)
        -> WhatsAppTransport.send_template(to, params, idempotency_key)
        -> AiSensy campaign API (or FakeTransport in tests)
        -> {'provider_id', 'status': 'sent'|'failed', ...}

Transports:
  * :class:`AiSensyTransport` — real HTTP call to AiSensy's campaign API. ``httpx``
    is imported lazily and the API key read at call time, so importing this
    module never needs httpx or credentials.
  * :class:`FakeWhatsAppTransport` — records sends in-memory for tests; can be
    primed to raise to exercise the failure path.

A send error is never swallowed: the transport raises, the adapter catches, and
returns ``status='failed'`` with the error — so the orchestrator reschedules and
never records a 'sent' for a send that didn't happen. Python 3.9 compatible.
"""
from __future__ import annotations

import os
import uuid
from typing import Any, Dict, List, Optional

from .. import registry


class TransportError(RuntimeError):
    """Raised by a transport when the underlying send fails."""


# ---- Transport interface ----------------------------------------------------

class WhatsAppTransport:
    """Interface a concrete WhatsApp transport implements.

    ``send_template`` delivers an approved template to ``to`` with ordered
    ``params`` and returns a provider-side id (string). MUST raise on failure.
    """

    def send_template(self, *, to: str, params: List[str], idempotency_key: str) -> str:
        raise NotImplementedError


# ---- AiSensy transport (real) -----------------------------------------------

class AiSensyTransport(WhatsAppTransport):
    """Send via AiSensy's campaign API (India-focused WhatsApp BSP).

    Config (env, with explicit-arg overrides for testing):
      AISENSY_API_KEY   (required)  — from the AiSensy dashboard
      AISENSY_CAMPAIGN  (required)  — the approved template campaign name
      AISENSY_BASE_URL  (optional)  — defaults to AiSensy's v2 campaign endpoint

    The message text is delivered as the campaign template's parameters. ``to``
    is an E.164 number (a leading ``+`` is stripped — AiSensy wants ``9198…``).
    """

    DEFAULT_URL = "https://backend.aisensy.com/campaign/t1/api/v2"

    def __init__(
        self,
        api_key: Optional[str] = None,
        campaign: Optional[str] = None,
        base_url: Optional[str] = None,
        sender_name: str = "Exly Outbound",
        timeout: float = 20.0,
    ) -> None:
        self._api_key = api_key
        self._campaign = campaign
        self._base_url = base_url
        self.sender_name = sender_name
        self.timeout = timeout

    def _cfg(self) -> Dict[str, str]:
        api_key = self._api_key or os.environ.get("AISENSY_API_KEY")
        campaign = self._campaign or os.environ.get("AISENSY_CAMPAIGN")
        base_url = self._base_url or os.environ.get("AISENSY_BASE_URL") or self.DEFAULT_URL
        if not api_key:
            raise TransportError("AISENSY_API_KEY is not configured")
        if not campaign:
            raise TransportError("AISENSY_CAMPAIGN (approved template name) is not configured")
        return {"api_key": api_key, "campaign": campaign, "base_url": base_url}

    def send_template(self, *, to: str, params: List[str], idempotency_key: str) -> str:
        from sourcing._http import request_with_retry  # reuse retry/backoff helper
        import httpx  # lazy

        cfg = self._cfg()
        destination = to.lstrip("+").replace(" ", "")
        payload = {
            "apiKey": cfg["api_key"],
            "campaignName": cfg["campaign"],
            "destination": destination,
            "userName": self.sender_name,
            "templateParams": [str(p) for p in params],
            # tag the send so AiSensy webhooks / logs correlate to our job
            "tags": [idempotency_key],
        }
        try:
            resp = httpx.post(cfg["base_url"], json=payload, timeout=self.timeout)
        except Exception as exc:  # network/timeout
            raise TransportError("AiSensy request failed: {0}".format(exc)) from exc
        if resp.status_code >= 400:
            raise TransportError("AiSensy {0}: {1}".format(resp.status_code, resp.text[:200]))
        try:
            data = resp.json()
        except Exception:
            data = {}
        # AiSensy returns a submission id / success flag; fall back to the idem key.
        return str(data.get("messageId") or data.get("submitted_message_id") or idempotency_key)


# ---- Fake transport (tests) -------------------------------------------------

class FakeWhatsAppTransport(WhatsAppTransport):
    """In-memory transport for tests. Records sends; optionally raises."""

    def __init__(self, raise_on_send: Optional[BaseException] = None) -> None:
        self.sent: List[Dict[str, Any]] = []
        self.raise_on_send = raise_on_send

    def send_template(self, *, to: str, params: List[str], idempotency_key: str) -> str:
        if self.raise_on_send is not None:
            raise self.raise_on_send
        provider_id = "fake-wa-{0}".format(uuid.uuid4().hex[:12])
        self.sent.append({
            "to": to,
            "params": list(params),
            "idempotency_key": idempotency_key,
            "provider_id": provider_id,
        })
        return provider_id


# ---- The adapter ------------------------------------------------------------

class WhatsAppAdapter:
    """Channel adapter for 'whatsapp'. Sends the message text as the approved
    template's first parameter via a :class:`WhatsAppTransport`. Returns a
    delivery-status dict; never raises on a send failure (reports 'failed')."""

    def __init__(self, transport: Optional[WhatsAppTransport] = None) -> None:
        self._transport = transport

    @property
    def transport(self) -> WhatsAppTransport:
        # Lazily build the AiSensy transport from env if none injected. Tests
        # inject a Fake, so the real path (and its env reads) is never touched.
        if self._transport is None:
            self._transport = AiSensyTransport()
        return self._transport

    def send(
        self, *, to: str, subject: Optional[str] = None, body: str, idempotency_key: str
    ) -> Dict[str, Any]:
        """Send one WhatsApp template message. ``subject`` is ignored (WhatsApp
        has none); ``body`` becomes the template's first parameter. Returns
        ``{'provider_id', 'status', 'idempotency_key'[, 'error']}``."""
        try:
            provider_id = self.transport.send_template(
                to=to, params=[body], idempotency_key=idempotency_key
            )
        except Exception as exc:  # surface as failed status, never swallow
            return {
                "provider_id": None,
                "status": "failed",
                "idempotency_key": idempotency_key,
                "error": str(exc),
            }
        return {
            "provider_id": provider_id,
            "status": "sent",
            "idempotency_key": idempotency_key,
        }


# Default adapter registered for 'whatsapp'. Lazily builds AiSensyTransport only
# if actually used with no injected transport, so importing/registering never
# requires credentials.
default_whatsapp_adapter = WhatsAppAdapter()

registry.register("whatsapp", default_whatsapp_adapter)
