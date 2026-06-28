"""L2 — Enrichment, rules-based ICP scoring, and priority queue.

This package turns the raw signals a lead carries in ``leads.attributes`` (and
``follower_count``) into two committed numbers on the lead row:

  * ``icp_score``    — a 0-100 rules-based ICP fit score (see :mod:`enrichment.score`)
  * ``priority_rank`` — the dispatcher's top-down work order (see :mod:`enrichment.run`)

Design split:
  * :mod:`enrichment.enrich` — PURE derivations from a lead's attributes. No DB,
    no network. Trivially unit-testable, identical at every call site.
  * :mod:`enrichment.score` — the auditable v1 ICP formula. One ``WEIGHTS`` dict,
    two hard gates, capped at 100.
  * :mod:`enrichment.run`   — the re-runnable batch CLI that reads eligible leads,
    joins their channels, enriches + scores, and assigns deterministic ranks.
"""
from __future__ import annotations
