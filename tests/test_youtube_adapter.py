"""Unit tests for the YouTube adapter (FakeYouTubeClient, no DB, no network).

Asserts:
  * an approved spec yields candidates with the right follower_band / niche /
    platform=youtube and a usable contact/handle,
  * a 403 quotaExceeded marks the spec partially-sourced with a resume cursor
    and raises NO exception (partial progress kept),
  * an unapproved spec yields nothing (the gate),
  * subscriber_band boundaries.
"""
from __future__ import annotations

import pytest

from targeting.brain import TargetSpec
from sourcing.youtube.adapter import (
    YouTubeAdapter,
    FakeYouTubeClient,
    subscriber_band,
)


def _channel(cid, title, subs, desc="", topic=None, custom=None):
    return {
        "id": cid,
        "snippet": {
            "title": title,
            "description": desc,
            "customUrl": custom or "",
            "country": "IN",
        },
        "statistics": {"subscriberCount": str(subs), "videoCount": "100", "viewCount": "999"},
        "topicDetails": {"topicCategories": [topic] if topic else []},
    }


def test_approved_spec_yields_candidates_with_band_niche_platform():
    ch = _channel(
        "UC1", "Money Mindset Co", 45000,
        desc="DM for coaching. Email: hi@money.co",
        topic="https://en.wikipedia.org/wiki/Lifestyle_(sociology)",
        custom="@moneymindset",
    )
    client = FakeYouTubeClient(
        pages={"money mindset coach": [{"channel_ids": ["UC1"], "next": None}]},
        channels={"UC1": ch},
    )
    spec = TargetSpec(id=7, mode="keyword", expanded_keywords=["money mindset coach"], approved=True)

    cands = list(YouTubeAdapter(client=client).run(spec))

    assert len(cands) == 1
    c = cands[0]
    assert c["lead_fields"]["platform"] == "youtube"
    assert c["lead_fields"]["source"] == "youtube"
    assert c["lead_fields"]["follower_band"] == "mid"          # 10k–100k
    assert c["lead_fields"]["follower_count"] == 45000
    assert c["lead_fields"]["niche"] == "lifestyle"            # from topicCategories
    assert c["email"] == "hi@money.co"                          # parsed from about
    assert c["handle"] == "@moneymindset"
    assert c["attributes"]["channel_id"] == "UC1"
    # completed cleanly -> status complete, no resume cursor left behind
    assert spec.attributes.get("youtube_status") == "complete"
    assert "youtube_resume" not in spec.attributes


def test_paging_dedupes_channels_across_pages():
    ch1 = _channel("UC1", "A", 2000)
    ch2 = _channel("UC2", "B", 3000)
    client = FakeYouTubeClient(
        pages={
            "kw": [
                {"channel_ids": ["UC1", "UC2"], "next": "1"},
                {"channel_ids": ["UC2"], "next": None},   # UC2 repeats — must dedupe
            ]
        },
        channels={"UC1": ch1, "UC2": ch2},
    )
    spec = TargetSpec(id=1, mode="keyword", expanded_keywords=["kw"], approved=True)
    cands = list(YouTubeAdapter(client=client).run(spec))
    ids = sorted(c["attributes"]["channel_id"] for c in cands)
    assert ids == ["UC1", "UC2"]   # two distinct channels, no duplicate


def test_quota_exceeded_marks_partial_and_saves_resume_cursor_no_exception():
    ch1 = _channel("UC1", "A", 5000)
    # quota_after=1: first search.list page succeeds, the second raises.
    client = FakeYouTubeClient(
        pages={"kw": [
            {"channel_ids": ["UC1"], "next": "1"},   # page 0 OK
            {"channel_ids": ["UC2"], "next": None},  # page 1 -> quota
        ]},
        channels={"UC1": ch1},
        quota_after=1,
    )
    spec = TargetSpec(id=42, mode="keyword", expanded_keywords=["kw"], approved=True)

    # Must NOT raise — partial progress is kept.
    cands = list(YouTubeAdapter(client=client).run(spec))

    assert len(cands) == 1                                  # the one we got before quota
    assert spec.attributes.get("youtube_status") == "quota_exceeded"
    cursor = spec.attributes.get("youtube_resume")
    assert cursor is not None
    assert cursor["keyword_index"] == 0
    assert cursor["page_token"] == "1"                      # resume at the next page


def test_resume_cursor_starts_mid_keyword():
    ch2 = _channel("UC2", "B", 8000)
    client = FakeYouTubeClient(
        pages={"kw": [
            {"channel_ids": ["UC1"], "next": "1"},
            {"channel_ids": ["UC2"], "next": None},
        ]},
        channels={"UC2": ch2},
    )
    # Pre-seed a resume cursor: skip page 0, start at page 1.
    spec = TargetSpec(
        id=43, mode="keyword", expanded_keywords=["kw"], approved=True,
        attributes={"youtube_resume": {"keyword_index": 0, "page_token": "1"}},
    )
    cands = list(YouTubeAdapter(client=client).run(spec))
    ids = [c["attributes"]["channel_id"] for c in cands]
    assert ids == ["UC2"]   # resumed at page 1, did not re-fetch UC1


def test_unapproved_spec_yields_nothing():
    client = FakeYouTubeClient(
        pages={"kw": [{"channel_ids": ["UC1"], "next": None}]},
        channels={"UC1": _channel("UC1", "A", 1000)},
    )
    spec = TargetSpec(id=5, mode="deep", expanded_keywords=["kw"], approved=False)
    assert list(YouTubeAdapter(client=client).run(spec)) == []


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
def test_subscriber_band_boundaries(count, band):
    assert subscriber_band(count) == band
