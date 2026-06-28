import "server-only";
import { getServerClient } from "@/lib/supabase/server";
import type { AppJobKind } from "@/lib/types";

/**
 * Enqueue an engine command for the Python orchestrator to run.
 *
 * The front end never runs the brain / scorer / scrapers itself — it writes a
 * row to app_jobs (migration 0004) and the Python consumer
 * (orchestration/app_jobs.py) claims and executes it. Postgres is the contract.
 */
export async function enqueueJob(
  kind: AppJobKind,
  payload: Record<string, unknown> = {},
  requestedBy = "console",
): Promise<{ id: number } | { error: string }> {
  const supa = getServerClient();
  const { data, error } = await supa
    .from("app_jobs")
    .insert({ kind, payload, requested_by: requestedBy })
    .select("id")
    .single();
  if (error) return { error: error.message };
  return { id: data!.id as number };
}
