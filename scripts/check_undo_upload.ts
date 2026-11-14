// Asserts the historical upload's undo confirmation (src/app/lib/undoUpload.ts) and its wiring in UploadHistoryPanel. Run: node scripts/check_undo_upload.ts
import { readFileSync } from "node:fs";
import type { UndoSummary } from "../src/app/lib/api.ts";
import { UNDO_ACK_LABEL, undoNeedsAcknowledgement, undoSummarySentence } from "../src/app/lib/undoUpload.ts";

const eq = (a: unknown, b: unknown, m: string) => { if (JSON.stringify(a) !== JSON.stringify(b)) throw new Error(`${m}: ${JSON.stringify(a)} !== ${JSON.stringify(b)}`); };
const fail = (m: string) => { throw new Error(m); };

const base: UndoSummary = {
  days_deleted: 150, days_restored: 0, first_date: "2026-04-24", last_date: "2026-09-20", deleted_first_date: "2026-04-24", deleted_last_date: "2026-09-20",
  days_remaining: 24, forecast_min_days: 30,
};

// the example the confirmation is built around
eq(undoSummarySentence(base),
  "This will permanently remove 150 days of history (24 Apr 2026 to 20 Sep 2026). You would have 24 days left. The forecast needs at least 30 days.", "big delete, under 30 left");
eq(undoNeedsAcknowledgement(base), true, "warns");

// a big delete that still leaves enough: warned, but no forecast sentence
const big = { ...base, days_remaining: 200 };
eq(undoSummarySentence(big), "This will permanently remove 150 days of history (24 Apr 2026 to 20 Sep 2026). You would have 200 days left.", "big delete, plenty left");
eq(undoNeedsAcknowledgement(big), true, "30 or more days is warned even with plenty left");
eq(undoNeedsAcknowledgement({ ...big, days_deleted: 29 }), false, "29 days is not");
eq(undoNeedsAcknowledgement({ ...big, days_deleted: 30 }), true, "30 days is");

// a small delete that leaves too few: warned
const small = { ...base, days_deleted: 3, deleted_first_date: "2026-11-01", deleted_last_date: "2026-11-03", days_remaining: 20 };
eq(undoNeedsAcknowledgement(small), true, "under 30 left");
eq(undoSummarySentence(small), "This will permanently remove 3 days of history (1 Nov 2026 to 3 Nov 2026). You would have 20 days left. The forecast needs at least 30 days.", "small delete, under 30 left");
eq(undoNeedsAcknowledgement({ ...small, days_remaining: 30 }), false, "exactly 30 left is enough");

// normal: simple confirm, no box
const normal = { ...base, days_deleted: 3, deleted_first_date: "2026-11-01", deleted_last_date: "2026-11-03", days_remaining: 140 };
eq(undoNeedsAcknowledgement(normal), false, "a normal undo needs no box");
eq(undoSummarySentence(normal), "This will permanently remove 3 days of history (1 Nov 2026 to 3 Nov 2026). You would have 140 days left.", "normal");
eq(undoSummarySentence({ ...normal, days_deleted: 1, deleted_first_date: "2026-11-01", deleted_last_date: "2026-11-01" }).startsWith("This will permanently remove 1 day of history (1 Nov 2026)."), true, "singular, one date");

// nothing deleted: not warned, even when the facility already has few days (nothing is being taken away)
const restoreOnly = { ...base, days_deleted: 0, days_restored: 2, deleted_first_date: null, deleted_last_date: null, first_date: "2026-11-02", last_date: "2026-11-03", days_remaining: 10 };
eq(undoNeedsAcknowledgement(restoreOnly), false, "restore only");
eq(undoSummarySentence(restoreOnly), "No days will be removed. 2 days will go back to an earlier value (2 Nov 2026 to 3 Nov 2026). You would have 10 days left.", "restore only wording");
eq(undoSummarySentence({ ...restoreOnly, days_restored: 0 }), "No days of history will change. You would have 10 days left.", "nothing changes");
eq(undoSummarySentence({ ...base, days_restored: 3, days_remaining: 200 }).includes("3 days will stay, with an earlier value."), true, "deleted and kept");
eq(UNDO_ACK_LABEL, "I understand this removes this history", "the tick-box words");

// the screen
const panel = readFileSync(new URL("../src/app/components/UploadHistoryPanel.tsx", import.meta.url), "utf8");
for (const t of ['type="checkbox"', 'htmlFor="undo-understood"', "UNDO_ACK_LABEL", "undoSummarySentence(preview.summary)"]) if (!panel.includes(t)) fail(`${t} missing from the undo confirmation`);
if (!/disabled=\{applying \|\| \(preview\.summary != null && undoNeedsAcknowledgement\(preview\.summary\) && !understood\)\}/.test(panel)) fail("the confirm button must stay disabled until the box is ticked when the warning applies");
if (!/preview\.summary && \(/.test(panel)) fail("the summary is shown only when the server sends one (other upload types have none)");
const server = readFileSync(new URL("../server/main.py", import.meta.url), "utf8");
if (!/"forecast_min_days": SARIMAX_MIN_DAYS_REQUIRED/.test(server)) fail("the 30-day figure must come from the server's constant");

// the red Confirm buttons: white on the light red of the dark theme is 2.2:1, so every one carries the dark text colour too
import { readdirSync } from "node:fs";
const walk = (dir: URL): URL[] => readdirSync(dir, { withFileTypes: true }).flatMap((e) =>
  e.isDirectory() ? walk(new URL(`${e.name}/`, dir)) : e.name.endsWith(".tsx") ? [new URL(e.name, dir)] : []);
for (const f of walk(new URL("../src/app/", import.meta.url))) {
  for (const line of readFileSync(f, "utf8").split("\n")) {
    if (line.includes("bg-status-critical-text text-white") && !line.includes("dark:text-background")) {
      fail(`${f.pathname.split("/src/")[1]}: a red button with white text and no dark:text-background: ${line.trim().slice(0, 90)}`);
    }
  }
}

console.log("ok");
