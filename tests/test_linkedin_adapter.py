"""Unit tests for the LinkedIn adapter (FakeLinkedInClient, no DB, no network).

Asserts:
  * an approved spec yields candidates with the right follower_band / niche /
    platform=linkedin and a usable handle (the public slug),
  * a 429 rate-limit marks the spec partially-sourced with a resume cursor and
    raises NO exception (partial progress kept),
  * slugs dedupe across pages,
  * an unapproved spec yields nothing (the gate),
  * follower_band boundaries.
"""
from __future__ import annotations

import pytest

from targeting.brain import TargetSpec
from sourcing.linkedin.adapter import (
    LinkedInAdapter,
    FakeLinkedInClient,
    follower_band,
)


def _profile(slug, name, followers, headline="", industry=None, company=None, email=None):
    p = {
        "public_id": slug,
        "full_name": name,
        "headline": headline,
        "follower_count": followers,
        "profile_url": "https://www.linkedin.com/in/{0}".format(slug),
    }
    if industry:
        p["industry"] = industry
    if company:
        p["current_company"] = company
    if email:
        p["email"] = email
    return p


def test_approved_spec_yields_candidates_with_band_niche_platform():
    prof = _profile(
        "jane-doe", "Jane Doe", 45000,
        headline="Founder & Performance Coach",
        industry="Professional Training & Coaching",
        company="PeakMind",
        email="jane@peakmind.in",
    )
    client = FakeLinkedInClient(
        pages={"performance coach": [{"slugs": ["jane-doe"], "next": None}]},
        profiles={"jane-doe": prof},
    )
    spec = TargetSpec(id=7, mode="keyword", expanded_keywords=["performance coach"], approved=True)

    cands = list(LinkedInAdapter(client=client).run(spec))

    assert len(cands) == 1
    c = cands[0]
    assert c["lead_fields"]["platform"] == "linkedin"
    assert c["lead_fields"]["source"] == "linkedin"
    assert c["lead_fields"]["follower_band"] == "mid"          # 10k–100k
    assert c["lead_fields"]["follower_count"] == 45000
    assert c["lead_fields"]["niche"] == "professional training & coaching"
    assert c["email"] == "jane@peakmind.in"
    assert c["handle"] == "jane-doe"                            # the public slug
    assert c["attributes"]["company"] == "PeakMind"
    assert spec.attributes.get("linkedin_status") == "complete"
    assert "linkedin_resume" not in spec.attributes


def test_name_assembled_from_first_last_and_email_from_summary():
    prof = {
        "public_id": "ravi-k",
        "first_name": "Ravi",
        "last_name": "Kumar",
        "summary": "Reach me at ravi@growth.in",
        "connections": 8000,
    }
    client = FakeLinkedInClient(
        pages={"growth": [{"slugs": ["ravi-k"], "next": None}]},
        profiles={"ravi-k": prof},
    )
    spec = TargetSpec(id=2, mode="keyword", expanded_keywords=["growth"], approved=True)
    c = list(LinkedInAdapter(client=client).run(spec))[0]
    assert c["attributes"]["full_name"] == "Ravi Kumar"
    assert c["email"] == "ravi@growth.in"
    assert c["lead_fields"]["follower_band"] == "micro"        # 1k–10k


def test_paging_dedupes_slugs_across_pages():
    client = FakeLinkedInClient(
        pages={
            "kw": [
                {"slugs": ["a", "b"], "next": "1"},
                {"slugs": ["b"], "next": None},   # b repeats — must dedupe
            ]
        },
        profiles={"a": _profile("a", "A", 2000), "b": _profile("b", "B", 3000)},
    )
    spec = TargetSpec(id=1, mode="keyword", expanded_keywords=["kw"], approved=True)
    cands = list(LinkedInAdapter(client=client).run(spec))
    handles = sorted(c["handle"] for c in cands)
    assert handles == ["a", "b"]


def test_rate_limited_marks_partial_and_saves_resume_cursor_no_exception():
    client = FakeLinkedInClient(
        pages={"kw": [
            {"slugs": ["a"], "next": "1"},
            {"slugs": ["b"], "next": None},
        ]},
        profiles={"a": _profile("a", "A", 5000)},
        rate_limit_after=1,
    )
    spec = TargetSpec(id=42, mode="keyword", expanded_keywords=["kw"], approved=True)

    cands = list(LinkedInAdapter(client=client).run(spec))   # must NOT raise

    assert len(cands) == 1
    assert spec.attributes.get("linkedin_status") == "rate_limited"
    cursor = spec.attributes.get("linkedin_resume")
    assert cursor is not None
    assert cursor["keyword_index"] == 0
    assert cursor["page_cursor"] == "1"


def test_unapproved_spec_yields_nothing():
    client = FakeLinkedInClient(
        pages={"kw": [{"slugs": ["a"], "next": None}]},
        profiles={"a": _profile("a", "A", 1000)},
    )
    spec = TargetSpec(id=5, mode="deep", expanded_keywords=["kw"], approved=False)
    assert list(LinkedInAdapter(client=client).run(spec)) == []


@pytest.mark.parametrize(
    "count,band",
    [
        (None, None),
        (500, "nano"),
        (5000, "micro"),
        (45000, "mid"),
        (450000, "macro"),
        (4500000, "mega"),
    ],
)
def test_follower_band_boundaries(count, band):
    assert follower_band(count) == band
