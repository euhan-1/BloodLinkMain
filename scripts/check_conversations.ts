// Asserts the Messages screen's logic (src/app/lib/conversations.ts) and how its components are wired. Run: node scripts/check_conversations.ts
// The server side (the endpoints, the counts, the paging) is tests/test_conversations.py; the behaviour in a browser is checked by hand
// with a headless Chrome against a mock API (a throwaway script, not in the repo).
import { existsSync, readFileSync } from "node:fs";
import {
  actionLine, composerState, eventDetailsLabel, eventSentence, finishedSummaryText, firstWaitingRequest, initials, listSubline, listTimeText,
  markedRead, mergeEarlier, previewText, readTarget, sortConversations, threadRows, threadSummaryText, totalNeedsAction, withMessage,
  type ConversationSummary, type ConversationThread, type RequestCard, type ThreadItem, type ThreadMessage,
} from "../src/app/lib/conversations.ts";
import { needsAction, rowFromDetail } from "../src/app/lib/requestInbox.ts";
import type { TimelineEvent } from "../src/app/lib/requestTimeline.ts";

const eq = (a: unknown, b: unknown, m: string) => { if (JSON.stringify(a) !== JSON.stringify(b)) throw new Error(`${m}: ${JSON.stringify(a)} !== ${JSON.stringify(b)}`); };
const yes = (c: unknown, m: string) => { if (!c) throw new Error(m); };
const read = (p: string) => readFileSync(new URL(`../${p}`, import.meta.url), "utf8");

const ME = 5, OTHER = 9;
const day = (d: number, h = 12, m = 0) => new Date(2026, 10, d, h, m).toISOString();   // 2026-11-<d> at h:m local time

// ── the list
const conv = (id: number, over: Partial<ConversationSummary> = {}): ConversationSummary => ({
  facility: { id, name: `Facility ${id}`, facility_type: "hospital", is_active: true }, last_message: null, unread: 0, needs_action: 0, open_requests: 0,
  requests_total: 1, last_activity_at: day(1), ...over,
});
eq(initials("Riverside General Hospital"), "RH", "initials: first and last word");
eq(initials("Northside"), "NO", "one word: its first two letters");
eq(initials("  lipa  city "), "LC", "case and spaces");
eq(initials("St. Camillus"), "SC", "punctuation");
eq(initials(""), "?", "no name");

const list = [conv(1, { last_activity_at: day(3) }), conv(2, { needs_action: 1, last_activity_at: day(1) }), conv(3, { last_activity_at: day(5) }), conv(4, { last_activity_at: null }), conv(6, { needs_action: 2, last_activity_at: day(2) })];
eq(sortConversations(list).map((c) => c.facility.id), [6, 2, 3, 1, 4], "needs my action first, then latest activity; none last");
eq(sortConversations([conv(8, { last_activity_at: day(1) }), conv(7, { last_activity_at: day(1) })]).map((c) => c.facility.id), [7, 8], "a tie goes by facility id");
eq(list.map((c) => c.facility.id), [1, 2, 3, 4, 6], "the input is left alone");
eq(totalNeedsAction(list), 3, "the nav badge: requests needing my action, summed");
eq(listSubline(list), { action: "3 requests need your action", rest: "5 conversations" }, "list subline");
eq(listSubline([conv(1, { needs_action: 1 })]), { action: "1 request needs your action", rest: "1 conversation" }, "singular");
eq(previewText(conv(1, { last_message: { id: 1, sender_facility_id: ME, text: "On our way", created_at: day(1) } }), ME), "You: On our way", "my own last message says You");
eq(previewText(conv(1, { last_message: { id: 1, sender_facility_id: OTHER, text: "Thanks", created_at: day(1) } }), ME), "Thanks", "theirs is plain");
eq(previewText(conv(1), ME), "No messages yet", "no message");
eq(actionLine({ needs_action: 2, open_requests: 3 }), { text: "2 requests need your action", tone: "yours" }, "action line: mine");
eq(actionLine({ needs_action: 1, open_requests: 1 }).text, "1 request needs your action", "action line: singular");
eq(actionLine({ needs_action: 0, open_requests: 2 }), { text: "2 open requests, waiting on them", tone: "theirs" }, "action line: theirs");
eq(actionLine({ needs_action: 0, open_requests: 0 }), { text: "No open requests", tone: "none" }, "action line: none");
const now = new Date(2026, 10, 11, 15, 0);
eq(listTimeText(day(11, 10, 42), now), "10:42", "today: the time");
eq(listTimeText(day(10, 23, 59), now), "Yesterday", "yesterday");
eq(listTimeText(day(2, 9, 0), now), "2 Nov", "this year: day and month");
eq(listTimeText(new Date(2025, 11, 30, 9).toISOString(), now), "30 Dec 2025", "another year: with the year");
eq(listTimeText(null, now), "", "no activity, no time");

// ── the thread
const ev = (kind: TimelineEvent["kind"], at: string | null, facility: number, name: string, over: Partial<TimelineEvent> = {}): TimelineEvent => ({
  kind, at, order_at: at ?? day(1), facility_id: facility, facility_name: name, account_email: `${name.toLowerCase().replace(/\W/g, "")}@example.test`, ...over,
});
const req = (id: number, status: string, requester: number, over: Partial<RequestCard["request"]> = {}): RequestCard["request"] => ({
  id, status, blood_type: "O-", component: "Packed RBC", quantity: 5, emergency_type: "trauma", created_at: day(1), supplier_confirmed_at: null, requester_confirmed_at: null,
  requesting_facility: { id: requester, name: requester === ME ? "Probe Bank" : "Riverside General Hospital", facility_type: "hospital", address: null },
  supplying_facility: { id: requester === ME ? OTHER : ME, name: requester === ME ? "Riverside General Hospital" : "Probe Bank", facility_type: "bloodbank", address: null }, ...over,
});
const ledger = (n: number, outcome: "reserved" | "received" = "received") => Array.from({ length: n }, (_, i) => ({
  din: `W${i}`, blood_type: "O-", component: "Packed RBC", volume_ml: 450, collected_date: "2026-10-30", expires_date: "2026-11-20", origin_location: "Bay 1",
  days_remaining_at_release: 9, outcome, reason: null, days_remaining_at_receipt: outcome === "received" ? 8 : null,
}));
const card = (r: RequestCard["request"], timeline: TimelineEvent[], extra: Partial<RequestCard> = {}): RequestCard => ({
  request: r, direction: r.requesting_facility.id === ME ? "outgoing" : "incoming", needs_my_action: false, timeline, units: [], units_recorded: false,
  summary: { asked: r.quantity, received: null, failed: null }, ...extra,
});
const msg = (id: number, sender: number, when: string, text = `m${id}`, request_id: number | null = null): ThreadMessage => ({
  id, request_id, sender_facility_id: sender, sender_facility_name: sender === ME ? "Probe Bank" : "Riverside General Hospital", message: text, created_at: when,
});
const asItems = (events: [number, TimelineEvent][], messages: ThreadMessage[]): ThreadItem[] => [
  ...events.map(([request_id, e]) => ({ type: "event" as const, request_id, ...e })),
  ...messages.map((m) => ({ type: "message" as const, ...m })),
].sort((a, b) => {
  const ta = Date.parse(a.type === "event" ? a.order_at : a.created_at), tb = Date.parse(b.type === "event" ? b.order_at : b.created_at);
  return ta - tb || Number(a.type === "message") - Number(b.type === "message");
});

// Riverside (9) is the hospital; I am the bank (5). #31 completed (Riverside requested, I accepted and released, they received),
// #38 accepted (waiting on my release), #41 pending (waiting on my decision).
const r31 = req(31, "completed", OTHER, { supplier_confirmed_at: day(3, 10), requester_confirmed_at: day(3, 11) });
const r38 = req(38, "accepted", OTHER, { created_at: day(10, 9) });
const r41 = req(41, "pending", OTHER, { created_at: day(10, 10), component: "Platelets", quantity: 2 });
const e31 = [ev("sent", day(3, 9), OTHER, "Riverside General Hospital"), ev("accepted", day(3, 9, 20), ME, "Probe Bank"), ev("released", day(3, 10), ME, "Probe Bank", { units: { count: 5, dins: ["W0", "W1", "W2", "W3", "W4"], components: ["Packed RBC"] } }),
  ev("received", day(3, 11), OTHER, "Riverside General Hospital", { outcome: { arrived: 5, failed: [], from_ledger: true } })];
const cards = [
  card(r31, e31, { units: ledger(5), units_recorded: true, summary: { asked: 5, received: 5, failed: 0 } }),
  card(r38, [ev("sent", day(10, 9, 14), OTHER, "Riverside General Hospital"), ev("accepted", day(10, 9, 20), ME, "Probe Bank")], { needs_my_action: true }),
  card(r41, [ev("sent", day(10, 10, 31), OTHER, "Riverside General Hospital")], { needs_my_action: true }),
];
const messages = [msg(1, OTHER, day(3, 9, 5), "hello"), msg(2, ME, day(3, 9, 6), "hi", 31), msg(3, OTHER, day(10, 9, 12), "A+ too, please"), msg(4, ME, day(10, 10, 40), "Accepted #38"), msg(5, OTHER, day(10, 10, 42), "Please confirm"), msg(6, OTHER, day(10, 10, 50), "Thanks")];
const thread: ConversationThread = {
  facility: { id: OTHER, name: "Riverside General Hospital", facility_type: "hospital", address: null }, conversation_id: 3, read_position: 4, unread: 2, last_message_id: 6,
  counts: { open_requests: 2, needs_action: 2, requests_total: 3 }, requests: cards,
  thread: asItems(cards.flatMap((c) => c.timeline.map((e): [number, TimelineEvent] => [c.request.id, e])), messages),
  finished: { total: 1, shown: 1, has_more: false, next_cursor: null }, messages_truncated: false,
};

// header
eq(threadSummaryText(thread.counts), { open: "2 requests still open", action: "2 need your action" }, "thread header");
eq(threadSummaryText({ open_requests: 1, needs_action: 1, requests_total: 4 }), { open: "1 request still open", action: "1 needs your action" }, "thread header, singular");
eq(threadSummaryText({ open_requests: 0, needs_action: 0, requests_total: 4 }), { open: "0 requests still open", action: null }, "thread header, nothing waiting");
eq(firstWaitingRequest(thread), 38, "the link goes to the first card waiting on me");
eq(firstWaitingRequest({ ...thread, requests: cards.map((c) => ({ ...c, needs_my_action: false })) }), null, "none waiting, no link");

// wording and alignment by actor: "You" for me, the facility's name for them
const accepted = e31[1], received = e31[3], released = e31[2];
eq(eventSentence(accepted, r31, ME), "You accepted request #31", "my event says You");
eq(eventSentence(accepted, r31, OTHER), "Probe Bank accepted request #31", "the same event for the other side names the facility");
eq(eventSentence(released, r31, ME), "You released 5 units for request #31", "released, with the count");
eq(eventSentence({ ...released, units: null }, r31, ME), "You released the units for request #31", "released, units not recorded");
eq(eventSentence(received, r31, OTHER), "You confirmed receipt for request #31: 5 of 5 arrived", "receipt, mine");
eq(eventSentence(received, r31, ME), "Riverside General Hospital confirmed receipt for request #31: 5 of 5 arrived", "receipt, theirs");
eq(eventSentence({ ...received, outcome: { arrived: 3, failed: [{ din: "A", reason: "x" }, { din: "B", reason: "x" }], from_ledger: true } }, r31, ME).endsWith("3 of 5 arrived, 2 not received"), true, "receipt with failures");
eq(eventSentence(ev("cancelled", day(1), ME, "Probe Bank"), r31, ME), "You cancelled request #31", "cancelled");
eq(eventSentence(ev("declined", day(1), OTHER, "Riverside General Hospital"), r31, ME), "Riverside General Hospital declined request #31", "declined");
eq(eventDetailsLabel(released, cards[0]), "Units released (5)", "the release toggle");
eq(eventDetailsLabel(received, cards[0]), "Units and outcome (5)", "the receipt toggle");
eq(eventDetailsLabel(accepted, cards[0]), null, "no toggle on an acceptance");
eq(eventDetailsLabel(released, cards[1]), null, "no ledger, no toggle");
eq(finishedSummaryText(cards[0]), "Completed, 5 of 5 received", "finished summary: completed");
eq(finishedSummaryText({ request: req(1, "failed", ME), summary: { asked: 3, received: 0, failed: 3 } }), "Failed, 0 of 3 received", "finished summary: failed");
eq(finishedSummaryText({ request: req(1, "declined", ME), summary: { asked: 3, received: null, failed: null } }), "Declined", "finished summary: declined");
eq(finishedSummaryText({ request: req(1, "cancelled", ME), summary: { asked: 3, received: null, failed: null } }), "Cancelled", "finished summary: cancelled");

const rows = threadRows(thread, ME, { readAtOpen: 4 });
const keys = (rs: { key: string }[]) => rs.map((r) => r.key);
const find = <T,>(rs: unknown[], key: string) => (rs as { key: string }[]).find((r) => r.key === key) as unknown as T;
const sideOf = (key: string) => find<{ side: string }>(rows, key).side;
type Live = { kind: "request"; key: string; side: string; label: string; sentence: string | null; detailsLabel: string | null; card: RequestCard };
type Trail = { kind: "trail"; key: string; side: string; label: string; line: string; sentence: string; detailsLabel: string | null };

// ── ONE live card per open request, at its latest status event, on the side of whoever did that event
eq(rows.filter((r) => r.kind === "request").map((r) => (r as Live).card.request.id).sort(), [38, 41], "one live card for each open request, and none for a finished one");
eq(keys(rows.filter((r) => r.kind === "request")), ["live-38-accepted", "live-41-sent"], "the key names the request and its latest event");
eq(sideOf("live-38-accepted"), "me", "#38: I accepted last, so its live card is on my right");
eq(sideOf("live-41-sent"), "them", "#41: nothing but its creation, by them, so it is on their left");
eq(find<Live>(rows, "live-38-accepted").sentence, "You accepted request #38", "the live card says what the latest event was");
eq(find<Live>(rows, "live-38-accepted").label, "You", "labelled with the actor");
eq(find<Live>(rows, "live-41-sent").sentence, null, "a request with no event yet: the creation is the label, not a sentence");
eq(find<Live>(rows, "live-41-sent").label, "Riverside General Hospital requested", "a request's label");
// the earlier event of #38 is a one-line trail where it happened, on the side of the facility that did it
eq(keys(rows.filter((r) => r.kind === "trail")), ["e-38-sent"], "earlier events are trails");
eq(find<Trail>(rows, "e-38-sent").line, "Request #38 · 5 units of Packed RBC · Requested", "a trail line: request, what, status then");
eq(find<Trail>(rows, "e-38-sent").side, "them", "a trail is aligned like the actor of its event");
yes(!rows.some((r) => r.kind === "trail" && r.requestId === 31), "a collapsed finished request has no trails");
// a finished request is unchanged: one line at the moment it was sent, its later events hidden
eq(keys(rows.filter((r) => r.kind === "finished")), ["f-31"], "the finished request is a one-line summary");
eq(sideOf("f-31"), "them", "on the side of the facility that made it");
eq(keys(rows).indexOf("f-31") < keys(rows).indexOf("m-1"), true, "at the moment it was sent");
// alignment: mine on the right, theirs on the left, for every kind of row
eq([sideOf("m-1"), sideOf("m-2"), sideOf("m-4"), sideOf("m-5")], ["them", "me", "me", "them"], "messages by their sender");
const myReq = req(50, "pending", ME);
eq((threadRows({ requests: [card(myReq, [ev("sent", day(11, 8), ME, "Probe Bank")])], thread: asItems([[50, ev("sent", day(11, 8), ME, "Probe Bank")]], []) }, ME).find((r) => r.kind === "request") as Live).side, "me", "my own request's live card is on my side");
// labels: "You" or the facility's name, on every row, and the time
for (const r of rows) {
  if (r.kind === "day" || r.kind === "new-messages") continue;
  yes(r.label === "You" || r.label.includes("Riverside General Hospital"), `row ${r.key} carries a label`);
  eq((r.side === "me") === r.label.startsWith("You"), true, `row ${r.key}: the label agrees with the side`);
}
// no account email anywhere in the rows' own text (it belongs inside the expandable details only)
yes(!JSON.stringify(rows.map((r) => ("sentence" in r ? [r.sentence, r.label] : "label" in r ? [r.label] : []))).includes("@"), "no email in a label or a sentence");
yes(!JSON.stringify(rows.map((r) => ("line" in r ? r.line : ""))).includes("@"), "nor in a trail line");

// ── a new status event moves the live card to the new event, and the old event becomes a trail
const rel38 = ev("released", day(10, 10, 45), ME, "Probe Bank", { units: { count: 4, dins: ["D0", "D1", "D2", "D3"], components: ["Packed RBC"] } });
const card38b = card({ ...r38, supplier_confirmed_at: day(10, 10, 45) }, [...cards[1].timeline, rel38], { units: ledger(4, "reserved"), units_recorded: true, needs_my_action: false });
const cardsB = [cards[0], card38b, cards[2]];
const threadB: ConversationThread = { ...thread, requests: cardsB, thread: asItems(cardsB.flatMap((c) => c.timeline.map((e): [number, TimelineEvent] => [c.request.id, e])), messages) };
const rowsB = threadRows(threadB, ME, { readAtOpen: 4 });
eq(keys(rowsB.filter((r) => r.kind === "request")), ["live-41-sent", "live-38-released"], "after I release #38 its live card is the release, not the acceptance");
eq(find<Live>(rowsB, "live-38-released").sentence, "You released 4 units for request #38", "…saying what just happened");
eq(find<Live>(rowsB, "live-38-released").detailsLabel, "Units released (4)", "…with its units behind a toggle inside it");
eq(keys(rowsB.filter((r) => r.kind === "trail")), ["e-38-sent", "e-38-accepted"], "the acceptance is now a trail");
eq(find<Trail>(rowsB, "e-38-accepted").line, "Request #38 · 5 units of Packed RBC · Accepted", "its one-line trail");
eq(find<Trail>(rowsB, "e-38-accepted").side, "me", "on the side of whoever did it");
eq(keys(rowsB).filter((k) => !keys(rows).includes(k)), ["e-38-accepted", "live-38-released"], "only those two are new");
eq(keys(rows).filter((k) => !keys(rowsB).includes(k)), ["live-38-accepted"], "and only the old live card is gone: nothing is duplicated");
// time order, with messages interleaved: the card sits where the event was (10:45), below the messages before it and above the one after
const orderB = keys(rowsB);
yes(orderB.indexOf("m-5") < orderB.indexOf("live-38-released") && orderB.indexOf("live-38-released") < orderB.indexOf("m-6"), "the live card is among the messages by the time of its event");
// the other side acts: the card moves to their side
const recv38 = ev("received", day(10, 11, 10), OTHER, "Riverside General Hospital", { outcome: { arrived: 5, failed: [], from_ledger: true } });
const card38c = card({ ...r38, status: "completed", supplier_confirmed_at: day(10, 10, 45), requester_confirmed_at: day(10, 11, 10) }, [...card38b.timeline, recv38], { units: ledger(5), units_recorded: true, summary: { asked: 5, received: 5, failed: 0 } });
const threadC: ConversationThread = { ...thread, requests: [cards[0], card38c, cards[2]], thread: asItems([cards[0], card38c, cards[2]].flatMap((c) => c.timeline.map((e): [number, TimelineEvent] => [c.request.id, e])), messages) };
eq(keys(threadRows(threadC, ME).filter((r) => r.kind === "finished")), ["f-31", "f-38"], "a request that just finished collapses like any finished one");
const rowsC = threadRows(threadC, ME, { expanded: new Set([38]) });
eq(find<Live>(rowsC, "live-38-received").side, "them", "expanded, a finished request's live card is at its final event, on the actor's side");
eq(find<Live>(rowsC, "live-38-received").sentence, "Riverside General Hospital confirmed receipt for request #38: 5 of 5 arrived", "…saying the result");
eq(find<Live>(rowsC, "live-38-received").detailsLabel, "Units and outcome (5)", "…with the outcome toggle");
eq(keys(rowsC.filter((r) => r.kind === "trail" && r.requestId === 38)), ["e-38-sent", "e-38-accepted", "e-38-released"], "and its earlier events as trails");
eq(keys(rowsC).includes("f-38"), false, "no summary while it is expanded");
// a message never moves a live card; only a status event does
const later = withMessage(threadB, msg(7, OTHER, day(10, 11, 5), "one more"));
const rowsL = threadRows(later, ME, { readAtOpen: 4 });
eq(keys(rowsL).filter((k) => !orderB.includes(k)), ["m-7"], "a message adds one row");
eq(keys(rowsL).filter((k) => k !== "m-7"), orderB, "and every other row keeps its key and its place, the live card included");
const earlier = withMessage(threadB, msg(8, ME, day(10, 9, 30), "an older message arriving late"));
eq(keys(threadRows(earlier, ME, { readAtOpen: 4 })).filter((k) => k.startsWith("live-")), ["live-41-sent", "live-38-released"], "a message at any time leaves the live cards' keys alone");
yes(keys(threadRows(later, ME)).indexOf("m-7") > keys(threadRows(later, ME)).indexOf("live-38-released"), "a message after the latest event sits below the live card");
// an expanded finished request (31) in the base thread
const open31 = threadRows(thread, ME, { expanded: new Set([31]), readAtOpen: 4 });
eq(find<Live>(open31, "live-31-received").side, "them", "#31 expanded: its live card is the receipt, theirs");
eq(keys(open31.filter((r) => r.kind === "trail" && r.requestId === 31)), ["e-31-sent", "e-31-accepted", "e-31-released"], "and the rest are trails, in time order");
eq(find<Trail>(open31, "e-31-released").side, "me", "I released: on my side");
eq(find<Trail>(open31, "e-31-released").detailsLabel, "Units released (5)", "the details stay an expandable section");
eq(find<Trail>(open31, "e-31-released").line, "Request #31 · 5 units of Packed RBC · Released", "trail line");
eq(keys(open31).includes("f-31"), false, "the summary is gone");
// day separators: before the first item of each day
eq(rows.filter((r) => r.kind === "day").map((r) => (r as { label: string }).label), ["3 Nov 2026", "10 Nov 2026"], "a separator at each new day");
eq(rows[0].kind, "day", "the first row is a day");
eq(threadRows({ requests: [], thread: [] }, ME), [], "an empty thread has no rows");
// the "N new messages" divider: before the first message from them after my read position when the thread was opened
const divider = rows.filter((r) => r.kind === "new-messages") as { count: number; key: string }[];
eq(divider.length, 1, "one divider");
eq(divider[0].count, 2, "it counts their messages after the position (5 and 6; 4 was mine)");
eq(rows[rows.findIndex((r) => r.kind === "new-messages") + 1].key, "m-5", "and sits right before the first of them");
eq(threadRows(thread, ME, { readAtOpen: 6 }).some((r) => r.kind === "new-messages"), false, "nothing after the position, no divider");
eq(threadRows(thread, ME).some((r) => r.kind === "new-messages"), false, "no position known, no divider");
eq(threadRows(thread, ME, { readAtOpen: 0 }).filter((r) => r.kind === "new-messages").map((r) => (r as { count: number }).count), [4], "a position of 0: every message of theirs is new");
// stable keys: nothing is renamed between polls, so a screen reader is not told old messages again
const again = threadRows({ ...thread, thread: [...thread.thread] }, ME, { readAtOpen: 4 });
eq(keys(again), keys(rows), "the same thread gives the same keys");
const grown = threadRows(withMessage(thread, msg(7, OTHER, day(10, 11, 0), "one more")), ME, { readAtOpen: 4 });
eq(keys(grown).filter((k) => !keys(rows).includes(k)), ["m-7"], "a new message adds one key and renames none");
eq(new Set(keys(rowsB)).size, keys(rowsB).length, "no key is used twice");

// ── pages of older finished requests
const old30 = req(30, "cancelled", OTHER, { created_at: day(2) });
const oldPage: ConversationThread = { ...thread, requests: [card(old30, [ev("sent", day(2, 9), OTHER, "Riverside General Hospital"), ev("cancelled", day(2, 9, 30), OTHER, "Riverside General Hospital")])],
  thread: asItems([[30, ev("sent", day(2, 9), OTHER, "Riverside General Hospital")], [30, ev("cancelled", day(2, 9, 30), OTHER, "Riverside General Hospital")]], [msg(0, ME, day(2, 9, 10), "earlier")]),
  finished: { total: 2, shown: 1, has_more: true, next_cursor: 30 } };
const base2 = { ...thread, finished: { total: 2, shown: 1, has_more: true, next_cursor: 31 } };
const merged = mergeEarlier(base2, [oldPage]);
eq(merged.requests.map((c) => c.request.id), [30, 31, 38, 41], "older requests come first, de-duplicated, in id order");
eq(merged.thread.filter((i) => i.type === "message").map((i) => (i as ThreadMessage).id).slice(0, 2), [0, 1], "their messages are woven in time order");
eq(merged.finished, { total: 2, shown: 2, has_more: true, next_cursor: 30 }, "the paging state is the oldest page's");
eq(mergeEarlier(base2, []), base2, "no pages, no change");
eq(mergeEarlier(base2, [oldPage, oldPage]).requests.length, 4, "the same page twice adds nothing");
eq(merged.counts, thread.counts, "the counts, read position and the rest are the newest answer's");
eq(mergeEarlier({ ...base2, thread: [...thread.thread, ...thread.thread] } as ConversationThread, [oldPage]).thread.length, merged.thread.length, "an item repeated in a poll is shown once");

// ── local changes and the read position
const m9 = { ...msg(9, ME, day(10, 12, 0), "sent just now"), conversation_id: 3 };
const sent = withMessage(thread, m9);
eq([sent.thread.length, sent.last_message_id], [thread.thread.length + 1, 9], "a sent message is in the thread at once");
eq(withMessage(sent, m9), sent, "and is not added twice when the poll returns it too");
eq(readTarget(thread, null), 6, "opening a thread always reports (it also makes the conversation row)");
eq(readTarget({ last_message_id: 0 }, null), 0, "even a thread with no messages");
eq(readTarget(thread, 6), null, "nothing newer, nothing to report");
eq(readTarget({ last_message_id: 8 }, 6), 8, "a newer message while it is open is reported again");
eq(markedRead([conv(1, { unread: 3 }), conv(2, { unread: 1 })], 1).map((c) => c.unread), [0, 1], "reading zeroes that conversation's badge only");
eq(composerState(null), { enabled: false, hint: null }, "composer: no thread");
eq(composerState({ counts: { open_requests: 0, needs_action: 0, requests_total: 0 } }).enabled, false, "composer: no request between us, so it is off with a reason");
yes(composerState({ counts: { open_requests: 0, needs_action: 0, requests_total: 0 } }).hint, "and says why");
eq(composerState(thread), { enabled: true, hint: null }, "composer: on");

// ── the same rule the server counts with: needsAction over every status, direction and phase
for (const status of ["pending", "accepted", "completed", "failed", "declined", "cancelled"]) {
  for (const sup of [null, day(1)]) for (const rec of [null, day(1)]) for (const requester of [ME, OTHER]) {
    const r = req(1, status, requester, { supplier_confirmed_at: sup, requester_confirmed_at: rec });
    const iAmSupplier = r.supplying_facility.id === ME;
    const expected = iAmSupplier ? status === "pending" || (status === "accepted" && sup === null) : status === "accepted" && sup !== null && rec === null;
    eq(needsAction(rowFromDetail(r, ME)), expected, `needsAction(${status}, supplier=${!!sup}, receipt=${!!rec}, requester=${requester})`);
  }
}

// ── how the components are wired (the repo has no component test runner: these read the sources)
const parts = read("src/app/components/ThreadParts.tsx");
const messagesTab = read("src/app/screens/Messages.tsx");
const supply = read("src/app/screens/Supply.tsx");
const app = read("src/app/App.tsx");
const list2 = read("src/app/components/ConversationList.tsx");
for (const gone of ["src/app/screens/Requests.tsx", "src/app/components/RequestThread.tsx", "src/app/components/RequestHeader.tsx", "src/app/components/RequestInboxRow.tsx"]) {
  yes(!existsSync(new URL(`../${gone}`, import.meta.url)), `${gone} was removed with the per-request chat`);
}
// names
yes(/label: "Supply"/.test(app) && !/label: "Requests"/.test(app), "the nav tab is Supply");
yes(/messages: "Messages"/.test(supply) && /history: "Transfer history"/.test(supply) && /sourcing: sourcingLabel/.test(supply), "the sub-tabs: the role's sourcing name, Messages, Transfer history");
yes(supply.includes('isBloodBankRequester ? "Source supply" : "Emergency sourcing"'), "the first sub-tab keeps its role-specific name");
for (const file of ["src/app/App.tsx", "src/app/screens/Supply.tsx", "src/app/screens/Messages.tsx", "src/app/screens/HospitalSourcing.tsx", "src/app/screens/HospitalDashboard.tsx", "src/app/components/ReceiptRecord.tsx", "src/app/components/ThreadParts.tsx"]) {
  const code = read(file).replace(/\/\/.*$/gm, "");
  yes(!/Request and chats|Coordination chat|Open the Requests screen|under Requests/.test(code), `${file} uses no old tab name`);
}
// one list of actions: the cards use rowActions through RequestActionButtons, and name no button of their own
yes(/RequestActionButtons/.test(parts) && /rowActions\(inbox\)/.test(parts) && /from "..\/lib\/requestInbox"/.test(parts), "request cards get their buttons from rowActions");
for (const label of ["Confirm release", "Confirm receipt", "Accept", "Decline", "Cancel request"]) {
  yes(!parts.includes(`>${label}<`) && !messagesTab.includes(`>${label}<`), `no hand-written "${label}" button outside RequestStatusParts`);
}
yes(read("src/app/components/RequestStatusParts.tsx").includes('"confirm-release": "Confirm release"'), "the one place the labels are written");
yes(!/RequestActionButtons|rowActions/.test(messagesTab.replace(/import[^\n]*\n/g, "")), "the thread header and the screen have no action buttons of their own");
// the cancel confirmation is kept; failures show inside the card; focus goes to the status text
yes(messagesTab.includes('title="Cancel Request"') && /setCancelConfirmId/.test(messagesTab) && /Keep Request/.test(messagesTab), "Cancel keeps its confirmation dialog");
yes(/role="alert"[^>]*>\{error\}/.test(parts), "an action's error is shown inside its card");
yes(/req-status-\$\{requestId\}/.test(messagesTab) && /id=\{`req-status-\$\{req\.id\}`\} tabIndex=\{-1\}/.test(parts), "after an action focus moves to the card's status text");
// no Receipt button in the thread header; a Receipt link on released or finished cards that opens the same page as Transfer history
yes(!/>Receipt</.test(messagesTab) && !/showReceipt/.test(messagesTab), "no Receipt button in the thread header");
yes(/hasReceipt\(req\)[\s\S]{0,200}href=\{`\/receipt\/\$\{req\.id\}`\}/.test(parts), "a Receipt link on a released or finished card");
yes(read("src/app/screens/TransferHistory.tsx").includes("href={`/receipt/${r.id}`}"), "Transfer history's Receipt link is the same /receipt/{id} page");
// no account email on a card or a chip: only inside the expandable details
yes((parts.match(/eventAccountText\(/g) ?? []).length === 1 && /function EventDetails[\s\S]*eventAccountText\(event\)/.test(parts), "the account email appears only inside the expandable details");
yes(!/account_email/.test(parts) && !/account_email/.test(messagesTab), "no component reads account_email directly");
// composer: attaches to the conversation, an optional chip set by "Message about this", announced errors, 429 told apart, focus stays
yes(/sendConversationMessage\(facilityId, text\.trim\(\), about\)/.test(messagesTab) && /useState<number \| null>\(null\);\s*\n\s*const \[sending/.test(messagesTab), "the composer attaches to the conversation, with no request by default");
yes(/About: request #\{aboutRequestId\}/.test(parts) && /Message about this/.test(parts), "the chip and the link that sets it");
yes(/errorStatus\(err\) === 429 \? SEND_LIMIT_TEXT/.test(messagesTab) && /sending messages too quickly/.test(read("src/app/lib/conversations.ts")), "a 429 says so clearly");
yes(/role="alert"[^>]*>\{error\}<\/p>/.test(parts) && /aria-describedby=\{error \|\| hint/.test(parts), "errors and hints are announced next to the input");
yes(/inputRef\.current\?\.focus\(\); \/\/ focus stays in the composer/.test(messagesTab), "focus stays in the composer after sending");
yes(/<input[\s\S]*?disabled=\{!enabled\}/.test(parts), "the input is disabled only when messaging is not available");
// the log region and its names
yes(/role="log"/.test(messagesTab) && /aria-live="polite"/.test(messagesTab) && /aria-relevant="additions"/.test(messagesTab) && /tabIndex=\{0\}/.test(messagesTab) && /aria-label=\{`Messages and requests with/.test(messagesTab), "the thread is a named, focusable, polite log region");
yes((list2.match(/<h1\b/g) ?? []).length === 1 && />Messages<\/h1>/.test(list2) && (messagesTab.match(/<h1\b/g) ?? []).length === 0, "one h1, Messages, in the list; the thread's title is an h2");
yes(/<h2 id="conversation-heading"/.test(messagesTab) && /<h3 className="text-\[15px\]/.test(parts), "h2 for the conversation, h3 for a request");
// live: the shared poller's messages and requests tokens refetch the list; the open thread polls only while open and visible
yes(/subscribe\("messages"/.test(supply) && /subscribe\("requestsIncoming"/.test(supply) && /subscribe\("requestsOutgoing"/.test(supply), "the list reloads when the messages or a requests token moves");
yes(/const THREAD_POLL_MS = 4000/.test(messagesTab) && /document\.visibilityState !== "visible"\) return/.test(messagesTab) && /visibilitychange/.test(messagesTab), "the open thread polls every 4 s while the tab is visible");
yes(/markConversationRead\(facilityId, target\)/.test(messagesTab) && /readTarget\(thread, lastMarked\.current\)/.test(messagesTab) && /\bvisible\b\]\); \/\/ eslint/.test(messagesTab), "the read position is reported when the thread is visible and again when newer messages arrive");
// bubbles: a border (forced colors), the accent with white text for mine, the foreground colour for every name, no hard-coded role colours
yes(/bg-primary text-white border-primary/.test(parts) && /border px-3\.5 py-2\.5/.test(parts), "my bubble: the accent with white text, and a border");
yes(/font-bold text-foreground/.test(parts) && !/text-primary/.test(parts.replace(/hover:bg-primary-hover|bg-primary-tint/g, "")), "no accent-coloured text in a thread (names are the foreground colour)");
const partsCode = parts.replace(/\/\/.*$/gm, "");
yes(!/#[0-9a-fA-F]{3,8}\b/.test(partsCode) && !/maroon|navy/i.test(partsCode), "no hard-coded colour: the role accent comes from the tokens");
yes(/msg-bubble-own/.test(parts) && /\.msg-bubble-own/.test(read("src/styles/theme.css")) && !/thread-chip/.test(parts + read("src/styles/theme.css")), "the clay rules have their marker classes (the chip is gone: a status event is a trail line or the live card)");
// the old per-request server endpoints stay
const server = read("server/main.py");
yes(server.includes('@app.get("/requests/{request_id}/messages")') && server.includes('@app.post("/requests/{request_id}/messages")'), "the old per-request message endpoints are still on the server");
// what the frontend expects of the server's answers is what the server sends
for (const key of ["conversation_id", "read_position", "last_message_id", "needs_my_action", "units_recorded", "messages_truncated", "next_cursor", "open_requests", "needs_action", "requests_total", "last_activity_at", "last_message"]) {
  yes(server.includes(`"${key}"`), `the server sends ${key}`);
}

console.log("ok");
