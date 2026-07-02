-- ============================================================================
-- Migration 0007: RAG knowledge base + demo meeting link
--
-- 1. kb_docs — the retrieval corpus for L6 auto-replies. Each row is a small,
--    self-contained fact/answer about Exly. A generated tsvector + GIN index
--    give real full-text retrieval (no external vector DB needed): draftReply
--    ranks chunks against the lead's inbound message and grounds Claude on the
--    top matches, so answers cite the actual KB instead of a static blurb.
--
-- 2. conversions.meeting_url — the Google Calendar / Meet link created when a
--    demo is booked (L7 calendar sync).
-- ============================================================================

CREATE TABLE IF NOT EXISTS kb_docs (
    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    title      TEXT NOT NULL,
    content    TEXT NOT NULL,
    tags       TEXT,
    tsv        tsvector GENERATED ALWAYS AS (
                 to_tsvector('english', coalesce(title,'') || ' ' || coalesce(content,''))
               ) STORED,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS kb_docs_tsv_idx ON kb_docs USING GIN (tsv);

ALTER TABLE conversions ADD COLUMN IF NOT EXISTS meeting_url TEXT;

-- Seed a starter KB (idempotent: only seed when empty).
INSERT INTO kb_docs (title, content, tags)
SELECT * FROM (VALUES
  ('What Exly is',
   'Exly is an all-in-one platform for Indian course creators, coaches and affiliates. It lets you host and sell online courses, run 1:1 and group coaching, take bookings, collect payments and manage your audience — replacing a stack of separate tools with one platform.',
   'overview'),
  ('Payments and payouts',
   'Exly supports Indian payments out of the box: UPI, cards and netbanking, with GST-compliant invoicing and automated payouts to your bank account. Pricing of your own courses is fully in your control.',
   'payments,india'),
  ('Courses and coaching',
   'You can sell recorded courses, live cohorts, and 1:1 or group coaching sessions. Exly handles scheduling, reminders, and access control so learners get what they paid for automatically.',
   'product,courses'),
  ('Website and store',
   'Exly gives every creator a branded website/store to showcase and sell offerings, with a checkout optimised for Indian buyers. No coding needed.',
   'product,website'),
  ('Marketing tools',
   'Built-in email and WhatsApp marketing let you nurture and convert your audience from the same place you host your courses, with analytics on what is working.',
   'marketing'),
  ('Who it is for',
   'Exly is built for creators and coaches in India — fitness coaches, NLP and life coaches, educators, finance and career mentors, and affiliates who promote such offers.',
   'icp'),
  ('Getting started and demo',
   'The fastest way to see if Exly fits is a short guided demo where we map your current setup to Exly and show the exact flow for your niche. Demos are quick and no-obligation.',
   'demo,onboarding'),
  ('Migrating from other tools',
   'If you already sell on another platform or juggle separate tools for payments, calendar and email, Exly can consolidate them; the team helps you move your existing offers over during onboarding.',
   'migration'),
  ('Support',
   'Creators get onboarding help and ongoing support so you are not setting things up alone. The goal is to get you selling quickly.',
   'support')
) AS seed(title, content, tags)
WHERE NOT EXISTS (SELECT 1 FROM kb_docs);
