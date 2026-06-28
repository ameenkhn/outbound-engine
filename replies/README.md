# Reply Handling & Two-Way

**Status:** 🔧 Not built yet — scaffold for the upcoming layer.
**PRD:** L6 / M7 — see [`../PRD.md`](../PRD.md) (or `../../PRD.md`).
**Builds on:** followups/ (L5)

## What goes here
The "is it automated?" core. Inbound capture (email+WhatsApp), intent classification (Haiku), KB auto-answer via RAG, escalation to human on low confidence / high value.

## Interface (keep it clean — this is how the next layer attaches)
- **Reads:** inbound messages; kb/ chunks
- **Writes:** intent/sentiment events → data/; escalations → human

> Empty by design. Delete this note and drop your code here when you build this layer.
