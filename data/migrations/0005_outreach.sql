-- ============================================================================
-- Migration 0005: outreach send-log (CRM tracking of WhatsApp/email sends)
--
-- Every message the platform sends (from Compose or a campaign) is recorded here
-- so the CRM can show "who did we contact, on which channel, when, and did it
-- land". Independent of the deeper `messages`/`send_jobs` tables (which model the
-- durable outbox); this is the lightweight, human-facing activity log the UI reads.
-- ============================================================================
CREATE TABLE IF NOT EXISTS outreach (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    lead_id     BIGINT REFERENCES leads(id) ON DELETE CASCADE,
    channel     TEXT NOT NULL,              -- 'email' | 'whatsapp'
    to_handle   TEXT NOT NULL,              -- the email/phone we sent to
    subject     TEXT,
    body        TEXT NOT NULL,
    status      TEXT NOT NULL,              -- 'sent' | 'failed'
    provider_id TEXT,                       -- id/handle from AiSensy/Resend
    error       TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS outreach_lead_idx    ON outreach (lead_id);
CREATE INDEX IF NOT EXISTS outreach_created_idx ON outreach (created_at DESC);
CREATE INDEX IF NOT EXISTS outreach_channel_idx ON outreach (channel);
