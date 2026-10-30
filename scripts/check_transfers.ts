// Asserts the transfer history's filters, date presets and wording (src/app/lib/transfers.ts). Run: node scripts/check_transfers.ts
import { readFileSync } from "node:fs";
import { formatDate, formatDateTime, formatTime } from "../src/app/lib/dateFormat.ts";
import { receiptTimeText } from "../src/app/lib/requestTimeline.ts";
import {
  NO_FILTERS, datePreset, directionText, hasFilters, manilaToday, otherFacilityName, outcomeOptions, totalsSentence, transferDateText,
  transfersQuery, type TransferRow,
} from "../src/app/lib/transfers.ts";

const eq = (a: unknown, b: unknown, m: string) => { if (JSON.stringify(a) !== JSON.stringify(b)) throw new Error(`${m}: ${JSON.stringify(a)} !== ${JSON.stringify(b)}`); };

// the query sends only what is set, under the names the server reads
eq(transfersQuery(NO_FILTERS), "", "no filters, no query");
eq(transfersQuery({ ...NO_FILTERS, includeActive: true, dateFrom: "2026-10-01", dateTo: "2026-10-31", otherFacilityId: "5", bloodType: "A+", component: "Packed RBC", outcome: "expired_in_transit" }),
  "include_active=true&date_from=2026-10-01&date_to=2026-10-31&other_facility_id=5&blood_type=A%2B&component=Packed+RBC&outcome=expired_in_transit", "every filter");
eq(transfersQuery({ ...NO_FILTERS, bloodType: "O-" }), "blood_type=O-", "one filter");
eq(hasFilters(NO_FILTERS), false, "none set");
eq(hasFilters({ ...NO_FILTERS, includeActive: true }), true, "including in-progress counts as a filter");

// the server's parameter names: every one the query builds is a parameter list_transfers takes
const server = readFileSync(new URL("../server/main.py", import.meta.url), "utf8");
const sig = server.match(/def list_transfers\(([\s\S]*?)\):/);
if (!sig) throw new Error("list_transfers not found in server/main.py");
const params = [...sig[1].matchAll(/^\s*(\w+):/gm)].map((m) => m[1]);
for (const p of ["include_active", "date_from", "date_to", "other_facility_id", "blood_type", "component", "outcome"]) {
  if (!params.includes(p)) throw new Error(`the server does not take ${p}`);
}
// and every outcome the select offers is one the server accepts
const outcomes = server.match(/^TRANSFER_OUTCOMES\s*=\s*\(([^)]*)\)/m);
if (!outcomes) throw new Error("TRANSFER_OUTCOMES not found in server/main.py");
const accepted = [...(outcomes[1].match(/"([^"]+)"/g) ?? [])].map((x) => x.slice(1, -1));
for (const o of outcomeOptions(true).map((x) => x.value).filter(Boolean)) if (!accepted.includes(o)) throw new Error(`the server does not accept outcome ${o}`);

// outcome choices: in progress only when in-progress transfers are included
eq(outcomeOptions(false).map((o) => o.value), ["", "received_in_full", "partly_received", "none_received", "expired_in_transit", "declined", "cancelled"], "outcomes, finished only, then the two refusals");
eq(outcomeOptions(true).map((o) => o.value), ["", "received_in_full", "partly_received", "none_received", "expired_in_transit", "in_progress", "declined", "cancelled"], "in progress is offered once they are included");
eq(outcomeOptions(false).slice(-2).map((o) => o.label), ["Declined", "Cancelled"], "refusal labels");

// Manila business dates, whatever the viewer's zone
eq(manilaToday(new Date("2026-10-31T15:59:00Z")), "2026-10-31", "15:59 UTC is 23:59 on the 31st in Manila");
eq(manilaToday(new Date("2026-10-31T16:01:00Z")), "2026-11-01", "16:01 UTC is already the 1st in Manila");
eq(transferDateText("2026-10-31T16:30:00Z"), "1 Nov 2026", "a row's date is its Manila date");
eq(transferDateText("2026-09-25T04:00:00Z"), "25 Sep 2026", "September is Sep, never Chrome's Sept");
eq(transferDateText("2026-10-31T15:30:00Z"), "31 Oct 2026", "a row's date, the evening before");

// dates are written by us, not by the browser's locale, and the table and the receipt use the same writer
eq(formatDate("2026-10-30T12:27:00Z", "Asia/Manila"), "30 Oct 2026", "date");
eq(formatDateTime("2026-10-30T12:27:00Z", "Asia/Manila"), "30 Oct 2026, 20:27", "date and time, 24-hour");
eq(formatDateTime("2026-10-30T23:05:00Z", "UTC"), "30 Oct 2026, 23:05", "UTC");
eq(formatDateTime("2026-03-03T00:05:00Z", "UTC"), "3 Mar 2026, 00:05", "no leading zero on the day, midnight is 00 not 24");
eq(formatDateTime("2026-12-01T16:30:00Z", "Asia/Manila"), "2 Dec 2026, 00:30", "crossing midnight into the next day in Manila");
eq(Array.from({ length: 12 }, (_, i) => formatDate(new Date(Date.UTC(2026, i, 15)), "UTC").split(" ")[1]), ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], "all twelve month names");
eq(receiptTimeText("2026-10-30T12:27:00Z"), formatDateTime("2026-10-30T12:27:00Z"), "the receipt writes a time exactly as the shared writer does");
eq(receiptTimeText(null), "not recorded", "the receipt, no time");
eq(formatTime("2026-10-30T12:27:00Z", "Asia/Manila"), "20:27", "time only, 24-hour");
eq(formatTime("2026-03-03T00:05:00Z", "UTC"), "00:05", "time only, midnight and a leading zero");
for (const f of ["lib/dateFormat.ts", "lib/transfers.ts", "lib/requestTimeline.ts", "components/RequestThread.tsx"]) {
  if (/toLocale/.test(readFileSync(new URL(`../src/app/${f}`, import.meta.url), "utf8").replace(/\/\/.*$/gm, ""))) throw new Error(`${f} must not rely on the browser's locale`);
}

// presets: calendar month, previous month, calendar quarter
eq(datePreset("this_month", "2026-10-31"), { from: "2026-10-01", to: "2026-10-31" }, "this month");
eq(datePreset("this_month", "2028-02-10"), { from: "2028-02-01", to: "2028-02-29" }, "a leap February");
eq(datePreset("last_month", "2026-11-01"), { from: "2026-10-01", to: "2026-10-31" }, "last month");
eq(datePreset("last_month", "2026-01-15"), { from: "2025-12-01", to: "2025-12-31" }, "last month across the new year");
eq(datePreset("last_month", "2026-03-31"), { from: "2026-02-01", to: "2026-02-28" }, "last month, a short one");
eq(datePreset("this_quarter", "2026-10-29"), { from: "2026-10-01", to: "2026-12-31" }, "this quarter, Q4");
eq(datePreset("this_quarter", "2026-01-01"), { from: "2026-01-01", to: "2026-03-31" }, "this quarter, Q1 first day");
eq(datePreset("this_quarter", "2026-06-30"), { from: "2026-04-01", to: "2026-06-30" }, "this quarter, Q2 last day");
eq(datePreset("this_quarter", "2026-09-15"), { from: "2026-07-01", to: "2026-09-30" }, "this quarter, Q3");

// wording
const row = (direction: "sent" | "received"): TransferRow => ({
  request: {
    id: 1, status: "completed", blood_type: "O+", component: "Packed RBC", quantity: 3, emergency_type: "trauma", created_at: "2026-10-01T00:00:00Z",
    supplier_confirmed_at: null, requester_confirmed_at: null,
    requesting_facility: { id: 1, name: "Riverside", facility_type: "hospital", address: null, doh_license_number: null },
    supplying_facility: { id: 2, name: "Northside", facility_type: "bloodbank", address: null, doh_license_number: null },
  },
  direction, other_facility_id: 0, transfer_at: "2026-10-02T00:00:00Z", outcome: "received_in_full", summary: { asked: 3, received: 3, failed: 0 },
});
eq([directionText("sent"), directionText("received")], ["They requested", "We requested"], "direction says who asked, not what moved");
eq(otherFacilityName(row("sent")), "Riverside", "when we supplied, the other facility is the requester");
eq(otherFacilityName(row("received")), "Northside", "when we requested, the other facility is the supplier");
const t = { transfers: 3, units_sent: 5, units_received: 2, units_failed: 1, transfers_with_expired_units: 1 };
eq(totalsSentence(t, true), "3 transfers: 5 units sent, 2 units received, 1 unit expired in transit across 1 transfer.", "a blood bank's sentence");
eq(totalsSentence({ ...t, units_sent: 0, units_failed: 0, transfers_with_expired_units: 0 }, false), "3 transfers: 2 units received.", "a hospital is not told how much it sent");
eq(totalsSentence({ transfers: 1, units_sent: 1, units_received: 0, units_failed: 0, transfers_with_expired_units: 0 }, true), "1 transfer: 1 unit sent, 0 units received.", "singular");
eq(totalsSentence({ transfers: 0, units_sent: 0, units_received: 0, units_failed: 0, transfers_with_expired_units: 0 }, true), "No transfers match.", "nothing");
const refused = { transfers: 3, units_sent: 0, units_received: 0, units_failed: 0, transfers_with_expired_units: 0 };
eq(totalsSentence(refused, true, "declined"), "3 declined requests.", "declined: requests, and no units");
eq(totalsSentence({ ...refused, transfers: 1 }, false, "cancelled"), "1 cancelled request.", "cancelled, singular");
eq(totalsSentence({ ...refused, transfers: 0 }, true, "declined"), "No declined requests match.", "no declined requests");

console.log("ok");
