"""Inbound capture (L6 / M7) — the warm-reply + suppression edge of the engine.

This package turns inbound signals from the email ESP (replies, bounces,
complaints) into the durable state the rest of the engine reacts to:

  * ``classify``  — pure, DB-free text classification: opt-out detection and
    intent labelling. Heavily unit-tested, no I/O, so the same rules run
    identically in tests and in production.
  * ``handlers``  — the DB logic. Looks a lead up by its channel handle and
    writes ``events`` / ``suppression`` rows and flips ``leads.status``,
    implementing decision 6A (opt-out => identity-wide suppression;
    hardbounce/complaint => channel-specific) and stop-on-reply.
  * ``webhook``   — a thin, lazily-imported FastAPI wrapper that parses ESP
    payloads and calls the handlers. The framework import is guarded so this
    package imports cleanly even when FastAPI is absent (tests exercise the
    handlers and classifier directly, not the HTTP layer).

Boundary (how the layers attach):
  * Reads:  inbound ESP payloads; ``channels``/``leads`` from data/.
  * Writes: ``events`` (reply/optout/bounce/complaint) + ``suppression`` +
    ``leads.status`` -> data/; warm replies -> a human-handoff payload.
"""
from __future__ import annotations

from .classify import classify_intent, is_optout
from .handlers import handle_bounce, handle_inbound_email

__all__ = [
    "classify_intent",
    "is_optout",
    "handle_inbound_email",
    "handle_bounce",
]
