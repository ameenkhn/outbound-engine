"use server";

import { revalidatePath } from "next/cache";
import { getServerClient } from "@/lib/supabase/server";
import type { LeadStatus } from "@/lib/types";

type Res = { ok: true } | { ok: false; error: string };

/** Manual stage override (operator drags / changes a lead's lifecycle status). */
export async function setStatus(leadId: number, status: LeadStatus): Promise<Res> {
  try {
    const supa = getServerClient();
    const { error } = await supa.from("leads").update({ status }).eq("id", leadId);
    if (error) return { ok: false, error: error.message };
    revalidatePath("/pipeline");
    return { ok: true };
  } catch (e) {
    return { ok: false, error: (e as Error).message };
  }
}

/** Mark a lead dead (one click). */
export async function markDead(leadId: number): Promise<Res> {
  return setStatus(leadId, "dead");
}

/**
 * One-click opt-out / suppress: identity-wide suppression (reason='optout',
 * channel_type NULL per the 6A CHECK) + flip the lead to opted_out. Idempotent
 * — a duplicate suppression row is ignored.
 */
export async function suppressLead(leadId: number, identityKey: string): Promise<Res> {
  try {
    const supa = getServerClient();
    const { error: supErr } = await supa
      .from("suppression")
      .insert({ identity_key: identityKey, channel_type: null, reason: "optout", note: "console opt-out" });
    // ignore unique-violation (already suppressed); surface anything else
    if (supErr && !/duplicate|unique/i.test(supErr.message)) {
      return { ok: false, error: supErr.message };
    }
    const { error } = await supa.from("leads").update({ status: "opted_out" }).eq("id", leadId);
    if (error) return { ok: false, error: error.message };
    revalidatePath("/pipeline");
    return { ok: true };
  } catch (e) {
    return { ok: false, error: (e as Error).message };
  }
}
