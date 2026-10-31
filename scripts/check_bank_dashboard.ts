// Asserts what the blood bank Dashboard says (src/app/lib/bankDashboard.ts). Run: node scripts/check_bank_dashboard.ts
import { readFileSync } from "node:fs";
import {
  ORDER_COMPONENT, basisFor, basisSummary, canGiveText, expiryLine, orderLabel, orderPrefill, outlookFor, sortedRows, supplySentence, waitingRequests,
} from "../src/app/lib/bankDashboard.ts";
import { formatDayMonth } from "../src/app/lib/dateFormat.ts";
import { BLOOD_COMPONENTS } from "../src/app/lib/bloodComponents.ts";
import type { ForecastView } from "../src/app/lib/forecastTypes.ts";
import type { InventorySummaryRow } from "../src/app/lib/inventoryTypes.ts";

const eq = (a: unknown, b: unknown, m: string) => { if (JSON.stringify(a) !== JSON.stringify(b)) throw new Error(`${m}: ${JSON.stringify(a)} !== ${JSON.stringify(b)}`); };
const row = (blood_type: string, units: number, minimum_units: number, can_give?: Record<string, number>): InventorySummaryRow =>
  ({ blood_type, units, minimum_units, maximum_units: minimum_units * 2, can_give });

// ── the sentence: worst state's level, and the three facts
const critical = [row("O-", 10, 80), row("A-", 20, 50)]; // under 60% of the minimum
const watch = [row("B+", 50, 60)];
const fine = [row("O+", 100, 100)];
eq(supplySentence({ summary: [...critical, ...watch, ...fine], waiting: 3, expiring: 14, windowDays: 7 }),
  { level: "critical", text: "2 blood types are critically low (A-, O-) and 1 below minimum (B+); 3 requests are waiting for you and 14 units expire within 7 days." }, "critical outranks the rest");
eq(supplySentence({ summary: [...watch, ...fine], waiting: 1, expiring: 1, windowDays: 7 }),
  { level: "watch", text: "1 blood type is below minimum (B+); 1 request is waiting for you and 1 unit expires within 7 days." }, "watch, singulars");
eq(supplySentence({ summary: fine, waiting: 0, expiring: 0, windowDays: 7 }),
  { level: "safe", text: "Every blood type is at or above its minimum; no requests are waiting for you and no units expire within 7 days." }, "all fine");
eq(supplySentence({ summary: fine, waiting: 0, expiring: 2, windowDays: 7 }).level, "watch", "units about to expire are a watch, as before");
eq(supplySentence({ summary: fine, waiting: 9, expiring: 0, windowDays: 7 }).level, "safe", "waiting requests are work, not a shortage: the colour does not change");
eq(supplySentence({ summary: fine, waiting: null, expiring: null, windowDays: 7 }).text, "Every blood type is at or above its minimum; requests could not be loaded and expiring units could not be loaded.", "what could not be loaded is said");
eq(supplySentence({ summary: [row("A+", 1, 80), row("A-", 1, 80), row("B+", 1, 80), row("B-", 1, 80), row("O+", 1, 80), row("O-", 1, 80)], waiting: 0, expiring: 0, windowDays: 7 }).text.split(";")[0],
  "6 blood types are critically low (A-, A+, B- and 3 more)", "a long list is cut");
eq(supplySentence({ summary: [row("O+", 0, 0)], waiting: 0, expiring: 0, windowDays: 7 }).level, "safe", "a type with no minimum is never short");
for (const banned of ["·", "→", "(est."]) if (supplySentence({ summary: critical, waiting: 2, expiring: 2, windowDays: 7 }).text.includes(banned)) throw new Error(`the sentence contains ${banned}`);

// ── the outlook, per row
const forecast = (over: Partial<ForecastView> = {}): ForecastView => ({
  view: "forecast", has_sufficient_history: true, days_of_history: 118, min_days_required: 60, is_dengue_season: false, forecast_source: "sarimax_facility_history",
  sarimax_orders: { "A+": "SARIMAX(1,1,1)x(1,0,1,7)+dengue [grid-searched]", "O+": "SARIMAX(0,1,4)x(1,0,1,7)+dengue [default-order]", "B+": "SARIMAX(0,1,2)x(1,0,1,7)+dengue [ai-recommended]" },
  sarimax_fallback_types: ["A-", "AB-", "B-", "O-", "AB+"], interval_confidence: 0.95, types_total: 8, pending_types: [],
  restock_recommendations: [{ blood_type: "O-", breach_date: "2026-11-04", days_until_breach: 4, recommended_units: 90, projected_low_units: 52 },
                            { blood_type: "A+", breach_date: "2026-10-30", days_until_breach: 0, recommended_units: 7 }],
  restock_status: "action_needed", series: [], alerts: [], ...over,
});
const f = forecast();
const o = outlookFor("O-", f);
eq(o.kind, "order", "an order");
if (o.kind !== "order") throw new Error("unreachable");
eq([o.units, o.byDate, o.projectedLow], [90, "2026-11-04", 52], "the order's numbers, with the projected low");
eq(orderLabel(o, "2026-10-31"), "Order 90 by 4 Nov", "the label, explicit date");
const today = outlookFor("A+", f);
if (today.kind !== "order") throw new Error("unreachable");
eq([orderLabel(today, "2026-10-30"), today.projectedLow], ["Order 7 now", null], "already below the minimum: now; an older API has no projected low");
eq(orderPrefill("O-", o), { bloodType: "O-", quantity: 90, component: "Packed RBC" }, "the link carries type, quantity and component");
if (!(BLOOD_COMPONENTS as readonly string[]).includes(ORDER_COMPONENT)) throw new Error("the order component is not a component");
eq(outlookFor("B+", f).kind, "on_track", "no recommendation and ready: on track");
eq(outlookFor("O-", forecast({ pending_types: ["O-"] })).kind, "calculating", "a pending type is calculating, whatever else");
eq(outlookFor("O-", forecast({ forecast_source: "none", days_of_history: 9, restock_recommendations: [] })), { kind: "collecting", have: 9, need: 60 }, "collecting data");
eq(outlookFor("O-", null).kind, "unavailable", "no forecast");

// ── At risk: the projection holds, the interval's low end does not
const risky = forecast({ downside_risks: [{ blood_type: "B+", risk_date: "2026-11-09", days_until_risk: 10, lower_units: 31 }] });
const ar = outlookFor("B+", risky, 40);
eq(ar.kind, "at_risk", "a downside risk with no recommendation is at risk");
if (ar.kind !== "at_risk") throw new Error("unreachable");
eq([ar.byDate, ar.lowerUnits, ar.minimum], ["2026-11-09", 31, 40], "the risk's numbers");
if (!ar.sentence.startsWith("Projected to hold, but could fall below 40 by 9 Nov.") || !/no order is suggested/.test(ar.sentence)) throw new Error(`the detail: ${ar.sentence}`);
const noMin = outlookFor("B+", risky);
if (noMin.kind !== "at_risk" || !noMin.sentence.includes("below its minimum by")) throw new Error("without a minimum it reads 'its minimum', no number invented");
eq(outlookFor("O-", risky, 40).kind, "order", "a type with a recommendation stays an order, whatever else");
eq(outlookFor("B+", forecast({ downside_risks: [...(risky.downside_risks ?? []), { blood_type: "O-", risk_date: "2026-11-09", days_until_risk: 10, lower_units: 1 }] }), 40).kind, "at_risk", "another type's risk does not leak");
eq(outlookFor("A-", risky, 40).kind, "on_track", "a type with neither stays on track");
eq(outlookFor("B+", forecast({ downside_risks: [], forecast_source: "synthetic_model_stand_in" }), 40).kind, "on_track", "the stand-in has no interval: on track");
{ const noKey = forecast({ forecast_source: "synthetic_model_stand_in" }); delete noKey.downside_risks; eq(outlookFor("B+", noKey, 40).kind, "on_track", "the key absent (stand-in, older API) reads on track, not an error"); }
eq(outlookFor("B+", forecast({ pending_types: ["B+"], downside_risks: risky.downside_risks }), 40).kind, "calculating", "a pending type is calculating, not at risk");
// ── the 30-day gate: too few days, and the interval says nothing about supply
const thinF = forecast({ insufficient_history: [{ blood_type: "B+", observed_days: 6, min_days_required: 30 }] });
eq(outlookFor("B+", thinF, 40), { kind: "thin_history", have: 6, need: 30 }, "a type gated for thin history says so, with the days");
eq(outlookFor("A-", thinF, 40).kind, "on_track", "another type is unaffected");
eq(outlookFor("O-", forecast({ insufficient_history: [{ blood_type: "O-", observed_days: 6, min_days_required: 30 }] }), 40).kind, "order", "the gate never touches a recommendation");
// ...but a recommendation on a short history says so, with the day count, in the row and in its hover text
const limitedF = forecast({ restock_recommendations: [{ blood_type: "O-", breach_date: "2026-11-04", days_until_breach: 4, recommended_units: 90, limited_history: { observed_days: 6, min_days_required: 30 } }] });
const lo = outlookFor("O-", limitedF, 40);
if (lo.kind !== "order") throw new Error("unreachable");
eq(lo.limited, { have: 6, need: 30 }, "a recommendation from a short history carries its day count");
if (!lo.sentence.endsWith("Based on limited history: 6 of 30 recorded days.")) throw new Error("the order hover text: " + lo.sentence);
const fullO = outlookFor("O-", f, 40);
if (fullO.kind !== "order") throw new Error("unreachable");
eq(fullO.limited, null, "a recommendation with 30 days or more has no note");
if (fullO.sentence.includes("limited history")) throw new Error("a full-history recommendation claims limited history");
// below 30 days the outlook is a limited-history recommendation or "Not enough history": never on track, never at risk
const shortF = forecast({
  restock_recommendations: [{ blood_type: "O-", breach_date: "2026-11-04", days_until_breach: 4, recommended_units: 9, limited_history: { observed_days: 6, min_days_required: 30 } }],
  insufficient_history: ["A+", "B+", "A-"].map((blood_type) => ({ blood_type, observed_days: 6, min_days_required: 30 })),
  downside_risks: [{ blood_type: "B+", risk_date: "2026-11-09", days_until_risk: 10, lower_units: 1 }],
});
for (const t of ["A+", "B+", "O-", "A-"]) {
  const k = outlookFor(t, shortF, 40).kind;
  if (k === "on_track" || k === "at_risk") throw new Error(t + " below 30 days read " + k);
}
eq(outlookFor("B+", forecast({ insufficient_history: [{ blood_type: "B+", observed_days: 6, min_days_required: 30 }], pending_types: ["B+"] }), 40).kind, "calculating", "pending still wins");
for (const w of ["better", "optimized", "improved", "best", "safe"]) if (new RegExp(w, "i").test(ar.sentence)) throw new Error(`the at-risk detail claims ${w}`);

// ── the basis in the row
eq(basisFor(f, "A+")?.text, "SARIMAX, grid-searched", "SARIMAX with its order's source");
eq(basisFor(f, "B+")?.text, "SARIMAX, AI-recommended", "AI-recommended is named, never as better");
eq(basisFor(f, "O+")?.text, "SARIMAX, default order", "default order");
eq(basisFor(f, "O-")?.text, "Linear trend", "a type missing from sarimax_orders is on the linear trend");
eq(basisFor(forecast({ forecast_source: "linear_trend" }), "A+")?.text, "Linear trend", "an all-linear forecast");
eq(basisFor(forecast({ pending_types: ["A+"] }), "A+"), null, "calculating has no basis yet: the row says its state");
eq(basisFor(forecast({ forecast_source: "none" }), "A+"), null, "collecting has none either");
eq(basisFor(forecast({ forecast_source: "synthetic_model_stand_in" }), "A+")?.text, "Reference model", "the synthetic stand-in is not passed off as real");
eq(basisFor(null, "A+"), null, "nothing loaded");
for (const w of ["better", "optimized", "improved", "best"]) {
  for (const t of ["A+", "B+", "O+", "O-"]) if (new RegExp(w, "i").test(basisFor(f, t)?.text ?? "") || new RegExp(w, "i").test(basisFor(f, t)?.title ?? "")) throw new Error(`a basis label claims quality: ${w}`);
}
eq(basisSummary(f), "SARIMAX for 3 blood types, linear trend for 5.", "the summary line");
eq(basisSummary(forecast({ pending_types: ["O-", "B-"] })), "SARIMAX for 3 blood types, linear trend for 3.", "the summary counts only what is ready");
eq(basisSummary(forecast({ forecast_source: "linear_trend", pending_types: [] })), "Linear trend for 8 blood types.", "all linear");
eq(basisSummary(forecast({ forecast_source: "none" })), null, "no summary while collecting");

// ── can give
eq(canGiveText(row("O+", 100, 40, { "Packed RBC": 60, Platelets: 0 })), { main: 60, extra: null }, "can give, packed RBC");
eq(canGiveText(row("O+", 100, 40, { "Packed RBC": 0, Platelets: 1 })), { main: 0, extra: "and 1 platelet unit" }, "platelets follow in words");
eq(canGiveText(row("O+", 100, 40, {})), null, "a hospital has none");
eq(canGiveText(row("O+", 100, 40)), null, "an older API says nothing");

// ── row order: most urgent first, orders before none, then the app's blood-type order
const ordered = sortedRows([row("O+", 100, 100), row("A+", 90, 100), row("B+", 10, 100), row("O-", 50, 100), row("A-", 5, 100)], forecast({ restock_recommendations: [
  { blood_type: "B+", breach_date: "x", days_until_breach: 0, recommended_units: 1 }, { blood_type: "A-", breach_date: "x", days_until_breach: 9, recommended_units: 1 }] }));
eq(ordered.map((r) => r.blood_type), ["B+", "A-", "O-", "A+", "O+"], "critical (soonest order first), then below minimum, then adequate in type order");

// ── waiting requests and expiring lines
const req = (id: number, status: string) => ({ id, status } as never);
eq(waitingRequests([req(1, "pending"), req(2, "accepted"), req(3, "pending")])?.map((r) => (r as { id: number }).id), [1, 3], "only pending, in the server's order");
eq(waitingRequests(null), null, "not loaded");
eq(expiryLine({ blood_type: "O+", component: "Packed RBC", expires_date: "2026-11-02", days_left: 3, units: 3 }), "3 units of Packed RBC expire in 3 days (2 Nov)", "a group");
eq(expiryLine({ blood_type: "O+", component: "Platelets", expires_date: "2026-10-31", days_left: 0, units: 1 }), "1 unit of Platelets expires today", "one unit, today");
eq(expiryLine({ blood_type: "A+", component: "Packed RBC", expires_date: "2026-11-01", days_left: 1, units: 2 }), "2 units of Packed RBC expire tomorrow (1 Nov)", "tomorrow");
eq(formatDayMonth("2026-11-04"), "4 Nov", "day and month, explicit");

// ── the view does not use the browser's locale or the old chrome
const view = readFileSync(new URL("../src/app/screens/BankDashboard.tsx", import.meta.url), "utf8").replace(/\/\/.*$/gm, "");
if (/toLocale/.test(view)) throw new Error("BankDashboard must not rely on the browser's locale");
if (/uppercase|tracking-wide|tracking-wider/.test(view)) throw new Error("BankDashboard has tracked capitals");
if (/\(est\.\)/.test(view)) throw new Error("BankDashboard shows an estimate where a count belongs");

// the API's per-type fields are the ones the view reads
const server = readFileSync(new URL("../server/main.py", import.meta.url), "utf8");
for (const key of ["can_give", "projected_low_units", '"window_days"', '"total_units"', '"groups"']) if (!server.includes(key)) throw new Error(`the server does not return ${key}`);

console.log("ok");
