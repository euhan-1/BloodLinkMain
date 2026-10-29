import { useState } from "react";
import { ChevronDown, SlidersHorizontal, Search, Trash2 } from "lucide-react";
import { BloodTypeBadge } from "../components/BloodTypeBadge";
import { Skeleton } from "../components/Skeleton";
import { BLOOD_TYPE_ORDER, STATUS_STYLES, STOCK_LABELS, isNotStocked, stockStatus } from "../lib/statusTokens";
import { type InventorySummaryRow, type InventoryUnit } from "../lib/inventoryTypes";

// The hospital's Inventory: tabular data, so a table. One dense row per blood type with the column this screen exists
// for, Status, in the dashboard's three states and colours. A row opens to that type's individual units.
//
// Flat on the page background, rows separated by hairlines, no card. Colour is status only: teal / gold / red-orange.
// Below `md` a row becomes two lines, not a horizontal scroll: type, units and status first; minimum, maximum and the
// Thresholds action on the second.

const plural = (n: number, one: string, many: string) => (n === 1 ? one : many);
const HAIRLINE = "border-gray-200";
// Rows are `md:grid` so columns line up; the second-line wrapper is `md:contents` so its cells join that grid.
const TYPE_COLUMNS = "md:grid-cols-[9rem_7rem_9rem_7rem_7rem_1fr]";
const UNIT_COLUMNS = "md:grid-cols-[10.5rem_1fr_8rem_4.5rem_6.5rem_6.5rem_6.5rem_2.5rem]";
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

export function StockByTypeTable(p: StockByTypeTableProps) {
  if (p.loading) {
    return (
      <div className="space-y-px" aria-busy="true" aria-label="Loading inventory">
        {BLOOD_TYPE_ORDER.map((t) => <Skeleton key={t} className="h-11 w-full" />)}
      </div>
    );
  }
  if (p.error) {
    return <p role="alert" className="text-[15px] text-status-critical-text">Failed to load inventory: {p.error}</p>;
  }

  return (
    <div role="table" aria-label="Stock by blood type">
      <div role="row" className={`hidden md:grid ${TYPE_COLUMNS} items-end pb-2 border-b border-gray-300 text-[13px] font-semibold text-gray-600`}>
        <div role="columnheader" className="pl-[30px]">Type</div>
        <div role="columnheader" className="text-right pr-6">Units held</div>
        <div role="columnheader">Status</div>
        <div role="columnheader" className="text-right pr-6">Minimum</div>
        <div role="columnheader" className="text-right pr-6">Maximum</div>
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
  if (!s) return <span className="text-[14px] text-foreground">Not set</span>;
  if (isNotStocked(s.units, s.minimum_units)) return <span className="text-[14px] font-semibold text-foreground">Not stocked</span>;
  const level = stockStatus(s.units, s.minimum_units);
  return (
    <span className="inline-flex items-center gap-2">
      <span aria-hidden="true" className={`w-2.5 h-2.5 rounded-full shrink-0 ${STATUS_STYLES[level].dot}`} />
      <span className={`text-[14px] font-semibold ${level === "safe" ? "text-foreground" : STATUS_STYLES[level].text}`}>{STOCK_LABELS[level]}</span>
    </span>
  );
}

function TypeRow({
  type, s, units, isOpen, onToggle, onEditThresholds, onRemoveUnit,
}: { type: string; s: InventorySummaryRow | undefined; units: InventoryUnit[] } & StockByTypeTableProps) {
  const open = isOpen(type);
  const panelId = `units-${type.replace("+", "pos").replace("-", "neg")}`;
  return (
    <div role="rowgroup" className={`border-b ${HAIRLINE}`}>
      {/* The whole row toggles on click for the mouse; the chevron button is the keyboard and screen-reader control. */}
      <div
        role="row"
        onClick={() => onToggle(type)}
        className={`flex flex-wrap items-center gap-x-4 gap-y-1 py-2.5 md:py-0 md:min-h-11 md:grid md:gap-x-0 ${TYPE_COLUMNS} cursor-pointer hover:bg-secondary/60 transition-colors`}
      >
        <div role="cell" className="flex items-center gap-2.5">
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
        <div role="cell" className="md:text-right md:pr-6">
          <span className="text-[18px] md:text-[15px] font-bold tabular-nums text-foreground">{s ? s.units : units.length}</span>
          <span className="md:hidden text-[14px] text-foreground"> {plural(s ? s.units : units.length, "unit", "units")}</span>
        </div>
        <div role="cell" className="ml-auto md:ml-0"><StatusCell s={s} /></div>
        <div className="basis-full flex items-center gap-5 md:contents">
          <div role="cell" className="text-[14px] text-foreground tabular-nums md:text-right md:pr-6">
            <span className="md:hidden">Minimum </span>
            <span className="font-semibold md:font-bold md:text-[15px]">{s ? s.minimum_units : "—"}</span>
          </div>
          <div role="cell" className="text-[14px] text-foreground tabular-nums md:text-right md:pr-6">
            <span className="md:hidden">Maximum </span>
            <span className="font-semibold md:font-bold md:text-[15px]">{s ? s.maximum_units : "—"}</span>
          </div>
          <div role="cell" className="ml-auto md:ml-0">
            <button
              onClick={(e) => { e.stopPropagation(); onEditThresholds(type); }}
              title="Set minimum and maximum stock thresholds"
              className="flex items-center gap-1.5 h-8 px-2.5 -mr-2.5 rounded-md text-[13px] font-semibold text-foreground hover:bg-secondary transition-colors"
            >
              <SlidersHorizontal size={14} /> Thresholds
              <span className="sr-only"> for {type}</span>
            </button>
          </div>
        </div>
      </div>
      {open && (
        <div id={panelId} className="pb-5 md:pl-9">
          <UnitList type={type} units={units} onRemoveUnit={onRemoveUnit} />
        </div>
      )}
    </div>
  );
}

function UnitList({ type, units, onRemoveUnit }: { type: string; units: InventoryUnit[]; onRemoveUnit: (din: string) => void }) {
  const [query, setQuery] = useState("");
  if (units.length === 0) return <p className="text-[15px] text-foreground py-2">No {type} units on file.</p>;

  // Soonest-to-expire first, the same first-expired-first-out order the backend uses for real transfers.
  const sorted = [...units].sort((a, b) => a.daysLeft - b.daysLeft);
  const q = query.trim().toLowerCase();
  const shown = q === "" ? sorted : sorted.filter((u) => [u.din, u.location, u.component].some((v) => v.toLowerCase().includes(q)));
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

      <div role="table" aria-label={`${type} units`}>
        <div role="row" className={`hidden md:grid ${UNIT_COLUMNS} items-end pb-1.5 border-b border-gray-300 text-[13px] font-semibold text-gray-600`}>
          <div role="columnheader">DIN</div>
          <div role="columnheader">Component</div>
          <div role="columnheader">Location</div>
          <div role="columnheader" className="text-right pr-4">Volume</div>
          <div role="columnheader">Collected</div>
          <div role="columnheader">Expires</div>
          <div role="columnheader">Time left</div>
          <div role="columnheader"><span className="sr-only">Remove</span></div>
        </div>
        {shown.length === 0 && <p className="text-[15px] text-foreground py-3">No {type} units match "{query.trim()}".</p>}
        {shown.map((u) => {
          const urgent = u.daysLeft <= 3; // expired, or the dashboard's critical expiry window
          return (
            <div key={u.din} role="row" className={`flex flex-wrap items-baseline gap-x-5 gap-y-0.5 py-2.5 md:py-0 md:min-h-10 md:grid md:gap-x-0 ${UNIT_COLUMNS} md:items-center border-b ${HAIRLINE} last:border-b-0`}>
              <div role="cell" className="font-mono text-[13px] text-foreground order-1 md:order-none">{u.din}</div>
              <div role="cell" className="text-[14px] text-foreground order-3 md:order-none truncate">{u.component}</div>
              <div role="cell" className="text-[14px] text-foreground order-4 md:order-none truncate">
                <span className="md:hidden">Location </span>{u.location}
              </div>
              <div role="cell" className="text-[14px] text-foreground tabular-nums order-5 md:order-none md:text-right md:pr-4">
                {u.volume}<span className="text-[13px]"> mL</span>
              </div>
              <div role="cell" className="text-[14px] text-foreground tabular-nums order-6 md:order-none">
                <span className="md:hidden">Collected </span>{u.collected}
              </div>
              <div role="cell" className="text-[14px] text-foreground tabular-nums order-7 md:order-none">
                <span className="md:hidden">Expires </span>{u.expires}
              </div>
              <div role="cell" className={`text-[14px] font-semibold order-2 md:order-none ml-auto md:ml-0 ${urgent ? "text-status-critical-text" : "text-foreground"}`}>
                {u.daysLeft < 0 ? "Expired" : u.daysLeft === 0 ? "Today" : `${u.daysLeft} ${plural(u.daysLeft, "day", "days")}`}
              </div>
              <div role="cell" className="order-2 md:order-none text-right">
                <button
                  onClick={() => onRemoveUnit(u.din)}
                  aria-label={`Remove unit ${u.din}`}
                  title="Remove this unit"
                  className="p-1.5 rounded-md text-gray-600 hover:text-status-critical-text hover:bg-secondary transition-colors"
                >
                  <Trash2 size={15} />
                </button>
              </div>
              {/* Phone: ends the first line (DIN, time left, remove) so the unit's details start on the next. */}
              <div aria-hidden="true" className="basis-full h-0 order-2 md:hidden" />
            </div>
          );
        })}
      </div>
    </div>
  );
}
