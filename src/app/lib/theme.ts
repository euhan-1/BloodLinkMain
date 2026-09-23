// Light/dark preference — a real user choice (AccountMenu's toggle), not a
// system-preference mirror: a first-time visitor with nothing stored gets
// light, regardless of their OS setting, and only an explicit toggle click
// (setTheme, persisted below) ever moves them to dark. The .dark class this
// applies to <html> is what every color token in theme.css branches on (see
// the .dark block there). index.html carries a matching inline script that
// applies the same stored choice before React ever mounts, so there's no
// flash of the wrong theme on load — STORAGE_KEY and the light default must
// stay in sync with that script.

export type Theme = "light" | "dark";

const STORAGE_KEY = "bloodlink-theme";

export function getStoredTheme(): Theme {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === "light" || stored === "dark") return stored;
  } catch {
    // localStorage unavailable (private browsing, etc.) — fall through
  }
  return "light";
}

function applyTheme(theme: Theme): void {
  document.documentElement.classList.toggle("dark", theme === "dark");
}

export function setTheme(theme: Theme): void {
  applyTheme(theme);
  try {
    localStorage.setItem(STORAGE_KEY, theme);
  } catch {
    // best-effort persistence — theme still applies for this session
  }
}
