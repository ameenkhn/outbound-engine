"""L5 — Follow-up cadence engine (M6).

Multi-step follow-up sequences (D0/D3/D7) over the L0/Lane-C database, with the
M6 stop rules baked in (stop-on-reply, stop-on-opt-out, suppression-aware) and a
MAX_TOUCHES cap.

Public surface:
    * ``cadence``  — pure cadence data + math (offsets, next_step, MAX_TOUCHES).
    * ``engine.advance_cadences(conn, now)`` — enqueue due next-touches.
"""
from __future__ import annotations

from . import cadence, engine
from .cadence import (
    DEFAULT_CADENCE,
    DEFAULT_OFFSETS_DAYS,
    MAX_TOUCHES,
    CadenceStep,
    next_step,
)
from .engine import advance_cadences

__all__ = [
    "cadence",
    "engine",
    "advance_cadences",
    "next_step",
    "CadenceStep",
    "DEFAULT_CADENCE",
    "DEFAULT_OFFSETS_DAYS",
    "MAX_TOUCHES",
]
