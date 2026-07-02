import Link from "next/link";
import { getServerClient } from "@/lib/supabase/server";
import type { Lead, LeadStatus } from "@/lib/types";
import { ALL_STAGES, STAGE_LABEL, STAGE_CLASS } from "@/lib/stages";
import { applyFilters, LEAD_SOURCES, type LeadFilters } from "./filters";
import { ExportButton } from "./ExportButton";
import { ImportButton } from "./ImportButton";

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
    source: one(sp.source) || undefined,
    spec: one(sp.spec) ? Number(one(sp.spec)) : undefined,
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
      "id,identity_key,segment,niche,platform,follower_band,follower_count,icp_score,priority_rank,status,source,created_at,attributes",
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
    <div className="space-y-5 rise">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Leads</h1>
          <p className="mt-1 text-sm text-muted">
            {total.toLocaleString()} total · sorted by ICP score · page {page} of {pages}
          </p>
        </div>
        <div className="flex gap-2">
          <ImportButton />
          <ExportButton filters={filters} />
        </div>
      </div>

      {/* filter bar — plain GET form, no client JS, server-rendered results */}
      <form className="card grid gap-3 sm:grid-cols-2 lg:grid-cols-7" action="/leads" method="get">
        {filters.spec != null && !Number.isNaN(filters.spec) && (
          <input type="hidden" name="spec" value={filters.spec} />
        )}
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
          <label className="mb-1 block text-xs text-muted">Source</label>
          <select className="input" name="source" defaultValue={filters.source ?? ""}>
            <option value="">All</option>
            {LEAD_SOURCES.map((s) => <option key={s} value={s}>{s === "import" ? "Imported" : s}</option>)}
          </select>
        </div>
        <div>
          <label className="mb-1 block text-xs text-muted">Min ICP score</label>
          <input className="input" type="number" name="minScore" min={0} max={100} defaultValue={filters.minScore ?? ""} placeholder="0" />
        </div>
        <div className="flex items-end gap-2 sm:col-span-2 lg:col-span-7">
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
              <th className="th">Name</th><th className="th">Email</th><th className="th">Phone</th>
              <th className="th">Platform</th><th className="th">Category</th><th className="th">Audience</th>
              <th className="th">Price</th><th className="th">Profile</th><th className="th">Notes</th>
              <th className="th">Score</th><th className="th">Status</th><th className="th">Origin</th>
            </tr>
          </thead>
          <tbody>
            {leads.map((l) => {
              const c = leadContact(l);
              const a = (l.attributes ?? {}) as Record<string, unknown>;
              const s = (k: string) => (a[k] ? String(a[k]) : "");
              const name = s("advertiser") || l.identity_key;
              const audience = s("audience_size") || (l.follower_count != null ? l.follower_count.toLocaleString() : "");
              const profile = s("profile_url");
              const notes = s("notes");
              return (
              <tr key={l.id}>
                <td className="td">
                  <Link href={`/leads/${l.id}`} className="font-medium text-accent hover:underline">{name}</Link>
                </td>
                <td className="td text-xs">
                  {c.email ? <a href={`mailto:${c.email}`} className="text-accent hover:underline">{c.email}</a>
                    : <span className="text-muted">—</span>}
                </td>
                <td className="td text-xs tabular-nums">{c.phone || <span className="text-muted">—</span>}</td>
                <td className="td">{l.platform ?? "—"}</td>
                <td className="td text-xs">{l.niche ?? "—"}</td>
                <td className="td text-xs">{audience || <span className="text-muted">—</span>}</td>
                <td className="td text-xs">{s("price") || <span className="text-muted">—</span>}</td>
                <td className="td text-xs">
                  {profile
                    ? <a href={profile.startsWith("http") ? profile : "https://" + profile} target="_blank" rel="noreferrer" className="text-accent hover:underline">Open ↗</a>
                    : <span className="text-muted">—</span>}
                </td>
                <td className="td max-w-[16rem] truncate text-xs text-muted" title={notes}>{notes || "—"}</td>
                <td className="td tabular-nums font-semibold">{l.icp_score ?? "—"}</td>
                <td className="td"><span className={"pill " + STAGE_CLASS[l.status as LeadStatus]}>{STAGE_LABEL[l.status as LeadStatus]}</span></td>
                <td className="td whitespace-nowrap text-xs">
                  {(() => {
                    const imported = l.source === "import" || a.imported === true;
                    return (
                      <>
                        <span className={"pill " + (imported ? "bg-violet-100 text-violet-700" : "bg-emerald-100 text-emerald-700")}>
                          {imported ? "Imported" : `Scraped${l.source ? ` · ${l.source}` : ""}`}
                        </span>
                        <span className="mt-0.5 block text-[11px] text-muted">{fmtDateTime(l.created_at)}</span>
                      </>
                    );
                  })()}
                </td>
              </tr>
              );
            })}
            {leads.length === 0 && (
              <tr><td className="td text-muted" colSpan={12}>No leads match these filters.</td></tr>
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

/** Best email/phone to show in the list — from attributes (imported + enriched)
 *  or parsed from the identity_key (e.g. "email:hi@x.in" / "phone:+91…"). */
function leadContact(l: Lead): { email?: string; phone?: string } {
  const a = (l.attributes ?? {}) as Record<string, unknown>;
  let email = (a.email as string) || undefined;
  let phone = (a.phone as string) || undefined;
  const key = l.identity_key || "";
  if (!email && key.startsWith("email:")) email = key.slice(6);
  if (!phone && key.startsWith("phone:")) phone = key.slice(6);
  return { email, phone };
}

function filtersToParams(f: LeadFilters): Record<string, string> {
  const o: Record<string, string> = {};
  if (f.q) o.q = f.q;
  if (f.platform) o.platform = f.platform;
  if (f.status) o.status = f.status;
  if (f.segment) o.segment = f.segment;
  if (f.minScore != null && !Number.isNaN(f.minScore)) o.minScore = String(f.minScore);
  if (f.source) o.source = f.source;
  if (f.spec != null && !Number.isNaN(f.spec)) o.spec = String(f.spec);
  return o;
}

/** Date + time, e.g. "2 Jul 2026, 10:55 PM". Falls back to em-dash. */
function fmtDateTime(ts: string | null | undefined): string {
  if (!ts) return "—";
  try {
    return new Date(ts).toLocaleString(undefined, {
      day: "numeric", month: "short", year: "numeric", hour: "numeric", minute: "2-digit",
    });
  } catch { return "—"; }
}
