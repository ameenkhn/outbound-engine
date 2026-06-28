// Shared lead-filter helpers. Kept in a PLAIN module (no "use server") because a
// "use server" file may only export async functions — constants and sync helpers
// like CSV_COLS / applyFilters must live outside it. Imported by both the server
// action (actions.ts) and the page (page.tsx).

export interface LeadFilters {
  q?: string;
  platform?: string;
  status?: string;
  segment?: string;
  minScore?: number;
}

export const CSV_COLS = [
  "id", "identity_key", "segment", "niche", "platform",
  "follower_band", "follower_count", "icp_score", "priority_rank",
  "status", "source", "created_at",
] as const;

/** Apply the shared filter set to a Supabase query builder (mutating + returning it). */
export function applyFilters(query: any, f: LeadFilters) {
  if (f.platform) query = query.eq("platform", f.platform);
  if (f.status) query = query.eq("status", f.status);
  if (f.segment) query = query.eq("segment", f.segment);
  if (typeof f.minScore === "number" && !Number.isNaN(f.minScore)) {
    query = query.gte("icp_score", f.minScore);
  }
  if (f.q && f.q.trim()) {
    const term = `%${f.q.trim()}%`;
    query = query.or(`identity_key.ilike.${term},niche.ilike.${term}`);
  }
  return query;
}
