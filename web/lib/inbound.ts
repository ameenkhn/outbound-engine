import "server-only";
import type { SupabaseClient } from "@supabase/supabase-js";

/** Channel we received on. */
export type InChannel = "whatsapp" | "email";

const digits = (s: string) => (s || "").replace(/[^\d]/g, "");

/**
 * Resolve an inbound sender handle → a lead. Matches the `channels` table:
 * email by case-insensitive equality, WhatsApp by trailing-digit match (handles
 * +country-code / 0-prefix noise). Returns null if we don't recognise them.
 */
export async function findLeadByHandle(
  supa: SupabaseClient,
  channel: InChannel,
  handle: string,
): Promise<{ leadId: number; identityKey: string; channelId: number } | null> {
  const type = channel === "email" ? "email" : "whatsapp";
  const { data } = await supa
    .from("channels")
    .select("id,lead_id,handle,type,leads(identity_key)")
    .eq("type", type)
    .limit(500);
  const rows = (data ?? []) as any[];
  const want = channel === "email" ? handle.trim().toLowerCase() : digits(handle);
  for (const r of rows) {
    const h = channel === "email" ? String(r.handle).trim().toLowerCase() : digits(r.handle);
    const hit = channel === "email" ? h === want : h.length >= 8 && (h.endsWith(want) || want.endsWith(h));
    if (hit) return { leadId: r.lead_id, identityKey: r.leads?.identity_key ?? "", channelId: r.id };
  }
  return null;
}

/**
 * Record an inbound reply: log it (direction 'in'), emit a `reply` event, flip the
 * most-recent outbound send on this channel to 'replied', and advance the lead
 * contacted→replied. Idempotency is best-effort (providers may retry).
 */
export async function recordInbound(
  supa: SupabaseClient,
  args: { leadId: number; channel: InChannel; fromHandle: string; body: string },
): Promise<void> {
  const { leadId, channel, fromHandle, body } = args;

  await supa.from("outreach").insert({
    lead_id: leadId, channel, direction: "in",
    to_handle: fromHandle, body: body || "(no text)", status: "received",
  });

  await supa.from("events").insert({ lead_id: leadId, type: "reply", meta: { channel, body } });

  // flip the latest still-'sent' outbound message on this channel to 'replied'
  const { data: last } = await supa
    .from("outreach")
    .select("id")
    .eq("lead_id", leadId).eq("channel", channel).eq("direction", "out").eq("status", "sent")
    .order("created_at", { ascending: false }).limit(1);
  const lastId = (last ?? [])[0]?.id;
  if (lastId) await supa.from("outreach").update({ status: "replied" }).eq("id", lastId);

  await supa.from("leads").update({ status: "replied" }).eq("id", leadId).eq("status", "contacted");

  // Optional AI auto-responder (the "chatbot"): best-effort, never breaks inbound.
  try {
    await maybeAutoRespond(supa, { leadId, channel, toHandle: fromHandle, inboundText: body });
  } catch {
    /* auto-reply is additive; swallow errors */
  }
}

/**
 * RAG auto-responder. When AUTORESPOND=1, drafts a KB-grounded reply with Claude
 * Haiku and:
 *   - email    → sends it via Resend and logs it as an outbound 'sent';
 *   - whatsapp → stores it as a ready 'draft' (free-form session sends aren't
 *                wired to a WhatsApp session endpoint, so a human sends it).
 * No-op unless AUTORESPOND=1 and ANTHROPIC_API_KEY is set.
 */
export async function maybeAutoRespond(
  supa: SupabaseClient,
  args: { leadId: number; channel: InChannel; toHandle: string; inboundText: string },
): Promise<void> {
  if (process.env.AUTORESPOND !== "1") return;
  const key = process.env.ANTHROPIC_API_KEY;
  if (!key) return;
  const { leadId, channel, toHandle, inboundText } = args;

  // retrieve KB (RAG)
  let knowledge = "";
  try {
    const { data } = await supa.rpc("kb_search", { q: inboundText || "", k: 5 });
    knowledge = ((data ?? []) as { title: string; content: string }[]).map((r) => `- ${r.title}: ${r.content}`).join("\n");
  } catch { /* fall through */ }
  if (!knowledge) {
    const { data } = await supa.from("kb_docs").select("title,content").limit(5);
    knowledge = ((data ?? []) as { title: string; content: string }[]).map((r) => `- ${r.title}: ${r.content}`).join("\n");
  }
  if (!knowledge) return;

  const { data: lead } = await supa.from("leads").select("niche,attributes").eq("id", leadId).single();
  const a = (lead?.attributes || {}) as Record<string, unknown>;
  const name = ((a.advertiser as string) || "").split(" ")[0] || "there";

  const system =
    "You are a helpful rep for Exly. Answer the creator's message warmly using ONLY the facts below. " +
    "Never invent pricing, numbers, or guarantees. Nudge toward a short demo. " +
    (channel === "whatsapp" ? "Reply in 2-3 short WhatsApp sentences." : "Reply as a short email body (no subject line).") +
    "\n\nKNOWLEDGE:\n" + knowledge;
  const prompt = `Creator (${name}) just said:\n"""${inboundText}"""\n\nWrite my reply.`;

  let draft = "";
  try {
    const res = await fetch("https://api.anthropic.com/v1/messages", {
      method: "POST",
      headers: { "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json" },
      body: JSON.stringify({ model: "claude-haiku-4-5-20251001", max_tokens: 400, system, messages: [{ role: "user", content: prompt }] }),
    });
    if (!res.ok) return;
    const j = await res.json();
    draft = (j?.content?.[0]?.text ?? "").trim();
  } catch { return; }
  if (!draft) return;

  let status = "draft";
  let provider_id: string | null = null;
  let error: string | null = null;
  let subject: string | null = null;

  if (channel === "email") {
    const rk = process.env.RESEND_API_KEY;
    const from = process.env.EMAIL_FROM;
    if (rk && from) {
      subject = "Re: your message";
      try {
        const res = await fetch("https://api.resend.com/emails", {
          method: "POST",
          headers: { authorization: `Bearer ${rk}`, "content-type": "application/json" },
          body: JSON.stringify({ from, to: [toHandle], subject, text: draft }),
        });
        if (res.ok) { const d = await res.json(); status = "sent"; provider_id = String(d?.id || "sent"); }
        else { status = "failed"; error = `Resend ${res.status}`; }
      } catch (e) { status = "failed"; error = (e as Error).message; }
    }
  }
  // whatsapp stays status 'draft' (needs a session-message endpoint to auto-send)

  await supa.from("outreach").insert({
    lead_id: leadId, channel, direction: "out", to_handle: toHandle,
    subject, body: draft, status, provider_id, error,
  });
}

/**
 * Suppress a lead after a hard bounce / spam complaint / unsubscribe.
 *   bounce/complaint → channel-specific   |   optout → identity-wide (null channel)
 */
export async function suppressInbound(
  supa: SupabaseClient,
  args: { identityKey: string; leadId: number; channel: InChannel; reason: "hardbounce" | "complaint" | "optout" },
): Promise<void> {
  const { identityKey, leadId, channel, reason } = args;
  const identityWide = reason === "optout";
  const { error } = await supa.from("suppression").insert({
    identity_key: identityKey,
    channel_type: identityWide ? null : channel,
    reason,
    note: `inbound webhook (${reason})`,
  });
  if (error && !/duplicate|unique/i.test(error.message)) throw error;

  await supa.from("events").insert({
    lead_id: leadId,
    type: reason === "complaint" ? "complaint" : reason === "optout" ? "optout" : "bounce",
    meta: { channel, reason },
  });

  if (identityWide) {
    await supa.from("leads").update({ status: "opted_out" }).eq("id", leadId);
  }
}
