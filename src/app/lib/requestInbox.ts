// The Requests screen's single inbox: every request this facility is a party to, in one list. Built from the two lists the
// screen already fetches (GET /requests, the ones this facility sent, and GET /requests/incoming, the ones it supplies), so
// nothing here asks the server for anything new.

import { groupIncomingRequests, requesterStatusText, type RequestRow } from "./requestTypes.ts";

export type Direction = "incoming" | "outgoing";

// `position` is set only on an incoming request waiting for this facility's decision: its place in the order to work in.
export type InboxRow = { req: RequestRow; direction: Direction; position?: number };

const isAccepted = (r: RequestRow) => r.status === "accepted";

// The order of the list. Work in flight only (a finished request is in Transfer history), except the one request that is
// open, which stays in the list while it is open (a finished request opened from Transfer history, or one just declined).
//   1. Incoming, waiting for this facility's decision, numbered: immediate use first (trauma, scheduled surgery), then
//      restock, oldest first within each (the server lists pending rows this way; groupIncomingRequests keeps it).
//   2. Outgoing, waiting for the other facility's response, by the same rule.
//   3. Accepted, either direction, by submission time and never moved by what happens next to them.
//   4. The open request, if it is finished.
export function buildInbox(outgoing: RequestRow[], incoming: RequestRow[], openId: number | null = null): InboxRow[] {
  const mine = outgoing.filter((r) => !incoming.some((i) => i.id === r.id)); // a request is in one list; never twice if it were
  const waitingIn: InboxRow[] = groupIncomingRequests(incoming).waiting.map((req, i) => ({ req, direction: "incoming", position: i + 1 }));
  const waitingOut: InboxRow[] = groupIncomingRequests(mine).waiting.map((req) => ({ req, direction: "outgoing" }));
  const accepted: InboxRow[] = [
    ...incoming.filter(isAccepted).map((req): InboxRow => ({ req, direction: "incoming" })),
    ...mine.filter(isAccepted).map((req): InboxRow => ({ req, direction: "outgoing" })),
  ].sort((a, b) => Date.parse(a.req.created_at) - Date.parse(b.req.created_at) || a.req.id - b.req.id);
  const rows = [...waitingIn, ...waitingOut, ...accepted];
  if (openId !== null && !rows.some((r) => r.req.id === openId)) {
    const inc = incoming.find((r) => r.id === openId);
    const out = mine.find((r) => r.id === openId);
    if (inc) rows.push({ req: inc, direction: "incoming" });
    else if (out) rows.push({ req: out, direction: "outgoing" });
  }
  return rows;
}

// The tab's badge and the line above the list: how many rows have an action for this facility (`action`), and how many more
// are in flight but waiting on the other facility (`other`). The open request kept in the list after it finished is neither.
export function inboxCounts(rows: InboxRow[]): { action: number; other: number } {
  const inFlight = rows.filter((r) => r.req.status === "pending" || r.req.status === "accepted");
  const action = inFlight.filter(needsAction).length;
  return { action, other: inFlight.length - action };
}

// The other facility on a row.
export function counterpartName(row: InboxRow): string {
  return (row.direction === "incoming" ? row.req.requesting_facility_name : row.req.supplying_facility_name) ?? "Unknown facility";
}

// Where the request stands, in this facility's terms, and whether the next move is this facility's or the other's.
export type StatusTone = "yours" | "theirs" | "done";
export function inboxStatus(row: InboxRow): { text: string; tone: StatusTone } {
  const { req, direction } = row;
  if (req.status === "pending") {
    return direction === "incoming"
      ? { text: "Awaiting your decision", tone: "yours" }
      : { text: requesterStatusText(req), tone: "theirs" };
  }
  if (req.status === "accepted") {
    const released = !!req.supplier_confirmed_at;
    if (direction === "incoming") {
      return released
        ? { text: "Released, awaiting the requester's receipt", tone: "theirs" }
        : { text: "Accepted, confirm release", tone: "yours" };
    }
    return { text: requesterStatusText(req), tone: released && !req.requester_confirmed_at ? "yours" : "theirs" };
  }
  return { text: req.status.charAt(0).toUpperCase() + req.status.slice(1), tone: "done" };
}

// This facility has an action available on the row: accept or decline an incoming request, confirm release of an accepted
// incoming one, confirm receipt of an accepted outgoing one that was released. The same three states as the buttons on the row
// (RequestInboxRow), and the one figure behind both the tab's badge and the line above the list.
export const needsAction = (row: InboxRow): boolean => inboxStatus(row).tone === "yours";

// ─── Unread ──────────────────────────────────────────────────────────────────
// The server keeps no read state for a request or its chat, and the screen only ever receives the open thread's messages, so
// "unread" here is the one thing the lists do show: this request has moved since this browser last had it open. Each request's
// phase (status, release, receipt) is remembered when it is opened; a different phase now means "Updated", and a request this
// browser has never held is "New" when it is incoming. A request this facility sent is not new to it, so only a change counts.
// Unread chat messages are not tracked.

export type SeenMap = Record<string, string>;

export const requestPhase = (r: Pick<RequestRow, "status" | "supplier_confirmed_at" | "requester_confirmed_at">): string =>
  `${r.status}:${r.supplier_confirmed_at ?? ""}:${r.requester_confirmed_at ?? ""}`;

const PENDING_PHASE = requestPhase({ status: "pending", supplier_confirmed_at: null, requester_confirmed_at: null });

// `seen` is null until it has been set up, and nothing is flagged before then.
export function unreadKind(row: InboxRow, seen: SeenMap | null): "new" | "updated" | null {
  if (seen === null) return null;
  const phase = requestPhase(row.req);
  const last = seen[row.req.id];
  if (last === phase) return null;
  if (last === undefined) {
    if (row.direction === "incoming") return "new";
    return phase === PENDING_PHASE ? null : "updated";
  }
  return "updated";
}

// What was on screen when the inbox first loaded in this browser counts as already seen, so the first visit is not all "Updated".
export function seedSeen(rows: RequestRow[]): SeenMap {
  return Object.fromEntries(rows.map((r) => [r.id, requestPhase(r)]));
}

const storageKey = (facilityId: number) => `bloodlink.requestSeen.${facilityId}`;

// Per facility, because one browser can be signed in as several. Storage can be blocked or empty: null means "not set up".
export function loadSeen(facilityId: number | null): SeenMap | null {
  if (facilityId === null) return null;
  try {
    const raw = localStorage.getItem(storageKey(facilityId));
    const parsed = raw ? JSON.parse(raw) : null;
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? (parsed as SeenMap) : null;
  } catch {
    return null;
  }
}

export function saveSeen(facilityId: number | null, seen: SeenMap): void {
  if (facilityId === null) return;
  try {
    localStorage.setItem(storageKey(facilityId), JSON.stringify(seen));
  } catch {
    // blocked or full: the flags then last only until the page is reloaded
  }
}
