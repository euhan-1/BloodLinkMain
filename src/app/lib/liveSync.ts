// The one poller behind live updates. A signed-in tab asks GET /sync, about every 15 seconds while it is visible, "did anything change for
// my facility?" and gets back small opaque tokens, one per section. A screen subscribes to the sections it shows and refetches only when
// its token moved. There is exactly one timer for the whole app (this class), no matter how many screens subscribe.
//
//   - Visible tab only: nothing is polled while the tab is hidden, and becoming visible (or the window gaining focus) polls at once.
//   - Every subscription carries its own baseline, the token its screen last saw for its section, so no change is lost between a
//     screen's first fetch and the poller's first answer:
//       * subscribing records the poller's current token for the section (nothing is fetched or polled by subscribing);
//       * if the poller has no answer yet the baseline is "unknown", and the first answer counts as a change (the screen's data may
//         be older than that answer): one refetch, at page load only;
//       * a screen that subscribes after an answer has arrived records that token and is told nothing until a later answer differs,
//         so it is never fetched twice for the same load.
//     Subscribe BEFORE the first fetch, or in the same synchronous turn (a poll answer cannot land in between). A token the poller
//     reported a moment ago can already be stale when a screen subscribes; the cost is one redundant refetch at the next answer,
//     never a lost update.
//   - A 304 (If-None-Match matched) means nothing changed: no callbacks, and the next poll is scheduled as usual.
//   - Errors back off (the interval doubles per consecutive failure, up to two minutes) and a success resets it. 401 (the session is
//     gone) and 403 (the account may not use the app: deactivated, or an admin) stop polling for good.
//   - Forecasts are not part of it: they are cached per Manila day and loaded by the Dashboard.
//
// Pure: the network, the clock and the page's visibility are injected, so scripts/check_live_sync.ts can drive it with fakes. The real
// wiring is in liveSyncRuntime.ts.

export type SyncSection = "requestsIncoming" | "requestsOutgoing" | "inventory" | "expiring" | "messages";

export type SyncPayload = {
  notifications: { unread: number; latest_id: number };
  requests: { incoming: string; outgoing: string };
  inventory: string;
  expiring: string;
  messages: string; // moves when a message arrives or is sent in any of my conversations, and when my own read position moves
};

export type PollResult = { status: 200; data: SyncPayload; etag: string | null } | { status: 304 };

export type LiveSyncDeps = {
  /** One request to GET /sync with If-None-Match = etag. Rejects with an Error whose `status` is the HTTP status for an HTTP failure. */
  poll: (etag: string | null) => Promise<PollResult>;
  isVisible: () => boolean;
  setTimer: (fn: () => void, ms: number) => unknown;
  clearTimer: (id: unknown) => void;
  random?: () => number;
};

export const SYNC_INTERVAL_MS = 15_000;
export const SYNC_MAX_BACKOFF_MS = 120_000;
export const SYNC_JITTER = 0.1; // +/-10%, so tabs opened together do not poll in step

export const tokenOf = (p: SyncPayload, section: SyncSection): string =>
  section === "requestsIncoming" ? p.requests.incoming : section === "requestsOutgoing" ? p.requests.outgoing : p[section];

/** One screen's interest in a section: what to call, and the token that screen last saw (null = unknown: nothing to compare with yet). */
type Subscription = { listener: () => void; seen: string | null };

/** How long to wait before the next poll: the interval after a success, doubling per consecutive failure up to the cap. */
export function nextDelay(failures: number, random: () => number = Math.random): number {
  const base = Math.min(SYNC_INTERVAL_MS * 2 ** failures, SYNC_MAX_BACKOFF_MS);
  return Math.round(base * (1 + SYNC_JITTER * (random() * 2 - 1)));
}

type NotificationListener = (n: SyncPayload["notifications"]) => void;

export class LiveSync {
  private deps: LiveSyncDeps;
  private sections = new Map<SyncSection, Set<Subscription>>();
  private notificationListeners = new Set<NotificationListener>();
  private timer: unknown = null;
  private etag: string | null = null;
  private last: SyncPayload | null = null;
  private failures = 0;
  private inFlight = false;
  private started = false;
  private stopped = false; // a 401/403 ends polling until start() is called again

  constructor(deps: LiveSyncDeps) {
    this.deps = deps;
  }

  /** Begin polling (idempotent). Polls at once if the tab is visible. */
  start(): void {
    if (this.started) return;
    this.started = true;
    this.stopped = false;
    this.failures = 0;
    this.etag = null;
    this.last = null;
    if (this.deps.isVisible()) this.pollNow();
  }

  /** End polling and forget the last answer (sign-out, or the nav unmounting). Subscribers stay registered, but what they last saw is
   *  forgotten too: after a restart their data may belong to another session, so the first answer tells each one to refetch. */
  stop(): void {
    this.started = false;
    this.cancelTimer();
    this.etag = null;
    this.last = null;
    for (const set of this.sections.values()) for (const sub of set) sub.seen = null;
  }

  /** Call when the page's visibility or focus changed. Hidden: the pending poll is dropped. Visible: poll now. */
  visibilityChanged(): void {
    if (!this.started || this.stopped) return;
    if (this.deps.isVisible()) this.pollNow();
    else this.cancelTimer();
  }

  /** Be told to refetch `section`: when an answer's token differs from the one this subscription last saw (see the header for what it
   *  starts from). Call it just before the screen's first fetch. Returns the unsubscribe function. */
  subscribe(section: SyncSection, listener: () => void): () => void {
    let set = this.sections.get(section);
    if (!set) this.sections.set(section, (set = new Set()));
    const sub: Subscription = { listener, seen: this.last ? tokenOf(this.last, section) : null };
    set.add(sub);
    return () => { set!.delete(sub); };
  }

  /** The bell's two numbers: called with the latest ones at once if known, then on every answer that carries them. */
  subscribeNotifications(listener: NotificationListener): () => void {
    this.notificationListeners.add(listener);
    if (this.last) this.safely(() => listener(this.last!.notifications));
    return () => { this.notificationListeners.delete(listener); };
  }

  /** How many timers are pending: 0 or 1, never more (the check script asserts it). */
  get pendingTimers(): number {
    return this.timer === null ? 0 : 1;
  }

  pollNow(): void {
    if (!this.started || this.stopped || this.inFlight) return;
    this.cancelTimer();
    void this.tick();
  }

  private async tick(): Promise<void> {
    this.inFlight = true;
    try {
      const result = await this.deps.poll(this.etag);
      this.failures = 0;
      if (result.status === 200) {
        this.last = result.data;
        this.etag = result.etag;
        for (const section of this.sections.keys()) this.notify(section, tokenOf(result.data, section));
        for (const l of [...this.notificationListeners]) this.safely(() => l(result.data.notifications));
      }
    } catch (error) {
      const status = (error as { status?: number }).status;
      if (status === 401 || status === 403) {
        this.stopped = true; // the session or the account is gone: nothing more to ask
        return;
      }
      this.failures += 1;
    } finally {
      this.inFlight = false;
    }
    if (this.started && !this.stopped) this.schedule();
  }

  private schedule(): void {
    this.cancelTimer();
    if (!this.deps.isVisible()) return; // hidden: visibilityChanged() restarts it
    this.timer = this.deps.setTimer(() => { this.timer = null; this.pollNow(); }, nextDelay(this.failures, this.deps.random));
  }

  private cancelTimer(): void {
    if (this.timer !== null) this.deps.clearTimer(this.timer);
    this.timer = null;
  }

  /** Tell each subscription of `section` whose last-seen token is not `token`, once, and record it as seen. */
  private notify(section: SyncSection, token: string): void {
    for (const sub of [...(this.sections.get(section) ?? [])]) {
      if (sub.seen === token) continue;
      sub.seen = token;
      this.safely(sub.listener);
    }
  }

  private safely(fn: () => void): void {
    try { fn(); } catch { /* one screen's failure must not stop the others, or the poller */ }
  }
}
