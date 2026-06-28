"""Database connection helper for the L0 Lead DB.

Single place that resolves the Postgres DSN. Everything else imports
:func:`connect` / :func:`get_dsn` so there is exactly one source of truth for
how we reach the database (explicit > clever).

Config: the ``DATABASE_URL`` env var, e.g.
``postgresql://user:pass@localhost:5432/exly_outbound``. A local ``.env`` file
is loaded if present (via python-dotenv), so dev setups don't have to export it
by hand.
"""
from __future__ import annotations

import os
from typing import Optional

try:  # python-dotenv is optional at runtime; load .env if it's installed.
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover - dotenv simply absent
    pass


class ConfigError(RuntimeError):
    """Raised when required DB configuration is missing."""


def get_dsn(explicit: Optional[str] = None) -> str:
    """Return the Postgres DSN.

    Precedence: an explicit argument, then ``DATABASE_URL``. Raises
    :class:`ConfigError` with an actionable message if neither is set, so a
    misconfigured run fails loudly instead of silently connecting to nothing.
    """
    dsn = explicit or os.environ.get("DATABASE_URL")
    if not dsn:
        raise ConfigError(
            "DATABASE_URL is not set. Copy .env.example to .env and set it, e.g.\n"
            "  DATABASE_URL=postgresql://user:pass@localhost:5432/exly_outbound"
        )
    return dsn


def connect(explicit: Optional[str] = None):
    """Open a new psycopg connection. Caller owns closing it (use as a context
    manager). psycopg is imported lazily so importing this module never requires
    the driver to be installed (keeps the structural tests dependency-free).
    """
    import psycopg  # local import: only needed when actually connecting

    return psycopg.connect(get_dsn(explicit))
