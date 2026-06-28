"use client";

import { useMemo, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import type { LeadStatus, Segment } from "@/lib/types";
import { ALL_STAGES, STAGE_LABEL, STAGE_CLASS } from "@/lib/stages";
import { setStatus, markDead, suppressLead } from "./actions";

export interface BoardLead {
  id: number;
  identity_key: string;
  segment: Segment | null;
  niche: string | null;
  platform: string | null;
  follower_count: number | null;
  icp_score: number | null;
  priority_rank: number | null;
  status: LeadStatus;
  source: string | null;
}

type Band = "all" | "gate" | "low" | "mid" | "high";

function inBand(score: number | null, band: Band): boolean {
  if (band === "all") return true;
  const s = score ?? -1;
  if (band === "gate") return s === 0;
  if (band === "low") return s >= 1 && s <= 40;
  if (band === "mid") return s >= 41 && s <= 70;
  return s >= 71;
}

export function PipelineBoard({ leads }: { leads: BoardLead[] }) {
  const router = useRouter();
  const [seg, setSeg] = useState<"all" | Segment>("all");
  const [band, setBand] = useState<Band>("all");
  const [source, setSource] = useState<string>("all");
  const [view, setView] = useState<"board" | "table">("board");
  const [msg, setMsg] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  const sources = useMemo(
    () => Array.from(new Set(leads.map((l) => l.source).filter(Boolean))) as string[],
    [leads],
  );

  const filtered = useMemo(
    () =>
      leads.filter(
        (l) =>
          (seg === "all" || l.segment === seg) &&
          inBand(l.icp_score, band) &&
          (source === "all" || l.source === source),
      ),
    [leads, seg, band, source],
  );

  const byStage = useMemo(() => {
    const m: Record<string, BoardLead[]> = {};
    for (const s of ALL_STAGES) m[s] = [];
    for (const l of filtered) (m[l.status] ??= []).push(l);
    return m;
  }, [filtered]);

  function act(fn: () => Promise<{ ok: boolean; error?: string }>, okText: string) {
    setMsg(null);
    startTransition(async () => {
      const r = await fn();
      if (r.ok) {
        setMsg(okText);
        router.refresh();
      } else {
        setMsg(`Error: ${r.error}`);
      }
    });
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <h1 className="text-xl font-semibold">Pipeline</h1>
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <Select label="Segment" value={seg} onChange={(v) => setSeg(v as "all" | Segment)}
            options={[["all", "All"], ["creator", "Creator"], ["affiliate", "Affiliate"]]} />
          <Select label="Score" value={band} onChange={(v) => setBand(v as Band)}
            options={[["all", "All"], ["high", "71-100"], ["mid", "41-70"], ["low", "1-40"], ["gate", "0 (gated)"]]} />
          <Select label="Source" value={source} onChange={setSource}
            options={[["all", "All"], ...sources.map((s) => [s, s] as [string, string])]} />
          <button className="btn-ghost" onClick={() => setView(view === "board" ? "table" : "board")}>
            {view === "board" ? "Table view" : "Board view"}
          </button>
        </div>
      </div>

      <p className="text-xs text-muted">
        {filtered.length} of {leads.length} leads shown. Stage changes, dead, and opt-out write
        straight to Postgres. {msg && <span className="ml-2 text-ink">· {msg}</span>}
      </p>

      {view === "board" ? (
        <div className="flex gap-3 overflow-x-auto pb-3">
          {ALL_STAGES.map((s) => (
            <div key={s} className="w-64 shrink-0">
              <div className="mb-2 flex items-center justify-between">
                <span className={"pill " + STAGE_CLASS[s]}>{STAGE_LABEL[s]}</span>
                <span className="text-xs text-muted tabular-nums">{byStage[s]?.length ?? 0}</span>
              </div>
              <div className="space-y-2">
                {(byStage[s] ?? []).slice(0, 50).map((l) => (
                  <Card key={l.id} lead={l} pending={pending} act={act} />
                ))}
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="card overflow-x-auto">
          <table className="w-full border-collapse">
            <thead>
              <tr><th className="th">Rank</th><th className="th">Score</th><th className="th">Lead</th><th className="th">Stage</th><th className="th">Segment</th><th className="th">Niche</th><th className="th">Source</th><th className="th">Actions</th></tr>
            </thead>
            <tbody>
              {filtered.slice(0, 300).map((l) => (
                <tr key={l.id}>
                  <td className="td tabular-nums">{l.priority_rank ?? "—"}</td>
                  <td className="td tabular-nums">{l.icp_score ?? "—"}</td>
                  <td className="td font-mono text-xs"><Link href={`/leads/${l.id}`} className="text-accent hover:underline">{l.identity_key}</Link></td>
                  <td className="td"><span className={"pill " + STAGE_CLASS[l.status]}>{STAGE_LABEL[l.status]}</span></td>
                  <td className="td">{l.segment ?? "—"}</td>
                  <td className="td">{l.niche ?? "—"}</td>
                  <td className="td">{l.source ?? "—"}</td>
                  <td className="td"><RowActions lead={l} pending={pending} act={act} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function Card({ lead, pending, act }: { lead: BoardLead; pending: boolean; act: ActFn }) {
  return (
    <div className="card p-3">
      <div className="flex items-start justify-between gap-2">
        <Link href={`/leads/${lead.id}`} className="font-mono text-[11px] leading-tight text-accent hover:underline">{lead.identity_key}</Link>
        <span className="pill bg-accent/10 text-accent tabular-nums">{lead.icp_score ?? "—"}</span>
      </div>
      <div className="mt-1 text-[11px] text-muted">
        {lead.segment ?? "—"}{lead.niche ? ` · ${lead.niche}` : ""}
        {lead.follower_count ? ` · ${lead.follower_count.toLocaleString()}` : ""}
      </div>
      <div className="mt-2"><RowActions lead={lead} pending={pending} act={act} /></div>
    </div>
  );
}

function RowActions({ lead, pending, act }: { lead: BoardLead; pending: boolean; act: ActFn }) {
  return (
    <div className="flex flex-wrap items-center gap-1">
      <select
        className="rounded border border-line px-1 py-0.5 text-xs"
        value={lead.status}
        disabled={pending}
        onChange={(e) => act(() => setStatus(lead.id, e.target.value as LeadStatus), `#${lead.id} → ${e.target.value}`)}
      >
        {ALL_STAGES.map((s) => <option key={s} value={s}>{STAGE_LABEL[s]}</option>)}
      </select>
      <button className="btn-ghost px-2 py-0.5 text-xs" disabled={pending}
        onClick={() => act(() => markDead(lead.id), `#${lead.id} dead`)}>Dead</button>
      <button className="btn-ghost px-2 py-0.5 text-xs" disabled={pending}
        onClick={() => act(() => suppressLead(lead.id, lead.identity_key), `#${lead.id} opted out`)}>Opt-out</button>
    </div>
  );
}

type ActFn = (fn: () => Promise<{ ok: boolean; error?: string }>, okText: string) => void;

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
