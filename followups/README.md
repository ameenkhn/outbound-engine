# Follow-up Engine

**Status:** 🔧 Not built yet — scaffold for the upcoming layer.
**PRD:** L5 / M6 — see [`../PRD.md`](../PRD.md) (or `../../PRD.md`).
**Builds on:** dispatch/ (L4)

## What goes here
Multi-step cadences (D0/D3/D7) across channels. Stop-on-reply, stop-on-opt-out, max-touch caps.

## Interface (keep it clean — this is how the next layer attaches)
- **Reads:** lead status from data/
- **Writes:** next-touch schedule; status transitions → data/

> Empty by design. Delete this note and drop your code here when you build this layer.
