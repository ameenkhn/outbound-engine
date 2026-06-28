"""Thin HTTP edge for inbound ESP webhooks (FastAPI).

This is a *wrapper*, not logic. All decisions live in :mod:`inbound.handlers`;
this module only parses an ESP payload and calls the handler with an open DB
connection. Keeping it thin means the handlers are testable without any HTTP or
framework, and this layer can be re-pointed at a different framework cheaply.

FastAPI is imported LAZILY (inside :func:`build_app`) and the module-level import
is guarded, so ``import inbound.webhook`` succeeds even when FastAPI is not
installed. Tests exercise the handlers/classifier directly; the HTTP layer is
validated against a live ESP webhook at integration time. Install the optional
deps with ``pip install -r requirements-inbound.txt`` to actually serve it.

ESP payload shape (provider-agnostic; the adapter maps the real ESP fields):

  POST /inbound/email   {"from": "<sender>", "body": "<reply text>"}
  POST /inbound/bounce  {"handle": "<channel handle>",
                         "channel_type": "email",
                         "kind": "hardbounce" | "complaint"}

The booking link dropped into warm-reply handoffs is read from the
``INBOUND_BOOKING_LINK`` env var (fallback to a placeholder) so it is config,
not code.
"""
from __future__ import annotations

import os
from typing import Optional

try:  # FastAPI is optional; guard so the module imports without it.
    import fastapi  # noqa: F401

    _FASTAPI_AVAILABLE = True
except Exception:  # pragma: no cover - exercised only when fastapi is absent
    _FASTAPI_AVAILABLE = False


# Default booking link for warm-reply handoffs; override via env.
DEFAULT_BOOKING_LINK = os.environ.get(
    "INBOUND_BOOKING_LINK", "https://cal.exly.com/demo"
)


def build_app(booking_link: Optional[str] = None):
    """Build and return the FastAPI app exposing the inbound webhooks.

    Raises :class:`RuntimeError` if FastAPI isn't installed, with the exact
    command to fix it — so a misconfigured deploy fails loudly instead of
    importing a half-broken app. The handlers it calls are the same ones the
    tests cover directly, so this layer carries no untested business logic.
    """
    if not _FASTAPI_AVAILABLE:
        raise RuntimeError(
            "FastAPI is not installed. Install the inbound HTTP deps with:\n"
            "  pip install -r requirements-inbound.txt"
        )

    # Imported here (not at module top) so importing this module never needs the
    # framework; only actually building the app does.
    from fastapi import FastAPI, Request

    from data.db import connect
    from . import handlers

    link = booking_link or DEFAULT_BOOKING_LINK
    app = FastAPI(title="Exly Inbound Capture", version="1")

    @app.post("/inbound/email")
    async def inbound_email(request: Request):  # pragma: no cover - HTTP layer
        payload = await request.json()
        from_email = payload.get("from") or payload.get("from_email") or ""
        body = payload.get("body") or payload.get("text") or ""
        with connect() as conn:
            result = handlers.handle_inbound_email(
                conn, from_email, body, booking_link=link
            )
        return result

    @app.post("/inbound/bounce")
    async def inbound_bounce(request: Request):  # pragma: no cover - HTTP layer
        payload = await request.json()
        handle = payload.get("handle") or payload.get("email") or ""
        channel_type = payload.get("channel_type") or "email"
        kind = payload.get("kind") or "hardbounce"
        with connect() as conn:
            result = handlers.handle_bounce(conn, handle, channel_type, kind)
        return result

    return app
