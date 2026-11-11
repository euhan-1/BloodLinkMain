// Asserts the one poller behind live updates (src/app/lib/liveSync.ts). Run: node scripts/check_live_sync.ts
import {
  LiveSync, SYNC_INTERVAL_MS, SYNC_MAX_BACKOFF_MS, nextDelay, tokenOf,
  type PollResult, type SyncPayload,
} from "../src/app/lib/liveSync.ts";

const eq = (a: unknown, b: unknown, m: string) => { if (JSON.stringify(a) !== JSON.stringify(b)) throw new Error(`${m}: ${JSON.stringify(a)} !== ${JSON.stringify(b)}`); };

const payload = (over: Partial<{ unread: number; latest: number; incoming: string; outgoing: string; inventory: string; expiring: string; messages: string }> = {}): SyncPayload => {
  const o = { unread: 0, latest: 10, incoming: "i0", outgoing: "o0", inventory: "v0", expiring: "e0", messages: "m0", ...over };
  return { notifications: { unread: o.unread, latest_id: o.latest }, requests: { incoming: o.incoming, outgoing: o.outgoing }, inventory: o.inventory, expiring: o.expiring, messages: o.messages };
};

// A world of fake timers, a fake page and a scripted network.
function world() {
  const w = {
    visible: true,
    timers: [] as { id: number; fn: () => void; ms: number }[],
    nextId: 1,
    calls: [] as (string | null)[],          // the If-None-Match each poll sent
    script: [] as (PollResult | { error: number } | { networkError: true })[],
    sync: null as unknown as LiveSync,
    fire() { const t = w.timers.shift(); if (!t) throw new Error("no timer pending"); t.fn(); },
    async settle() { for (let i = 0; i < 10; i++) await Promise.resolve(); },
  };
  w.sync = new LiveSync({
    poll: async (etag) => {
      w.calls.push(etag);
      const next = w.script.shift();
      if (!next) return { status: 304 };
      if ("error" in next) throw Object.assign(new Error("http"), { status: next.error });
      if ("networkError" in next) throw new Error("offline");
      return next;
    },
    isVisible: () => w.visible,
    setTimer: (fn, ms) => { const id = w.nextId++; w.timers.push({ id, fn, ms }); return id; },
    clearTimer: (id) => { w.timers = w.timers.filter((t) => t.id !== id); },
    random: () => 0.5,                         // no jitter, so delays are exact
  });
  return w;
}
const ok = (data: SyncPayload, etag = "e"): PollResult => ({ status: 200, data, etag });

// ── pure helpers
eq((["requestsIncoming", "requestsOutgoing", "inventory", "expiring", "messages"] as const).map((x) => tokenOf(payload(), x)), ["i0", "o0", "v0", "e0", "m0"], "each section reads its own token; incoming and outgoing are separate");
eq([nextDelay(0, () => 0.5), nextDelay(1, () => 0.5), nextDelay(2, () => 0.5)], [SYNC_INTERVAL_MS, SYNC_INTERVAL_MS * 2, SYNC_INTERVAL_MS * 4], "interval, then doubling");
eq(nextDelay(20, () => 0.5), SYNC_MAX_BACKOFF_MS, "the backoff is capped");
eq([nextDelay(0, () => 0), nextDelay(0, () => 1)], [13500, 16500], "jitter is +/-10%");
eq(SYNC_INTERVAL_MS, 15000, "about every 15 seconds");

// ── a later change notifies only its own section (screens that subscribed before the first answer are told once at it: see the gap block above)
{
  const w = world();
  const seen: string[] = [];
  w.script.push(ok(payload()), ok(payload({ inventory: "v1" })));
  w.sync.start();
  await w.settle();
  w.sync.subscribe("inventory", () => seen.push("inventory"));
  w.sync.subscribe("expiring", () => seen.push("expiring"));
  w.sync.subscribe("requestsIncoming", () => seen.push("incoming"));
  eq(w.calls, [null], "the first poll was asked with no validator yet");
  w.fire(); await w.settle();
  eq(seen, ["inventory"], "only the section whose token moved is told");
}

// ── the first-load gap: a change that lands after a screen's first fetch but before the poller's first answer must not be lost
{
  const w = world();
  const told: string[] = [];
  w.sync.subscribe("inventory", () => told.push("inventory"));   // the screen subscribes just before its first fetch; the poller knows nothing yet
  w.sync.subscribe("expiring", () => told.push("expiring"));
  w.script.push(ok(payload({ inventory: "after-the-change" })), { status: 304 }, ok(payload({ inventory: "after-the-change", expiring: "e1" })));
  w.sync.start(); await w.settle();
  eq(told.length > 0, true, "REPRODUCTION: a screen that subscribed before the first answer is told to refetch, because its data may predate that answer");
  eq(told, ["inventory", "expiring"], "each such screen refetches once, even though no token 'moved' (there was nothing to compare with)");
  w.fire(); await w.settle();
  eq(told.length, 2, "a 304 afterwards adds nothing");
  w.fire(); await w.settle();
  eq(told, ["inventory", "expiring", "expiring"], "and from then on only a moved token notifies, once");
}

// ── a screen that loads AFTER the first answer is not double-fetched: it records the poller's token and compares later answers with it
{
  const w = world();
  const told: string[] = [];
  w.script.push(ok(payload({ inventory: "v0" })), { status: 304 }, ok(payload({ inventory: "v1" })), ok(payload({ inventory: "v1" })), ok(payload({ inventory: "v2" })));
  w.sync.start(); await w.settle();
  const callsBefore = w.calls.length;
  w.sync.subscribe("inventory", () => told.push("late"));
  eq(w.calls.length, callsBefore, "subscribing never polls");
  eq(told, [], "a screen that loaded after the answer is not told to fetch again");
  w.fire(); await w.settle();
  eq(told, [], "an unchanged poll (304) tells it nothing");
  w.fire(); await w.settle();
  eq(told, ["late"], "the next answer with a different token tells it once, against the token it recorded at subscribing");
  w.fire(); await w.settle();
  eq(told, ["late"], "the same token again: not told twice");
  w.fire(); await w.settle();
  eq(told, ["late", "late"], "a later move tells it again");
}

// ── the poller's token may be stale when a screen subscribes (a change landed since the last poll): that is one harmless refetch at the next
// answer, never a lost update
{
  const w = world();
  const told: string[] = [];
  w.script.push(ok(payload({ inventory: "v0" })), ok(payload({ inventory: "v1" })));
  w.sync.start(); await w.settle();
  w.sync.subscribe("inventory", () => told.push("x"));            // fetched just now, so it already shows v1's data, but the poller still says v0
  w.fire(); await w.settle();
  eq(told, ["x"], "told once at the next answer; a redundant refetch is the price of never losing a change");
}

// ── subscriptions are independent: a re-subscribing screen (a changed filter) gets a fresh baseline, others are not disturbed
{
  const w = world();
  const a: string[] = [], b: string[] = [];
  w.script.push(ok(payload({ inventory: "v0" })), ok(payload({ inventory: "v1" })));
  w.sync.start(); await w.settle();
  w.sync.subscribe("inventory", () => a.push("a"));
  w.fire(); await w.settle();
  const off = w.sync.subscribe("inventory", () => b.push("b"));   // subscribed after v1 was reported: baseline v1
  off();
  eq([a, b], [["a"], []], "one told once; an unsubscribed one never");
}

// ── 304: nothing changed, nobody told, the next poll still scheduled; the ETag is sent back
{
  const w = world();
  let told = 0;
  w.sync.subscribe("inventory", () => told++);
  w.script.push(ok(payload(), "etag-1"), { status: 304 }, { status: 304 });
  w.sync.start(); await w.settle();
  eq(told, 1, "the first answer tells a screen that subscribed before it, once");
  w.fire(); await w.settle();
  w.fire(); await w.settle();
  eq(w.calls, [null, "etag-1", "etag-1"], "each poll sends the last ETag");
  eq(told, 1, "304: no further callbacks");
  eq(w.timers.length, 1, "and the next poll is scheduled");
}

// ── one timer for the whole app, however many screens subscribe
{
  const w = world();
  for (let i = 0; i < 25; i++) { w.sync.subscribe("inventory", () => {}); w.sync.subscribe("requestsOutgoing", () => {}); }
  w.script.push(ok(payload()));
  w.sync.start(); await w.settle();
  eq([w.timers.length, w.sync.pendingTimers], [1, 1], "50 subscribers, one pending timer");
  w.sync.visibilityChanged(); w.sync.visibilityChanged(); await w.settle();
  eq(w.timers.length <= 1, true, "repeated visibility events never stack timers");
  eq(w.timers[0]?.ms ?? SYNC_INTERVAL_MS, SYNC_INTERVAL_MS, "scheduled about 15 s out");
}

// ── subscribers are called once each, a throwing one does not stop the rest, unsubscribe works
{
  const w = world();
  const log: string[] = [];
  w.sync.subscribe("inventory", () => { throw new Error("a screen broke"); });
  w.sync.subscribe("inventory", () => log.push("second"));
  const off = w.sync.subscribe("inventory", () => log.push("removed"));
  off();
  w.script.push(ok(payload()), ok(payload({ inventory: "v1" })));
  w.sync.start(); await w.settle();
  w.fire(); await w.settle();
  eq(log, ["second", "second"], "the others still run (at the first answer and at the change), and an unsubscribed listener does not");
  eq(w.timers.length, 1, "and polling carried on");
}

// ── the bell hears the numbers on every answer, and at once on subscribing when they are known
{
  const w = world();
  const heard: number[] = [];
  w.script.push(ok(payload({ unread: 1 })), ok(payload({ unread: 2 })));
  w.sync.start(); await w.settle();
  w.sync.subscribeNotifications((n) => heard.push(n.unread));
  eq(heard, [1], "a late subscriber gets the current numbers immediately");
  w.fire(); await w.settle();
  eq(heard, [1, 2], "and every later answer");
}

// ── hidden: nothing is polled; visible again: poll at once
{
  const w = world();
  w.script.push(ok(payload()), ok(payload({ outgoing: "o1" })));
  let told = 0;
  w.sync.subscribe("requestsOutgoing", () => told++);
  w.sync.start(); await w.settle();
  told = 0;                                              // (the first answer told it once; what follows is about visibility)
  w.visible = false;
  w.sync.visibilityChanged();
  eq(w.timers.length, 0, "hiding the tab drops the pending poll");
  eq(w.calls.length, 1, "and nothing was asked while hidden");
  w.visible = true;
  w.sync.visibilityChanged(); await w.settle();
  eq(w.calls.length, 2, "becoming visible polls at once");
  eq(told, 1, "and a change that happened while hidden is picked up");
}
{
  const w = world();
  w.visible = false;
  w.sync.start(); await w.settle();
  eq([w.calls.length, w.timers.length], [0, 0], "starting while hidden asks nothing and sets no timer");
}

// ── a poll never overlaps another
{
  const w = world();
  let release: () => void = () => {};
  const gate = new Promise<void>((r) => { release = r; });
  const sync = new LiveSync({
    poll: async () => { w.calls.push(null); await gate; return ok(payload()); },
    isVisible: () => true, setTimer: (fn, ms) => { w.timers.push({ id: 1, fn, ms }); return 1; }, clearTimer: () => {}, random: () => 0.5,
  });
  sync.start(); sync.pollNow(); sync.pollNow(); sync.visibilityChanged();
  eq(w.calls.length, 1, "while one poll is in flight, others are not started");
  release(); await w.settle();
}

// ── errors back off, a success resets, 401 and 403 stop for good
{
  const w = world();
  w.script.push({ networkError: true }, { error: 500 }, { error: 502 }, ok(payload()));
  w.sync.start(); await w.settle();
  eq(w.timers[0].ms, SYNC_INTERVAL_MS * 2, "after one failure: twice the interval");
  w.fire(); await w.settle();
  eq(w.timers[0].ms, SYNC_INTERVAL_MS * 4, "after two: four times");
  w.fire(); await w.settle();
  eq(w.timers[0].ms, SYNC_INTERVAL_MS * 8, "after three: eight times");
  w.fire(); await w.settle();
  eq(w.timers[0].ms, SYNC_INTERVAL_MS, "a success goes back to the normal interval");
}
{
  const w = world();
  w.script.push(ok(payload()));
  for (let i = 0; i < 12; i++) w.script.push({ error: 503 });
  w.sync.start(); await w.settle();
  for (let i = 0; i < 12; i++) { w.fire(); await w.settle(); }
  eq(w.timers[0].ms, SYNC_MAX_BACKOFF_MS, "never longer than two minutes between polls");
}
for (const status of [401, 403]) {
  const w = world();
  w.script.push(ok(payload()), { error: status });
  w.sync.start(); await w.settle();
  w.fire(); await w.settle();
  eq([w.timers.length, w.calls.length], [0, 2], `${status}: no further timer, no further request`);
  w.sync.visibilityChanged(); w.sync.pollNow(); await w.settle();
  eq(w.calls.length, 2, `${status}: even a focus event does not restart it`);
  w.sync.stop(); w.sync.start(); await w.settle();
  eq(w.calls.length, 3, `${status}: signing in again (start) does`);
}

// ── stop() ends it and start() begins afresh (a new baseline, no validator)
{
  const w = world();
  let told = 0;
  w.sync.subscribe("inventory", () => told++);
  w.script.push(ok(payload(), "old"), ok(payload({ inventory: "other-account" }), "new"));
  w.sync.start(); await w.settle();
  eq(told, 1, "the first answer tells the screen that was already subscribed");
  w.sync.stop();
  eq(w.timers.length, 0, "stop() clears the timer");
  w.sync.start(); await w.settle();
  eq(w.calls, [null, null], "a restart sends no validator from the previous session");
  eq(told, 2, "and the first answer after a restart tells it again: its data may belong to another session");
}

// the messages section (the Messages list subscribes to it, with both requests tokens): a moved messages token tells only its subscribers
{
  const w = world();
  w.script.push(ok(payload()), ok(payload({ messages: "m1" })), ok(payload({ messages: "m1", outgoing: "o1" })));
  const told: string[] = [];
  w.sync.subscribe("messages", () => told.push("messages"));
  w.sync.subscribe("requestsOutgoing", () => told.push("outgoing"));
  w.sync.subscribe("inventory", () => told.push("inventory"));
  w.sync.start();
  await w.settle();
  eq(told.sort(), ["inventory", "messages", "outgoing"], "the first answer counts as a change for a subscription that had nothing to compare with");
  told.length = 0;
  w.fire(); await w.settle();
  eq(told, ["messages"], "a new message moves only the messages token");
  told.length = 0;
  w.fire(); await w.settle();
  eq(told, ["outgoing"], "a request moving moves only the requests token");
  w.sync.stop();
}

console.log("ok");
