import type { Lead, ScoringConfig } from "@/lib/types";

// Channel shape used by the breakdown (subset of the channels table).
export interface ChannelLite {
  type: "email" | "whatsapp" | "linkedin";
  handle: string;
  deliverable: boolean;
  opted_out: boolean;
  opted_in?: boolean;
}

export interface BreakdownRow {
  label: string;
  points: number;     // points this rule contributes (0 if not fired)
  fired: boolean;
}

export interface Breakdown {
  gated: boolean;
  gateReason: string | null;
  rows: BreakdownRow[];
  computed: number;   // capped, display-only re-derivation
}

const BANDS: Array<[string, number, number]> = [
  ["nano", 1, 1_000],
  ["micro", 1_000, 100_000],
  ["mid", 100_000, 1_000_000],
  ["macro", 1_000_000, Number.POSITIVE_INFINITY],
];

function band(count: number | null): string | null {
  if (!count || count <= 0) return null;
  for (const [name, lo, hi] of BANDS) if (count >= lo && count < hi) return name;
  return "macro";
}

/**
 * Re-derive the L2 score components for the transparency view. This MIRRORS
 * enrichment/score.py against the live scoring_config weights — it is for
 * DISPLAY ONLY. The authoritative value is `leads.icp_score` (written by the
 * Python scorer). Shown so an operator can see which weights fired.
 */
export function scoreBreakdown(
  lead: Pick<Lead, "attributes" | "follower_count" | "geo" | "segment" | "niche">,
  channels: ChannelLite[],
  cfg: ScoringConfig | null,
): Breakdown {
  const w = cfg?.weights ?? {};
  const niches = (cfg?.target_niches ?? []).map((n) => n.toLowerCase());
  const tools = (cfg?.competitor_tools ?? []).map((t) => t.toLowerCase());
  const attrs = (lead.attributes ?? {}) as Record<string, unknown>;

  const reachable = channels.some((c) => (c.type === "email" || c.type === "whatsapp") && !c.opted_out && c.deliverable);
  if (!reachable) return { gated: true, gateReason: "No reachable channel (email/WhatsApp)", rows: [], computed: 0 };
  if ((lead.geo ?? "").toUpperCase() !== "IN") return { gated: true, gateReason: "geo ≠ IN", rows: [], computed: 0 };

  const adText = String(attrs.ad_text ?? "");
  const category = String(attrs.category ?? "");
  const hasSocial = !!attrs.socials;
  const b = band(lead.follower_count);
  const nicheText = `${lead.niche ?? ""} ${category}`.toLowerCase();
  const nicheMatch = niches.some((n) => n && nicheText.includes(n));
  const competitor = tools.some((t) => t && adText.toLowerCase().includes(t));
  const verifiedEmail = channels.some((c) => c.type === "email" && c.deliverable && !c.opted_out && c.handle);

  // signal richness (capped)
  let signal = 0;
  if (adText.trim()) signal += w.signal_ad_text ?? 0;
  if (category.trim()) signal += w.signal_category ?? 0;
  if (hasSocial) signal += w.signal_social ?? 0;
  signal = Math.min(signal, w.signal_max ?? signal);

  const bandPts = b ? (w[`band_${b}`] ?? 0) : 0;
  const segClear = !!lead.segment;

  const rows: BreakdownRow[] = [
    { label: `Signals (ad_text/category/social, cap ${w.signal_max ?? 0})`, points: signal, fired: signal > 0 },
    { label: `Follower band: ${b ?? "unknown"}`, points: bandPts, fired: bandPts > 0 },
    { label: "Niche in target list", points: nicheMatch ? (w.niche_match ?? 0) : 0, fired: nicheMatch },
    { label: segClear ? "Segment clear" : "Segment ambiguous", points: segClear ? (w.segment_clear ?? 0) : (w.segment_ambiguous ?? 0), fired: true },
    { label: "Competitor-tool hint", points: competitor ? (w.competitor_hint ?? 0) : 0, fired: competitor },
    { label: "Verified email", points: verifiedEmail ? (w.verified_email ?? 0) : 0, fired: verifiedEmail },
  ];

  const sum = rows.reduce((a, r) => a + r.points, 0);
  return { gated: false, gateReason: null, rows, computed: Math.min(sum, w.score_cap ?? 100) };
}
