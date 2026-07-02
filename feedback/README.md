# Feedback Loops

**Status:** ✅ Built (in `web/`) — the `/insights` page computes reply-rate + conversion by niche/channel/source and surfaces heuristic suggested actions (which niche to prioritise, which channel wins, which ICP weight to bump).
**PRD:** L9 / M10 — see [`../PRD.md`](../PRD.md) (or `../../PRD.md`).
**Builds on:** orchestration/ (L8)

## What goes here
Loop A (targeting: bias sourcing + re-weight ICP toward converters) and Loop B (content: bandit/A-B on angle/hook/subject/CTA per segment). Both read outcome data from data/.

## Interface (keep it clean — this is how the next layer attaches)
- **Reads:** events + conversions from data/
- **Writes:** targeting params → targeting/; winning variants → personalization/

> Empty by design. Delete this note and drop your code here when you build this layer.
