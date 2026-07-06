"use server";

import { getServerClient } from "@/lib/supabase/server";

type SendResult = { ok: boolean; id?: string; error?: string };

// ── WhatsApp provider: WATI ────────────────────────────────────────────────
// Sends a pre-approved template. WATI template variables are positional named
// "1","2",… — our template's {{1}} = first name, so waParams map name "1"→value.
// Env: WATI_API_ENDPOINT (your tenant base URL from WATI → API Docs),
//      WATI_ACCESS_TOKEN (Bearer), WATI_TEMPLATE_NAME, WATI_BROADCAST_NAME (optional).
async function sendWhatsAppWati(to: string, body: string, waParams?: string[]): Promise<SendResult> {
  const endpoint = (process.env.WATI_API_ENDPOINT || "").replace(/\/+$/, "");
  const token = process.env.WATI_ACCESS_TOKEN;
  const template = process.env.WATI_TEMPLATE_NAME;
  const broadcast = process.env.WATI_BROADCAST_NAME || "outbound_engine";
  if (!endpoint || !token) return { ok: false, error: "Set WATI_API_ENDPOINT + WATI_ACCESS_TOKEN in Vercel." };
  if (!template) return { ok: false, error: "Set WATI_TEMPLATE_NAME (your approved WATI template) in Vercel." };

  const num = to.replace(/[^\d]/g, "");
  const values = waParams && waParams.length ? waParams : [body];
  const parameters = values.map((v, i) => ({ name: String(i + 1), value: v }));
  const auth = token.startsWith("Bearer ") ? token : `Bearer ${token}`;

  const res = await fetch(`${endpoint}/api/v1/sendTemplateMessage?whatsappNumber=${encodeURIComponent(num)}`, {
    method: "POST",
    headers: { authorization: auth, "content-type": "application/json" },
    body: JSON.stringify({ template_name: template, broadcast_name: broadcast, parameters }),
  });
  const raw = await res.text();
  let parsed: unknown = raw;
  try { parsed = JSON.parse(raw); } catch { /* keep raw */ }
  const obj = (parsed && typeof parsed === "object" ? parsed : {}) as Record<string, unknown>;
  // WATI replies { result: true/false, info?: "..." }.
  const rejected = obj.result === false || obj.ok === false;
  if (!res.ok || rejected) {
    const detail = (obj.info || obj.message || obj.error || (typeof parsed === "string" ? parsed : "")) as string;
    return { ok: false, error: `WATI ${res.status}${detail ? `: ${String(detail).slice(0, 200)}` : ""}` };
  }
  return { ok: true, id: "accepted" };
}

// ── WhatsApp provider: AiSensy (fallback) ──────────────────────────────────
async function sendWhatsAppAisensy(to: string, body: string, waParams?: string[]): Promise<SendResult> {
  const key = process.env.AISENSY_API_KEY;
  const campaign = process.env.AISENSY_CAMPAIGN;
  if (!key || !campaign) return { ok: false, error: "Configure WATI (WATI_API_ENDPOINT/…) or AiSensy (AISENSY_API_KEY + AISENSY_CAMPAIGN) in Vercel." };
  const res = await fetch("https://backend.aisensy.com/campaign/t1/api/v2", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      apiKey: key, campaignName: campaign,
      destination: to.replace(/[^\d]/g, ""), userName: "Exly Outbound",
      templateParams: waParams && waParams.length ? waParams : [body],
    }),
  });
  const raw = await res.text();
  let parsed: unknown = raw;
  try { parsed = JSON.parse(raw); } catch { /* keep raw */ }
  const obj = (parsed && typeof parsed === "object" ? parsed : {}) as Record<string, unknown>;
  const rejected = obj.success === false || obj.status === "error" || (typeof obj.errorMessage === "string");
  if (!res.ok || rejected) {
    const detail = (obj.errorMessage || obj.error || obj.message || (typeof parsed === "string" ? parsed : "")) as string;
    return { ok: false, error: `AiSensy ${res.status}${detail ? `: ${String(detail).slice(0, 200)}` : ""}` };
  }
  return { ok: true, id: "accepted" };
}

// ── low-level: send ONE message via the provider ───────────────────────────
async function sendOne(
  channel: "whatsapp" | "email",
  to: string,
  subject: string,
  body: string,
  // WhatsApp templates are pre-approved by Meta: only the {{n}} variables change
  // per recipient. Our approved template is "Hi {{1}}! …" so {{1}} = first name.
  // Pass the variable values here; falls back to [body] for older 1-var setups.
  waParams?: string[],
): Promise<{ ok: boolean; id?: string; error?: string }> {
  try {
    if (channel === "whatsapp") {
      // Provider is pluggable: WATI when configured, else AiSensy. Both send a
      // pre-approved WhatsApp template; {{1}} is filled with the first name.
      if (process.env.WATI_API_ENDPOINT && process.env.WATI_ACCESS_TOKEN) {
        return await sendWhatsAppWati(to, body, waParams);
      }
      return await sendWhatsAppAisensy(to, body, waParams);
    }
    const key = process.env.RESEND_API_KEY;
    const from = process.env.EMAIL_FROM;
    if (!key || !from) return { ok: false, error: "Set RESEND_API_KEY + EMAIL_FROM in Vercel." };
    const res = await fetch("https://api.resend.com/emails", {
      method: "POST",
      headers: { authorization: `Bearer ${key}`, "content-type": "application/json" },
      body: JSON.stringify({ from, to: [to], subject, text: body }),
    });
    if (!res.ok) return { ok: false, error: `Resend ${res.status}` };
    const data = await res.json();
    return { ok: true, id: String(data?.id || "sent") };
  } catch (e) {
    return { ok: false, error: (e as Error).message };
  }
}

/** Send one message to a manually-typed recipient (test sends). */
export async function sendMessage(input: {
  channel: "whatsapp" | "email"; to: string; subject?: string; body: string; firstName?: string;
}): Promise<{ ok: true; id: string } | { ok: false; error: string }> {
  if (!input.to?.trim()) return { ok: false, error: "Enter a recipient." };
  if (!input.body?.trim()) return { ok: false, error: "Message is empty." };
  const wa = input.channel === "whatsapp" ? [ (input.firstName || "there").split(" ")[0] ] : undefined;
  const r = await sendOne(input.channel, input.to.trim(), input.subject || "", input.body, wa);
  return r.ok ? { ok: true, id: r.id || "sent" } : { ok: false, error: r.error || "send failed" };
}

// ── lead selection for a campaign ──────────────────────────────────────────
export type SendableLead = {
  id: number; name: string; niche: string | null; platform: string | null;
  status: string; email: string | null; phone: string | null;
};

function contactFromRow(r: any): { email: string | null; phone: string | null } {
  const a = (r.attributes || {}) as Record<string, unknown>;
  const key: string = r.identity_key || "";
  let email = (a.email as string) || (r.channel_email as string) || null;
  let phone = (a.phone as string) || (r.channel_phone as string) || null;
  if (!email && key.startsWith("email:")) email = key.slice(6);
  if (!phone && key.startsWith("phone:")) phone = key.slice(6);
  return { email, phone };
}

/** List leads that can receive on `channel`, with optional niche/status filters. */
export async function listSendableLeads(filters: {
  channel: "whatsapp" | "email"; niche?: string; status?: string; limit?: number;
}): Promise<{ ok: true; leads: SendableLead[] } | { ok: false; error: string }> {
  try {
    const supa = getServerClient();
    let q = supa
      .from("leads")
      .select("id,identity_key,niche,platform,status,attributes,channels(type,handle)")
      .order("priority_rank", { ascending: true, nullsFirst: false })
      .limit(filters.limit || 300);
    if (filters.niche) q = q.ilike("niche", `%${filters.niche}%`);
    if (filters.status) q = q.eq("status", filters.status);
    const { data, error } = await q;
    if (error) return { ok: false, error: error.message };

    const need = filters.channel === "email" ? "email" : "phone";
    const leads: SendableLead[] = [];
    for (const r of (data ?? []) as any[]) {
      const chans = (r.channels || []) as { type: string; handle: string }[];
      r.channel_email = chans.find((c) => c.type === "email")?.handle;
      r.channel_phone = chans.find((c) => c.type === "whatsapp")?.handle;
      const { email, phone } = contactFromRow(r);
      if (need === "email" ? !email : !phone) continue;
      const a = (r.attributes || {}) as Record<string, unknown>;
      leads.push({
        id: r.id, name: (a.advertiser as string) || r.identity_key,
        niche: r.niche, platform: r.platform, status: r.status, email, phone,
      });
    }
    return { ok: true, leads };
  } catch (e) {
    return { ok: false, error: (e as Error).message };
  }
}

function fillTemplate(text: string, name: string, niche: string): string {
  const first = (name || "").split(" ")[0] || "there";
  return text
    .replace(/\{\{\s*first_name\s*\}\}/gi, first)
    .replace(/\{\{\s*niche\s*\}\}/gi, niche || "your niche");
}

/** Send a composed message to many leads (personalized per lead), log each to
 *  `outreach`, and move sent leads to 'contacted'. */
export async function sendCampaign(input: {
  leadIds: number[]; channel: "whatsapp" | "email"; subject?: string; body: string;
}): Promise<{ ok: true; sent: number; failed: number } | { ok: false; error: string }> {
  if (!input.leadIds?.length) return { ok: false, error: "Select at least one lead." };
  if (!input.body?.trim()) return { ok: false, error: "Message is empty." };
  if (input.leadIds.length > 250) return { ok: false, error: "Send to at most 250 leads per batch (WhatsApp daily tier / rate limits)." };

  const supa = getServerClient();
  const { data, error } = await supa
    .from("leads")
    .select("id,identity_key,niche,attributes,channels(type,handle)")
    .in("id", input.leadIds);
  if (error) return { ok: false, error: error.message };

  const rows = (data ?? []) as any[];

  async function sendToLead(r: any) {
    const chans = (r.channels || []) as { type: string; handle: string }[];
    r.channel_email = chans.find((c) => c.type === "email")?.handle;
    r.channel_phone = chans.find((c) => c.type === "whatsapp")?.handle;
    const { email, phone } = contactFromRow(r);
    const to = input.channel === "email" ? email : phone;
    const a = (r.attributes || {}) as Record<string, unknown>;
    const name = (a.advertiser as string) || "";
    if (!to) return { id: r.id, ok: false, to: null as string | null };

    const body = fillTemplate(input.body, name, r.niche || "");
    const subject = fillTemplate(input.subject || "", name, r.niche || "");
    const firstName = (name || "").split(" ")[0] || "there";
    const wa = input.channel === "whatsapp" ? [firstName] : undefined;
    const sent = await sendOne(input.channel, to, subject, body, wa);

    await supa.from("outreach").insert({
      lead_id: r.id, channel: input.channel, to_handle: to,
      subject: subject || null, body, status: sent.ok ? "sent" : "failed",
      provider_id: sent.id || null, error: sent.ok ? null : sent.error || null,
    });
    if (sent.ok) {
      await supa.from("leads").update({ status: "contacted" }).eq("id", r.id).eq("status", "new");
    }
    return { id: r.id, ok: sent.ok, to };
  }

  // Send in small concurrent chunks so a large batch doesn't hammer the provider
  // (WATI/Resend) with hundreds of simultaneous requests and trip rate limits.
  const CHUNK = 8;
  const results: { id: number; ok: boolean; to: string | null }[] = [];
  for (let i = 0; i < rows.length; i += CHUNK) {
    const part = await Promise.all(rows.slice(i, i + CHUNK).map(sendToLead));
    results.push(...part);
  }

  const sent = results.filter((x) => x.ok).length;
  return { ok: true, sent, failed: results.length - sent };
}

// ── AI copy generation (Claude Haiku) ──────────────────────────────────────
export async function generateCopy(input: {
  channel: "whatsapp" | "email"; niche: string; firstName?: string;
}): Promise<{ ok: true; subject?: string; body: string } | { ok: false; error: string }> {
  const key = process.env.ANTHROPIC_API_KEY;
  if (!key) return { ok: false, error: "ANTHROPIC_API_KEY is not set in Vercel (Settings → Environment Variables)." };
  const niche = (input.niche || "their niche").trim();
  const name = (input.firstName || "").trim();
  const isWa = input.channel === "whatsapp";
  const system =
    "You are an outbound copywriter for Exly, an all-in-one platform for Indian " +
    "course/coaching creators and affiliates. Be warm, specific and human. Do NOT " +
    "invent pricing, percentages, or guarantees. " +
    (isWa
      ? "Write ONE short WhatsApp message: 2-3 sentences, under ~350 characters, plain text, no subject. End with '(reply STOP to opt out)'. Return only the message."
      : "Write one short cold email. Return 'Subject:' on the first line, a blank line, then the body. End the body with an opt-out line (reply STOP).");
  const prompt =
    `Write ${isWa ? "a WhatsApp message" : "a cold email"} to a creator/coach in the "${niche}" niche` +
    (name ? `, first name "${name}"` : "") +
    `. Reference their niche naturally and pitch how Exly helps them sell & scale their courses.`;
  try {
    const res = await fetch("https://api.anthropic.com/v1/messages", {
      method: "POST",
      headers: { "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json" },
      body: JSON.stringify({ model: "claude-haiku-4-5-20251001", max_tokens: 500, system, messages: [{ role: "user", content: prompt }] }),
    });
    if (!res.ok) return { ok: false, error: `Anthropic ${res.status}: ${(await res.text()).slice(0, 160)}` };
    const data = await res.json();
    const text: string = (data?.content?.[0]?.text ?? "").trim();
    if (!text) return { ok: false, error: "Empty response from the model." };
    if (isWa) return { ok: true, body: text };
    const lines = text.split("\n");
    if (lines[0]?.toLowerCase().startsWith("subject:")) {
      return { ok: true, subject: lines[0].split(":").slice(1).join(":").trim(), body: lines.slice(1).join("\n").trim() };
    }
    return { ok: true, subject: `A quick idea for your ${niche} offer`, body: text };
  } catch (e) {
    return { ok: false, error: (e as Error).message };
  }
}
