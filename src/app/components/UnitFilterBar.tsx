import { NO_UNIT_FILTERS, WINDOW_OPTIONS, hasUnitFilters, type StateFilterOption, type UnitFilters, type WindowFilter } from "../lib/bankInventory";

// The per-unit filter above the units of one blood type: Component, Expires, Status, and "Clear filters" once any is set. One
// component for both Inventory screens (a blood bank's and a hospital's) so they cannot drift apart. What differs is data, passed in:
// the components that exist in this type's units, and the Status options (a bank's include the release states, a hospital's do not:
// see BANK_STATE_FILTER_OPTIONS and HOSPITAL_STATE_FILTER_OPTIONS in lib/bankInventory.ts). The colours are the neutral field tokens,
// so the bank's maroon and the hospital's navy only show on focus, as for every field.

const FIELD = "h-9 px-2.5 text-[14px] text-foreground border border-border rounded-md bg-card";

export function UnitFilterBar({ filters, onChange, components, stateOptions }: {
  filters: UnitFilters;
  onChange: (next: UnitFilters) => void;
  components: string[];
  stateOptions: StateFilterOption[];
}) {
  const set = (patch: Partial<UnitFilters>) => onChange({ ...filters, ...patch });
  return (
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
        <select value={filters.state} onChange={(e) => set({ state: e.target.value as UnitFilters["state"] })} className={`${FIELD} w-[15rem] max-w-full`}>
          {stateOptions.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
      </label>
      {hasUnitFilters(filters) && (
        <button onClick={() => onChange(NO_UNIT_FILTERS)} className="h-9 text-[14px] text-foreground underline underline-offset-2 hover:text-role-accent-text">Clear filters</button>
      )}
    </div>
  );
}
