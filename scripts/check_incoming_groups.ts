// Asserts how the blood bank's incoming list is grouped (groupIncomingRequests in requestTypes.ts). Run: node scripts/check_incoming_groups.ts
import { readFileSync } from "node:fs";
import { groupIncomingRequests, IMMEDIATE_USE_TYPES, isImmediateUse, type RequestRow } from "../src/app/lib/requestTypes.ts";

const eq = (a: unknown, b: unknown, m: string) => { if (JSON.stringify(a) !== JSON.stringify(b)) throw new Error(`${m}: ${JSON.stringify(a)} !== ${JSON.stringify(b)}`); };
const T0 = Date.UTC(2026, 0, 1);
const req = (id: string, status: string, emergency_type: string, hours: number): RequestRow => ({
  id: id as unknown as number, requesting_facility_id: 1, supplying_facility_id: 2, blood_type: "O+", quantity: 2,
  emergency_type: emergency_type as RequestRow["emergency_type"], status, created_at: new Date(T0 + hours * 3600e3).toISOString(),
  supplier_confirmed_at: null, requester_confirmed_at: null, failed_units: [],
});
const ids = (rows: RequestRow[]) => rows.map((r) => r.id as unknown as string);

// The order GET /requests/incoming returns for the nine-row set in server/tests/test_request_priority.py: pending rows are
// priority-sorted into the pending slots, every other row stays where its creation time put it.
const fromServer = [
  req("r1", "completed", "restock", 0), req("r4", "pending", "scheduled_surgery", 3), req("r3", "accepted", "trauma", 2),
  req("r6", "pending", "trauma", 5), req("r8", "pending", "trauma", 7), req("r2", "pending", "restock", 1),
  req("r7", "declined", "trauma", 6), req("r5", "pending", "restock", 4), req("r9", "pending", "restock", 8),
];
const g = groupIncomingRequests(fromServer);
eq(ids(g.immediate), ["r4", "r6", "r8"], "immediate tier, oldest first");
eq(ids(g.restock), ["r2", "r5", "r9"], "restock tier, oldest first");
eq(ids(g.waiting), ["r4", "r6", "r8", "r2", "r5", "r9"], "waiting = immediate then restock (these are the position numbers 1 to 6)");
eq(ids(g.inProgress), ["r3"], "accepted");
eq(ids(g.finished), ["r7", "r1"], "finished, newest first");
eq(ids(g.waiting).concat(ids(g.inProgress), ids(g.finished)).sort(), ids(fromServer).sort(), "every row lands in exactly one section");

// a trauma request that was accepted is in progress, not waiting, even though trauma ranks first while pending
eq(ids(groupIncomingRequests([req("a", "pending", "restock", 1), req("t", "accepted", "trauma", 2)]).waiting), ["a"], "accepted trauma is not waiting");

// a released request (supplier has confirmed, requester has not) is still in progress
const released = { ...req("rel", "accepted", "restock", 1), supplier_confirmed_at: new Date(T0).toISOString() };
eq(ids(groupIncomingRequests([released]).inProgress), ["rel"], "released stays in progress");

// every finished status goes to finished, including one this code has never heard of
eq(ids(groupIncomingRequests([req("c", "completed", "restock", 1), req("f", "failed", "restock", 2), req("d", "declined", "trauma", 3),
  req("x", "cancelled", "restock", 4), req("m", "mystery", "restock", 5)]).finished), ["m", "x", "d", "f", "c"], "finished statuses, newest first");

// the tiers are the server's two, not three reasons: scheduled surgery is immediate use; anything else ranks as restock
eq(["trauma", "scheduled_surgery", "restock", "Trauma", "", "banana"].map(isImmediateUse), [true, true, false, false, false, false], "tier of each reason");
eq(ids(groupIncomingRequests([req("legacy", "pending", "Trauma", 1)]).restock), ["legacy"], "an unrecognised reason is restock, as on the server");

// if a server ever returned the pending rows unsorted, the sections still never show restock above immediate use
const unsorted = [req("a", "pending", "restock", 1), req("b", "pending", "trauma", 2)];
eq(ids(groupIncomingRequests(unsorted).waiting), ["b", "a"], "defensive: immediate use first");

// nothing to group, and the input is left alone
eq(groupIncomingRequests([]), { waiting: [], immediate: [], restock: [], inProgress: [], finished: [] }, "empty");
const before = ids(fromServer);
groupIncomingRequests(fromServer);
eq(ids(fromServer), before, "input not reordered");

// this file's immediate-use list is the server's
const server = readFileSync(new URL("../server/main.py", import.meta.url), "utf8");
const m = server.match(/^IMMEDIATE_USE_TYPES\s*=\s*\{([^}]*)\}/m);
if (!m) throw new Error("IMMEDIATE_USE_TYPES not found in server/main.py");
eq([...(m[1].match(/"([^"]+)"/g) ?? [])].map((s) => s.slice(1, -1)).sort(), [...IMMEDIATE_USE_TYPES].sort(), "frontend and server immediate-use types");

console.log("ok");
