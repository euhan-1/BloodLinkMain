// The transfer history (GET /transfers): its types, the filters it sends, and the wording of its rows. Each row carries the
// same `request` block and `summary` as GET /requests/{id}, so the history and the receipt describe a transfer alike.

import type { RequestDetail } from "./requestTimeline.ts";
import { formatDate } from "./dateFormat.ts";

// declined and cancelled are requests that never became transfers: they are listed only when chosen as the outcome.
export type TransferOutcome = "received_in_full" | "partly_received" | "none_received" | "in_progress" | "declined" | "cancelled";

export const isRefused = (o: string): boolean => o === "declined" || o === "cancelled";

export type TransferRow = {
  request: RequestDetail["request"];
  direction: "sent" | "received"; // the flow of blood relative to the viewer: sent when it was the supplier
  other_facility_id: number;
  transfer_at: string; // when it happened: receipt for a finished one, release (or the request) for one in progress
  outcome: TransferOutcome;
  summary: { asked: number; received: number | null; failed: number | null }; // null until receipt
};

export type TransfersResponse = {
  transfers: TransferRow[];
  total: number;
  truncated: boolean;
  other_facilities: { id: number; name: string }[];
  totals: { transfers: number; units_sent: number; units_received: number; units_failed: number; transfers_with_expired_units: number };
};

export type TransferFilters = {
  includeActive: boolean;
  dateFrom: string; // YYYY-MM-DD in Manila business dates, "" = no bound
  dateTo: string;
  otherFacilityId: string; // "" = any
  bloodType: string;
  component: string;
  outcome: string;
};

export const NO_FILTERS: TransferFilters = { includeActive: false, dateFrom: "", dateTo: "", otherFacilityId: "", bloodType: "", component: "", outcome: "" };

export const hasFilters = (f: TransferFilters): boolean => JSON.stringify(f) !== JSON.stringify(NO_FILTERS);

// The query string for GET /transfers: only what is set.
export function transfersQuery(f: TransferFilters): string {
  const p = new URLSearchParams();
  if (f.includeActive) p.set("include_active", "true");
  if (f.dateFrom) p.set("date_from", f.dateFrom);
  if (f.dateTo) p.set("date_to", f.dateTo);
  if (f.otherFacilityId) p.set("other_facility_id", f.otherFacilityId);
  if (f.bloodType) p.set("blood_type", f.bloodType);
  if (f.component) p.set("component", f.component);
  if (f.outcome) p.set("outcome", f.outcome);
  return p.toString();
}

export const OUTCOME_LABELS: Record<TransferOutcome, string> = {
  received_in_full: "Received in full",
  partly_received: "Partly received",
  none_received: "None received",
  in_progress: "In progress",
  declined: "Declined",
  cancelled: "Cancelled",
};

// The outcome filter. "Units expired in transit" is partly received or none received together: the question it answers.
export function outcomeOptions(includeActive: boolean): { value: string; label: string }[] {
  return [
    { value: "", label: "Any outcome" },
    { value: "received_in_full", label: OUTCOME_LABELS.received_in_full },
    { value: "partly_received", label: OUTCOME_LABELS.partly_received },
    { value: "none_received", label: OUTCOME_LABELS.none_received },
    { value: "expired_in_transit", label: "Units expired in transit" },
    ...(includeActive ? [{ value: "in_progress", label: OUTCOME_LABELS.in_progress }] : []),
    { value: "declined", label: OUTCOME_LABELS.declined },
    { value: "cancelled", label: OUTCOME_LABELS.cancelled },
  ];
}

// Who asked, on every row whatever happened to the request: when this facility supplied, the other one requested. The Outcome
// column says what happened, so this never claims blood moved. (The API's `direction` is still the flow, sent or received.)
export const directionText = (d: TransferRow["direction"]): string => (d === "sent" ? "They requested" : "We requested");

// The facility on the other side of the transfer, by name.
export function otherFacilityName(row: TransferRow): string {
  const r = row.request;
  return (row.direction === "sent" ? r.requesting_facility : r.supplying_facility).name;
}

// Business dates are Manila's (the server's rule), whatever the viewer's own time zone.
const MANILA = "Asia/Manila";

// "2026-11-01"
export function manilaToday(now: Date = new Date()): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: MANILA, year: "numeric", month: "2-digit", day: "2-digit" }).format(now);
}

// "30 Oct 2026", in Manila time so a row's date is the date the date filter would match it with. Written by
// lib/dateFormat.ts, the same writer the receipt uses, so the two cannot disagree about how a date reads.
export function transferDateText(at: string): string {
  return formatDate(at, MANILA);
}

const pad = (n: number) => String(n).padStart(2, "0");
const lastDay = (y: number, m: number) => new Date(Date.UTC(y, m, 0)).getUTCDate(); // m is 1-12

export type DatePreset = "this_month" | "last_month" | "this_quarter";
export const DATE_PRESET_LABELS: Record<DatePreset, string> = { this_month: "This month", last_month: "Last month", this_quarter: "This quarter" };

// A preset's range, in Manila business dates, from today's Manila date. Quarters are calendar quarters.
export function datePreset(kind: DatePreset, today: string): { from: string; to: string } {
  const [y, m] = today.split("-").map(Number);
  if (kind === "this_month") return { from: `${y}-${pad(m)}-01`, to: `${y}-${pad(m)}-${pad(lastDay(y, m))}` };
  if (kind === "last_month") {
    const [ly, lm] = m === 1 ? [y - 1, 12] : [y, m - 1];
    return { from: `${ly}-${pad(lm)}-01`, to: `${ly}-${pad(lm)}-${pad(lastDay(ly, lm))}` };
  }
  const q0 = Math.floor((m - 1) / 3) * 3 + 1; // first month of the quarter
  return { from: `${y}-${pad(q0)}-01`, to: `${y}-${pad(q0 + 2)}-${pad(lastDay(y, q0 + 2))}` };
}

const units = (n: number) => `${n} ${n === 1 ? "unit" : "units"}`;

// The line above the table: what the filtered set adds up to. A hospital only receives, so it is not told "0 sent".
export function totalsSentence(t: TransfersResponse["totals"], isBloodBank: boolean, outcome = ""): string {
  // Declined and cancelled requests are not transfers and have no units: say how many requests, and nothing about units.
  if (isRefused(outcome)) return t.transfers === 0 ? `No ${outcome} requests match.` : `${t.transfers} ${outcome} ${t.transfers === 1 ? "request" : "requests"}.`;
  const count = `${t.transfers} ${t.transfers === 1 ? "transfer" : "transfers"}`;
  if (t.transfers === 0) return "No transfers match.";
  const flow = isBloodBank ? `${units(t.units_sent)} sent, ${units(t.units_received)} received` : `${units(t.units_received)} received`;
  const expired = t.units_failed > 0
    ? `, ${units(t.units_failed)} expired in transit across ${t.transfers_with_expired_units} ${t.transfers_with_expired_units === 1 ? "transfer" : "transfers"}`
    : "";
  return `${count}: ${flow}${expired}.`;
}
