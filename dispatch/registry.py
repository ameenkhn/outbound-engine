"""Channel-adapter registry.

A tiny indirection so the orchestrator's send step is decoupled from any
specific provider. Adapters register themselves at import time for a
``channel_type`` (matching ``channels.type`` in 0001: 'email' | 'whatsapp' |
'linkedin'); the dispatch task looks one up by the job's channel type and calls
it.

An adapter implements the ``send`` protocol::

    send(*, to: str, subject: str | None, body: str,
         idempotency_key: str) -> dict   # {'provider_id': ..., 'status': ...}

It may be a plain function or any object with a ``send`` method — both are
accepted. The registry only stores and returns the adapter; it does not call it.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional, Union

# An adapter is either a callable with the send() signature, or an object that
# has a .send(...) method. We normalize to "thing you can .send() or call" at
# dispatch time (see orchestration.tasks.send_via_channel).
Adapter = Union[Callable[..., Dict[str, Any]], Any]

_REGISTRY: Dict[str, Adapter] = {}


class UnknownChannelError(KeyError):
    """Raised by :func:`get_adapter` when no adapter is registered for a type."""


def register(channel_type: str, adapter: Adapter) -> Adapter:
    """Register ``adapter`` for ``channel_type``. Returns the adapter so it can
    be used as a decorator on a class or factory. Re-registering the same type
    overwrites (last writer wins) — handy for tests that swap in a FakeTransport
    -backed adapter."""
    if not channel_type:
        raise ValueError("channel_type must be a non-empty string")
    _REGISTRY[channel_type] = adapter
    return adapter


def unregister(channel_type: str) -> None:
    """Remove an adapter (no-op if absent). Mainly for test isolation."""
    _REGISTRY.pop(channel_type, None)


def get_adapter(channel_type: str, default: Optional[Adapter] = None) -> Adapter:
    """Return the adapter registered for ``channel_type``.

    If none is registered: return ``default`` when one is supplied, otherwise
    raise :class:`UnknownChannelError`. The orchestrator passes ``default=None``
    is *not* used — it lets the KeyError propagate and translates a missing
    adapter into its existing NotImplementedError->reschedule behavior.
    """
    try:
        return _REGISTRY[channel_type]
    except KeyError:
        if default is not None:
            return default
        raise UnknownChannelError(
            f"no dispatch adapter registered for channel_type={channel_type!r}; "
            f"known: {sorted(_REGISTRY)}"
        )


def has_adapter(channel_type: str) -> bool:
    """True if an adapter is registered for ``channel_type``."""
    return channel_type in _REGISTRY


def registered_channels() -> Dict[str, Adapter]:
    """Snapshot of the registry (copy; mutating it does not affect the registry)."""
    return dict(_REGISTRY)
