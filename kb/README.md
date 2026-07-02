# Knowledge Base (RAG)

**Status:** ✅ Live — RAG knowledge base is the `kb_docs` table (Postgres full-text search + `kb_search`), editable in-app at `/kb`, used by the L6 suggested-reply and auto-responder.
**PRD:** supports M7 — see [`../PRD.md`](../PRD.md) (or `../../PRD.md`).
**Builds on:** —

## What goes here
Exly product/pricing/affiliate/objection docs, chunked + embedded (pgvector) so reply auto-answers are grounded and never hallucinate pricing/terms.

## Interface (keep it clean — this is how the next layer attaches)
- **Reads:** source docs
- **Writes:** kb_chunks → data/ (queried by replies/)

> Empty by design. Delete this note and drop your code here when you build this layer.
