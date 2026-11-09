// A small shared layer under apiGet: components that ask for the same thing at the same time share one network request, and a
// caller that opts in (`ttlMs`) can reuse a recent answer. No library: it is a map of promises.
//
//   - In flight: two callers asking for the same key while the first is still running get the same promise (one request).
//   - Short cache: only for a caller that passes `ttlMs`, and only for answers `cacheIf` accepts (e.g. not a forecast that still
//     lists pending types). A caller without `ttlMs` never reads the cache, so polls and the 4-second chat refresh are untouched.
//   - Invalidation: `invalidate()` forgets every cached answer and every in-flight request, so nothing started before the user's
//     own write can be handed to a caller after it, and a request that finishes after an invalidation is not cached.
//
// Pure (no fetch, no window), so scripts/check_request_cache.ts can assert it.

export type SharedOptions<T> = {
  ttlMs?: number; // reuse an answer younger than this; omit for "share while in flight only"
  fresh?: boolean; // skip the cache and any in-flight request, and ask again
  cacheIf?: (data: T) => boolean; // keep this answer for later only if true
};

export class SharedRequests {
  private inflight = new Map<string, Promise<unknown>>();
  private cache = new Map<string, { at: number; data: unknown }>();
  private generation = 0;
  private now: () => number;

  // `now` is injectable so the check script can move the clock.
  constructor(now: () => number = Date.now) {
    this.now = now;
  }

  get<T>(key: string, load: () => Promise<T>, o: SharedOptions<T> = {}): Promise<T> {
    const ttl = o.ttlMs ?? 0;
    if (!o.fresh) {
      const hit = this.cache.get(key);
      if (ttl > 0 && hit && this.now() - hit.at < ttl) return Promise.resolve(hit.data as T);
      const running = this.inflight.get(key);
      if (running) return running as Promise<T>;
    }
    const generation = this.generation;
    const promise: Promise<T> = load().then(
      (data) => {
        if (this.inflight.get(key) === promise) this.inflight.delete(key);
        if (generation === this.generation && ttl > 0 && (!o.cacheIf || o.cacheIf(data))) this.cache.set(key, { at: this.now(), data });
        return data;
      },
      (err) => {
        if (this.inflight.get(key) === promise) this.inflight.delete(key);
        throw err;
      },
    );
    this.inflight.set(key, promise);
    return promise;
  }

  invalidate(): void {
    this.generation++;
    this.cache.clear();
    this.inflight.clear();
  }
}
