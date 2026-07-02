"use server";

import { revalidatePath } from "next/cache";
import { getServerClient } from "@/lib/supabase/server";

type Res = { ok: true } | { ok: false; error: string };

// Fallback grounding if the KB table is empty / retrieval returns nothing.
const EXLY_KB_FALLBACK =
  "Exly is an all-in-one platform for Indian course creators, coaches and affiliates: " +
  "hosting & selling courses and 1:1/group coaching, bookings, payments (UPI/cards) and " +
  "payouts, a creator website/store, email + WhatsApp marketing, and analytics. India-first " +
  "(INR, GST, UPI). Do NOT quote specific prices, discounts, or guarantees — offer a quick demo.";

/**
 * Retrieve the most relevant KB chunks for a query via Postgres full-text search
 * (RAG retrieval step). Falls back to the top few docs when the query matches
 * nothing, and to a static blurb if the table is empty.
 */
async function retrieveKb(supa: ReturnType<typeof getServerClient>, query: string): Promise<string> {
  try {
    const { data } = await supa.rpc("kb_search", { q: query, k: 5 });
    let rows = (data ?? []) as { title: string; content: string }[];
    if (!rows.length) {
      const { data: any5 } = await supa.from("kb_docs").select("title,content").limit(5);
      rows = (any5 ?? []) as { title: string; content: string }[];
    }
    if (!rows.length) return EXLY_KB_FALLBACK;
    return rows.map((r) => `- ${r.title}: ${r.content}`).join("\n");
  } catch {
    return EXLY_KB_FALLBACK;
  }
}

/**
 * Draft a reply to a lead's inbound message (L6, RAG-lite). Grounds Claude Haiku
 * in EXLY_KB + the lead's niche and their latest inbound message. Returns a draft
 * for the operator to review/send — it does NOT send anything.
 */
export async function draftReply(
  leadId: number,
): Promise<{ ok: true; draft: string; channel: "whatsapp" | "email" } | { ok: false; error: string }> {
  const key = process.env.ANTHROPIC_API_KEY;
  if (!key) return { ok: false, error: "ANTHROPIC_API_KEY is not set in Vercel." };
  try {
    const supa = getServerClient();
    const { data: lead } = await supa.from("leads").select("niche,attributes").eq("id", leadId).single();
    const { data: inbound } = await supa
      .from("outreach").select("channel,body")
      .eq("lead_id", leadId).eq("direction", "in")
      .order("created_at", { ascending: false }).limit(1);
    const last = (inbound ?? [])[0];
    if (!last) return { ok: false, error: "No inbound message from this lead yet." };

    const a = (lead?.attributes || {}) as Record<string, unknown>;
    const name = ((a.advertiser as string) || "").split(" ")[0] || "there";
    const niche = lead?.niche || "their niche";
    const channel = (last.channel === "email" ? "email" : "whatsapp") as "whatsapp" | "email";

    // RAG: retrieve the KB chunks most relevant to what the lead actually asked.
    const knowledge = await retrieveKb(supa, String(last.body || niche));

    const system =
      "You are a helpful outbound rep for Exly. Answer the creator's message warmly and " +
      "specifically, using ONLY the facts below. Never invent pricing, numbers, or guarantees. " +
      "Aim to move them toward a short demo. " +
      (channel === "whatsapp"
        ? "Reply in 2-3 short WhatsApp sentences, plain text."
        : "Reply as a short email body (no subject line).") +
      "\n\nKNOWLEDGE:\n" + knowledge;
    const prompt =
      `Creator (${name}, niche: ${niche}) just replied:\n"""${last.body}"""\n\n` +
      `Write my reply.`;

    const res = await fetch("https://api.anthropic.com/v1/messages", {
      method: "POST",
      headers: { "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json" },
      body: JSON.stringify({ model: "claude-haiku-4-5-20251001", max_tokens: 400, system, messages: [{ role: "user", content: prompt }] }),
    });
    if (!res.ok) return { ok: false, error: `Anthropic ${res.status}: ${(await res.text()).slice(0, 160)}` };
    const j = await res.json();
    const draft: string = (j?.content?.[0]?.text ?? "").trim();
    if (!draft) return { ok: false, error: "Empty response from the model." };
    return { ok: true, draft, channel };
  } catch (e) {
    return { ok: false, error: (e as Error).message };
  }
}

/**
 * Book a demo (L7): record a conversion, emit a `book` event, advance the lead to
 * demo_booked.
 */
export async function bookDemo(
  leadId: number,
  input: { scheduledAt?: string; owner?: string; notes?: string },
): Promise<Res> {
  try {
    const supa = getServerClient();
    const { error: cErr } = await supa.from("conversions").insert({
      lead_id: leadId,
      demo_scheduled_at: input.scheduledAt ? new Date(input.scheduledAt).toISOString() : null,
      demo_booked_at: new Date().toISOString(),
      owner: input.owner || null,
      summary: input.notes || null,
      status: "booked",
    });
    if (cErr) return { ok: false, error: cErr.message };
    await supa.from("events").insert({ lead_id: leadId, type: "book", meta: { owner: input.owner || null } });
    const { error } = await supa.from("leads").update({ status: "demo_booked" }).eq("id", leadId);
    if (error) return { ok: false, error: error.message };
    revalidatePath(`/leads/${leadId}`);
    return { ok: true };
  } catch (e) {
    return { ok: false, error: (e as Error).message };
  }
}

/** Save operator free-text notes (leads.notes, added in migration 0004). */
export async function saveNotes(leadId: number, notes: string): Promise<Res> {
  try {
    const supa = getServerClient();
    const { error } = await supa.from("leads").update({ notes }).eq("id", leadId);
    if (error) return { ok: false, error: error.message };
    revalidatePath(`/leads/${leadId}`);
    return { ok: true };
  } catch (e) {
    return { ok: false, error: (e as Error).message };
  }
}

/** Identity-wide opt-out + flip the lead to opted_out (6A: optout => channel_type NULL). */
export async function suppressFromLead(leadId: number, identityKey: string): Promise<Res> {
  try {
    const supa = getServerClient();
    const { error: supErr } = await supa
      .from("suppression")
      .insert({ identity_key: identityKey, channel_type: null, reason: "optout", note: "console opt-out" });
    if (supErr && !/duplicate|unique/i.test(supErr.message)) return { ok: false, error: supErr.message };
    const { error } = await supa.from("leads").update({ status: "opted_out" }).eq("id", leadId);
    if (error) return { ok: false, error: error.message };
    revalidatePath(`/leads/${leadId}`);
    return { ok: true };
  } catch (e) {
    return { ok: false, error: (e as Error).message };
  }
}
