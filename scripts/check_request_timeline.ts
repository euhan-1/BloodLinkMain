// Asserts how a request's status events are worded and woven into the chat thread (src/app/lib/requestTimeline.ts).
// Run: node scripts/check_request_timeline.ts
import { readFileSync } from "node:fs";
import {
  describeEvent, eventAccountText, eventDetail, eventTimeText, EVENT_LABELS, listIsBehind, mergeThread, receiptTimeText, TIMELINE_KINDS, unitOutcomeText, type RequestDetail, type TimelineEvent,
} from "../src/app/lib/requestTimeline.ts";

const eq = (a: unknown, b: unknown, m: string) => { if (JSON.stringify(a) !== JSON.stringify(b)) throw new Error(`${m}: ${JSON.stringify(a)} !== ${JSON.stringify(b)}`); };
const T0 = Date.UTC(2026, 0, 1);
const iso = (h: number) => new Date(T0 + h * 3600e3).toISOString();

const request: RequestDetail["request"] = {
  id: 7, status: "completed", blood_type: "O+", component: "Packed RBC", quantity: 3, emergency_type: "trauma", created_at: iso(0),
  supplier_confirmed_at: iso(2), requester_confirmed_at: iso(3),
  requesting_facility: { id: 1, name: "Riverside", facility_type: "hospital", address: null, doh_license_number: null },
  supplying_facility: { id: 2, name: "Northside", facility_type: "bloodbank", address: null, doh_license_number: null },
};
const ev = (kind: TimelineEvent["kind"], h: number | null, over: Partial<TimelineEvent> = {}): TimelineEvent => ({
  kind, at: h === null ? null : iso(h), order_at: iso(h ?? 0), facility_id: 2, facility_name: "Northside", account_email: "desk@northside.example", ...over,
});

// wording
eq(describeEvent(ev("sent", 0, { facility_name: "Riverside" }), request), "Riverside sent the request for 3 units of O+ Packed RBC, reason trauma", "sent");
eq(describeEvent(ev("accepted", 1), request), "Northside accepted the request", "accepted");
eq(describeEvent(ev("released", 2, { units: { count: 3, dins: ["D-1", "D-2", "D-3"], components: ["Packed RBC"] } }), request),
  "Northside released 3 units of Packed RBC: D-1, D-2, D-3", "released names count, component and DINs");
eq(describeEvent(ev("released", 2, { units: { count: 1, dins: ["D-1"], components: ["Platelets"] } }), request), "Northside released 1 unit of Platelets: D-1", "singular");
eq(describeEvent(ev("released", 2, { units: null }), request), "Northside released the units. Which units were released was not recorded", "released, units not recorded");
eq(describeEvent(ev("received", 3, { facility_name: "Riverside", outcome: { arrived: 3, failed: [], from_ledger: true } }), request),
  "Riverside confirmed receipt: 3 of 3 arrived", "received, all arrived");
eq(describeEvent(ev("received", 3, { facility_name: "Riverside", outcome: { arrived: 2, failed: [{ din: "D-2", reason: "expired_before_receipt" }], from_ledger: true } }), request),
  "Riverside confirmed receipt: 2 of 3 arrived. 1 unit not received, expired before receipt: D-2", "received, one failed with its reason");
eq(describeEvent(ev("received", 3, { facility_name: "Riverside", outcome: { arrived: 0, failed: [{ din: "A", reason: "expired_before_receipt" }, { din: "B", reason: "expired_before_receipt" }, { din: "C", reason: "expired_before_receipt" }], from_ledger: true } }), request),
  "Riverside confirmed receipt: 0 of 3 arrived. 3 units not received, expired before receipt: A, B, C", "received, all failed");
eq(describeEvent(ev("declined", 1), request), "Northside declined the request", "declined");
eq(describeEvent(ev("cancelled", 1, { facility_name: "Riverside" }), request), "Riverside cancelled the request", "cancelled");
eq(describeEvent(ev("accepted", 1, { facility_name: null }), request), "A facility accepted the request", "unknown facility name");
eq(describeEvent(ev("sent", 0), { ...request, component: null }), "Northside sent the request for 3 units of O+, component not recorded, reason trauma", "request with no component");

// gaps read as "not recorded", never blank
eq(eventTimeText({ at: null }), "time not recorded", "no time");
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
if (!/\d/.test(eventTimeText({ at: iso(5) }))) throw new Error("a recorded time shows a time");
eq(eventTimeText({ at: iso(5) }), receiptTimeText(iso(5)), "the chat timeline writes a time exactly as the receipt does");

// the thread: one time order, events before messages at the same instant, lifecycle order among events
const msg = (id: number, h: number) => ({ id, created_at: iso(h), message: `m${id}` });
const merged = mergeThread([msg(2, 2.5), msg(1, 1.5), msg(3, 3)], [ev("sent", 0), ev("accepted", 1), ev("released", 2), ev("received", 3)]);
eq(merged.map((i) => i.key), ["e-sent", "e-accepted", "m-1", "e-released", "m-2", "e-received", "m-3"].map((k) => k), "events and messages in time order");
eq(mergeThread([msg(9, 1)], [ev("accepted", 1)]).map((i) => i.key), ["e-accepted", "m-9"], "an event precedes a message at the same instant");
// events with no time of their own sit at their order_at, after the event before them, not at 1970
const historical = [ev("sent", 0), ev("accepted", null, { order_at: iso(0) }), ev("released", 2)];
eq(mergeThread([msg(1, 1)], historical).map((i) => i.key), ["e-sent", "e-accepted", "m-1", "e-released"], "an event with no time keeps its place");
eq(mergeThread([], []).length, 0, "empty");
const before = [ev("sent", 0)].map((e) => e.kind);
mergeThread([msg(1, 1)], [ev("sent", 0)]);
eq(before, ["sent"], "input untouched");

// the lists catch up when the server reports a different phase
const row = { status: "accepted", supplier_confirmed_at: null, requester_confirmed_at: null };
eq(listIsBehind(row, { ...request, status: "accepted", supplier_confirmed_at: null, requester_confirmed_at: null }), false, "in step");
eq(listIsBehind(row, { ...request, status: "accepted", supplier_confirmed_at: iso(2), requester_confirmed_at: null }), true, "released meanwhile");
eq(listIsBehind(row, { ...request, status: "declined", supplier_confirmed_at: null, requester_confirmed_at: null }), true, "status changed");
eq(listIsBehind(undefined, request), false, "request not in either list");

// the kinds are the server's, in the same order
const server = readFileSync(new URL("../server/main.py", import.meta.url), "utf8");
const m = server.match(/^_TIMELINE_KINDS\s*=\s*\(([^)]*)\)/m);
if (!m) throw new Error("_TIMELINE_KINDS not found in server/main.py");
eq([...(m[1].match(/"([^"]+)"/g) ?? [])].map((x) => x.slice(1, -1)), [...TIMELINE_KINDS], "frontend and server timeline kinds");

console.log("ok");
