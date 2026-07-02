"""Channel-aware LLM personalization (offline, FakeGenerator)."""
from __future__ import annotations

from personalization.generate import generate_message, build_prompt, FakeGenerator
from personalization.run import personalize_lead


def _lead():
    return {
        "id": 1, "niche": "yoga",
        "attributes": {
            "advertiser": "Maya Yoga", "ad_text": "Join my 30-day yoga challenge",
            "category": "Yoga studio",
        },
    }


def _imported_lead():
    # An imported NLP lead: no ad_text, but rich sub_category / audience / notes.
    return {
        "id": 2, "niche": "NLP practitioner certification",
        "attributes": {
            "advertiser": "NLP Coaching Academy", "sub_category": "NLP Practitioner Certification",
            "audience_size": "8000+ coaches; 36K IG", "notes": "Bangalore HQ, ABNLP accredited",
        },
    }


def test_email_has_subject_and_body():
    m = generate_message(_lead(), "creator", None, FakeGenerator(), channel="email")
    assert m["subject"]
    assert "Maya" in m["body"]


def test_whatsapp_is_short_and_has_no_subject():
    m = generate_message(_lead(), "creator", None, FakeGenerator(), channel="whatsapp")
    assert m["subject"] == ""
    assert len(m["body"]) <= 400
    assert "stop" in m["body"].lower()   # opt-out present


def test_build_prompt_system_differs_by_channel():
    assert "WhatsApp" in build_prompt(_lead(), "creator", "ease", channel="whatsapp")["system"]
    assert "Subject" in build_prompt(_lead(), "creator", "ease", channel="email")["system"]


def test_personalize_lead_generates_both_channels():
    out = personalize_lead(_lead(), FakeGenerator())
    assert out["msg_email_subject"] and out["msg_email_body"] and out["msg_whatsapp"]
    assert out["rejected"] == []


def test_imported_lead_passes_guardrail_via_subcategory():
    # sub_category / audience_size now count as concrete signals, so imported
    # leads (no ad_text) still produce guardrail-passing copy.
    out = personalize_lead(_imported_lead(), FakeGenerator())
    assert out.get("msg_whatsapp"), out["rejected"]
    assert out.get("msg_email_body"), out["rejected"]
