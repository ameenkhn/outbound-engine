"""Unified multi-source harvest runner (L1 / M1).

Drives EVERY sourcing channel — Meta Ad Library, Instagram, LinkedIn, YouTube —
over a single APPROVED target_spec, through the shared :class:`SourceAdapter`
registry. Each adapter yields loader-ready candidate dicts; this runner collects
them, dedupes by identity signal, and prints a per-source summary. It does NOT
write to the DB itself — feed the returned candidates through
``data.loader.load_candidates(..., target_spec_id=spec.id)`` to resolve/dedupe
into leads (same contract every adapter honours).

Usage (programmatic)::

    from targeting.brain import TargetSpec
    from sourcing.harvest_all import harvest_all

    spec = TargetSpec(id=1, mode="keyword",
                      expanded_keywords=["fitness coach", "yoga teacher"],
                      approved=True)
    candidates, summary = harvest_all(spec)   # sources='all'
    # candidates -> list of dicts; summary -> {source: count}

Usage (CLI)::

    python -m sourcing.harvest_all '{"id":1,"approved":true,"expanded_keywords":["fitness coach"]}'
    python -m sourcing.harvest_all --sources instagram,linkedin '{...spec...}'

Sources default to all four. A source whose provider isn't configured (missing
API key/base) is skipped with a logged warning rather than aborting the run, so
one missing credential never blocks the others. Python 3.9 compatible.
"""
from __future__ import annotations

import json
import logging
import sys
from typing import Any, Dict, Iterable, List, Optional, Tuple

# Importing each adapter module registers it under its channel name.
import sourcing.meta_ads.adapter   # noqa: F401  registers "meta_ads"
import sourcing.instagram.adapter  # noqa: F401  registers "instagram"
import sourcing.linkedin.adapter   # noqa: F401  registers "linkedin"
import sourcing.youtube.adapter    # noqa: F401  registers "youtube"
import sourcing.websearch.adapter  # noqa: F401  registers "websearch"

from sourcing.base import get_adapter, is_registered

logger = logging.getLogger("sourcing.harvest_all")

#: The default DISCOVERY fan-out, in a deterministic order. Web search is NOT
#: here on purpose — broad web-search discovery is token/credit-expensive, so it
#: runs instead as a *targeted enrichment fallback* (see ``enrich_with_websearch``
#: in :func:`harvest_all`): only leads a platform source returned WITHOUT an
#: email/phone get a single web lookup to fill the gap.
ALL_SOURCES: Tuple[str, ...] = ("meta_ads", "instagram", "linkedin", "youtube")


def _candidate_dedup_key(cand: Dict[str, Any]) -> Optional[str]:
    """Strongest available identity signal for cross-source dedupe.

    Mirrors the resolver's precedence (page > email > phone > handle). Two
    candidates that share the strongest signal collapse to one here; the L0
    resolver still does the authoritative merge with its false-merge guard.
    """
    for field in ("page", "email", "phone", "handle"):
        val = cand.get(field)
        if val:
            return "{0}:{1}".format(field, str(val).strip().lower())
    return None


def _run_one_source(name: str, target_spec, skip_known=None) -> List[Dict[str, Any]]:
    """Run a single registered adapter and return its raw candidate list.

    ``skip_known`` (optional ``handle -> bool``) is attached to the adapter so it
    can skip already-known creators before the costly profile fetch. Adapters
    that don't support it simply ignore the attribute.
    """
    adapter = get_adapter(name)
    if skip_known is not None:
        try:
            adapter.skip_known = skip_known
        except Exception:
            pass  # adapter doesn't accept the hook; harmless
    return list(adapter.run(target_spec))


def harvest_all(
    target_spec,
    sources: Optional[Iterable[str]] = None,
    continue_on_error: bool = True,
    concurrent: bool = True,
    enrich_with_websearch: bool = True,
    enrich_budget: int = 25,
    enrich_client: Any = None,
    skip_known: Any = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """Run the selected source adapters over one approved spec.

    Returns ``(candidates, summary)`` where ``candidates`` is the deduped list of
    loader-ready dicts and ``summary`` maps each source name to how many *new*
    (post-dedupe) candidates it contributed. An unapproved spec yields nothing
    from every adapter (each enforces its own gate), so the result is empty.

    ``concurrent`` (default True) runs the sources in parallel threads — the work
    is network-I/O bound (provider APIs / the headless browser), so fanning out
    cuts a 4-source run to roughly the time of the slowest single source. Dedupe
    and the per-source tally are applied deterministically afterwards in
    :data:`ALL_SOURCES` order, so the result is identical to a serial run.

    WEB-SEARCH ENRICHMENT (token/credit-frugal — ``enrich_with_websearch``):
    After discovery, web search is used ONLY to fill gaps, never for broad
    discovery. Each lead a platform returned *without* an email/phone gets a
    single targeted web lookup (subject = name/handle + platform), capped at
    ``enrich_budget`` lookups for the whole run. Complete leads cost nothing.
    The count of leads filled is reported as ``summary['websearch_enrich']``.
    """
    selected = [s for s in (sources if sources is not None else ALL_SOURCES)]
    known = [s for s in selected if is_registered(s)]
    for s in selected:
        if s not in known:
            logger.warning("harvest_all: unknown source %r; skipping", s)

    # 1) Collect each source's candidates (in parallel when asked).
    raw: Dict[str, List[Dict[str, Any]]] = {s: [] for s in selected}

    def _safe(name: str) -> List[Dict[str, Any]]:
        try:
            return _run_one_source(name, target_spec, skip_known=skip_known)
        except Exception as exc:  # provider/network/config failure for ONE source
            if not continue_on_error:
                raise
            logger.warning("harvest_all: source %r failed: %s", name, exc)
            return []

    if concurrent and len(known) > 1:
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=len(known)) as pool:
            futures = {name: pool.submit(_safe, name) for name in known}
            for name, fut in futures.items():
                raw[name] = fut.result()
    else:
        for name in known:
            raw[name] = _safe(name)

    # 2) Dedupe deterministically in ALL_SOURCES order (stable, source-priority).
    order = [s for s in ALL_SOURCES if s in selected] + [
        s for s in selected if s not in ALL_SOURCES
    ]
    candidates: List[Dict[str, Any]] = []
    summary: Dict[str, int] = {s: 0 for s in selected}
    seen: "set[str]" = set()

    for name in order:
        added = 0
        for cand in raw.get(name, []):
            key = _candidate_dedup_key(cand)
            if key is not None:
                if key in seen:
                    continue
                seen.add(key)
            candidates.append(cand)
            added += 1
        summary[name] = added
        logger.info("harvest_all: %s contributed %d candidates", name, added)

    # 3) Web-search ENRICHMENT fallback — only fill leads missing email/phone,
    #    bounded by enrich_budget. Skipped entirely when disabled or empty.
    if enrich_with_websearch and candidates and enrich_budget > 0:
        try:
            from sourcing.websearch.adapter import enrich_candidates

            filled = enrich_candidates(candidates, client=enrich_client, budget=enrich_budget)
            summary["websearch_enrich"] = filled
            logger.info("harvest_all: web-search enrichment filled %d lead(s)", filled)
        except Exception as exc:  # never let enrichment failure lose discovery results
            if not continue_on_error:
                raise
            logger.warning("harvest_all: web-search enrichment skipped: %s", exc)

    logger.info(
        "harvest_all: %d total candidates across %s", len(candidates), ", ".join(selected)
    )
    return candidates, summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _spec_from_json(blob: str):
    """Build a TargetSpec from a JSON blob, falling back to a plain dict.

    Adapters accept either a TargetSpec or a mapping, so even if the targeting
    layer isn't importable we can still drive a run from a dict on the CLI.
    """
    data = json.loads(blob)
    try:
        from targeting.brain import TargetSpec
        return TargetSpec(
            id=data.get("id"),
            mode=data.get("mode", "keyword"),
            seed_keywords=data.get("seed_keywords"),
            expanded_keywords=data.get("expanded_keywords"),
            filters=data.get("filters"),
            attributes=data.get("attributes"),
            approved=bool(data.get("approved", False)),
        )
    except Exception:
        return data


def main(argv: Optional[List[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    argv = list(sys.argv[1:] if argv is None else argv)

    sources: Optional[List[str]] = None
    if argv and argv[0] == "--sources":
        if len(argv) < 2:
            print("--sources requires a comma-separated value", file=sys.stderr)
            return 2
        sources = [s.strip() for s in argv[1].split(",") if s.strip()]
        argv = argv[2:]

    if not argv:
        print(
            "usage: python -m sourcing.harvest_all [--sources a,b] '<spec-json>'",
            file=sys.stderr,
        )
        return 2

    spec = _spec_from_json(argv[0])
    candidates, summary = harvest_all(spec, sources=sources)

    json.dump(
        {"summary": summary, "total": len(candidates), "candidates": candidates},
        sys.stdout,
        ensure_ascii=False,
        indent=2,
        default=str,
    )
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
