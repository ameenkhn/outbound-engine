# Analytics & Ops

**Status:** ✅ Built (in `web/`) — funnel / send / reply-rate / conversion views are covered by the `web/` dashboard, Outreach log and Insights page; no separate service needed.
**PRD:** L10 / M11 — see [`../PRD.md`](../PRD.md) (or `../../PRD.md`).
**Builds on:** orchestration/ (L8)

## What goes here
Live funnel dashboard (sourced→contacted→replied→demo→converted) by channel + segment, reputation health, loop lift. Weekly auto-digest. Compliance ops (DPDP, opt-out enforcement, retention).

## Interface (keep it clean — this is how the next layer attaches)
- **Reads:** events from data/
- **Writes:** dashboards + alerts + digests

> Empty by design. Delete this note and drop your code here when you build this layer.
