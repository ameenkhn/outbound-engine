-- ============================================================================
-- Exly Autonomous Outbound Engine — Lane C: Orchestration (T4 / decision 1B)
-- Migration 0002: durable outbox queue + warmup rate counters
--
-- Builds ON TOP of the frozen 0001 schema (never edits it). Adds the two tables
-- the Celery+Redis orchestrator needs to schedule sends, retry on failure,
-- apply warmup back-pressure, and guarantee idempotent (no-double-send) sends
-- even across a worker crash.
--
-- WHY a Postgres-backed outbox at all (and not "just Celery")?
--   Redis/Celery is the *trigger* (when to look for work). Postgres is the
--   *source of truth* for what was sent. Putting the queue in the same DB as
--   the messages means claim->send->record can be reasoned about with row locks
--   and a UNIQUE key — a crash can never lose a job or double-send one.
--
-- DESIGN (crash-safe claim->send->record):
--   * idempotency_key is UNIQUE. enqueue() does ON CONFLICT DO NOTHING, so the
--     same logical send can be enqueued any number of times and only one row
--     exists. This single constraint is what prevents double-sends.
--   * claim_due() uses SELECT ... FOR UPDATE SKIP LOCKED to hand each pending,
--     due row to exactly one worker, flips it to 'claimed' and bumps attempts.
--   * Only a CONFIRMED send flips 'claimed' -> 'sent'. If a worker crashes
--     mid-send the row is left 'claimed'; after a visibility timeout it is
--     reclaimable (claim_due also picks up stale 'claimed' rows). Because the
--     real send is keyed on idempotency_key at the channel adapter too, a
--     reclaim retries safely.
--
-- DECISION 6A: suppression is re-checked at DISPATCH time inside the worker
--   (see orchestration/tasks.py), NOT at enqueue time. This migration only
--   stores the queue; the suppression table itself is frozen in 0001.
-- ============================================================================

BEGIN;

-- ---- send_jobs status enum --------------------------------------------------
-- pending  : enqueued, waiting for run_after to pass and a worker to claim it.
-- claimed  : a worker holds it and is attempting the send (in flight).
-- sent     : send confirmed by the channel adapter (terminal, success).
-- failed   : send failed after exhausting retries (terminal, failure).
-- skipped  : suppressed at dispatch (6A) or otherwise intentionally not sent.
CREATE TYPE send_job_status_t AS ENUM ('pending', 'claimed', 'sent', 'failed', 'skipped');

-- ---- send_jobs (the durable outbox / queue) ---------------------------------
-- One row per intended send. message_id / channel_id tie back to 0001. The
-- identity_key is denormalized onto the job so the 6A dispatch-time suppression
-- re-check is a single index lookup with no joins.
CREATE TABLE send_jobs (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    message_id      BIGINT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    channel_id      BIGINT NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
    identity_key    TEXT   NOT NULL,                       -- denormalized for the 6A re-check
    idempotency_key TEXT   NOT NULL,                       -- the no-double-send guard
    status          send_job_status_t NOT NULL DEFAULT 'pending',
    attempts        INTEGER NOT NULL DEFAULT 0,
    claimed_at      TIMESTAMPTZ,                           -- when the current claim started (visibility timeout)
    run_after       TIMESTAMPTZ NOT NULL DEFAULT now(),    -- earliest dispatch time (scheduling + backoff)
    last_error      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- The single constraint that makes sends idempotent. enqueue() relies on it
    -- via ON CONFLICT (idempotency_key) DO NOTHING.
    CONSTRAINT send_jobs_idempotency_key_uniq UNIQUE (idempotency_key)
);

-- Hot path: claim_due() scans pending, due jobs in run_after order.
CREATE INDEX send_jobs_due_idx
    ON send_jobs (run_after)
    WHERE status = 'pending';
-- Reclaim path: find stale 'claimed' jobs past the visibility timeout.
CREATE INDEX send_jobs_claimed_idx
    ON send_jobs (claimed_at)
    WHERE status = 'claimed';
CREATE INDEX send_jobs_message_idx ON send_jobs (message_id);
CREATE INDEX send_jobs_identity_idx ON send_jobs (identity_key);

-- ---- rate_counters (per-domain/day warmup caps) -----------------------------
-- Back-pressure for the warmup ramp. scope_key is an opaque bucket the caller
-- chooses, e.g. 'email:domain.com:2026-06-26'. check_and_increment() upserts
-- here and refuses to go past the cap. window_date lets a sweeper prune old
-- buckets; the cap itself is passed in by the caller (warmup schedule), not
-- stored here, so the ramp can change without a migration.
CREATE TABLE rate_counters (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    scope_key   TEXT NOT NULL,
    count       INTEGER NOT NULL DEFAULT 0,
    window_date DATE NOT NULL DEFAULT CURRENT_DATE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT rate_counters_scope_key_uniq UNIQUE (scope_key)
);
CREATE INDEX rate_counters_window_idx ON rate_counters (window_date);

COMMIT;
