import { getServerClient } from "@/lib/supabase/server";
import { PipelineBoard, type BoardLead } from "./PipelineBoard";

export const dynamic = "force-dynamic";

export default async function PipelinePage() {
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

  // Pull a working set ordered by priority so the hottest leads surface first in
  // each column. 800 is plenty for a human board; v2 adds server-side paging.
  const { data } = await supa
    .from("leads")
    .select("id,identity_key,segment,niche,platform,follower_count,icp_score,priority_rank,status,source,attributes")
    .order("priority_rank", { ascending: true, nullsFirst: false })
    .limit(800);

  const leads = (data ?? []) as unknown as BoardLead[];

  return <PipelineBoard leads={leads} />;
}
