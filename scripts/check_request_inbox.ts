// Asserts what a request is to this facility on a request card (lib/requestInbox.ts): the buttons it has (rowActions), the move waiting on
// it (needsAction, the nav badge's rule), its status wording and tone, and the card's row from a conversation's request block.
// The inbox that used to order every request, count them and flag them unread is gone: the Messages screen lists conversations
// (check_conversations.ts), and the server counts the same rule (tests/test_conversations.py).
// Run: node scripts/check_request_inbox.ts
import { counterpartName, needsAction, rowActions, rowFromDetail, inboxStatus, type InboxRow } from "../src/app/lib/requestInbox.ts";
import type { RequestRow } from "../src/app/lib/requestTypes.ts";

const eq = (a: unknown, b: unknown, m: string) => { if (JSON.stringify(a) !== JSON.stringify(b)) throw new Error(`${m}: ${JSON.stringify(a)} !== ${JSON.stringify(b)}`); };
const T0 = Date.UTC(2026, 0, 1);
const req = (id: number, status: string, emergency_type: string, hours: number, extra: Partial<RequestRow> = {}): RequestRow => ({
  id, requesting_facility_id: 1, supplying_facility_id: 2, blood_type: "O+", component: "Packed RBC", quantity: 2,
  emergency_type: emergency_type as RequestRow["emergency_type"], status, created_at: new Date(T0 + hours * 3600e3).toISOString(),
  supplier_confirmed_at: null, requester_confirmed_at: null, failed_units: [], supplying_facility_name: "Bank", requesting_facility_name: "Hospital", ...extra,
});
const released = new Date(T0).toISOString();

// status wording and whose move it is
const row = (r: RequestRow, direction: InboxRow["direction"]): InboxRow => ({ req: r, direction });
eq(inboxStatus(row(req(1, "pending", "restock", 0), "incoming")), { text: "Awaiting your decision", tone: "yours" }, "incoming pending");
eq(inboxStatus(row(req(1, "pending", "restock", 0), "outgoing")), { text: "Awaiting response", tone: "theirs" }, "outgoing pending");
eq(inboxStatus(row(req(1, "accepted", "restock", 0), "incoming")).tone, "yours", "supplier must release");
eq(inboxStatus(row(req(1, "accepted", "restock", 0, { supplier_confirmed_at: released }), "incoming")).tone, "theirs", "supplier waits for receipt");
eq(inboxStatus(row(req(1, "accepted", "restock", 0), "outgoing")).tone, "theirs", "requester waits for release");
eq(inboxStatus(row(req(1, "accepted", "restock", 0, { supplier_confirmed_at: released }), "outgoing")), { text: "Released, confirm receipt", tone: "yours" }, "requester must confirm receipt");
eq(inboxStatus(row(req(1, "failed", "restock", 0), "outgoing")), { text: "Failed", tone: "done" }, "finished");

// the actions: one list, for every request card. Cancel is an action but not a move waiting on this facility.
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
// badge, card and status cannot disagree: the status tone says "yours" exactly when needsAction does, over every state
for (const status of ["pending", "accepted", "completed", "failed", "declined", "cancelled"]) {
  for (const extra of [{}, rel, done]) {
    for (const d of ["incoming", "outgoing"] as const) {
      const r = row(req(1, status, "restock", 0, extra), d);
      eq(inboxStatus(r).tone === "yours", needsAction(r), `tone matches needsAction: ${status} ${d} ${JSON.stringify(extra)}`);
      eq(needsAction(r), rowActions(r).some((a) => a !== "cancel"), "needsAction is rowActions without cancel");
    }
  }
}

// a card's row, from a conversation's request block (the shape of GET /requests/{id}): its own facilities, and direction by which side this facility is
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

console.log("ok");
