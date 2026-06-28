"""L4 — channel dispatch adapters.

The orchestrator (Lane C) owns the durable outbox queue and the
claim -> suppression-recheck -> ratecheck -> send -> record pipeline. The *send*
step is a seam: ``orchestration.tasks.send_via_channel`` looks up the adapter
registered for a channel type here and calls it.

An adapter is any callable/object exposing::

    send(*, to, subject, body, idempotency_key) -> dict(provider_id=..., status=...)

and is wired in via :func:`dispatch.registry.register`. Importing this package
imports the built-in adapters (currently email) so they self-register.
"""
from __future__ import annotations

from . import registry  # noqa: F401  (re-exported for convenience)

# Importing the email subpackage runs its module-level register() call, so
# ``get_adapter('email')`` works as soon as ``dispatch`` is imported. Kept at
# the bottom and guarded so a partially-configured email module never makes the
# whole package unimportable.
from . import email as _email  # noqa: E402,F401

__all__ = ["registry"]
