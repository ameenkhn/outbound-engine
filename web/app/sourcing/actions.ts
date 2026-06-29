"use server";

import { revalidatePath } from "next/cache";
import { getServerClient } from "@/lib/supabase/server";
import { enqueueJob } from "@/lib/jobs";

type Res = { ok: true; jobId?: number } | { ok: false; error: string };

/** Mode B: expand seed keywords -> the brain writes an approved keyword spec. */
export async function runModeB(keywords: string[]): Promise<Res> {
  const clean = keywords.map((k) => k.trim()).filter(Boolean);
  if (clean.length === 0) return { ok: false, error: "Enter at least one seed keyword." };
  const res = await enqueueJob("mode_b", { keywords: clean });
  if ("error" in res) return { ok: false, error: res.error };
  revalidatePath("/sourcing");
  return { ok: true, jobId: res.id };
}

/** Mode A: persona -> the brain writes an UNAPPROVED deep spec for sign-off. */
export async function runModeA(persona: string): Promise<Res> {
  if (!persona.trim()) return { ok: false, error: "Describe the persona first." };
  const res = await enqueueJob("mode_a", { persona: persona.trim() });
  if ("error" in res) return { ok: false, error: res.error };
  revalidatePath("/sourcing");
  return { ok: true, jobId: res.id };
}

/** Human sign-off — pure DB, no engine needed. Adapters only consume approved specs. */
export async function approveSpec(specId: number): Promise<Res> {
  try {
    const supa = getServerClient();
    const { error } = await supa.from("target_specs").update({ approved: true }).eq("id", specId);
    if (error) return { ok: false, error: error.message };
    revalidatePath("/sourcing");
    return { ok: true };
  } catch (e) {
    return { ok: false, error: (e as Error).message };
  }
}

/** The source channels the engine can run for an approved spec. */
export type SourcePlatform =
  | "meta_ads"
  | "instagram"
  | "linkedin"
  | "youtube"
  | "websearch"
  | "all";

/** Kick a source adapter run for an approved spec (engine runs the scraper/API).
 *  "all" fans out across every channel in one job. */
export async function kickSourceRun(specId: number, platform: SourcePlatform): Promise<Res> {
  const res = await enqueueJob("source_run", { spec_id: specId, platform });
  if ("error" in res) return { ok: false, error: res.error };
  revalidatePath("/sourcing");
  return { ok: true, jobId: res.id };
}

/** Quick Harvest: scrape straight from typed keywords — no Mode B / LLM needed.
 *  Enqueues a source_run the worker runs with an ad-hoc approved spec. */
export async function kickQuickHarvest(
  keywords: string[],
  platform: SourcePlatform,
  limit = 0,
): Promise<Res> {
  const clean = keywords.map((k) => k.trim()).filter(Boolean);
  if (clean.length === 0) return { ok: false, error: "Enter at least one keyword." };
  const payload: Record<string, unknown> = { keywords: clean, platform };
  if (limit && limit > 0) payload.limit = limit;
  const res = await enqueueJob("source_run", payload);
  if ("error" in res) return { ok: false, error: res.error };
  revalidatePath("/sourcing");
  return { ok: true, jobId: res.id };
}

export type JobSnapshot = {
  id: number;
  status: "pending" | "claimed" | "done" | "failed";
  result: Record<string, unknown> | null;
  last_error: string | null;
};

/** Poll a single job's live status (used by Quick Harvest to show progress). */
export async function getJobStatus(
  jobId: number,
): Promise<{ ok: true; job: JobSnapshot } | { ok: false; error: string }> {
  try {
    const supa = getServerClient();
    const { data, error } = await supa
      .from("app_jobs")
      .select("id,status,result,last_error")
      .eq("id", jobId)
      .single();
    if (error) return { ok: false, error: error.message };
    return { ok: true, job: data as unknown as JobSnapshot };
  } catch (e) {
    return { ok: false, error: (e as Error).message };
  }
}
