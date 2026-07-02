"""L8 pipeline — unit tests for the pure autonomy guardrails + wiring."""
from orchestration.pipeline import consent_ok, copy_for, fill


def test_consent_email_needs_deliverable_not_optout():
    assert consent_ok("email", {"deliverable": True, "opted_out": False})
    assert not consent_ok("email", {"deliverable": True, "opted_out": True})
    assert not consent_ok("email", {"deliverable": False, "opted_out": False})
    assert not consent_ok("email", None)


def test_consent_whatsapp_requires_optin():
    # WhatsApp is opt-in-led: deliverable + not opted-out is NOT enough.
    assert not consent_ok("whatsapp", {"deliverable": True, "opted_out": False, "opted_in": False})
    assert consent_ok("whatsapp", {"deliverable": True, "opted_out": False, "opted_in": True})
    assert not consent_ok("whatsapp", {"deliverable": True, "opted_out": True, "opted_in": True})


def test_copy_for_channels():
    attrs = {"msg_email_subject": "Hi", "msg_email_body": "Body", "msg_whatsapp": "WA"}
    assert copy_for("email", attrs) == ("Hi", "Body")
    assert copy_for("whatsapp", attrs) == ("", "WA")
    # missing copy → None (lead not personalized for that channel yet)
    assert copy_for("email", {"msg_whatsapp": "WA"}) is None
    assert copy_for("whatsapp", {}) is None


def test_fill_placeholders():
    out = fill("Hi {{first_name}}, love your {{niche}} work", "Maya Sharma", "NLP coaching")
    assert out == "Hi Maya, love your NLP coaching work"
    # empty name/niche fall back gracefully
    assert fill("Hi {{first_name}}", "", "") == "Hi there"


def test_pipeline_cycle_handler_registered():
    from orchestration.app_jobs import HANDLERS

    assert "pipeline_cycle" in HANDLERS


def test_autopilot_double_gate(monkeypatch):
    """The handler must refuse to send unless BOTH payload.send and AUTOPILOT_SEND=1."""
    import orchestration.app_jobs as aj

    calls = {}

    def fake_run_cycle(conn, **kw):
        calls.update(kw)
        return {"ok": True, "send": {"sent": 0}}

    monkeypatch.setattr("orchestration.pipeline.run_cycle", fake_run_cycle)

    # payload wants send, but env not set → send must be False into run_cycle
    monkeypatch.delenv("AUTOPILOT_SEND", raising=False)
    res = aj._do_pipeline_cycle(None, {"keywords": ["x"], "send": True})
    assert calls["send"] is False
    assert "AUTOPILOT_SEND" in res["send"]["skipped"]

    # both set → send True
    monkeypatch.setenv("AUTOPILOT_SEND", "1")
    aj._do_pipeline_cycle(None, {"keywords": ["x"], "send": True})
    assert calls["send"] is True
