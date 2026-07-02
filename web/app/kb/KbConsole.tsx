"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { addKbDoc, deleteKbDoc, type KbDoc } from "./actions";

export function KbConsole({ docs }: { docs: KbDoc[] }) {
  const router = useRouter();
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [tags, setTags] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [pending, start] = useTransition();

  function add() {
    setMsg(null);
    start(async () => {
      const r = await addKbDoc({ title, content, tags });
      if (r.ok) { setTitle(""); setContent(""); setTags(""); setMsg("Added ✓"); router.refresh(); }
      else setMsg(`Error: ${r.error}`);
    });
  }
  function remove(id: number) {
    start(async () => { const r = await deleteKbDoc(id); if (r.ok) router.refresh(); else setMsg(`Error: ${r.error}`); });
  }

  return (
    <div className="space-y-6 rise">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Knowledge base</h1>
        <p className="mt-1 text-sm text-muted">Facts the AI uses to answer leads. Add clear, factual entries — the auto-reply retrieves the most relevant ones.</p>
      </div>

      <div className="card space-y-3">
        <h2 className="text-sm font-semibold">Add an entry</h2>
        <input className="input" placeholder="Title (e.g. Refund policy)" value={title} onChange={(e) => setTitle(e.target.value)} />
        <textarea className="input" rows={4} placeholder="Content — the factual answer the AI can use…" value={content} onChange={(e) => setContent(e.target.value)} />
        <input className="input" placeholder="Tags (optional, comma-separated)" value={tags} onChange={(e) => setTags(e.target.value)} />
        <div className="flex items-center gap-2">
          <button className="btn" disabled={pending} onClick={add}>{pending ? "Saving…" : "Add entry"}</button>
          {msg && <span className="text-xs text-muted">{msg}</span>}
        </div>
      </div>

      <div className="space-y-2">
        <h2 className="text-sm font-semibold">Entries ({docs.length})</h2>
        {docs.map((d) => (
          <div key={d.id} className="card">
            <div className="flex items-start justify-between gap-3">
              <div>
                <div className="font-medium">{d.title}</div>
                <div className="mt-0.5 text-sm text-muted">{d.content}</div>
                {d.tags && <div className="mt-1 text-[11px] text-muted">🏷 {d.tags}</div>}
              </div>
              <button className="btn-ghost shrink-0 px-2 py-1 text-xs text-red-600" disabled={pending} onClick={() => remove(d.id)}>Delete</button>
            </div>
          </div>
        ))}
        {docs.length === 0 && <p className="text-sm text-muted">No entries yet. Add your first fact above.</p>}
      </div>
    </div>
  );
}
