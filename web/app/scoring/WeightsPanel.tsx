"use client";

import { useState, useTransition } from "react";
import type { ScoringConfig } from "@/lib/types";
import { saveScoringConfig, triggerRescore } from "./actions";

const WEIGHT_LABELS: Record<string, string> = {
  signal_ad_text: "Ad text present",
  signal_category: "Category present",
  signal_social: "Has a social",
  signal_max: "Signal cap",
  band_nano: "Band: nano (<1k)",
  band_micro: "Band: micro (1k-100k)",
  band_mid: "Band: mid (100k-1M)",
  band_macro: "Band: macro (1M+)",
  niche_match: "Niche in target list",
  segment_clear: "Segment clear",
  segment_ambiguous: "Segment ambiguous",
  competitor_hint: "Competitor-tool hint",
  verified_email: "Verified email",
  score_cap: "Overall cap",
};

export function WeightsPanel({ config }: { config: ScoringConfig | null }) {
  const [weights, setWeights] = useState<Record<string, number>>(config?.weights ?? {});
  const [niches, setNiches] = useState<string>((config?.target_niches ?? []).join(", "));
  const [msg, setMsg] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  if (!config) {
    return (
      <div className="card border-amber-300 bg-amber-50 text-sm text-amber-800">
        scoring_config not found — run migration 0004 (it seeds the v1 defaults).
      </div>
    );
  }

  function save() {
    setMsg(null);
    startTransition(async () => {
      const target_niches = niches.split(",").map((s) => s.trim()).filter(Boolean);
      const res = await saveScoringConfig({ weights, target_niches });
      setMsg(res.ok ? "Saved. Re-score to apply." : `Error: ${res.error}`);
    });
  }

  function rescore() {
    setMsg(null);
    startTransition(async () => {
      const res = await triggerRescore();
      setMsg(res.ok ? `Re-score queued (job #${res.jobId}). The engine runs it.` : `Error: ${res.error}`);
    });
  }

  return (
    <div className="card">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-semibold">Scoring weights (editable)</h2>
        <div className="flex gap-2">
          <button className="btn-ghost" onClick={save} disabled={pending}>Save weights</button>
          <button className="btn" onClick={rescore} disabled={pending}>Re-score now</button>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        {Object.keys(weights).map((k) => (
          <label key={k} className="text-xs">
            <span className="block text-muted">{WEIGHT_LABELS[k] ?? k}</span>
            <input
              type="number"
              className="mt-0.5 w-full rounded border border-line px-2 py-1 text-sm tabular-nums"
              value={weights[k]}
              onChange={(e) => setWeights({ ...weights, [k]: Number(e.target.value) })}
            />
          </label>
        ))}
      </div>

      <label className="mt-3 block text-xs">
        <span className="block text-muted">Target-ICP niches (comma-separated, +{weights.niche_match ?? 0} each match)</span>
        <textarea
          className="mt-0.5 w-full rounded border border-line px-2 py-1 text-sm"
          rows={2}
          value={niches}
          onChange={(e) => setNiches(e.target.value)}
        />
      </label>

      {msg && <p className="mt-2 text-sm text-muted">{msg}</p>}
      <p className="mt-2 text-xs text-muted">
        Note: editing here updates <code>scoring_config</code> (source of truth). The Python
        scorer must be wired to read it (see web/README · backend TODO); until then it uses
        its committed defaults.
      </p>
    </div>
  );
}
