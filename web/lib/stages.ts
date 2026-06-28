import type { LeadStatus } from "@/lib/types";

// The lifecycle order (from migration 0001 lead_status_t). The funnel + board
// render in this order. dead/opted_out are "leakage" shown apart from the funnel.
export const FUNNEL_STAGES: LeadStatus[] = [
  "new",
  "queued",
  "contacted",
  "replied",
  "in_conversation",
  "demo_booked",
  "converted",
];

export const LEAKAGE_STAGES: LeadStatus[] = ["dead", "opted_out"];

export const ALL_STAGES: LeadStatus[] = [...FUNNEL_STAGES, ...LEAKAGE_STAGES];

export const STAGE_LABEL: Record<LeadStatus, string> = {
  new: "New",
  queued: "Queued",
  contacted: "Contacted",
  replied: "Replied",
  in_conversation: "In conversation",
  demo_booked: "Demo booked",
  converted: "Converted",
  dead: "Dead",
  opted_out: "Opted out",
};

// Tailwind classes per stage for the board column headers / pills.
export const STAGE_CLASS: Record<LeadStatus, string> = {
  new: "bg-slate-100 text-slate-700",
  queued: "bg-slate-100 text-slate-700",
  contacted: "bg-blue-100 text-blue-700",
  replied: "bg-indigo-100 text-indigo-700",
  in_conversation: "bg-violet-100 text-violet-700",
  demo_booked: "bg-amber-100 text-amber-700",
  converted: "bg-green-100 text-green-700",
  dead: "bg-slate-100 text-muted",
  opted_out: "bg-red-100 text-red-700",
};
