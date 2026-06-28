"""L3 — Personalization Engine (M4).

Segment-aware, Haiku-class copy generation for the Exly outbound engine.

Public surface:
  * :mod:`personalization.value_props` — the value-prop library keyed by
    ``segment x angle`` plus :func:`~personalization.value_props.pick_angle`.
  * :mod:`personalization.guardrail` — the P4 guardrail
    (:func:`~personalization.guardrail.passes_guardrail`).
  * :mod:`personalization.generate` — the :class:`~personalization.generate.Generator`
    interface, :class:`~personalization.generate.AnthropicGenerator`,
    :class:`~personalization.generate.FakeGenerator`,
    :func:`~personalization.generate.generate_message` and
    :func:`~personalization.generate.personalize_and_queue`.

Builds on L0 (the frozen 0001 schema: ``leads.attributes`` JSONB carries the
scraped signals, ``messages`` carries variant/angle/subject/body) and the L-C
orchestration outbox (``orchestration.queue.enqueue``).
"""
from __future__ import annotations

from personalization.value_props import ANGLES, SEGMENTS, get_value_prop, pick_angle

__all__ = [
    "ANGLES",
    "SEGMENTS",
    "get_value_prop",
    "pick_angle",
]
