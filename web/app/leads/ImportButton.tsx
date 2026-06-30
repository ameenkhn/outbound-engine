"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { importLeads } from "./actions";

type Tab = "gsheet" | "file" | "paste";

export function ImportButton() {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<Tab>("gsheet");
  const [url, setUrl] = useState("");
  const [csv, setCsv] = useState("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [pending, start] = useTransition();

  function close() { setOpen(false); setMsg(null); setUrl(""); setCsv(""); }

  function onFile(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    if (!f) return;
    const r = new FileReader();
    r.onload = () => setCsv(String(r.result || ""));
    r.readAsText(f);
  }

  function submit() {
    setMsg(null);
    start(async () => {
      const res =
        tab === "gsheet"
          ? await importLeads({ mode: "gsheet", url })
          : await importLeads({ mode: "csv", csv });
      if (!res.ok) { setMsg({ ok: false, text: res.error }); return; }
      setMsg({ ok: true, text: `Imported ${res.inserted} lead(s)${res.skipped ? `, skipped ${res.skipped} with no contact` : ""}.` });
      router.refresh();
    });
  }

  return (
    <>
      <button className="btn-ghost" onClick={() => setOpen(true)}>Import</button>

      {open && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={close}>
          <div className="card w-full max-w-lg" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between">
              <h2 className="text-base font-semibold">Import leads</h2>
              <button className="text-muted hover:text-ink" onClick={close}>✕</button>
            </div>
            <p className="mt-1 text-xs text-muted">
              Bring in an existing list. We dedupe by email / phone / handle, so re-imports won't duplicate.
            </p>

            <div className="mt-4 flex gap-1 rounded-xl border border-line p-1 text-sm">
              {([["gsheet", "Google Sheet"], ["file", "CSV file"], ["paste", "Paste CSV"]] as [Tab, string][]).map(
                ([t, label]) => (
                  <button key={t}
                    className={"flex-1 rounded-lg px-3 py-1.5 font-medium transition-colors " +
                      (tab === t ? "bg-accent text-white" : "text-muted hover:text-ink")}
                    onClick={() => { setTab(t); setMsg(null); }}>
                    {label}
                  </button>
                ))}
            </div>

            <div className="mt-4">
              {tab === "gsheet" && (
                <div>
                  <label className="mb-1 block text-xs text-muted">Google Sheet link</label>
                  <input className="input" placeholder="https://docs.google.com/spreadsheets/d/…"
                    value={url} onChange={(e) => setUrl(e.target.value)} />
                  <p className="mt-1.5 text-[11px] text-muted">
                    The sheet must be shareable: <b>Share → Anyone with the link → Viewer</b>. First row = column headers
                    (email, phone, handle, name, niche…).
                  </p>
                </div>
              )}
              {tab === "file" && (
                <div>
                  <label className="mb-1 block text-xs text-muted">Choose a .csv file</label>
                  <input className="input" type="file" accept=".csv,text/csv" onChange={onFile} />
                  {csv && <p className="mt-1 text-[11px] text-muted">File loaded ({csv.split("\n").length - 1} rows).</p>}
                </div>
              )}
              {tab === "paste" && (
                <div>
                  <label className="mb-1 block text-xs text-muted">Paste CSV (with a header row)</label>
                  <textarea className="input font-mono text-xs" rows={6}
                    placeholder={"email,phone,handle,name,niche\nhi@x.in,+919876543210,@coach,Maya,yoga"}
                    value={csv} onChange={(e) => setCsv(e.target.value)} />
                </div>
              )}
            </div>

            {msg && (
              <p className={"mt-3 text-sm " + (msg.ok ? "text-green-600" : "text-red-600")}>{msg.text}</p>
            )}

            <div className="mt-4 flex justify-end gap-2">
              <button className="btn-ghost" onClick={close}>Close</button>
              <button className="btn" disabled={pending} onClick={submit}>
                {pending ? "Importing…" : "Import"}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
