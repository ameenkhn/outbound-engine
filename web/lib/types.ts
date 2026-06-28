// TypeScript shapes for the subset of the frozen schema (migrations 0001/0003/0004)
// the L1 + L2 screens touch. These mirror the SQL — keep in sync with data/migrations.

export type LeadStatus =
  | "new" | "queued" | "contacted" | "replied" | "in_conversation"
  | "demo_booked" | "converted" | "dead" | "opted_out";

export type Segment = "creator" | "affiliate";
export type FollowerBand = "nano" | "micro" | "mid" | "macro";

export interface Lead {
  id: number;
  identity_key: string;
  segment: Segment | null;
  niche: string | null;
  platform: string | null;
  follower_band: FollowerBand | null;
  follower_count: number | null;
  icp_score: number | null;
  priority_rank: number | null;
  status: LeadStatus;
  geo: string;
  source: string | null;
  attributes: Record<string, unknown>;
  notes: string | null;            // 0004
  created_at: string;
  updated_at: string;
}

export type TargetMode = "deep" | "keyword";

export interface TargetSpec {
  id: number;
  mode: TargetMode;
  persona_text: string | null;
  seed_keywords: string[];
  expanded_keywords: string[];
  filters: Record<string, unknown>;
  attributes: Record<string, unknown>;   // youtube_status / youtube_resume live here
  approved: boolean;
  created_by_model: string | null;
  created_at: string;
}

export interface ScoringConfig {
  id: 1;
  weights: Record<string, number>;
  target_niches: string[];
  competitor_tools: string[];
  updated_at: string;
  updated_by: string | null;
}

export type AppJobKind = "rescore" | "mode_b" | "mode_a" | "source_run" | "approve_spec";
export type AppJobStatus = "pending" | "claimed" | "done" | "failed";

export interface AppJob {
  id: number;
  kind: AppJobKind;
  payload: Record<string, unknown>;
  status: AppJobStatus;
  attempts: number;
  result: Record<string, unknown> | null;
  last_error: string | null;
  requested_by: string | null;
  created_at: string;
  updated_at: string;
}
