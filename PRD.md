# PRD — Exly Autonomous Outbound Engine
## 1. TL;DR
Build an always-on agent that **continuously sources ICP creators and affiliates across India, reaches out over email + WhatsApp (+ LinkedIn later), follows up, answers their questions, and books them into a demo** — with two feedback loops that make it sharper over time (who to target, and what to say).

We already have: a Meta Ad Library scraper and tested email reach-outs. We are missing: WhatsApp as a channel, sourcing beyond Meta, a two-way reply/answer layer, demo booking, the orchestration loop, and the learning loops. This PRD sequences all of it as **layers built one on top of another** — each layer is shippable and unlocks the next.

**Design principle — built open-ended from day one.** Per the founding requirement ("keep it open-ended from the start"), the architecture is **modular and extensible**: new sourcing platforms, channels, segments, value-prop angles, and models can be plugged in without re-architecting. Every layer in §11 exposes clean interfaces so the next layer — and future ones we haven't scoped — attach without rework.

---

## Build status — 2026-07-02 (feature-complete for internal-team use; details in HANDOFF.md / README.md)
Verified on live Supabase Postgres (Mumbai, migrations `0001`–`0007`, 113 leads) + build-clean CRM front end. **306 Python tests green + web build green.**
- ✅ **L0** schema/loader/resolver · **L1** targeting brain (Mode A + B) + five live sources (Meta, YouTube, **Instagram, LinkedIn**, web-search) · **L2** ICP scoring · **L3** personalization + P4 guardrail + Haiku copy
- ✅ **L4** dispatch — **WhatsApp (AiSensy) + email (Resend)** live + Smartlead · **L5** follow-ups · **L6** inbound webhooks + **RAG** suggested-reply/auto-responder (`kb_docs`, `/kb`)
- ✅ **L7** demo booking + **Google Calendar** sync · **L8** durable queue + **always-on pipeline loop** · **L9** insights/feedback · **L10** analytics via CRM
- ✅ **Front-end CRM** (`web/`): sourcing, scoring, dashboard, pipeline, lead-360, compose, outreach, insights, `/kb`
- 🚧 **To go live (setup, not code):** provider env vars + accounts, register inbound webhooks, Railway paid plan, DPDP legal sign-off. **Productization layer (not built by design):** multi-tenant auth + billing.

---

## 2. Background & context
- The Gushwork model proved that **outbound mechanics (scrape → personalize → multi-channel send → follow up) can be pointed at any audience** — they pointed it at AE candidates. Exly's version points it at **creators and affiliates**, our actual revenue audience.
- **Current state at Exly:**
  - A bot that scrapes the **Meta Ad Library** by keyword, cleans, and exports CSV.
  - **Email reach-outs tested** (one channel proven).
  - Open: add **WhatsApp**, expand **sourcing beyond Meta** (Instagram, YouTube, LinkedIn), and make the whole thing **autonomous end-to-end** (the core open question: "does the agent keep looking for leads, messaging, following up, and answering queries until they get on a demo?").

---

## 3. Problem statement
Creator/affiliate acquisition today is manual and capacity-bound: someone has to find creators, write messages, send them, chase replies, and answer the same product/affiliate questions repeatedly. This caps volume and lets warm leads go cold. We need a system that does this at hundreds of touches/day, never drops a follow-up, answers instantly, and gets better as it runs.

---

## 4. Goal & objectives
**Purpose:** *"Create an automated engine that does outbound to ICP creators (and for affiliates) across India — WhatsApp reach-outs, email reach-out drips, laying out why they need Exly, the USP, why we're better than competition, and what's in it for them (cost saving, affiliate fee). The whole thing automated. Goal is to convert creators and affiliates."*

**Objectives**
1. Source and qualify ICP creators/affiliates continuously, multi-platform.
2. Run automated, personalized, multi-channel outbound (email + WhatsApp) with drips and follow-ups.
3. Handle inbound replies and questions automatically up to the point of a booked demo.
4. Learn over time on two axes — **targeting** (who) and **content** (what).
5. Stay compliant (WhatsApp policy, India DPDP Act, email deliverability) and protect sender reputation.

---

## 5. Success metrics (KPIs)
**North-star:** Demos booked per week from the engine (and downstream: creators/affiliates activated).

| Funnel stage | Metric | Why |
|---|---|---|
| Sourcing | New ICP leads added/week; % passing ICP filter | Top of funnel health |
| Outreach | Messages sent/day; deliverability %, bounce %, WhatsApp block rate | Volume + reputation |
| Engagement | Open %, reply %, positive-reply % | Message effectiveness |
| Conversation | % replies auto-resolved vs escalated; avg replies-to-demo | Automation depth |
| Conversion | **Demos booked**, demo→activation %, CAC per channel | Business outcome |
| Learning | Lift in reply/conversion % over time per segment | Are the loops working |

Set baselines from the existing email test; target improvement per release.

---

## 6. Scope
**In scope (v1–v3):** sourcing (Meta done, IG, YouTube, LinkedIn), enrichment/scoring, segment-aware personalization via a low-cost model, email + WhatsApp dispatch, follow-ups, reply handling + auto-answer, demo booking, orchestration loop, both feedback loops, analytics, compliance.

**Out of scope (for now):** paid ads, the demo call itself / closing (human or product-led), full CRM replacement, building creators' funnels, languages beyond English/Hinglish (phase later), phone/voice outreach.

---

## 7. Target users / ICP
Two segments, tagged separately because messaging and economics differ:
1. **Creators** — course/coaching/digital-product creators in India who could host on Exly. *Value props:* cost saving vs. competitors, all-in-one platform, payouts, ease of launch.
2. **Affiliates** — creators/marketers who promote Exly creators' offers for commission. *Value props:* affiliate fee/earning potential, ready catalog, tracking, payouts.

**ICP attributes captured per lead:** platform(s), niche/category, follower/subscriber band, engagement signals, geography (India), whether they already sell digital products, competitor-tool usage (if detectable from ads), and contactability (email / WhatsApp number / LinkedIn).

---

## 8. The autonomous loop (overview)
```
        ┌──────────────── TARGETING LOOP (who) ───────────────┐
        ▼                                                      │
[AI Targeting] → [1 Sourcing] → [2 Lead DB] → [3 Enrich+Score+Prioritize]
 (deep ICP based + keyword modes)                                │
                                                       ▼
                         [4 Personalization Engine  (Haiku-class)]
                                   │      ▲
                                   ▼      └──── CONTENT LOOP (what) ───┐
                         [5 Multi-channel Dispatch: Email/WhatsApp/LinkedIn]
                                   │                                    │
                                   ▼                                    │
                         [6 Follow-up + Reply Handling] ────────────────┘
                                   │
                                   ▼
                         [7 Conversion: book demo → sales handoff]

   [8 Orchestration] wraps 1–7 as an always-on loop.
   Feeder into 4 = existing data + new signals + inbound replies.
```
**Loop closure:** the loop is **open in v1** (linear scrape-and-send; humans handle replies), becomes a **closed continuous loop in v2** (orchestration, L8), and a **closed self-improving loop in v3** (feedback loops, L9). It closes *up to the booked demo* — human escalation (M7) and the demo/close itself stay human-in-the-loop by design.

---

## 9. Functional requirements (by module)
Status: built · to build · new in this scope.

### M1 — AI Targeting + Lead Sourcing (new)
An **AI Targeting brain** (Sonnet-class) that turns intent into precise source queries, then drives the platform scrapers. Two modes:

**Mode A — Detailed (deep, interactive).** Inputs (degrades gracefully): a natural-language **persona**, and/or **seed examples** (creator handles, competitor pages/URLs for lookalikes), and/or an **uploaded list**. The AI **asks clarifying questions**, produces a **structured audience breakdown** (segments → sub-niches → signals), and converts it into **keywords + platform filters + attributes**. Human signs off before sourcing.

**Mode B — Keyword expansion (auto).** Input: **seed keywords**. The AI **broadens/expands** them into a richer deduped query set. Runs automatically — no approval gate.

**Output of both → scrapers:** keywords + filters (niche, follower band, geo = India) + attributes.

**Scrapers:** Meta Ad Library (built); Instagram, YouTube (to build); LinkedIn (new). Contact extraction (email, WhatsApp number, profile URL); dedup; write to Lead DB.

**Loop A tie-in:** the Targeting brain consumes targeting feedback (which keywords/segments convert) and auto-refines future queries.

- **AC:** Given a deep persona or seed keywords, the system produces an approved/expanded **target spec** and returns deduped, ICP-filtered leads — each with ≥1 reachable channel — written to the DB with source + timestamp.

### M2 — Lead DB / CRM spine
Central store; per-lead lifecycle status; segment + niche + channel tags; full activity history.
- **AC:** Every lead has a status in `{new, queued, contacted, replied, in_conversation, demo_booked, converted, dead, opted_out}` and an auditable event log.

### M3 — Enrichment, Scoring & Prioritization
Enrich profile metrics; compute **ICP score**; detect channels; build a **priority queue**.
- **AC:** Each lead has a 0–100 ICP score and a priority rank; the dispatcher always pulls the highest-priority eligible leads.

### M4 — Personalization Engine (the "feeder" + low-cost model)
Generate copy per lead with a **Haiku-class model**. Feeder inputs: existing lead data + new signals + inbound replies. **Value-prop library** keyed by segment (creator vs affiliate) and angle (cost-saving / affiliate-fee / competitive-switch / ease). Segment-aware tone; Exly USP + competitive differentiation.
- **AC:** For a given lead+segment+angle, returns a personalized message that names a relevant value prop and passes a quality/guardrail check before send.

### M5 — Multi-channel Dispatch
**Email** (built, drips); **WhatsApp** (new, via Business API/BSP); **LinkedIn** (new, sourcing first, semi-automated DMs later). Send scheduler, per-channel rate limiting, sending-window controls, reputation safeguards.
- **AC:** Messages dispatch on schedule within per-channel caps; every send logged with delivery status; throttles prevent reputation/ban triggers.

### M6 — Follow-up Engine
Multi-step cadences (e.g., D0 / D3 / D7) across channels; stop-on-reply; stop-on-opt-out; max-touch caps.
- **AC:** A lead advances through the cadence automatically and is removed the instant they reply or opt out.

### M7 — Reply Handling & Two-Way Conversation (new) — the "is it automated?" core
Inbound capture (email + WhatsApp); **intent classification** (interested / question / objection / not-now / unsubscribe); **auto-answer** from an Exly knowledge base (RAG over product, pricing, affiliate terms, objections); **escalation** to a human when confidence is low or intent is high-value.
- **AC:** ≥X% of common questions answered without a human; objections handled; anything unclassifiable is escalated with full context; conversation always pushes toward a demo.

### M8 — Conversion / Demo Booking (new)
Qualify, then offer a booking link / calendar; confirm; hand off to sales/CRM.
- **AC:** A positive lead is offered booking, the slot is captured, and a structured handoff (with conversation summary) reaches the sales owner.

### M9 — Orchestration (always-on loop) (new)
A continuous controller that runs sourcing → scoring → personalize → dispatch → follow-up → reply-handling on a schedule, unattended, with health checks and back-pressure.
- **AC:** With zero manual triggers for 24h, the system sources, contacts, follows up, and replies to leads, and surfaces only escalations/exceptions to humans.

### M10 — Feedback Loops (learning layer) (new) — see §10.

### M11 — Compliance, Deliverability & Analytics — see §13 and §15.

---

## 10. The two feedback loops (detailed)
Both read from the **same outcome data** in the Lead DB. Scope = **both**.

### Loop A — Targeting feedback ("who gets contacted")
- **Signal in:** outcomes by segment — which niches, follower tiers, platforms, and creator-vs-affiliate cohorts reply, book demos, convert.
- **Writes back to:** (1) M1 Sourcing — bias scrapers toward converting keywords/niches/platforms; (2) M3 Scoring — re-weight the ICP model so lookalikes of converters rank higher; (3) M3 Priority queue — work high-propensity leads first.
- **Mechanism:** periodic aggregation job → update scoring weights + sourcing parameters. Start rules-based; graduate to a propensity model once data volume allows.

### Loop B — Content feedback ("what they're told")
- **Signal in:** per-message engagement — opens, reply rate, reply intent/sentiment, demo-booked — tagged by segment + message variant/angle.
- **Writes back to:** M4 Personalization Engine — which value-prop angle, hook, subject line, and CTA win per segment; winning patterns injected into the generation prompt.
- **Mechanism:** lightweight A/B or multi-armed-bandit per segment; track variant performance; feed winners into the next generation cycle.

**Shared spine:** Loop A changes *who*; Loop B changes *what*. Together they turn a scrape-and-send pipeline into a self-improving SDR agent.

---

## 11. Layered build plan (one on top of another)
Each layer is independently shippable and a prerequisite for the next. Releases bundle layers.

| Layer | Name | Builds on | What it delivers | Exit / done criteria |
|---|---|---|---|---|
| **L0** | **Data foundation** | — | Lead DB schema, segment taxonomy, event log | Schema live; can store leads, channels, events, statuses |
| **L1** | **AI Targeting + Sourcing** | L0 | Targeting brain (deep + keyword modes) → multi-platform scrapers (Meta done → IG, YouTube, LinkedIn), ICP filter, dedup | Target spec generated; DB auto-populates with deduped ICP leads from ≥2 platforms |
| **L2** | **Enrichment + scoring** | L1 | Enrichment, ICP score, channel detection, priority queue | Every lead scored + ranked; channels detected |
| **L3** | **Personalization engine** | L2 | Haiku-class generation, value-prop library, feeder | Segment-aware message generated + guardrail-passed per lead |
| **L4** | **Dispatch (multi-channel)** | L3 | Email + WhatsApp API + scheduler/throttle/compliance gate | Messages send on both channels within caps, fully logged |
| **L5** | **Follow-up engine** | L4 | Cadence sequences, stop rules, max-touch | Leads auto-progress and auto-exit on reply/opt-out |
| **L6** | **Reply handling / two-way** | L5 | Inbound capture, intent classification, KB auto-answer, escalation | Common Qs auto-answered; objections handled; low-confidence escalated |
| **L7** | **Conversion / demo booking** | L6 | Booking link, qualification, sales handoff | Positive leads booked + handed off with summary |
| **L8** | **Orchestration** | L7 | Always-on controller, scheduling, health checks | Runs 24h unattended; only exceptions surface |
| **L9** | **Feedback loops** | L8 | Loop A (targeting) + Loop B (content) | Measurable lift in reply/conversion per segment over time |
| **L10** | **Analytics & ops** | L8 | Dashboards, monitoring, compliance ops, alerting | Live funnel dashboard + reputation/health alerts |

**Release packaging**
- **v1 — "Send machine" (L0–L5).** Scrape → score → personalize → send (email + WhatsApp) → follow up. Humans handle replies.
- **v2 — "Autonomous to demo" (L6–L8).** Adds reply handling, auto-answer, demo booking, orchestration. The answer to "is it fully automated?" — yes, to the demo.
- **v3 — "Self-improving" (L9–L10 + LinkedIn channel).** Adds both feedback loops, analytics, LinkedIn outreach.

---

## 12. Technical architecture
**Components**
- **AI Targeting brain** — Sonnet-class; clarifying-question flow (deep) + keyword-expansion routine (auto); outputs a structured **target spec** that configures the scrapers. Consumes Loop A feedback.
- **Scrapers** — Python bot + Playwright/Apify-style workers per platform; prefer official APIs where they exist (YouTube Data API); treat IG/LinkedIn as ToS-sensitive (see §13).
- **Lead DB** — Postgres (with `pgvector` for KB embeddings).
- **Queue / workflow** — a durable orchestrator (Temporal / n8n / Celery+Redis) for scheduling, retries, back-pressure.
- **LLM layer** — Haiku-class for bulk generation + intent classification; Sonnet-class for harder reply reasoning/escalation drafting; embeddings for KB RAG.
- **Channel adapters** — ESP for email (the tested stack); WhatsApp Business Cloud API via a BSP (Gupshup/AiSensy/Wati); LinkedIn adapter (semi-automated).
- **Knowledge base** — Exly product/pricing/affiliate/objection docs, chunked + embedded.
- **Dashboard** — funnel + reputation analytics.

**Core data model (starter schema)**
```
target_specs(id, mode[deep|keyword], persona_text, seed_examples[],
      seed_keywords[], expanded_keywords[], filters_json, attributes_json,
      approved, created_by_model, created_at)
leads(id, segment[creator|affiliate], niche, platform, follower_band,
      icp_score, priority_rank, status, geo, source, created_at)
channels(id, lead_id, type[email|whatsapp|linkedin], handle/number,
         deliverable, opted_out)
campaigns(id, segment, goal, active)
messages(id, lead_id, channel_id, campaign_id, variant, angle,
         body, sent_at, delivery_status)
events(id, lead_id, type[open|reply|click|bounce|book|optout],
       intent, sentiment, meta_json, ts)
conversions(id, lead_id, demo_booked_at, owner, summary, outcome)
kb_chunks(id, topic, text, embedding)
```

---

## 13. Channel specifics & risks (read before building L4)
- **Email (built)** — protect deliverability: domain warmup, SPF/DKIM/DMARC, volume ramp, bounce/spam monitoring, list hygiene.
- **WhatsApp (new) — biggest constraint.** Cold outbound to people who haven't opted in is **restricted by WhatsApp's Business policy**. Outbound to non-saved contacts generally requires **pre-approved template messages** through the **official Business API (via a BSP)**; aggressive unsolicited messaging risks **number bans**. Design WhatsApp as **opt-in-led / template-based**, triggered after an email or soft opt-in, not raw cold blasting. Unofficial automation libraries = high ban risk; avoid.
- **LinkedIn (new)** — **no official outreach API**; automated connection requests/DMs **violate LinkedIn ToS**. Use LinkedIn primarily for **sourcing/enrichment**; keep DMs **manual / human-in-the-loop**.
- **India DPDP Act 2023** — processing personal data needs a lawful basis/consent; honor opt-outs and data-subject requests; keep records. Loop in legal before scale.

---

## 14. Model strategy
- **Targeting brain (M1):** Sonnet-class — audience breakdown + keyword expansion. Quality matters more than cost; volume is low.
- **Generation (M4):** Haiku-class — cheap, fast, good enough for templated-but-personalized copy at hundreds/day. Fed lead context + Loop B winners.
- **Classification (M7):** Haiku-class for intent/sentiment tagging.
- **Hard reasoning / escalation drafts (M7):** Sonnet-class.
- **RAG:** embeddings + KB chunks so answers are grounded in real Exly pricing/affiliate terms (prevents hallucinated commitments).
- **Guardrails:** no invented pricing/claims; mandatory opt-out language; tone limits; human review queue for low-confidence outputs.

---

## 15. Analytics & reporting (L10)
Live dashboard of the full funnel (sourced → contacted → replied → demo → converted) split by **channel** and **segment**, plus reputation health (bounce, spam, WhatsApp block rate) and **loop performance** (reply/conversion lift over time). Weekly auto-digest to stakeholders.

---

## 16. Risks & mitigations
| Risk | Impact | Mitigation |
|---|---|---|
| WhatsApp cold outbound bans/policy breach | High | Official Business API + templates + opt-in-led design; throttle; monitor block rate |
| Email reputation damage | High | Warmup, authentication, volume ramp, hygiene, monitoring |
| LinkedIn account restriction | Med | Sourcing-only by default; manual/HITL DMs |
| DPDP / privacy non-compliance | High | Consent basis, opt-out handling, legal review, data retention limits |
| LLM hallucinating pricing/affiliate terms | Med | RAG grounding + guardrails + human review queue |
| Over-automation feels spammy → brand harm | Med | Quality gates, caps, segment-fit, easy opt-out |
| Feedback loop overfits to a narrow segment | Med | Exploration budget (bandit), periodic review |

---

## 17. Open questions / decisions needed
1. **"Automated" bar for v1** — blast + human reply handling (faster), or hold v1 until reply-handling is in?
2. **WhatsApp approach** — official Business API/BSP (compliant, template-gated) vs. an opt-in funnel that earns the right to message? (Recommend official + opt-in-led.)
3. **Sourcing priority** — which platforms first after Meta: Instagram, YouTube, or LinkedIn?
4. **Success definition** — is "converted" = demo booked, signup, or first affiliate sale?
5. **Sales handoff** — where do booked demos land (CRM/calendar/owner)?
6. **Volume target** — Gushwork ran ~200 msgs/day; what's our daily target per channel?

---

## 18. Appendix — glossary
- **ICP** — Ideal Customer Profile (here: target creators/affiliates).
- **Feeder** — the data intake (existing + new signals + replies) that powers personalization.
- **Loop A / Targeting** — learning that changes *who* we contact.
- **Loop B / Content** — learning that changes *what* we say.
- **BSP** — Business Solution Provider (WhatsApp API partner).
- **HITL** — Human In The Loop.
