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
