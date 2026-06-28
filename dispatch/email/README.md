# Email Dispatch

**Status:** 🔧 Not built yet — scaffold for the upcoming layer.
**PRD:** L4 / M5 — see [`../PRD.md`](../PRD.md) (or `../../PRD.md`).
**Builds on:** personalization/ (L3)

## What goes here
Email channel adapter (ESP). Drips, scheduler, throttle, sending windows, SPF/DKIM/DMARC + warmup safeguards. NOTE: email was proven in a separate test; that code is NOT in this repo yet.

## Interface (keep it clean — this is how the next layer attaches)
- **Reads:** messages from personalization/
- **Writes:** delivery_status events → data/

> Empty by design. Delete this note and drop your code here when you build this layer.
