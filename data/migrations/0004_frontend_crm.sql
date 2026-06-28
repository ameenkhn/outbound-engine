-- ============================================================================
-- Exly Autonomous Outbound Engine — CRM Front End support
-- Migration 0004: app_jobs command queue + scoring_config + no-show + notes
--
-- Builds ON TOP of frozen 0001 (and 0002/0003). ADDITIVE ONLY — no existing
-- column is changed. These four additions back the L1/L2 front-end screens and
-- the no-show ops the CRM needs.
--
--   app_jobs       : a generic command queue the TS front end writes and the
--                    Python engine consumes (run targeting brain / re-score /
--                    kick a source adapter). Distinct from send_jobs (0002),
--                    which is the OUTBOUND-SEND outbox; app_jobs is for engine
--                    control actions. Postgres stays the contract between the
--                    Next.js app and the Python orchestrator.
--   scoring_config : single editable row holding the L2 WEIGHTS + target niches
--                    + competitor tools, seeded from the committed v1 defaults
--                    (enrichment/score.py). The L2 console edits this; the
--                    scorer should read it (see note at bottom).
--   conversions.*  : demo_scheduled_at + status give no-show detection a real
--                    backing field instead of guessing from free-text outcome.
--   leads.notes    : free-text operator notes surfaced on the Lead 360 view.
-- ============================================================================

BEGIN;

-- ---- app_jobs: front-end -> engine command queue ----------------------------
CREATE TYPE app_job_status_t AS ENUM ('pending', 'claimed', 'done', 'failed');

CREATE TABLE app_jobs (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kind         TEXT NOT NULL,                         -- rescore | mode_b | mode_a | source_run | approve_spec
    payload      JSONB NOT NULL DEFAULT '{}',           -- e.g. {"keywords":[...]} or {"spec_id":7,"platform":"youtube"}
    status       app_job_status_t NOT NULL DEFAULT 'pending',
    attempts     INTEGER NOT NULL DEFAULT 0,
    result       JSONB,                                 -- engine writes a summary back for the UI
    last_error   TEXT,
    requested_by TEXT,                                  -- operator id/email (from Supabase auth)
    claimed_at   TIMESTAMPTZ,
    run_after    TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Hot path: the consumer claims pending, due jobs oldest-first.
CREATE INDEX app_jobs_due_idx ON app_jobs (run_after) WHERE status = 'pending';
CREATE INDEX app_jobs_status_idx ON app_jobs (status);
CREATE INDEX app_jobs_kind_idx ON app_jobs (kind);

CREATE TRIGGER app_jobs_set_updated_at
    BEFORE UPDATE ON app_jobs
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();   -- reuses the 0001 trigger fn

-- ---- scoring_config: the editable L2 weights (single row id=1) ---------------
CREATE TABLE scoring_config (
    id               INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    weights          JSONB NOT NULL,
    target_niches    TEXT[] NOT NULL DEFAULT '{}',
    competitor_tools TEXT[] NOT NULL DEFAULT '{}',
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by       TEXT
);

-- Seed with the committed v1 defaults from enrichment/score.py (WEIGHTS) and
-- TARGET_ICP_NICHES, so the console shows the live formula on first load.
INSERT INTO scoring_config (id, weights, target_niches, competitor_tools)
VALUES (
    1,
    '{
      "signal_ad_text": 25, "signal_category": 10, "signal_social": 5, "signal_max": 40,
      "band_nano": 5, "band_micro": 20, "band_mid": 25, "band_macro": 15,
      "niche_match": 20, "segment_clear": 10, "segment_ambiguous": 5,
      "competitor_hint": 10, "verified_email": 5, "score_cap": 100
    }'::jsonb,
    ARRAY['fitness','yoga','wellness','nutrition','finance','trading','stock market',
          'education','coaching','edtech','language','music','dance','astrology',
          'spirituality','cooking','beauty','fashion','photography','design',
          'marketing','career','study abroad','mental health'],
    ARRAY[]::TEXT[]
)
ON CONFLICT (id) DO NOTHING;

-- ---- conversions: real no-show backing (additive) ---------------------------
ALTER TABLE conversions ADD COLUMN demo_scheduled_at TIMESTAMPTZ;   -- when the demo is supposed to happen
ALTER TABLE conversions ADD COLUMN status TEXT;                     -- booked|held|no_show|converted|canceled
CREATE INDEX conversions_status_idx ON conversions (status);
CREATE INDEX conversions_scheduled_idx ON conversions (demo_scheduled_at);

-- ---- leads.notes: operator free-text (additive) -----------------------------
ALTER TABLE leads ADD COLUMN notes TEXT;

COMMIT;

-- ============================================================================
-- BACKEND WIRING TODO (not done here — front-end build only):
--  1. enrichment/score.py + enrichment/enrich.py currently read hardcoded
--     WEIGHTS / TARGET_ICP_NICHES / COMPETITOR_TOOLS. To make the L2 weights
--     panel live, enrichment.run should load scoring_config (id=1) and pass the
--     weights/niches into score_lead instead of the module constants. Until
--     that lands, the console edits scoring_config (source of truth) but the
--     scorer still uses its constants — flagged in web/README.md.
--  2. A consumer must drain app_jobs and dispatch by `kind` (see
--     orchestration/app_jobs.py added in this branch; wire it into Celery beat).
-- ============================================================================
