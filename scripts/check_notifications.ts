// Asserts what the notification bell shows (src/app/lib/notifications.ts). Run: node scripts/check_notifications.ts
import {
  BADGE_MAX, agoText, appendOlder, badgeLabel, isArrival, markEveryRead, markOneRead, mayHaveMore, oldestId, shouldPoll, unreadText,
} from "../src/app/lib/notifications.ts";
import { formatDateTime } from "../src/app/lib/dateFormat.ts";
import type { NotificationItem } from "../src/app/lib/api.ts";

const eq = (a: unknown, b: unknown, m: string) => { if (JSON.stringify(a) !== JSON.stringify(b)) throw new Error(`${m}: ${JSON.stringify(a)} !== ${JSON.stringify(b)}`); };
const note = (id: number, read_at: string | null = null): NotificationItem =>
  ({ id, type: "request_accepted", message: `m${id}`, link: null, read_at, created_at: "2026-10-30T12:00:00Z" });

// ── the badge: the true count, exact up to 99
eq(badgeLabel(0), null, "nothing unread: no badge");
eq([badgeLabel(1), badgeLabel(9), badgeLabel(10), badgeLabel(99)], ["1", "9", "10", "99"], "exact, with no '9+' cut");
eq([BADGE_MAX, badgeLabel(100), badgeLabel(204)], [99, "99+", "99+"], "past 99 it says so");
eq([unreadText(0), unreadText(1), unreadText(204)], ["Nothing unread", "1 unread", "204 unread"], "the header gives the exact number whatever the badge shows");

// ── how long ago: words, then a written-out date, never "2d" or the browser's locale
const t0 = Date.parse("2026-10-30T12:00:00Z");
eq(agoText("2026-10-30T12:00:00Z", t0 + 20_000), "Just now", "under a minute");
eq(agoText("2026-10-30T12:00:00Z", t0 + 60_000), "1 minute ago", "singular");
eq(agoText("2026-10-30T12:00:00Z", t0 + 5 * 60_000), "5 minutes ago", "minutes");
eq(agoText("2026-10-30T12:00:00Z", t0 + 3_600_000), "1 hour ago", "singular hour");
eq(agoText("2026-10-30T12:00:00Z", t0 + 23 * 3_600_000), "23 hours ago", "hours");
eq(agoText("2026-10-30T12:00:00Z", t0 + 25 * 3_600_000), formatDateTime("2026-10-30T12:00:00Z"), "a day or more: the date, written out");
if (!/^\d{1,2} [A-Z][a-z]{2} \d{4}, \d{2}:\d{2}$/.test(agoText("2026-10-30T12:00:00Z", t0 + 9 * 86_400_000))) throw new Error("an old notification is dated explicitly");
for (const ms of [30_000, 600_000, 7_200_000, 90_000_000]) if (/\b\d+[mhd]\b/.test(agoText("2026-10-30T12:00:00Z", t0 + ms))) throw new Error("compressed time");

// ── polling follows the tab
eq([shouldPoll("visible"), shouldPoll("hidden"), shouldPoll("prerender")], [true, false, false], "only a visible tab polls");

// ── the list's bookkeeping
eq(markOneRead([note(3), note(2), note(1, "x")], 2, "T").map((n) => n.read_at), [null, "T", "x"], "one row is read, others keep their own state");
eq(markOneRead([note(1, "earlier")], 1, "T")[0].read_at, "earlier", "an already-read row keeps its own time");
eq(markEveryRead([note(3), note(2, "x")], "T").map((n) => n.read_at), ["T", "x"], "all read, keeping earlier times");
eq([oldestId([note(30), note(12), note(21)]), oldestId([])], [12, null], "the cursor is the oldest id held");
eq([mayHaveMore(50, 50), mayHaveMore(49, 50), mayHaveMore(0, 50)], [true, false, false], "a full page may have more behind it");
eq(appendOlder([note(5), note(4)], [note(4), note(3), note(2)]).map((n) => n.id), [5, 4, 3, 2], "older rows appended, each id once");

// ── an arrival is a newer newest id, not a count that merely changed
eq([isArrival(null, 10), isArrival(10, 10), isArrival(10, 11), isArrival(10, 9)], [false, false, true, false], "first answer, same, newer, older");
console.log("ok");
