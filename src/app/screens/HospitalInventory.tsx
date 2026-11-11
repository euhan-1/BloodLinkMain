import { useMemo, useState } from "react";
import { ChevronDown, Search, SlidersHorizontal, Trash2 } from "lucide-react";
import { BloodTypeBadge, StatusDot } from "../components/BloodTypeBadge";
import { Skeleton } from "../components/Skeleton";
import { SearchResultsFrame, UnitSearchBox } from "../components/UnitSearch";
import { UnitFilterBar } from "../components/UnitFilterBar";
import { BLOOD_TYPE_ORDER, STATUS_STYLES, STOCK_LABELS, isNotStocked, stockStatus } from "../lib/statusTokens";
import { type InventorySummaryRow, type InventoryUnit } from "../lib/inventoryTypes";
import { HOSPITAL_STATE_FILTER_OPTIONS, NO_UNIT_FILTERS, filterUnits, hasUnitFilters, searchUnits, type UnitFilters } from "../lib/bankInventory";

// The hospital's Inventory: the same page as a blood bank's (BankInventory.tsx): a search across every unit at the top, then a
// table of the eight types, each of which opens to its units. The columns are the hospital's own: units held, Status (the column
// this screen exists for, in the dashboard's three states and colours), Minimum, Maximum and the Thresholds action at the right edge.
//
// Flat on the page background, rows separated by hairlines, no card. Colour is status only: teal / gold / red-orange.
// Below `lg` a row becomes two lines, not a horizontal scroll: type, units and status first; minimum, maximum and the
// Thresholds action on the second. The widths, the gaps and the `lg` breakpoint are the bank's, so the two pages line up.

const plural = (n: number, one: string, many: string) => (n === 1 ? one : many);
const HAIRLINE = "border-gray-200";
// Rows are `lg:grid` so columns line up; every cell is `lg:order-none` so the phone's reading order does not reach the grid.
const TYPE_COLUMNS = "lg:grid-cols-[4.75rem_5rem_minmax(9.5rem,1fr)_5.5rem_5.5rem_9rem]";
const UNIT_COLUMNS = "lg:grid-cols-[10.5rem_7rem_6.5rem_5rem_7.5rem_7.5rem_minmax(8rem,1fr)_2.5rem]";
const SEARCH_COLUMNS = "lg:grid-cols-[10.5rem_3.5rem_7rem_6.5rem_5rem_7.5rem_7.5rem_minmax(8rem,1fr)_2.5rem]";
// A type holding more units than this gets a search box when opened; fewer than this is short enough to scan.
const SEARCH_MIN_UNITS = 8;

export type StockByTypeTableProps = {
  summary: Record<string, InventorySummaryRow>;
  units: InventoryUnit[];
  loading: boolean;
  error: string | null;
  isOpen: (type: string) => boolean;
  onToggle: (type: string) => void;
  onEditThresholds: (type: string) => void;
  onRemoveUnit: (din: string) => void;
};

export function HospitalInventoryView(p: StockByTypeTableProps) {
  const [query, setQuery] = useState("");
  const searching = query.trim() !== "";

  return (
    <div className="space-y-6">
      <UnitSearchBox query={query} onChange={setQuery} placeholder="Find a unit by DIN, location or component" />

      {p.loading && (
        <div className="space-y-px" aria-busy="true" aria-label="Loading inventory">
          {BLOOD_TYPE_ORDER.map((t) => <Skeleton key={t} className="h-11 w-full" />)}
        </div>
      )}
      {!p.loading && p.error && <p role="alert" className="text-[15px] text-status-critical-text">Failed to load inventory: {p.error}</p>}

      {!p.loading && !p.error && searching && <SearchResults units={p.units} query={query} onRemoveUnit={p.onRemoveUnit} onClear={() => setQuery("")} />}
      {!p.loading && !p.error && !searching && <TypeTable {...p} />}
    </div>
  );
}

function TypeTable(p: StockByTypeTableProps) {
  return (
    <div role="table" aria-label="Stock by blood type">
      <div role="row" className={`hidden lg:grid ${TYPE_COLUMNS} items-end gap-x-4 pb-2 border-b border-gray-300 text-[13px] font-semibold text-gray-600`}>
        <div role="columnheader">Type</div>
        <div role="columnheader" className="text-right">Units held</div>
        <div role="columnheader">Status</div>
        <div role="columnheader" className="text-right">Minimum</div>
        <div role="columnheader" className="text-right">Maximum</div>
        <div role="columnheader"><span className="sr-only">Actions</span></div>
      </div>
      <div role="rowgroup">
        {BLOOD_TYPE_ORDER.map((type) => (
          <TypeRow key={type} {...p} type={type} s={p.summary[type]} units={p.units.filter((u) => u.type === type)} />
        ))}
      </div>
    </div>
  );
}

function StatusCell({ s }: { s: InventorySummaryRow | undefined }) {
  if (!s) return <span className="text-[14px] font-semibold text-foreground">Not set</span>;
  if (isNotStocked(s.units, s.minimum_units)) return <span className="text-[14px] font-semibold text-foreground">Not stocked</span>;
  const level = stockStatus(s.units, s.minimum_units);
  return (
    <span className="flex items-center gap-2 text-[14px] font-semibold">
      <StatusDot status={level} />
      <span className={level === "safe" ? "text-foreground" : STATUS_STYLES[level].text}>{STOCK_LABELS[level]}</span>
    </span>
  );
}

function TypeRow({
  type, s, units, isOpen, onToggle, onEditThresholds, onRemoveUnit,
}: { type: string; s: InventorySummaryRow | undefined; units: InventoryUnit[] } & StockByTypeTableProps) {
  const open = isOpen(type);
  const panelId = `units-${type.replace("+", "pos").replace("-", "neg")}`;
  const held = s ? s.units : units.length;
  return (
    <div role="rowgroup" className={`border-b ${HAIRLINE}`}>
      {/* The whole row toggles on click for the mouse; the chevron button is the keyboard and screen-reader control. */}
      <div
        role="row"
        onClick={() => onToggle(type)}
        className={`flex flex-wrap items-center gap-x-4 gap-y-1.5 py-3 lg:py-0 lg:min-h-12 lg:grid ${TYPE_COLUMNS} lg:gap-x-4 cursor-pointer hover:bg-secondary/60 transition-colors`}
      >
        <div role="cell" className="order-1 lg:order-none flex items-center gap-2">
          <button
            aria-expanded={open}
            aria-controls={panelId}
            aria-label={`${open ? "Hide" : "Show"} individual units of ${type}`}
            className="p-1 -ml-1 rounded-md text-gray-600 hover:text-foreground"
          >
            <ChevronDown size={16} className={`transition-transform ${open ? "" : "-rotate-90"}`} />
          </button>
          <BloodTypeBadge type={type} size="sm" />
        </div>
        <div role="cell" className="order-2 lg:order-none lg:text-right text-foreground">
          <span className="text-[18px] lg:text-[16px] font-bold tabular-nums">{held}</span>
          <span className="lg:hidden text-[14px]"> {plural(held, "unit", "units")}</span>
        </div>
        <div role="cell" className="order-3 lg:order-none ml-auto lg:ml-0"><StatusCell s={s} /></div>
        <div aria-hidden="true" className="order-4 basis-full h-0 lg:hidden" />
        <div role="cell" className="order-5 lg:order-none lg:text-right text-[14px] text-foreground tabular-nums">
          <span className="lg:hidden text-gray-600">Minimum </span>{s ? s.minimum_units : "—"}
        </div>
        <div role="cell" className="order-6 lg:order-none lg:text-right text-[14px] text-foreground tabular-nums">
          <span className="lg:hidden text-gray-600">Maximum </span>{s ? s.maximum_units : "—"}
        </div>
        <div role="cell" className="order-7 lg:order-none ml-auto lg:ml-0 lg:text-right">
          <button
            onClick={(e) => { e.stopPropagation(); onEditThresholds(type); }}
            aria-label={`Set minimum and maximum stock thresholds for ${type}`}
            title="Set minimum and maximum stock thresholds"
            className="inline-flex items-center gap-1.5 h-8 px-2.5 -mr-2.5 rounded-md text-[13px] font-semibold text-foreground hover:bg-secondary transition-colors"
          >
            <SlidersHorizontal size={14} aria-hidden="true" /> Thresholds
            <span className="sr-only"> for {type}</span>
          </button>
        </div>
      </div>
      {open && (
        <div id={panelId} className="pb-5 lg:pl-9">
          <UnitList type={type} units={units} onRemoveUnit={onRemoveUnit} />
        </div>
      )}
    </div>
  );
}

// ─── One type's units ───────────────────────────────────────────────────────

function UnitList({ type, units, onRemoveUnit }: { type: string; units: InventoryUnit[]; onRemoveUnit: (din: string) => void }) {
  const [query, setQuery] = useState("");
  const [filters, setFilters] = useState<UnitFilters>(NO_UNIT_FILTERS);
  const components = useMemo(() => Array.from(new Set(units.map((u) => u.component))).sort(), [units]);
  if (units.length === 0) return <p className="text-[15px] text-foreground py-2">No {type} units on file.</p>;

  // Soonest-to-expire first, the same first-expired-first-out order the backend uses for real transfers.
  const sorted = [...units].sort((a, b) => a.daysLeft - b.daysLeft);
  const q = query.trim().toLowerCase();
  const searched = q === "" ? sorted : sorted.filter((u) => [u.din, u.location, u.component].some((v) => v.toLowerCase().includes(q)));
  const shown = filterUnits(searched, filters);
  const expired = units.filter((u) => u.daysLeft < 0).length;

  return (
    <div>
      {(units.length > SEARCH_MIN_UNITS || expired > 0) && (
        <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-2 pb-3">
          {units.length > SEARCH_MIN_UNITS ? (
            <div className="relative w-full sm:w-80">
              <Search size={14} aria-hidden="true" className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-600" />
              <input
                type="search"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                aria-label={`Search ${type} units by DIN, location or component`}
                placeholder="Search DIN, location or component"
                className="w-full h-9 pl-9 pr-3 text-[14px] border border-border rounded-md bg-card focus:border-primary"
              />
            </div>
          ) : <span />}
          {expired > 0 && (
            <p className="text-[14px] text-foreground">{expired} expired {plural(expired, "unit is", "units are")} not counted in units held.</p>
          )}
        </div>
      )}
      <UnitFilterBar filters={filters} onChange={setFilters} components={components} stateOptions={HOSPITAL_STATE_FILTER_OPTIONS} />
      {shown.length === 0
        ? <p className="text-[15px] text-foreground py-3">
            {q !== "" ? `No ${type} units match "${query.trim()}"${hasUnitFilters(filters) ? " with these filters" : ""}.` : `No ${type} units match these filters.`}
          </p>
        : <UnitTable units={shown} showType={false} onRemoveUnit={onRemoveUnit} />}
    </div>
  );
}

// ─── Search results ─────────────────────────────────────────────────────────

function SearchResults({ units, query, onRemoveUnit, onClear }: { units: InventoryUnit[]; query: string; onRemoveUnit: (din: string) => void; onClear: () => void }) {
  const { results, total } = useMemo(() => searchUnits(units, query), [units, query]);
  return (
    <SearchResultsFrame total={total} query={query} onClear={onClear}>
      {total > 0 && <UnitTable units={results} showType onRemoveUnit={onRemoveUnit} />}
    </SearchResultsFrame>
  );
}

// ─── The unit rows, shared by an opened type and by search ──────────────────

function UnitTable({ units, showType, onRemoveUnit }: { units: InventoryUnit[]; showType: boolean; onRemoveUnit: (din: string) => void }) {
  const columns = showType ? SEARCH_COLUMNS : UNIT_COLUMNS;
  return (
    <div role="table" aria-label={showType ? "Units" : `${units[0]?.type ?? ""} units`}>
      <div role="row" className={`hidden lg:grid ${columns} items-end gap-x-4 pb-1.5 border-b border-gray-300 text-[13px] font-semibold text-gray-600`}>
        <div role="columnheader">DIN</div>
        {showType && <div role="columnheader">Type</div>}
        <div role="columnheader">Component</div>
        <div role="columnheader">Location</div>
        <div role="columnheader" className="text-right">Volume</div>
        <div role="columnheader">Collected</div>
        <div role="columnheader">Expires</div>
        <div role="columnheader">Time left</div>
        <div role="columnheader"><span className="sr-only">Remove</span></div>
      </div>
      {units.map((u) => {
        const urgent = u.daysLeft <= 3; // expired, or the dashboard's critical expiry window
        return (
          <div key={u.din} role="row" className={`flex flex-wrap items-baseline gap-x-4 gap-y-0.5 py-2.5 lg:py-0 lg:min-h-11 lg:grid ${columns} lg:gap-x-4 lg:items-center border-b ${HAIRLINE} last:border-b-0`}>
            <div role="cell" className="font-mono text-[13px] text-foreground order-1 lg:order-none">{u.din}</div>
            {showType && <div role="cell" className="order-3 lg:order-none"><BloodTypeBadge type={u.type} size="sm" /></div>}
            <div role="cell" className="text-[14px] text-foreground order-3 lg:order-none truncate">{u.component}</div>
            <div role="cell" className="text-[14px] text-foreground order-4 lg:order-none truncate">
              <span className="lg:hidden text-gray-600">Location </span>{u.location}
            </div>
            <div role="cell" className="text-[14px] text-foreground tabular-nums order-5 lg:order-none lg:text-right">
              {u.volume}<span className="text-[13px]"> mL</span>
            </div>
            <div role="cell" className="text-[14px] text-foreground tabular-nums order-6 lg:order-none">
              <span className="lg:hidden text-gray-600">Collected </span>{u.collected}
            </div>
            <div role="cell" className="text-[14px] text-foreground tabular-nums order-7 lg:order-none">
              <span className="lg:hidden text-gray-600">Expires </span>{u.expires}
            </div>
            <div role="cell" className={`text-[14px] font-semibold order-2 lg:order-none ml-auto lg:ml-0 ${urgent ? "text-status-critical-text" : "text-foreground"}`}>
              {u.daysLeft < 0 ? "Expired" : u.daysLeft === 0 ? "Today" : `${u.daysLeft} ${plural(u.daysLeft, "day", "days")}`}
            </div>
            <div role="cell" className="order-2 lg:order-none text-right">
              <button
                onClick={() => onRemoveUnit(u.din)}
                aria-label={`Remove unit ${u.din}`}
                title="Remove this unit"
                className="p-1.5 rounded-md text-gray-600 hover:text-status-critical-text hover:bg-secondary transition-colors"
              >
                <Trash2 size={15} aria-hidden="true" />
              </button>
            </div>
            {/* Phone: ends the first line (DIN, time left, remove) so the unit's details start on the next. */}
            <div aria-hidden="true" className="basis-full h-0 order-2 lg:hidden" />
          </div>
        );
      })}
    </div>
  );
}
