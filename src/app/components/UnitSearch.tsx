import { useEffect, useRef, type ReactNode } from "react";
import { Search } from "lucide-react";
import { SEARCH_LIMIT } from "../lib/bankInventory";

// The unit search that sits at the top of both Inventory screens (a blood bank's and a hospital's), and the frame around its
// results. One component for both so the two pages cannot drift apart: same box, same help line, same "/" shortcut, same heading.

const plural = (n: number, one: string, many: string) => (n === 1 ? one : many);

export function UnitSearchBox({ query, onChange, placeholder }: { query: string; onChange: (q: string) => void; placeholder: string }) {
  const searchRef = useRef<HTMLInputElement>(null);

  // "/" jumps to the search from anywhere on the page that is not already a text field.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = document.activeElement as HTMLElement | null;
      if (e.key !== "/" || e.metaKey || e.ctrlKey || e.altKey) return;
      if (el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.isContentEditable)) return;
      e.preventDefault();
      searchRef.current?.focus();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <section aria-labelledby="find-unit-heading">
      <h2 id="find-unit-heading" className="sr-only">Find a unit</h2>
      <div className="relative max-w-2xl">
        <Search size={16} aria-hidden="true" className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-600" />
        <input
          ref={searchRef}
          type="search"
          value={query}
          onChange={(e) => onChange(e.target.value)}
          aria-label="Find a unit across every blood type"
          placeholder={placeholder}
          className="w-full h-11 pl-10 pr-3 text-[15px] border border-border rounded-md bg-card focus:border-primary"
        />
      </div>
      <p className="mt-1.5 text-[13px] text-foreground">
        Searches every blood type. A DIN matches from its start or anywhere inside it, with or without spaces. Press / to jump here.
      </p>
    </section>
  );
}

export function SearchResultsFrame({ total, query, onClear, children }: { total: number; query: string; onClear: () => void; children: ReactNode }) {
  return (
    <section aria-labelledby="results-heading">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 mb-2">
        <h2 id="results-heading" className="font-display font-bold text-xl tracking-tight text-foreground" aria-live="polite">
          {total === 0 ? `No unit matches "${query.trim()}"` : total <= SEARCH_LIMIT ? `${total} ${plural(total, "unit matches", "units match")} "${query.trim()}"` : `${total} units match "${query.trim()}"`}
        </h2>
        <button onClick={onClear} className="text-[14px] font-semibold text-role-accent-text rounded-sm hover:underline hover:underline-offset-2">Clear search</button>
      </div>
      {total > SEARCH_LIMIT && <p className="mb-2 text-[14px] text-foreground">Showing the first {SEARCH_LIMIT}: best DIN match first, then live units soonest to expire, then expired ones. Refine your search to narrow them.</p>}
      {total === 0 && <p className="text-[15px] text-foreground py-2">Try fewer characters of the DIN, or a shelf or component.</p>}
      {children}
    </section>
  );
}
