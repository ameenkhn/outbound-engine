"""Pure unit tests for data.normalize — no DB, runs everywhere.

Covers the blocking-key normalizers and their edge cases: junk phones, spaced
``+91``, pages with ``/about``, BOM-prefixed input, and idempotency.
"""
from __future__ import annotations

from data.normalize import (
    clean_phone,
    normalize_email,
    normalize_handle,
    normalize_page,
)

BOM = "﻿"


# --- normalize_email --------------------------------------------------------

class TestEmail:
    def test_lowercases_and_trims(self):
        assert normalize_email("  Hello@Aanya.Coach ") == "hello@aanya.coach"

    def test_strips_bom(self):
        assert normalize_email(BOM + "Hi@X.com") == "hi@x.com"

    def test_strips_mailto(self):
        assert normalize_email("mailto:Hi@X.com") == "hi@x.com"

    def test_rejects_no_at(self):
        assert normalize_email("not-an-email") is None

    def test_rejects_no_dot_domain(self):
        assert normalize_email("a@b") is None

    def test_rejects_placeholder(self):
        assert normalize_email("test@example.com") is None
        assert normalize_email("noreply@aanya.coach") is None

    def test_none_and_empty(self):
        assert normalize_email(None) is None
        assert normalize_email("   ") is None

    def test_idempotent(self):
        once = normalize_email("Hello@Aanya.Coach")
        assert normalize_email(once) == once

    def test_does_not_strip_plus_tag(self):
        # distinct inboxes must stay distinct
        assert normalize_email("a+tag@x.com") == "a+tag@x.com"


# --- clean_phone ------------------------------------------------------------

class TestPhone:
    def test_plain_indian_mobile(self):
        assert clean_phone("9876543210") == "+919876543210"

    def test_spaced_plus_91(self):
        assert clean_phone("+91 98765 43210") == "+919876543210"

    def test_trailing_space_plus91(self):
        assert clean_phone("+91 9876543210") == "+919876543210"

    def test_trunk_zero_prefix(self):
        assert clean_phone("09876543210") == "+919876543210"

    def test_bom_prefixed(self):
        assert clean_phone(BOM + "+91-98765-43210") == "+919876543210"

    def test_rejects_junk_fb_page_id(self):
        # a 12-digit numeric blob (e.g. a Facebook page id) is not a phone
        assert clean_phone("100200300400") is None

    def test_rejects_short(self):
        assert clean_phone("12345") is None

    def test_rejects_wrong_leading_digit(self):
        # Indian mobiles start 6-9; a 10-digit number starting 1 is junk
        assert clean_phone("1234567890") is None

    def test_none_and_empty(self):
        assert clean_phone(None) is None
        assert clean_phone("") is None
        assert clean_phone("abc") is None

    def test_idempotent(self):
        once = clean_phone("+91 98765 43210")
        assert clean_phone(once) == once


# --- normalize_page ---------------------------------------------------------

class TestPage:
    def test_strips_scheme_www_and_trailing_slash(self):
        assert normalize_page("https://www.facebook.com/MyPage/") == "facebook.com/mypage"

    def test_strips_about_subpath(self):
        assert normalize_page("https://www.facebook.com/MyPage/about") == "facebook.com/mypage"
        assert normalize_page("https://facebook.com/MyPage/about_details") == "facebook.com/mypage"

    def test_host_only_form(self):
        assert normalize_page("facebook.com/MyPage") == "facebook.com/mypage"

    def test_bare_slug(self):
        assert normalize_page("MyPage") == "facebook.com/mypage"

    def test_m_subdomain(self):
        assert normalize_page("https://m.facebook.com/MyPage") == "facebook.com/mypage"

    def test_fb_com_alias(self):
        assert normalize_page("https://fb.com/MyPage") == "facebook.com/mypage"

    def test_strips_query_and_fragment(self):
        assert normalize_page("https://www.facebook.com/MyPage?ref=ad#top") == "facebook.com/mypage"

    def test_bom_prefixed(self):
        assert normalize_page(BOM + "https://www.facebook.com/MyPage") == "facebook.com/mypage"

    def test_host_with_no_slug_is_none(self):
        assert normalize_page("https://www.facebook.com/") is None
        assert normalize_page("facebook.com") is None

    def test_none_and_empty(self):
        assert normalize_page(None) is None
        assert normalize_page("   ") is None

    def test_idempotent(self):
        once = normalize_page("https://www.facebook.com/MyPage/about")
        assert normalize_page(once) == once

    def test_two_different_pages_distinct(self):
        assert normalize_page("facebook.com/AanyaCoaching") != normalize_page("facebook.com/TarotByRhea")


# --- normalize_handle -------------------------------------------------------

class TestHandle:
    def test_strips_at(self):
        assert normalize_handle("@MyName") == "myname"

    def test_from_instagram_url(self):
        assert normalize_handle("https://instagram.com/MyName/") == "myname"

    def test_from_linkedin_company_url(self):
        assert normalize_handle("https://www.linkedin.com/company/aanya-coaching/") == "aanya-coaching"

    def test_from_linkedin_in_url(self):
        assert normalize_handle("https://linkedin.com/in/jane-doe") == "jane-doe"

    def test_bom_and_whitespace(self):
        assert normalize_handle(BOM + "  @MyName  ") == "myname"

    def test_none_and_empty(self):
        assert normalize_handle(None) is None
        assert normalize_handle("") is None
        assert normalize_handle("@") is None

    def test_idempotent(self):
        once = normalize_handle("https://instagram.com/MyName/")
        assert normalize_handle(once) == once
