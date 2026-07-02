import { NextResponse } from "next/server";
import { getServerClient } from "@/lib/supabase/server";
import { findLeadByHandle, recordInbound, suppressInbound } from "@/lib/inbound";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

/**
 * AiSensy / WhatsApp webhook. Payload shapes vary by account, so we parse
 * tolerantly. We handle:
 *   - inbound user message  → log reply (or opt-out on STOP)
 *   - delivery status 'failed' → mark the last send failed
 *
 * Optional shared-secret gate: set INBOUND_WEBHOOK_SECRET and call
 * /api/webhooks/aisensy?secret=... .
 */
export async function POST(req: Request) {
  const url = new URL(req.url);
  const secret = process.env.INBOUND_WEBHOOK_SECRET;
  if (secret && url.searchParams.get("secret") !== secret) {
    return NextResponse.json({ ok: false, error: "unauthorized" }, { status: 401 });
  }

  let p: any;
  try { p = await req.json(); } catch { return NextResponse.json({ ok: true, skipped: "no-json" }); }

  try {
    const supa = getServerClient();
    const d = p?.data || p?.payload || p;
    const phone: string =
      d?.waId || d?.mobile || d?.from || d?.sender || d?.source || d?.phone || d?.destination || "";
    const text: string =
      d?.text || d?.message || d?.body || d?.messageBody || d?.text?.body || "";
    const status: string = (d?.status || p?.type || "").toString().toLowerCase();

    if (!phone) return NextResponse.json({ ok: true, skipped: "no-phone" });
    const lead = await findLeadByHandle(supa, "whatsapp", phone);
    if (!lead) return NextResponse.json({ ok: true, skipped: "unknown-lead" });

    // delivery failure on a prior send
    if (/fail|undeliver/.test(status) && !text) {
      const { data: last } = await supa
        .from("outreach").select("id")
        .eq("lead_id", lead.leadId).eq("channel", "whatsapp").eq("direction", "out")
        .order("created_at", { ascending: false }).limit(1);
      const id = (last ?? [])[0]?.id;
      if (id) await supa.from("outreach").update({ status: "failed", error: "provider: failed" }).eq("id", id);
      return NextResponse.json({ ok: true, handled: "status-failed" });
    }

    if (text) {
      if (/^\s*stop\b|unsubscribe|opt.?out/i.test(text)) {
        await suppressInbound(supa, { ...lead, channel: "whatsapp", reason: "optout" });
        return NextResponse.json({ ok: true, handled: "optout" });
      }
      await recordInbound(supa, { leadId: lead.leadId, channel: "whatsapp", fromHandle: phone, body: text });
      return NextResponse.json({ ok: true, handled: "inbound" });
    }

    return NextResponse.json({ ok: true, skipped: "no-text" });
  } catch (e) {
    return NextResponse.json({ ok: false, error: (e as Error).message }, { status: 500 });
  }
}

export async function GET() {
  return NextResponse.json({ ok: true, hint: "POST AiSensy events here." });
}
