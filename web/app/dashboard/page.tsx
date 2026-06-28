import Link from "next/link";
import { getServerClient } from "@/lib/supabase/server";
import type { LeadStatus } from "@/lib/types";
import { FUNNEL_STAGES, LEAKAGE_STAGES, STAGE_LABEL, STAGE_CLASS } from "@/lib/stages";

export const dynamic = "force-dynamic";

const DAY = 24 * 60 * 60 * 1000;

export default async function DashboardPage() {
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

  const now = new Date();

  // Fire every read in PARALLEL. The DB is far away, so sequential awaits stack
  // latency (6 round-trips back-to-back); Promise.all collapses them into a
  // single wall-clock wait — the main reason the dashboard felt slow.
  const [
    { data: leadRows },
    { data: convRows },
    { data: msgRows },
    { count: complaintCount },
    { count: suppressionCount },
    { data: awaiting },
  ] = await Promise.all([
    supa.from("leads").select("status,segment,created_at").limit(10000),
    supa.from("conversions").select("demo_booked_at,status,created_at")
      .not("demo_booked_at", "is", null).limit(10000),
    supa.from("messages").select("delivery_status,sent_at").limit(10000),
    supa.from("events").select("*", { count: "exact", head: true }).eq("type", "complaint"),
    supa.from("suppression").select("*", { count: "exact", head: true }),
    supa.from("leads").select("id,identity_key,segment,niche,icp_score,status,updated_at")
      .in("status", ["replied", "in_conversation"])
      .order("updated_at", { ascending: true }).limit(12),
  ]);

  // ---- leads: status + segment counts (bucket in JS) -----------------------
  const leads = leadRows ?? [];
  const byStatus = (s: LeadStatus) => leads.filter((l) => l.status === s).length;
  const stageCounts = Object.fromEntries(
    [...FUNNEL_STAGES, ...LEAKAGE_STAGES].map((s) => [s, byStatus(s)]),
  ) as Record<LeadStatus, number>;
  const maxStage = Math.max(1, ...FUNNEL_STAGES.map((s) => stageCounts[s]));

  const atOrBeyond = (stages: LeadStatus[]) =>
    leads.filter((l) => stages.includes(l.status as LeadStatus)).length;
  const contactedPlus = atOrBeyond(["contacted", "replied", "in_conversation", "demo_booked", "converted"]);
  const repliedPlus = atOrBeyond(["replied", "in_conversation", "demo_booked", "converted"]);
  const demoPlus = atOrBeyond(["demo_booked", "converted"]);
  const pct = (n: number, d: number) => (d > 0 ? Math.round((n / d) * 100) : 0);

  const leadsToday = leads.filter((l) => new Date(l.created_at).getTime() > now.getTime() - DAY).length;

  // segment split at demo stage
  const segAt = (seg: string, stages: LeadStatus[]) =>
    leads.filter((l) => l.segment === seg && stages.includes(l.status as LeadStatus)).length;

  // ---- conversions: demos this week + 8-week trend + no-shows --------------
  const convs = convRows ?? [];
  const weekStart = now.getTime() - 7 * DAY;
  const demosThisWeek = convs.filter((c) => new Date(c.demo_booked_at as string).getTime() > weekStart).length;
  const trend = Array.from({ length: 8 }, (_, i) => {
    const hi = now.getTime() - i * 7 * DAY;
    const lo = hi - 7 * DAY;
    return convs.filter((c) => {
      const t = new Date(c.demo_booked_at as string).getTime();
      return t > lo && t <= hi;
    }).length;
  }).reverse();
  const maxTrend = Math.max(1, ...trend);
  const noShows = convs.filter((c) => c.status === "no_show").length;

  // ---- reputation: delivery breakdown + bounce rate ------------------------
  const msgs = msgRows ?? [];
  const dcount = (st: string) => msgs.filter((m) => m.delivery_status === st).length;
  const attempted = dcount("sent") + dcount("delivered") + dcount("bounced");
  const bounceRate = attempted > 0 ? ((dcount("bounced") / attempted) * 100).toFixed(1) : "0.0";
  const sentToday = msgs.filter((m) => m.sent_at && new Date(m.sent_at).getTime() > now.getTime() - DAY).length;

  // ---- what needs me: awaiting human reply ---------------------------------
  const awaitingList = awaiting ?? [];

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold">Dashboard</h1>

      {/* north star + today */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <BigStat label="Demos booked · this week" value={demosThisWeek} accent />
        <BigStat label="Reply rate (of contacted)" value={`${pct(repliedPlus, contactedPlus)}%`} />
        <BigStat label="Demo rate (of contacted)" value={`${pct(demoPlus, contactedPlus)}%`} />
        <BigStat label="No-shows to re-engage" value={noShows} />
      </div>

      {/* demos trend */}
      <div className="card">
        <h2 className="mb-3 text-sm font-semibold">Demos booked · last 8 weeks</h2>
        <div className="flex items-end gap-2" style={{ height: 100 }}>
          {trend.map((c, i) => (
            <div key={i} className="flex flex-1 flex-col items-center justify-end">
              <span className="text-[10px] text-muted">{c}</span>
              <div className="w-full rounded-t bg-accent/80" style={{ height: `${(c / maxTrend) * 80}%` }} />
              <span className="mt-1 text-[10px] text-muted">{i === 7 ? "now" : `-${7 - i}w`}</span>
            </div>
          ))}
        </div>
      </div>

      {/* funnel */}
      <div className="card">
        <h2 className="mb-3 text-sm font-semibold">Lifecycle funnel</h2>
        <div className="space-y-1.5">
          {FUNNEL_STAGES.map((s) => (
            <div key={s} className="flex items-center gap-3">
              <span className="w-32 text-xs text-muted">{STAGE_LABEL[s]}</span>
              <div className="h-6 flex-1 rounded bg-slate-100">
                <div
                  className="flex h-6 items-center rounded bg-accent/70 px-2 text-xs font-medium text-white"
                  style={{ width: `${Math.max((stageCounts[s] / maxStage) * 100, 6)}%` }}
                >
                  {stageCounts[s]}
                </div>
              </div>
              <span className="w-28 text-right text-[11px] text-muted">
                C:{segAt("creator", [s])} · A:{segAt("affiliate", [s])}
              </span>
            </div>
          ))}
          <div className="flex gap-4 pt-1 text-xs text-muted">
            {LEAKAGE_STAGES.map((s) => (
              <span key={s}>{STAGE_LABEL[s]}: <b>{stageCounts[s]}</b></span>
            ))}
            <span>New today: <b>{leadsToday}</b></span>
          </div>
        </div>
        <p className="mt-2 text-[11px] text-muted">Right column = creator · affiliate split per stage.</p>
      </div>

      {/* reputation */}
      <div className="grid gap-3 sm:grid-cols-4">
        <BigStat label="Bounce rate" value={`${bounceRate}%`} warn={Number(bounceRate) > 2} />
        <BigStat label="Complaints" value={complaintCount ?? 0} warn={(complaintCount ?? 0) > 0} />
        <BigStat label="Sent today" value={sentToday} />
        <BigStat label="Suppressed (opt-out etc.)" value={suppressionCount ?? 0} />
      </div>

      {/* what needs me */}
      <div className="card overflow-x-auto">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-semibold">What needs me — awaiting reply ({awaitingList.length})</h2>
          <Link href="/pipeline" className="text-xs text-accent hover:underline">Open pipeline →</Link>
        </div>
        <table className="w-full border-collapse">
          <thead>
            <tr><th className="th">Lead</th><th className="th">Stage</th><th className="th">Segment</th><th className="th">Niche</th><th className="th">Score</th><th className="th">Waiting</th></tr>
          </thead>
          <tbody>
            {awaitingList.map((l) => {
              const hrs = Math.round((now.getTime() - new Date(l.updated_at as string).getTime()) / 3600000);
              return (
                <tr key={l.id as number}>
                  <td className="td font-mono text-xs"><Link href={`/leads/${l.id}`} className="text-accent hover:underline">{l.identity_key as string}</Link></td>
                  <td className="td"><span className={"pill " + STAGE_CLASS[l.status as LeadStatus]}>{STAGE_LABEL[l.status as LeadStatus]}</span></td>
                  <td className="td">{(l.segment as string) ?? "—"}</td>
                  <td className="td">{(l.niche as string) ?? "—"}</td>
                  <td className="td tabular-nums">{(l.icp_score as number) ?? "—"}</td>
                  <td className={"td tabular-nums " + (hrs > 24 ? "text-red-600" : hrs > 4 ? "text-amber-600" : "text-muted")}>{hrs}h</td>
                </tr>
              );
            })}
            {awaitingList.length === 0 && (
              <tr><td className="td text-muted" colSpan={6}>Nothing awaiting a reply. (Replies land here once inbound capture is live.)</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function BigStat({ label, value, accent, warn }: { label: string; value: number | string; accent?: boolean; warn?: boolean }) {
  return (
    <div className={"card " + (warn ? "border-red-300 bg-red-50" : "")}>
      <div className={"text-2xl font-semibold tabular-nums " + (accent ? "text-accent" : warn ? "text-red-700" : "")}>{value}</div>
      <div className="mt-1 text-xs text-muted">{label}</div>
    </div>
  );
}
