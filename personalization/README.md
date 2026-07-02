# Personalization Engine

**Status:** ✅ Built — L3 personalization: value-prop library + P4 anti-mail-merge guardrail + Claude Haiku channel-aware copy (email + WhatsApp).
**PRD:** L3 / M4 — see [`../PRD.md`](../PRD.md) (or `../../PRD.md`).
**Builds on:** enrichment/ (L2)

## What goes here
Haiku-class copy generation. Value-prop library keyed by segment (creator/affiliate) x angle (cost-saving/affiliate-fee/switch/ease). Guardrails: no invented pricing, mandatory opt-out. Consumes Loop B winners.

## Interface (keep it clean — this is how the next layer attaches)
- **Reads:** lead + segment + angle; Loop B winners from feedback/
- **Writes:** message body → dispatch/

> Empty by design. Delete this note and drop your code here when you build this layer.
