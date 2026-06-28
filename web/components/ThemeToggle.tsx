"use client";

import { useEffect, useState } from "react";

/** Light/dark toggle. Persists to localStorage and flips `.dark` on <html>.
 *  The no-flash inline script in layout.tsx sets the class before paint; this
 *  just keeps the button in sync and lets the user switch. */
export function ThemeToggle() {
  const [dark, setDark] = useState<boolean>(false);

  useEffect(() => {
    setDark(document.documentElement.classList.contains("dark"));
  }, []);

  function toggle() {
    const next = !document.documentElement.classList.contains("dark");
    document.documentElement.classList.toggle("dark", next);
    try {
      localStorage.setItem("theme", next ? "dark" : "light");
    } catch {
      /* ignore storage failures (private mode etc.) */
    }
    setDark(next);
  }

  return (
    <button
      onClick={toggle}
      aria-label="Toggle dark mode"
      title={dark ? "Switch to light" : "Switch to dark"}
      className="ml-auto rounded-md border border-line px-2 py-1 text-sm text-muted hover:text-ink"
    >
      {dark ? "☀︎" : "☾"}
    </button>
  );
}
