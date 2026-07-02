"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { ThemeToggle } from "./ThemeToggle";

type Item = { href: string; label: string; icon: React.ReactNode };

const I = (d: string) => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
    <path d={d} />
  </svg>
);

const NAV: Item[] = [
  { href: "/", label: "Home", icon: I("M3 11.5 12 4l9 7.5M5 10v10h14V10") },
  { href: "/dashboard", label: "Dashboard", icon: I("M4 13h6V4H4zM14 20h6V4h-6zM4 20h6v-4H4z") },
  { href: "/leads", label: "Leads", icon: I("M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8M22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75") },
  { href: "/compose", label: "Compose", icon: I("M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z") },
  { href: "/pipeline", label: "Pipeline", icon: I("M3 6h18M7 12h10M10 18h4") },
  { href: "/sourcing", label: "Sourcing", icon: I("M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16zM21 21l-4.3-4.3") },
  { href: "/scoring", label: "Scoring", icon: I("M12 2 15 9l7 .5-5.5 4.5L18 21l-6-3.7L6 21l1.5-7L2 9.5 9 9z") },
];

function NavItems({ pathname, onNav }: { pathname: string; onNav?: () => void }) {
  return (
    <>
      {NAV.map((n) => {
        const active = n.href === "/" ? pathname === "/" : pathname.startsWith(n.href);
        return (
          <Link key={n.href} href={n.href} data-active={active} className="nav-link" onClick={onNav}>
            <span className={active ? "text-accent" : "text-muted"}>{n.icon}</span>
            {n.label}
          </Link>
        );
      })}
    </>
  );
}

function Brand() {
  return (
    <Link href="/" className="flex items-center gap-2.5">
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src="/exly-logo.svg" alt="Exly" width={32} height={32}
        className="h-8 w-8 rounded-xl shadow-sm" />
      <span className="text-[15px] font-semibold tracking-tight">Outbound</span>
    </Link>
  );
}

export function Sidebar() {
  const pathname = usePathname();
  return (
    <>
      {/* desktop sidebar */}
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-64 flex-col border-r border-line bg-card/70 px-3.5 py-5 backdrop-blur-xl md:flex">
        <div className="px-2">
          <Brand />
        </div>
        <p className="mt-1 px-2 text-[11px] text-muted">Acquisition engine</p>
        <nav className="mt-7 flex flex-1 flex-col gap-1">
          <NavItems pathname={pathname} />
        </nav>
        <div className="mt-auto flex items-center justify-between border-t border-line px-2 pt-4">
          <span className="text-[11px] text-muted">v1 · internal</span>
          <ThemeToggle />
        </div>
      </aside>

      {/* mobile top bar */}
      <header className="sticky top-0 z-30 flex items-center gap-3 border-b border-line bg-card/80 px-4 py-3 backdrop-blur-xl md:hidden">
        <Brand />
        <div className="ml-auto"><ThemeToggle /></div>
      </header>
      <nav className="sticky top-[57px] z-20 flex gap-1 overflow-x-auto border-b border-line bg-card/80 px-3 py-2 backdrop-blur-xl md:hidden">
        <NavItems pathname={pathname} />
      </nav>
    </>
  );
}
