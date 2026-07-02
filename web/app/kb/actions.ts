"use server";

import { revalidatePath } from "next/cache";
import { getServerClient } from "@/lib/supabase/server";

export type KbDoc = { id: number; title: string; content: string; tags: string | null; created_at: string };

export async function listKb(): Promise<{ ok: true; docs: KbDoc[] } | { ok: false; error: string }> {
  try {
    const supa = getServerClient();
    const { data, error } = await supa
      .from("kb_docs").select("id,title,content,tags,created_at")
      .order("created_at", { ascending: false }).limit(500);
    if (error) return { ok: false, error: error.message };
    return { ok: true, docs: (data ?? []) as KbDoc[] };
  } catch (e) {
    return { ok: false, error: (e as Error).message };
  }
}

export async function addKbDoc(input: { title: string; content: string; tags?: string }): Promise<{ ok: true } | { ok: false; error: string }> {
  if (!input.title?.trim() || !input.content?.trim()) return { ok: false, error: "Title and content are required." };
  try {
    const supa = getServerClient();
    const { error } = await supa.from("kb_docs").insert({
      title: input.title.trim(), content: input.content.trim(), tags: input.tags?.trim() || null,
    });
    if (error) return { ok: false, error: error.message };
    revalidatePath("/kb");
    return { ok: true };
  } catch (e) {
    return { ok: false, error: (e as Error).message };
  }
}

export async function deleteKbDoc(id: number): Promise<{ ok: true } | { ok: false; error: string }> {
  try {
    const supa = getServerClient();
    const { error } = await supa.from("kb_docs").delete().eq("id", id);
    if (error) return { ok: false, error: error.message };
    revalidatePath("/kb");
    return { ok: true };
  } catch (e) {
    return { ok: false, error: (e as Error).message };
  }
}
