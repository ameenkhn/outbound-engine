"""SourceAdapter — the contract every sourcing channel implements (L1).

PRD §11: every layer exposes a clean interface the next attaches to. Sourcing is
fan-out: Meta Ads, YouTube, (later) Instagram/LinkedIn. They differ wildly in
*how* they find creators, but they all agree on the shape of what they produce —
a stream of **candidate dicts** the L0 loader/resolver already consumes
(``page``/``email``/``phone``/``handle`` + ``attributes`` / ``lead_fields`` /
extra ``channels``; see :func:`data.identity.build_candidate`).

So the contract is deliberately tiny::

    class SourceAdapter:
        name: str
        def run(self, target_spec) -> Iterable[candidate]: ...

``run`` takes an APPROVED :class:`targeting.brain.TargetSpec` and yields
candidates. It does NOT touch the DB itself — the orchestration layer pipes the
candidates through ``data.loader.load_candidates(..., target_spec_id=spec.id)``
so every adapter gets dedup, the false-merge guard, and spec attribution for
free. Keeping adapters DB-free also makes them trivially unit-testable with a
fake client and no Postgres.

A small registry lets the engine discover adapters by name and enable/disable a
channel via config without import gymnastics.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, List, Optional


# A candidate is the loosely-typed dict data.identity.build_candidate consumes.
Candidate = Dict[str, Any]


class SourceAdapter:
    """Abstract sourcing adapter. Subclasses set ``name`` and implement ``run``.

    Invariants every adapter MUST honor:
      * ``run(target_spec)`` returns an *iterable* of candidate dicts (it may be
        a generator — quota-limited adapters stream and stop cleanly).
      * It yields nothing for an UNAPPROVED spec (sourcing only ever acts on
        human-/gate-approved audience definitions — see :meth:`require_approved`).
      * It performs NO database writes. Resolution/dedup is the loader's job.
    """

    #: Stable channel name, also the registry key (e.g. "youtube", "meta_ads").
    name: str = ""

    def run(self, target_spec) -> Iterable[Candidate]:
        raise NotImplementedError

    @staticmethod
    def require_approved(target_spec) -> bool:
        """True iff ``target_spec`` is approved and may be sourced.

        Accepts either a :class:`targeting.brain.TargetSpec` (``.approved``) or a
        plain mapping (``["approved"]``), so adapters and tests can pass either.
        Adapters call this at the top of ``run`` and yield nothing when False —
        the gate that guarantees we never source an unvetted audience.
        """
        approved = getattr(target_spec, "approved", None)
        if approved is None and isinstance(target_spec, dict):
            approved = target_spec.get("approved")
        return bool(approved)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

# name -> a zero-arg factory (or the class itself) that builds an adapter.
_REGISTRY: Dict[str, Callable[[], SourceAdapter]] = {}
# names that are switched on for this run (config-driven enable/disable).
_ENABLED: "set[str]" = set()


def register(
    name: str,
    factory: Callable[[], SourceAdapter],
    enabled: bool = True,
) -> Callable[[], SourceAdapter]:
    """Register an adapter ``factory`` under ``name``.

    ``factory`` is anything callable with no required args that returns a
    :class:`SourceAdapter` — typically the adapter class itself. Returns the
    factory so it can be used as a decorator. Re-registering a name overwrites
    it (last definition wins), which keeps test setup simple.
    """
    if not name:
        raise ValueError("adapter name must be a non-empty string")
    _REGISTRY[name] = factory
    if enabled:
        _ENABLED.add(name)
    else:
        _ENABLED.discard(name)
    return factory


def get_adapter(name: str) -> SourceAdapter:
    """Build and return the adapter registered under ``name``.

    Raises :class:`KeyError` with the list of known names if unregistered, so a
    typo fails loudly instead of silently sourcing nothing.
    """
    try:
        factory = _REGISTRY[name]
    except KeyError:
        known = ", ".join(sorted(_REGISTRY)) or "(none registered)"
        raise KeyError("no source adapter named {0!r}; known: {1}".format(name, known))
    return factory()


def enabled_adapters() -> List[SourceAdapter]:
    """Build one instance of every currently-enabled adapter (name order)."""
    return [get_adapter(n) for n in sorted(_ENABLED) if n in _REGISTRY]


def is_registered(name: str) -> bool:
    return name in _REGISTRY


def set_enabled(name: str, enabled: bool) -> None:
    """Flip a registered adapter on/off without re-registering it."""
    if enabled:
        if name not in _REGISTRY:
            raise KeyError("cannot enable unregistered adapter {0!r}".format(name))
        _ENABLED.add(name)
    else:
        _ENABLED.discard(name)


def _reset_registry_for_tests() -> None:
    """Clear the registry. Test-only — keeps cross-test state from leaking."""
    _REGISTRY.clear()
    _ENABLED.clear()
