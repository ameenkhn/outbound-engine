"""Unit tests for the Instagram adapter (FakeInstagramClient, no DB, no network).

Asserts:
  * an approved spec yields candidates with the right follower_band / niche /
    platform=instagram and a usable contact/handle,
  * a 429 rate-limit marks the spec partially-sourced with a resume cursor and
    raises NO exception (partial progress kept),
  * usernames dedupe across pages,
  * an unapproved spec yields nothing (the gate),
  * follower_band boundaries.
"""
from __future__ import annotations

import pytest

from targeting.brain import TargetSpec
from sourcing.instagram.adapter import (
    InstagramAdapter,
    FakeInstagramClient,
    follower_band,
)


def _profile(username, full_name, followers, bio="", category=None, email=None, phone=None):
    p = {
        "username": username,
        "full_name": full_name,
        "biography": bio,
        "follower_count": followers,
        "media_count": 120,
        "is_business_account": True,
        "id": "ig_" + username,
    }
    if category:
        p["category_name"] = category
    if email:
        p["business_email"] = email
    if phone:
        p["business_phone_number"] = phone
    return p


def test_approved_spec_yields_candidates_with_band_niche_platform():
    prof = _profile(
        "moneymindset", "Money Mindset Co", 45000,
        bio="DM for coaching. hi@money.co",
        category="Entrepreneur",
        phone="+91 98765 43210",
    )
    client = FakeInstagramClient(
        pages={"money mindset coach": [{"usernames": ["moneymindset"], "next": None}]},
        profiles={"moneymindset": prof},
    )
    spec = TargetSpec(id=7, mode="keyword", expanded_keywords=["money mindset coach"], approved=True)

    cands = list(InstagramAdapter(client=client).run(spec))

    assert len(cands) == 1
    c = cands[0]
    assert c["lead_fields"]["platform"] == "instagram"
    assert c["lead_fields"]["source"] == "instagram"
    assert c["lead_fields"]["follower_band"] == "mid"          # 10k–100k
    assert c["lead_fields"]["follower_count"] == 45000
    assert c["lead_fields"]["niche"] == "entrepreneur"         # from category
    assert c["email"] == "hi@money.co"                          # business_email
    assert c["phone"] == "+91 98765 43210"                     # cleaned by the loader later
    assert c["handle"] == "moneymindset"
    assert c["attributes"]["username"] == "moneymindset"
    assert spec.attributes.get("instagram_status") == "complete"
    assert "instagram_resume" not in spec.attributes


def test_email_parsed_from_bio_when_no_business_email():
    prof = _profile("yogaguru", "Yoga Guru", 8000, bio="bookings: yoga@guru.in")
    client = FakeInstagramClient(
        pages={"yoga": [{"usernames": ["yogaguru"], "next": None}]},
        profiles={"yogaguru": prof},
    )
    spec = TargetSpec(id=2, mode="keyword", expanded_keywords=["yoga"], approved=True)
    c = list(InstagramAdapter(client=client).run(spec))[0]
    assert c["email"] == "yoga@guru.in"
    assert c["lead_fields"]["follower_band"] == "micro"        # 1k–10k


def test_paging_dedupes_usernames_across_pages():
    client = FakeInstagramClient(
        pages={
            "kw": [
                {"usernames": ["a", "b"], "next": "1"},
                {"usernames": ["b"], "next": None},   # b repeats — must dedupe
            ]
        },
        profiles={"a": _profile("a", "A", 2000), "b": _profile("b", "B", 3000)},
    )
    spec = TargetSpec(id=1, mode="keyword", expanded_keywords=["kw"], approved=True)
    cands = list(InstagramAdapter(client=client).run(spec))
    handles = sorted(c["handle"] for c in cands)
    assert handles == ["a", "b"]   # two distinct profiles, no duplicate


def test_rate_limited_marks_partial_and_saves_resume_cursor_no_exception():
    client = FakeInstagramClient(
        pages={"kw": [
            {"usernames": ["a"], "next": "1"},   # page 0 OK
            {"usernames": ["b"], "next": None},  # page 1 -> rate limit
        ]},
        profiles={"a": _profile("a", "A", 5000)},
        rate_limit_after=1,
    )
    spec = TargetSpec(id=42, mode="keyword", expanded_keywords=["kw"], approved=True)

    cands = list(InstagramAdapter(client=client).run(spec))   # must NOT raise

    assert len(cands) == 1
    assert spec.attributes.get("instagram_status") == "rate_limited"
    cursor = spec.attributes.get("instagram_resume")
    assert cursor is not None
    assert cursor["keyword_index"] == 0
    assert cursor["page_cursor"] == "1"


def test_resume_cursor_starts_mid_keyword():
    client = FakeInstagramClient(
        pages={"kw": [
            {"usernames": ["a"], "next": "1"},
            {"usernames": ["b"], "next": None},
        ]},
        profiles={"b": _profile("b", "B", 8000)},
    )
    spec = TargetSpec(
        id=43, mode="keyword", expanded_keywords=["kw"], approved=True,
        attributes={"instagram_resume": {"keyword_index": 0, "page_cursor": "1"}},
    )
    cands = list(InstagramAdapter(client=client).run(spec))
    assert [c["handle"] for c in cands] == ["b"]   # resumed at page 1, did not re-fetch a


def test_unapproved_spec_yields_nothing():
    client = FakeInstagramClient(
        pages={"kw": [{"usernames": ["a"], "next": None}]},
        profiles={"a": _profile("a", "A", 1000)},
    )
    spec = TargetSpec(id=5, mode="deep", expanded_keywords=["kw"], approved=False)
    assert list(InstagramAdapter(client=client).run(spec)) == []


def test_skip_known_avoids_profile_fetch_for_existing_handles():
    """A known handle is skipped BEFORE get_profiles — saving the billed fetch."""
    fetched = []

    class RecordingClient(FakeInstagramClient):
        def get_profiles(self, usernames):
            fetched.extend(usernames)
            return super().get_profiles(usernames)

    client = RecordingClient(
        pages={"coach": [{"usernames": ["known_one", "new_two"], "next": None}]},
        profiles={
            "known_one": _profile("known_one", "Known", 5000),
            "new_two": _profile("new_two", "New", 6000),
        },
    )
    adapter = InstagramAdapter(client=client)
    adapter.skip_known = lambda h: h == "known_one"   # pretend known_one is in the DB
    spec = TargetSpec(id=9, mode="keyword", expanded_keywords=["coach"], approved=True)

    cands = list(adapter.run(spec))

    assert [c["handle"] for c in cands] == ["new_two"]   # only the new one yielded
    assert fetched == ["new_two"]                         # known_one was NEVER fetched


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
