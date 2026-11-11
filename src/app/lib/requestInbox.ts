// What a request is to this facility, on a request card in a conversation (components/ThreadParts.tsx): the buttons it has for the
// request (rowActions), whether one of them is a move waiting on this facility (needsAction, the nav badge's rule, the same one the server
// counts with: `_request_needs_my_action` in server/main.py), and where the request stands in this facility's words (inboxStatus).
// There is no inbox any more: the Messages screen lists conversations (lib/conversations.ts), and a request is a card inside one.

import { requesterStatusText, type RequestRow } from "./requestTypes.ts";
import type { RequestDetail } from "./requestTimeline.ts";

export type Direction = "incoming" | "outgoing";

// `position` is set only on an incoming request waiting for this facility's decision: its place in the order to work in.
export type InboxRow = { req: RequestRow; direction: Direction; position?: number };

// The other facility on a row.
export function counterpartName(row: InboxRow): string {
  return (row.direction === "incoming" ? row.req.requesting_facility_name : row.req.supplying_facility_name) ?? "Unknown facility";
}

// What this facility can do to the row, in button order. The one source for the buttons on a row and in the thread's header, and
// (through needsAction) for the tab badge and the status tone, so none of the four can disagree about whether a move is waiting.
export type RowAction = "accept" | "decline" | "cancel" | "confirm-release" | "confirm-receipt";
export function rowActions(row: InboxRow): RowAction[] {
  const { req, direction } = row;
  if (req.status === "pending") return direction === "incoming" ? ["accept", "decline"] : ["cancel"];
  if (req.status === "accepted") {
    if (direction === "incoming" && !req.supplier_confirmed_at) return ["confirm-release"];
    if (direction === "outgoing" && req.supplier_confirmed_at && !req.requester_confirmed_at) return ["confirm-receipt"];
  }
  return [];
}

// This facility has a move to make: accept or decline an incoming request, confirm release of an accepted incoming one, confirm
// receipt of an accepted outgoing one that was released. Cancelling one's own pending request is an action but not a move that
// is waiting on this facility, so it does not count. The one figure behind the tab's badge and the line above the list.
export const needsAction = (row: InboxRow): boolean => rowActions(row).some((a) => a !== "cancel");

// Where the request stands, in this facility's terms, and whether the next move is this facility's or the other's.
export type StatusTone = "yours" | "theirs" | "done";
export function inboxStatus(row: InboxRow): { text: string; tone: StatusTone } {
  const { req, direction } = row;
  const tone: StatusTone = needsAction(row) ? "yours" : req.status === "pending" || req.status === "accepted" ? "theirs" : "done";
  if (req.status === "pending") return { text: direction === "incoming" ? "Awaiting your decision" : requesterStatusText(req), tone };
  if (req.status === "accepted") {
    if (direction === "incoming") return { text: req.supplier_confirmed_at ? "Released, awaiting the requester's receipt" : "Accepted, confirm release", tone };
    return { text: requesterStatusText(req), tone };
  }
  return { text: req.status.charAt(0).toUpperCase() + req.status.slice(1), tone };
}

// The row for a request as GET /requests/{id} describes it, for the thread's header: that poll is fresher than the lists, and it
// carries both facilities' names. The direction is this facility's side of it.
export function rowFromDetail(r: RequestDetail["request"], actingFacilityId: number | null): InboxRow {
  const req: RequestRow = {
    id: r.id, requesting_facility_id: r.requesting_facility.id, supplying_facility_id: r.supplying_facility.id,
    blood_type: r.blood_type, component: r.component, quantity: r.quantity, emergency_type: r.emergency_type, status: r.status,
    created_at: r.created_at, supplier_confirmed_at: r.supplier_confirmed_at, requester_confirmed_at: r.requester_confirmed_at,
    requesting_facility_name: r.requesting_facility.name, supplying_facility_name: r.supplying_facility.name, failed_units: [],
  };
  return { req, direction: actingFacilityId === req.requesting_facility_id ? "outgoing" : "incoming" };
}
