import { listKb } from "./actions";
import { KbConsole } from "./KbConsole";

export const dynamic = "force-dynamic";
export const metadata = { title: "Knowledge base — Exly Outbound" };

export default async function KbPage() {
  const r = await listKb();
  if (!r.ok) {
    return (
      <div className="card border-amber-300 bg-amber-50">
        <h2 className="font-medium text-amber-900">Couldn’t load the knowledge base</h2>
        <p className="mt-1 text-sm text-amber-800">{r.error}</p>
      </div>
    );
  }
  return <KbConsole docs={r.docs} />;
}
