-- ============================================================================
-- Exly Autonomous Outbound Engine — L0 Data Foundation
-- Migration 0001: initial schema  (THE FROZEN CONTRACT)
--
-- This migration is the single source of truth for the L0 Lead DB. Every later
-- layer (sourcing, enrichment, personalization, dispatch, follow-up, replies,
-- conversion, feedback, analytics) reads and writes these tables.
--
-- RULES:
--   * Do NOT edit a committed migration in place. To change the schema, add a
--     new NNNN_*.sql migration. The runner (data/migrate.py) applies them in
--     filename order and records each in schema_migrations.
--   * This is the coordination contract for parallel build agents. It is frozen
--     once merged; downstream layers depend on these names and types.
--
-- DECISIONS BAKED IN (plan-eng-review 2026-06-26):
--   3C  Lead identity is the output of a composite resolver (page/email/phone/
--       handle). leads.identity_key holds the resolved key and is UNIQUE; email
--       and phone are channels hanging off the lead, not the identity. Resolver
--       logic itself is T2 — this schema just provides the column + uniqueness.
--   6A  Suppression is scoped by reason. opt-out => identity-wide (channel_type
--       NULL, blocks the person on every channel). hardbounce/complaint =>
--       channel-specific (channel_type set). Enforced by a CHECK constraint.
--
-- ENTITY SKETCH:
--
--   target_specs ──drives──> (sourcing) ──> leads
--                                              │ 1
--                          ┌───────────────────┼───────────────────┐
--                          │ *                 │ *                 │ *
--                       channels             events           conversions
--                          │ 1
--                          │ *
--                       messages ──*:1── campaigns
--
--   suppression : keyed by identity_key (+ optional channel_type), independent
--                 of leads so an opt-out survives lead churn.
--   kb_chunks   : reserved for L6 RAG. The embedding column + pgvector are
--                 DEFERRED to L6 (see commented line) — L0 does not require the
--                 pgvector extension.
-- ============================================================================

BEGIN;

-- ---- Enumerated types -------------------------------------------------------
CREATE TYPE segment_t           AS ENUM ('creator', 'affiliate');
CREATE TYPE target_mode_t       AS ENUM ('deep', 'keyword');
CREATE TYPE channel_type_t      AS ENUM ('email', 'whatsapp', 'linkedin');

-- Lead lifecycle (M2 AC). A lead is always in exactly one of these states.
CREATE TYPE lead_status_t       AS ENUM (
    'new', 'queued', 'contacted', 'replied', 'in_conversation',
    'demo_booked', 'converted', 'dead', 'opted_out'
);

CREATE TYPE event_type_t        AS ENUM (
    'open', 'reply', 'click', 'bounce', 'complaint', 'book', 'optout'
);

CREATE TYPE suppression_reason_t AS ENUM ('optout', 'hardbounce', 'complaint', 'manual');

CREATE TYPE delivery_status_t   AS ENUM (
    'queued', 'sent', 'delivered', 'bounced', 'failed', 'skipped'
);

-- ---- target_specs (M1) ------------------------------------------------------
-- Output of the AI Targeting brain: what to source and how.
CREATE TABLE target_specs (
    id                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    mode              target_mode_t NOT NULL,
    persona_text      TEXT,
    seed_examples     TEXT[]  NOT NULL DEFAULT '{}',
    seed_keywords     TEXT[]  NOT NULL DEFAULT '{}',
    expanded_keywords TEXT[]  NOT NULL DEFAULT '{}',
    filters           JSONB   NOT NULL DEFAULT '{}',   -- niche, follower band, geo=IN, ...
    attributes        JSONB   NOT NULL DEFAULT '{}',
    approved          BOOLEAN NOT NULL DEFAULT FALSE,  -- deep mode needs human sign-off
    created_by_model  TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---- leads (M2) -------------------------------------------------------------
-- One row per real creator/affiliate. identity_key is the resolved composite
-- key (3C). Rich signals (ad_text, category, ...) ride in `attributes` so L3
-- personalization can "name a real signal" (P4).
CREATE TABLE leads (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    identity_key   TEXT NOT NULL,                       -- 3C resolved identity
    segment        segment_t,                           -- creator | affiliate
    niche          TEXT,
    platform       TEXT,                                -- meta | instagram | youtube | linkedin
    follower_band  TEXT,
    follower_count BIGINT,
    icp_score      SMALLINT CHECK (icp_score IS NULL OR icp_score BETWEEN 0 AND 100),
    priority_rank  INTEGER,
    status         lead_status_t NOT NULL DEFAULT 'new',
    geo            TEXT NOT NULL DEFAULT 'IN',
    source         TEXT,                                -- 'meta_ads', ...
    source_ref     TEXT,                                -- e.g. normalized facebook_page
    attributes     JSONB NOT NULL DEFAULT '{}',         -- ad_text/category/socials for P4
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT leads_identity_key_uniq UNIQUE (identity_key)
);
CREATE INDEX leads_status_idx        ON leads (status);
CREATE INDEX leads_priority_idx      ON leads (priority_rank) WHERE status IN ('new','queued');
CREATE INDEX leads_segment_idx       ON leads (segment);

-- ---- channels (M2/M3) -------------------------------------------------------
-- Contact points hanging off a lead. (type, handle) is globally unique so the
-- resolver can merge a channel onto whichever lead it belongs to.
CREATE TABLE channels (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    lead_id       BIGINT NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    type          channel_type_t NOT NULL,
    handle        TEXT NOT NULL,                        -- email / E.164 phone / profile url
    deliverable   BOOLEAN NOT NULL DEFAULT TRUE,
    opted_in      BOOLEAN NOT NULL DEFAULT FALSE,       -- WhatsApp gate (opt-in-led)
    opted_out     BOOLEAN NOT NULL DEFAULT FALSE,
    opt_in_source TEXT,                                 -- email_reply | link_click | reply_yes
    opt_in_ts     TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT channels_type_handle_uniq UNIQUE (type, handle)
);
CREATE INDEX channels_lead_idx ON channels (lead_id);
CREATE INDEX channels_optin_idx ON channels (type) WHERE opted_in AND NOT opted_out;

-- ---- campaigns (M4/M5) ------------------------------------------------------
CREATE TABLE campaigns (
    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name       TEXT NOT NULL,
    segment    segment_t,
    goal       TEXT,
    active     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---- messages (M5) ----------------------------------------------------------
-- One row per send. Carries the variant/angle so Loop B can learn what wins.
CREATE TABLE messages (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    lead_id         BIGINT NOT NULL REFERENCES leads(id)     ON DELETE CASCADE,
    channel_id      BIGINT NOT NULL REFERENCES channels(id)  ON DELETE CASCADE,
    campaign_id     BIGINT          REFERENCES campaigns(id) ON DELETE SET NULL,
    variant         TEXT,
    angle           TEXT,                                -- cost_saving | affiliate_fee | competitive_switch | ease
    subject         TEXT,
    body            TEXT NOT NULL,
    delivery_status delivery_status_t NOT NULL DEFAULT 'queued',
    sent_at         TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX messages_lead_idx     ON messages (lead_id);
CREATE INDEX messages_channel_idx  ON messages (channel_id);
CREATE INDEX messages_delivery_idx ON messages (delivery_status);

-- ---- events (M10) -----------------------------------------------------------
-- Auditable event log. The feed for both learning loops and the funnel dash.
CREATE TABLE events (
    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    lead_id    BIGINT NOT NULL REFERENCES leads(id)     ON DELETE CASCADE,
    channel_id BIGINT          REFERENCES channels(id)  ON DELETE SET NULL,
    message_id BIGINT          REFERENCES messages(id)  ON DELETE SET NULL,
    type       event_type_t NOT NULL,
    intent     TEXT,                                    -- interested|question|objection|not_now|unsubscribe
    sentiment  TEXT,
    meta       JSONB NOT NULL DEFAULT '{}',
    ts         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX events_lead_ts_idx ON events (lead_id, ts DESC);
CREATE INDEX events_type_idx    ON events (type);

-- ---- conversions (M8) -------------------------------------------------------
CREATE TABLE conversions (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    lead_id        BIGINT NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    demo_booked_at TIMESTAMPTZ,
    owner          TEXT,
    summary        TEXT,
    outcome        TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX conversions_lead_idx ON conversions (lead_id);

-- ---- suppression (P5 / 6A) --------------------------------------------------
-- Checked before EVERY send (re-checked at dispatch time, per 6A). Keyed by
-- identity_key so it survives lead churn and spans channels.
--   opt-out      => channel_type NULL  (identity-wide: block the person)
--   bounce/compl => channel_type SET   (channel-specific: block that channel)
--   manual       => either
CREATE TABLE suppression (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    identity_key TEXT NOT NULL,
    channel_type channel_type_t,                        -- NULL = identity-wide
    reason       suppression_reason_t NOT NULL,
    note         TEXT,
    ts           TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT suppression_scope_by_reason CHECK (
        (reason = 'optout'                        AND channel_type IS NULL)
     OR (reason IN ('hardbounce', 'complaint')    AND channel_type IS NOT NULL)
     OR (reason = 'manual')
    )
);
-- Prevent duplicate suppressions; one identity-wide row, one per channel.
CREATE UNIQUE INDEX suppression_identity_wide_uniq
    ON suppression (identity_key) WHERE channel_type IS NULL;
CREATE UNIQUE INDEX suppression_per_channel_uniq
    ON suppression (identity_key, channel_type) WHERE channel_type IS NOT NULL;
CREATE INDEX suppression_identity_idx ON suppression (identity_key);

-- ---- kb_chunks (M7, reserved) -----------------------------------------------
-- Reserved for L6 RAG. pgvector + the embedding column are DEFERRED to L6 so
-- L0 does not require the extension.
CREATE TABLE kb_chunks (
    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    topic      TEXT,
    text       TEXT NOT NULL,
    -- embedding VECTOR(1536),   -- DEFERRED to L6 (requires: CREATE EXTENSION vector;)
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---- updated_at trigger for leads ------------------------------------------
CREATE OR REPLACE FUNCTION set_updated_at() RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER leads_set_updated_at
    BEFORE UPDATE ON leads
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

COMMIT;
