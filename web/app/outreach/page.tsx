import { getServerClient } from "@/lib/supabase/server";
import { OutreachClient, type OutreachRow } from "./OutreachClient";

export const dynamic = "force-dynamic";
export const metadata = { title: "Outreach — Exly Outbound" };

export default async function OutreachPage() {
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

  const { data } = await supa
    .from("outreach")
    .select("id,channel,to_handle,subject,body,status,error,created_at,lead_id,leads(identity_key,attributes,niche)")
    .order("created_at", { ascending: false })
    .limit(500);

  const rows: OutreachRow[] = ((data ?? []) as any[]).map((r) => {
    const a = (r.leads?.attributes || {}) as Record<string, unknown>;
    return {
      id: r.id,
      lead_id: r.lead_id ?? null,
      lead_name: (a.advertiser as string) || r.leads?.identity_key || "—",
      niche: r.leads?.niche ?? null,
      channel: r.channel,
      to_handle: r.to_handle,
      subject: r.subject ?? null,
      body: r.body,
      status: r.status,
      error: r.error ?? null,
      created_at: r.created_at,
    };
  });

  return <OutreachClient rows={rows} />;
}
