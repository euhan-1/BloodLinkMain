// The Messages screen's logic as plain functions (the components only draw it), so scripts/check_conversations.ts can assert it.
// The server (GET /conversations, GET /conversations/{id}, see "Conversations" in server/main.py) has already merged and ordered what it
// sends; what is decided here is how it is shown: who a thing belongs to (mine on the right, theirs on the left), how a finished request
// collapses, where the day separators and the "N new messages" divider go, and how pages of older requests are merged in.

import { dayKey, formatDate, formatTime } from "./dateFormat.ts";
import type { LedgerUnit, RequestDetail, RequestFacility, TimelineEvent } from "./requestTimeline.ts";
import { requestUnitsText } from "./requestTypes.ts";

// ─── What the server sends ──────────────────────────────────────────────────

export type ConversationSummary = {
  facility: { id: number; name: string; facility_type: string; is_active: boolean };
  last_message: { id: number; sender_facility_id: number; text: string; created_at: string } | null;
  unread: number;          // the other facility's messages after my read position
  needs_action: number;    // requests that need MY action (the nav badge's rule)
  open_requests: number;   // pending or accepted
  requests_total: number;
  last_activity_at: string | null;
};

export type ConversationList = { conversations: ConversationSummary[]; needs_action: number };

export type ThreadMessage = {
  id: number;
  request_id: number | null;   // the request it is about, if any
  sender_facility_id: number;
  sender_facility_name: string;
  message: string;
  created_at: string;
};

export type RequestCard = {
  request: RequestDetail["request"];
  direction: "incoming" | "outgoing";
  needs_my_action: boolean;
  timeline: TimelineEvent[];
  units: LedgerUnit[];
  units_recorded: boolean;
  summary: RequestDetail["summary"];
};

export type ThreadEventItem = { type: "event"; request_id: number } & TimelineEvent;
export type ThreadMessageItem = { type: "message" } & ThreadMessage;
export type ThreadItem = ThreadEventItem | ThreadMessageItem;

export type ConversationThread = {
  facility: RequestFacility;
  conversation_id: number | null;
  read_position: number;
  unread: number;
  last_message_id: number;
  counts: { open_requests: number; needs_action: number; requests_total: number };
  requests: RequestCard[];
  thread: ThreadItem[];
  finished: { total: number; shown: number; has_more: boolean; next_cursor: number | null };
  messages_truncated: boolean;
};

export const FINISHED_STATUSES: readonly string[] = ["completed", "failed", "declined", "cancelled"];
export const isFinished = (status: string): boolean => FINISHED_STATUSES.includes(status);

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

// ─── The list ───────────────────────────────────────────────────────────────

export const initials = (name: string): string => {
  const words = name.split(/\s+/).filter((w) => /[A-Za-z0-9]/.test(w));
  if (words.length === 0) return "?";
  const first = words[0][0], last = words.length > 1 ? words[words.length - 1][0] : (words[0][1] ?? "");
  return (first + last).toUpperCase();
};

// The server's order, applied again after a local change (a read, a send): a conversation with something waiting on me first, then the
// latest activity, newest first; ties by facility id. No activity at all sorts last.
export function sortConversations(list: ConversationSummary[]): ConversationSummary[] {
  const time = (c: ConversationSummary) => (c.last_activity_at ? Date.parse(c.last_activity_at) : Number.NEGATIVE_INFINITY);
  return [...list].sort((a, b) => Number(b.needs_action > 0) - Number(a.needs_action > 0) || time(b) - time(a) || a.facility.id - b.facility.id);
}

export const totalNeedsAction = (list: ConversationSummary[]): number => list.reduce((n, c) => n + c.needs_action, 0);

/** The conversation's last message as the row shows it: "You: ..." when I sent it. */
export function previewText(c: ConversationSummary, me: number | null): string {
  if (!c.last_message) return "No messages yet";
  return `${c.last_message.sender_facility_id === me ? "You: " : ""}${c.last_message.text}`;
}

/** The row's time: "10:42" today, "Yesterday", else "2 Nov" (the year only when it is not this year). `now` is injected for tests. */
export function listTimeText(iso: string | null, now: Date = new Date()): string {
  if (!iso) return "";
  const then = new Date(iso);
  const today = dayKey(now), thatDay = dayKey(then);
  if (thatDay === today) return formatTime(then);
  const yesterday = dayKey(new Date(now.getTime() - 24 * 3600 * 1000));
  if (thatDay === yesterday) return "Yesterday";
  const [y, m, d] = thatDay.split("-").map(Number);
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  return `${d} ${months[m - 1]}${y === Number(today.slice(0, 4)) ? "" : ` ${y}`}`;
}

/** The line under a conversation's name in the list: what is waiting on me, else what is open, else nothing. */
export function actionLine(c: Pick<ConversationSummary, "needs_action" | "open_requests">): { text: string; tone: "yours" | "theirs" | "none" } {
  if (c.needs_action > 0) return { text: `${plural(c.needs_action, "request needs", "requests need")} your action`, tone: "yours" };
  if (c.open_requests > 0) return { text: `${plural(c.open_requests, "open request", "open requests")}, waiting on them`, tone: "theirs" };
  return { text: "No open requests", tone: "none" };
}

/** The list header's second line. */
export const listSubline = (list: ConversationSummary[]): { action: string; rest: string } => ({
  action: `${plural(totalNeedsAction(list), "request needs", "requests need")} your action`,
  rest: plural(list.length, "conversation", "conversations"),
});

// ─── The thread header ──────────────────────────────────────────────────────

export function threadSummaryText(counts: ConversationThread["counts"]): { open: string; action: string | null } {
  return {
    open: `${counts.open_requests} ${counts.open_requests === 1 ? "request" : "requests"} still open`,
    action: counts.needs_action > 0 ? `${counts.needs_action} ${counts.needs_action === 1 ? "needs" : "need"} your action` : null,
  };
}

/** The first request card (oldest first, as they sit in the thread) that is waiting on me, for the header's link. */
export function firstWaitingRequest(thread: ConversationThread): number | null {
  const waiting = thread.requests.filter((c) => c.needs_my_action).sort((a, b) => a.request.id - b.request.id);
  return waiting.length > 0 ? waiting[0].request.id : null;
}

// ─── Who did it, and what it says ───────────────────────────────────────────

export type Side = "me" | "them";

/** "You" for me, else the facility's name: the label every message, event and request carries, so alignment is never the only cue. */
export const actorLabel = (facilityId: number, name: string | null, me: number | null): string => (facilityId === me ? "You" : name ?? "A facility");

/** One status event as a short sentence naming its actor ("You accepted request #38"); the details are behind the card's toggle. */
export function eventSentence(e: TimelineEvent, req: RequestDetail["request"], me: number | null): string {
  const who = actorLabel(e.facility_id, e.facility_name, me);
  const n = `request #${req.id}`;
  switch (e.kind) {
    case "sent": return `${who} sent ${n}`;
    case "accepted": return `${who} accepted ${n}`;
    case "released":
      return e.units ? `${who} released ${plural(e.units.count, "unit", "units")} for ${n}` : `${who} released the units for ${n}`;
    case "received": {
      const o = e.outcome ?? { arrived: 0, failed: [], from_ledger: false };
      const failed = o.failed.length > 0 ? `, ${o.failed.length} not received` : "";
      return `${who} confirmed receipt for ${n}: ${o.arrived} of ${req.quantity} arrived${failed}`;
    }
    case "declined": return `${who} declined ${n}`;
    case "cancelled": return `${who} cancelled ${n}`;
  }
}

/** The toggle's label under a release or receipt event ("Units released (9)"), or null when the ledger holds nothing for it. */
export function eventDetailsLabel(e: Pick<TimelineEvent, "kind">, card: Pick<RequestCard, "units" | "units_recorded">): string | null {
  if (!card.units_recorded || card.units.length === 0) return null;
  if (e.kind === "released") return `Units released (${card.units.length})`;
  if (e.kind === "received") return `Units and outcome (${card.units.length})`;
  return null;
}

/** The one line a finished request collapses to: "Completed, 5 of 5 received", "Declined", ... */
export function finishedSummaryText(card: Pick<RequestCard, "request" | "summary">): string {
  const s = card.request.status;
  if (s === "completed" || s === "failed") {
    const received = card.summary.received ?? 0;
    return `${s === "failed" ? "Failed" : "Completed"}, ${received} of ${card.summary.asked} received`;
  }
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/** "5 units of Packed RBC", the compact request line (a request from before components says so). */
export const cardWhatText = (card: Pick<RequestCard, "request">): string => requestUnitsText(card.request);

/** A request can be messaged about, and has a receipt, once it was released or has ended. (The receipt page itself has no status gate.) */
export const hasReceipt = (req: Pick<RequestDetail["request"], "status" | "supplier_confirmed_at">): boolean =>
  !!req.supplier_confirmed_at || isFinished(req.status);

// ─── Rows ───────────────────────────────────────────────────────────────────

export type MessageRow = {
  kind: "message"; key: string; side: Side; label: string; at: string; id: number; text: string; requestId: number | null;
};
export type EventRow = {
  kind: "event"; key: string; side: Side; label: string; at: string | null; requestId: number; eventKind: TimelineEvent["kind"];
  sentence: string; detailsLabel: string | null; event: TimelineEvent;
};
export type RequestRowItem = { kind: "request"; key: string; side: Side; label: string; at: string; card: RequestCard };
export type FinishedRow = { kind: "finished"; key: string; side: Side; label: string; at: string; card: RequestCard; summary: string };
export type DayRow = { kind: "day"; key: string; label: string };
export type DividerRow = { kind: "new-messages"; key: string; count: number };
export type ThreadRow = MessageRow | EventRow | RequestRowItem | FinishedRow | DayRow | DividerRow;

const eventTime = (e: TimelineEvent): string => e.at ?? e.order_at;

/**
 * The thread as rows, in order. Mine on the right, theirs on the left: a message by its sender, an event by the facility that did it, a
 * request by the facility that made it. A request appears as its card at the moment it was sent; a FINISHED request collapses to one
 * line there (its later events are hidden) until it is in `expanded`. A day separator goes before the first item of each day. The
 * "N new messages" divider goes before the first message from the other facility that is after `readAtOpen` (my read position when the
 * thread was opened), with how many there are; null means no divider.
 */
export function threadRows(
  thread: Pick<ConversationThread, "requests" | "thread">, me: number | null,
  opts: { expanded?: ReadonlySet<number>; readAtOpen?: number | null } = {},
): ThreadRow[] {
  const expanded = opts.expanded ?? new Set<number>();
  const cards = new Map(thread.requests.map((c) => [c.request.id, c]));
  const rows: ThreadRow[] = [];
  for (const item of thread.thread) {
    if (item.type === "message") {
      rows.push({
        kind: "message", key: `m-${item.id}`, side: item.sender_facility_id === me ? "me" : "them",
        label: actorLabel(item.sender_facility_id, item.sender_facility_name, me), at: item.created_at, id: item.id, text: item.message, requestId: item.request_id,
      });
      continue;
    }
    const card = cards.get(item.request_id);
    if (!card) continue;
    const collapsed = isFinished(card.request.status) && !expanded.has(card.request.id);
    const side: Side = item.facility_id === me ? "me" : "them";
    if (item.kind === "sent") {
      const requesterId = card.request.requesting_facility.id;
      const label = `${actorLabel(requesterId, card.request.requesting_facility.name, me)} requested`;
      const rowSide: Side = requesterId === me ? "me" : "them";
      rows.push(collapsed
        ? { kind: "finished", key: `f-${card.request.id}`, side: rowSide, label, at: eventTime(item), card, summary: finishedSummaryText(card) }
        : { kind: "request", key: `r-${card.request.id}`, side: rowSide, label, at: eventTime(item), card });
      continue;
    }
    if (collapsed) continue;
    rows.push({
      kind: "event", key: `e-${item.request_id}-${item.kind}`, side, label: actorLabel(item.facility_id, item.facility_name, me), at: item.at,
      requestId: item.request_id, eventKind: item.kind, sentence: eventSentence(item, card.request, me), detailsLabel: eventDetailsLabel(item, card), event: item,
    });
  }

  const out: ThreadRow[] = [];
  let lastDay = "";
  let divided = false;
  const newCount = opts.readAtOpen == null ? 0 : rows.filter((r) => r.kind === "message" && r.side === "them" && r.id > opts.readAtOpen!).length;
  for (const row of rows) {
    const at = row.kind === "day" || row.kind === "new-messages" ? null : row.at ?? null;
    if (at) {
      const day = dayKey(at);
      if (day !== lastDay) {
        out.push({ kind: "day", key: `d-${day}`, label: formatDate(at) });
        lastDay = day;
      }
    }
    if (!divided && newCount > 0 && row.kind === "message" && row.side === "them" && row.id > opts.readAtOpen!) {
      out.push({ kind: "new-messages", key: "new-messages", count: newCount });
      divided = true;
    }
    out.push(row);
  }
  return out;
}

// ─── Pages and local changes ────────────────────────────────────────────────

const sortKey = (i: ThreadItem): [number, number, number] =>
  i.type === "event" ? [Date.parse(i.order_at), 0, i.request_id] : [Date.parse(i.created_at), 1, i.id];
const itemId = (i: ThreadItem): string => (i.type === "event" ? `e-${i.request_id}-${i.kind}` : `m-${i.id}`);

/**
 * Older finished requests ("Show earlier") merged into what is already shown: requests and thread items de-duplicated and kept in the
 * server's order, and the paging state of the OLDEST page. The counts, read position and the rest stay the base's (the newest answer).
 */
export function mergeEarlier(base: ConversationThread, pages: ConversationThread[]): ConversationThread {
  if (pages.length === 0) return base;
  const requests = new Map<number, RequestCard>();
  const items = new Map<string, ThreadItem>();
  for (const t of [...pages, base]) {
    for (const c of t.requests) requests.set(c.request.id, c);
    for (const i of t.thread) items.set(itemId(i), i);
  }
  const oldest = pages[pages.length - 1];
  const sorted = [...items.values()].sort((a, b) => {
    const [ta, ra, ia] = sortKey(a), [tb, rb, ib] = sortKey(b);
    return ta - tb || ra - rb || ia - ib;
  });
  return {
    ...base,
    requests: [...requests.values()].sort((a, b) => a.request.id - b.request.id),
    thread: sorted,
    finished: { total: base.finished.total, shown: [...requests.values()].filter((c) => isFinished(c.request.status)).length, has_more: oldest.finished.has_more, next_cursor: oldest.finished.next_cursor },
    messages_truncated: base.messages_truncated || pages.some((p) => p.messages_truncated),
  };
}

/** A message I just sent, in the thread at once (the next poll returns it too and it is not added twice). */
export function withMessage(thread: ConversationThread, m: ThreadMessage): ConversationThread {
  if (thread.thread.some((i) => i.type === "message" && i.id === m.id)) return thread;
  const added: ThreadItem = { type: "message", ...m };
  return { ...thread, thread: [...thread.thread, added], last_message_id: Math.max(thread.last_message_id, m.id) };
}

/** Whether the thread has something newer than what was last reported as read, and the screen is on it (for POST .../read). */
export function readTarget(thread: Pick<ConversationThread, "last_message_id">, lastMarked: number | null): number | null {
  if (lastMarked === null) return thread.last_message_id;    // the first time this thread is open: also makes the conversation row
  return thread.last_message_id > lastMarked ? thread.last_message_id : null;
}

/** An unread count after reading: zero, and the list is re-sorted by the caller. */
export const markedRead = (list: ConversationSummary[], facilityId: number): ConversationSummary[] =>
  list.map((c) => (c.facility.id === facilityId ? { ...c, unread: 0 } : c));

/** The composer's state for a thread: whether a message can be sent, and what to say when it cannot. */
export function composerState(thread: Pick<ConversationThread, "counts"> | null): { enabled: boolean; hint: string | null } {
  if (thread === null) return { enabled: false, hint: null };
  if (thread.counts.requests_total === 0) return { enabled: false, hint: "Messaging needs at least one request between you and this facility." };
  return { enabled: true, hint: null };
}

export const SEND_LIMIT_TEXT = "You are sending messages too quickly. Wait a few seconds, then send again.";
