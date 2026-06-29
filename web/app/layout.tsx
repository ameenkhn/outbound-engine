import "./globals.css";
import type { Metadata } from "next";
import { Sidebar } from "@/components/Sidebar";

export const metadata: Metadata = {
  title: "Exly Outbound — CRM",
  description: "Operating console for the Exly Autonomous Outbound Engine.",
};

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
        <Sidebar />
        <div className="md:pl-64">
          <main className="mx-auto max-w-6xl px-4 py-7 sm:px-6 lg:px-8">{children}</main>
        </div>
      </body>
    </html>
  );
}
