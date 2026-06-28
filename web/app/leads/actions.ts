"use server";

import { getServerClient } from "@/lib/supabase/server";
import { CSV_COLS, applyFilters, type LeadFilters } from "./filters";

export type { LeadFilters };

/** Export up to `limit` filtered leads as a CSV string (server-side, past RLS). */
export async function exportLeadsCsv(
  f: LeadFilters,
  limit = 5000,
): Promise<{ ok: true; csv: string; count: number } | { ok: false; error: string }> {
  try {
    const supa = getServerClient();
    let q = supa
      .from("leads")
      .select(CSV_COLS.join(","))
      .order("icp_score", { ascending: false, nullsFirst: false })
      .limit(limit);
    q = applyFilters(q, f);
    const { data, error } = await q;
    if (error) return { ok: false, error: error.message };

    const rows = (data ?? []) as unknown as Record<string, unknown>[];
    const esc = (v: unknown) => `"${String(v ?? "").replace(/"/g, '""')}"`;
    const csv = [CSV_COLS.join(",")]
      .concat(rows.map((r) => CSV_COLS.map((c) => esc(r[c])).join(",")))
      .join("\n");
    return { ok: true, csv, count: rows.length };
  } catch (e) {
    return { ok: false, error: (e as Error).message };
  }
}
