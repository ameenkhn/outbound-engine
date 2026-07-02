"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { saveNotes, suppressFromLead, draftReply, bookDemo } from "./actions";

export function NotesEditor({ leadId, initial }: { leadId: number; initial: string }) {
  const router = useRouter();
  const [notes, setNotes] = useState(initial);
  const [msg, setMsg] = useState<string | null>(null);
  const [pending, start] = useTransition();

  return (
    <div>
      <textarea
        className="w-full rounded border border-line px-2 py-1.5 text-sm"
        rows={4}
        placeholder="Operator notes…"
        value={notes}
        onChange={(e) => setNotes(e.target.value)}
      />
      <div className="mt-2 flex items-center gap-2">
        <button
          className="btn"
          disabled={pending}
          onClick={() =>
            start(async () => {
              const r = await saveNotes(leadId, notes);
              setMsg(r.ok ? "Saved" : `Error: ${r.error}`);
              if (r.ok) router.refresh();
            })
          }
        >
          Save notes
        </button>
        {msg && <span className="text-xs text-muted">{msg}</span>}
      </div>
    </div>
  );
}

export function ReplyDrafter({ leadId, hasInbound }: { leadId: number; hasInbound: boolean }) {
  const [draft, setDraft] = useState<string | null>(null);
  const [channel, setChannel] = useState<"whatsapp" | "email">("whatsapp");
  const [err, setErr] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [pending, start] = useTransition();

  if (!hasInbound) {
    return <p className="text-sm text-muted">No inbound reply yet. When this lead replies, draft an AI response here.</p>;
  }
  return (
    <div className="space-y-2">
      <button className="btn" disabled={pending}
        onClick={() => { setErr(null); start(async () => {
          const r = await draftReply(leadId);
          if (r.ok) { setDraft(r.draft); setChannel(r.channel); } else setErr(r.error);
        }); }}>
        {pending ? "Drafting…" : "✨ Draft AI reply"}
      </button>
      {err && <p className="text-xs text-red-600">{err}</p>}
      {draft && (
        <div className="space-y-2">
          <textarea className="w-full rounded border border-line px-2 py-1.5 text-sm" rows={5}
            value={draft} onChange={(e) => setDraft(e.target.value)} />
          <div className="flex items-center gap-2">
            <button className="btn-ghost text-xs"
              onClick={() => { navigator.clipboard?.writeText(draft); setCopied(true); setTimeout(() => setCopied(false), 1500); }}>
              {copied ? "Copied ✓" : "Copy"}
            </button>
            <Link className="btn-ghost text-xs" href={`/compose?lead=${leadId}&channel=${channel}`}>Open in Compose →</Link>
            <span className="text-[11px] text-muted">Grounded in the Exly KB · review before sending.</span>
          </div>
        </div>
      )}
    </div>
  );
}

export function BookDemoForm({ leadId }: { leadId: number }) {
  const router = useRouter();
  const [when, setWhen] = useState("");
  const [owner, setOwner] = useState("");
  const [notes, setNotes] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [link, setLink] = useState<string | null>(null);
  const [pending, start] = useTransition();
  return (
    <div className="space-y-2">
      <div className="grid grid-cols-2 gap-2">
        <label className="text-xs text-muted">Scheduled for
          <input type="datetime-local" className="mt-1 w-full rounded border border-line px-2 py-1 text-sm text-ink"
            value={when} onChange={(e) => setWhen(e.target.value)} />
        </label>
        <label className="text-xs text-muted">Owner
          <input className="mt-1 w-full rounded border border-line px-2 py-1 text-sm text-ink" placeholder="you"
            value={owner} onChange={(e) => setOwner(e.target.value)} />
        </label>
      </div>
      <input className="w-full rounded border border-line px-2 py-1 text-sm" placeholder="Notes (optional)"
        value={notes} onChange={(e) => setNotes(e.target.value)} />
      <div className="flex items-center gap-2">
        <button className="btn" disabled={pending}
          onClick={() => { setMsg(null); setLink(null); start(async () => {
            const r = await bookDemo(leadId, { scheduledAt: when || undefined, owner: owner || undefined, notes: notes || undefined });
            if (r.ok) { setMsg(r.meetingUrl ? "Demo booked + calendar invite sent ✓" : "Demo booked ✓"); setLink(r.meetingUrl || null); router.refresh(); }
            else setMsg(`Error: ${r.error}`);
          }); }}>
          {pending ? "Booking…" : "Book demo"}
        </button>
        {msg && <span className="text-xs text-muted">{msg}</span>}
      </div>
      {link && <a href={link} target="_blank" rel="noreferrer" className="text-xs text-accent hover:underline">Open meeting link ↗</a>}
    </div>
  );
}

export function OptOutButton({ leadId, identityKey }: { leadId: number; identityKey: string }) {
  const router = useRouter();
  const [msg, setMsg] = useState<string | null>(null);
  const [pending, start] = useTransition();
  return (
    <span className="flex items-center gap-2">
      <button
        className="btn-ghost"
        disabled={pending}
        onClick={() =>
          start(async () => {
            const r = await suppressFromLead(leadId, identityKey);
            setMsg(r.ok ? "Opted out + suppressed" : `Error: ${r.error}`);
            if (r.ok) router.refresh();
          })
        }
      >
        Opt-out / suppress
      </button>
      {msg && <span className="text-xs text-muted">{msg}</span>}
    </span>
  );
}
