"use client";

import { useState, useTransition, useEffect } from "react";
import type { TargetSpec, AppJob } from "@/lib/types";
import {
  runModeB, runModeA, approveSpec, kickSourceRun, kickQuickHarvest,
  getJobStatus, type JobSnapshot,
} from "./actions";

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
  const [qhLimit, setQhLimit] = useState("100");
  const [msg, setMsg] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();
  const [liveJob, setLiveJob] = useState<JobSnapshot | null>(null);

  // Live status: while a Quick Harvest job is pending/claimed, poll it every 2s
  // so the user sees Queued → Running → Done with the per-source counts.
  useEffect(() => {
    if (!liveJob || liveJob.status === "done" || liveJob.status === "failed") return;
    const t = setInterval(async () => {
      const r = await getJobStatus(liveJob.id);
      if (r.ok) setLiveJob(r.job);
    }, 2000);
    return () => clearInterval(t);
  }, [liveJob]);

  function quickHarvest() {
    setMsg(null);
    setLiveJob(null);
    startTransition(async () => {
      const r = await kickQuickHarvest(qhKeywords.split(","), qhPlatform, parseInt(qhLimit) || 0);
      if (!r.ok) { setMsg("Error: " + r.error); return; }
      setLiveJob({ id: r.jobId!, status: "pending", result: null, last_error: null });
    });
  }

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
      <div className="card border-accent/30 rise"
        style={{ background: "linear-gradient(180deg, rgb(var(--accent)/0.05), transparent 55%)" }}>
        <div className="flex items-center justify-between">
          <h2 className="flex items-center gap-2 text-base font-semibold">
            <span className="grad-text">⚡ Quick Harvest</span>
            <span className="text-sm font-normal text-muted">— scrape now from keywords</span>
          </h2>
          <span className="pill bg-indigo-100 text-indigo-700">no sign-off needed</span>
        </div>
        <p className="mt-1.5 text-xs text-muted">
          Type a niche, choose your sources and how many leads you want, and the engine
          harvests them straight into your database.
        </p>
        <div className="mt-4 grid gap-3 lg:grid-cols-[1fr_auto_auto_auto]">
          <div>
            <label className="mb-1 block text-[11px] font-medium uppercase tracking-wide text-muted">Niche / keywords</label>
            <input className="input" placeholder="e.g. fitness coach, yoga teacher"
              value={qhKeywords} onChange={(e) => setQhKeywords(e.target.value)} />
          </div>
          <div>
            <label className="mb-1 block text-[11px] font-medium uppercase tracking-wide text-muted">Sources</label>
            <select className="input lg:w-44" value={qhPlatform}
              onChange={(e) => setQhPlatform(e.target.value as typeof qhPlatform)}>
              <option value="all">All sources</option>
              <option value="meta_ads">Meta Ad Library</option>
              <option value="instagram">Instagram</option>
              <option value="linkedin">LinkedIn</option>
              <option value="youtube">YouTube</option>
              <option value="websearch">Web Search</option>
            </select>
          </div>
          <div>
            <label className="mb-1 block text-[11px] font-medium uppercase tracking-wide text-muted">Max leads</label>
            <input className="input lg:w-28" type="number" min={1} max={5000} step={10}
              value={qhLimit} onChange={(e) => setQhLimit(e.target.value)} />
          </div>
          <div className="flex items-end">
            <button className="btn h-[38px] w-full lg:w-auto" disabled={pending} onClick={quickHarvest}>
              Harvest now
            </button>
          </div>
        </div>

        {liveJob && <LiveHarvest job={liveJob} />}
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

/** Live status panel for a Quick Harvest job: Queued → Running → Done + counts. */
function LiveHarvest({ job }: { job: JobSnapshot }) {
  const r = (job.result ?? {}) as Record<string, unknown>;
  const per = (r.per_source ?? {}) as Record<string, number>;

  const phase =
    job.status === "pending" ? { label: "Queued — waiting for the worker…", cls: "bg-slate-100 text-muted", spin: true }
    : job.status === "claimed" ? { label: "Scraping now…", cls: "bg-blue-100 text-blue-700", spin: true }
    : job.status === "failed" ? { label: "Failed", cls: "bg-red-100 text-red-700", spin: false }
    : { label: "Done", cls: "bg-green-100 text-green-700", spin: false };

  return (
    <div className="mt-3 rounded-lg border border-line bg-bg p-3 text-sm">
      <div className="flex items-center gap-2">
        {phase.spin && (
          <span className="inline-block h-3.5 w-3.5 animate-spin rounded-full border-2 border-line border-t-accent" />
        )}
        <span className={"pill " + phase.cls}>{phase.label}</span>
        <span className="text-xs text-muted">job #{job.id}</span>
      </div>

      {job.status === "done" && (
        <div className="mt-2">
          <span className="font-semibold text-ink">
            {(r.created as number) ?? 0} new · {(r.merged as number) ?? 0} updated
          </span>
          {Object.keys(per).length > 0 && (
            <span className="ml-2 text-xs text-muted">
              ({Object.entries(per).map(([k, v]) => `${k}: ${v}`).join(" · ")})
            </span>
          )}
          <a href="/leads" className="ml-2 text-accent hover:underline">View leads →</a>
        </div>
      )}
      {job.status === "failed" && job.last_error && (
        <p className="mt-2 text-xs text-red-600">{job.last_error}</p>
      )}
      {(job.status === "pending" || job.status === "claimed") && (
        <p className="mt-1.5 text-xs text-muted">
          Watch the detailed line-by-line scrape in your Railway logs. This updates automatically.
        </p>
      )}
    </div>
  );
}
