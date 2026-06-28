"""Email channel adapter for L4.

Fills the orchestrator's send seam for ``channel_type='email'``. The adapter
owns *how* an email goes out; the transport owns *the wire*.

Layering:

    EmailAdapter.send(to, subject, body, idempotency_key)
        -> builds a MIME message (stamps idempotency_key as a header so the
           provider/ESP can dedupe and so bounces can be correlated)
        -> Transport.send_message(...)            # SMTP, fake, or future ESP HTTP
        -> returns {'provider_id': ..., 'status': 'sent'|'failed', ...}

A *send* error is never swallowed: the transport raises, the adapter catches,
and the returned dict carries ``status='failed'`` with the error string. The
orchestrator treats a non-'sent' status (or a raised exception) as a failed
attempt and reschedules with backoff — it must never record a 'sent' for a send
that didn't happen.

Transports:
  * :class:`SMTPTransport` — stdlib ``smtplib`` over STARTTLS, config from env
    (SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASS / EMAIL_FROM). No third-party
    dependency.
  * :class:`FakeTransport` — records sends in-memory for tests; can be primed to
    raise to exercise the failure path.

A future ESP HTTP transport (SendGrid/Postmark/SES API) would implement the same
:class:`Transport` interface using ``httpx`` (see requirements-email.txt) and
pass the idempotency_key as the provider's native idempotency header — no change
to the adapter or the registry needed.
"""
from __future__ import annotations

import os
import uuid
from email.message import EmailMessage
from typing import Any, Dict, List, Optional

from .. import registry

# Header used to carry the idempotency key on the wire. A custom X- header is
# safe across providers; SMTP has no native idempotency key, so this both lets a
# downstream ESP relay dedupe and lets us correlate provider bounces/DSNs back
# to the originating send_job.
IDEMPOTENCY_HEADER = "X-Idempotency-Key"

# Also set Message-ID from the idempotency key (when not already set) so the
# provider-visible id is stable across a reclaimed retry of the same job.
DEFAULT_MESSAGE_ID_DOMAIN = "outbound.local"


class TransportError(RuntimeError):
    """Raised by a Transport when the underlying send fails. The adapter catches
    this (and any Exception) and reports status='failed' rather than letting it
    bubble as a success."""


# ---- Transport interface ----------------------------------------------------

class Transport:
    """Interface a concrete email transport implements.

    Implementations send an already-built :class:`email.message.EmailMessage`
    and return a provider-side id (string). They MUST raise on failure (so the
    adapter can mark the send failed); returning normally means "handed to the
    provider".
    """

    def send_message(self, msg: EmailMessage, *, idempotency_key: str) -> str:
        raise NotImplementedError


# ---- SMTP transport (stdlib) ------------------------------------------------

class SMTPTransport(Transport):
    """Send via SMTP + STARTTLS using the standard library ``smtplib``.

    Config (env, with explicit-arg overrides for testing):
      SMTP_HOST   (required)   e.g. smtp.youresp.com
      SMTP_PORT   (default 587)
      SMTP_USER   (optional; if set with SMTP_PASS we authenticate)
      SMTP_PASS   (optional)
      EMAIL_FROM  (required)   the From: address (on the dedicated sending domain)

    STARTTLS is always attempted (port 587 submission). ``smtplib`` is stdlib —
    no pip install. The ``from_addr`` is read once at construction so a
    misconfigured environment fails loudly here, not mid-send.
    """

    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        user: Optional[str] = None,
        password: Optional[str] = None,
        from_addr: Optional[str] = None,
        timeout: float = 30.0,
        use_starttls: bool = True,
    ) -> None:
        self.host = host or os.environ.get("SMTP_HOST")
        self.port = int(port or os.environ.get("SMTP_PORT", 587))
        self.user = user if user is not None else os.environ.get("SMTP_USER")
        self.password = password if password is not None else os.environ.get("SMTP_PASS")
        self.from_addr = from_addr or os.environ.get("EMAIL_FROM")
        self.timeout = timeout
        self.use_starttls = use_starttls
        if not self.host:
            raise TransportError("SMTP_HOST is not configured")
        if not self.from_addr:
            raise TransportError("EMAIL_FROM is not configured")

    def send_message(self, msg: EmailMessage, *, idempotency_key: str) -> str:
        # smtplib is stdlib; import locally so module import never needs network
        # config and so a non-SMTP deployment (fake/ESP) doesn't pay for it.
        import smtplib

        try:
            with smtplib.SMTP(self.host, self.port, timeout=self.timeout) as server:
                server.ehlo()
                if self.use_starttls:
                    server.starttls()
                    server.ehlo()
                if self.user and self.password:
                    server.login(self.user, self.password)
                # send_message returns a dict of refused recipients (empty == ok).
                refused = server.send_message(msg)
            if refused:
                raise TransportError(f"recipients refused: {refused}")
        except TransportError:
            raise
        except Exception as exc:  # any smtplib/socket error -> transport failure
            raise TransportError(f"SMTP send failed: {exc}") from exc
        # SMTP has no provider id; surface the Message-ID we stamped so callers
        # have a stable handle for correlating DSNs.
        return msg.get("Message-ID") or idempotency_key


# ---- Fake transport (tests) -------------------------------------------------

class FakeTransport(Transport):
    """In-memory transport for tests. Records every send; optionally raises.

    * ``sent`` accumulates one record dict per successful send_message call.
    * Set ``raise_on_send`` (an Exception instance) to make the next/every send
      fail, exercising the adapter's failure path.
    """

    def __init__(self, raise_on_send: Optional[BaseException] = None) -> None:
        self.sent: List[Dict[str, Any]] = []
        self.raise_on_send = raise_on_send

    def send_message(self, msg: EmailMessage, *, idempotency_key: str) -> str:
        if self.raise_on_send is not None:
            raise self.raise_on_send
        provider_id = f"fake-{uuid.uuid4().hex[:12]}"
        self.sent.append(
            {
                "to": msg.get("To"),
                "from": msg.get("From"),
                "subject": msg.get("Subject"),
                "body": msg.get_content() if msg.get_content_maintype() == "text" else None,
                "idempotency_key": idempotency_key,
                "idempotency_header": msg.get(IDEMPOTENCY_HEADER),
                "message_id": msg.get("Message-ID"),
                "provider_id": provider_id,
            }
        )
        return provider_id


# ---- The adapter ------------------------------------------------------------

class EmailAdapter:
    """Channel adapter for 'email'. Builds the MIME message and delegates to a
    :class:`Transport`. Returns a delivery-status dict; never raises on a send
    failure (reports status='failed' instead) so the orchestrator can decide to
    reschedule."""

    def __init__(self, transport: Optional[Transport] = None, from_addr: Optional[str] = None) -> None:
        self._transport = transport
        self._from_addr = from_addr or os.environ.get("EMAIL_FROM")

    @property
    def transport(self) -> Transport:
        """Lazily build an SMTPTransport from env if none was injected. Tests
        always inject a FakeTransport, so the SMTP path (and its env reads) is
        never touched under test."""
        if self._transport is None:
            self._transport = SMTPTransport()
        return self._transport

    def _build_message(
        self, *, to: str, subject: Optional[str], body: str, idempotency_key: str
    ) -> EmailMessage:
        msg = EmailMessage()
        from_addr = self._from_addr or getattr(self._transport, "from_addr", None)
        if from_addr:
            msg["From"] = from_addr
        msg["To"] = to
        msg["Subject"] = subject or ""
        # Idempotency: stamp it as a header AND derive a stable Message-ID so a
        # reclaimed retry produces the same provider-visible id.
        msg[IDEMPOTENCY_HEADER] = idempotency_key
        msg["Message-ID"] = f"<{idempotency_key}@{DEFAULT_MESSAGE_ID_DOMAIN}>"
        msg.set_content(body)
        return msg

    def send(
        self, *, to: str, subject: Optional[str], body: str, idempotency_key: str
    ) -> Dict[str, Any]:
        """Send one email. Returns
        ``{'provider_id', 'status', 'idempotency_key'[, 'error']}``.

        ``status`` is 'sent' on success or 'failed' on any transport error. The
        idempotency_key is passed through to the transport (and stamped on the
        message) so a provider that supports dedupe sees it and a reclaimed
        retry can't double-send.
        """
        msg = self._build_message(
            to=to, subject=subject, body=body, idempotency_key=idempotency_key
        )
        try:
            provider_id = self.transport.send_message(msg, idempotency_key=idempotency_key)
        except Exception as exc:  # surface as failed status, do NOT swallow
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


# Default adapter instance registered for 'email'. It lazily builds an
# SMTPTransport only if actually used with no injected transport, so importing
# this module (and registering) never requires SMTP env to be present.
default_email_adapter = EmailAdapter()

# Self-register for the 'email' channel type (matches channels.type in 0001).
registry.register("email", default_email_adapter)
