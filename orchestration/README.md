# Orchestration (always-on loop)

**Status:** 🔧 Not built yet — scaffold for the upcoming layer.
**PRD:** L8 / M9 — see [`../PRD.md`](../PRD.md) (or `../../PRD.md`).
**Builds on:** conversion/ (L7)

## What goes here
Durable controller (Temporal/n8n/Celery+Redis) running sourcing → score → personalize → dispatch → follow-up → replies on a schedule, unattended, with health checks + back-pressure. Surfaces only exceptions.

## Interface (keep it clean — this is how the next layer attaches)
- **Reads:** all layers
- **Writes:** schedules + health/exception alerts

> Empty by design. Delete this note and drop your code here when you build this layer.
