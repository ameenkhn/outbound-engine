"""AI Targeting brain (L1 / M1).

A Sonnet-class brain that turns intent into an approved :class:`TargetSpec`:

  * **Mode B (auto)** — seed keywords -> an expanded, *validated* query set ->
    a ``mode='keyword'`` spec written ``approved=TRUE``. A validation gate runs
    BEFORE the write: it caps the count, drops over-broad / duplicate terms, and
    dedupes against existing specs, so a bad expansion can never reach sourcing.

  * **Mode A (deep)** — a persona -> clarifying questions -> a structured
    audience breakdown in ``filters`` JSONB + keywords -> a ``mode='deep'`` spec
    written ``approved=FALSE`` (deep targeting needs human sign-off).

  * **approve(spec_id)** — the human sign-off: flips ``approved=TRUE``.

The real model is lazily imported (``anthropic``, model id
``claude-sonnet-4-6``); a :class:`FakeBrain` makes both modes deterministic and
offline for tests.
"""
from .brain import (
    Brain,
    FakeBrain,
    AnthropicBrain,
    TargetSpec,
    SONNET_MODEL,
    ValidationResult,
    expand_and_validate_keywords,
    run_mode_b,
    run_mode_a,
    approve,
)

__all__ = [
    "Brain",
    "FakeBrain",
    "AnthropicBrain",
    "TargetSpec",
    "SONNET_MODEL",
    "ValidationResult",
    "expand_and_validate_keywords",
    "run_mode_b",
    "run_mode_a",
    "approve",
]
