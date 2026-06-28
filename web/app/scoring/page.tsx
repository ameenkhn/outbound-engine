import { getServerClient } from "@/lib/supabase/server";
import type { Lead, ScoringConfig } from "@/lib/types";
import { WeightsPanel } from "./WeightsPanel";

// Reads happen per-request against live Postgres; never statically prerendered.
export const dynamic = "force-dynamic";

type QueueLead = Pick<
  Lead,
  "id" | "identity_key" | "segment" | "niche" | "platform" | "follower_band" |
  "follower_count" | "icp_score" | "priority_rank" | "status" | "attributes"
>;

function signalChips(l: QueueLead): string[] {
  const a = (l.attributes ?? {}) as Record<string, unknown>;
  const chips: string[] = [];
  if (a.ad_text) chips.push("ad_text");
  if (a.category) chips.push("category");
  if (a.socials) chips.push("social");
  if (l.follower_band) chips.push(l.follower_band);
  if (l.segment) chips.push(l.segment);
  return chips;
}

export default async function ScoringPage() {
  let supa;
  try {
    supa = getServerClient();
  } catch (e) {
    return <EnvError msg={(e as Error).message} />;
  }

  // 1. Score distribution: pull scores (capped) and bucket in JS.
  const { data: scoreRows } = await supa
    .from("leads")
    .select("icp_score")
    .not("status", "in", "(dead,opted_out)")
    .limit(10000);
  const scores = (scoreRows ?? []).map((r) => r.icp_score as number | null);

  const total = scores.length;
  const scored = scores.filter((s) => s !== null).length;
  const gateFailed = scores.filter((s) => s === 0).length;
  const eligible = scores.filter((s): s is number => s !== null && s > 0);
  const avg = eligible.length
    ? Math.round(eligible.reduce((a, b) => a + b, 0) / eligible.length)
    : 0;

  // deciles 1-10 .. 91-100 (gate-failed 0 shown separately)
  const buckets = Array.from({ length: 10 }, (_, i) => ({
    label: `${i * 10 + 1}-${i * 10 + 10}`,
    count: eligible.filter((s) => s > i * 10 && s <= i * 10 + 10).length,
  }));
  const maxBucket = Math.max(1, ...buckets.map((b) => b.count));

  // 2. Priority queue — what the dispatcher works next.
  const { data: queue } = await supa
    .from("leads")
    .select(
      "id,identity_key,segment,niche,platform,follower_band,follower_count,icp_score,priority_rank,status,attributes",
    )
    .in("status", ["new", "queued"])
    .gt("icp_score", 0)
    .order("priority_rank", { ascending: true })
    .limit(25);

  // 3. Gate-failed sample (score 0 = excluded from dispatch).
  const { data: gated } = await supa
    .from("leads")
    .select("id,identity_key,segment,niche,geo,follower_count")
    .eq("icp_score", 0)
    .limit(15);

  // 4. The editable weights.
  const { data: cfg } = await supa.from("scoring_config").select("*").eq("id", 1).single();

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">L2 · Enrichment &amp; Scoring</h1>
        <p className="mt-1 text-sm text-muted">
          Rules-based ICP score (0-100). The dispatcher works leads in{" "}
          <code>priority_rank</code> order. Gate-failed leads (no reachable channel or
          geo≠IN) score 0 and are never dispatched.
        </p>
      </div>

      {/* summary strip */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat label="Leads (active)" value={total} />
        <Stat label="Scored" value={scored} />
        <Stat label="Avg score (eligible)" value={avg} />
        <Stat label="Gate-failed (0)" value={gateFailed} />
      </div>

      {/* distribution */}
      <div className="card">
        <h2 className="mb-3 text-sm font-semibold">ICP score distribution (eligible)</h2>
        <div className="flex items-end gap-1" style={{ height: 120 }}>
          {buckets.map((b) => (
            <div key={b.label} className="flex flex-1 flex-col items-center justify-end">
              <div
                className="w-full rounded-t bg-accent/80"
                style={{ height: `${(b.count / maxBucket) * 100}%` }}
                title={`${b.label}: ${b.count}`}
              />
              <span className="mt-1 text-[10px] text-muted">{b.label}</span>
            </div>
          ))}
        </div>
      </div>

      {/* weights panel + rescore (client) */}
      <WeightsPanel config={(cfg as ScoringConfig) ?? null} />

      {/* priority queue */}
      <div className="card overflow-x-auto">
        <h2 className="mb-3 text-sm font-semibold">
          Priority queue — next {queue?.length ?? 0} leads the dispatcher will work
        </h2>
        <table className="w-full border-collapse">
          <thead>
            <tr>
              <th className="th">Rank</th><th className="th">Score</th><th className="th">Lead</th>
              <th className="th">Segment</th><th className="th">Niche</th>
              <th className="th">Followers</th><th className="th">Signals</th>
            </tr>
          </thead>
          <tbody>
            {(queue as QueueLead[] | null ?? []).map((l) => (
              <tr key={l.id}>
                <td className="td tabular-nums">{l.priority_rank ?? "—"}</td>
                <td className="td">
                  <span className="pill bg-accent/10 text-accent tabular-nums">{l.icp_score}</span>
                </td>
                <td className="td font-mono text-xs">{l.identity_key}</td>
                <td className="td">{l.segment ?? "—"}</td>
                <td className="td">{l.niche ?? "—"}</td>
                <td className="td tabular-nums">{l.follower_count?.toLocaleString() ?? "—"}</td>
                <td className="td">
                  <div className="flex flex-wrap gap-1">
                    {signalChips(l).map((c) => (
                      <span key={c} className="pill bg-slate-100 text-muted">{c}</span>
                    ))}
                  </div>
                </td>
              </tr>
            ))}
            {(!queue || queue.length === 0) && (
              <tr><td className="td text-muted" colSpan={7}>No eligible leads yet — source some, then re-score.</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {/* gate-failed */}
      <div className="card overflow-x-auto">
        <h2 className="mb-3 text-sm font-semibold">Gate-failed (score 0, excluded)</h2>
        <table className="w-full border-collapse">
          <thead>
            <tr><th className="th">Lead</th><th className="th">Segment</th><th className="th">Niche</th><th className="th">Geo</th><th className="th">Likely reason</th></tr>
          </thead>
          <tbody>
            {(gated ?? []).map((l) => (
              <tr key={l.id as number}>
                <td className="td font-mono text-xs">{l.identity_key as string}</td>
                <td className="td">{(l.segment as string) ?? "—"}</td>
                <td className="td">{(l.niche as string) ?? "—"}</td>
                <td className="td">{(l.geo as string) ?? "—"}</td>
                <td className="td text-muted">
                  {(l.geo as string) !== "IN" ? "geo ≠ IN" : "no reachable channel"}
                </td>
              </tr>
            ))}
            {(!gated || gated.length === 0) && (
              <tr><td className="td text-muted" colSpan={5}>No gate-failed leads.</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="card">
      <div className="text-2xl font-semibold tabular-nums">{value.toLocaleString()}</div>
      <div className="mt-1 text-xs text-muted">{label}</div>
    </div>
  );
}

function EnvError({ msg }: { msg: string }) {
  return (
    <div className="card border-amber-300 bg-amber-50">
      <h2 className="font-medium text-amber-900">Not connected to Supabase</h2>
      <p className="mt-1 text-sm text-amber-800">{msg}</p>
      <p className="mt-2 text-sm text-amber-800">Copy <code>.env.example</code> → <code>.env.local</code> with your rotated keys, then restart.</p>
    </div>
  );
}
