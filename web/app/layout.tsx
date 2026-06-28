import "./globals.css";
import type { Metadata } from "next";
import Link from "next/link";
import { ThemeToggle } from "@/components/ThemeToggle";

export const metadata: Metadata = {
  title: "Exly Outbound — CRM",
  description: "Operating console for the Exly Autonomous Outbound Engine.",
};

const NAV = [
  { href: "/", label: "Home" },
  { href: "/dashboard", label: "Dashboard" },
  { href: "/leads", label: "Leads" },
  { href: "/pipeline", label: "Pipeline" },
  { href: "/sourcing", label: "L1 · Sourcing" },
  { href: "/scoring", label: "L2 · Scoring" },
];

// Runs before paint: applies the saved (or system) theme so there's no flash.
const NO_FLASH = `(() => {
  try {
    const t = localStorage.getItem("theme");
    const dark = t ? t === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
    if (dark) document.documentElement.classList.add("dark");
  } catch (e) {}
})();`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: NO_FLASH }} />
      </head>
      <body>
        <header className="sticky top-0 z-20 border-b border-line bg-card/80 backdrop-blur">
          <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3">
            <Link href="/" className="flex items-center gap-2">
              <span className="flex h-7 w-7 items-center justify-center rounded-lg text-sm font-bold text-white"
                    style={{ backgroundImage: "linear-gradient(135deg, rgb(var(--accent)), rgb(var(--accent-2)))" }}>
                E
              </span>
              <span className="font-semibold tracking-tight">Exly Outbound</span>
            </Link>
            <nav className="flex flex-wrap gap-x-1 gap-y-1 text-sm">
              {NAV.map((n) => (
                <Link
                  key={n.href}
                  href={n.href}
                  className="rounded-md px-2.5 py-1 text-muted transition-colors hover:bg-bg hover:text-ink"
                >
                  {n.label}
                </Link>
              ))}
            </nav>
            <ThemeToggle />
          </div>
        </header>
        <main className="mx-auto max-w-7xl px-4 py-7">{children}</main>
      </body>
    </html>
  );
}
