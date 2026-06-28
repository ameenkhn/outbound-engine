"use server";

import { revalidatePath } from "next/cache";
import { getServerClient } from "@/lib/supabase/server";

type Res = { ok: true } | { ok: false; error: string };

/** Save operator free-text notes (leads.notes, added in migration 0004). */
export async function saveNotes(leadId: number, notes: string): Promise<Res> {
  try {
    const supa = getServerClient();
    const { error } = await supa.from("leads").update({ notes }).eq("id", leadId);
    if (error) return { ok: false, error: error.message };
    revalidatePath(`/leads/${leadId}`);
    return { ok: true };
  } catch (e) {
    return { ok: false, error: (e as Error).message };
  }
}

/** Identity-wide opt-out + flip the lead to opted_out (6A: optout => channel_type NULL). */
export async function suppressFromLead(leadId: number, identityKey: string): Promise<Res> {
  try {
    const supa = getServerClient();
    const { error: supErr } = await supa
      .from("suppression")
      .insert({ identity_key: identityKey, channel_type: null, reason: "optout", note: "console opt-out" });
    if (supErr && !/duplicate|unique/i.test(supErr.message)) return { ok: false, error: supErr.message };
    const { error } = await supa.from("leads").update({ status: "opted_out" }).eq("id", leadId);
    if (error) return { ok: false, error: error.message };
    revalidatePath(`/leads/${leadId}`);
    return { ok: true };
  } catch (e) {
    return { ok: false, error: (e as Error).message };
  }
}
