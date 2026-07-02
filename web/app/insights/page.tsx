import { getServerClient } from "@/lib/supabase/server";

export const dynamic = "force-dynamic";
export const metadata = { title: "Insights — Exly Outbound" };

type Agg = { key: string; leads: number; contacted: number; replied: number; booked: number; converted: number; score: number };

const CONTACTED_PLUS = new Set(["contacted", "replied", "in_conversation", "demo_booked", "converted"]);
const REPLIED_PLUS = new Set(["replied", "in_conversation", "demo_booked", "converted"]);
const BOOKED_PLUS = new Set(["demo_booked", "converted"]);
const pct = (n: number, d: number) => (d > 0 ? Math.round((n / d) * 100) : 0);

export default async function InsightsPage() {
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

  const [{ data: leadRows }, { data: sendRows }] = await Promise.all([
    supa.from("leads").select("niche,source,status,icp_score").limit(5000),
    supa.from("outreach").select("channel,direction,status").eq("direction", "out").limit(5000),
  ]);
  const leads = (leadRows ?? []) as any[];
  const sends = (sendRows ?? []) as any[];

  // ---- funnel by niche & by source -----------------------------------------
  function aggregate(field: "niche" | "source"): Agg[] {
    const m = new Map<string, Agg>();
    for (const l of leads) {
      const key = (l[field] as string) || "—";
      const g = m.get(key) ?? { key, leads: 0, contacted: 0, replied: 0, booked: 0, converted: 0, score: 0 };
      g.leads++;
      g.score += l.icp_score ?? 0;
      if (CONTACTED_PLUS.has(l.status)) g.contacted++;
      if (REPLIED_PLUS.has(l.status)) g.replied++;
      if (BOOKED_PLUS.has(l.status)) g.booked++;
      if (l.status === "converted") g.converted++;
      m.set(key, g);
    }
    return [...m.values()].sort((a, b) => b.leads - a.leads);
  }
  const byNiche = aggregate("niche");
  const bySource = aggregate("source");

  // ---- channel reply performance -------------------------------------------
  const chan = { whatsapp: { sent: 0, replied: 0, failed: 0 }, email: { sent: 0, replied: 0, failed: 0 } } as Record<string, { sent: number; replied: number; failed: number }>;
  for (const s of sends) {
    const c = chan[s.channel] ?? (chan[s.channel] = { sent: 0, replied: 0, failed: 0 });
    if (s.status === "failed") c.failed++;
    else { c.sent++; if (s.status === "replied") c.replied++; }
  }

  // ---- suggested nudges (heuristic feedback loop) --------------------------
  const totalContacted = byNiche.reduce((n, g) => n + g.contacted, 0);
  const totalReplied = byNiche.reduce((n, g) => n + g.replied, 0);
  const avgReply = pct(totalReplied, totalContacted);
  const nudges: { tone: "up" | "down" | "info"; text: string }[] = [];
  for (const g of byNiche) {
    if (g.contacted < 3) continue;
    const r = pct(g.replied, g.contacted);
    if (r >= avgReply + 15) nudges.push({ tone: "up", text: `“${g.key}” replies at ${r}% (vs ${avgReply}% avg) — source & prioritise more of this niche.` });
    if (r <= Math.max(0, avgReply - 15)) nudges.push({ tone: "down", text: `“${g.key}” replies at ${r}% (below ${avgReply}% avg) — rework the angle or de-prioritise.` });
  }
  const wr = pct(chan.whatsapp?.replied ?? 0, chan.whatsapp?.sent ?? 0);
  const er = pct(chan.email?.replied ?? 0, chan.email?.sent ?? 0);
  if ((chan.whatsapp?.sent ?? 0) >= 5 && (chan.email?.sent ?? 0) >= 5) {
    nudges.push({ tone: "info", text: wr >= er
      ? `WhatsApp is out-replying email (${wr}% vs ${er}%) — lead with WhatsApp where you have consent.`
      : `Email is out-replying WhatsApp (${er}% vs ${wr}%) — lead with email for this cohort.` });
  }
  const topConv = [...byNiche].filter((g) => g.leads >= 3).sort((a, b) => pct(b.converted, b.leads) - pct(a.converted, a.leads))[0];
  if (topConv && topConv.converted > 0) nudges.push({ tone: "up", text: `“${topConv.key}” converts best (${pct(topConv.converted, topConv.leads)}% of leads) — bump its ICP weight in Scoring.` });
  if (nudges.length === 0) nudges.push({ tone: "info", text: "Not enough send/reply volume yet to surface trends. Send a few campaigns and check back." });

  return (
    <div className="space-y-6 rise">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Insights</h1>
        <p className="mt-1 text-sm text-muted">Feedback loop — what’s working, so you can sharpen who you target and how you reach out.</p>
      </div>

      {/* suggested actions */}
      <div className="card">
        <h2 className="mb-3 text-sm font-semibold">Suggested actions</h2>
        <ul className="space-y-2">
          {nudges.map((n, i) => (
            <li key={i} className="flex items-start gap-2 text-sm">
              <span className={"mt-0.5 " + (n.tone === "up" ? "text-green-600" : n.tone === "down" ? "text-red-600" : "text-accent")}>
                {n.tone === "up" ? "▲" : n.tone === "down" ? "▼" : "•"}
              </span>
              <span>{n.text}</span>
            </li>
          ))}
        </ul>
      </div>

      {/* channel performance */}
      <div className="grid gap-3 sm:grid-cols-2">
        <ChannelCard label="WhatsApp" c={chan.whatsapp} />
        <ChannelCard label="Email" c={chan.email} />
      </div>

      {/* by niche */}
      <FunnelTable title="By niche" rows={byNiche} />
      {/* by source */}
      <FunnelTable title="By source" rows={bySource} />
    </div>
  );
}

function ChannelCard({ label, c }: { label: string; c?: { sent: number; replied: number; failed: number } }) {
  const sent = c?.sent ?? 0, replied = c?.replied ?? 0, failed = c?.failed ?? 0;
  return (
    <div className="card">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold">{label}</h3>
        <span className="grad-text text-2xl font-bold tabular-nums">{pct(replied, sent)}%</span>
      </div>
      <p className="mt-1 text-xs text-muted">{sent} sent · {replied} replied · {failed} failed</p>
    </div>
  );
}

function FunnelTable({ title, rows }: { title: string; rows: Agg[] }) {
  return (
    <div className="card overflow-x-auto">
      <h2 className="mb-2 text-sm font-semibold">{title}</h2>
      <table className="w-full border-collapse">
        <thead>
          <tr>
            <th className="th">{title.replace("By ", "")}</th><th className="th">Leads</th><th className="th">Avg score</th>
            <th className="th">Contacted</th><th className="th">Reply %</th><th className="th">Demos</th><th className="th">Conv %</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((g) => (
            <tr key={g.key}>
              <td className="td text-sm font-medium">{g.key}</td>
              <td className="td tabular-nums">{g.leads}</td>
              <td className="td tabular-nums text-muted">{g.leads ? Math.round(g.score / g.leads) : 0}</td>
              <td className="td tabular-nums">{g.contacted}</td>
              <td className="td tabular-nums">{pct(g.replied, g.contacted)}%</td>
              <td className="td tabular-nums">{g.booked}</td>
              <td className="td tabular-nums">{pct(g.converted, g.leads)}%</td>
            </tr>
          ))}
          {rows.length === 0 && <tr><td className="td text-muted" colSpan={7}>No leads yet.</td></tr>}
        </tbody>
      </table>
    </div>
  );
}
