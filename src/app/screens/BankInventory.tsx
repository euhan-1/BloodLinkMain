import { useEffect, useMemo, useRef, useState } from "react";
import { Archive, ChevronDown, Search, SlidersHorizontal, Trash2 } from "lucide-react";
import { BloodTypeBadge, StatusDot } from "../components/BloodTypeBadge";
import { Skeleton } from "../components/Skeleton";
import { StockBar } from "../components/StockBar";
import { formatCalendarDate } from "../lib/dateFormat";
import { type InventorySummaryRow, type InventoryUnit } from "../lib/inventoryTypes";
import { BLOOD_TYPE_ORDER, EXPIRY_NEAR_DAYS, STATUS_STYLES, STOCK_LABELS, getExpiryStatus } from "../lib/statusTokens";
import {
  NO_UNIT_FILTERS, SEARCH_LIMIT, STATE_LABELS, WINDOW_OPTIONS, backlog, filterUnits, hasUnitFilters, pageOf,
  searchUnits, sortUnits, typeSummaries, unitNote, type TypeSummary, type UnitFilters, type UnitState, type WindowFilter,
} from "../lib/bankInventory";

// What a blood bank came to Inventory for: where every unit it holds is, and which ones need it to act. It holds hundreds of
// units, so the page is a table of its eight types (stock against minimum, how many expire soon, how many have expired), each
// of which opens to its units, soonest expiry first (the order release picks them in), fifty to a page. Search sits at the top
// and covers every type, because a bank looks a unit up by its DIN far more often than it browses for one.
//
// The expired backlog is the headline: units past their expiry are not stock and release never offers them, yet they sit on the
// list until someone clears them, so the first thing on the page says how many and offers to clear them.
//
// Flat on the page background, rows separated by hairlines, no cards. Colour is status only: the three stock states, and the
// expiry tones for time left.

const HAIRLINE = "border-gray-200";
const TYPE_COLUMNS = "lg:grid-cols-[4.75rem_minmax(8rem,1.5fr)_5rem_9.5rem_5.5rem_5.5rem_9rem]";
const UNIT_COLUMNS = "lg:grid-cols-[10.5rem_7rem_6.5rem_5rem_7.5rem_8.5rem_minmax(10rem,1fr)_2.5rem]";
const SEARCH_COLUMNS = "lg:grid-cols-[10.5rem_3.5rem_7rem_6.5rem_5rem_7.5rem_8.5rem_minmax(9rem,1fr)_2.5rem]";
const FIELD = "h-9 px-2.5 text-[14px] text-foreground border border-border rounded-md bg-card";
const plural = (n: number, one: string, many: string) => (n === 1 ? one : many);

export type BankInventoryProps = {
  units: InventoryUnit[];
  loading: boolean;
  error: string | null;
  summary: Record<string, InventorySummaryRow>;
  summaryLoading: boolean;
  summaryError: string | null;
  clearing: { busy: boolean; error: string | null; lastCleared: number | null; onClear: () => void };
  onEditThresholds: (type: string) => void;
  onRemoveUnit: (din: string) => void;
};

export function BankInventoryView(p: BankInventoryProps) {
  const [query, setQuery] = useState("");
  const searching = query.trim() !== "";
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

  const loading = p.loading || p.summaryLoading;
  const error = p.error ?? p.summaryError;

  return (
    <div className="space-y-6">
      {!p.loading && !p.error && <ExpiredBacklog units={p.units} clearing={p.clearing} />}

      <section aria-labelledby="find-unit-heading">
        <h2 id="find-unit-heading" className="sr-only">Find a unit</h2>
        <div className="relative max-w-2xl">
          <Search size={16} aria-hidden="true" className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-600" />
          <input
            ref={searchRef}
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            aria-label="Find a unit across every blood type"
            placeholder="Find a unit by DIN, shelf or component"
            className="w-full h-11 pl-10 pr-3 text-[15px] border border-border rounded-md bg-card focus:border-primary"
          />
        </div>
        <p className="mt-1.5 text-[13px] text-foreground">
          Searches every blood type. A DIN matches from its start or anywhere inside it, with or without spaces. Press / to jump here.
        </p>
      </section>

      {loading && (
        <div className="space-y-px" aria-busy="true" aria-label="Loading inventory">
          {BLOOD_TYPE_ORDER.map((t) => <Skeleton key={t} className="h-11 w-full" />)}
        </div>
      )}
      {!loading && error && <p role="alert" className="text-[15px] text-status-critical-text">Failed to load inventory: {error}</p>}

      {!loading && !error && searching && <SearchResults units={p.units} query={query} onRemoveUnit={p.onRemoveUnit} onClear={() => setQuery("")} />}
      {!loading && !error && !searching && <TypeTable {...p} />}
    </div>
  );
}

// ─── The expired backlog (the headline) ─────────────────────────────────────

function ExpiredBacklog({ units, clearing }: { units: InventoryUnit[]; clearing: BankInventoryProps["clearing"] }) {
  const b = backlog(units);
  const [confirming, setConfirming] = useState(false);
  useEffect(() => { if (!clearing.busy && b.clearable === 0) setConfirming(false); }, [clearing.busy, b.clearable]);

  if (b.expired === 0) {
    return clearing.lastCleared !== null ? (
      <p role="status" className="text-[15px] font-semibold text-status-safe-text">
        Cleared {clearing.lastCleared} expired {plural(clearing.lastCleared, "unit", "units")}. Nothing expired is left on the list.
      </p>
    ) : null;
  }
  return (
    <section aria-labelledby="backlog-heading" className="rounded-xl bg-status-critical-tint px-5 py-4 sm:px-6">
      <h2 id="backlog-heading" className="font-display font-bold text-xl sm:text-2xl tracking-tight text-status-critical-text">
        {b.expired} expired {plural(b.expired, "unit is", "units are")} still on your list
      </h2>
      <p className="mt-1.5 text-[15px] text-foreground max-w-[75ch]">
        {b.usable} of the {b.total} units listed {plural(b.usable, "is", "are")} usable. The expired ones are not counted in your stock and release will
        never offer them, but they stay here until you clear them. Clearing archives them: they leave this list and stay on file, nothing is deleted.
        {b.held > 0 && ` ${b.held} expired ${plural(b.held, "unit is", "units are")} still reserved for a request and ${plural(b.held, "stays", "stay")} until that request settles.`}
      </p>
      {b.clearable > 0 && !confirming && (
        <button
          onClick={() => setConfirming(true)}
          className="mt-3 inline-flex items-center gap-2 h-10 px-4 rounded-md bg-status-critical-text text-white text-[14px] font-semibold hover:opacity-90 transition-opacity"
        >
          <Archive size={15} aria-hidden="true" /> Clear {b.clearable} expired {plural(b.clearable, "unit", "units")}
        </button>
      )}
      {b.clearable > 0 && confirming && (
        <div role="alertdialog" aria-label="Confirm clearing expired units" className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2">
          <span className="text-[15px] font-semibold text-foreground">Archive {b.clearable} expired {plural(b.clearable, "unit", "units")}? This writes to your live inventory.</span>
          <span className="flex gap-2">
            <button
              onClick={() => { clearing.onClear(); }}
              disabled={clearing.busy}
              className="h-10 px-4 rounded-md bg-status-critical-text text-white text-[14px] font-semibold hover:opacity-90 transition-opacity disabled:opacity-60"
            >
              {clearing.busy ? "Clearing…" : `Archive ${b.clearable}`}
            </button>
            <button
              onClick={() => setConfirming(false)}
              disabled={clearing.busy}
              className="h-10 px-4 rounded-md border border-border bg-card text-[14px] font-semibold text-foreground hover:bg-secondary transition-colors"
            >
              Cancel
            </button>
          </span>
        </div>
      )}
      {clearing.error && <p role="alert" className="mt-2 text-[14px] font-semibold text-status-critical-text">{clearing.error}</p>}
    </section>
  );
}

// ─── The table of types ─────────────────────────────────────────────────────

function TypeTable(p: BankInventoryProps) {
  const [open, setOpen] = useState<Set<string>>(() => new Set());
  const rows = useMemo(() => typeSummaries(p.units, p.summary), [p.units, p.summary]);
  const toggle = (type: string) => setOpen((prev) => { const next = new Set(prev); if (next.has(type)) next.delete(type); else next.add(type); return next; });
  return (
    <div role="table" aria-label="Stock by blood type">
      <div role="row" className={`hidden lg:grid ${TYPE_COLUMNS} items-end gap-x-4 pb-2 border-b border-gray-300 text-[13px] font-semibold text-gray-600`}>
        <div role="columnheader">Type</div>
        <div role="columnheader">Stock against minimum</div>
        <div role="columnheader" className="text-right">Units</div>
        <div role="columnheader">Status</div>
        <div role="columnheader" className="text-right" title={`Units that expire within ${EXPIRY_NEAR_DAYS} days, not counting any reserved for a request`}>Expiring</div>
        <div role="columnheader" className="text-right">Expired</div>
        <div role="columnheader"><span className="sr-only">Thresholds</span></div>
      </div>
      {rows.map((r) => (
        <TypeRow key={r.type} r={r} open={open.has(r.type)} onToggle={() => toggle(r.type)} units={p.units.filter((u) => u.type === r.type)}
          onEditThresholds={p.onEditThresholds} onRemoveUnit={p.onRemoveUnit} />
      ))}
    </div>
  );
}

function TypeRow({
  r, open, onToggle, units, onEditThresholds, onRemoveUnit,
}: { r: TypeSummary; open: boolean; onToggle: () => void; units: InventoryUnit[]; onEditThresholds: (type: string) => void; onRemoveUnit: (din: string) => void }) {
  const panelId = `units-${r.type.replace("+", "pos").replace("-", "neg")}`;
  return (
    <div role="rowgroup" className={`border-b ${HAIRLINE}`}>
      {/* The whole row toggles for the mouse; the chevron button is the keyboard and screen-reader control. */}
      <div
        role="row"
        onClick={onToggle}
        className={`flex flex-wrap items-center gap-x-4 gap-y-1.5 py-3 lg:py-0 lg:min-h-12 lg:grid ${TYPE_COLUMNS} lg:gap-x-4 cursor-pointer hover:bg-secondary/60 transition-colors`}
      >
        <div role="cell" className="order-1 lg:order-none flex items-center gap-2">
          <button
            aria-expanded={open}
            aria-controls={panelId}
            aria-label={`${open ? "Hide" : "Show"} the units of ${r.type}`}
            className="p-1 -ml-1 rounded-md text-gray-600 hover:text-foreground"
          >
            <ChevronDown size={16} className={`transition-transform ${open ? "" : "-rotate-90"}`} />
          </button>
          <BloodTypeBadge type={r.type} size="sm" />
        </div>
        <div role="cell" className="order-2 lg:order-none flex-1 min-w-[6rem]">
          {r.minimum !== null && r.maximum !== null ? <StockBar units={r.units} minimum={r.minimum} maximum={r.maximum} /> : <span className="text-[13px] text-gray-600">No thresholds set</span>}
        </div>
        <div role="cell" className={`order-3 lg:order-none text-right text-[16px] font-bold tabular-nums ${r.level && r.level !== "safe" ? STATUS_STYLES[r.level].text : "text-foreground"}`}>
          {r.units}<span className="sr-only"> {plural(r.units, "unit", "units")} usable</span>
        </div>
        <div aria-hidden="true" className="order-4 basis-full h-0 lg:hidden" />
        <div role="cell" className="order-5 lg:order-none flex items-center gap-2 text-[14px] font-semibold">
          {r.level === null ? <span className="text-foreground">Not set</span>
            : r.notStocked ? <span className="text-foreground">Not stocked</span>
            : (<><StatusDot status={r.level} /><span className={r.level === "safe" ? "text-foreground" : STATUS_STYLES[r.level].text}>{STOCK_LABELS[r.level]}</span></>)}
        </div>
        <div role="cell" className={`order-6 lg:order-none lg:text-right text-[14px] tabular-nums ${r.expiring > 0 ? "font-semibold text-status-watch-text" : "text-foreground"}`}>
          <span className="lg:hidden text-gray-600 font-normal">Expiring </span>{r.expiring}
        </div>
        <div role="cell" className={`order-7 lg:order-none lg:text-right text-[14px] tabular-nums ${r.expired > 0 ? "font-semibold text-status-critical-text" : "text-foreground"}`}>
          <span className="lg:hidden text-gray-600 font-normal">Expired </span>{r.expired}
        </div>
        <div role="cell" className="order-8 lg:order-none ml-auto lg:ml-0 lg:text-right">
          <button
            onClick={(e) => { e.stopPropagation(); onEditThresholds(r.type); }}
            title="Set minimum and maximum stock thresholds"
            className="inline-flex items-center gap-1.5 h-8 px-2.5 -mr-2.5 rounded-md text-[13px] font-semibold text-foreground hover:bg-secondary transition-colors"
          >
            <SlidersHorizontal size={14} aria-hidden="true" /><span className="hidden lg:inline" aria-hidden="true">Thresholds</span><span className="sr-only">Thresholds for {r.type}</span>
          </button>
        </div>
      </div>
      {open && (
        <div id={panelId} className="pb-5 lg:pl-9">
          <UnitsPanel type={r.type} units={units} onRemoveUnit={onRemoveUnit} />
        </div>
      )}
    </div>
  );
}

// ─── One type's units ───────────────────────────────────────────────────────

const STATE_FILTER_OPTIONS: { value: "any" | UnitState; label: string }[] = [
  { value: "any", label: "Any status" },
  { value: "available", label: STATE_LABELS.available },
  { value: "reserved", label: STATE_LABELS.reserved },
  { value: "inside_margin", label: STATE_LABELS.inside_margin },
  { value: "expired", label: STATE_LABELS.expired },
];

function UnitsPanel({ type, units, onRemoveUnit }: { type: string; units: InventoryUnit[]; onRemoveUnit: (din: string) => void }) {
  const [filters, setFilters] = useState<UnitFilters>(NO_UNIT_FILTERS);
  const [page, setPage] = useState(1);
  const components = useMemo(() => Array.from(new Set(units.map((u) => u.component))).sort(), [units]);
  const sorted = useMemo(() => sortUnits(units), [units]);
  const shown = useMemo(() => filterUnits(sorted, filters), [sorted, filters]);
  const set = (patch: Partial<UnitFilters>) => { setFilters((f) => ({ ...f, ...patch })); setPage(1); };
  const pg = pageOf(shown, page);

  if (units.length === 0) return <p className="text-[15px] text-foreground py-2">No {type} units on the list.</p>;
  return (
    <div>
      <div className="flex flex-wrap items-end gap-x-4 gap-y-2 pb-3">
        <label className="text-[13px] font-semibold text-foreground">
          <span className="block mb-1">Component</span>
          <select value={filters.component} onChange={(e) => set({ component: e.target.value })} className={`${FIELD} w-[9.5rem]`}>
            <option value="">Any component</option>
            {components.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        </label>
        <label className="text-[13px] font-semibold text-foreground">
          <span className="block mb-1">Expires</span>
          <select value={filters.window} onChange={(e) => set({ window: e.target.value as WindowFilter })} className={`${FIELD} w-[9.5rem]`}>
            {WINDOW_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </label>
        <label className="text-[13px] font-semibold text-foreground">
          <span className="block mb-1">Status</span>
          <select value={filters.state} onChange={(e) => set({ state: e.target.value as "any" | UnitState })} className={`${FIELD} w-[15rem] max-w-full`}>
            {STATE_FILTER_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </label>
        {hasUnitFilters(filters) && (
          <button onClick={() => { setFilters(NO_UNIT_FILTERS); setPage(1); }} className="h-9 text-[14px] text-foreground underline underline-offset-2 hover:text-primary">Clear filters</button>
        )}
      </div>
      <UnitTable units={pg.items} showType={false} onRemoveUnit={onRemoveUnit} empty={`No ${type} units match these filters.`} />
      <Pager pg={pg} noun="units" onPage={setPage} />
    </div>
  );
}

function Pager({ pg, noun, onPage }: { pg: ReturnType<typeof pageOf>; noun: string; onPage: (n: number) => void }) {
  if (pg.total === 0) return null;
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 pt-3 text-[14px] text-foreground">
      <span aria-live="polite">Showing {pg.from} to {pg.to} of {pg.total} {noun}</span>
      {pg.pages > 1 && (
        <span className="flex items-center gap-2">
          <button onClick={() => onPage(pg.page - 1)} disabled={pg.page <= 1} className="h-9 px-3 rounded-md border border-border bg-card text-[13px] font-semibold hover:bg-secondary disabled:opacity-50 transition-colors">Previous</button>
          <span className="tabular-nums">Page {pg.page} of {pg.pages}</span>
          <button onClick={() => onPage(pg.page + 1)} disabled={pg.page >= pg.pages} className="h-9 px-3 rounded-md border border-border bg-card text-[13px] font-semibold hover:bg-secondary disabled:opacity-50 transition-colors">Next</button>
        </span>
      )}
    </div>
  );
}

// ─── Search results ─────────────────────────────────────────────────────────

function SearchResults({ units, query, onRemoveUnit, onClear }: { units: InventoryUnit[]; query: string; onRemoveUnit: (din: string) => void; onClear: () => void }) {
  const { results, total } = useMemo(() => searchUnits(units, query), [units, query]);
  return (
    <section aria-labelledby="results-heading">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 mb-2">
        <h2 id="results-heading" className="font-display font-bold text-xl tracking-tight text-foreground" aria-live="polite">
          {total === 0 ? `No unit matches "${query.trim()}"` : total <= SEARCH_LIMIT ? `${total} ${plural(total, "unit matches", "units match")} "${query.trim()}"` : `${total} units match "${query.trim()}"`}
        </h2>
        <button onClick={onClear} className="text-[14px] font-semibold text-primary rounded-sm hover:underline hover:underline-offset-2">Clear search</button>
      </div>
      {total > SEARCH_LIMIT && <p className="mb-2 text-[14px] text-foreground">Showing the first {SEARCH_LIMIT}: best DIN match first, then live units soonest to expire, then expired ones. Refine your search to narrow them.</p>}
      {total === 0 && <p className="text-[15px] text-foreground py-2">Try fewer characters of the DIN, or a shelf or component.</p>}
      {total > 0 && <UnitTable units={results} showType onRemoveUnit={onRemoveUnit} empty="" />}
    </section>
  );
}

// ─── The unit rows, shared by an opened type and by search ──────────────────

function UnitTable({ units, showType, onRemoveUnit, empty }: { units: InventoryUnit[]; showType: boolean; onRemoveUnit: (din: string) => void; empty: string }) {
  const columns = showType ? SEARCH_COLUMNS : UNIT_COLUMNS;
  if (units.length === 0) return empty ? <p className="text-[15px] text-foreground py-2">{empty}</p> : null;
  return (
    <div role="table" aria-label="Units">
      <div role="row" className={`hidden lg:grid ${columns} items-end gap-x-4 pb-1.5 border-b border-gray-300 text-[13px] font-semibold text-gray-600`}>
        <div role="columnheader">DIN</div>
        {showType && <div role="columnheader">Type</div>}
        <div role="columnheader">Component</div>
        <div role="columnheader">Shelf</div>
        <div role="columnheader" className="text-right">Volume</div>
        <div role="columnheader">Expires</div>
        <div role="columnheader">Time left</div>
        <div role="columnheader">Note</div>
        <div role="columnheader"><span className="sr-only">Remove</span></div>
      </div>
      {units.map((u) => <UnitRow key={u.din} u={u} showType={showType} columns={columns} onRemoveUnit={onRemoveUnit} />)}
    </div>
  );
}

const TIME_TONE = { expired: "text-status-critical-text", critical: "text-status-critical-text", "near-expiry": "text-status-watch-text", ok: "text-foreground" } as const;

function timeLeftText(daysLeft: number): string {
  if (daysLeft < 0) return `Expired ${-daysLeft} ${plural(-daysLeft, "day", "days")} ago`;
  if (daysLeft === 0) return "Today";
  return `${daysLeft} ${plural(daysLeft, "day", "days")}`;
}

function UnitRow({ u, showType, columns, onRemoveUnit }: { u: InventoryUnit; showType: boolean; columns: string; onRemoveUnit: (din: string) => void }) {
  const note = unitNote(u);
  const reserved = u.reservedForRequestId !== null;
  const tone = TIME_TONE[getExpiryStatus(u.daysLeft)];
  return (
    <div role="row" className={`flex flex-wrap items-baseline gap-x-4 gap-y-0.5 py-2.5 lg:py-0 lg:min-h-11 lg:grid ${columns} lg:gap-x-4 lg:items-center border-b ${HAIRLINE} last:border-b-0`}>
      <div role="cell" className="order-1 lg:order-none font-mono text-[13px] text-foreground">{u.din}</div>
      {showType && <div role="cell" className="order-3 lg:order-none"><BloodTypeBadge type={u.type} size="sm" /></div>}
      <div role="cell" className="order-4 lg:order-none text-[14px] text-foreground truncate">{u.component}</div>
      <div role="cell" className="order-5 lg:order-none text-[14px] text-foreground truncate"><span className="lg:hidden text-gray-600">Shelf </span>{u.location}</div>
      <div role="cell" className="order-6 lg:order-none text-[14px] text-foreground tabular-nums lg:text-right">{u.volume}<span className="text-[13px]"> mL</span></div>
      <div role="cell" className="order-7 lg:order-none text-[14px] text-foreground tabular-nums"><span className="lg:hidden text-gray-600">Expires </span>{formatCalendarDate(u.expires)}</div>
      <div role="cell" className={`order-2 lg:order-none ml-auto lg:ml-0 text-[14px] font-semibold tabular-nums ${tone}`}>{timeLeftText(u.daysLeft)}</div>
      <div role="cell" className="order-8 basis-full lg:basis-auto lg:order-none text-[13.5px] text-foreground">
        {note && <span title={note.title} className={reserved ? "font-semibold" : ""}>{note.text}</span>}
      </div>
      <div role="cell" className="order-9 lg:order-none ml-auto lg:ml-0 text-right">
        <button
          onClick={() => onRemoveUnit(u.din)}
          disabled={reserved}
          aria-label={reserved ? `Unit ${u.din} is reserved for request ${u.reservedForRequestId} and can't be removed` : `Remove unit ${u.din}`}
          title={reserved ? `Reserved for request ${u.reservedForRequestId}: can't be removed` : "Remove this unit"}
          className="p-1.5 rounded-md text-gray-600 hover:text-status-critical-text hover:bg-secondary transition-colors disabled:opacity-40 disabled:hover:bg-transparent disabled:hover:text-gray-600"
        >
          <Trash2 size={15} aria-hidden="true" />
        </button>
      </div>
    </div>
  );
}
