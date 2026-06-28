-- ============================================================================
-- Exly Autonomous Outbound Engine — L1: Sourcing expansion + AI Targeting brain
-- Migration 0003: link leads back to the target_spec that sourced them
--
-- Builds ON TOP of the frozen 0001 schema (never edits it). ADDITIVE ONLY:
-- one nullable FK column + one index. No existing column is altered, so every
-- L0 reader/writer keeps working unchanged and existing leads stay valid with
-- target_spec_id = NULL.
--
-- WHY: L1 sources leads from approved target_specs (keyword expansion + the
-- deep-persona audience breakdown). Stamping each lead with the spec that
-- surfaced it lets Loop A attribute outcomes back to a spec ("which audience
-- definition actually converts?") without a join table. ON DELETE SET NULL so
-- retiring a spec never deletes the leads it produced — they just lose the
-- backref.
-- ============================================================================

BEGIN;

ALTER TABLE leads
    ADD COLUMN target_spec_id BIGINT REFERENCES target_specs(id) ON DELETE SET NULL;

-- Attribution queries ("leads sourced by spec N") and the SET NULL fan-out on
-- spec delete both want this index.
CREATE INDEX leads_target_spec_idx ON leads (target_spec_id);

COMMIT;
