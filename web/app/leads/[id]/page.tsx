import Link from "next/link";
import { getServerClient } from "@/lib/supabase/server";
import type { Lead, ScoringConfig } from "@/lib/types";
import { STAGE_LABEL, STAGE_CLASS } from "@/lib/stages";
import { scoreBreakdown, type ChannelLite } from "@/lib/scoreBreakdown";
import { NotesEditor, OptOutButton, ReplyDrafter, BookDemoForm } from "./LeadActions";

export const dynamic = "force-dynamic";

export default async function LeadPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const leadId = Number(id);

  let supa;
  try {
    supa = getServerClient();
  } catch (e) {
    return <div className="card border-amber-300 bg-amber-50 text-amber-800">{(e as Error).message}</div>;
  }

  const { data: lead } = await supa
    .from("leads")
    .select("id,identity_key,segment,niche,platform,follower_band,follower_count,icp_score,priority_rank,status,geo,source,source_ref,attributes,notes,target_spec_id,created_at,updated_at")
    .eq("id", leadId)
    .single();

  if (!lead) {
    return (
      <div className="card">
        <p className="text-sm text-muted">No lead with id {leadId}.</p>
        <Link href="/pipeline" className="mt-2 inline-block text-sm text-accent hover:underline">← Back to pipeline</Link>
      </div>
    );
  }

  const [{ data: channels }, { data: events }, { data: messages }, { data: convs }, { data: supp }, { data: cfg }, { data: sends }] =
    await Promise.all([
      supa.from("channels").select("type,handle,deliverable,opted_in,opted_out,opt_in_source,opt_in_ts").eq("lead_id", leadId),
      supa.from("events").select("type,intent,sentiment,meta,ts").eq("lead_id", leadId).order("ts", { ascending: false }).limit(50),
      supa.from("messages").select("angle,subject,body,delivery_status,sent_at,created_at").eq("lead_id", leadId).order("created_at", { ascending: false }).limit(50),
      supa.from("conversions").select("demo_booked_at,demo_scheduled_at,status,owner,summary,outcome").eq("lead_id", leadId),
      supa.from("suppression").select("channel_type,reason,note,ts").eq("identity_key", lead.identity_key),
      supa.from("scoring_config").select("*").eq("id", 1).single(),
      supa.from("outreach").select("channel,direction,to_handle,subject,body,status,error,created_at").eq("lead_id", leadId).order("created_at", { ascending: false }).limit(50),
    ]);

  const hasInbound = (sends ?? []).some((s) => (s as { direction?: string }).direction === "in");

  const chans = (channels ?? []) as ChannelLite[];
  const bd = scoreBreakdown(lead as unknown as Lead, chans, (cfg as ScoringConfig) ?? null);
  const suppressed = (supp ?? []).length > 0;
  const a = (lead.attributes ?? {}) as Record<string, unknown>;

  return (
    <div className="space-y-5">
      <Link href="/pipeline" className="text-sm text-accent hover:underline">← Pipeline</Link>

      {/* identity header */}
      <div className="card">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="font-mono text-lg font-semibold">{lead.identity_key}</h1>
            <p className="mt-1 text-sm text-muted">
              {lead.segment ?? "—"}{lead.niche ? ` · ${lead.niche}` : ""}
              {lead.platform ? ` · ${lead.platform}` : ""}
              {lead.follower_count ? ` · ${lead.follower_count.toLocaleString()} followers` : ""}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <span className={"pill " + STAGE_CLASS[lead.status as keyof typeof STAGE_CLASS]}>{STAGE_LABEL[lead.status as keyof typeof STAGE_LABEL]}</span>
            <Link href={`/compose?lead=${lead.id}`} className="btn px-3 py-1.5 text-sm">Message →</Link>
            <OptOutButton leadId={lead.id} identityKey={lead.identity_key} />
          </div>
        </div>
      </div>

      {suppressed && (
        <div className="card border-red-300 bg-red-50 text-sm text-red-800">
          🚫 Suppressed — {(supp ?? []).map((s) => `${s.reason}${s.channel_type ? ` (${s.channel_type})` : " (identity-wide)"}`).join(", ")}. Do not contact.
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        {/* score + why */}
        <div className="card">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold">ICP score</h2>
            <span className="text-2xl font-semibold tabular-nums text-accent">{lead.icp_score ?? "—"}</span>
          </div>
          <p className="text-xs text-muted">priority_rank {lead.priority_rank ?? "—"}</p>
          {bd.gated ? (
            <p className="mt-2 text-sm text-red-700">Gated (score 0): {bd.gateReason}</p>
          ) : (
            <table className="mt-2 w-full">
              <tbody>
                {bd.rows.map((r) => (
                  <tr key={r.label}>
                    <td className="py-1 text-sm">{r.label}</td>
                    <td className={"py-1 text-right text-sm tabular-nums " + (r.fired ? "text-ink" : "text-muted")}>
                      {r.fired ? `+${r.points}` : "—"}
                    </td>
                  </tr>
                ))}
                <tr className="border-t border-line">
                  <td className="py-1 text-sm font-medium">Computed (display)</td>
                  <td className="py-1 text-right text-sm font-medium tabular-nums">{bd.computed}</td>
                </tr>
              </tbody>
            </table>
          )}
          <p className="mt-2 text-[11px] text-muted">
            Breakdown mirrors the L2 formula against current weights for transparency. The
            stored <code>icp_score</code> ({lead.icp_score ?? "—"}) is authoritative.
          </p>
        </div>

        {/* channels + consent */}
        <div className="card">
          <h2 className="mb-2 text-sm font-semibold">Channels &amp; consent</h2>
          {chans.length === 0 && <p className="text-sm text-muted">No channels.</p>}
          <ul className="space-y-1 text-sm">
            {chans.map((c, i) => (
              <li key={i} className="flex items-center justify-between gap-2">
                <span className="font-mono text-xs">{c.type}: {c.handle}</span>
                <span className="flex gap-1">
                  {c.deliverable ? <span className="pill bg-green-100 text-green-700">deliverable</span> : <span className="pill bg-slate-100 text-muted">undeliverable</span>}
                  {c.opted_in ? <span className="pill bg-blue-100 text-blue-700">opted-in</span> : null}
                  {c.opted_out ? <span className="pill bg-red-100 text-red-700">opted-out</span> : null}
                </span>
              </li>
            ))}
          </ul>
          <p className="mt-2 text-[11px] text-muted">
            WhatsApp sends require <b>opted-in</b>. DPDP: opt-out is honored identity-wide via suppression.
          </p>
        </div>
      </div>

      {/* conversion */}
      {(convs ?? []).length > 0 && (
        <div className="card">
          <h2 className="mb-2 text-sm font-semibold">Demo / conversion</h2>
          {(convs ?? []).map((c, i) => (
            <p key={i} className="text-sm">
              {c.status ?? "booked"} · scheduled {fmt(c.demo_scheduled_at)} · booked {fmt(c.demo_booked_at)}
              {c.owner ? ` · owner ${c.owner}` : ""}{c.outcome ? ` · ${c.outcome}` : ""}
            </p>
          ))}
        </div>
      )}

      {/* two-way outreach thread (Compose sends + inbound replies) */}
      <div className="card">
        <h2 className="mb-2 text-sm font-semibold">Conversation ({(sends ?? []).length})</h2>
        <div className="space-y-2">
          {(sends ?? []).map((s, i) => {
            const inbound = (s as { direction?: string }).direction === "in";
            return (
              <div key={i} className={"rounded border p-2 text-sm " + (inbound ? "border-accent/40 bg-accent/5 ml-6" : "border-line mr-6")}>
                <div className="flex items-center justify-between text-xs text-muted">
                  <span>
                    <span className={"pill " + (inbound ? "bg-accent/15 text-accent" : "bg-slate-100 text-muted")}>{inbound ? "← reply" : "sent →"}</span>
                    <span className={"ml-1 pill " + (s.channel === "whatsapp" ? "bg-green-100 text-green-700" : "bg-indigo-100 text-indigo-700")}>{s.channel}</span>
                    {!inbound && <span className={"ml-1 pill " + (s.status === "replied" ? "bg-indigo-100 text-indigo-700" : s.status === "sent" ? "bg-green-100 text-green-700" : "bg-red-100 text-red-700")}>{s.status}</span>}
                  </span>
                  <span>{fmt(s.created_at)}</span>
                </div>
                {s.subject && <div className="mt-1 font-medium">{s.subject}</div>}
                <div className="text-muted line-clamp-4">{s.body}</div>
                {s.error && <div className="mt-1 text-xs text-red-600">{s.error}</div>}
              </div>
            );
          })}
          {(sends ?? []).length === 0 && (
            <p className="text-sm text-muted">Nothing yet. Use <Link href={`/compose?lead=${lead.id}`} className="text-accent hover:underline">Compose</Link> to reach out.</p>
          )}
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        {/* AI suggested reply (L6) */}
        <div className="card">
          <h2 className="mb-2 text-sm font-semibold">Suggested reply</h2>
          <ReplyDrafter leadId={lead.id} hasInbound={hasInbound} />
        </div>
        {/* demo booking (L7) */}
        <div className="card">
          <h2 className="mb-2 text-sm font-semibold">Book a demo</h2>
          <BookDemoForm leadId={lead.id} />
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        {/* messages */}
        <div className="card">
          <h2 className="mb-2 text-sm font-semibold">Messages ({(messages ?? []).length})</h2>
          <div className="space-y-2">
            {(messages ?? []).map((m, i) => (
              <div key={i} className="rounded border border-line p-2 text-sm">
                <div className="flex justify-between text-xs text-muted">
                  <span>{m.angle ?? "—"} · {m.delivery_status}</span>
                  <span>{fmt(m.sent_at ?? m.created_at)}</span>
                </div>
                {m.subject && <div className="font-medium">{m.subject}</div>}
                <div className="text-muted line-clamp-3">{m.body}</div>
              </div>
            ))}
            {(messages ?? []).length === 0 && <p className="text-sm text-muted">No messages sent yet.</p>}
          </div>
        </div>

        {/* events timeline */}
        <div className="card">
          <h2 className="mb-2 text-sm font-semibold">Events ({(events ?? []).length})</h2>
          <ul className="space-y-1 text-sm">
            {(events ?? []).map((e, i) => (
              <li key={i} className="flex justify-between gap-2">
                <span>
                  <span className="pill bg-slate-100 text-muted">{e.type}</span>
                  {e.intent ? ` ${e.intent}` : ""}{e.sentiment ? ` · ${e.sentiment}` : ""}
                </span>
                <span className="text-xs text-muted">{fmt(e.ts)}</span>
              </li>
            ))}
            {(events ?? []).length === 0 && <p className="text-sm text-muted">No events yet.</p>}
          </ul>
        </div>
      </div>

      {/* attribution + notes */}
      <div className="grid gap-4 lg:grid-cols-2">
        <div className="card text-sm">
          <h2 className="mb-2 text-sm font-semibold">Attribution</h2>
          <p>Source: <b>{lead.source ?? "—"}</b>{lead.source_ref ? ` (${lead.source_ref})` : ""}</p>
          <p>Target spec: <b>{lead.target_spec_id ?? "—"}</b></p>
          {a.ad_text ? <p className="mt-1 text-muted line-clamp-3">Ad text: {String(a.ad_text)}</p> : null}
          <p className="mt-1 text-xs text-muted">Created {fmt(lead.created_at)} · updated {fmt(lead.updated_at)}</p>
        </div>
        <div className="card">
          <h2 className="mb-2 text-sm font-semibold">Notes</h2>
          <NotesEditor leadId={lead.id} initial={lead.notes ?? ""} />
        </div>
      </div>
    </div>
  );
}

function fmt(ts: string | null | undefined): string {
  if (!ts) return "—";
  try { return new Date(ts).toLocaleString(); } catch { return "—"; }
}
