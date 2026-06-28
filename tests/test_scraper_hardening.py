"""Pure unit tests for the Meta Ad Library scraper hardening (Lane B / T9).

These tests exercise ONLY the extractable, deterministic logic — no browser,
no network. Playwright is imported lazily by the scraper module at function
call time, not at import time, so importing the module here does not require a
browser to be installed.

Covered (one-to-one with the hardening checklist):
  - clean_phone / clean_whatsapp: rejects a junk WhatsApp value, accepts a
    valid +91 mobile.
  - is_valid_founded_year: accepts a 2026 founder, rejects an absurd future year.
  - USER_AGENTS pool: non-empty, entries look like user-agents.
  - backoff schedule: returns increasing (monotonic, jittered) delays.
  - env-config readers: get_concurrency / get_proxies / pick_proxy default
    correctly and parse overrides.
"""

import os
import random
import sys
from datetime import datetime

# Make the scraper package importable regardless of where pytest is invoked.
SCRAPER_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "sourcing", "meta_ads",
)
if SCRAPER_DIR not in sys.path:
    sys.path.insert(0, SCRAPER_DIR)

import facebook_ads_scraper as fas  # noqa: E402


# ---------------------------------------------------------------------------
# WhatsApp / phone validation through clean_phone
# ---------------------------------------------------------------------------
class TestPhoneAndWhatsApp:
    def test_clean_phone_accepts_valid_indian_mobile(self):
        assert fas.clean_phone("+91 9876543210") == "+91 9876543210"
        assert fas.clean_phone("9876543210") == "+91 9876543210"

    def test_clean_phone_rejects_junk(self):
        # A 12-digit Facebook page id (starts with 1) is NOT a mobile.
        assert fas.clean_phone("100123456789") is None
        assert fas.clean_phone("") is None
        assert fas.clean_phone(None) is None

    def test_clean_whatsapp_rejects_junk_value(self):
        # wa.me slug / non-mobile junk must be rejected, not leaked.
        assert fas.clean_whatsapp("wame12345") is None
        assert fas.clean_whatsapp("0000000000") is None
        assert fas.clean_whatsapp("12345") is None

    def test_clean_whatsapp_accepts_valid_mobile(self):
        # WhatsApp now goes through the same clean_phone path.
        assert fas.clean_whatsapp("+919876543210") == "+91 9876543210"
        assert fas.clean_whatsapp("9123456780") == "+91 9123456780"

    def test_clean_whatsapp_matches_clean_phone(self):
        for value in ["+91 9876543210", "wame123", "100000000000", "8000000000"]:
            assert fas.clean_whatsapp(value) == fas.clean_phone(value)


# ---------------------------------------------------------------------------
# Founded-year validator (live bug fix: dynamic ceiling, was <= 2025)
# ---------------------------------------------------------------------------
class TestFoundedYear:
    def test_accepts_2026_founder(self):
        # The old hardcoded `<= 2025` silently rejected this.
        assert fas.is_valid_founded_year(2026) is True
        assert fas.is_valid_founded_year("2026") is True

    def test_accepts_current_year(self):
        assert fas.is_valid_founded_year(datetime.now().year) is True

    def test_ceiling_is_dynamic_current_year_plus_one(self):
        next_year = datetime.now().year + 1
        assert fas.is_valid_founded_year(next_year) is True
        # Two years out is implausible / clock-skew beyond tolerance.
        assert fas.is_valid_founded_year(next_year + 1) is False

    def test_rejects_absurd_future_year(self):
        assert fas.is_valid_founded_year(3000) is False
        assert fas.is_valid_founded_year(9999) is False

    def test_rejects_too_old_and_garbage(self):
        assert fas.is_valid_founded_year(1800) is False
        assert fas.is_valid_founded_year("not-a-year") is False
        assert fas.is_valid_founded_year(None) is False


# ---------------------------------------------------------------------------
# User-agent pool
# ---------------------------------------------------------------------------
class TestUserAgentPool:
    def test_pool_is_non_empty(self):
        assert len(fas.USER_AGENTS) >= 2

    def test_entries_look_like_user_agents(self):
        for ua in fas.USER_AGENTS:
            assert isinstance(ua, str)
            assert ua.startswith("Mozilla/5.0")
            # A real desktop UA names at least one engine/browser token.
            assert any(tok in ua for tok in ("Chrome/", "Firefox/", "Safari/", "Edg/"))

    def test_default_is_in_pool(self):
        assert fas.DEFAULT_USER_AGENT in fas.USER_AGENTS

    def test_pick_user_agent_returns_pool_member(self):
        rng = random.Random(1234)
        for _ in range(20):
            assert fas.pick_user_agent(rng) in fas.USER_AGENTS

    def test_pick_user_agent_rotates(self):
        # Over enough draws we should see more than one distinct UA.
        rng = random.Random(42)
        seen = {fas.pick_user_agent(rng) for _ in range(200)}
        assert len(seen) > 1


# ---------------------------------------------------------------------------
# Backoff with jitter
# ---------------------------------------------------------------------------
class TestBackoff:
    def test_ceiling_is_monotonically_increasing(self):
        ceilings = [fas.backoff_ceiling(i, base=1.0, cap=1000.0) for i in range(6)]
        assert ceilings == sorted(ceilings)
        # Strictly increasing before the cap.
        for a, b in zip(ceilings, ceilings[1:]):
            assert b > a

    def test_ceiling_respects_cap(self):
        assert fas.backoff_ceiling(50, base=1.0, cap=30.0) == 30.0

    def test_delay_within_zero_and_ceiling(self):
        rng = random.Random(7)
        for attempt in range(6):
            ceiling = fas.backoff_ceiling(attempt, base=1.0, cap=1000.0)
            for _ in range(50):
                d = fas.backoff_delay(attempt, base=1.0, cap=1000.0, rng=rng)
                assert 0.0 <= d <= ceiling

    def test_average_delay_grows_with_attempt(self):
        rng = random.Random(99)

        def avg(attempt):
            return sum(
                fas.backoff_delay(attempt, base=1.0, cap=1000.0, rng=rng)
                for _ in range(400)
            ) / 400.0

        # Mean of a uniform [0, ceiling] grows as the ceiling doubles.
        assert avg(0) < avg(2) < avg(4)


# ---------------------------------------------------------------------------
# Env-config readers
# ---------------------------------------------------------------------------
class TestEnvConfig:
    def test_concurrency_default(self):
        assert fas.get_concurrency({}) == 2
        assert fas.DEFAULT_CONCURRENCY == 2

    def test_concurrency_override(self):
        assert fas.get_concurrency({"SCRAPER_CONCURRENCY": "5"}) == 5

    def test_concurrency_invalid_falls_back(self):
        assert fas.get_concurrency({"SCRAPER_CONCURRENCY": "abc"}) == 2
        assert fas.get_concurrency({"SCRAPER_CONCURRENCY": "0"}) == 2
        assert fas.get_concurrency({"SCRAPER_CONCURRENCY": "-3"}) == 2

    def test_proxies_default_empty(self):
        assert fas.get_proxies({}) == []
        assert fas.get_proxies({"SCRAPER_PROXIES": ""}) == []
        assert fas.get_proxies({"SCRAPER_PROXIES": "   "}) == []

    def test_proxies_parsed_and_trimmed(self):
        env = {"SCRAPER_PROXIES": "http://h1:8080, http://h2:3128 ,"}
        assert fas.get_proxies(env) == ["http://h1:8080", "http://h2:3128"]

    def test_pick_proxy_none_when_empty(self):
        assert fas.pick_proxy([]) is None

    def test_pick_proxy_returns_playwright_dict(self):
        proxy = fas.pick_proxy(["http://h1:8080"])
        assert proxy == {"server": "http://h1:8080"}

    def test_pick_proxy_splits_inline_credentials(self):
        proxy = fas.pick_proxy(["http://user:pass@h1:8080"])
        assert proxy == {
            "server": "http://h1:8080",
            "username": "user",
            "password": "pass",
        }


# ---------------------------------------------------------------------------
# Run-level monitoring / fail-loud plumbing (no browser needed)
# ---------------------------------------------------------------------------
class TestRunMonitoring:
    def test_scraper_blocked_error_is_exception(self):
        assert issubclass(fas.ScraperBlockedError, Exception)

    def test_fresh_scraper_has_zeroed_stats(self):
        s = fas.FacebookAdsLibraryScraper()
        assert s.run_stats["queries_run"] == 0
        assert s.run_stats["advertisers_found"] == 0
        assert s.run_stats["contacts_found"] == 0
        assert s.run_stats["failures"] == 0
        assert s.run_stats["library_id_detected"] is False

    def test_run_summary_is_readable_string(self):
        s = fas.FacebookAdsLibraryScraper()
        s.run_stats["queries_run"] = 3
        s.run_stats["advertisers_found"] = 42
        summary = s.run_summary()
        assert "queries_run=3" in summary
        assert "advertisers_found=42" in summary

    def test_reset_run_stats(self):
        s = fas.FacebookAdsLibraryScraper()
        s.run_stats["queries_run"] = 9
        s.reset_run_stats()
        assert s.run_stats["queries_run"] == 0
