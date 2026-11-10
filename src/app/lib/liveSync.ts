// The one poller behind live updates. A signed-in tab asks GET /sync, about every 15 seconds while it is visible, "did anything change for
// my facility?" and gets back small opaque tokens, one per section. A screen subscribes to the sections it shows and refetches only when
// its token moved. There is exactly one timer for the whole app (this class), no matter how many screens subscribe.
//
//   - Visible tab only: nothing is polled while the tab is hidden, and becoming visible (or the window gaining focus) polls at once.
//   - The first answer is the baseline: it notifies nobody (each screen loads its own data when it mounts); every later answer is
//     compared with the one before it.
//   - A 304 (If-None-Match matched) means nothing changed: no callbacks, and the next poll is scheduled as usual.
//   - Errors back off (the interval doubles per consecutive failure, up to two minutes) and a success resets it. 401 (the session is
//     gone) and 403 (the account may not use the app: deactivated, or an admin) stop polling for good.
//   - Forecasts are not part of it: they are cached per Manila day and loaded by the Dashboard.
//
// Pure: the network, the clock and the page's visibility are injected, so scripts/check_live_sync.ts can drive it with fakes. The real
// wiring is in liveSyncRuntime.ts.

export type SyncSection = "requestsIncoming" | "requestsOutgoing" | "inventory" | "expiring";

export type SyncPayload = {
  notifications: { unread: number; latest_id: number };
  requests: { incoming: string; outgoing: string };
  inventory: string;
  expiring: string;
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

const tokenOf = (p: SyncPayload, section: SyncSection): string =>
  section === "requestsIncoming" ? p.requests.incoming : section === "requestsOutgoing" ? p.requests.outgoing : p[section];

/** The sections whose token differs between two answers. */
export function changedSections(before: SyncPayload, after: SyncPayload): SyncSection[] {
  return (["requestsIncoming", "requestsOutgoing", "inventory", "expiring"] as SyncSection[]).filter((s) => tokenOf(before, s) !== tokenOf(after, s));
}

/** How long to wait before the next poll: the interval after a success, doubling per consecutive failure up to the cap. */
export function nextDelay(failures: number, random: () => number = Math.random): number {
  const base = Math.min(SYNC_INTERVAL_MS * 2 ** failures, SYNC_MAX_BACKOFF_MS);
  return Math.round(base * (1 + SYNC_JITTER * (random() * 2 - 1)));
}

type NotificationListener = (n: SyncPayload["notifications"]) => void;

export class LiveSync {
  private deps: LiveSyncDeps;
  private sections = new Map<SyncSection, Set<() => void>>();
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

  /** End polling and forget the last answer (sign-out, or the nav unmounting). Subscribers stay registered. */
  stop(): void {
    this.started = false;
    this.cancelTimer();
    this.etag = null;
    this.last = null;
  }

  /** Call when the page's visibility or focus changed. Hidden: the pending poll is dropped. Visible: poll now. */
  visibilityChanged(): void {
    if (!this.started || this.stopped) return;
    if (this.deps.isVisible()) this.pollNow();
    else this.cancelTimer();
  }

  /** Be told when the token of `section` changes between two answers. Returns the unsubscribe function. */
  subscribe(section: SyncSection, listener: () => void): () => void {
    let set = this.sections.get(section);
    if (!set) this.sections.set(section, (set = new Set()));
    set.add(listener);
    return () => { set!.delete(listener); };
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
        const previous = this.last;
        this.last = result.data;
        this.etag = result.etag;
        if (previous) for (const section of changedSections(previous, result.data)) this.notify(section);
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

  private notify(section: SyncSection): void {
    for (const l of [...(this.sections.get(section) ?? [])]) this.safely(l);
  }

  private safely(fn: () => void): void {
    try { fn(); } catch { /* one screen's failure must not stop the others, or the poller */ }
  }
}
