"use client";

import { useMemo, useState, useTransition } from "react";
import { generateCopy, sendMessage } from "./actions";

type Channel = "whatsapp" | "email";
type Mode = "template" | "ai";

const TEMPLATES: Record<Channel, { name: string; subject?: string; body: string }[]> = {
  whatsapp: [
    { name: "Warm intro", body: "Hi {{first_name}}, loved your work in {{niche}}! I'm with Exly — we help {{niche}} creators sell & scale their courses. Open to a quick chat? (reply STOP to opt out)" },
    { name: "Value-led", body: "Hey {{first_name}}, saw you're doing great things in {{niche}}. Exly gives creators a done-for-you platform to launch, sell and grow. Worth a 10-min look? (reply STOP to opt out)" },
    { name: "Direct", body: "Hi {{first_name}} — quick one. We help {{niche}} coaches turn their audience into course revenue with Exly. Can I share how? (reply STOP to opt out)" },
  ],
  email: [
    { name: "Quick idea", subject: "A quick idea for your {{niche}} offer", body: "Hi {{first_name}},\n\nI came across your work in {{niche}} and thought Exly could help you package and sell it — one platform for courses, payments, and your audience.\n\nOpen to a quick call this week?\n\n— Ameen, Exly\n\nNot relevant? Reply STOP and I won't reach out again." },
    { name: "Social proof", subject: "Helping {{niche}} creators sell more", body: "Hi {{first_name}},\n\nWe work with a lot of {{niche}} creators who wanted an easier way to sell courses and coaching. Exly gives you the whole stack in one place.\n\nWorth a short chat?\n\n— Ameen, Exly\n\nReply STOP to opt out." },
  ],
};

function fill(text: string, name: string, niche: string): string {
  return text
    .replace(/\{\{\s*first_name\s*\}\}/gi, name || "there")
    .replace(/\{\{\s*niche\s*\}\}/gi, niche || "your niche");
}

export function ComposeStudio() {
  const [channel, setChannel] = useState<Channel>("whatsapp");
  const [mode, setMode] = useState<Mode>("template");
  const [niche, setNiche] = useState("NLP coaching");
  const [sampleName, setSampleName] = useState("Maya");
  const [tplIdx, setTplIdx] = useState(0);
  const [subject, setSubject] = useState(TEMPLATES.email[0].subject || "");
  const [body, setBody] = useState(TEMPLATES.whatsapp[0].body);
  const [err, setErr] = useState<string | null>(null);
  const [pending, start] = useTransition();
  const [recipient, setRecipient] = useState("");
  const [sendState, setSendState] = useState<{ ok: boolean; text: string } | null>(null);
  const [sending, startSend] = useTransition();

  function send() {
    setSendState(null);
    startSend(async () => {
      const r = await sendMessage({
        channel, to: recipient,
        subject: fill(subject, sampleName, niche),
        body: fill(body, sampleName, niche),
      });
      setSendState(r.ok ? { ok: true, text: "Sent! ✓" } : { ok: false, text: r.error });
    });
  }

  function applyTemplate(ch: Channel, idx: number) {
    const t = TEMPLATES[ch][idx];
    setBody(t.body);
    setSubject(t.subject || "");
  }

  function switchChannel(ch: Channel) {
    setChannel(ch);
    setTplIdx(0);
    if (mode === "template") applyTemplate(ch, 0);
  }

  function aiGenerate() {
    setErr(null);
    start(async () => {
      const r = await generateCopy({ channel, niche, firstName: sampleName });
      if (!r.ok) { setErr(r.error); return; }
      setBody(r.body);
      if (channel === "email") setSubject(r.subject || subject);
    });
  }

  const previewSubject = useMemo(() => fill(subject, sampleName, niche), [subject, sampleName, niche]);
  const previewBody = useMemo(() => fill(body, sampleName, niche), [body, sampleName, niche]);

  return (
    <div className="grid gap-6 lg:grid-cols-[1fr_360px]">
      {/* ---- composer ---- */}
      <div className="space-y-4 rise">
        <div>
          <h1 className="text-xl font-semibold">Compose</h1>
          <p className="mt-1 text-sm text-muted">Write once, preview how it lands. Use a template or let AI draft it for your niche.</p>
        </div>

        {/* channel */}
        <div className="inline-flex rounded-xl border border-line p-1 text-sm">
          {(["whatsapp", "email"] as Channel[]).map((c) => (
            <button key={c} onClick={() => switchChannel(c)}
              className={"rounded-lg px-4 py-1.5 font-medium capitalize transition-colors " +
                (channel === c ? "bg-accent text-white" : "text-muted hover:text-ink")}>
              {c === "whatsapp" ? "WhatsApp" : "Email"}
            </button>
          ))}
        </div>

        {/* mode: template vs AI */}
        <div className="card space-y-3">
          <div className="flex gap-2">
            <button onClick={() => { setMode("template"); applyTemplate(channel, tplIdx); }}
              className={"flex-1 rounded-lg border px-3 py-2 text-sm font-medium " +
                (mode === "template" ? "border-accent text-accent" : "border-line text-muted hover:text-ink")}>
              📋 Use a template
            </button>
            <button onClick={() => setMode("ai")}
              className={"flex-1 rounded-lg border px-3 py-2 text-sm font-medium " +
                (mode === "ai" ? "border-accent text-accent" : "border-line text-muted hover:text-ink")}>
              ✨ Generate with AI
            </button>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="mb-1 block text-xs text-muted">Niche</label>
              <input className="input" value={niche} onChange={(e) => setNiche(e.target.value)} />
            </div>
            <div>
              <label className="mb-1 block text-xs text-muted">Sample first name (preview)</label>
              <input className="input" value={sampleName} onChange={(e) => setSampleName(e.target.value)} />
            </div>
          </div>

          {mode === "template" ? (
            <div>
              <label className="mb-1 block text-xs text-muted">Template</label>
              <select className="input" value={tplIdx}
                onChange={(e) => { const i = Number(e.target.value); setTplIdx(i); applyTemplate(channel, i); }}>
                {TEMPLATES[channel].map((t, i) => <option key={i} value={i}>{t.name}</option>)}
              </select>
            </div>
          ) : (
            <button className="btn w-full" disabled={pending} onClick={aiGenerate}>
              {pending ? "Writing…" : `✨ Generate ${channel === "whatsapp" ? "WhatsApp message" : "email"} for “${niche}”`}
            </button>
          )}
          {err && <p className="text-xs text-red-600">{err}</p>}
        </div>

        {/* editable copy */}
        <div className="card space-y-2">
          {channel === "email" && (
            <div>
              <label className="mb-1 block text-xs text-muted">Subject</label>
              <input className="input" value={subject} onChange={(e) => setSubject(e.target.value)} />
            </div>
          )}
          <div>
            <label className="mb-1 block text-xs text-muted">Message · use <code>{"{{first_name}}"}</code> and <code>{"{{niche}}"}</code></label>
            <textarea className="input font-mono text-xs" rows={channel === "email" ? 9 : 5}
              value={body} onChange={(e) => setBody(e.target.value)} />
          </div>
          <p className="text-[11px] text-muted">
            {body.length} chars{channel === "whatsapp" && body.length > 350 ? " · long for WhatsApp (keep under ~350)" : ""}
          </p>
        </div>

        {/* send */}
        <div className="card space-y-2">
          <label className="block text-xs text-muted">
            Send to ({channel === "whatsapp" ? "phone with country code" : "email"})
          </label>
          <div className="flex gap-2">
            <input className="input" value={recipient} onChange={(e) => setRecipient(e.target.value)}
              placeholder={channel === "whatsapp" ? "+919876543210" : "you@example.com"} />
            <button className="btn shrink-0" disabled={sending} onClick={send}>
              {sending ? "Sending…" : "Send →"}
            </button>
          </div>
          {sendState && (
            <p className={"text-sm " + (sendState.ok ? "text-green-600" : "text-red-600")}>{sendState.text}</p>
          )}
          <p className="text-[11px] text-muted">
            Sends via {channel === "whatsapp" ? "AiSensy" : "Resend"} — test with your own {channel === "whatsapp" ? "number" : "email"} first.
          </p>
        </div>
      </div>

      {/* ---- phone preview ---- */}
      <div className="lg:sticky lg:top-6 rise-2">
        <p className="mb-2 text-center text-xs font-medium uppercase tracking-wide text-muted">Live phone preview</p>
        <Phone>
          {channel === "whatsapp"
            ? <WhatsAppPreview body={previewBody} />
            : <EmailPreview subject={previewSubject} body={previewBody} />}
        </Phone>
      </div>
    </div>
  );
}

function Phone({ children }: { children: React.ReactNode }) {
  return (
    <div className="mx-auto w-[320px] rounded-[2.2rem] border-[10px] border-slate-900 bg-slate-900 shadow-2xl">
      <div className="relative overflow-hidden rounded-[1.5rem] bg-white" style={{ height: 560 }}>
        <div className="absolute left-1/2 top-0 h-5 w-32 -translate-x-1/2 rounded-b-2xl bg-slate-900" />
        {children}
      </div>
    </div>
  );
}

function WhatsAppPreview({ body }: { body: string }) {
  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-2 bg-[#075E54] px-3 pt-7 pb-2 text-white">
        <div className="h-8 w-8 rounded-full bg-white/30" />
        <div className="text-sm font-medium">You → Lead</div>
      </div>
      <div className="flex-1 space-y-2 p-3" style={{ background: "#ECE5DD" }}>
        <div className="ml-auto max-w-[80%] rounded-lg rounded-tr-none bg-[#DCF8C6] px-3 py-2 text-[13px] leading-snug text-slate-800 shadow-sm">
          {body || "Your message will appear here…"}
          <div className="mt-1 text-right text-[10px] text-slate-500">now ✓✓</div>
        </div>
      </div>
    </div>
  );
}

function EmailPreview({ subject, body }: { subject: string; body: string }) {
  return (
    <div className="flex h-full flex-col bg-white pt-7">
      <div className="border-b border-slate-200 px-4 py-2 text-[11px] text-slate-500">Inbox</div>
      <div className="px-4 py-3">
        <div className="text-[15px] font-semibold text-slate-900">{subject || "(no subject)"}</div>
        <div className="mt-1 flex items-center gap-2">
          <div className="h-7 w-7 rounded-full bg-indigo-500 text-center text-xs font-bold leading-7 text-white">E</div>
          <div className="text-[12px] text-slate-600">Exly Outbound &lt;you@exly.io&gt;</div>
        </div>
      </div>
      <div className="flex-1 overflow-auto whitespace-pre-wrap px-4 pb-4 text-[13px] leading-relaxed text-slate-800">
        {body || "Your email body will appear here…"}
      </div>
    </div>
  );
}
