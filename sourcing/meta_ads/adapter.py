"""Meta Ads source adapter — a CSV/JSON -> loader bridge (L1 / M1).

We do NOT rewrite the hardened Meta Ad Library scraper
(``sourcing/meta_ads/facebook_ads_scraper.py`` + ``run_scraper.py``). This
adapter is a thin bridge that:

  1. reads keywords from an APPROVED target_spec,
  2. invokes the existing scraper with those keywords (via an injectable runner
     so we never couple to its async internals here), getting back a path to the
     scraper's comprehensive ``_full.json``,
  3. feeds that file through the EXISTING loader
     (:func:`data.loader.load_candidates` via :func:`data.loader.build_candidates`)
     passing ``target_spec.id`` so every resolved lead is attributed to the spec.

The adapter therefore *yields candidates* (honoring the SourceAdapter contract)
built from the scraper output, leaving DB resolution to the loader — exactly
like the YouTube adapter. The orchestration layer pipes the candidates through
``data.loader.load_candidates(..., target_spec_id=spec.id)``.

For tests, inject either a ``scraper_output_path`` (a ready ``_full.json``) or a
``scraper_runner`` callable ``(keywords) -> path``. No scraper, no network, no DB
needed to unit-test the bridge.

Python 3.9 compatible.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Iterable, List, Optional

from sourcing.base import SourceAdapter

logger = logging.getLogger("sourcing.meta_ads")

# Type of the injectable scraper runner: keywords -> path to a _full.json.
ScraperRunner = Callable[[List[str]], str]


def _default_scraper_runner(keywords: List[str], max_ads_to_detail: int = 100) -> str:  # pragma: no cover - needs live scraper
    """Real runner: drive the hardened scraper for ``keywords`` -> _full.json path.

    The hardened scraper is async and writes timestamped files into
    ``scraper_results/``. Rather than reimplement its run loop, we import the
    scraper class lazily and run a minimal multi-query pass that mirrors
    ``run_scraper.py``'s output contract (a ``*_full.json`` with an ``ads`` list).
    Importing this adapter never imports the scraper (which needs playwright); it
    is only pulled in when this default runner actually executes.
    """
    import asyncio
    import json
    import os
    from datetime import datetime

    # The scraper modules use flat imports; add their dir to sys.path lazily.
    import sys
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    from facebook_ads_scraper import FacebookAdsLibraryScraper  # type: ignore

    async def _run() -> str:
        scraper = FacebookAdsLibraryScraper()
        ads: List[Dict[str, Any]] = []
        for q in keywords:
            try:
                res = await scraper.scrape_ads(
                    query=q, country="IN", active_status="active",
                    ad_type="all", media_type="all", max_scrolls=50,
                    scrape_advertiser_details=True, max_ads_to_detail=max_ads_to_detail,
                    filter_by_keywords=True, min_keyword_matches=1,
                )
            except Exception as exc:
                logger.warning("meta scraper error for %r: %s", q, exc)
                continue
            for ad in res:
                if isinstance(ad, dict) and "error" in ad and "advertiser" not in ad:
                    continue
                ad["search_query"] = q
                ads.append(ad)
        out_dir = "scraper_results"
        os.makedirs(out_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(out_dir, "multi_keyword_{0}_full.json".format(ts))
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"metadata": {"queries": keywords}, "ads": ads}, f,
                      ensure_ascii=False, indent=2, default=str)
        return path

    return asyncio.run(_run())


class MetaAdsAdapter(SourceAdapter):
    """SourceAdapter bridging the hardened Meta scraper to the L0 loader.

    Construct with either:
      * ``scraper_output_path`` — a ready ``_full.json`` to load (tests / replay),
        OR
      * ``scraper_runner`` — a callable ``(keywords) -> path`` (defaults to the
        real scraper). The ``scraper_output_path`` short-circuits the runner.
    """

    name = "meta_ads"

    def __init__(
        self,
        scraper_output_path: Optional[str] = None,
        scraper_runner: Optional[ScraperRunner] = None,
    ) -> None:
        self.scraper_output_path = scraper_output_path
        self.scraper_runner = scraper_runner or _default_scraper_runner

    def run(self, target_spec) -> Iterable[Dict[str, Any]]:
        # GATE: only ever source an approved spec.
        if not self.require_approved(target_spec):
            logger.info("meta_ads: spec not approved; sourcing nothing")
            return

        keywords = _spec_keywords(target_spec)
        if not keywords and self.scraper_output_path is None:
            logger.info("meta_ads: spec has no keywords; nothing to source")
            return

        # Reuse an existing scraper output if provided, else run the scraper.
        # "Max leads" (spec.limit) caps how many advertiser pages get the slow
        # deep-scrape, so a small target finishes fast instead of grinding all.
        path = self.scraper_output_path
        if path is None:
            limit = _spec_limit(target_spec)
            max_detail = limit if (limit and limit > 0) else 100
            try:
                path = self.scraper_runner(keywords, max_detail)
            except TypeError:
                path = self.scraper_runner(keywords)  # custom 1-arg runner

        # Bridge through the EXISTING loader's candidate builder. We yield
        # candidate dicts (not Candidate objects) so the orchestration layer can
        # pipe them through load_candidates(..., target_spec_id=spec.id) exactly
        # like every other adapter.
        from data.loader import load_json, build_candidates

        ads = load_json(path)
        spec_id = getattr(target_spec, "id", None)
        if spec_id is None and isinstance(target_spec, dict):
            spec_id = target_spec.get("id")
        candidates = build_candidates(ads, target_spec_id=spec_id)
        for cand in candidates:
            yield _candidate_to_dict(cand)


def _candidate_to_dict(cand) -> Dict[str, Any]:
    """Flatten a data.identity.Candidate back to the loader's raw dict shape.

    Keeps the adapter's output uniform with the YouTube adapter (raw dicts that
    ``data.loader.load_candidates`` re-builds), so one orchestration path handles
    every source.
    """
    return {
        "page": cand.page,
        "email": cand.email,
        "phone": cand.phone,
        "handle": cand.handle,
        "attributes": dict(cand.attributes or {}),
        "lead_fields": dict(cand.lead_fields or {}),
        "channels": list(cand.channels or []),
        "target_spec_id": cand.target_spec_id,
    }


def _spec_keywords(target_spec) -> List[str]:
    if hasattr(target_spec, "keywords"):
        kws = target_spec.keywords()
    elif isinstance(target_spec, dict):
        kws = target_spec.get("expanded_keywords") or target_spec.get("seed_keywords") or []
    else:
        kws = []
    return [k for k in kws if k]


def _spec_limit(target_spec) -> Optional[int]:
    """Per-run "Max leads" cap, if the caller put one on the spec (Quick Harvest
    passes a dict with ``limit``). Used to bound the slow deep-scrape."""
    val = None
    if isinstance(target_spec, dict):
        val = target_spec.get("limit")
    else:
        val = getattr(target_spec, "limit", None)
    try:
        return int(val) if val else None
    except (TypeError, ValueError):
        return None


# Register so the engine can discover this adapter by name / via enabled_adapters().
from sourcing.base import register as _register  # noqa: E402

_register("meta_ads", MetaAdsAdapter)
