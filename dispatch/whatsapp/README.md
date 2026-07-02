# WhatsApp Dispatch

**Status:** ✅ Built — L4 WhatsApp dispatch: AiSensy approved-template send, registered in `dispatch.registry` under `whatsapp` (opt-in-led).
**PRD:** L4 / M5 — see [`../PRD.md`](../PRD.md) (or `../../PRD.md`).
**Builds on:** personalization/ (L3)

## What goes here
WhatsApp Business Cloud API via a BSP (Gupshup/AiSensy/Wati). TEMPLATE-based, opt-in-led only — cold blasting risks number bans. Monitor block rate.

## Interface (keep it clean — this is how the next layer attaches)
- **Reads:** approved templates + opted-in leads
- **Writes:** delivery_status events → data/

> Empty by design. Delete this note and drop your code here when you build this layer.
