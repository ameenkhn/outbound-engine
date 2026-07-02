"use client";

import { useMemo, useState, useTransition } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { setOutreachStatus } from "./actions";

export type OutreachRow = {
  id: number;
  lead_id: number | null;
  lead_name: string;
  niche: string | null;
  channel: string;
  to_handle: string;
  subject: string | null;
  body: string;
  status: string;
  error: string | null;
  created_at: string;
};

const STATUS_CLASS: Record<string, string> = {
  sent: "bg-green-100 text-green-700",
  failed: "bg-red-100 text-red-700",
  replied: "bg-indigo-100 text-indigo-700",
};

export function OutreachClient({ rows }: { rows: OutreachRow[] }) {
  const router = useRouter();
  const [channel, setChannel] = useState<"all" | "whatsapp" | "email">("all");
  const [status, setStatus] = useState<"all" | "sent" | "failed" | "replied">("all");
  const [niche, setNiche] = useState<string>("all");
  const [msg, setMsg] = useState<string | null>(null);
  const [pending, start] = useTransition();

  const niches = useMemo(
    () => Array.from(new Set(rows.map((r) => r.niche).filter(Boolean))) as string[],
    [rows],
  );

  const filtered = useMemo(
    () =>
      rows.filter(
        (r) =>
          (channel === "all" || r.channel === channel) &&
          (status === "all" || r.status === status) &&
          (niche === "all" || r.niche === niche),
      ),
    [rows, channel, status, niche],
  );

  const sent = filtered.filter((r) => r.status === "sent").length;
  const replied = filtered.filter((r) => r.status === "replied").length;
  const failed = filtered.filter((r) => r.status === "failed").length;
  const reachable = sent + replied;
  const rate = reachable > 0 ? Math.round((replied / reachable) * 100) : 0;

  function mark(id: number, to: "sent" | "replied") {
    setMsg(null);
    start(async () => {
      const r = await setOutreachStatus(id, to);
      if (r.ok) { setMsg(to === "replied" ? "Marked replied ✓" : "Reverted ✓"); router.refresh(); }
      else setMsg(`Error: ${r.error}`);
    });
  }

  return (
    <div className="space-y-6 rise">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Outreach</h1>
          <p className="mt-1 text-sm text-muted">Every WhatsApp &amp; email your platform has sent — the CRM activity log.</p>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <Select label="Channel" value={channel} onChange={(v) => setChannel(v as typeof channel)}
            options={[["all", "All"], ["whatsapp", "WhatsApp"], ["email", "Email"]]} />
          <Select label="Status" value={status} onChange={(v) => setStatus(v as typeof status)}
            options={[["all", "All"], ["sent", "Sent"], ["replied", "Replied"], ["failed", "Failed"]]} />
          <Select label="Niche" value={niche} onChange={setNiche}
            options={[["all", "All"], ...niches.map((n) => [n, n] as [string, string])]} />
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat label="Sent" value={sent} accent />
        <Stat label="Replied" value={replied} />
        <Stat label="Reply rate" value={`${rate}%`} />
        <Stat label="Failed" value={failed} warn={failed > 0} />
      </div>

      <p className="text-xs text-muted">
        {filtered.length} of {rows.length} messages shown.
        {msg && <span className="ml-2 text-ink">· {msg}</span>}
      </p>

      <div className="card overflow-x-auto">
        <table className="w-full border-collapse">
          <thead>
            <tr>
              <th className="th">Lead</th><th className="th">Channel</th><th className="th">To</th>
              <th className="th">Message</th><th className="th">Status</th><th className="th">When</th><th className="th"></th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((r) => (
              <tr key={r.id}>
                <td className="td text-sm">
                  {r.lead_id
                    ? <Link href={`/leads/${r.lead_id}`} className="font-medium text-accent hover:underline">{r.lead_name}</Link>
                    : <span className="text-muted">—</span>}
                  {r.niche && <span className="block text-[11px] text-muted">{r.niche}</span>}
                </td>
                <td className="td">
                  <span className={"pill " + (r.channel === "whatsapp" ? "bg-green-100 text-green-700" : "bg-indigo-100 text-indigo-700")}>{r.channel}</span>
                </td>
                <td className="td text-xs">{r.to_handle}</td>
                <td className="td max-w-[22rem] truncate text-xs text-muted" title={r.body}>
                  {r.subject ? <b className="text-ink">{r.subject} · </b> : null}{r.body}
                </td>
                <td className="td">
                  <span className={"pill " + (STATUS_CLASS[r.status] || "bg-slate-100 text-muted")}>{r.status}</span>
                  {r.error ? <span className="ml-1 text-[10px] text-red-500" title={r.error}>ⓘ</span> : null}
                </td>
                <td className="td text-xs text-muted">{new Date(r.created_at).toLocaleString()}</td>
                <td className="td">
                  {r.status === "sent" && (
                    <button className="btn-ghost px-2 py-0.5 text-xs" disabled={pending}
                      onClick={() => mark(r.id, "replied")}>Mark replied</button>
                  )}
                  {r.status === "replied" && (
                    <button className="btn-ghost px-2 py-0.5 text-xs" disabled={pending}
                      onClick={() => mark(r.id, "sent")}>Undo</button>
                  )}
                </td>
              </tr>
            ))}
            {filtered.length === 0 && (
              <tr><td className="td text-muted" colSpan={7}>
                {rows.length === 0 ? "No messages sent yet. Head to Compose to send your first." : "No messages match these filters."}
              </td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Stat({ label, value, accent, warn }: { label: string; value: number | string; accent?: boolean; warn?: boolean }) {
  return (
    <div className={"card relative overflow-hidden " + (warn ? "border-red-300" : "")}>
      {accent && <div className="absolute inset-x-0 top-0 h-1" style={{ backgroundImage: "linear-gradient(90deg, rgb(var(--accent)), rgb(var(--accent-2)))" }} />}
      <div className={"text-3xl font-bold tabular-nums " + (accent ? "grad-text" : warn ? "text-red-600" : "")}>{value}</div>
      <div className="mt-1 text-xs font-medium text-muted">{label}</div>
    </div>
  );
}

function Select({ label, value, onChange, options }: {
  label: string; value: string; onChange: (v: string) => void; options: [string, string][];
}) {
  return (
    <label className="flex items-center gap-1 text-xs text-muted">
      {label}
      <select className="rounded border border-line px-2 py-1 text-sm text-ink" value={value} onChange={(e) => onChange(e.target.value)}>
        {options.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
      </select>
    </label>
  );
}
