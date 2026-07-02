"""Smartlead API client for cold-email at scale (L4).

Smartlead is *campaign-based*: you build a campaign in the Smartlead dashboard
(email sequence + warmed sending mailboxes + schedule), and this pushes your CRM
leads — with their personalized fields — into that campaign. Smartlead then does
the actual sending, warmup, and mailbox rotation. So the engine's job is
"source → dedupe → score → personalize → hand off to the campaign", not raw SMTP.

Seam (same shape as the sourcing clients):
  * :class:`SmartleadClient` — abstract.
      - :class:`HttpSmartleadClient` — real API over ``SMARTLEAD_API_KEY``.
        ``httpx`` is lazy; the key is read at call time, so importing this module
        never needs httpx or credentials.
      - :class:`FakeSmartleadClient` — offline, records pushes, for tests.

Docs: Smartlead "Add leads to a campaign" — POST /campaigns/{id}/leads.
Python 3.9 compatible.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger("dispatch.smartlead")

# Smartlead caps a single add-leads call; batch under it.
BATCH_SIZE = 100


class SmartleadError(RuntimeError):
    """Raised on a Smartlead API error."""


class SmartleadClient:
    """Abstract Smartlead client.

    ``add_leads`` pushes a batch of lead dicts into ``campaign_id`` and returns
    the API response. Each lead dict may carry: ``email`` (required),
    ``first_name``, ``last_name``, ``company_name``, and ``custom_fields`` (a map
    the campaign's ``{{variables}}`` reference).
    """

    def add_leads(self, campaign_id: str, leads: List[Dict[str, Any]]) -> Dict[str, Any]:
        raise NotImplementedError


class HttpSmartleadClient(SmartleadClient):
    """Real Smartlead API client (lazy ``httpx``)."""

    DEFAULT_BASE = "https://server.smartlead.ai/api/v1"

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: float = 30.0,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url
        self.timeout = timeout

    def _key(self) -> str:
        key = self._api_key or os.environ.get("SMARTLEAD_API_KEY")
        if not key:
            raise SmartleadError(
                "SMARTLEAD_API_KEY is not set — get it from Smartlead → Settings → API."
            )
        return key

    def _base(self) -> str:
        return (self._base_url or os.environ.get("SMARTLEAD_BASE_URL") or self.DEFAULT_BASE).rstrip("/")

    def add_leads(self, campaign_id: str, leads: List[Dict[str, Any]]) -> Dict[str, Any]:
        import httpx  # lazy

        url = "{0}/campaigns/{1}/leads".format(self._base(), campaign_id)
        body = {
            "lead_list": leads,
            "settings": {
                "ignore_global_block_list": False,
                "ignore_unsubscribe_list": False,
                "ignore_duplicate_leads_in_other_campaign": False,
            },
        }
        try:
            resp = httpx.post(url, params={"api_key": self._key()}, json=body, timeout=self.timeout)
        except Exception as exc:
            raise SmartleadError("Smartlead request failed: {0}".format(exc)) from exc
        if resp.status_code >= 400:
            raise SmartleadError("Smartlead {0}: {1}".format(resp.status_code, resp.text[:300]))
        try:
            return resp.json()
        except Exception:
            return {"ok": True}


class FakeSmartleadClient(SmartleadClient):
    """Offline client for tests — records every push."""

    def __init__(self, fail: bool = False) -> None:
        self.pushed: List[Dict[str, Any]] = []
        self.fail = fail

    def add_leads(self, campaign_id: str, leads: List[Dict[str, Any]]) -> Dict[str, Any]:
        if self.fail:
            raise SmartleadError("forced failure")
        self.pushed.append({"campaign_id": campaign_id, "leads": list(leads)})
        return {"ok": True, "upload_count": len(leads)}
