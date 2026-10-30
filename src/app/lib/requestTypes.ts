// What /requests and /requests/incoming return, shared by the Requests screen and the hospital Dashboard's
// "Your requests" column so the two describe a request in the same words.

export type EmergencyType = "trauma" | "scheduled_surgery" | "restock";

export const EMERGENCY_TYPE_LABELS: Record<EmergencyType, string> = {
  trauma: "Trauma",
  scheduled_surgery: "Scheduled Surgery",
  restock: "Restock",
};

export type RequestRow = {
  id: number;
  requesting_facility_id: number;
  supplying_facility_id: number;
  blood_type: string;
  quantity: number;
  emergency_type: EmergencyType;
  status: string;
  created_at: string;
  supplier_confirmed_at: string | null;
  requester_confirmed_at: string | null;
  supplying_facility_name?: string;
  requesting_facility_name?: string;
  failed_units: { din: string; expires_date: string }[];
};

// Not yet resolved: still waiting on the supplier, or accepted and not yet received.
export function isOutstandingRequest(req: RequestRow): boolean {
  return req.status === "pending" || req.status === "accepted";
}

// The requester's view of where an outstanding request stands. Wording follows the Requests screen's own
// ("Awaiting response", "Waiting for supplier to confirm release").
export function requesterStatusText(req: RequestRow): string {
  if (req.status === "pending") return "Awaiting response";
  if (!req.supplier_confirmed_at) return "Accepted, waiting for the supplier to release";
  if (!req.requester_confirmed_at) return "Released, confirm receipt";
  return "Accepted";
}

// True when the next move is the requester's, so the UI can flag it.
export function needsRequesterAction(req: RequestRow): boolean {
  return req.status === "accepted" && !!req.supplier_confirmed_at && !req.requester_confirmed_at;
}

// ─── The supplier's incoming list ────────────────────────────────────────────

// The two tiers the server actually has. Mirrors IMMEDIATE_USE_TYPES in server/main.py (scripts/check_incoming_groups.ts
// compares the two): among a facility's pending requests the server lists these ahead of restock, oldest first within
// each tier. Any other reason, including a legacy value the API would now refuse, ranks as restock there, so it does here.
export const IMMEDIATE_USE_TYPES: readonly string[] = ["trauma", "scheduled_surgery"];
export const isImmediateUse = (reason: string): boolean => IMMEDIATE_USE_TYPES.includes(reason);

export type IncomingGroups = {
  waiting: RequestRow[]; // pending, immediate use then restock: the order a supplier should work in
  immediate: RequestRow[];
  restock: RequestRow[];
  inProgress: RequestRow[]; // accepted, in any phase (release to confirm, or waiting for the requester)
  finished: RequestRow[]; // completed, failed, declined, cancelled; newest first
};

// Splits GET /requests/incoming into what the supplier needs to decide, what is under way, and what is done.
// No sort happens here. The server has already put the pending rows in priority order and left every other row in
// creation order, so this is a stable partition: filtering keeps each group's order, and the only reordering is that
// "finished" is shown newest first (reversing creation order).
export function groupIncomingRequests(rows: RequestRow[]): IncomingGroups {
  const pending = rows.filter((r) => r.status === "pending");
  const immediate = pending.filter((r) => isImmediateUse(r.emergency_type));
  const restock = pending.filter((r) => !isImmediateUse(r.emergency_type));
  return {
    waiting: [...immediate, ...restock],
    immediate,
    restock,
    inProgress: rows.filter((r) => r.status === "accepted"),
    finished: rows.filter((r) => r.status !== "pending" && r.status !== "accepted").reverse(),
  };
}
