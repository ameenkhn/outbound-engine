"use client";

import { useState, useTransition } from "react";
import type { TargetSpec, AppJob } from "@/lib/types";
import { runModeB, runModeA, approveSpec, kickSourceRun, kickQuickHarvest } from "./actions";

export function SourcingConsole({
  specs,
  jobs,
  leadCounts,
}: {
  specs: TargetSpec[];
  jobs: AppJob[];
  leadCounts: Record<number, number>;
}) {
  const [seeds, setSeeds] = useState("");
  const [persona, setPersona] = useState("");
  const [qhKeywords, setQhKeywords] = useState("");
  const [qhPlatform, setQhPlatform] = useState<"all" | "meta_ads" | "instagram" | "linkedin" | "youtube" | "websearch">("all");
  const [msg, setMsg] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  function run(fn: () => Promise<{ ok: boolean; jobId?: number; error?: string }>, okText: string) {
    setMsg(null);
    startTransition(async () => {
      const r = await fn();
      setMsg(r.ok ? okText + (r.jobId ? ` (job #${r.jobId})` : "") : `Error: ${r.error}`);
    });
  }

  // Per-source run status, if any spec carries an adapter's status in attributes.
  const SOURCE_STATUS_KEYS = [
    "meta_ads_status",
    "instagram_status",
    "linkedin_status",
    "youtube_status",
  ] as const;
  const sourceStatuses = specs
    .flatMap((s) =>
      SOURCE_STATUS_KEYS.map((k) => {
        const v = (s.attributes as Record<string, unknown>)?.[k];
        return v ? { spec: s.id, source: k.replace("_status", ""), status: v } : null;
      })
    )
    .filter(Boolean) as { spec: number; source: string; status: unknown }[];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">L1 · Sourcing &amp; Targeting</h1>
        <p className="mt-1 text-sm text-muted">
          Turn intent into source queries. Mode B expands keywords and auto-approves; Mode A
          builds a persona spec you sign off. Adapters only source from <b>approved</b> specs.
        </p>
      </div>

      {/* Quick Harvest — scrape straight from keywords, no LLM. The worker runs it. */}
      <div className="card border-accent/40">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold">⚡ Quick Harvest — scrape now from keywords</h2>
          <span className="pill bg-indigo-100 text-indigo-700">no sign-off needed</span>
        </div>
        <p className="mt-1 text-xs text-muted">
          Type a niche, pick sources, and the engine harvests leads straight into your database.
          Requires the worker to be running (Railway / your machine).
        </p>
        <div className="mt-3 flex flex-wrap items-end gap-2">
          <input
            className="input flex-1"
            style={{ minWidth: 220 }}
            placeholder="e.g. fitness coach, yoga teacher"
            value={qhKeywords}
            onChange={(e) => setQhKeywords(e.target.value)}
          />
          <select className="input" style={{ maxWidth: 180 }} value={qhPlatform}
            onChange={(e) => setQhPlatform(e.target.value as typeof qhPlatform)}>
            <option value="all">All sources</option>
            <option value="meta_ads">Meta Ad Library</option>
            <option value="instagram">Instagram</option>
            <option value="linkedin">LinkedIn</option>
            <option value="youtube">YouTube</option>
            <option value="websearch">Web Search</option>
          </select>
          <button
            className="btn"
            disabled={pending}
            onClick={() => run(() => kickQuickHarvest(qhKeywords.split(","), qhPlatform), "Harvest queued — leads will appear in Leads shortly")}
          >
            Harvest now
          </button>
        </div>
      </div>

      {/* the two brain modes */}
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="card">
          <h2 className="text-sm font-semibold">Mode B · keyword expansion (auto-approved)</h2>
          <input
            className="mt-2 w-full rounded border border-line px-2 py-1.5 text-sm"
            placeholder="seed keywords, comma or space separated"
            value={seeds}
            onChange={(e) => setSeeds(e.target.value)}
          />
          <button
            className="btn mt-2"
            disabled={pending}
            onClick={() => run(() => runModeB(seeds.split(/[,\s]+/)), "Expansion queued")}
          >
            Expand &amp; source
          </button>
        </div>

        <div className="card">
          <h2 className="text-sm font-semibold">Mode A · persona (needs sign-off)</h2>
          <textarea
            className="mt-2 w-full rounded border border-line px-2 py-1.5 text-sm"
            rows={2}
            placeholder="e.g. Indian yoga & wellness creators, 50k-500k followers, selling courses"
            value={persona}
            onChange={(e) => setPersona(e.target.value)}
          />
          <button
            className="btn mt-2"
            disabled={pending}
            onClick={() => run(() => runModeA(persona), "Persona spec queued — approve it below once built")}
          >
            Build persona spec
          </button>
        </div>
      </div>

      {msg && <p className="text-sm text-muted">{msg}</p>}

      {sourceStatuses.length > 0 && (
        <div className="card text-sm">
          <span className="font-semibold">Source run status</span>
          <div className="mt-1 flex flex-wrap gap-2">
            {sourceStatuses.map((s, i) => (
              <span key={i} className="pill bg-slate-100 text-muted">
                #{s.spec} · {s.source}: {String(s.status)}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* target specs library */}
      <div className="card overflow-x-auto">
        <h2 className="mb-3 text-sm font-semibold">Target specs ({specs.length})</h2>
        <table className="w-full border-collapse">
          <thead>
            <tr>
              <th className="th">ID</th><th className="th">Mode</th><th className="th">Approved</th>
              <th className="th">Keywords</th><th className="th">Leads</th>
              <th className="th">Created</th><th className="th">Actions</th>
            </tr>
          </thead>
          <tbody>
            {specs.map((s) => (
              <tr key={s.id}>
                <td className="td tabular-nums">{s.id}</td>
                <td className="td">{s.mode}</td>
                <td className="td">
                  {s.approved
                    ? <span className="pill bg-green-100 text-green-700">approved</span>
                    : <span className="pill bg-amber-100 text-amber-700">pending</span>}
                </td>
                <td className="td text-xs text-muted">
                  {s.expanded_keywords?.length ?? 0} expanded
                  {s.seed_keywords?.length ? ` · ${s.seed_keywords.length} seed` : ""}
                </td>
                <td className="td tabular-nums">{leadCounts[s.id] ?? 0}</td>
                <td className="td text-xs text-muted">{new Date(s.created_at).toLocaleDateString()}</td>
                <td className="td">
                  <div className="flex flex-wrap gap-1">
                    {!s.approved && (
                      <button className="btn-ghost" disabled={pending}
                        onClick={() => run(() => approveSpec(s.id), `Spec #${s.id} approved`)}>
                        Approve
                      </button>
                    )}
                    <button className="btn-ghost" disabled={pending || !s.approved}
                      onClick={() => run(() => kickSourceRun(s.id, "meta_ads"), `Meta run queued for #${s.id}`)}>
                      Meta
                    </button>
                    <button className="btn-ghost" disabled={pending || !s.approved}
                      onClick={() => run(() => kickSourceRun(s.id, "instagram"), `Instagram run queued for #${s.id}`)}>
                      Instagram
                    </button>
                    <button className="btn-ghost" disabled={pending || !s.approved}
                      onClick={() => run(() => kickSourceRun(s.id, "linkedin"), `LinkedIn run queued for #${s.id}`)}>
                      LinkedIn
                    </button>
                    <button className="btn-ghost" disabled={pending || !s.approved}
                      onClick={() => run(() => kickSourceRun(s.id, "youtube"), `YouTube run queued for #${s.id}`)}>
                      YouTube
                    </button>
                    <button className="btn" disabled={pending || !s.approved}
                      onClick={() => run(() => kickSourceRun(s.id, "all"), `All-source run queued for #${s.id}`)}>
                      Run all
                    </button>
                  </div>
                </td>
              </tr>
            ))}
            {specs.length === 0 && (
              <tr><td className="td text-muted" colSpan={7}>No specs yet — run Mode B or Mode A above.</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {/* recent runs */}
      <div className="card overflow-x-auto">
        <h2 className="mb-3 text-sm font-semibold">Recent runs</h2>
        <table className="w-full border-collapse">
          <thead>
            <tr><th className="th">Job</th><th className="th">Kind</th><th className="th">Status</th><th className="th">Result / error</th><th className="th">When</th></tr>
          </thead>
          <tbody>
            {jobs.map((j) => (
              <tr key={j.id}>
                <td className="td tabular-nums">#{j.id}</td>
                <td className="td">{j.kind}</td>
                <td className="td">
                  <span className={"pill " + statusClass(j.status)}>{j.status}</span>
                </td>
                <td className="td text-xs text-muted">
                  {j.last_error ?? (j.result ? JSON.stringify(j.result) : "—")}
                </td>
                <td className="td text-xs text-muted">{new Date(j.created_at).toLocaleString()}</td>
              </tr>
            ))}
            {jobs.length === 0 && (
              <tr><td className="td text-muted" colSpan={5}>No runs yet.</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function statusClass(s: string): string {
  if (s === "done") return "bg-green-100 text-green-700";
  if (s === "failed") return "bg-red-100 text-red-700";
  if (s === "claimed") return "bg-blue-100 text-blue-700";
  return "bg-slate-100 text-muted";
}
