#!/usr/bin/env python3
"""
Facebook Ads Library Scraper - COMPLETE EDITION
================================================
Comprehensive scraper for finding potential affiliates/partners from Meta Ad Library.

Features:
- Full niche keyword configurations (Business, Finance, Occult, NLP/Mindset, Health, Yoga)
- Robust deduplication using Library ID
- Comprehensive advertiser page scraping (main page + 4 About pages)
- Full contact extraction (emails, phones, websites)
- Complete social media extraction (Instagram, Twitter/X, YouTube, LinkedIn, WhatsApp)
- Business details (address, city, hours, price_range, founded, products)
- Additional info (GST, PAN, registration, mission, vision)
- Boolean search combinations for advanced queries
- Multiple date extraction patterns
- Keyword match scoring with weighted priorities

Author: Built for EXLY affiliate discovery
"""

import sys
import io
import os
import asyncio
import json
import logging
import random
import re
from urllib.parse import urlparse
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup
from datetime import datetime

# Fix encoding for Windows so emoji/Unicode logging doesn't crash on cp1252.
# Guarded so importing this module under a test runner (where stdout is a
# captured, non-buffer stream) does not clobber the captured stream.
def _force_utf8_stdout():
    enc = (getattr(sys.stdout, "encoding", "") or "").lower()
    if enc.startswith("utf"):
        return  # already UTF-8 (typical on macOS/Linux and under pytest)
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is None:
        return  # captured/replaced stream without a real buffer — leave it alone
    try:
        sys.stdout = io.TextIOWrapper(buffer, encoding="utf-8")
    except (ValueError, AttributeError):
        pass  # never let an encoding tweak break import


_force_utf8_stdout()

# ============================================
# STRUCTURED LOGGING
# ============================================
# Failures used to be swallowed (bare `except: return ""`). They are now logged
# through this logger so a wholesale failure is visible, while per-item
# resilience (skip-and-continue) is preserved. Logs go to stderr so they don't
# pollute the JSON written to stdout by main().

logger = logging.getLogger("facebook_ads_scraper")
if not logger.handlers:
    _h = logging.StreamHandler(sys.stderr)
    _h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logger.addHandler(_h)
logger.setLevel(os.environ.get("SCRAPER_LOG_LEVEL", "INFO").upper())


class ScraperBlockedError(RuntimeError):
    """Raised on a wholesale (run-level) failure.

    A run is considered a wholesale failure when the ad-detection selector
    (`text=Library ID`) is never found across the entire run, i.e. every query
    returned a blocked / empty page. Per-item failures do NOT raise — they are
    logged and skipped so partial results are still returned.
    """


# ============================================
# HARDENING CONFIG — UA rotation / proxies / concurrency / backoff
# ============================================
# All of the knobs below are pure + import-safe (no browser, no network) so
# they can be unit-tested without Playwright. They are read once at call time
# from the environment, defaulting to the scraper's historical behaviour.

# A small pool of realistic, current desktop user-agents. The first entry is
# the historical default (Chrome 120 on Windows) so behaviour is unchanged when
# rotation happens to pick index 0.
USER_AGENTS = [
    # Chrome on Windows (historical default — keep first)
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    # Chrome on macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    # Chrome on Linux
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    # Edge on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36 Edg/121.0.0.0",
    # Firefox on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) "
    "Gecko/20100101 Firefox/123.0",
]

# Historical single UA — kept as the sensible default for callers that want a
# stable agent (e.g. the very first context).
DEFAULT_USER_AGENT = USER_AGENTS[0]


def pick_user_agent(rng=None):
    """Return a user-agent string chosen at random from the pool.

    A different UA per browser context spreads the fingerprint across a run.
    Pass a seeded `random.Random` for deterministic tests.
    """
    r = rng or random
    return r.choice(USER_AGENTS)


def get_proxies(env=None):
    """Parse the optional `SCRAPER_PROXIES` env var into a list of proxy URLs.

    Format: comma-separated proxy server strings, e.g.
        SCRAPER_PROXIES="http://user:pass@host1:8080,http://host2:3128"
    Empty / unset => [] (run direct, exactly as before).
    """
    env = os.environ if env is None else env
    raw = (env.get("SCRAPER_PROXIES", "") or "").strip()
    if not raw:
        return []
    return [p.strip() for p in raw.split(",") if p.strip()]


def pick_proxy(proxies=None, rng=None):
    """Return a Playwright proxy dict ({'server': ...}) chosen from the pool.

    Returns None when there are no proxies configured, so the caller launches
    a direct connection exactly as today. Supports `user:pass@host` inline
    credentials by splitting them into Playwright's username/password fields.
    """
    pool = get_proxies() if proxies is None else proxies
    if not pool:
        return None
    r = rng or random
    server = r.choice(pool)
    return _proxy_to_playwright(server)


def _proxy_to_playwright(server):
    """Convert a proxy URL (optionally with inline user:pass) to a Playwright
    proxy dict. Inline credentials are split out because Playwright wants them
    in separate `username` / `password` keys."""
    if not server:
        return None
    proxy = {"server": server}
    # Pull inline credentials out of the authority, if present.
    m = re.match(r'^(?P<scheme>\w+://)?(?P<user>[^:/@]+):(?P<pw>[^@/]+)@(?P<rest>.+)$', server)
    if m:
        scheme = m.group("scheme") or ""
        proxy = {
            "server": scheme + m.group("rest"),
            "username": m.group("user"),
            "password": m.group("pw"),
        }
    return proxy


# Default page-visit concurrency. Lowered from the old hardcoded 4 to 2 to be
# gentler on Facebook and reduce block rate. Override with SCRAPER_CONCURRENCY.
DEFAULT_CONCURRENCY = 2


def get_concurrency(env=None):
    """Read page-visit concurrency from `SCRAPER_CONCURRENCY` (default 2).

    Invalid / non-positive values fall back to the default so a bad env var
    never crashes a run.
    """
    env = os.environ if env is None else env
    raw = env.get("SCRAPER_CONCURRENCY", "")
    try:
        val = int(raw)
        return val if val > 0 else DEFAULT_CONCURRENCY
    except (TypeError, ValueError):
        return DEFAULT_CONCURRENCY


# Backoff tuning (exponential with full jitter), overridable for tests/ops.
BACKOFF_BASE_SECONDS = float(os.environ.get("SCRAPER_BACKOFF_BASE", "1.0"))
BACKOFF_MAX_SECONDS = float(os.environ.get("SCRAPER_BACKOFF_MAX", "60.0"))


def backoff_delay(attempt, base=None, cap=None, rng=None):
    """Exponential backoff with full jitter for retrying a blocked/failed query.

    Returns a delay in seconds for a 0-indexed `attempt`. Without jitter the
    delays grow as base * 2**attempt (capped at `cap`); jitter then picks a
    random value in [0, that ceiling]. The *ceiling* is strictly increasing
    until the cap, which is what the retry loop relies on.
    """
    base = BACKOFF_BASE_SECONDS if base is None else base
    cap = BACKOFF_MAX_SECONDS if cap is None else cap
    r = rng or random
    ceiling = min(cap, base * (2 ** max(0, attempt)))
    return r.uniform(0, ceiling)


def backoff_ceiling(attempt, base=None, cap=None):
    """The (deterministic) upper bound of `backoff_delay` for a given attempt.

    Exposed so tests can assert the schedule is monotonically increasing
    without depending on the random jitter.
    """
    base = BACKOFF_BASE_SECONDS if base is None else base
    cap = BACKOFF_MAX_SECONDS if cap is None else cap
    return min(cap, base * (2 ** max(0, attempt)))


def is_valid_founded_year(year):
    """True if `year` is a plausible 'founded'/'established' year.

    LIVE BUG FIX: the old inline check hardcoded `year <= 2025`, silently
    rejecting any company founded in 2026 or later. The ceiling is now dynamic
    (current year + 1, to tolerate clock skew / pages dated slightly ahead).
    """
    try:
        y = int(year)
    except (TypeError, ValueError):
        return False
    return 1900 <= y <= datetime.now().year + 1


def clean_whatsapp(value):
    """Validate a captured WhatsApp value through the same `clean_phone` path.

    WhatsApp numbers were previously stored raw, so junk (wa.me slugs, page
    IDs) leaked downstream. Routing through `clean_phone` normalises a real
    Indian mobile/landline or returns None.
    """
    return clean_phone(value)

# ============================================
# KEYWORD CONFIGURATION
# ============================================

# Course/Program indicators - HIGH PRIORITY (2 points each)
COURSE_INDICATORS = [
    "course", "program", "certification", "training", "workshop",
    "masterclass", "bootcamp", "cohort", "mentorship", "batch",
    "module", "curriculum", "syllabus", "classes", "sessions",
    "level 1", "level 2", "level 3", "practitioner", "advanced",
    "diploma", "degree", "fellowship", "internship", "apprenticeship"
]

# CTA/Urgency triggers - HIGH PRIORITY (2 points each)
CTA_TRIGGERS = [
    "enroll now", "register now", "apply now", "join now", "sign up",
    "limited seats", "batch starting", "applications open", "closing soon",
    "early bird", "last chance", "few spots left", "deadline",
    "book now", "reserve your spot", "seats filling fast", "hurry",
    "don't miss", "act now", "today only", "limited time", "offer ends",
    "grab your seat", "secure your spot", "join the waitlist"
]

# Payment/Price signals - HIGH PRIORITY (2 points each)
PAYMENT_SIGNALS = [
    "₹", "rs.", "rs ", "inr", "payment", "emi available", "installment", 
    "fee", "investment", "pricing", "cost", "pay in", "discount",
    "50% off", "scholarship", "early bird price", "special offer",
    "one-time payment", "monthly payment", "no cost emi", "easy emi"
]

# Credential signals (1 point each)
CREDENTIAL_SIGNALS = [
    "certified", "certification", "accredited", "recognised", "recognized",
    "diploma", "degree", "certificate", "credential", "license", "licensed",
    "internationally recognised", "globally recognized", "award-winning",
    "icf accredited", "yoga alliance", "ryt certification", "sebi registered",
    "government recognized", "iso certified", "ncert", "ugc approved"
]

# Outcome/Transformation signals (1 point each)
OUTCOME_SIGNALS = [
    "become a certified", "start your practice", "launch your", "build your",
    "scale your", "turn your passion into", "from zero to", "master the art of",
    "learn to", "transform your", "unlock your", "discover how to",
    "get certified in", "earn while you learn", "quit your 9-5",
    "work from anywhere", "financial freedom", "6-figure income", "6 figure income",
    "high-ticket clients", "high ticket clients", "passive income",
    "become a professional", "start earning", "change your life",
    "proven system", "guaranteed results", "success stories"
]

# Universal Triggers - Combined for convenience
UNIVERSAL_TRIGGERS = CTA_TRIGGERS + PAYMENT_SIGNALS

# Avoid Keywords - Filter these OUT (freebie/non-monetized content)
AVOID_KEYWORDS = [
    "free webinar", "free masterclass", "free workshop", "free course",
    "free download", "free ebook", "free guide", "no cost",
    "100% free", "completely free", "free training",
    # Generic content creator signals (not selling courses)
    "follow for more", "like and share", "viral video", "trending now"
]

# ============================================
# NICHE-SPECIFIC KEYWORD CONFIGURATIONS
# ============================================

NICHE_KEYWORDS = {
    "business": {
        "sub_categories": [
            "business coach", "business coaching", "startup founder", "startup mentor",
            "agency marketing", "creator economy", "consultant", "agency owner",
            "entrepreneur", "founder", "ceo coach", "executive coach",
            "sales coach", "leadership coach", "management consultant"
        ],
        "search_keywords": [
            # Business Coaching
            "business coach program", "coach training program", "coach certification",
            "consulting program online", "coaching business", "high-ticket clients",
            "high ticket clients", "6-figure business", "6 figure business",
            "scale to 7 figures", "scale to seven figures", "million dollar business",
            # Startup/Founder
            "founder bootcamp", "startup mentorship", "entrepreneur program",
            "founder cohort", "startup program", "accelerator program",
            "incubator program", "pitch deck training", "fundraising course",
            # Agency/Marketing
            "agency growth program", "digital marketing course", 
            "performance marketing program", "funnel building program",
            "marketing certification", "smma course", "social media marketing",
            "facebook ads course", "google ads certification", "seo course",
            # Creator Economy
            "creator program", "personal brand", "digital product",
            "online business", "monetize your knowledge", "monetise your knowledge",
            "knowledge business", "info product", "course creation",
            "membership site", "community building"
        ],
        "trigger_words": [
            "framework", "proven system", "scale", "lead flow", "revenue",
            "clients", "growth", "profit", "sales funnel", "high ticket",
            "recurring revenue", "mrc", "arr", "client acquisition",
            "lead generation", "conversion", "roi", "kpi"
        ]
    },
    
    "finance": {
        "sub_categories": [
            "stock market educator", "investment coach", "trading mentor",
            "wealth coach", "financial advisor", "money mindset coach",
            "crypto trader", "options trader", "forex trader",
            "mutual fund advisor", "insurance advisor", "ca coach"
        ],
        "search_keywords": [
            # Stock Market
            "stock market course", "trading mentorship", "investment program online",
            "trading course", "options trading", "intraday trading",
            "swing trading course", "technical analysis course",
            "fundamental analysis", "price action trading", "candlestick patterns",
            # Wealth Management
            "wealth management course", "financial planning certification",
            "mutual fund certification", "cfp certification", "cfa prep",
            "portfolio management", "asset allocation", "retirement planning",
            # Crypto/Forex
            "crypto trading course", "forex trading", "cryptocurrency investing",
            "defi course", "blockchain certification", "nft trading",
            # Indian Specific
            "nifty trading", "banknifty options", "nse certification",
            "sebi ra exam", "nism certification", "amfi certification"
        ],
        "trigger_words": [
            "live classes", "dashboard access", "wealth", "long-term",
            "financial freedom", "passive income", "roi", "returns",
            "nifty", "banknifty", "sebi registered", "profit", "portfolio",
            "compound interest", "sip", "systematic investment", "nse", "bse"
        ]
    },
    
    "occult": {
        "sub_categories": [
            # Tarot
            "tarot reader", "tarot", "tarot card reader", "oracle reader",
            # Astrology
            "astrologer", "astrology", "vedic astrology", "jyotish",
            "kundli expert", "horoscope reader",
            # Numerology
            "numerologist", "numerology", "lo shu grid", "pythagorean numerology",
            # Energy Healing
            "energy healer", "reiki master", "reiki", "pranic healer",
            "theta healer", "quantum healer",
            # Sound Healing
            "sound healer", "sound healing", "sound therapist", "singing bowl",
            # Spiritual
            "spiritual coach", "akashic reader", "psychic", "medium",
            "crystal healer", "angel therapist", "past life regression",
            "hypnotherapist", "meditation teacher"
        ],
        "search_keywords": [
            # Tarot
            "tarot course", "tarot reading course online", "tarot certification",
            "learn tarot", "become a tarot reader", "professional tarot",
            "rider waite tarot", "tarot deck", "oracle card reading",
            # Astrology
            "astrology course", "vedic astrology", "astrology certification",
            "learn astrology", "jyotish course", "kundli reading",
            "birth chart reading", "nadi astrology", "kp astrology",
            # Numerology
            "numerology certification", "numerology course", "learn numerology",
            "numerology training", "name numerology", "mobile numerology",
            # Energy Healing
            "energy healing course", "reiki level 1", "reiki level 2",
            "reiki master", "reiki certification", "reiki training",
            "pranic healing course", "theta healing", "quantum healing",
            # Sound Healing
            "sound healing course", "sound healing training",
            "sound healing certification", "sound bath training",
            "singing bowl therapy", "tibetan bowl certification",
            # Spiritual
            "akashic records course", "spiritual coach program",
            "healing modality", "crystal healing", "angel therapy",
            "intuitive training", "psychic development", "channeling course",
            "past life regression training", "hypnotherapy certification"
        ],
        "trigger_words": [
            "certified", "level 1", "level 2", "level 3", "initiation",
            "practitioner", "sacred knowledge", "alignment", "healing",
            "akashic", "spiritual", "divine", "intuitive", "mystic",
            "esoteric", "metaphysical", "channeling", "attunement",
            "third eye", "chakra", "kundalini", "manifestation"
        ]
    },
    
    "nlp_mindset": {
        "sub_categories": [
            # NLP
            "nlp coach", "nlp practitioner", "nlp trainer", "nlp master",
            # Mindset
            "mindset coach", "confidence coach", "success coach",
            "peak performance coach", "high performance coach",
            # Life Coaching
            "life coach", "transformation coach", "personal development",
            "self-improvement coach", "motivational coach",
            # Psychology
            "emotional healer", "trauma healer", "counselor",
            "psychologist", "therapist", "mental health coach",
            # Relationships
            "relationship coach", "dating coach", "marriage counselor"
        ],
        "search_keywords": [
            # NLP
            "nlp practitioner course", "nlp certification",
            "nlp master practitioner", "nlp training", "nlp techniques",
            "neuro linguistic programming", "nlp coaching",
            # Mindset
            "mindset coach certification", "confidence building program",
            "identity shift program", "success mindset", "growth mindset",
            "abundance mindset", "millionaire mindset",
            # Life Coaching
            "life coach certification", "life coaching program online",
            "icf accredited", "icf certification", "icf acc", "icf pcc",
            "cti coaching", "co-active coaching",
            # Psychology
            "emotional healing program", "trauma healing certification",
            "inner child healing", "shadow work", "shadow work course",
            "somatic healing", "nervous system regulation",
            # Transformation
            "breakthrough program", "transformation program",
            "personal mastery", "self-mastery"
        ],
        "trigger_words": [
            "identity shift", "transformation", "breakthrough", "rewire",
            "subconscious", "beliefs", "patterns", "mindset", "nlp",
            "limiting beliefs", "reprogramming", "icf", "coaching federation",
            "neuroplasticity", "brain rewiring", "habit formation"
        ]
    },
    
    "health": {
        "sub_categories": [
            # Nutrition
            "nutrition coach", "nutritionist", "dietitian", "diet consultant",
            "clinical nutritionist", "sports nutritionist",
            # Health Coaching
            "health coach", "wellness coach", "wellness practitioner",
            "functional medicine", "integrative health",
            # Gut Health
            "gut health coach", "digestive health", "microbiome specialist",
            # Hormones
            "hormone coach", "hormonal health coach", "pcos specialist",
            "thyroid health", "menopause coach",
            # Ayurveda
            "ayurveda practitioner", "ayurvedic doctor", "vaidya",
            "panchakarma specialist", "ayurvedic consultant",
            # Medical
            "doctor coach", "medical educator", "healthcare consultant"
        ],
        "search_keywords": [
            # Nutrition
            "nutrition coaching program", "clinical nutrition course",
            "nutrition certification", "dietitian course", "cnc certification",
            "sports nutrition", "weight loss coaching", "keto coach",
            # Health Coaching
            "health coach certification", "wellness practitioner program",
            "functional nutrition", "holistic health certification",
            "integrative health coaching", "lifestyle medicine",
            # Gut Health
            "gut health course online", "gut health program",
            "microbiome course", "leaky gut healing", "ibs coaching",
            # Hormones
            "hormone balance program", "hormonal health",
            "women's health coach", "pcos program", "pcod reversal",
            "thyroid healing", "menopause support",
            # Ayurveda
            "ayurveda certification course", "ayurpreneur program",
            "learn ayurveda", "ayurveda training", "panchakarma training",
            "bams coaching", "ayurvedic practitioner",
            # Medical
            "doctor coaching program", "medical education program",
            "diet consultation", "cme program", "medical writing"
        ],
        "trigger_words": [
            "evidence-based", "client protocols", "structured plan", "case studies",
            "transform", "results", "proven", "holistic", "natural", "wellness",
            "functional", "integrative", "clinical", "therapeutic",
            "healing protocol", "reversal program", "detox"
        ]
    },
    
    "yoga": {
        "sub_categories": [
            # Yoga
            "yoga teacher", "yoga instructor", "yoga coach", "yoga therapist",
            "yoga alliance", "hatha yoga", "vinyasa yoga", "ashtanga yoga",
            # Wellness
            "wellness coach", "holistic wellness", "mind-body coach",
            # Fitness
            "fitness trainer", "personal trainer", "gym trainer",
            "strength coach", "pilates instructor", "zumba instructor",
            # Somatic
            "breathwork facilitator", "somatic healer", "pranayama teacher",
            "meditation teacher", "mindfulness coach"
        ],
        "search_keywords": [
            # Yoga Teacher Training
            "yoga teacher training online", "ytt", "200-hour", "200 hour",
            "300-hour", "300 hour", "500-hour", "500 hour",
            "yoga alliance", "ryt certification", "ryt 200", "ryt 500",
            "advanced yoga training", "yoga certification",
            "hatha yoga teacher training", "vinyasa teacher training",
            "ashtanga yoga course", "iyengar yoga certification",
            "kundalini yoga training", "aerial yoga certification",
            # Wellness
            "wellness coach certification", "holistic wellness",
            "wellness practitioner program", "lifestyle coach",
            # Fitness
            "fitness trainer certification", "personal trainer course",
            "strength conditioning", "strength and conditioning",
            "gym trainer certification", "ace certification", "nasm",
            "crossfit certification", "pilates certification",
            # Somatic/Breathwork
            "breathwork facilitator training", "somatic healing program",
            "trauma informed yoga", "breathwork certification",
            "pranayama training", "meditation teacher training",
            "mindfulness instructor", "vipassana teaching"
        ],
        "trigger_words": [
            "certified", "ryt", "ytt", "transform", "practice", "asana",
            "meditation", "breathwork", "mindfulness", "wellness",
            "yoga alliance certified", "pranayama", "vinyasa", "hatha",
            "alignment", "anatomy", "philosophy", "sequencing"
        ]
    }
}

# ============================================
# BOOLEAN SEARCH COMBINATIONS
# Ready-to-use high-priority search strings
# ============================================

BOOLEAN_SEARCH_COMBINATIONS = [
    # Priority 5 stars - Highest conversion potential
    '"enroll now" course program certification',
    '"certification" "limited seats" "batch starting"',
    '"practitioner" training certification',
    '"coach" certification training india',
    '"yoga" 200 300 500 hour',
    '"nlp" practitioner master certification',
    '"tarot" course certification certified',
    '"health" coach certification program',
    
    # Priority 4-4.5 stars
    '₹ program course',
    '"cohort" apply enroll',
    '"mentorship" 1:1 group',
    '"trading" course mentorship stock',
    '"healing" course certification training',
    '"business" coach enroll apply',
    '"ayurveda" certification course india',
    
    # Priority 3.5-4 stars
    '"reiki" level master certification',
    '"astrology" course vedic jyotish',
    '"numerology" certification course',
    '"life coach" certification icf',
    '"nutrition" certification program',
    '"fitness" trainer certification',
    
    # Urgency-based searches
    '"batch starting" "limited seats"',
    '"applications open" "deadline"',
    '"early bird" "discount"',
    '"few spots left" program'
]

# ============================================
# VALIDATION LISTS
# ============================================

# Invalid Instagram usernames to filter out
INVALID_INSTAGRAM_USERNAMES = [
    'p', 'reel', 'reels', 'stories', 'explore', 'accounts', 'direct',
    'tv', 'about', 'legal', 'help', 'privacy', 'terms', 'contact',
    'basic', 'developer', 'developers', 'blog', 'brand',
    'press', 'api', 'jobs', 'support', 'safety', 'download', 'web',
    'emails', 'email', 'locations', 'hashtag', 'settings', 'nametag',
    'session', 'login', 'challenge', 'directory', 'lite', 'data',
    'share', 'static', 'embed', 'graphql', 'query', 'oauth',
    'explore', 'tagged', 'saved', 'following', 'followers', 'channel'
]

# Invalid website domains to filter out
INVALID_WEBSITE_DOMAINS = [
    'gmail.com', 'yahoo.com', 'hotmail.com', 'outlook.com', 'mail.com',
    'facebook.com', 'instagram.com', 'twitter.com', 'x.com', 'fb.com',
    'meta.com', 'whatsapp.com', 'google.com', 'youtube.com', 'fbcdn.net',
    'linkedin.com', 'pinterest.com', 'tiktok.com', 'snapchat.com',
    'bit.ly', 'goo.gl', 't.co', 'tinyurl.com', 'shorturl.at'
]

# Invalid Twitter/X usernames
INVALID_TWITTER_USERNAMES = [
    'home', 'explore', 'search', 'settings', 'i', 'intent', 'share',
    'messages', 'notifications', 'login', 'signup', 'tos', 'privacy'
]

# ============================================
# VALIDATION FUNCTIONS
# ============================================

def is_valid_instagram_username(username):
    """Check if Instagram username is valid"""
    if not username:
        return False
    
    username = username.lower().strip()
    
    if username in INVALID_INSTAGRAM_USERNAMES:
        return False
    
    if len(username) < 2 or len(username) > 30:
        return False
    
    if not re.match(r'^[a-z0-9._]+$', username):
        return False
    
    if username.startswith('.') or username.endswith('.'):
        return False
    
    if '..' in username:
        return False
    
    return True


def is_valid_twitter_username(username):
    """Check if Twitter/X username is valid"""
    if not username:
        return False
    
    username = username.lower().strip()
    
    if username in INVALID_TWITTER_USERNAMES:
        return False
    
    if len(username) < 2 or len(username) > 15:
        return False
    
    if not re.match(r'^[a-z0-9_]+$', username):
        return False
    
    return True


def is_valid_website(url):
    """Check if website URL is valid and not a social media/email domain.

    Matches blocked domains on host boundaries (not substring), so legit
    sites like box.com / fox.com are NOT wrongly rejected by 'x.com'.
    """
    if not url:
        return False

    if '.' not in url:
        return False

    # Parse out the host so we compare domains, not raw substrings.
    candidate = url if '://' in url else 'http://' + url
    try:
        host = urlparse(candidate).netloc.lower()
    except Exception:
        host = url.lower()
    host = host.split('@')[-1].split(':')[0]  # strip any userinfo / port
    if not host:
        return False

    for domain in INVALID_WEBSITE_DOMAINS:
        d = domain.lower()
        if host == d or host.endswith('.' + d):
            return False

    return True


def is_valid_email(email):
    """Check if email is valid"""
    if not email:
        return False
    
    email_lower = email.lower()
    
    if '@' not in email or '.' not in email:
        return False
    
    # Filter out generic/placeholder emails
    invalid_patterns = ['example.com', 'test.com', 'dummy', 'noreply', 'no-reply']
    for pattern in invalid_patterns:
        if pattern in email_lower:
            return False
    
    return True


def clean_phone(phone):
    """Normalize to a valid Indian mobile ('+91 XXXXXXXXXX') or return None.

    A bare 10-13 digit count is NOT enough: numeric Facebook page IDs of that
    length leaked in as 'phones'. This requires a real Indian mobile (first
    digit 6-9) or a 0-prefixed landline, matching the harvest scripts.
    """
    raw = (phone or "").strip()
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    m = re.search(r"(?:\+?91|0)?([6-9]\d{9})", digits)
    if m:
        return "+91 " + m.group(1)
    # landline with leading 0 + STD code
    if raw.startswith("0") and 10 <= len(digits) <= 11 and digits[1] in "1234589":
        return raw
    return None


def is_valid_phone(phone):
    """Valid only if it normalizes to a real Indian mobile/landline.

    Rejects junk (e.g. Facebook page IDs) that the old digit-count-only
    check let through.
    """
    return clean_phone(phone) is not None


# ============================================
# KEYWORD FUNCTIONS
# ============================================

def build_keyword_list(query, custom_keywords=None):
    """Build a comprehensive keyword list based on query and/or custom keywords"""
    
    if custom_keywords:
        return list(set(custom_keywords)), "custom"
    
    # Start with universal high-priority keywords
    keywords = set()
    keywords.update(COURSE_INDICATORS)
    keywords.update(CTA_TRIGGERS)
    keywords.update(PAYMENT_SIGNALS)
    keywords.update(CREDENTIAL_SIGNALS)
    keywords.update(OUTCOME_SIGNALS)
    
    # Detect niche from query
    query_lower = query.lower()
    matched_niche = None
    
    for niche, data in NICHE_KEYWORDS.items():
        # Check niche name
        if niche.replace('_', ' ') in query_lower or niche in query_lower:
            matched_niche = niche
            break
        
        # Check sub-categories
        for sub_cat in data.get("sub_categories", []):
            if sub_cat in query_lower:
                matched_niche = niche
                break
        
        if matched_niche:
            break
        
        # Check search keywords
        for kw in data.get("search_keywords", []):
            if kw in query_lower:
                matched_niche = niche
                break
        
        if matched_niche:
            break
    
    # Add niche-specific keywords if matched
    if matched_niche:
        niche_data = NICHE_KEYWORDS[matched_niche]
        keywords.update(niche_data.get("sub_categories", []))
        keywords.update(niche_data.get("search_keywords", []))
        keywords.update(niche_data.get("trigger_words", []))
    
    return list(keywords), matched_niche


def keyword_match_score(text, keywords):
    """
    Calculate a match score based on keyword matches.
    Returns (score, matched_keywords)
    
    Scoring:
    - High priority keywords (course indicators, CTAs, payment): 2 points
    - Other keywords: 1 point
    """
    if not text or not keywords:
        return 0, []
    
    text_lower = text.lower()
    matched = []
    score = 0
    
    # High priority keywords get extra weight
    high_priority = set()
    high_priority.update([kw.lower() for kw in COURSE_INDICATORS])
    high_priority.update([kw.lower() for kw in CTA_TRIGGERS])
    high_priority.update([kw.lower() for kw in PAYMENT_SIGNALS])
    
    for keyword in keywords:
        kw_lower = keyword.lower()
        if kw_lower in text_lower:
            matched.append(keyword)
            # Higher score for high-priority keywords
            if kw_lower in high_priority:
                score += 2
            else:
                score += 1
    
    return score, list(set(matched))


def contains_avoid_keywords(text):
    """Check if text contains keywords we want to avoid"""
    if not text:
        return False
    
    text_lower = text.lower()
    for avoid_kw in AVOID_KEYWORDS:
        if avoid_kw in text_lower:
            return True
    return False


# ============================================
# MAIN SCRAPER CLASS
# ============================================

class FacebookAdsLibraryScraper:
    def __init__(self):
        self.base_url = "https://www.facebook.com/ads/library/"
        self.seen_library_ids = set()  # Track seen Library IDs for deduplication
        self.seen_advertiser_keys = set()  # Track seen advertisers as backup
        # Run-level monitoring counters (fail-loud). A scraper instance is
        # reused across queries by the harvest scripts, so these accumulate
        # across the whole run and are summarised / asserted at the end.
        self.run_stats = {
            "queries_run": 0,
            "advertisers_found": 0,
            "contacts_found": 0,
            "failures": 0,
            "library_id_detected": False,  # selector ever seen across the run
        }
        # How many times to retry a blocked / errored query navigation.
        self.max_query_retries = int(os.environ.get("SCRAPER_MAX_RETRIES", "3"))

    def reset_deduplication(self):
        """Reset deduplication tracking for new scrape session"""
        self.seen_library_ids = set()
        self.seen_advertiser_keys = set()

    def reset_run_stats(self):
        """Reset run-level monitoring counters (start of a fresh run)."""
        self.run_stats = {
            "queries_run": 0,
            "advertisers_found": 0,
            "contacts_found": 0,
            "failures": 0,
            "library_id_detected": False,
        }

    def run_summary(self):
        """Return a human-readable one-line summary of the run so far."""
        s = self.run_stats
        return (
            "RUN SUMMARY | queries_run={queries_run} "
            "advertisers_found={advertisers_found} "
            "contacts_found={contacts_found} "
            "failures={failures} "
            "library_id_detected={library_id_detected}".format(**s)
        )
    
    def is_duplicate(self, library_id, advertiser_name):
        """Check if this ad is a duplicate based on Library ID or advertiser"""
        # Primary check: Library ID
        if library_id:
            if library_id in self.seen_library_ids:
                return True
            self.seen_library_ids.add(library_id)
        
        # Secondary check: Advertiser name (normalized)
        if advertiser_name:
            advertiser_key = advertiser_name.lower().strip()
            # Only use advertiser as backup if no library_id
            if not library_id:
                if advertiser_key in self.seen_advertiser_keys:
                    return True
                self.seen_advertiser_keys.add(advertiser_key)
        
        return False
    
    def extract_contact_info(self, text):
        """Extract emails, phone numbers, and websites from text"""
        contact_info = {"emails": [], "phones": [], "websites": [], "whatsapp": []}
        
        if not text:
            return contact_info
        
        # Email extraction
        email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b'
        emails = re.findall(email_pattern, text)
        contact_info["emails"] = list(set([e for e in emails if is_valid_email(e)]))
        
        # Phone extraction - multiple patterns for Indian and international numbers
        phone_patterns = [
            r'\+91[\s-]?\d{5}[\s-]?\d{5}',  # +91 XXXXX XXXXX
            r'\+91[\s-]?\d{10}',             # +91XXXXXXXXXX
            r'\b[6-9]\d{9}\b',               # Indian mobile (starts with 6-9)
            r'\b\d{5}[\s-]?\d{5}\b',         # XXXXX XXXXX
            r'\b\d{3}[-.\s]\d{3}[-.\s]\d{4}\b',  # XXX-XXX-XXXX
            r'\b\d{4}[-.\s]\d{3}[-.\s]\d{3}\b',  # XXXX-XXX-XXX
            r'\+\d{1,3}[\s-]?\d{4,5}[\s-]?\d{4,6}',  # International
        ]
        
        phones = []
        for pattern in phone_patterns:
            found = re.findall(pattern, text)
            phones.extend(found)
        
        cleaned_phones = []
        for phone in phones:
            normalized = clean_phone(phone)
            if normalized:
                cleaned_phones.append(normalized)

        contact_info["phones"] = list(set(cleaned_phones))
        
        # WhatsApp specific patterns
        whatsapp_patterns = [
            r'(?:whatsapp|wa\.me|wa)[:\s]*([+\d\s-]{10,15})',
            r'wa\.me/(\d{10,13})',
        ]
        for pattern in whatsapp_patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            for match in matches:
                # Validate WhatsApp through the same clean_phone path so junk
                # (wa.me slugs, page IDs) is rejected instead of leaking.
                normalized = clean_whatsapp(match)
                if normalized:
                    contact_info["whatsapp"].append(normalized)
        contact_info["whatsapp"] = list(set(contact_info["whatsapp"]))
        
        # URL extraction
        url_pattern = r'https?://(?:www\.)?[-a-zA-Z0-9@:%._\+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}\b(?:[-a-zA-Z0-9()@:%_\+.~#?&/=]*)'
        urls = re.findall(url_pattern, text)
        filtered_urls = [u for u in urls if is_valid_website(u)]
        contact_info["websites"] = list(set(filtered_urls))
        
        return contact_info
    
    def extract_date(self, text):
        """Extract the 'Started running on' date from text with multiple patterns"""
        if not text:
            return None
        
        # Comprehensive date patterns
        date_patterns = [
            # "Started running on" patterns
            r'Started running on\s+(\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4})',
            r'Started running on\s+([A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4})',
            r'Started running on\s+(\d{1,2}/\d{1,2}/\d{4})',
            r'Started running on\s+(\d{4}-\d{2}-\d{2})',
            
            # "Started running" without "on"
            r'Started running\s+(\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4})',
            r'Started running\s+([A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4})',
            
            # Just "Started"
            r'Started\s+(\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4})',
            r'Started\s+([A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4})',
            
            # "running" followed by date
            r'running[^\d]{0,20}(\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4})',
            r'running[^\d]{0,20}([A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4})',
            
            # Generic date patterns
            r'\b(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4})\b',
            r'\b((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+\d{4})\b',
            r'\b(\d{1,2}/\d{1,2}/\d{4})\b',
            r'\b(\d{4}-\d{2}-\d{2})\b',
        ]
        
        for pattern in date_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1).strip()
        
        return None
    
    def extract_instagram_username(self, text_or_url):
        """Extract and validate Instagram username from text or URL"""
        if not text_or_url:
            return None
        
        # Try to extract from URL pattern
        patterns = [
            r'instagram\.com/([a-zA-Z0-9._]+)',
            r'instagr\.am/([a-zA-Z0-9._]+)',
            r'@([a-zA-Z0-9._]{2,30})',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, text_or_url, re.IGNORECASE)
            if match:
                username = match.group(1).lower()
                if is_valid_instagram_username(username):
                    return username
        
        return None
    
    def extract_twitter_username(self, text_or_url):
        """Extract and validate Twitter/X username from text or URL"""
        if not text_or_url:
            return None
        
        patterns = [
            r'(?:twitter|x)\.com/([a-zA-Z0-9_]+)',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, text_or_url, re.IGNORECASE)
            if match:
                username = match.group(1).lower()
                if is_valid_twitter_username(username):
                    return username
        
        return None
    
    async def init_browser(self, playwright):
        """Initialize browser with anti-detection settings.

        Hardening: rotates a realistic user-agent per context and, if
        `SCRAPER_PROXIES` is set, routes through a rotating proxy. With no proxy
        configured the launch is identical to before (direct connection).
        """
        launch_kwargs = {
            "headless": True,
            "args": [
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--no-first-run",
                "--no-zygote",
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--window-size=1920,1080",
            ],
        }
        proxy = pick_proxy()
        if proxy:
            launch_kwargs["proxy"] = proxy
            logger.info("Launching browser via proxy %s", proxy.get("server"))

        browser = await playwright.chromium.launch(**launch_kwargs)
        user_agent = pick_user_agent()
        context = await browser.new_context(
            user_agent=user_agent,
            viewport={'width': 1920, 'height': 1080},
            locale="en-US",
            java_script_enabled=True,
            timezone_id="Asia/Kolkata"
        )
        
        # Anti-detection scripts
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
            window.chrome = { runtime: {} };
        """)
        
        return browser, context

    async def extract_ads_with_deduplication(self, page, keywords=None, min_score=1):
        """
        Extract ads with robust deduplication based on Library ID.
        Only returns ads that match keyword criteria.
        """
        
        # First, extract all "Started running on" dates from the page
        all_dates = await page.evaluate('''() => {
            const dates = [];
            const text = document.body.textContent || '';
            
            const patterns = [
                /Started running on\\s+(\\d{1,2}\\s+[A-Za-z]{3,9}\\s+\\d{4})/gi,
                /Started running\\s+(\\d{1,2}\\s+[A-Za-z]{3,9}\\s+\\d{4})/gi,
            ];
            
            for (const pattern of patterns) {
                let match;
                while ((match = pattern.exec(text)) !== null) {
                    dates.push(match[1]);
                }
            }
            
            return dates;
        }''')
        
        print(f"  Found {len(all_dates)} 'Started running' dates on page", file=sys.stderr)
        
        # Extract ads using JavaScript
        print(f"  Extracting ads...", file=sys.stderr)
        
        js_extracted = await page.evaluate('''(invalidUsernames) => {
            const ads = [];
            const seenLibraryIds = new Set();
            
            // Helper function to validate Instagram username
            function isValidUsername(username) {
                if (!username) return false;
                username = username.toLowerCase();
                if (invalidUsernames.includes(username)) return false;
                if (username.length < 2 || username.length > 30) return false;
                if (!/^[a-z0-9._]+$/.test(username)) return false;
                return true;
            }
            
            // Get all Library IDs from the page HTML
            const html = document.body.innerHTML;
            const libIdRegex = /Library ID[:\\s]*([\\d]+)/gi;
            const libraryIds = [];
            let match;
            
            while ((match = libIdRegex.exec(html)) !== null) {
                if (!seenLibraryIds.has(match[1])) {
                    seenLibraryIds.add(match[1]);
                    libraryIds.push(match[1]);
                }
            }
            
            console.log('Found ' + libraryIds.length + ' unique Library IDs');
            
            // Get all Facebook page links
            const allLinks = Array.from(document.querySelectorAll('a[href*="facebook.com"]'));
            const pageLinks = allLinks.filter(link => {
                const href = link.href || '';
                return !href.includes('/ads/library') && 
                       !href.includes('l.facebook.com') &&
                       !href.includes('/help') &&
                       !href.includes('/policies') &&
                       !href.includes('/privacy') &&
                       !href.includes('/recover') &&
                       !href.includes('/login') &&
                       href !== 'https://www.facebook.com/' &&
                       href !== 'https://www.facebook.com';
            });
            
            // Build a map of Library ID -> page URL and advertiser name
            const libIdData = {};
            
            pageLinks.forEach(link => {
                const href = link.href || '';
                const linkText = (link.textContent || '').trim();
                
                // Check if this is a valid page URL
                const pageMatch = href.match(/facebook\\.com\\/([a-zA-Z0-9._-]+|\\d{10,})\\/?/);
                if (!pageMatch) return;
                
                const pageName = pageMatch[1];
                const invalidPages = ['ads', 'help', 'policies', 'privacy', 'search', 'watch', 
                                     'groups', 'events', 'marketplace', 'recover', 'login', 
                                     'pages', 'photo', 'video', 'reel', 'story', 'stories',
                                     'hashtag', 'profile.php', 'sharer'];
                if (invalidPages.includes(pageName.toLowerCase())) return;
                
                // Find which Library ID this link belongs to
                let parent = link.parentElement;
                for (let i = 0; i < 25 && parent; i++) {
                    const text = parent.textContent || '';
                    const libMatch = text.match(/Library ID[:\\s]*(\\d+)/i);
                    if (libMatch && libMatch[1]) {
                        const libId = libMatch[1];
                        if (!libIdData[libId]) {
                            libIdData[libId] = {
                                pageUrl: href.split('?')[0].split('#')[0],
                                advertiser: '',
                                text: text.substring(0, 5000)
                            };
                        }
                        
                        // Check if link text is a valid advertiser name
                        if (!libIdData[libId].advertiser && linkText && 
                            linkText.length >= 2 && linkText.length <= 100) {
                            const lower = linkText.toLowerCase();
                            const isMetadata = lower.includes('library id') || 
                                              lower.includes('started running') ||
                                              lower.includes('inactive') ||
                                              lower.includes('active') ||
                                              lower.includes('sponsored') ||
                                              lower.includes('see ad') ||
                                              lower.includes('platforms') ||
                                              lower.includes('see details') ||
                                              lower.includes('about this ad') ||
                                              /^\\d+$/.test(lower);
                            if (!isMetadata) {
                                libIdData[libId].advertiser = linkText;
                            }
                        }
                        break;
                    }
                    parent = parent.parentElement;
                }
            });
            
            // Now create ads from the data
            libraryIds.forEach((libId, index) => {
                const data = libIdData[libId] || {};
                const fullText = data.text || '';
                
                let advertiser = data.advertiser || '';
                if (!advertiser) {
                    advertiser = 'Advertiser ' + libId.substring(0, 8);
                }
                
                // Extract date
                let startedDate = '';
                const datePatterns = [
                    /Started running on\\s+(\\d{1,2}\\s+[A-Za-z]{3,9}\\s+\\d{4})/i,
                    /Started running\\s+(\\d{1,2}\\s+[A-Za-z]{3,9}\\s+\\d{4})/i,
                    /(\\d{1,2}\\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\\s+\\d{4})/i,
                ];
                for (const pattern of datePatterns) {
                    const dateMatch = fullText.match(pattern);
                    if (dateMatch) {
                        startedDate = dateMatch[1];
                        break;
                    }
                }
                
                // Extract platforms
                const platforms = [];
                const lowerText = fullText.toLowerCase();
                if (lowerText.includes('facebook')) platforms.push('Facebook');
                if (lowerText.includes('instagram')) platforms.push('Instagram');
                if (lowerText.includes('messenger')) platforms.push('Messenger');
                if (lowerText.includes('audience network')) platforms.push('Audience Network');
                if (lowerText.includes('meta')) platforms.push('Meta');
                
                // Extract ad text
                let adText = '';
                const sponsoredIdx = fullText.indexOf('Sponsored');
                if (sponsoredIdx > -1) {
                    adText = fullText.substring(sponsoredIdx + 9, sponsoredIdx + 1000);
                    adText = adText.replace(/Library ID[:\\s]*\\d+/gi, '').trim();
                } else {
                    adText = fullText.substring(0, 1000);
                }
                
                // Clean up ad text
                adText = adText.replace(/\\s+/g, ' ').trim();
                
                // Extract Instagram username
                let instagramUsername = '';
                const instaPatterns = [
                    /instagram\\.com\\/([a-zA-Z0-9._]+)/i,
                    /@([a-zA-Z0-9._]{2,30})(?:\\s|$|,)/,
                ];
                for (const pattern of instaPatterns) {
                    const instaMatch = fullText.match(pattern);
                    if (instaMatch && instaMatch[1] && isValidUsername(instaMatch[1])) {
                        instagramUsername = instaMatch[1].toLowerCase();
                        break;
                    }
                }
                
                // Extract landing page URL if present
                let landingPage = '';
                const landingPatterns = [
                    /(?:Learn more|Sign up|Shop now|Apply now|Book now|Get offer|See more)[^"]*href="([^"]+)"/i,
                ];
                for (const pattern of landingPatterns) {
                    const lpMatch = fullText.match(pattern);
                    if (lpMatch && lpMatch[1]) {
                        landingPage = lpMatch[1];
                        break;
                    }
                }
                
                ads.push({
                    advertiser: advertiser,
                    library_id: libId,
                    started_date: startedDate,
                    ad_text: adText.substring(0, 800),
                    platforms: platforms.length > 0 ? platforms.join(', ') : 'N/A',
                    advertiser_page_url: data.pageUrl || '',
                    instagram_username: instagramUsername,
                    landing_page: landingPage,
                    full_text: fullText
                });
            });
            
            return ads;
        }''', INVALID_INSTAGRAM_USERNAMES)
        
        print(f"  JavaScript extracted {len(js_extracted)} total ads", file=sys.stderr)
        
        # Process and filter results with deduplication
        results = []
        duplicates_skipped = 0
        filtered_by_keywords = 0
        filtered_by_avoid = 0
        dates_found = 0
        
        for idx, ad in enumerate(js_extracted):
            lib_id = ad.get('library_id', '')
            advertiser = ad.get('advertiser', '')
            
            # DEDUPLICATION CHECK
            if self.is_duplicate(lib_id, advertiser):
                duplicates_skipped += 1
                continue
            
            # Get full text for filtering
            full_text_for_matching = (
                advertiser + ' ' + 
                ad.get('ad_text', '') + ' ' + 
                ad.get('full_text', '')
            )
            
            # Check for avoid keywords
            if contains_avoid_keywords(full_text_for_matching):
                filtered_by_avoid += 1
                continue
            
            # KEYWORD FILTERING
            if keywords:
                score, matched_kws = keyword_match_score(full_text_for_matching, keywords)
                if score < min_score:
                    filtered_by_keywords += 1
                    continue
            else:
                score = 0
                matched_kws = []
            
            # Extract date if missing
            started_date = ad.get('started_date', '')
            if not started_date:
                started_date = self.extract_date(ad.get('full_text', ''))
            
            if started_date:
                dates_found += 1
            
            # Extract contact info from ad text
            ad_contact = self.extract_contact_info(ad.get('ad_text', '') + ' ' + ad.get('full_text', ''))
            
            # Validate page URL
            page_url = ad.get('advertiser_page_url', '')
            invalid_urls = ['https://www.facebook.com/', 'https://www.facebook.com', 
                           'https://facebook.com/', 'https://facebook.com']
            if page_url in invalid_urls:
                page_url = ''
            
            # Validate Instagram username
            instagram_username = ad.get('instagram_username', '')
            if instagram_username and not is_valid_instagram_username(instagram_username):
                instagram_username = ''
            
            ad_data = {
                "index": len(results) + 1,
                "advertiser": advertiser if advertiser else 'Unknown',
                "library_id": lib_id,
                "ad_text": ad.get('ad_text', ''),
                "started_running": started_date if started_date else "N/A",
                "platforms": ad.get('platforms', 'N/A'),
                "advertiser_page_url": page_url,
                "landing_page": ad.get('landing_page', ''),
                "instagram_username": instagram_username,
                "ad_emails": ad_contact["emails"],
                "ad_phones": ad_contact["phones"],
                "ad_websites": ad_contact["websites"],
                "ad_whatsapp": ad_contact.get("whatsapp", []),
                "match_score": score,
                "matched_keywords": matched_kws
            }
            
            results.append(ad_data)
            
            # Log first few ads
            if len(results) <= 5:
                kw_str = f" | Score: {score}" if score > 0 else ""
                insta_str = f" | IG: @{instagram_username}" if instagram_username else ""
                print(f"  ✓ Ad {len(results)}: {advertiser[:35]} | Date: {started_date or 'N/A'}{insta_str}{kw_str}", file=sys.stderr)
        
        print(f"\n  📊 Extraction Summary:", file=sys.stderr)
        print(f"     Raw ads extracted: {len(js_extracted)}", file=sys.stderr)
        print(f"     Duplicates skipped: {duplicates_skipped}", file=sys.stderr)
        print(f"     Filtered (avoid keywords): {filtered_by_avoid}", file=sys.stderr)
        print(f"     Filtered (low score): {filtered_by_keywords}", file=sys.stderr)
        print(f"     Valid ads: {len(results)}", file=sys.stderr)
        print(f"     Dates found: {dates_found}/{len(results)}", file=sys.stderr)
        
        # Sort by match score (highest first)
        results.sort(key=lambda x: x.get('match_score', 0), reverse=True)
        
        # Re-index after sorting
        for idx, ad in enumerate(results):
            ad['index'] = idx + 1
        
        # NOTE: leftover page-level dates used to be assigned to ads by list
        # position. That attached the WRONG "started running" date to ads, so
        # it's been removed. An ad now keeps only the date found within its own
        # text block; otherwise it stays "N/A" (honest > wrong).
        na_dates = sum(1 for ad in results if ad['started_running'] == 'N/A')
        if na_dates:
            print(f"  ℹ️  {na_dates} ad(s) had no date in their own block — left as N/A", file=sys.stderr)

        return results

    async def scrape_advertiser_page(self, page, advertiser_url, advertiser_name):
        """
        Visit advertiser's Facebook page and extract COMPREHENSIVE contact details.
        Visits main page + all 4 About page variants for maximum data extraction.
        """
        details = {
            "page_visited": False,
            "facebook_page": advertiser_url,
            "page_name": "",
            "page_username": "",
            "category": "",
            "subcategory": "",
            "followers": "",
            "followers_count": 0,
            "following": "",
            "following_count": 0,
            "likes": "",
            "likes_count": 0,
            "rating": "",
            "reviews_count": 0,
            "emails": [],
            "phones": [],
            "websites": [],
            "whatsapp": "",
            "instagram": "",
            "instagram_username": "",
            "twitter": "",
            "twitter_username": "",
            "youtube": "",
            "youtube_channel": "",
            "linkedin": "",
            "linkedin_profile": "",
            "bio": "",
            "about_text": "",
            "address": "",
            "city": "",
            "state": "",
            "country": "",
            "pincode": "",
            "hours": "",
            "price_range": "",
            "founded": "",
            "products": "",
            "services": "",
            "mission": "",
            "vision": "",
            "registration": "",
            "gst": "",
            "pan": "",
            "impressum": "",
            "additional_info": {},
            "scrape_timestamp": datetime.now().isoformat()
        }
        
        if not advertiser_url:
            return details
        
        try:
            # Visit main page first
            await page.goto(advertiser_url, wait_until="domcontentloaded", timeout=25000)
            await page.wait_for_timeout(random.randint(2500, 4000))
            details["page_visited"] = True
            
            # Extract main page info using JavaScript
            main_page_data = await page.evaluate('''() => {
                const data = {
                    page_name: '',
                    page_username: '',
                    category: '',
                    subcategory: '',
                    followers: '',
                    followers_count: 0,
                    following: '',
                    following_count: 0,
                    likes: '',
                    likes_count: 0,
                    rating: '',
                    reviews_count: 0,
                    bio: ''
                };
                
                const pageText = document.body.innerText || '';
                const pageHtml = document.body.innerHTML || '';
                
                // ===== PAGE NAME =====
                const h1 = document.querySelector('h1');
                if (h1) {
                    data.page_name = h1.textContent.trim();
                }
                
                // ===== PAGE USERNAME =====
                const url = window.location.href;
                const usernameMatch = url.match(/facebook\\.com\\/([a-zA-Z0-9._-]+)/);
                if (usernameMatch && usernameMatch[1]) {
                    data.page_username = usernameMatch[1];
                }
                
                // ===== CATEGORY EXTRACTION =====
                const categoryPatterns = [
                    /Page\\s*[·•]\\s*([^·\\n]+?)(?:\\s*[·•]\\s*([^\\n]+))?(?:\\n|$)/i,
                    /([A-Za-z\\s&\\/]+)\\s*[·•]\\s*([^\\n]+)/,
                ];
                
                for (const pattern of categoryPatterns) {
                    const match = pageText.match(pattern);
                    if (match && match[1]) {
                        const cat = match[1].trim();
                        if (cat.length > 2 && cat.length < 60 && 
                            !cat.includes('follower') && !cat.includes('like') &&
                            !cat.includes('http') && !cat.includes('@')) {
                            data.category = cat;
                            if (match[2]) {
                                const subcat = match[2].trim();
                                if (subcat.length < 60) {
                                    data.subcategory = subcat;
                                }
                            }
                            break;
                        }
                    }
                }
                
                // Also try to find category from specific elements
                const allSpans = document.querySelectorAll('span, a');
                for (const el of allSpans) {
                    const text = el.textContent.trim();
                    const href = el.href || '';
                    
                    if (href.includes('/pages/category/') || href.includes('page_category')) {
                        if (text.length > 2 && text.length < 60) {
                            data.category = text;
                            break;
                        }
                    }
                }
                
                // ===== FOLLOWERS/FOLLOWING/LIKES EXTRACTION =====
                const statsPatterns = [
                    { regex: /([\\d,.]+[KMB]?)\\s*followers?/gi, field: 'followers' },
                    { regex: /([\\d,.]+[KMB]?)\\s*following/gi, field: 'following' },
                    { regex: /([\\d,.]+[KMB]?)\\s*likes?/gi, field: 'likes' },
                    { regex: /([\\d,.]+[KMB]?)\\s*people\\s*(?:like|follow)/gi, field: 'likes' },
                ];
                
                for (const {regex, field} of statsPatterns) {
                    const matches = pageText.match(regex);
                    if (matches && matches.length > 0) {
                        const match = matches[0];
                        const numMatch = match.match(/([\\d,.]+[KMB]?)/i);
                        if (numMatch) {
                            data[field] = numMatch[1];
                            
                            let num = numMatch[1].replace(/,/g, '');
                            if (num.includes('K')) {
                                num = parseFloat(num) * 1000;
                            } else if (num.includes('M')) {
                                num = parseFloat(num) * 1000000;
                            } else if (num.includes('B')) {
                                num = parseFloat(num) * 1000000000;
                            } else {
                                num = parseFloat(num);
                            }
                            data[field + '_count'] = Math.round(num) || 0;
                        }
                    }
                }
                
                // ===== RATING & REVIEWS =====
                const ratingPatterns = [
                    /([0-9.]+)\\s*(?:out of 5|stars?|⭐)/i,
                    /Rating:\\s*([0-9.]+)/i,
                ];
                for (const pattern of ratingPatterns) {
                    const match = pageText.match(pattern);
                    if (match && match[1]) {
                        data.rating = match[1];
                        break;
                    }
                }
                
                const reviewsMatch = pageText.match(/([\\d,]+)\\s*reviews?/i);
                if (reviewsMatch) {
                    data.reviews_count = parseInt(reviewsMatch[1].replace(/,/g, '')) || 0;
                }
                
                // ===== BIO/INTRO EXTRACTION =====
                const introSelectors = [
                    '[data-pagelet="ProfileTilesBio"]',
                    '[data-pagelet="intro"]',
                    '[data-pagelet="ProfileIntro"]',
                    '.bio',
                    '#intro'
                ];
                
                for (const selector of introSelectors) {
                    const el = document.querySelector(selector);
                    if (el) {
                        data.bio = el.textContent.trim().substring(0, 500);
                        break;
                    }
                }
                
                if (!data.bio) {
                    const metaDesc = document.querySelector('meta[property="og:description"]');
                    if (metaDesc) {
                        data.bio = metaDesc.content || '';
                    }
                }
                
                return data;
            }''')
            
            # Merge main page data
            if main_page_data:
                for key, value in main_page_data.items():
                    if value and key in details:
                        details[key] = value
            
            # Get page source for BeautifulSoup backup
            html = await page.content()
            soup = BeautifulSoup(html, "html.parser")
            
            meta_desc = soup.find('meta', {'property': 'og:description'})
            if meta_desc and meta_desc.get('content') and not details["bio"]:
                details["bio"] = meta_desc['content'][:500]
            
            # ===== VISIT ALL ABOUT PAGES =====
            base_url = advertiser_url.rstrip('/')
            about_urls = [
                f"{base_url}/about",
                f"{base_url}/about_details", 
                f"{base_url}/about_contact_and_basic_info",
                f"{base_url}/about_profile_transparency"
            ]
            
            for about_url in about_urls:
                try:
                    await page.goto(about_url, wait_until="domcontentloaded", timeout=15000)
                    await page.wait_for_timeout(random.randint(2000, 3500))
                    
                    # Use JavaScript to extract comprehensive contact info from About page
                    page_data = await page.evaluate('''(invalidUsernames) => {
                        const data = {
                            emails: [],
                            phones: [],
                            websites: [],
                            whatsapp: '',
                            instagram: '',
                            instagram_username: '',
                            twitter: '',
                            twitter_username: '',
                            youtube: '',
                            youtube_channel: '',
                            linkedin: '',
                            linkedin_profile: '',
                            address: '',
                            city: '',
                            state: '',
                            country: '',
                            pincode: '',
                            hours: '',
                            price_range: '',
                            founded: '',
                            products: '',
                            services: '',
                            mission: '',
                            vision: '',
                            registration: '',
                            gst: '',
                            pan: '',
                            impressum: '',
                            about_text: '',
                            category: '',
                            additional_info: {}
                        };
                        
                        const pageText = document.body.innerText || '';
                        data.about_text = pageText.substring(0, 5000);
                        
                        // Helper function to validate Instagram username
                        function isValidUsername(username) {
                            if (!username) return false;
                            username = username.toLowerCase();
                            if (invalidUsernames.includes(username)) return false;
                            if (username.length < 2 || username.length > 30) return false;
                            if (!/^[a-z0-9._]+$/.test(username)) return false;
                            if (username.startsWith('.') || username.endsWith('.')) return false;
                            if (username.includes('..')) return false;
                            return true;
                        }
                        
                        // Invalid website domains
                        const invalidDomains = ['gmail.com', 'yahoo.com', 'hotmail.com', 'outlook.com', 
                            'facebook.com', 'instagram.com', 'twitter.com', 'x.com', 'fb.com',
                            'meta.com', 'whatsapp.com', 'google.com', 'fbcdn.net', 'bit.ly'];
                        
                        // ===== PHONE NUMBER EXTRACTION =====
                        const phonePatterns = [
                            /(?:\\+91[\\s-]?)?\\d{3,5}[\\s-]?\\d{3,5}[\\s-]?\\d{4}/g,
                            /(?:\\+91[\\s-]?)?\\d{10}/g,
                            /\\d{3}[\\s-]\\d{4}[\\s-]\\d{4}/g,
                            /\\d{4}[\\s-]\\d{3}[\\s-]\\d{3}/g,
                            /\\d{5}[\\s-]\\d{5}/g,
                            /\\+\\d{1,3}[\\s-]?\\d{4,5}[\\s-]?\\d{4,6}/g,
                            /\\(\\d{3}\\)[\\s-]?\\d{3}[\\s-]?\\d{4}/g
                        ];
                        
                        for (const pattern of phonePatterns) {
                            const matches = pageText.match(pattern);
                            if (matches) {
                                for (const match of matches) {
                                    const cleaned = match.replace(/[\\s\\-\\(\\)]/g, '');
                                    if (cleaned.length >= 10 && cleaned.length <= 15) {
                                        data.phones.push(match.trim());
                                    }
                                }
                            }
                        }
                        
                        // ===== EMAIL EXTRACTION =====
                        const emailPattern = /[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}/g;
                        const emails = pageText.match(emailPattern);
                        if (emails) {
                            data.emails = [...new Set(emails.map(e => e.toLowerCase()))];
                        }
                        
                        // ===== WHATSAPP =====
                        const waPatterns = [
                            /(?:whatsapp|wa\\.me|wa)[:\\s]*([+\\d\\s-]{10,15})/i,
                            /wa\\.me\\/(\\d{10,13})/i,
                        ];
                        for (const pattern of waPatterns) {
                            const match = pageText.match(pattern);
                            if (match && match[1]) {
                                data.whatsapp = match[1].replace(/[\\s-]/g, '');
                                break;
                            }
                        }
                        
                        // ===== ADDRESS EXTRACTION =====
                        const addressPatterns = [
                            /(?:Address|Location|Office|Visit us at|Located at|Headquarters)[:\\s]*([^\\n]{10,300})/i,
                            /\\d+[,\\s]+[A-Za-z\\s]+(?:Road|Street|Lane|Avenue|Nagar|Colony|Sector|Block|Floor|Building|Plot|Phase|Market|Complex|Tower|Marg|Path)[^\\n]{5,200}/i,
                            /(?:Near|Opposite|Behind|Next to|Adjacent to)[\\s]+[^\\n]{10,150}/i
                        ];
                        for (const pattern of addressPatterns) {
                            const match = pageText.match(pattern);
                            if (match) {
                                const addr = (match[1] || match[0]).trim();
                                if (addr.length > 10 && !addr.includes('http')) {
                                    data.address = addr.substring(0, 300);
                                    break;
                                }
                            }
                        }
                        
                        // ===== CITY/STATE/COUNTRY EXTRACTION =====
                        const locationPatterns = [
                            /(?:City|Located in|Based in)[:\\s]*([A-Za-z\\s]+?)(?:,|\\n|$)/i,
                            /([A-Za-z]+),\\s*(Maharashtra|Delhi|Karnataka|Tamil Nadu|Gujarat|Rajasthan|Uttar Pradesh|West Bengal|Kerala|Punjab|Haryana|Bihar|Telangana|Andhra Pradesh)/i,
                            /([A-Za-z]+),\\s*(India|IN)\\s*-?\\s*(\\d{6})?/i,
                        ];
                        for (const pattern of locationPatterns) {
                            const match = pageText.match(pattern);
                            if (match && match[1]) {
                                const city = match[1].trim();
                                if (city.length > 2 && city.length < 50) {
                                    data.city = city;
                                    if (match[2]) data.state = match[2].trim();
                                    if (match[3]) data.pincode = match[3].trim();
                                    break;
                                }
                            }
                        }
                        
                        // ===== PINCODE =====
                        const pincodeMatch = pageText.match(/\\b(\\d{6})\\b/);
                        if (pincodeMatch && !data.pincode) {
                            data.pincode = pincodeMatch[1];
                        }
                        
                        // ===== BUSINESS HOURS =====
                        const hoursPatterns = [
                            /(?:Hours|Open|Timing|Business Hours|Working Hours)[:\\s]*([^\\n]{5,200})/i,
                            /(\\d{1,2}(?::\\d{2})?\\s*(?:AM|PM|am|pm)\\s*[-–to]+\\s*\\d{1,2}(?::\\d{2})?\\s*(?:AM|PM|am|pm))/i,
                            /(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*[\\s\\-:]+\\d{1,2}(?::\\d{2})?\\s*(?:AM|PM|am|pm)/i,
                            /(?:Always open|Open 24 hours|24\\/7|Open now)/i
                        ];
                        for (const pattern of hoursPatterns) {
                            const match = pageText.match(pattern);
                            if (match) {
                                data.hours = (match[1] || match[0]).trim().substring(0, 200);
                                break;
                            }
                        }
                        
                        // ===== PRICE RANGE =====
                        const pricePatterns = [
                            /(?:Price Range|Pricing|Price)[:\\s]*([₹$€£]?[\\d,]+\\s*[-–to]+\\s*[₹$€£]?[\\d,]+)/i,
                            /(?:Price Range)[:\\s]*([^\\n]{5,50})/i,
                            /(₹[\\d,]+\\s*[-–]\\s*₹[\\d,]+)/,
                            /(?:Starting from|From)[:\\s]*(₹[\\d,]+)/i,
                        ];
                        for (const pattern of pricePatterns) {
                            const match = pageText.match(pattern);
                            if (match) {
                                data.price_range = (match[1] || match[0]).trim();
                                break;
                            }
                        }
                        
                        // ===== FOUNDED/ESTABLISHED =====
                        const foundedPatterns = [
                            /(?:Founded|Established|Since|Started in|Operating since|In business since)[:\\s]*(\\d{4})/i,
                            /(?:Est\\.?|Since)[\\s]*(\\d{4})/i,
                        ];
                        // LIVE BUG FIX: ceiling was hardcoded to 2025, silently
                        // rejecting 2026+ founders. Use a dynamic ceiling
                        // (current year + 1) computed in JS; Python re-validates
                        // via is_valid_founded_year on the merged value.
                        const maxFoundedYear = new Date().getFullYear() + 1;
                        for (const pattern of foundedPatterns) {
                            const match = pageText.match(pattern);
                            if (match && match[1]) {
                                const year = parseInt(match[1]);
                                if (year >= 1900 && year <= maxFoundedYear) {
                                    data.founded = match[1];
                                    break;
                                }
                            }
                        }
                        
                        // ===== PRODUCTS/SERVICES =====
                        const productsPatterns = [
                            /(?:Products|Services|We offer|Specializes in|Offering|What we do)[:\\s]*([^\\n]{10,400})/i,
                        ];
                        for (const pattern of productsPatterns) {
                            const match = pageText.match(pattern);
                            if (match && match[1]) {
                                data.products = match[1].trim().substring(0, 300);
                                break;
                            }
                        }
                        
                        // ===== MISSION/VISION =====
                        const missionMatch = pageText.match(/(?:Mission|Our Mission)[:\\s]*([^\\n]{20,400})/i);
                        if (missionMatch) data.mission = missionMatch[1].trim().substring(0, 300);
                        
                        const visionMatch = pageText.match(/(?:Vision|Our Vision)[:\\s]*([^\\n]{20,400})/i);
                        if (visionMatch) data.vision = visionMatch[1].trim().substring(0, 300);
                        
                        // ===== REGISTRATION/GST/PAN =====
                        const gstMatch = pageText.match(/(?:GST(?:IN)?|GSTIN)[:\\s]*([A-Z0-9]{15})/i);
                        if (gstMatch) data.gst = gstMatch[1].toUpperCase();
                        
                        const panMatch = pageText.match(/(?:PAN)[:\\s]*([A-Z]{5}[0-9]{4}[A-Z])/i);
                        if (panMatch) data.pan = panMatch[1].toUpperCase();
                        
                        const regMatch = pageText.match(/(?:Registration|Reg\\.?\\s*No\\.?|CIN|LLPIN)[:\\s]*([A-Z0-9-]{5,25})/i);
                        if (regMatch) data.registration = regMatch[1];
                        
                        // ===== CATEGORY FROM ABOUT =====
                        const categoryPatterns = [
                            /(?:Category|Type|Business Type)[:\\s]*([A-Za-z\\s&\\/]+?)(?:\\n|$)/i,
                            /Page\\s*[·•]\\s*([A-Za-z\\s&\\/]+?)(?:\\n|$|\\d)/i
                        ];
                        for (const pattern of categoryPatterns) {
                            const match = pageText.match(pattern);
                            if (match && match[1]) {
                                const cat = match[1].trim();
                                if (cat.length > 2 && cat.length < 60) {
                                    data.category = cat;
                                    break;
                                }
                            }
                        }
                        
                        // ===== SOCIAL MEDIA EXTRACTION =====
                        const allLinks = document.querySelectorAll('a[href]');
                        
                        for (const link of allLinks) {
                            const href = link.href || '';
                            const linkText = (link.textContent || '').trim();
                            
                            // Instagram
                            if ((href.includes('instagram.com/') || (href.includes('l.facebook.com') && href.includes('instagram'))) && !data.instagram_username) {
                                const instaMatch = href.match(/instagram\\.com\\/([a-zA-Z0-9._]+)/);
                                if (instaMatch && instaMatch[1]) {
                                    const username = instaMatch[1].toLowerCase();
                                    if (isValidUsername(username)) {
                                        data.instagram = 'https://instagram.com/' + username;
                                        data.instagram_username = username;
                                    }
                                }
                            }
                            
                            // Twitter/X
                            if ((href.includes('twitter.com/') || href.includes('x.com/')) && !data.twitter_username) {
                                const twitterMatch = href.match(/(?:twitter|x)\\.com\\/([a-zA-Z0-9_]+)/);
                                if (twitterMatch && twitterMatch[1]) {
                                    const username = twitterMatch[1].toLowerCase();
                                    const invalidTwitter = ['home', 'explore', 'search', 'settings', 'i', 'intent', 'share'];
                                    if (!invalidTwitter.includes(username) && username.length >= 2 && username.length <= 15) {
                                        data.twitter = 'https://twitter.com/' + username;
                                        data.twitter_username = username;
                                    }
                                }
                            }
                            
                            // YouTube
                            if (href.includes('youtube.com/') && !data.youtube) {
                                const ytMatch = href.match(/youtube\\.com\\/(channel\\/[a-zA-Z0-9_-]+|@[a-zA-Z0-9_-]+|c\\/[a-zA-Z0-9_-]+|user\\/[a-zA-Z0-9_-]+)/);
                                if (ytMatch) {
                                    data.youtube = 'https://youtube.com/' + ytMatch[1];
                                    data.youtube_channel = ytMatch[1];
                                }
                            }
                            
                            // LinkedIn
                            if (href.includes('linkedin.com/') && !data.linkedin) {
                                const liMatch = href.match(/linkedin\\.com\\/(company|in)\\/([a-zA-Z0-9_-]+)/);
                                if (liMatch) {
                                    data.linkedin = 'https://linkedin.com/' + liMatch[1] + '/' + liMatch[2];
                                    data.linkedin_profile = liMatch[2];
                                }
                            }
                            
                            // External websites (through Facebook redirect)
                            if (href.includes('l.facebook.com/l.php')) {
                                try {
                                    const url = new URL(href);
                                    const extUrl = url.searchParams.get('u');
                                    if (extUrl) {
                                        const decoded = decodeURIComponent(extUrl);
                                        let isValid = true;
                                        for (const domain of invalidDomains) {
                                            if (decoded.toLowerCase().includes(domain)) {
                                                isValid = false;
                                                break;
                                            }
                                        }
                                        if (decoded.includes('youtube.com') || decoded.includes('linkedin.com') || 
                                            decoded.includes('twitter.com') || decoded.includes('instagram.com')) {
                                            isValid = false;
                                        }
                                        if (isValid && decoded.includes('.')) {
                                            data.websites.push(decoded.split('?')[0]);
                                        }
                                    }
                                } catch(e) {}
                            }
                        }
                        
                        // ===== WEBSITE EXTRACTION FROM TEXT =====
                        const urlPattern = /(?:Website|Site|Web|Visit)[:\\s]*((?:https?:\\/\\/)?(?:www\\.)?[a-zA-Z0-9-]+\\.[a-zA-Z]{2,}(?:\\/[^\\s]*)?)/gi;
                        let urlMatch;
                        while ((urlMatch = urlPattern.exec(pageText)) !== null) {
                            const websiteUrl = urlMatch[1];
                            if (websiteUrl) {
                                let isValid = true;
                                for (const invalidDomain of invalidDomains) {
                                    if (websiteUrl.toLowerCase().includes(invalidDomain)) {
                                        isValid = false;
                                        break;
                                    }
                                }
                                if (isValid && websiteUrl.length > 5) {
                                    const fullUrl = websiteUrl.startsWith('http') ? websiteUrl : 'https://' + websiteUrl;
                                    data.websites.push(fullUrl.split('?')[0]);
                                }
                            }
                        }
                        
                        // ===== IMPRESSUM (for European pages) =====
                        const impressumMatch = pageText.match(/(?:Impressum)[:\\s]*([^\\n]{10,300})/i);
                        if (impressumMatch) data.impressum = impressumMatch[1].trim();
                        
                        // Clean up
                        data.phones = [...new Set(data.phones)].slice(0, 5);
                        data.emails = [...new Set(data.emails)].slice(0, 5);
                        data.websites = [...new Set(data.websites)].slice(0, 5);
                        
                        return data;
                    }''', INVALID_INSTAGRAM_USERNAMES)
                    
                    # Merge extracted data (only if not already filled)
                    if page_data:
                        # Arrays - extend
                        details["emails"].extend(page_data.get("emails", []))
                        details["phones"].extend(page_data.get("phones", []))
                        details["websites"].extend(page_data.get("websites", []))
                        
                        # Strings - only fill if empty
                        string_fields = [
                            "whatsapp", "instagram", "instagram_username", 
                            "twitter", "twitter_username", "youtube", "youtube_channel",
                            "linkedin", "linkedin_profile", "address", "city", "state",
                            "country", "pincode", "hours", "price_range", "founded",
                            "products", "services", "mission", "vision", "registration",
                            "gst", "pan", "impressum", "about_text", "category"
                        ]
                        
                        for field in string_fields:
                            if page_data.get(field) and not details.get(field):
                                details[field] = page_data[field]
                        
                        # Merge additional_info
                        if page_data.get("additional_info"):
                            for key, value in page_data["additional_info"].items():
                                if key not in details["additional_info"]:
                                    details["additional_info"][key] = value
                    
                    # BeautifulSoup extraction as backup
                    about_html = await page.content()
                    about_soup = BeautifulSoup(about_html, "html.parser")
                    about_text = about_soup.get_text(separator=" ", strip=True)
                    
                    contact_info = self.extract_contact_info(about_text)
                    details["emails"].extend(contact_info["emails"])
                    details["phones"].extend(contact_info["phones"])
                    details["websites"].extend(contact_info["websites"])
                    
                    # Look for social media links in HTML
                    for link in about_soup.find_all('a', href=True):
                        href = link.get('href', '')
                        
                        if 'instagram.com/' in href and not details["instagram_username"]:
                            username = self.extract_instagram_username(href)
                            if username:
                                details["instagram"] = f"https://instagram.com/{username}"
                                details["instagram_username"] = username
                        
                        if ('twitter.com/' in href or 'x.com/' in href) and not details["twitter_username"]:
                            username = self.extract_twitter_username(href)
                            if username:
                                details["twitter"] = f"https://twitter.com/{username}"
                                details["twitter_username"] = username
                        
                        if 'youtube.com/' in href and not details["youtube"]:
                            yt_match = re.search(r'youtube\.com/(channel/[a-zA-Z0-9_-]+|@[a-zA-Z0-9_-]+|c/[a-zA-Z0-9_-]+|user/[a-zA-Z0-9_-]+)', href)
                            if yt_match:
                                details["youtube"] = f"https://youtube.com/{yt_match.group(1)}"
                                details["youtube_channel"] = yt_match.group(1)
                        
                        if 'linkedin.com/' in href and not details["linkedin"]:
                            li_match = re.search(r'linkedin\.com/(company|in)/([a-zA-Z0-9_-]+)', href)
                            if li_match:
                                details["linkedin"] = f"https://linkedin.com/{li_match.group(1)}/{li_match.group(2)}"
                                details["linkedin_profile"] = li_match.group(2)
                    
                except Exception as e:
                    # Per-item resilience: one About-page variant failing is
                    # normal (404s, layout). Log and continue to the next.
                    logger.debug("Error on About page %s: %s", about_url, str(e)[:80])
                    print(f"    Error on {about_url}: {str(e)[:50]}", file=sys.stderr)
                    continue
            
            # ===== FINAL CLEANUP AND DEDUPLICATION =====
            details["emails"] = list(set([e.lower() for e in details["emails"] if is_valid_email(e)]))[:5]
            _norm_phones = (clean_phone(p) for p in details["phones"])
            details["phones"] = list({p for p in _norm_phones if p})[:5]
            valid_websites = [w for w in details["websites"] if is_valid_website(w)]
            details["websites"] = list(set(valid_websites))[:5]

            # WhatsApp was captured raw and never validated, so junk (wa.me
            # slugs, page IDs) leaked. Run it through the same clean_phone path.
            if details["whatsapp"]:
                details["whatsapp"] = clean_whatsapp(details["whatsapp"]) or ""

            # Re-validate 'founded' year with the dynamic ceiling (current year
            # + 1). Drops absurd future years that slipped through.
            if details["founded"] and not is_valid_founded_year(details["founded"]):
                details["founded"] = ""

            # Final validation of Instagram username
            if details["instagram_username"] and not is_valid_instagram_username(details["instagram_username"]):
                details["instagram"] = ""
                details["instagram_username"] = ""
            
            # Final validation of Twitter username
            if details["twitter_username"] and not is_valid_twitter_username(details["twitter_username"]):
                details["twitter"] = ""
                details["twitter_username"] = ""
            
        except Exception as e:
            details["error"] = str(e)[:200]
        
        return details

    async def search_and_get_advertiser_page(self, page, advertiser_name):
        """Search for advertiser on Facebook to find their page"""
        try:
            clean_name = re.sub(r'[^\w\s]', '', advertiser_name)
            search_query = clean_name.replace(' ', '%20')
            search_url = f"https://www.facebook.com/search/pages/?q={search_query}"
            
            await page.goto(search_url, wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_timeout(random.randint(2000, 3000))
            
            page_url = await page.evaluate('''() => {
                const links = document.querySelectorAll('a[href*="facebook.com/"]');
                for (const link of links) {
                    const href = link.href || '';
                    
                    if (href.includes('/search') || 
                        href.includes('/ads/library') ||
                        href.includes('/help') ||
                        href.includes('/policies') ||
                        href.includes('l.facebook.com') ||
                        href === 'https://www.facebook.com/' ||
                        href === 'https://www.facebook.com') {
                        continue;
                    }
                    
                    const pageMatch = href.match(/facebook\\.com\\/([a-zA-Z0-9._-]+)\\/?/);
                    if (pageMatch && pageMatch[1] && 
                        !['ads', 'help', 'policies', 'privacy', 'search', 'watch', 'groups', 
                          'events', 'marketplace', 'login', 'r.php', 'profile.php'].includes(pageMatch[1])) {
                        return href.split('?')[0];
                    }
                }
                return '';
            }''')
            
            return page_url
        except Exception as e:
            # Per-item resilience preserved (return "" so the caller skips this
            # advertiser) but the failure is now visible instead of silent.
            logger.warning("Page search failed for %r: %s", advertiser_name, str(e)[:120])
            return ""

    async def scrape_ads(self, query, country="IN", active_status="active",
                         ad_type="all", media_type="all", max_scrolls=3, 
                         scrape_advertiser_details=True, max_ads_to_detail=10,
                         filter_by_keywords=True, min_keyword_matches=1,
                         custom_keywords=None):
        """
        Main scraping method with keyword filtering and deduplication.
        
        Parameters:
        - query: Search query string
        - country: Country code (IN, US, GB, etc.)
        - active_status: "active" or "all"
        - ad_type: "all", "image", "video", etc.
        - media_type: "all", "image", "video", etc.
        - max_scrolls: Number of scroll iterations (more = more ads)
        - scrape_advertiser_details: Whether to visit advertiser pages
        - max_ads_to_detail: Maximum advertiser pages to visit
        - filter_by_keywords: Enable/disable keyword filtering
        - min_keyword_matches: Minimum score to include an ad
        - custom_keywords: Override auto-detected keywords
        """
        
        # Reset deduplication tracking for new scrape session
        self.reset_deduplication()
        
        # Build keyword list
        if filter_by_keywords:
            keywords, matched_niche = build_keyword_list(query, custom_keywords)
            print(f"\n🎯 Keyword Filtering: ENABLED", file=sys.stderr)
            if matched_niche:
                print(f"   Detected niche: {matched_niche}", file=sys.stderr)
            print(f"   Total keywords: {len(keywords)}", file=sys.stderr)
            print(f"   Min score required: {min_keyword_matches}", file=sys.stderr)
            print(f"   Sample keywords: {', '.join(keywords[:10])}...", file=sys.stderr)
        else:
            keywords = None
            print(f"\n⚠️  Keyword Filtering: DISABLED - returning all ads", file=sys.stderr)
        
        async with async_playwright() as pw:
            browser, context = await self.init_browser(pw)
            try:
                page = await context.new_page()

                # Build URL
                q = query.replace(" ", "%20")
                url = (
                    f"{self.base_url}?"
                    f"active_status={active_status}"
                    f"&ad_type={ad_type}"
                    f"&country={country}"
                    f"&is_targeted_country=false"
                    f"&media_type={media_type}"
                    f"&q={q}"
                    f"&search_type=keyword_unordered"
                )

                print(f"\n🔍 Navigating to Facebook Ads Library...", file=sys.stderr)
                print(f"   Query: {query}", file=sys.stderr)
                print(f"   Country: {country}", file=sys.stderr)
                print(f"   URL: {url[:100]}...", file=sys.stderr)
                
                # Navigate + detect ads with exponential backoff on
                # navigation errors / blocked-or-empty pages. We retry the
                # whole navigate+detect cycle a few times, sleeping a jittered
                # backoff delay between attempts.
                ads_detected = False
                for attempt in range(self.max_query_retries):
                    try:
                        await page.goto(url, wait_until="networkidle", timeout=60000)
                    except Exception as nav_err:
                        # Navigation error — log, back off, and retry.
                        delay = backoff_delay(attempt)
                        logger.warning(
                            "Navigation error for query %r (attempt %d/%d): %s — backing off %.1fs",
                            query, attempt + 1, self.max_query_retries,
                            str(nav_err)[:120], delay,
                        )
                        await page.wait_for_timeout(int(delay * 1000))
                        continue

                    await page.wait_for_timeout(random.randint(5000, 7000))

                    # Wait for ads to load
                    print(f"   ⏳ Waiting for ads to load...", file=sys.stderr)
                    try:
                        await page.wait_for_selector('text=Library ID', timeout=15000)
                        print(f"   ✓ Ads detected on page", file=sys.stderr)
                        ads_detected = True
                        break
                    except Exception:
                        print(f"   ⚠️  Initial ads not detected, scrolling to load...", file=sys.stderr)
                        for _ in range(3):
                            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                            await page.wait_for_timeout(2000)

                        try:
                            await page.wait_for_selector('text=Library ID', timeout=10000)
                            print(f"   ✓ Ads loaded after scrolling", file=sys.stderr)
                            ads_detected = True
                            break
                        except Exception:
                            # Empty / blocked response — back off before retrying.
                            delay = backoff_delay(attempt)
                            logger.warning(
                                "No 'Library ID' detected for query %r (attempt %d/%d) "
                                "— likely blocked/empty; backing off %.1fs",
                                query, attempt + 1, self.max_query_retries, delay,
                            )
                            await page.wait_for_timeout(int(delay * 1000))
                            continue

                if ads_detected:
                    self.run_stats["library_id_detected"] = True
                else:
                    self.run_stats["failures"] += 1
                    logger.warning(
                        "Query %r exhausted %d retries with no ads detected; "
                        "continuing to extraction (may yield nothing).",
                        query, self.max_query_retries,
                    )

                # Scroll to load more ads
                print(f"\n📜 Scrolling to load more ads ({max_scrolls} scrolls)...", file=sys.stderr)
                prev_height = 0
                for i in range(max_scrolls):
                    current_height = await page.evaluate("document.body.scrollHeight")
                    
                    # Main scroll
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    await page.wait_for_timeout(random.randint(2500, 4000))
                    
                    # Small intermediate scrolls to trigger lazy loading
                    await page.evaluate("window.scrollBy(0, -500)")
                    await page.wait_for_timeout(500)
                    await page.evaluate("window.scrollBy(0, 600)")
                    await page.wait_for_timeout(500)
                    
                    new_height = await page.evaluate("document.body.scrollHeight")
                    
                    # Log progress every 10 scrolls
                    if (i + 1) % 10 == 0:
                        print(f"  Scroll {i+1}/{max_scrolls} completed", file=sys.stderr)
                    
                    prev_height = new_height

                await page.wait_for_timeout(2000)

                # Save debug HTML
                html = await page.content()
                try:
                    with open('facebook_page_debug.html', 'w', encoding='utf-8') as f:
                        f.write(html)
                    print("📄 Saved debug HTML to facebook_page_debug.html", file=sys.stderr)
                except:
                    pass
                
                lib_id_count = html.count('Library ID')
                print(f"   📊 Found approximately {lib_id_count} ads in HTML", file=sys.stderr)

                # Extract and filter ads
                print("\n🔍 Extracting and filtering ads...", file=sys.stderr)
                results = await self.extract_ads_with_deduplication(page, keywords, min_keyword_matches)
                print(f"\n✅ Found {len(results)} unique matching ads", file=sys.stderr)

                # Run-level monitoring.
                self.run_stats["queries_run"] += 1
                self.run_stats["advertisers_found"] += len(results)

                if not results:
                    # FAIL LOUD: if this query produced nothing AND the
                    # detection selector was never seen anywhere in the run, the
                    # scraper is almost certainly blocked rather than legitimately
                    # finding zero matches. Raise instead of silently returning
                    # an empty/"no ads" result that downstream treats as success.
                    if not self.run_stats["library_id_detected"]:
                        logger.error(self.run_summary())
                        raise ScraperBlockedError(
                            "Wholesale failure: 'Library ID' selector never detected "
                            "across the run and no ads extracted for query "
                            "{!r}. Scraper is likely blocked or the page layout "
                            "changed.".format(query)
                        )
                    logger.warning(
                        "Query %r returned no matching ads (selector was seen "
                        "elsewhere, so treating as a genuine empty result).", query,
                    )
                    return [{"error": "No matching ads found",
                            "suggestion": "Try different search terms or lower min_keyword_matches value"}]

                # Scrape advertiser details
                if scrape_advertiser_details and results:
                    ads_to_detail = min(len(results), max_ads_to_detail)
                    print(f"\n📱 Scraping advertiser details for {ads_to_detail} ads...\n", file=sys.stderr)
                    
                    for idx, ad in enumerate(results[:ads_to_detail]):
                        print(f"[{idx + 1}/{ads_to_detail}] {ad['advertiser'][:40]}...", file=sys.stderr)
                        
                        # Search for page if not found
                        if not ad.get('advertiser_page_url'):
                            print(f"    → Searching for page...", file=sys.stderr)
                            ad['advertiser_page_url'] = await self.search_and_get_advertiser_page(
                                page, ad['advertiser']
                            )
                        
                        if ad.get('advertiser_page_url'):
                            print(f"    → Visiting: {ad['advertiser_page_url'][:50]}...", file=sys.stderr)
                            details = await self.scrape_advertiser_page(
                                page, 
                                ad['advertiser_page_url'], 
                                ad['advertiser']
                            )
                            ad["advertiser_details"] = details
                            
                            # Log what was found
                            found_items = []
                            if details.get('emails'): found_items.append(f"{len(details['emails'])} emails")
                            if details.get('phones'): found_items.append(f"{len(details['phones'])} phones")
                            if details.get('websites'): found_items.append(f"{len(details['websites'])} websites")
                            if details.get('instagram_username'): found_items.append(f"IG: @{details['instagram_username']}")
                            if details.get('twitter_username'): found_items.append(f"X: @{details['twitter_username']}")
                            if details.get('youtube'): found_items.append("YouTube")
                            if details.get('linkedin'): found_items.append("LinkedIn")
                            if details.get('followers_count'): found_items.append(f"{details['followers_count']} followers")
                            
                            if found_items:
                                print(f"    ✓ Found: {', '.join(found_items)}", file=sys.stderr)
                                if details.get('emails') or details.get('phones'):
                                    self.run_stats["contacts_found"] += 1
                            else:
                                print(f"    ✗ No contact info found", file=sys.stderr)
                        else:
                            ad["advertiser_details"] = {
                                "page_visited": False,
                                "emails": [], "phones": [], "websites": []
                            }
                            print(f"    ✗ Could not find page", file=sys.stderr)
                        
                        # Random delay between requests
                        await page.wait_for_timeout(random.randint(1500, 2500))

                print(f"\n✅ Scraping complete!", file=sys.stderr)
                logger.info(self.run_summary())
                return results

            except ScraperBlockedError:
                # Wholesale failure — must propagate (fail loud), never swallow.
                logger.error(self.run_summary())
                raise
            except Exception as e:
                import traceback
                traceback.print_exc()
                self.run_stats["failures"] += 1
                logger.error("Scraping failed for query %r: %s", query, str(e)[:200])
                return [{"error": f"Scraping failed: {str(e)}"}]

            finally:
                await context.close()
                await browser.close()


# ============================================
# MAIN ENTRY POINT
# ============================================

async def main():
    """Main entry point for command line usage"""
    if len(sys.argv) < 2:
        print(json.dumps({"error": "Expected JSON params in argv"}))
        return

    try:
        params = json.loads(sys.argv[1])
    except Exception as e:
        print(json.dumps({"error": f"Invalid params JSON: {e}"}))
        return

    scraper = FacebookAdsLibraryScraper()
    try:
        ads = await scraper.scrape_ads(
            query=params.get("query", "yoga coach"),
            country=params.get("country", "IN"),
            active_status=params.get("active_status", "active"),
            ad_type=params.get("ad_type", "all"),
            media_type=params.get("media_type", "all"),
            max_scrolls=int(params.get("max_scrolls", 3)),
            scrape_advertiser_details=params.get("scrape_advertiser_details", True),
            max_ads_to_detail=int(params.get("max_ads_to_detail", 10)),
            filter_by_keywords=params.get("filter_by_keywords", True),
            min_keyword_matches=int(params.get("min_keyword_matches", 1)),
            custom_keywords=params.get("custom_keywords", None)
        )
        print(json.dumps(ads, ensure_ascii=False, indent=2))
    except Exception as e:
        print(json.dumps({"error": str(e)}))

if __name__ == "__main__":
    asyncio.run(main())