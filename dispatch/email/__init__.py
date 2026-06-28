"""Email dispatch adapter (L4).

Importing this package registers the email adapter for channel_type 'email'
(see ``adapter.py``), so ``dispatch.registry.get_adapter('email')`` resolves it.
Re-exports the public surface for convenience.
"""
from __future__ import annotations

from . import deliverability  # noqa: F401
from .adapter import (  # noqa: F401
    EmailAdapter,
    FakeTransport,
    SMTPTransport,
    Transport,
    TransportError,
    default_email_adapter,
)
from .deliverability import daily_scope_key, warmup_cap  # noqa: F401

__all__ = [
    "EmailAdapter",
    "Transport",
    "SMTPTransport",
    "FakeTransport",
    "TransportError",
    "default_email_adapter",
    "warmup_cap",
    "daily_scope_key",
    "deliverability",
]
