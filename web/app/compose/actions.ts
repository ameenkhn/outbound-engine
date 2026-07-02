"use server";

/** Send a composed message directly via AiSensy (WhatsApp) or Resend (email).
 *  Runs server-side so keys never reach the browser. Great for test sends; the
 *  worker handles batch sending at scale. */
export async function sendMessage(input: {
  channel: "whatsapp" | "email";
  to: string;
  subject?: string;
  body: string;
}): Promise<{ ok: true; id: string } | { ok: false; error: string }> {
  const to = (input.to || "").trim();
  if (!to) return { ok: false, error: "Enter a recipient." };
  if (!input.body.trim()) return { ok: false, error: "Message is empty." };

  try {
    if (input.channel === "whatsapp") {
      const key = process.env.AISENSY_API_KEY;
      const campaign = process.env.AISENSY_CAMPAIGN;
      if (!key || !campaign) return { ok: false, error: "Set AISENSY_API_KEY + AISENSY_CAMPAIGN in Vercel to send WhatsApp." };
      const res = await fetch("https://backend.aisensy.com/campaign/t1/api/v2", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          apiKey: key, campaignName: campaign,
          destination: to.replace(/[^\d]/g, ""),
          userName: "Exly Outbound",
          templateParams: [input.body],
        }),
      });
      if (!res.ok) return { ok: false, error: `AiSensy ${res.status}: ${(await res.text()).slice(0, 160)}` };
      return { ok: true, id: "sent" };
    }

    // email via Resend
    const key = process.env.RESEND_API_KEY;
    const from = process.env.EMAIL_FROM;
    if (!key || !from) return { ok: false, error: "Set RESEND_API_KEY + EMAIL_FROM in Vercel to send email." };
    const res = await fetch("https://api.resend.com/emails", {
      method: "POST",
      headers: { authorization: `Bearer ${key}`, "content-type": "application/json" },
      body: JSON.stringify({ from, to: [to], subject: input.subject || "", text: input.body }),
    });
    if (!res.ok) return { ok: false, error: `Resend ${res.status}: ${(await res.text()).slice(0, 160)}` };
    const data = await res.json();
    return { ok: true, id: String(data?.id || "sent") };
  } catch (e) {
    return { ok: false, error: (e as Error).message };
  }
}

/** Generate outreach copy with Claude Haiku (cheap + fast). Server-side so the
 *  API key never reaches the browser. Returns {subject?, body} or an error. */
export async function generateCopy(input: {
  channel: "whatsapp" | "email";
  niche: string;
  firstName?: string;
}): Promise<{ ok: true; subject?: string; body: string } | { ok: false; error: string }> {
  const key = process.env.ANTHROPIC_API_KEY;
  if (!key) {
    return { ok: false, error: "ANTHROPIC_API_KEY is not set in Vercel. Add it (Settings → Environment Variables) to use AI generation." };
  }
  const niche = (input.niche || "their niche").trim();
  const name = (input.firstName || "").trim();

  const isWa = input.channel === "whatsapp";
  const system =
    "You are an outbound copywriter for Exly, an all-in-one platform for Indian " +
    "course/coaching creators and affiliates. Be warm, specific and human. Do NOT " +
    "invent pricing, percentages, or guarantees. " +
    (isWa
      ? "Write ONE short WhatsApp message: 2-3 sentences, under ~350 characters, plain text, no subject. End with a soft opt-out like '(reply STOP to opt out)'. Return only the message text."
      : "Write one short cold email. Return 'Subject:' on the first line, a blank line, then the body. End the body with an opt-out line (reply STOP).");

  const prompt =
    `Write ${isWa ? "a WhatsApp message" : "a cold email"} to a creator/coach in the "${niche}" niche` +
    (name ? `, first name "${name}"` : "") +
    `. Reference their niche naturally and pitch how Exly helps them sell & scale their courses.`;

  try {
    const res = await fetch("https://api.anthropic.com/v1/messages", {
      method: "POST",
      headers: {
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
      },
      body: JSON.stringify({
        model: "claude-haiku-4-5-20251001",
        max_tokens: 500,
        system,
        messages: [{ role: "user", content: prompt }],
      }),
    });
    if (!res.ok) {
      const t = await res.text();
      return { ok: false, error: `Anthropic ${res.status}: ${t.slice(0, 200)}` };
    }
    const data = await res.json();
    const text: string = (data?.content?.[0]?.text ?? "").trim();
    if (!text) return { ok: false, error: "Empty response from the model." };

    if (isWa) return { ok: true, body: text };
    // split Subject: line for email
    const lines = text.split("\n");
    if (lines[0]?.toLowerCase().startsWith("subject:")) {
      const subject = lines[0].split(":").slice(1).join(":").trim();
      const body = lines.slice(1).join("\n").trim();
      return { ok: true, subject, body };
    }
    return { ok: true, subject: `A quick idea for your ${niche} offer`, body: text };
  } catch (e) {
    return { ok: false, error: (e as Error).message };
  }
}
