import { NextResponse } from "next/server";
import { getServerClient } from "@/lib/supabase/server";
import { findLeadByHandle, recordInbound, suppressInbound } from "@/lib/inbound";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

/**
 * Resend webhook. Handles two payload families:
 *   1. Delivery events  — { type: "email.bounced" | "email.complained" | ... , data: { to } }
 *   2. Inbound email    — parsed reply with { from, subject, text }
 *
 * Optional shared-secret gate: set INBOUND_WEBHOOK_SECRET and call the URL as
 * /api/webhooks/resend?secret=... . (Add Resend Svix signature verification for
 * production-grade auth.)
 */
export async function POST(req: Request) {
  const url = new URL(req.url);
  const secret = process.env.INBOUND_WEBHOOK_SECRET;
  if (secret && url.searchParams.get("secret") !== secret) {
    return NextResponse.json({ ok: false, error: "unauthorized" }, { status: 401 });
  }

  let payload: any;
  try { payload = await req.json(); } catch { return NextResponse.json({ ok: true, skipped: "no-json" }); }

  try {
    const supa = getServerClient();
    const type: string = payload?.type || "";
    const data = payload?.data || payload;

    // ---- 1. delivery / reputation events -------------------------------------
    if (type.startsWith("email.")) {
      const to: string = Array.isArray(data?.to) ? data.to[0] : data?.to || data?.email || "";
      if (!to) return NextResponse.json({ ok: true, skipped: "no-recipient" });
      const lead = await findLeadByHandle(supa, "email", to);
      if (!lead) return NextResponse.json({ ok: true, skipped: "unknown-lead" });

      if (type === "email.bounced")
        await suppressInbound(supa, { ...lead, channel: "email", reason: "hardbounce" });
      else if (type === "email.complained")
        await suppressInbound(supa, { ...lead, channel: "email", reason: "complaint" });
      else if (type === "email.opened")
        await supa.from("events").insert({ lead_id: lead.leadId, type: "open", meta: { channel: "email" } });
      return NextResponse.json({ ok: true, handled: type });
    }

    // ---- 2. inbound reply (Resend Inbound) -----------------------------------
    const from: string = data?.from?.email || data?.from || data?.sender || "";
    const text: string = data?.text || data?.stripped_text || data?.body || data?.html || "";
    if (from) {
      const lead = await findLeadByHandle(supa, "email", from);
      if (!lead) return NextResponse.json({ ok: true, skipped: "unknown-sender" });
      // unsubscribe intent → identity-wide opt-out
      if (/\b(unsubscribe|stop|opt.?out|remove me)\b/i.test(text)) {
        await suppressInbound(supa, { ...lead, channel: "email", reason: "optout" });
      } else {
        await recordInbound(supa, { leadId: lead.leadId, channel: "email", fromHandle: from, body: text });
      }
      return NextResponse.json({ ok: true, handled: "inbound" });
    }

    return NextResponse.json({ ok: true, skipped: "unrecognized" });
  } catch (e) {
    return NextResponse.json({ ok: false, error: (e as Error).message }, { status: 500 });
  }
}

export async function GET() {
  return NextResponse.json({ ok: true, hint: "POST Resend events here." });
}
