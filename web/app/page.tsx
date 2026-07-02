import Link from "next/link";

const SECTIONS = [
  { href: "/sourcing", title: "Sourcing", tag: "L1", desc: "Harvest ICP creators across Meta, Instagram, LinkedIn, YouTube & web search.", icon: "M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16zM21 21l-4.3-4.3" },
  { href: "/leads", title: "Leads", tag: "Data", desc: "Every lead — deduped, scored, filterable, with contacts. Import from Sheets/CSV.", icon: "M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8" },
  { href: "/compose", title: "Compose", tag: "AI", desc: "Write niche-tailored WhatsApp & email — templates or AI — with a live phone preview.", icon: "M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z" },
  { href: "/pipeline", title: "Pipeline", tag: "CRM", desc: "Move leads through the lifecycle from new to demo, all in one board.", icon: "M3 6h18M7 12h10M10 18h4" },
  { href: "/dashboard", title: "Dashboard", tag: "Ops", desc: "Funnel, reply & demo rates, sending reputation, and what needs you.", icon: "M4 13h6V4H4zM14 20h6V4h-6zM4 20h6v-4H4z" },
  { href: "/scoring", title: "Scoring", tag: "L2", desc: "ICP score distribution, the priority queue, and tune the weights.", icon: "M12 2 15 9l7 .5-5.5 4.5L18 21l-6-3.7L6 21l1.5-7L2 9.5 9 9z" },
];

function Icon({ d }: { d: string }) {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d={d} /></svg>
  );
}

export default function Home() {
  return (
    <div className="space-y-10">
      {/* hero */}
      <section className="relative overflow-hidden rounded-3xl border border-line bg-card px-6 py-14 sm:px-10">
        <div className="aurora" style={{ width: 320, height: 320, top: -80, right: -40, background: "rgb(var(--accent))" }} />
        <div className="aurora" style={{ width: 280, height: 280, bottom: -100, left: -60, background: "rgb(var(--accent-2))", animationDelay: "-4s" }} />
        <div className="relative">
          <span className="inline-flex items-center gap-2 rounded-full border border-line bg-bg px-3 py-1 text-xs font-medium text-muted rise">
            <span className="h-1.5 w-1.5 rounded-full bg-green-500" /> Live · sourcing → outreach engine
          </span>
          <h1 className="mt-4 max-w-3xl text-4xl font-bold tracking-tight sm:text-5xl rise-1">
            Turn creators into customers, <span className="grad-text">on autopilot</span>.
          </h1>
          <p className="mt-4 max-w-2xl text-muted rise-2">
            Source ICP creators across five channels, dedupe and score them automatically, write
            niche-perfect WhatsApp &amp; email, and work every lead to a demo — from one console.
          </p>
          <div className="mt-6 flex flex-wrap gap-3 rise-3">
            <Link href="/sourcing" className="btn">Start sourcing →</Link>
            <Link href="/compose" className="btn-ghost">✨ Compose a message</Link>
          </div>
        </div>
      </section>

      {/* section grid */}
      <section className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {SECTIONS.map((s, i) => (
          <Link key={s.href} href={s.href}
            className="card card-hover group flex flex-col rise"
            style={{ animationDelay: `${0.05 * i}s` }}>
            <div className="flex items-center justify-between">
              <span className="flex h-10 w-10 items-center justify-center rounded-xl text-white shadow-sm transition-transform group-hover:scale-110"
                style={{ backgroundImage: "linear-gradient(135deg, rgb(var(--accent)), rgb(var(--accent-2)))" }}>
                <Icon d={s.icon} />
              </span>
              <span className="pill bg-indigo-100 text-indigo-700">{s.tag}</span>
            </div>
            <h2 className="mt-3 font-semibold">{s.title}</h2>
            <p className="mt-1 text-sm text-muted">{s.desc}</p>
            <span className="mt-3 text-sm font-medium text-accent">Open →</span>
          </Link>
        ))}
      </section>
    </div>
  );
}
