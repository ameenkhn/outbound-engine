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
