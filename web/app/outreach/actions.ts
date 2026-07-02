"use server";

import { revalidatePath } from "next/cache";
import { getServerClient } from "@/lib/supabase/server";

const ALLOWED = ["sent", "failed", "replied"] as const;
export type OutreachStatus = (typeof ALLOWED)[number];

/** Update the status of a logged send — e.g. mark that the lead replied. */
export async function setOutreachStatus(
  id: number,
  status: OutreachStatus,
): Promise<{ ok: true } | { ok: false; error: string }> {
  if (!ALLOWED.includes(status)) return { ok: false, error: "Bad status." };
  try {
    const supa = getServerClient();
    const { data: row, error: e0 } = await supa
      .from("outreach").select("lead_id").eq("id", id).single();
    if (e0) return { ok: false, error: e0.message };

    const { error } = await supa.from("outreach").update({ status }).eq("id", id);
    if (error) return { ok: false, error: error.message };

    // when a lead replies, advance the lead out of "contacted"
    if (status === "replied" && row?.lead_id) {
      await supa.from("leads").update({ status: "replied" })
        .eq("id", row.lead_id).eq("status", "contacted");
    }
    revalidatePath("/outreach");
    return { ok: true };
  } catch (e) {
    return { ok: false, error: (e as Error).message };
  }
}
