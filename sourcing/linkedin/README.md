# LinkedIn Sourcing

**Status:** ✅ Live (sourcing-only) — works today with **zero credentials** via a
compliant public web-search default client (`PublicSearchLinkedInClient`,
DuckDuckGo `site:linkedin.com/in`). Public search results only — no login, no
private API, no automation against LinkedIn, and it never sends anything. Set
`LINKEDIN_API_BASE` / `LINKEDIN_API_KEY` for a richer paid provider. `adapter.py`
implements the `SourceAdapter` contract.
**PRD:** L1 / M1 — see [`../../PRD.md`](../../PRD.md).
**Builds on:** sourcing layer (`sourcing/base.py`).

## What this does
From an **approved** target spec it searches LinkedIn for ICP people/creators by
keyword, fetches each public profile, and yields loader-ready candidate dicts
(`platform='linkedin'`, carrying `target_spec_id`). **Sourcing / enrichment ONLY** —
this adapter never sends connection requests or DMs (outreach over LinkedIn stays
manual / human-in-the-loop). No DB writes here — the L0 loader resolves the candidates.

## Interface
- **Reads:** target spec from `targeting/`.
- **Writes:** nothing directly — yields candidate dicts → `data.loader.load_candidates(..., target_spec_id=spec.id)`.

## Provider seam
LinkedIn has no open people-search API, so real sourcing runs through a third-party
provider. The adapter talks to a small `LinkedInClient` so the provider is swappable:

- `HttpLinkedInClient` — real impl over a configurable endpoint. Set
  `LINKEDIN_API_BASE` (and `LINKEDIN_API_KEY`) in `.env`. Expects:
  - `GET {base}/search?q=<kw>&cursor=<c>` → `{"people":[{"public_id":...}], "next_cursor":<str|null>}`
  - `GET {base}/profile?public_id=<slug>` → a profile object
  If your provider's response shape differs, subclass and override the two methods.
- `FakeLinkedInClient` — deterministic, offline, used by the tests.

## Field extraction
`profile_to_candidate` pulls: `handle` (the `/in/<slug>` public id — stored as a
`linkedin` channel), `email` (public/contact email or one parsed from the summary),
`follower_band`/`follower_count` (followers or connections), `niche` (from industry),
plus headline/company/location/industry into `attributes`.

## Resume / rate-limit
Per-run search budget + slug dedupe across pages. On a provider 429 the spec is
marked partially-sourced and a resume cursor is stashed on `spec.attributes`
(`linkedin_resume`); a later run continues instead of restarting. Partial progress
is always kept (no exception escapes).

## Tests
`tests/test_linkedin_adapter.py` — fully offline with `FakeLinkedInClient`.
