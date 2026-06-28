"use server";

import { revalidatePath } from "next/cache";
import { getServerClient } from "@/lib/supabase/server";
import { enqueueJob } from "@/lib/jobs";

/** Save the editable L2 weights + target niches (scoring_config id=1). */
export async function saveScoringConfig(input: {
  weights: Record<string, number>;
  target_niches: string[];
}): Promise<{ ok: true } | { ok: false; error: string }> {
  try {
    const supa = getServerClient();
    const { error } = await supa
      .from("scoring_config")
      .update({
        weights: input.weights,
        target_niches: input.target_niches,
        updated_at: new Date().toISOString(),
        updated_by: "console",
      })
      .eq("id", 1);
    if (error) return { ok: false, error: error.message };
    revalidatePath("/scoring");
    return { ok: true };
  } catch (e) {
    return { ok: false, error: (e as Error).message };
  }
}

/** Enqueue a re-score batch for the Python engine (enrichment.run) to execute. */
export async function triggerRescore(): Promise<{ ok: true; jobId: number } | { ok: false; error: string }> {
  const res = await enqueueJob("rescore", {}, "console");
  if ("error" in res) return { ok: false, error: res.error };
  revalidatePath("/scoring");
  return { ok: true, jobId: res.id };
}
