"use client";

import { useTransition } from "react";
import { exportLeadsCsv, type LeadFilters } from "./actions";

export function ExportButton({ filters }: { filters: LeadFilters }) {
  const [pending, start] = useTransition();

  function onClick() {
    start(async () => {
      const res = await exportLeadsCsv(filters);
      if (!res.ok) {
        alert("Export failed: " + res.error);
        return;
      }
      const a = document.createElement("a");
      a.href = URL.createObjectURL(new Blob([res.csv], { type: "text/csv" }));
      a.download = `leads-${new Date().toISOString().slice(0, 10)}.csv`;
      a.click();
      URL.revokeObjectURL(a.href);
    });
  }

  return (
    <button className="btn-ghost" disabled={pending} onClick={onClick}>
      {pending ? "Exporting…" : "Export CSV"}
    </button>
  );
}
