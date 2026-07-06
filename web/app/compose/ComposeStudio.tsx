"use client";

import { useEffect, useMemo, useRef, useState, useTransition } from "react";
import { generateCopy, sendMessage, sendCampaign, listSendableLeads, type SendableLead } from "./actions";

type Channel = "whatsapp" | "email";
type Mode = "template" | "ai";

const TEMPLATES: Record<Channel, { name: string; subject?: string; body: string }[]> = {
  whatsapp: [
    // Default matches the approved WATI template `outreach_outbound`. On WhatsApp
    // the message that actually sends is this approved template; only {{1}} (the
    // first name) changes per lead, so keep the wording identical to WATI.
    { name: "Exly intro (WATI · outreach_outbound)", body: "Hi {{1}}! 👋\n\nI'm reaching out from Exly — we help creators and coaches in India sell their courses, take bookings, and get paid, all from one platform.\n\nWould you be open to a quick look at how it could work for your audience?" },
    { name: "Short intro", body: "Hi {{1}}! 👋 I'm with Exly — we help Indian creators & coaches sell courses, take bookings and get paid, all from one platform. Open to a quick look?" },
  ],
  email: [
    { name: "Quick idea", subject: "A quick idea for your {{niche}} offer", body: "Hi {{first_name}},\n\nI came across your work in {{niche}} and thought Exly could help you package and sell it — one platform for courses, payments, and your audience.\n\nOpen to a quick call this week?\n\n— Ameen, Exly\n\nNot relevant? Reply STOP and I won't reach out again." },
    { name: "Social proof", subject: "Helping {{niche}} creators sell more", body: "Hi {{first_name}},\n\nWe work with a lot of {{niche}} creators who wanted an easier way to sell courses and coaching. Exly gives you the whole stack in one place.\n\nWorth a short chat?\n\n— Ameen, Exly\n\nReply STOP to opt out." },
  ],
};

function fill(text: string, name: string, niche: string): string {
  return text
    // {{1}} = WhatsApp/WATI first-name variable; {{first_name}} / {{niche}} = our own.
    .replace(/\{\{\s*1\s*\}\}/g, name || "there")
    .replace(/\{\{\s*first_name\s*\}\}/gi, name || "there")
    .replace(/\{\{\s*niche\s*\}\}/gi, niche || "your niche");
}

export function ComposeStudio({
  preselectLeadId,
  preselectChannel,
}: {
  preselectLeadId?: number;
  preselectChannel?: Channel;
} = {}) {
  const [channel, setChannel] = useState<Channel>(preselectChannel ?? "whatsapp");
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
  const [audience, setAudience] = useState<"leads" | "test">("leads");
  const [leads, setLeads] = useState<SendableLead[]>([]);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [recipFilter, setRecipFilter] = useState("");   // optional niche filter for recipients (empty = all)
  const [loadingLeads, startLoad] = useTransition();

  function loadLeads(onlyId?: number) {
    startLoad(async () => {
      // Load ALL leads reachable on this channel. Niche is an OPTIONAL filter
      // (recipFilter), NOT the personalization niche box — so "all leads" show
      // by default. limit high so the whole reachable list appears.
      const r = await listSendableLeads({ channel, niche: onlyId ? undefined : (recipFilter.trim() || undefined), limit: 1000 });
      if (r.ok) {
        setLeads(r.leads);
        setSelected(new Set(onlyId ? r.leads.filter((l) => l.id === onlyId).map((l) => l.id) : r.leads.map((l) => l.id)));
        if (!onlyId && r.leads.length === 0) setSendState({ ok: false, text: `No leads with a ${channel === "whatsapp" ? "WhatsApp number" : "email"}${recipFilter ? ` in “${recipFilter}”` : ""}.` });
        else setSendState(null);
      } else setSendState({ ok: false, text: r.error });
    });
  }

  // deep-link from a lead page: /compose?lead=123 → load & select just that lead
  const preselected = useRef(false);
  useEffect(() => {
    if (preselectLeadId && !preselected.current) {
      preselected.current = true;
      setAudience("leads");
      loadLeads(preselectLeadId);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [preselectLeadId]);

  function toggle(id: number) {
    setSelected((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });
  }

  function send() {
    setSendState(null);
    startSend(async () => {
      if (audience === "test") {
        const r = await sendMessage({ channel, to: recipient, subject: fill(subject, sampleName, niche), body: fill(body, sampleName, niche), firstName: sampleName });
        setSendState(r.ok
          ? { ok: true, text: channel === "whatsapp" ? "Accepted by WATI ✓ — check delivery in WATI/Outreach" : "Sent ✓" }
          : { ok: false, text: r.error });
        return;
      }
      const ids = [...selected];
      if (!ids.length) { setSendState({ ok: false, text: "Select at least one lead." }); return; }
      const r = await sendCampaign({ leadIds: ids, channel, subject, body });
      setSendState(r.ok
        ? { ok: true, text: `Sent to ${r.sent} lead(s)${r.failed ? `, ${r.failed} failed` : ""} ✓` }
        : { ok: false, text: r.error });
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
    // WhatsApp cold sends can ONLY use the approved template — no AI free-text.
    // Force template mode so we never show AI-generate on WhatsApp.
    const nextMode: Mode = ch === "whatsapp" ? "template" : mode;
    setMode(nextMode);
    if (nextMode === "template") applyTemplate(ch, 0);
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

        {/* mode: template vs AI — AI free-text is EMAIL-ONLY (WhatsApp cold sends
             must use the approved template, so we don't offer AI there). */}
        <div className="card space-y-3">
          {channel === "email" ? (
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
          ) : (
            <p className="rounded-lg border border-line px-3 py-2 text-xs text-muted">
              📋 WhatsApp uses your <b className="text-ink">approved WATI template</b>. Only <code>{"{{1}}"}</code> (first name) changes per lead — free-text/AI isn’t allowed by WhatsApp for cold sends. Use <b className="text-ink">Email</b> for fully custom or AI-written copy.
            </p>
          )}

          <div className={"grid gap-3 " + (channel === "email" ? "grid-cols-2" : "grid-cols-1")}>
            {/* Niche only matters for email personalization; WhatsApp template has no niche. */}
            {channel === "email" && (
              <div>
                <label className="mb-1 block text-xs text-muted">Niche</label>
                <input className="input" value={niche} onChange={(e) => setNiche(e.target.value)} />
              </div>
            )}
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
            <label className="mb-1 block text-xs text-muted">
              {channel === "whatsapp"
                ? <>Message · approved WATI template (read-only) — each lead’s first name is filled in automatically</>
                : <>Message · use <code>{"{{first_name}}"}</code> and <code>{"{{niche}}"}</code></>}
            </label>
            {channel === "whatsapp" ? (
              <textarea className="input cursor-not-allowed font-mono text-xs opacity-90" rows={6}
                value={previewBody} readOnly />
            ) : (
              <textarea className="input font-mono text-xs" rows={9}
                value={body} onChange={(e) => setBody(e.target.value)} />
            )}
          </div>
          <p className="text-[11px] text-muted">
            {(channel === "whatsapp" ? previewBody : body).length} chars{channel === "whatsapp" && previewBody.length > 350 ? " · long for WhatsApp (keep under ~350)" : ""}
          </p>
        </div>

        {/* recipients + send */}
        <div className="card space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold">Recipients &amp; send</h3>
            <div className="inline-flex rounded-lg border border-line p-0.5 text-xs">
              <button onClick={() => setAudience("leads")}
                className={"rounded-md px-2.5 py-1 " + (audience === "leads" ? "bg-accent text-white" : "text-muted")}>My leads</button>
              <button onClick={() => setAudience("test")}
                className={"rounded-md px-2.5 py-1 " + (audience === "test" ? "bg-accent text-white" : "text-muted")}>Test to me</button>
            </div>
          </div>

          {audience === "test" ? (
            <div className="flex gap-2">
              <input className="input" value={recipient} onChange={(e) => setRecipient(e.target.value)}
                placeholder={channel === "whatsapp" ? "+919876543210" : "you@example.com"} />
              <button className="btn shrink-0" disabled={sending} onClick={send}>{sending ? "Sending…" : "Send →"}</button>
            </div>
          ) : (
            <>
              <div className="flex flex-wrap items-center gap-2">
                <button className="btn text-xs" disabled={loadingLeads} onClick={() => loadLeads()}>
                  {loadingLeads ? "Loading…" : `Load ${channel === "whatsapp" ? "WhatsApp" : "email"} leads`}
                </button>
                <input className="input h-8 w-40 text-xs" placeholder="filter by niche (optional)"
                  value={recipFilter} onChange={(e) => setRecipFilter(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter") loadLeads(); }} />
                {leads.length > 0 && (
                  <button className="text-xs text-accent hover:underline"
                    onClick={() => setSelected(selected.size === leads.length ? new Set() : new Set(leads.map((l) => l.id)))}>
                    {selected.size === leads.length ? "Clear all" : "Select all"}
                  </button>
                )}
                {leads.length > 0 && <span className="ml-auto text-xs text-muted">{selected.size}/{leads.length} selected</span>}
              </div>
              {leads.length > 0 && (
                <div className="max-h-52 space-y-0.5 overflow-auto rounded-lg border border-line p-2">
                  {leads.map((l) => (
                    <label key={l.id} className="flex items-center gap-2 rounded px-1.5 py-1 text-xs hover:bg-bg">
                      <input type="checkbox" checked={selected.has(l.id)} onChange={() => toggle(l.id)} />
                      <span className="font-medium">{l.name}</span>
                      <span className="text-muted">· {channel === "email" ? l.email : l.phone}</span>
                      {l.niche && <span className="ml-auto truncate text-muted">{l.niche}</span>}
                    </label>
                  ))}
                </div>
              )}
              <button className="btn w-full" disabled={sending || selected.size === 0} onClick={send}>
                {sending ? "Sending…" : `Send to ${selected.size} lead${selected.size === 1 ? "" : "s"} →`}
              </button>
            </>
          )}
          {sendState && <p className={"text-sm " + (sendState.ok ? "text-green-600" : "text-red-600")}>{sendState.text}</p>}
          <p className="text-[11px] text-muted">
            Sends via {channel === "whatsapp" ? "WATI" : "Resend"}, personalized per lead. Every send is logged in Outreach &amp; moves the lead to “contacted”.
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
