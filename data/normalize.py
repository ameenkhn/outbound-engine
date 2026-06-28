"""Pure normalization functions for the L0 identity resolver (T2/T3).

These produce the *blocking keys* the resolver and loader use to dedup and merge
leads. Every function here is pure: no DB, no I/O, no globals. That keeps them
trivially unit-testable (see ``tests/test_normalize.py``) and means the same
key is computed identically at load time and at resolve time.

Design rules:
  * Always return a canonical string or ``None`` — never raise on junk input.
    A ``None`` means "this signal is unusable as a blocking key"; the caller
    drops it rather than indexing on garbage.
  * Idempotent: ``f(f(x)) == f(x)``. Re-normalizing a normalized value is a
    no-op, which is what makes the loader safe to re-run.
  * Strip a UTF-8 BOM and surrounding whitespace up front — scraped JSON and
    CSV-derived values occasionally carry a leading ``\\ufeff``.
"""
from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urlsplit

_BOM = "﻿"


def _pre(value: Optional[str]) -> str:
    """Shared pre-clean: coerce to str, drop BOM, strip whitespace."""
    if value is None:
        return ""
    text = str(value)
    # Remove any BOM occurrences (can appear mid-string after concatenation).
    text = text.replace(_BOM, "")
    return text.strip()


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------

# Deliberately permissive: a single @ with a dotted domain. We are normalizing,
# not deeply validating — the scraper already ran is_valid_email upstream.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# Junk/placeholder local-parts or domains that are never a real lead.
_EMAIL_BLOCKLIST = ("example.com", "test.com", "dummy", "noreply", "no-reply")


def normalize_email(value: Optional[str]) -> Optional[str]:
    """Lowercase + trim an email; return ``None`` if it isn't a usable address.

    The canonical form is the whole address lowercased (we do NOT strip Gmail
    dots or ``+tag`` suffixes — that would over-merge distinct inboxes). The
    lowercased address is the email blocking key.
    """
    text = _pre(value).lower()
    if not text:
        return None
    # A leading "mailto:" sometimes rides along from scraped hrefs.
    if text.startswith("mailto:"):
        text = text[len("mailto:"):]
    text = text.strip()
    if not _EMAIL_RE.match(text):
        return None
    for bad in _EMAIL_BLOCKLIST:
        if bad in text:
            return None
    return text


# ---------------------------------------------------------------------------
# Phone (Indian E.164)
# ---------------------------------------------------------------------------

# A real Indian mobile starts 6-9 and has 10 digits. We canonicalize to
# +91XXXXXXXXXX (no space) for a stable E.164 blocking key. This intentionally
# rejects junk like Facebook numeric page IDs that leaked in as "phones".
_INDIAN_MOBILE_RE = re.compile(r"(?:\+?91|0)?([6-9]\d{9})$")


def clean_phone(value: Optional[str]) -> Optional[str]:
    """Normalize to Indian E.164 ``+91XXXXXXXXXX`` or return ``None``.

    Strips all non-digits, then requires exactly a 10-digit Indian mobile
    (optionally prefixed by country code 91 or a trunk 0). Anything else —
    too short, too long, wrong leading digit — is rejected as junk. This is
    the phone blocking key.
    """
    raw = _pre(value)
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return None
    m = _INDIAN_MOBILE_RE.match(digits)
    if not m:
        return None
    return "+91" + m.group(1)


# ---------------------------------------------------------------------------
# Facebook page  (the PRIMARY identity blocking key)
# ---------------------------------------------------------------------------

# Path segments that are profile chrome, not part of the page slug.
_PAGE_TRAILING_SEGMENTS = ("about", "about_details", "about_contact_and_basic_info")


def normalize_page(value: Optional[str]) -> Optional[str]:
    """Canonicalize a Facebook page URL/handle to a bare slug.

    Strips scheme, ``www.``, query/fragment, a trailing slash, and any
    ``/about*`` profile sub-path, lowercasing the host but preserving slug case
    only by lowercasing the whole thing (FB slugs are case-insensitive). The
    result is the normalized page used as ``leads.source_ref`` and as the
    PRIMARY merge key (decision 3C / the false-merge guard).

    Examples:
      ``https://www.facebook.com/MyPage/about`` -> ``facebook.com/mypage``
      ``facebook.com/MyPage/``                  -> ``facebook.com/mypage``
      ``MyPage``                                -> ``facebook.com/mypage``
    """
    text = _pre(value).lower()
    if not text:
        return None

    # Parse into (host, path) regardless of whether a scheme was present.
    if "://" in text:
        parts = urlsplit(text)
        host = parts.netloc
        path = parts.path
    elif text.startswith("facebook.com") or text.startswith("www.facebook.com") or text.startswith("m.facebook.com") or text.startswith("fb.com"):
        # Host-only form like "facebook.com/page" — split on first slash.
        head, _, tail = text.partition("/")
        host = head
        path = "/" + tail if tail else ""
    else:
        # A bare slug like "mypage" (no host). Treat the whole thing as path.
        host = "facebook.com"
        path = "/" + text

    # Strip leading "www." / "m." sub-domain prefixes (prefix-wise, not the
    # char-set semantics of str.lstrip).
    for prefix in ("www.", "m."):
        if host.startswith(prefix):
            host = host[len(prefix):]
    if host in ("fb.com", "facebook.com") or not host:
        host = "facebook.com"

    # Clean the path: drop empty + chrome segments, keep the slug.
    segments = [seg for seg in path.split("/") if seg]
    kept = []
    for seg in segments:
        if seg in _PAGE_TRAILING_SEGMENTS:
            break  # everything from /about onward is profile chrome
        kept.append(seg)

    if not kept:
        return None  # a host with no slug is not an identity

    slug = "/".join(kept)
    return host + "/" + slug


# ---------------------------------------------------------------------------
# Social handle  (instagram / twitter / youtube / linkedin)
# ---------------------------------------------------------------------------

def normalize_handle(value: Optional[str]) -> Optional[str]:
    """Canonicalize a social handle to a bare lowercase username.

    Accepts either a raw handle (``@MyName``) or a profile URL
    (``https://instagram.com/MyName/``) and returns ``myname``. Used as the
    blocking key for ``channels.type='linkedin'`` (and for carrying socials in
    ``leads.attributes``). Returns ``None`` for empty/unusable input.
    """
    text = _pre(value).lower()
    if not text:
        return None

    if "://" in text or text.startswith("www.") or any(
        host in text for host in ("instagram.com", "twitter.com", "x.com", "youtube.com", "linkedin.com", "facebook.com")
    ):
        # Pull the first meaningful path segment out of a profile URL.
        candidate = text if "://" in text else "http://" + text
        parts = urlsplit(candidate)
        segments = [seg for seg in parts.path.split("/") if seg]
        # LinkedIn profiles look like /in/<slug> or /company/<slug>; keep the
        # final identifying segment, which is the stable handle.
        if not segments:
            return None
        # Drop known prefix segments so /in/jane-doe -> jane-doe.
        prefixes = {"in", "company", "channel", "c", "user", "pub", "profile"}
        meaningful = [s for s in segments if s not in prefixes]
        text = (meaningful[0] if meaningful else segments[-1])

    # Strip a leading @ and any surrounding punctuation/whitespace.
    text = text.lstrip("@").strip()
    text = text.strip("/")
    if not text:
        return None
    return text
