// What the blood bank Inventory says about its units, as plain functions (the view only draws it): why a unit is or is not
// available, the order its units are listed in, the filters, the search across every type, paging, and the expired backlog.
// Kept apart from the view so scripts/check_bank_inventory.ts can assert it.

import type { InventorySummaryRow, InventoryUnit } from "./inventoryTypes.ts";
import { BLOOD_TYPE_ORDER, EXPIRY_NEAR_DAYS, isNotStocked, stockStatus, type StatusLevel } from "./statusTokens.ts";

// ─── Why a unit is not offered ──────────────────────────────────────────────

// Release does not offer a unit with too little time left before it expires (see _release_expiry_cutoff in server/main.py):
// at least RELEASE_FLOOR_DAYS, and per component at least its own margin. These mirror MIN_DAYS_TO_EXPIRY_AT_RELEASE and
// RELEASE_EXPIRY_FLOOR_DAYS there, and scripts/check_bank_inventory.ts fails if the two ever differ. They are clinical
// configuration the blood banks' staff have not yet confirmed.
export const RELEASE_FLOOR_DAYS = 1;
export const RELEASE_MARGIN_DAYS: Record<string, number> = { "Packed RBC": 3, "Platelets": 1 };

// The fewest days a unit of this component must have left to be offered.
export const releaseCutoffDays = (component: string): number => Math.max(RELEASE_FLOOR_DAYS, RELEASE_MARGIN_DAYS[component] ?? 0);

export type UnitState = "expired" | "reserved" | "inside_margin" | "available";

// Expired comes first (nothing else matters for an expired unit), then reserved (it is already promised, whatever its dates),
// then inside the margin (a live unit that release will not offer), else available.
export function unitState(u: Pick<InventoryUnit, "daysLeft" | "component" | "reservedForRequestId">): UnitState {
  if (u.daysLeft < 0) return "expired";
  if (u.reservedForRequestId !== null) return "reserved";
  if (u.daysLeft < releaseCutoffDays(u.component)) return "inside_margin";
  return "available";
}

// The unit's own explanation, in the Note column: only for a unit that is not simply available.
export function unitNote(u: Pick<InventoryUnit, "daysLeft" | "component" | "reservedForRequestId">): { text: string; title?: string } | null {
  const state = unitState(u);
  if (state === "reserved") return { text: `Reserved for request ${u.reservedForRequestId}` };
  if (state === "inside_margin") {
    const n = releaseCutoffDays(u.component);
    return { text: "Not released: inside the margin", title: `Release does not offer ${u.component} with fewer than ${n} ${n === 1 ? "day" : "days"} left.` };
  }
  return null;
}

export const STATE_LABELS: Record<UnitState, string> = {
  available: "Available",
  reserved: "Reserved",
  inside_margin: "Not released: inside the margin",
  expired: "Expired",
};

// ─── The units of one type ──────────────────────────────────────────────────

// Live units soonest-expiry first, the order release picks them in; then the expired ones, which release never picks, last
// (most recently expired first). Ties by DIN so the order is stable.
export function releaseOrder(a: InventoryUnit, b: InventoryUnit): number {
  const aExpired = a.daysLeft < 0, bExpired = b.daysLeft < 0;
  if (aExpired !== bExpired) return aExpired ? 1 : -1;
  return (aExpired ? b.daysLeft - a.daysLeft : a.daysLeft - b.daysLeft) || a.din.localeCompare(b.din);
}

export const sortUnits = (units: InventoryUnit[]): InventoryUnit[] => [...units].sort(releaseOrder);

export type WindowFilter = "any" | "expired" | "3" | "7" | "14" | "30";
export const WINDOW_OPTIONS: { value: WindowFilter; label: string }[] = [
  { value: "any", label: "Any time" },
  { value: "expired", label: "Expired" },
  { value: "3", label: "Within 3 days" },
  { value: "7", label: "Within 7 days" },
  { value: "14", label: "Within 14 days" },
  { value: "30", label: "Within 30 days" },
];

export type UnitFilters = { component: string; window: WindowFilter; state: "any" | UnitState };
export const NO_UNIT_FILTERS: UnitFilters = { component: "", window: "any", state: "any" };
export const hasUnitFilters = (f: UnitFilters): boolean => f.component !== "" || f.window !== "any" || f.state !== "any";

export function filterUnits(units: InventoryUnit[], f: UnitFilters): InventoryUnit[] {
  return units.filter((u) => {
    if (f.component && u.component !== f.component) return false;
    if (f.window === "expired" && u.daysLeft >= 0) return false;
    if (f.window !== "any" && f.window !== "expired" && !(u.daysLeft >= 0 && u.daysLeft <= Number(f.window))) return false;
    if (f.state !== "any" && unitState(u) !== f.state) return false;
    return true;
  });
}

// ─── Paging ─────────────────────────────────────────────────────────────────

export const PAGE_SIZE = 50;

export function pageOf<T>(items: T[], page: number, size = PAGE_SIZE): { items: T[]; page: number; pages: number; from: number; to: number; total: number } {
  const pages = Math.max(1, Math.ceil(items.length / size));
  const p = Math.min(Math.max(1, page), pages);
  const start = (p - 1) * size;
  const slice = items.slice(start, start + size);
  return { items: slice, page: p, pages, from: items.length === 0 ? 0 : start + 1, to: start + slice.length, total: items.length };
}

// ─── Search across every type ───────────────────────────────────────────────

export const SEARCH_LIMIT = 50;

// A DIN as a person or a scanner might type it: case and spaces do not matter ("W0412 26 100200" and "w041226100200" are one DIN).
const squash = (s: string): string => s.replace(/\s+/g, "").toLowerCase();

// DIN prefix matches first, then DIN substring, then shelf or component; within each, in release order (live units soonest expiry
// first, expired ones last), the same order an opened type lists its units in. `total` is every
// match, `results` at most `limit` of them: the screen says "refine your search" when there are more.
export function searchUnits(units: InventoryUnit[], query: string, limit = SEARCH_LIMIT): { results: InventoryUnit[]; total: number } {
  const needle = squash(query);
  if (needle === "") return { results: [], total: 0 };
  const loose = query.trim().toLowerCase();
  const ranked: { u: InventoryUnit; rank: number }[] = [];
  for (const u of units) {
    const din = squash(u.din);
    const rank = din.startsWith(needle) ? 0 : din.includes(needle) ? 1
      : u.location.toLowerCase().includes(loose) || u.component.toLowerCase().includes(loose) ? 2 : -1;
    if (rank >= 0) ranked.push({ u, rank });
  }
  ranked.sort((a, b) => a.rank - b.rank || releaseOrder(a.u, b.u));
  return { results: ranked.slice(0, limit).map((r) => r.u), total: ranked.length };
}

// ─── The type table ─────────────────────────────────────────────────────────

export type TypeSummary = {
  type: string;
  units: number; // usable units on hand (the server's count, which stock status is measured on)
  minimum: number | null;
  maximum: number | null;
  level: StatusLevel | null; // null: no thresholds set
  notStocked: boolean;
  expiring: number; // live units that expire within EXPIRY_NEAR_DAYS (statusTokens), not counting units reserved for a request (they are leaving)
  expired: number; // units past their expiry still on the list
  held: number; // everything on the list for this type
};

// One row per blood type, in the app's order, including a type with no units at all. Exact counts throughout.
export function typeSummaries(units: InventoryUnit[], summary: Record<string, InventorySummaryRow>): TypeSummary[] {
  return BLOOD_TYPE_ORDER.map((type) => {
    const mine = units.filter((u) => u.type === type);
    const s = summary[type];
    const live = mine.filter((u) => u.daysLeft >= 0);
    return {
      type,
      units: s ? s.units : live.length,
      minimum: s ? s.minimum_units : null,
      maximum: s ? s.maximum_units : null,
      level: s ? stockStatus(s.units, s.minimum_units) : null,
      notStocked: s ? isNotStocked(s.units, s.minimum_units) : false,
      expiring: live.filter((u) => u.daysLeft <= EXPIRY_NEAR_DAYS && u.reservedForRequestId === null).length,
      expired: mine.length - live.length,
      held: mine.length,
    };
  });
}

// ─── The expired backlog ────────────────────────────────────────────────────

// The headline. Clearing archives the expired units, except those still reserved for a request, which belong to it until receipt
// settles them (see archive_expired_inventory in server/main.py), so the count the button names is the count it will clear.
export function backlog(units: InventoryUnit[]): { expired: number; clearable: number; held: number; usable: number; total: number } {
  const expired = units.filter((u) => u.daysLeft < 0);
  const held = expired.filter((u) => u.reservedForRequestId !== null).length;
  return { expired: expired.length, clearable: expired.length - held, held, usable: units.length - expired.length, total: units.length };
}
