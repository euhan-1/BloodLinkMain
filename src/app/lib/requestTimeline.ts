// A request's timeline: the status events the server derives from the request row (GET /requests/{id}, and per request card in
// GET /conversations/{id}), and the receipt's wording of them. The conversation's own wording ("You accepted request #38") is in
// lib/conversations.ts. The receipt renders the same `timeline` and `units`, so its wording lives here once.
//
// Events are derived on the server from which timestamps are set; nothing here decides whether an event happened. A time
// or account that was never captured arrives as null and is shown as "not recorded", never left out.

import { EMERGENCY_TYPE_LABELS, type EmergencyType } from "./requestTypes.ts";
import { formatDateTime } from "./dateFormat.ts";

// The server's _TIMELINE_KINDS, in lifecycle order (scripts/check_request_timeline.ts compares them).
export const TIMELINE_KINDS = ["sent", "accepted", "released", "received", "declined", "cancelled"] as const;
export type TimelineKind = (typeof TIMELINE_KINDS)[number];

export type TimelineEvent = {
  kind: TimelineKind;
  at: string | null; // null = the time was never recorded
  order_at: string; // where the event sits among chat messages: its own time, or the previous event's
  facility_id: number;
  facility_name: string | null;
  account_email: string | null; // the email of the facility's one account (a login, not a named person); null = not recorded
  units?: { count: number; dins: string[]; components: string[] } | null; // released; null = which units was not recorded
  outcome?: { arrived: number; failed: { din: string; reason: string }[]; from_ledger: boolean }; // received
};

export type RequestFacility = { id: number; name: string; facility_type: string; address: string | null };

export type LedgerUnit = {
  din: string;
  blood_type: string;
  component: string;
  volume_ml: number;
  collected_date: string;
  expires_date: string;
  origin_location: string;
  days_remaining_at_release: number;
  outcome: "reserved" | "received" | "failed";
  reason: string | null;
  days_remaining_at_receipt: number | null;
};

export type RequestDetail = {
  request: {
    id: number;
    status: string;
    blood_type: string;
    component: string | null;
    quantity: number;
    emergency_type: EmergencyType;
    created_at: string;
    supplier_confirmed_at: string | null;
    requester_confirmed_at: string | null;
    requesting_facility: RequestFacility;
    supplying_facility: RequestFacility;
  };
  timeline: TimelineEvent[];
  units: LedgerUnit[];
  units_recorded: boolean;
  message_count: number; // how many chat messages the request holds; the receipt states this and does not reproduce them
  summary: { asked: number; received: number | null; failed: number | null };
};

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;
const REASON_TEXT: Record<string, string> = { expired_before_receipt: "expired before receipt" };
export const failureReasonText = (reason: string): string => REASON_TEXT[reason] ?? reason;

const requestedText = (r: RequestDetail["request"]): string =>
  `${plural(r.quantity, "unit", "units")} of ${r.blood_type}${r.component ? ` ${r.component}` : ", component not recorded"}`;
const reasonText = (r: RequestDetail["request"]): string => EMERGENCY_TYPE_LABELS[r.emergency_type]?.toLowerCase() ?? r.emergency_type;

// "3 units of Packed RBC: D-1, D-2, D-3", or null when which units were released was never recorded.
function releasedUnitsText(e: TimelineEvent): string | null {
  if (!e.units) return null;
  const comp = e.units.components.join(" and ");
  return `${plural(e.units.count, "unit", "units")}${comp ? ` of ${comp}` : ""}: ${e.units.dins.join(", ")}`;
}

// "2 of 3 arrived. 1 unit not received, expired before receipt: D-2".
function receivedOutcomeText(e: TimelineEvent, r: RequestDetail["request"]): string {
  const o = e.outcome ?? { arrived: 0, failed: [], from_ledger: false };
  const head = `${o.arrived} of ${r.quantity} arrived`;
  if (o.failed.length === 0) return head;
  const reasons = [...new Set(o.failed.map((f) => failureReasonText(f.reason)))].join(" and ");
  return `${head}. ${plural(o.failed.length, "unit", "units")} not received, ${reasons}: ${o.failed.map((f) => f.din).join(", ")}`;
}

// The receipt's own columns: the event's label, and its detail without the facility (which has a column of its own).
export const EVENT_LABELS: Record<TimelineKind, string> = {
  sent: "Sent", accepted: "Accepted", released: "Released", received: "Receipt confirmed", declined: "Declined", cancelled: "Cancelled",
};
export function eventDetail(e: TimelineEvent, r: RequestDetail["request"]): string | null {
  switch (e.kind) {
    case "sent": return `${requestedText(r)}, reason ${reasonText(r)}`;
    case "released": return releasedUnitsText(e) ?? "Which units were released was not recorded";
    case "received": return receivedOutcomeText(e, r);
    default: return null;
  }
}

export function unitOutcomeText(u: Pick<LedgerUnit, "outcome" | "reason">): string {
  if (u.outcome === "received") return "Received";
  if (u.outcome === "failed") return `Failed: ${failureReasonText(u.reason ?? "unknown reason")}`;
  return "Reserved, not yet received";
}

// The account behind an event, by its email. One account per facility, so this names a login, not a person.
export function eventAccountText(e: Pick<TimelineEvent, "account_email">): string {
  return e.account_email ?? "account not recorded";
}

// "30 Oct 2026, 20:27" for the receipt: a record, so it carries the year and the 24-hour clock (no AM/PM to misread); or
// "not recorded". Written by lib/dateFormat.ts, the same writer the transfer history uses.
export function receiptTimeText(at: string | null): string {
  return at ? formatDateTime(at) : "not recorded";
}
