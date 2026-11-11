// Asserts the receipt's wording of a request's status events (src/app/lib/requestTimeline.ts). The conversation's own wording, its
// merge in time order and its day separators are asserted in check_conversations.ts.
// Run: node scripts/check_request_timeline.ts
import { readFileSync } from "node:fs";
import {
  eventAccountText, eventDetail, EVENT_LABELS, receiptTimeText, TIMELINE_KINDS, unitOutcomeText, type RequestDetail, type TimelineEvent,
} from "../src/app/lib/requestTimeline.ts";

const eq = (a: unknown, b: unknown, m: string) => { if (JSON.stringify(a) !== JSON.stringify(b)) throw new Error(`${m}: ${JSON.stringify(a)} !== ${JSON.stringify(b)}`); };
const T0 = Date.UTC(2026, 0, 1);
const iso = (h: number) => new Date(T0 + h * 3600e3).toISOString();

const request: RequestDetail["request"] = {
  id: 7, status: "completed", blood_type: "O+", component: "Packed RBC", quantity: 3, emergency_type: "trauma", created_at: iso(0),
  supplier_confirmed_at: iso(2), requester_confirmed_at: iso(3),
  requesting_facility: { id: 1, name: "Riverside", facility_type: "hospital", address: null },
  supplying_facility: { id: 2, name: "Northside", facility_type: "bloodbank", address: null },
};
const ev = (kind: TimelineEvent["kind"], h: number | null, over: Partial<TimelineEvent> = {}): TimelineEvent => ({
  kind, at: h === null ? null : iso(h), order_at: iso(h ?? 0), facility_id: 2, facility_name: "Northside", account_email: "desk@northside.example", ...over,
});

// gaps read as "not recorded", never blank
eq(eventAccountText({ account_email: null }), "account not recorded", "no account");
eq(eventAccountText({ account_email: "desk@northside.example" }), "desk@northside.example", "the account is its email, not an id");
eq(receiptTimeText(null), "not recorded", "receipt: no time");
if (!/2026/.test(receiptTimeText(iso(5)))) throw new Error("a receipt time carries the year");

// the receipt's columns
eq(TIMELINE_KINDS.map((k) => EVENT_LABELS[k]), ["Sent", "Accepted", "Released", "Receipt confirmed", "Declined", "Cancelled"], "event labels");
eq(eventDetail(ev("sent", 0), request), "3 units of O+ Packed RBC, reason trauma", "sent detail");
eq(eventDetail(ev("accepted", 1), request), null, "accepted has no detail");
eq(eventDetail(ev("released", 2, { units: { count: 2, dins: ["A", "B"], components: ["Packed RBC"] } }), request), "2 units of Packed RBC: A, B", "released detail");
eq(eventDetail(ev("released", 2, { units: null }), request), "Which units were released was not recorded", "released detail, not recorded");
eq(eventDetail(ev("received", 3, { outcome: { arrived: 2, failed: [{ din: "B", reason: "expired_before_receipt" }], from_ledger: true } }), request),
  "2 of 3 arrived. 1 unit not received, expired before receipt: B", "received detail");
eq(eventDetail(ev("declined", 1), request), null, "declined has no detail");
eq(["received", "failed", "reserved"].map((o) => unitOutcomeText({ outcome: o as "received", reason: o === "failed" ? "expired_before_receipt" : null })),
  ["Received", "Failed: expired before receipt", "Reserved, not yet received"], "unit outcomes");

// the kinds are the server's, in the same order
const server = readFileSync(new URL("../server/main.py", import.meta.url), "utf8");
const m = server.match(/^_TIMELINE_KINDS\s*=\s*\(([^)]*)\)/m);
if (!m) throw new Error("_TIMELINE_KINDS not found in server/main.py");
eq([...(m[1].match(/"([^"]+)"/g) ?? [])].map((x) => x.slice(1, -1)), [...TIMELINE_KINDS], "frontend and server timeline kinds");

console.log("ok");
