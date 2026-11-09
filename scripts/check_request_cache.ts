// Asserts the shared request layer under apiGet (src/app/lib/sharedRequests.ts). Run: node scripts/check_request_cache.ts
import { SharedRequests } from "../src/app/lib/sharedRequests.ts";

const eq = (a: unknown, b: unknown, m: string) => { if (JSON.stringify(a) !== JSON.stringify(b)) throw new Error(`${m}: ${JSON.stringify(a)} !== ${JSON.stringify(b)}`); };

let clock = 0;
const make = () => { clock = 0; return new SharedRequests(() => clock); };
// a loader that resolves when released, and counts how many times it was started
function loader<T>(value: T) {
  const calls = { n: 0 };
  const release: Array<() => void> = [];
  const load = () => { calls.n++; return new Promise<T>((res) => release.push(() => res(value))); };
  return { load, calls, finish: () => release.splice(0).forEach((r) => r()) };
}
const tick = () => new Promise((r) => setTimeout(r, 0));

// ── two callers asking at the same time share one request (the duplicate /auth/me and /forecast)
{
  const s = make(); const l = loader("me");
  const a = s.get("k", l.load), b = s.get("k", l.load);
  l.finish();
  eq([await a, await b, l.calls.n], ["me", "me", 1], "same key, same moment: one request, both get the answer");
}
// ── different keys do not share
{
  const s = make(); const l = loader(1);
  const a = s.get("a", l.load), b = s.get("b", l.load); l.finish(); await a; await b;
  eq(l.calls.n, 2, "different keys are different requests");
}
// ── without ttlMs nothing is remembered: a poll asks again every time
{
  const s = make(); const l = loader("x");
  const a = s.get("k", l.load); l.finish(); await a;
  const b = s.get("k", l.load); l.finish(); await b;
  eq(l.calls.n, 2, "no ttl: the second call after the first finished goes to the network");
}
// ── ttlMs reuses a recent answer, then lets it go
{
  const s = make(); const l = loader("f");
  const a = s.get("k", l.load, { ttlMs: 30_000 }); l.finish(); await a;
  clock = 29_999; eq(await s.get("k", l.load, { ttlMs: 30_000 }), "f", "inside the ttl: the remembered answer");
  eq(l.calls.n, 1, "inside the ttl: no new request");
  clock = 30_000; const c = s.get("k", l.load, { ttlMs: 30_000 }); l.finish(); await c;
  eq(l.calls.n, 2, "at the ttl: asked again");
}
// ── a caller without a ttl never reads what another caller kept
{
  const s = make(); const l = loader("f");
  const a = s.get("k", l.load, { ttlMs: 30_000 }); l.finish(); await a;
  const b = s.get("k", l.load); l.finish(); await b;
  eq(l.calls.n, 2, "a poll (no ttl) is not served from memory");
}
// ── cacheIf: an answer that is not final (a forecast with pending types) is not kept
{
  const s = make(); const l = loader({ pending: ["O+"] });
  const a = s.get("k", l.load, { ttlMs: 30_000, cacheIf: (d) => d.pending.length === 0 }); l.finish(); await a;
  const b = s.get("k", l.load, { ttlMs: 30_000 }); l.finish(); await b;
  eq(l.calls.n, 2, "pending answer is asked for again, never served from memory");
}
// ── fresh skips both the memory and the request already running
{
  const s = make(); const l = loader("v");
  const a = s.get("k", l.load); const b = s.get("k", l.load, { fresh: true });
  l.finish(); await a; await b;
  eq(l.calls.n, 2, "fresh starts its own request");
}
// ── the user's own write: invalidate drops what was kept and what was running
{
  const s = make(); const l = loader("old");
  const a = s.get("k", l.load, { ttlMs: 30_000 }); l.finish(); await a;
  s.invalidate();
  const b = s.get("k", l.load, { ttlMs: 30_000 }); l.finish(); await b;
  eq(l.calls.n, 2, "after a write the kept answer is gone");
}
{
  const s = make(); const l = loader("started before the write");
  const before = s.get("k", l.load, { ttlMs: 30_000 });      // read starts
  s.invalidate();                                            // the user writes
  const after = s.get("k", l.load, { ttlMs: 30_000 });       // read after the write must not join the old one
  l.finish(); await before; await after;
  eq(l.calls.n, 2, "a read started before a write is not handed to a caller after it");
  const again = s.get("k", l.load, { ttlMs: 30_000 }); await again;
  eq(l.calls.n, 2, "...but the read started after the write is kept");
}
{
  const s = make(); const l = loader("pre-write answer");
  const before = s.get("k", l.load, { ttlMs: 30_000 });
  s.invalidate(); l.finish(); await before;
  const next = s.get("k", l.load, { ttlMs: 30_000 }); l.finish(); await next;
  eq(l.calls.n, 2, "an answer that finishes after a write is not kept");
}
// ── a failure is shared while running, then forgotten: the next call tries again
{
  const s = make(); let n = 0; let fail: (e: Error) => void = () => {};
  const load = () => { n++; return new Promise<string>((_, rej) => { fail = rej; }); };
  const a = s.get("k", load).catch((e: Error) => e.message), b = s.get("k", load).catch((e: Error) => e.message);
  fail(new Error("boom"));
  eq([await a, await b, n], ["boom", "boom", 1], "one failed request, both callers see it");
  await tick();
  const c = s.get("k", () => { n++; return Promise.resolve("ok"); });
  eq([await c, n], ["ok", 2], "a failure is never kept: the next call asks again");
}

console.log("ok");
