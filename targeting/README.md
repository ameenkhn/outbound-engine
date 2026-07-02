# AI Targeting Brain

**Status:** ✅ Built — L1 targeting brain: Mode A (persona) + Mode B (keyword expansion) produce approved `target_specs` the sourcing adapters consume.
**PRD:** M1 — see [`../PRD.md`](../PRD.md) (or `../../PRD.md`).
**Builds on:** data/ (L0)

## What goes here
Sonnet-class brain. Mode A: deep persona Q&A → audience breakdown. Mode B: keyword expansion (auto). Outputs a structured target spec that configures the scrapers. Consumes Loop A feedback.

## Interface (keep it clean — this is how the next layer attaches)
- **Reads:** persona / seed examples / seed keywords; Loop A outcomes from feedback/
- **Writes:** target_specs → data/; drives sourcing/*

> Empty by design. Delete this note and drop your code here when you build this layer.
