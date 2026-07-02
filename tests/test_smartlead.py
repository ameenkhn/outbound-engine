"""Unit tests for the Smartlead campaign integration (no network)."""
from __future__ import annotations

import pytest

from dispatch.smartlead.client import FakeSmartleadClient, HttpSmartleadClient, SmartleadError
from dispatch.smartlead.push import build_smartlead_lead, push_leads


def test_mapper_builds_lead_with_fields():
    row = {"email": "maya@yoga.in", "name": "Maya Yoga", "niche": "yoga",
           "sub_category": "NLP", "identity_key": "email:maya@yoga.in"}
    m = build_smartlead_lead(row)
    assert m["email"] == "maya@yoga.in"
    assert m["first_name"] == "Maya"
    assert m["company_name"] == "Maya Yoga"
    assert m["custom_fields"]["niche"] == "yoga"
    assert m["custom_fields"]["sub_category"] == "NLP"


def test_mapper_pulls_email_from_identity_key():
    m = build_smartlead_lead({"email": None, "identity_key": "email:hi@x.in", "name": "", "niche": None})
    assert m is not None and m["email"] == "hi@x.in"


def test_mapper_skips_leads_without_email():
    assert build_smartlead_lead({"email": "", "identity_key": "handle:coach", "name": "Coach"}) is None
    assert build_smartlead_lead({"email": "notanemail", "identity_key": "phone:+919876543210"}) is None


def test_push_leads_batches_and_counts():
    rows = [{"email": "a{0}@x.in".format(i), "name": "N{0}".format(i),
             "identity_key": "email:a{0}@x.in".format(i)} for i in range(5)]
    rows.append({"email": "", "identity_key": "handle:nope"})  # no email → skipped
    client = FakeSmartleadClient()
    stats = push_leads(client, "camp-1", rows)
    assert stats == {"eligible": 6, "mapped": 5, "pushed": 5}
    assert client.pushed[0]["campaign_id"] == "camp-1"
    assert len(client.pushed[0]["leads"]) == 5


def test_push_leads_reports_failure():
    client = FakeSmartleadClient(fail=True)
    with pytest.raises(SmartleadError):
        push_leads(client, "camp-1", [{"email": "a@x.in", "identity_key": "email:a@x.in"}])


def test_http_client_requires_key(monkeypatch):
    monkeypatch.delenv("SMARTLEAD_API_KEY", raising=False)
    with pytest.raises(SmartleadError):
        HttpSmartleadClient().add_leads("c", [{"email": "x@y.in"}])
