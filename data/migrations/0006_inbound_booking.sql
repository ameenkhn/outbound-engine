-- ============================================================================
-- Migration 0006: inbound replies + demo booking
--
-- 1. outreach.direction — the send-log now also stores INBOUND messages (a lead
--    replying on WhatsApp/email), so the CRM shows a real two-way thread and the
--    reply-rate metric is driven by actual inbound events, not manual marking.
--       'out' = we sent it   |   'in' = the lead sent it
--
-- 2. conversions gains demo_scheduled_at + status so the L7 booking flow (and the
--    lead-360 UI, which already reads these) has real columns to write.
-- ============================================================================

ALTER TABLE outreach
    ADD COLUMN IF NOT EXISTS direction TEXT NOT NULL DEFAULT 'out';   -- 'out' | 'in'

CREATE INDEX IF NOT EXISTS outreach_direction_idx ON outreach (direction);

ALTER TABLE conversions
    ADD COLUMN IF NOT EXISTS demo_scheduled_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS status            TEXT DEFAULT 'booked';  -- booked|held|no_show|won|lost

-- outreach may have been created after RLS was enabled on the schema; keep it
-- consistent with the other tables (service-role bypasses RLS anyway).
ALTER TABLE outreach ENABLE ROW LEVEL SECURITY;
