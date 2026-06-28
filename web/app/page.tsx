import Link from "next/link";

export default function Home() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">Operating console</h1>
        <p className="mt-1 text-sm text-muted">
          The CRM front end for the Exly Outbound Engine. v1 ships the two screens whose
          backend is already built and was CLI-only: targeting/sourcing (L1) and ICP
          scoring (L2). Dashboard, pipeline, conversations and ops land next.
        </p>
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <Link href="/sourcing" className="card hover:border-accent">
          <h2 className="font-medium">L1 · Sourcing &amp; Targeting</h2>
          <p className="mt-1 text-sm text-muted">
            Expand keywords (Mode B), build a persona (Mode A), review &amp; approve target
            specs, and kick Meta / YouTube source runs.
          </p>
        </Link>
        <Link href="/scoring" className="card hover:border-accent">
          <h2 className="font-medium">L2 · Enrichment &amp; Scoring</h2>
          <p className="mt-1 text-sm text-muted">
            See the ICP score distribution, the priority queue the dispatcher works,
            why each lead scored, and tune the weights, then re-score.
          </p>
        </Link>
      </div>
    </div>
  );
}
