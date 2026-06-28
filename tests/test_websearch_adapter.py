"""Unit tests for the web-search adapter (FakeWebSearchClient, no DB, no network).

Asserts:
  * an approved spec yields candidates extracted from result url/snippet (social
    handle, email, phone, facebook page) with platform inferred + source=websearch,
  * results with no reachable signal are dropped,
  * keyword -> intent-suffixed queries,
  * a 429 saves a resume cursor and raises NO exception,
  * an unapproved spec yields nothing.
"""
from __future__ import annotations

from targeting.brain import TargetSpec
from sourcing.websearch.adapter import (
    WebSearchAdapter,
    FakeWebSearchClient,
    DuckDuckGoSearchClient,
    default_websearch_client,
    parse_ddg_html,
    _ddg_decode,
    result_to_candidate,
    needs_enrichment,
    enrich_candidates,
)


def test_extracts_social_handle_email_and_phone():
    res_ig = {
        "title": "Yoga With Maya",
        "url": "https://www.instagram.com/yogawithmaya/",
        "snippet": "Online yoga coach. Bookings: maya@yoga.in or +91 98765 43210",
    }
    c = result_to_candidate(res_ig, niche_hint="yoga")
    assert c is not None
    assert c["lead_fields"]["platform"] == "instagram"
    assert c["lead_fields"]["source"] == "websearch"
    assert c["handle"] == "yogawithmaya"
    assert c["email"] == "maya@yoga.in"
    assert c["phone"] == "+91 98765 43210"
    assert c["lead_fields"]["niche"] == "yoga"
    assert c["attributes"]["source_url"].startswith("https://www.instagram.com/")


def test_facebook_url_routes_into_page_identity():
    c = result_to_candidate({"title": "Fit Co", "url": "https://facebook.com/FitCo", "snippet": ""})
    assert c is not None
    assert c["page"] == "https://facebook.com/FitCo"   # strong identity, not handle
    assert c["handle"] is None
    assert c["lead_fields"]["platform"] == "facebook"


def test_linkedin_in_prefix_stripped():
    c = result_to_candidate({"title": "Jane", "url": "https://in.linkedin.com/in/jane-doe", "snippet": ""})
    assert c["lead_fields"]["platform"] == "linkedin"
    assert c["handle"] == "jane-doe"


def test_result_with_no_signal_is_dropped():
    c = result_to_candidate({"title": "Some blog", "url": "https://example.com/post", "snippet": "no contacts here"})
    assert c is None


def test_intent_suffixed_queries():
    a = WebSearchAdapter(client=FakeWebSearchClient())
    qs = a._queries(["fitness coach"])
    assert "fitness coach email" in qs
    assert "fitness coach contact" in qs
    assert "fitness coach instagram" in qs


def test_approved_spec_yields_and_dedupes():
    page = {"results": [
        {"title": "A", "url": "https://instagram.com/acoach", "snippet": "a@x.in"},
        {"title": "A dup", "url": "https://instagram.com/acoach", "snippet": "a@x.in"},  # dupe handle
        {"title": "no signal", "url": "https://example.com", "snippet": "nothing"},
    ], "next": None}
    # Every intent query returns the same page; dedupe must collapse across them.
    client = FakeWebSearchClient(pages={
        "coach email": [page], "coach contact": [page], "coach instagram": [page],
    })
    spec = TargetSpec(id=1, mode="keyword", expanded_keywords=["coach"], approved=True)
    cands = list(WebSearchAdapter(client=client).run(spec))
    handles = [c["handle"] for c in cands]
    assert handles == ["acoach"]                       # one, deduped across queries
    assert spec.attributes.get("websearch_status") == "complete"


def test_rate_limited_saves_cursor_no_exception():
    page = {"results": [{"title": "A", "url": "https://instagram.com/a", "snippet": "a@x.in"}], "next": "1"}
    client = FakeWebSearchClient(
        pages={"coach email": [page, {"results": [], "next": None}]},
        rate_limit_after=1,
    )
    spec = TargetSpec(id=2, mode="keyword", expanded_keywords=["coach"], approved=True)
    cands = list(WebSearchAdapter(client=client).run(spec))   # must NOT raise
    assert len(cands) == 1
    assert spec.attributes.get("websearch_status") == "rate_limited"
    assert spec.attributes.get("websearch_resume") is not None


def test_unapproved_spec_yields_nothing():
    client = FakeWebSearchClient(pages={"coach email": [{"results": [
        {"title": "A", "url": "https://instagram.com/a", "snippet": "a@x.in"}], "next": None}]})
    spec = TargetSpec(id=3, mode="keyword", expanded_keywords=["coach"], approved=False)
    assert list(WebSearchAdapter(client=client).run(spec)) == []


# --- enrichment fallback ----------------------------------------------------

def test_needs_enrichment_flags_missing_contact():
    assert needs_enrichment({"email": None, "phone": None}) is True
    assert needs_enrichment({"email": "a@x.in", "phone": None}) is True   # phone missing
    assert needs_enrichment({"email": "a@x.in", "phone": "+919876543210"}) is False


def test_enrich_fills_only_missing_and_respects_budget():
    # The query the adapter builds is "<subject> <platform> email contact".
    client = FakeWebSearchClient(pages={
        "Maya Yoga instagram email contact": [
            {"results": [{"title": "Maya", "url": "https://x.in",
                          "snippet": "reach maya@yoga.in / +91 98765 43210"}], "next": None}
        ],
    })
    complete = {"email": "have@x.in", "phone": "+919999999999",
                "lead_fields": {"platform": "instagram"}, "attributes": {"advertiser": "Done"}}
    incomplete = {"email": None, "phone": None,
                  "lead_fields": {"platform": "instagram"}, "attributes": {"advertiser": "Maya Yoga"}}

    filled = enrich_candidates([complete, incomplete], client=client, budget=25)

    assert filled == 1                                  # only the incomplete one
    assert incomplete["email"] == "maya@yoga.in"
    assert incomplete["phone"] == "+91 98765 43210"
    assert incomplete["attributes"]["contact_enriched_via"] == "websearch"
    assert complete["email"] == "have@x.in"             # untouched (additive only)
    assert client.search_calls == 1                     # complete lead cost nothing


def test_enrich_budget_zero_does_nothing():
    client = FakeWebSearchClient(pages={})
    incomplete = {"email": None, "phone": None, "lead_fields": {"platform": "web"},
                  "attributes": {"advertiser": "X"}}
    assert enrich_candidates([incomplete], client=client, budget=0) == 0
    assert client.search_calls == 0


# --- free DuckDuckGo backend -------------------------------------------------

def test_ddg_decode_extracts_real_url():
    href = "//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.instagram.com%2Fyogamaya%2F&rut=abc"
    assert _ddg_decode(href) == "https://www.instagram.com/yogamaya/"
    assert _ddg_decode("https://example.com/x") == "https://example.com/x"
    assert _ddg_decode("") is None


def test_parse_ddg_html_extracts_results():
    html = """
    <div class="result results_links web-result">
      <h2 class="result__title">
        <a class="result__a"
           href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.instagram.com%2Fyogamaya%2F">Yoga Maya (@yogamaya)</a>
      </h2>
      <a class="result__snippet">Online yoga coach in Delhi. Email maya@yoga.in</a>
    </div>
    <div class="result">
      <h2 class="result__title">
        <a class="result__a"
           href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fin.linkedin.com%2Fin%2Framesh-k">Ramesh K</a>
      </h2>
      <a class="result__snippet">Performance coach.</a>
    </div>
    """
    results = parse_ddg_html(html)
    assert len(results) == 2
    assert results[0]["url"] == "https://www.instagram.com/yogamaya/"
    assert "maya@yoga.in" in results[0]["snippet"]
    assert results[1]["url"] == "https://in.linkedin.com/in/ramesh-k"


def test_default_client_is_duckduckgo_without_api_base(monkeypatch):
    monkeypatch.delenv("WEBSEARCH_API_BASE", raising=False)
    assert isinstance(default_websearch_client(), DuckDuckGoSearchClient)


def test_ddg_client_parses_and_pages(monkeypatch):
    """search() parses a fake HTML response and offers a next-page cursor."""
    sample = """
    <div class="result"><h2 class="result__title">
      <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Finstagram.com%2Facoach">A Coach</a>
    </h2><a class="result__snippet">hi@a.in</a></div>
    """

    class _Resp:
        status_code = 200
        text = sample
        def raise_for_status(self): pass

    # search() does `from sourcing._http import request_with_retry`, which binds
    # the name in the sourcing._http module — patch it there.
    import sourcing._http as http
    monkeypatch.setattr(http, "request_with_retry", lambda *a, **k: _Resp(), raising=True)

    client = DuckDuckGoSearchClient()
    results, nxt = client.search("fitness coach instagram")
    assert results and results[0]["url"] == "https://instagram.com/acoach"
    assert nxt == "30"   # next offset offered
