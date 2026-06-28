"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { saveNotes, suppressFromLead } from "./actions";

export function NotesEditor({ leadId, initial }: { leadId: number; initial: string }) {
  const router = useRouter();
  const [notes, setNotes] = useState(initial);
  const [msg, setMsg] = useState<string | null>(null);
  const [pending, start] = useTransition();

  return (
    <div>
      <textarea
        className="w-full rounded border border-line px-2 py-1.5 text-sm"
        rows={4}
        placeholder="Operator notes…"
        value={notes}
        onChange={(e) => setNotes(e.target.value)}
      />
      <div className="mt-2 flex items-center gap-2">
        <button
          className="btn"
          disabled={pending}
          onClick={() =>
            start(async () => {
              const r = await saveNotes(leadId, notes);
              setMsg(r.ok ? "Saved" : `Error: ${r.error}`);
              if (r.ok) router.refresh();
            })
          }
        >
          Save notes
        </button>
        {msg && <span className="text-xs text-muted">{msg}</span>}
      </div>
    </div>
  );
}

export function OptOutButton({ leadId, identityKey }: { leadId: number; identityKey: string }) {
  const router = useRouter();
  const [msg, setMsg] = useState<string | null>(null);
  const [pending, start] = useTransition();
  return (
    <span className="flex items-center gap-2">
      <button
        className="btn-ghost"
        disabled={pending}
        onClick={() =>
          start(async () => {
            const r = await suppressFromLead(leadId, identityKey);
            setMsg(r.ok ? "Opted out + suppressed" : `Error: ${r.error}`);
            if (r.ok) router.refresh();
          })
        }
      >
        Opt-out / suppress
      </button>
      {msg && <span className="text-xs text-muted">{msg}</span>}
    </span>
  );
}
