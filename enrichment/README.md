# Enrichment, Scoring & Prioritization

**Status:** 🔧 Not built yet — scaffold for the upcoming layer.
**PRD:** L2 / M3 — see [`../PRD.md`](../PRD.md) (or `../../PRD.md`).
**Builds on:** data/ (L0), sourcing/

## What goes here
Enrich profile metrics, compute 0-100 ICP score, detect reachable channels, build the priority queue the dispatcher works top-down.

## Interface (keep it clean — this is how the next layer attaches)
- **Reads:** raw leads from data/
- **Writes:** icp_score + priority_rank → data/

> Empty by design. Delete this note and drop your code here when you build this layer.
