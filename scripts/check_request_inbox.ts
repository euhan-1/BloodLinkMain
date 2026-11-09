// Asserts the Requests screen's single inbox: its order, status wording and unread rule (lib/requestInbox.ts). Run: node scripts/check_request_inbox.ts
import { buildInbox, counterpartName, inboxCounts, needsAction, rowActions, rowFromDetail, inboxStatus, requestPhase, seedSeen, unreadKind, type InboxRow } from "../src/app/lib/requestInbox.ts";
import type { RequestRow } from "../src/app/lib/requestTypes.ts";

const eq = (a: unknown, b: unknown, m: string) => { if (JSON.stringify(a) !== JSON.stringify(b)) throw new Error(`${m}: ${JSON.stringify(a)} !== ${JSON.stringify(b)}`); };
const T0 = Date.UTC(2026, 0, 1);
const req = (id: number, status: string, emergency_type: string, hours: number, extra: Partial<RequestRow> = {}): RequestRow => ({
  id, requesting_facility_id: 1, supplying_facility_id: 2, blood_type: "O+", component: "Packed RBC", quantity: 2,
  emergency_type: emergency_type as RequestRow["emergency_type"], status, created_at: new Date(T0 + hours * 3600e3).toISOString(),
  supplier_confirmed_at: null, requester_confirmed_at: null, failed_units: [], supplying_facility_name: "Bank", requesting_facility_name: "Hospital", ...extra,
});
const ids = (rows: InboxRow[]) => rows.map((r) => r.req.id);
const released = new Date(T0).toISOString();

// What the two endpoints return for a bank that both supplies and requests: pending rows priority-sorted into the pending slots.
const incoming = [
  req(1, "completed", "restock", 0), req(4, "pending", "scheduled_surgery", 3), req(3, "accepted", "trauma", 2),
  req(6, "pending", "trauma", 5), req(2, "pending", "restock", 1), req(7, "declined", "trauma", 6),
];
const outgoing = [req(10, "pending", "restock", 4), req(11, "accepted", "restock", 1.5), req(12, "pending", "trauma", 8), req(13, "cancelled", "restock", 9)];
const inbox = buildInbox(outgoing, incoming);

eq(ids(inbox), [4, 6, 2, 12, 10, 11, 3], "waiting for you (immediate use first, then restock), waiting on them, then accepted by submission time");
eq(inbox.filter((r) => r.position !== undefined).map((r) => [r.req.id, r.position]), [[4, 1], [6, 2], [2, 3]], "only incoming waiting rows are numbered, 1..n");
eq(inbox.map((r) => r.direction), ["incoming", "incoming", "incoming", "outgoing", "outgoing", "outgoing", "incoming"], "direction follows the list a row came from");
eq(ids(inbox).filter((id) => [1, 7, 13].includes(id)), [], "finished requests are not in the inbox");

// the open request stays while it is open, last, whether it came from either list
eq(ids(buildInbox(outgoing, incoming, 7)).slice(-1), [7], "an open finished incoming request is kept");
eq(ids(buildInbox(outgoing, incoming, 13)).slice(-1), [13], "an open finished outgoing request is kept");
eq(ids(buildInbox(outgoing, incoming, 4)), ids(inbox), "an open request already listed is not duplicated or moved");
eq(ids(buildInbox(outgoing, incoming, 999)), ids(inbox), "an id in neither list adds nothing");

// accepted rows are not reshuffled by what happens to them: releasing one leaves it where its submission time puts it
const afterRelease = incoming.map((r) => (r.id === 3 ? { ...r, supplier_confirmed_at: released } : r));
eq(ids(buildInbox(outgoing, afterRelease)), ids(inbox), "release does not move an accepted request");
// a pending request that gets accepted leaves the waiting groups and joins accepted, in time order
eq(ids(buildInbox(outgoing, incoming.map((r) => (r.id === 4 ? { ...r, status: "accepted" } : r)))), [6, 2, 12, 10, 11, 3, 4], "accepting moves it to accepted, by time");

// the tab badge and the header line are one figure: rows where this facility has an action, the rest in flight waiting on the other
// side. Here: incoming 4, 6, 2 (decide) and 3 (accepted, release it) are this facility's; outgoing 10, 12 (pending) and 11 (not yet released) are not.
eq(inboxCounts(inbox), { action: 4, other: 3 }, "counts");
eq(inboxCounts(buildInbox(outgoing, incoming, 7)), { action: 4, other: 3 }, "an open finished request is not counted");
eq(inboxCounts([]), { action: 0, other: 0 }, "no rows, no counts");
eq(inbox.filter((r) => needsAction(r)).length, inboxCounts(inbox).action, "the badge is the number of rows with an action");
// releasing hands the move to the requester (outgoing 11) and away from the supplier (incoming 3)
eq(inboxCounts(buildInbox(outgoing.map((r) => (r.id === 11 ? { ...r, supplier_confirmed_at: released } : r)), incoming)), { action: 5, other: 2 }, "a released outgoing request is this facility's to receive");
eq(inboxCounts(buildInbox(outgoing, incoming.map((r) => (r.id === 3 ? { ...r, supplier_confirmed_at: released } : r)))), { action: 3, other: 4 }, "a released incoming request is no longer this facility's");
eq(inboxCounts(buildInbox([req(20, "accepted", "restock", 0, { supplier_confirmed_at: released, requester_confirmed_at: released })], [])), { action: 0, other: 1 }, "received: nothing left to do");
// a hospital sees only its own requests: nothing to decide or release, so only a released one counts
eq(inboxCounts(buildInbox([req(30, "pending", "trauma", 0), req(31, "accepted", "restock", 1), req(32, "accepted", "restock", 2, { supplier_confirmed_at: released })], [])), { action: 1, other: 2 }, "hospital: only the receipt to confirm");

// a request in both lists is shown once
eq(ids(buildInbox([req(3, "accepted", "trauma", 2)], incoming)).filter((id) => id === 3).length, 1, "no duplicate row");
eq(buildInbox([], []), [], "empty");

// status wording and whose move it is
const row = (r: RequestRow, direction: InboxRow["direction"]): InboxRow => ({ req: r, direction });
eq(inboxStatus(row(req(1, "pending", "restock", 0), "incoming")), { text: "Awaiting your decision", tone: "yours" }, "incoming pending");
eq(inboxStatus(row(req(1, "pending", "restock", 0), "outgoing")), { text: "Awaiting response", tone: "theirs" }, "outgoing pending");
eq(inboxStatus(row(req(1, "accepted", "restock", 0), "incoming")).tone, "yours", "supplier must release");
eq(inboxStatus(row(req(1, "accepted", "restock", 0, { supplier_confirmed_at: released }), "incoming")).tone, "theirs", "supplier waits for receipt");
eq(inboxStatus(row(req(1, "accepted", "restock", 0), "outgoing")).tone, "theirs", "requester waits for release");
eq(inboxStatus(row(req(1, "accepted", "restock", 0, { supplier_confirmed_at: released }), "outgoing")), { text: "Released, confirm receipt", tone: "yours" }, "requester must confirm receipt");
eq(inboxStatus(row(req(1, "failed", "restock", 0), "outgoing")), { text: "Failed", tone: "done" }, "finished");

// the actions: one list for the row and the thread's header. Cancel is an action but not a move waiting on this facility.
const rel = { supplier_confirmed_at: released };
const done = { supplier_confirmed_at: released, requester_confirmed_at: released };
eq(rowActions(row(req(1, "pending", "restock", 0), "incoming")), ["accept", "decline"], "incoming pending");
eq(rowActions(row(req(1, "pending", "restock", 0), "outgoing")), ["cancel"], "outgoing pending");
eq(rowActions(row(req(1, "accepted", "restock", 0), "incoming")), ["confirm-release"], "incoming accepted, not released");
eq(rowActions(row(req(1, "accepted", "restock", 0, rel), "incoming")), [], "incoming released: the requester's move");
eq(rowActions(row(req(1, "accepted", "restock", 0), "outgoing")), [], "outgoing not released: the supplier's move");
eq(rowActions(row(req(1, "accepted", "restock", 0, rel), "outgoing")), ["confirm-receipt"], "outgoing released");
eq(rowActions(row(req(1, "accepted", "restock", 0, done), "outgoing")), [], "received");
for (const status of ["completed", "failed", "declined", "cancelled", "mystery"]) {
  for (const d of ["incoming", "outgoing"] as const) eq(rowActions(row(req(1, status, "restock", 0, rel), d)), [], `no actions when ${status}`);
}
eq(needsAction(row(req(1, "pending", "restock", 0), "outgoing")), false, "cancelling is not a move waiting on you");
// badge, header and row cannot disagree: the status tone says "yours" exactly when needsAction does, over every state
for (const status of ["pending", "accepted", "completed", "failed", "declined", "cancelled"]) {
  for (const extra of [{}, rel, done]) {
    for (const d of ["incoming", "outgoing"] as const) {
      const r = row(req(1, status, "restock", 0, extra), d);
      eq(inboxStatus(r).tone === "yours", needsAction(r), `tone matches needsAction: ${status} ${d} ${JSON.stringify(extra)}`);
      eq(needsAction(r), rowActions(r).some((a) => a !== "cancel"), "needsAction is rowActions without cancel");
    }
  }
}

// the header's row, from GET /requests/{id}: its own facilities, and direction by which side this facility is
const detail: Parameters<typeof rowFromDetail>[0] = {
  id: 77, status: "accepted", blood_type: "A-", component: "Platelets", quantity: 4, emergency_type: "trauma", created_at: new Date(T0).toISOString(),
  supplier_confirmed_at: released, requester_confirmed_at: null,
  requesting_facility: { id: 1, name: "Hospital", facility_type: "hospital", address: null }, supplying_facility: { id: 2, name: "Bank", facility_type: "bloodbank", address: null },
};
const fromDetail = rowFromDetail(detail, 1);
eq([fromDetail.direction, fromDetail.req.id, fromDetail.req.blood_type, fromDetail.req.component, fromDetail.req.quantity], ["outgoing", 77, "A-", "Platelets", 4], "requester side is outgoing");
eq([counterpartName(fromDetail), rowFromDetail(detail, 2).direction, counterpartName(rowFromDetail(detail, 2))], ["Bank", "incoming", "Hospital"], "supplier side is incoming; each sees the other");
eq(rowActions(fromDetail), ["confirm-receipt"], "the header's row offers what the list's row would");
eq(inboxStatus(fromDetail), inboxStatus({ req: { ...fromDetail.req }, direction: "outgoing" }), "same status from the same data");

// counterpart is the other facility, and never blank
eq(counterpartName(row(req(1, "pending", "restock", 0), "incoming")), "Hospital", "incoming: the requester");
eq(counterpartName(row(req(1, "pending", "restock", 0), "outgoing")), "Bank", "outgoing: the supplier");
eq(counterpartName(row(req(1, "pending", "restock", 0, { requesting_facility_name: undefined }), "incoming")), "Unknown facility", "unnamed");

// unread: nothing flagged before it is set up; seeded rows are seen; a new incoming request is New; any later move is Updated
const a = row(req(5, "pending", "restock", 0), "incoming");
const o = row(req(6, "pending", "restock", 0), "outgoing");
eq(unreadKind(a, null), null, "not set up");
const seen = seedSeen([a.req, o.req]);
eq([unreadKind(a, seen), unreadKind(o, seen)], [null, null], "seeded rows are seen");
eq(unreadKind(row(req(8, "pending", "restock", 0), "incoming"), seen), "new", "an incoming request never seen is New");
eq(unreadKind(row(req(9, "pending", "restock", 0), "outgoing"), seen), null, "a request this facility sent is not New");
eq(unreadKind(row(req(9, "accepted", "restock", 0), "outgoing"), seen), "updated", "...but its first move is Updated");
eq(unreadKind(row({ ...a.req, status: "accepted" }, "incoming"), seen), "updated", "a seen request that moved is Updated");
eq(unreadKind(row({ ...o.req, status: "accepted", supplier_confirmed_at: released }, "outgoing"), seen), "updated", "release is a move too");
eq(requestPhase(a.req) === requestPhase({ ...a.req, quantity: 99 }), true, "only status and the two confirmations make a phase");

console.log("ok");
