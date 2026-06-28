import Link from "next/link";
import { getServerClient } from "@/lib/supabase/server";
import type { Lead, LeadStatus } from "@/lib/types";
import { ALL_STAGES, STAGE_LABEL, STAGE_CLASS } from "@/lib/stages";
import { applyFilters, type LeadFilters } from "./filters";
import { ExportButton } from "./ExportButton";

// Always read live (this is an ops console; freshness > cache here).
export const dynamic = "force-dynamic";

const PAGE_SIZE = 50;
const PLATFORMS = ["meta", "instagram", "linkedin", "youtube"];
const SEGMENTS = ["creator", "affiliate"];

type SP = Record<string, string | string[] | undefined>;
const one = (v: string | string[] | undefined) => (Array.isArray(v) ? v[0] : v);

export default async function LeadsPage({
  searchParams,
}: {
  searchParams: Promise<SP>;
}) {
  const sp = await searchParams;

  const filters: LeadFilters = {
    q: one(sp.q) || undefined,
    platform: one(sp.platform) || undefined,
    status: one(sp.status) || undefined,
    segment: one(sp.segment) || undefined,
    minScore: one(sp.minScore) ? Number(one(sp.minScore)) : undefined,
  };
  const page = Math.max(1, Number(one(sp.page) || "1") || 1);
  const from = (page - 1) * PAGE_SIZE;
  const to = from + PAGE_SIZE - 1;

  let supa;
  try {
    supa = getServerClient();
  } catch (e) {
    return (
      <div className="card border-amber-300 bg-amber-50">
        <h2 className="font-medium text-amber-900">Not connected to Supabase</h2>
        <p className="mt-1 text-sm text-amber-800">{(e as Error).message}</p>
      </div>
    );
  }

  // One paginated read; only the columns the table needs. count: exact for the pager.
  let query = supa
    .from("leads")
    .select(
      "id,identity_key,segment,niche,platform,follower_band,follower_count,icp_score,priority_rank,status,source,created_at",
      { count: "exact" },
    )
    .order("icp_score", { ascending: false, nullsFirst: false })
    .order("id", { ascending: true })
    .range(from, to);
  query = applyFilters(query, filters);

  const { data, count, error } = await query;
  const leads = (data ?? []) as unknown as Lead[];
  const total = count ?? 0;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  const qs = (over: Record<string, string | number | undefined>) => {
    const p = new URLSearchParams();
    const merged = { ...filtersToParams(filters), page: String(page), ...over };
    for (const [k, v] of Object.entries(merged)) {
      if (v !== undefined && v !== "" && v !== null) p.set(k, String(v));
    }
    return "/leads?" + p.toString();
  };

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Leads</h1>
          <p className="mt-1 text-sm text-muted">
            {total.toLocaleString()} total · sorted by ICP score · page {page} of {pages}
          </p>
        </div>
        <ExportButton filters={filters} />
      </div>

      {/* filter bar — plain GET form, no client JS, server-rendered results */}
      <form className="card grid gap-3 sm:grid-cols-2 lg:grid-cols-6" action="/leads" method="get">
        <div className="lg:col-span-2">
          <label className="mb-1 block text-xs text-muted">Search (identity / niche)</label>
          <input className="input" type="text" name="q" defaultValue={filters.q ?? ""} placeholder="e.g. yoga, @handle" />
        </div>
        <div>
          <label className="mb-1 block text-xs text-muted">Platform</label>
          <select className="input" name="platform" defaultValue={filters.platform ?? ""}>
            <option value="">All</option>
            {PLATFORMS.map((p) => <option key={p} value={p}>{p}</option>)}
          </select>
        </div>
        <div>
          <label className="mb-1 block text-xs text-muted">Status</label>
          <select className="input" name="status" defaultValue={filters.status ?? ""}>
            <option value="">All</option>
            {ALL_STAGES.map((s) => <option key={s} value={s}>{STAGE_LABEL[s]}</option>)}
          </select>
        </div>
        <div>
          <label className="mb-1 block text-xs text-muted">Segment</label>
          <select className="input" name="segment" defaultValue={filters.segment ?? ""}>
            <option value="">All</option>
            {SEGMENTS.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        </div>
        <div>
          <label className="mb-1 block text-xs text-muted">Min ICP score</label>
          <input className="input" type="number" name="minScore" min={0} max={100} defaultValue={filters.minScore ?? ""} placeholder="0" />
        </div>
        <div className="flex items-end gap-2 sm:col-span-2 lg:col-span-6">
          <button className="btn" type="submit">Apply filters</button>
          <Link className="btn-ghost" href="/leads">Reset</Link>
        </div>
      </form>

      {error && (
        <div className="card border-red-300 text-sm text-red-600">Query error: {error.message}</div>
      )}

      <div className="card overflow-x-auto">
        <table className="w-full border-collapse">
          <thead>
            <tr>
              <th className="th">Lead</th><th className="th">Platform</th><th className="th">Segment</th>
              <th className="th">Niche</th><th className="th">Followers</th><th className="th">Score</th>
              <th className="th">Rank</th><th className="th">Status</th><th className="th">Source</th>
            </tr>
          </thead>
          <tbody>
            {leads.map((l) => (
              <tr key={l.id}>
                <td className="td font-mono text-xs">
                  <Link href={`/leads/${l.id}`} className="text-accent hover:underline">{l.identity_key}</Link>
                </td>
                <td className="td">{l.platform ?? "—"}</td>
                <td className="td">{l.segment ?? "—"}</td>
                <td className="td">{l.niche ?? "—"}</td>
                <td className="td tabular-nums">
                  {l.follower_count != null ? l.follower_count.toLocaleString() : "—"}
                  {l.follower_band ? <span className="text-muted"> {l.follower_band}</span> : null}
                </td>
                <td className="td tabular-nums font-semibold">{l.icp_score ?? "—"}</td>
                <td className="td tabular-nums text-muted">{l.priority_rank ?? "—"}</td>
                <td className="td"><span className={"pill " + STAGE_CLASS[l.status as LeadStatus]}>{STAGE_LABEL[l.status as LeadStatus]}</span></td>
                <td className="td text-xs text-muted">{l.source ?? "—"}</td>
              </tr>
            ))}
            {leads.length === 0 && (
              <tr><td className="td text-muted" colSpan={9}>No leads match these filters.</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {/* pager */}
      <div className="flex items-center justify-between text-sm">
        <span className="text-muted">
          Showing {total === 0 ? 0 : from + 1}–{Math.min(to + 1, total)} of {total.toLocaleString()}
        </span>
        <div className="flex gap-2">
          {page > 1
            ? <Link className="btn-ghost" href={qs({ page: page - 1 })}>← Prev</Link>
            : <span className="btn-ghost opacity-50">← Prev</span>}
          {page < pages
            ? <Link className="btn-ghost" href={qs({ page: page + 1 })}>Next →</Link>
            : <span className="btn-ghost opacity-50">Next →</span>}
        </div>
      </div>
    </div>
  );
}

function filtersToParams(f: LeadFilters): Record<string, string> {
  const o: Record<string, string> = {};
  if (f.q) o.q = f.q;
  if (f.platform) o.platform = f.platform;
  if (f.status) o.status = f.status;
  if (f.segment) o.segment = f.segment;
  if (f.minScore != null && !Number.isNaN(f.minScore)) o.minScore = String(f.minScore);
  return o;
}
