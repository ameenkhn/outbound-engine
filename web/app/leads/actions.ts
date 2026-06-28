"use server";

import { getServerClient } from "@/lib/supabase/server";

export interface LeadFilters {
  q?: string;
  platform?: string;
  status?: string;
  segment?: string;
  minScore?: number;
}

const CSV_COLS = [
  "id", "identity_key", "segment", "niche", "platform",
  "follower_band", "follower_count", "icp_score", "priority_rank",
  "status", "source", "created_at",
] as const;

/** Apply the shared filter set to a Supabase query builder. */
function applyFilters(query: any, f: LeadFilters) {
  if (f.platform) query = query.eq("platform", f.platform);
  if (f.status) query = query.eq("status", f.status);
  if (f.segment) query = query.eq("segment", f.segment);
  if (typeof f.minScore === "number" && !Number.isNaN(f.minScore)) {
    query = query.gte("icp_score", f.minScore);
  }
  if (f.q && f.q.trim()) {
    const term = `%${f.q.trim()}%`;
    // identity_key OR niche match (both indexed text columns).
    query = query.or(`identity_key.ilike.${term},niche.ilike.${term}`);
  }
  return query;
}

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

export { applyFilters, CSV_COLS };
