// What the blood bank Dashboard says, as plain functions (the view only draws it): the one sentence at the top, each blood type's
// outlook and the basis it rests on, the order link's prefill, and the expiring-unit lines. Kept apart from the view so
// scripts/check_bank_dashboard.ts can assert it.

import type { ForecastView } from "./forecastTypes.ts";
import { forecastSourceTag } from "./forecastSource.ts";
import { formatDayMonth } from "./dateFormat.ts";
import type { InventorySummaryRow } from "./inventoryTypes.ts";
import { BLOOD_TYPE_ORDER, STATUS_STYLES, isNotStocked, stockStatus, type StatusLevel } from "./statusTokens.ts";
import type { ExpiringGroup } from "./api.ts";
import type { RequestRow } from "./requestTypes.ts";

// A restock order from the outlook is for red cells: the minimums it is measured against were set for them, and Packed RBC is
// all but one unit of what is on the shelves. The prefill carries it, but the Source supply screen still shows it as chosen,
// not hidden.
export const ORDER_COMPONENT = "Packed RBC";

const plural = (n: number, one: string, many: string) => (n === 1 ? one : many);

// ─── The sentence ───────────────────────────────────────────────────────────

const byTypeOrder = (a: string, b: string) => BLOOD_TYPE_ORDER.indexOf(a) - BLOOD_TYPE_ORDER.indexOf(b);

function nameList(types: string[]): string {
  const sorted = [...types].sort(byTypeOrder);
  return sorted.length <= 4 ? sorted.join(", ") : `${sorted.slice(0, 3).join(", ")} and ${sorted.length - 3} more`;
}

// The Dashboard's worst state, in the colour it is written in: critical stock outranks below-minimum stock, which outranks
// units about to expire, which outranks everything fine. Requests waiting never change the colour, they are work, not a shortage.
export function supplySentence(p: {
  summary: InventorySummaryRow[];
  waiting: number | null; // null: could not be loaded
  expiring: number | null; // null: could not be loaded
  windowDays: number;
}): { level: StatusLevel; text: string } {
  const critical = p.summary.filter((r) => stockStatus(r.units, r.minimum_units) === "critical").map((r) => r.blood_type);
  const watch = p.summary.filter((r) => stockStatus(r.units, r.minimum_units) === "watch").map((r) => r.blood_type);
  const stock =
    critical.length > 0 && watch.length > 0
      ? `${critical.length} blood ${plural(critical.length, "type is", "types are")} critically low (${nameList(critical)}) and ${watch.length} below minimum (${nameList(watch)})`
      : critical.length > 0
      ? `${critical.length} blood ${plural(critical.length, "type is", "types are")} critically low (${nameList(critical)})`
      : watch.length > 0
      ? `${watch.length} blood ${plural(watch.length, "type is", "types are")} below minimum (${nameList(watch)})`
      : "Every blood type is at or above its minimum";
  const requests =
    p.waiting === null ? "requests could not be loaded"
    : p.waiting === 0 ? "no requests are waiting for you"
    : `${p.waiting} ${plural(p.waiting, "request is", "requests are")} waiting for you`;
  const expiring =
    p.expiring === null ? "expiring units could not be loaded"
    : p.expiring === 0 ? `no units expire within ${p.windowDays} days`
    : `${p.expiring} ${plural(p.expiring, "unit expires", "units expire")} within ${p.windowDays} days`;
  const level: StatusLevel = critical.length > 0 ? "critical" : watch.length > 0 || (p.expiring ?? 0) > 0 ? "watch" : "safe";
  return { level, text: `${stock}; ${requests} and ${expiring}.` };
}

// ─── The table ──────────────────────────────────────────────────────────────

export type Outlook =
  | { kind: "order"; units: number; byDate: string; projectedLow: number | null; sentence: string; limited: { have: number; need: number } | null }
  | { kind: "on_track" }
  | { kind: "at_risk"; byDate: string; lowerUnits: number; minimum: number | null; sentence: string }
  | { kind: "thin_history"; have: number; need: number }
  | { kind: "calculating" }
  | { kind: "collecting"; have: number; need: number }
  | { kind: "unavailable" };

// What "At risk" means, in plain words. The date is the checkpoint the interval first reaches below the minimum, not a prediction of the day.
export const atRiskSentence = (byDate: string, minimum: number | null): string =>
  `Projected to hold, but could fall below ${minimum === null ? "its minimum" : minimum} by ${formatDayMonth(byDate)}. This is the low end of the 95% range; no order is suggested.`;

// One blood type's outlook, from /forecast. The projection is an estimate; it is only ever a column of its own, never folded
// into a current stock figure.
export function outlookFor(bloodType: string, f: ForecastView | null, minimum: number | null = null): Outlook {
  if (f === null) return { kind: "unavailable" };
  if (f.forecast_source === "none") return { kind: "collecting", have: f.days_of_history, need: f.min_days_required };
  if ((f.pending_types ?? []).includes(bloodType)) return { kind: "calculating" };
  const rec = f.restock_recommendations.find((r) => r.blood_type === bloodType);
  if (!rec) {
    // Below the SARIMAX floor a type is never asserted safe or at risk: there is no basis for either. Otherwise the server
    // only sends a risk for a type whose projection holds (a recommendation always wins over it); a forecast with no
    // interval or no list (an older API) reads as on track.
    const thin = (f.insufficient_history ?? []).find((r) => r.blood_type === bloodType);
    if (thin) return { kind: "thin_history", have: thin.observed_days, need: thin.min_days_required };
    const risk = (f.downside_risks ?? []).find((r) => r.blood_type === bloodType);
    if (!risk) return { kind: "on_track" };
    return { kind: "at_risk", byDate: risk.risk_date, lowerUnits: risk.lower_units, minimum, sentence: atRiskSentence(risk.risk_date, minimum) };
  }
  const when = rec.days_until_breach <= 0 ? "today" : formatDayMonth(rec.breach_date);
  const lh = rec.limited_history;
  return {
    kind: "order",
    limited: lh ? { have: lh.observed_days, need: lh.min_days_required } : null,
    units: rec.recommended_units,
    byDate: rec.breach_date,
    projectedLow: rec.projected_low_units ?? null,
    sentence: `Projected below its minimum ${rec.days_until_breach <= 0 ? "today" : `around ${when}`}; ordering ${rec.recommended_units} ${plural(rec.recommended_units, "unit", "units")} brings it back to its maximum.${lh ? ` ${limitedHistoryNote(lh.observed_days, lh.min_days_required)}` : ""}`,
  };
}

// A recommendation is not withheld on a short history, but it says it rests on one.
export const limitedHistoryNote = (have: number, need: number): string => `Based on limited history: ${have} of ${need} recorded days.`;

// "Order 90 by 4 Nov", or "Order 90 now" when the projection is already below the minimum today.
export function orderLabel(o: Extract<Outlook, { kind: "order" }>, today: string): string {
  return o.byDate <= today ? `Order ${o.units} now` : `Order ${o.units} by ${formatDayMonth(o.byDate)}`;
}

// What the order link carries into Source supply: the type and quantity from the outlook, and the component the outlook is about.
export function orderPrefill(bloodType: string, o: Extract<Outlook, { kind: "order" }>) {
  return { bloodType, quantity: o.units, component: ORDER_COMPONENT };
}

// Which mechanism this row's outlook rests on, in the row. null where the row says its state instead (calculating, collecting).
export function basisFor(f: ForecastView | null, bloodType: string): { text: string; title: string } | null {
  if (f === null || f.forecast_source === "none" || (f.pending_types ?? []).includes(bloodType)) return null;
  if (f.forecast_source === "synthetic_model_stand_in") {
    return { text: "Reference model", title: "A reference model trained on generated data, not this facility's own history." };
  }
  const tag = forecastSourceTag(f, bloodType);
  if (f.forecast_source === "linear_trend" || (tag !== null && tag.text === "linear trend")) {
    return { text: "Linear trend", title: tag?.title ?? "A straight-line projection from this blood type's recorded stock." };
  }
  return { text: tag === null ? "SARIMAX" : `SARIMAX, ${tag.text}`, title: tag?.title ?? "A SARIMAX model fitted to this blood type's recorded stock." };
}

// "SARIMAX for 4 blood types, linear trend for 4." Counts only the types that have an answer.
export function basisSummary(f: ForecastView | null): string | null {
  if (f === null || f.forecast_source === "none" || f.forecast_source === "synthetic_model_stand_in") return null;
  const total = f.types_total ?? 8;
  const ready = total - (f.pending_types ?? []).length;
  if (f.forecast_source === "linear_trend") return `Linear trend for ${ready} blood ${plural(ready, "type", "types")}.`;
  const sarimax = Object.keys(f.sarimax_orders ?? {}).filter((t) => !(f.pending_types ?? []).includes(t)).length;
  const linear = ready - sarimax;
  return linear > 0
    ? `SARIMAX for ${sarimax} blood ${plural(sarimax, "type", "types")}, linear trend for ${linear}.`
    : `SARIMAX for ${sarimax} blood ${plural(sarimax, "type", "types")}.`;
}

// The Packed RBC figure is the column; platelets, when there are any to give, follow in words. null: the API did not say.
export function canGiveText(row: InventorySummaryRow): { main: number; extra: string | null } | null {
  if (!row.can_give || Object.keys(row.can_give).length === 0) return null;
  const platelets = row.can_give["Platelets"] ?? 0;
  return { main: row.can_give[ORDER_COMPONENT] ?? 0, extra: platelets > 0 ? `and ${platelets} ${plural(platelets, "platelet unit", "platelet units")}` : null };
}

// Most urgent first: critical, then below minimum, then the rest; within each, types with an order to place first, soonest
// first; then the blood-type order the rest of the app uses.
export function sortedRows(summary: InventorySummaryRow[], f: ForecastView | null): InventorySummaryRow[] {
  const severity = { critical: 0, watch: 1, safe: 2 } as const;
  const due = (r: InventorySummaryRow) => f?.restock_recommendations.find((x) => x.blood_type === r.blood_type)?.days_until_breach ?? Infinity;
  return [...summary].sort(
    (a, b) =>
      severity[stockStatus(a.units, a.minimum_units)] - severity[stockStatus(b.units, b.minimum_units)]
      || due(a) - due(b)
      || byTypeOrder(a.blood_type, b.blood_type),
  );
}

export const statusWord = (row: InventorySummaryRow, labels: Record<StatusLevel, string>): string =>
  isNotStocked(row.units, row.minimum_units) ? "Not stocked" : labels[stockStatus(row.units, row.minimum_units)];

export const statusTextClass = (row: InventorySummaryRow): string => STATUS_STYLES[stockStatus(row.units, row.minimum_units)].text;

// ─── Waiting for you, and expiring units ────────────────────────────────────

// The requests this bank has to decide on, in the order the server lists them (immediate use first, then restock, oldest first).
export const waitingRequests = (rows: RequestRow[] | null): RequestRow[] | null => (rows === null ? null : rows.filter((r) => r.status === "pending"));

// "3 units of Packed RBC expire in 3 days (4 Nov)", "1 unit of Platelets expires today".
export function expiryLine(g: ExpiringGroup): string {
  const verb = g.units === 1 ? "expires" : "expire";
  const when = g.days_left === 0 ? "today" : g.days_left === 1 ? `tomorrow (${formatDayMonth(g.expires_date)})` : `in ${g.days_left} days (${formatDayMonth(g.expires_date)})`;
  return `${g.units} ${plural(g.units, "unit", "units")} of ${g.component} ${verb} ${when}`;
}
