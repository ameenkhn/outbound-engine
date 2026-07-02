# Conversion / Demo Booking

**Status:** ✅ Built (in `web/`) — demo booking form on the lead page writes `conversions`, emits a `book` event, advances the lead to `demo_booked`, and syncs a Google Calendar event + Meet link.
**PRD:** L7 / M8 — see [`../PRD.md`](../PRD.md) (or `../../PRD.md`).
**Builds on:** replies/ (L6)

## What goes here
Qualify, offer booking link/calendar, confirm slot, structured handoff (with conversation summary) to the sales owner.

## Interface (keep it clean — this is how the next layer attaches)
- **Reads:** qualified leads from replies/
- **Writes:** conversions → data/; handoff → sales/CRM

> Empty by design. Delete this note and drop your code here when you build this layer.
