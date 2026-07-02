# Instagram Sourcing

**Status:** ✅ Live — works today with **zero credentials** via a compliant
public web-search default client (`PublicSearchInstagramClient`, DuckDuckGo
`site:instagram.com`). Public search results only — no login, no private/Graph
API, no scraping behind auth. Set `INSTAGRAM_API_BASE` / `INSTAGRAM_API_KEY` to
switch to a richer paid provider. `adapter.py` implements the `SourceAdapter` contract.
**PRD:** L1 / M1 — see [`../../PRD.md`](../../PRD.md).
**Builds on:** sourcing layer (`sourcing/base.py`).

## What this does
From an **approved** target spec it searches Instagram for ICP creator profiles by
keyword, fetches each profile, and yields loader-ready candidate dicts
(`platform='instagram'`, carrying `target_spec_id`). No DB writes here — the L0
loader resolves/dedupes the candidates, exactly like the YouTube and Meta adapters.

## Interface
- **Reads:** target spec (keywords/filters) from `targeting/`.
- **Writes:** nothing directly — yields candidate dicts → `data.loader.load_candidates(..., target_spec_id=spec.id)`.

## Provider seam
Instagram has no open discovery API (the official Graph API only reaches accounts
that authorized your app), so real sourcing goes through a third-party provider.
The adapter talks to a small `InstagramClient` so the provider is **swappable**:

- `HttpInstagramClient` — real impl over a configurable endpoint. Set
  `INSTAGRAM_API_BASE` (and `INSTAGRAM_API_KEY`) in `.env`. Expects:
  - `GET {base}/search?q=<kw>&cursor=<c>` → `{"users":[{"username":...}], "next_cursor":<str|null>}`
  - `GET {base}/profile?username=<u>` → a profile object
  If your provider's response shape differs, subclass and override the two methods.
- `FakeInstagramClient` — deterministic, offline, used by the tests.

## Field extraction
`profile_to_candidate` pulls: `handle` (username — the stable identity), `email`
(business email or one parsed from the bio), `phone` (business number; the loader's
`clean_phone` keeps only valid Indian mobiles), `follower_band`/`follower_count`,
`niche` (from category), plus headline/bio/category/verified into `attributes`.

## Resume / rate-limit
Per-run search budget + username dedupe across pages. On a provider 429 the spec is
marked partially-sourced and a resume cursor is stashed on `spec.attributes`
(`instagram_resume`); a later run continues instead of restarting. Partial progress
is always kept (no exception escapes).

## Tests
`tests/test_instagram_adapter.py` — fully offline with `FakeInstagramClient`.
