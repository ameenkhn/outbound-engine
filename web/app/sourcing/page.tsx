import { getServerClient } from "@/lib/supabase/server";
import type { TargetSpec, AppJob } from "@/lib/types";
import { SourcingConsole } from "./SourcingConsole";

export const dynamic = "force-dynamic";

export default async function SourcingPage() {
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

  const { data: specs } = await supa
    .from("target_specs")
    .select("id,mode,persona_text,seed_keywords,expanded_keywords,filters,attributes,approved,created_by_model,created_at")
    .order("created_at", { ascending: false })
    .limit(50);

  const { data: jobs } = await supa
    .from("app_jobs")
    .select("id,kind,payload,status,attempts,result,last_error,requested_by,created_at,updated_at")
    .in("kind", ["mode_a", "mode_b", "source_run"])
    .order("created_at", { ascending: false })
    .limit(15);

  // lead counts per spec (sourcing yield) — one grouped read
  const { data: leadRows } = await supa
    .from("leads")
    .select("target_spec_id")
    .not("target_spec_id", "is", null)
    .limit(10000);
  const leadCounts: Record<number, number> = {};
  for (const r of leadRows ?? []) {
    const k = r.target_spec_id as number;
    leadCounts[k] = (leadCounts[k] ?? 0) + 1;
  }

  return (
    <SourcingConsole
      specs={(specs as TargetSpec[]) ?? []}
      jobs={(jobs as AppJob[]) ?? []}
      leadCounts={leadCounts}
    />
  );
}
