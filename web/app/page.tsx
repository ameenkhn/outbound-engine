import Link from "next/link";

const SECTIONS = [
  {
    href: "/dashboard",
    title: "Dashboard",
    desc: "Funnel, reply & demo rates, sending reputation, and what's awaiting you — at a glance.",
    tag: "Overview",
  },
  {
    href: "/leads",
    title: "Leads",
    desc: "Every sourced creator, scored and filterable by platform, niche, status and ICP score. Export to CSV.",
    tag: "Data",
  },
  {
    href: "/sourcing",
    title: "Sourcing & Targeting",
    desc: "Expand keywords or build a persona, approve target specs, and run Meta / Instagram / LinkedIn / YouTube.",
    tag: "L1",
  },
  {
    href: "/scoring",
    title: "Enrichment & Scoring",
    desc: "See the ICP score distribution, the priority queue, why each lead scored, and tune the weights.",
    tag: "L2",
  },
  {
    href: "/pipeline",
    title: "Pipeline",
    desc: "Move leads through the lifecycle and keep tabs on conversations and demos.",
    tag: "CRM",
  },
];

export default function Home() {
  return (
    <div className="space-y-8">
      {/* hero */}
      <section className="rise">
        <p className="text-sm font-medium text-accent">Creator &amp; affiliate acquisition engine</p>
        <h1 className="mt-1 text-3xl font-bold tracking-tight sm:text-4xl">
          The <span className="grad-text">Outbound</span> operating console
        </h1>
        <p className="mt-3 max-w-2xl text-muted">
          Source ICP creators across five channels, score and de-duplicate them automatically,
          and work every lead from discovery to demo — all from one place.
        </p>
        <div className="mt-5 flex flex-wrap gap-3">
          <Link href="/sourcing" className="btn">Start sourcing →</Link>
          <Link href="/leads" className="btn-ghost">Browse leads</Link>
        </div>
      </section>

      {/* section cards */}
      <section className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {SECTIONS.map((s) => (
          <Link key={s.href} href={s.href} className="card card-hover rise flex flex-col">
            <div className="flex items-center justify-between">
              <h2 className="font-semibold">{s.title}</h2>
              <span className="pill bg-indigo-100 text-indigo-700">{s.tag}</span>
            </div>
            <p className="mt-2 text-sm text-muted">{s.desc}</p>
            <span className="mt-3 text-sm font-medium text-accent">Open →</span>
          </Link>
        ))}
      </section>
    </div>
  );
}
