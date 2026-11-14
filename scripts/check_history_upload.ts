// Asserts the historical stock upload's preview wording (src/app/lib/historyUpload.ts) and the screen's wiring. Run: node scripts/check_history_upload.ts
import { readFileSync } from "node:fs";
import type { HistoryPreview } from "../src/app/lib/api.ts";
import {
  SUPPORTED_FILES_LINE, countsSentence, dateChoices, dateSentence, layoutSentence, moreProblemsLine, problemLine, repeatedSentence, saveState,
  savedSentence, sheetSentence,
} from "../src/app/lib/historyUpload.ts";

const eq = (a: unknown, b: unknown, m: string) => { if (JSON.stringify(a) !== JSON.stringify(b)) throw new Error(`${m}: ${JSON.stringify(a)} !== ${JSON.stringify(b)}`); };
const fail = (m: string) => { throw new Error(m); };

const base: HistoryPreview = {
  file_type: "csv", sheet: null, other_sheets: [], layout: "long", mapping: { date: "Date", blood_type: "Group", units: "Qty" }, header_row: 1,
  date_format: "year-month-day, like 2026-11-10", date_order: null, ambiguous_dates: false, ambiguous_example: null,
  row_counts: { total: 1210, usable: 1204, skipped: 6 }, repeated: 0, sample_rows: [], problems: [], more_problems: 0, fatal: null,
};

eq(SUPPORTED_FILES_LINE, ".csv or .xlsx, one date per row.", "the supported-files line");
eq(countsSentence(base), "1,204 rows will be saved, 6 skipped.", "counts");
eq(countsSentence({ ...base, row_counts: { total: 1, usable: 1, skipped: 0 } }), "1 row will be saved.", "one row, none skipped");
eq(layoutSentence(base), "Each row has a date, a blood type and a number of units. We used your columns “Date”, “Group” and “Qty”.", "long layout");
eq(layoutSentence({ ...base, layout: "wide", mapping: { date: "As of", blood_types: { "O pos": "O+", "A+": "A+" } } }),
  "Each row is one date, with a column for each blood type (O+, A+). Each date and blood type becomes one row. We used your column “As of” for the dates.", "wide layout");
eq(dateSentence(base), "Dates in your file: year-month-day, like 2026-11-10.", "date format");
eq(dateSentence({ ...base, date_format: null }), null, "no dates read");
eq(sheetSentence(base), null, "a csv has no sheet");
eq(sheetSentence({ ...base, file_type: "xlsx", sheet: "Stock" }), "We read the sheet “Stock”.", "one sheet");
eq(sheetSentence({ ...base, file_type: "xlsx", sheet: "Stock", other_sheets: ["2024", "Notes"] }),
  "This workbook has 2 other sheets with data (2024, Notes). We only read the first sheet, “Stock”.", "other sheets named");
eq(repeatedSentence(base), null, "no repeats");
eq(repeatedSentence({ ...base, repeated: 1 })?.startsWith("1 row repeats"), true, "one repeat");
eq(problemLine({ row: 5, reason: "units -2 is negative; it must be 0 or more" }), "Row 5: Units -2 is negative; it must be 0 or more", "a problem line");
eq(problemLine({ row: 0, reason: "The file is empty." }), "The file is empty.", "a whole-file problem has no row");
eq(moreProblemsLine({ ...base, more_problems: 1234 }), "and 1,234 more", "more problems");
eq(moreProblemsLine(base), null, "no more problems");
eq(savedSentence(1204, 6), "1,204 rows saved, 6 skipped.", "saved");

// Save is offered only when there is something to save and, if dates could be read two ways, a choice has been made
eq(saveState(base, false), { canSave: true, why: null }, "ready");
eq(saveState({ ...base, ambiguous_dates: true }, true).canSave, false, "no save until a choice is made");
eq(saveState({ ...base, row_counts: { total: 3, usable: 0, skipped: 3 } }, false).canSave, false, "nothing to save");
eq(saveState({ ...base, fatal: "The file is empty." }, false), { canSave: false, why: null }, "unusable file");
const choices = dateChoices({ text: "03/04/2026", day_first: "3 April 2026", month_first: "4 March 2026" });
eq(choices.map((c) => c.value), ["dmy", "mdy"], "the two answers, in the server's words");
eq(choices[0].detail.includes("03/04/2026 means 3 April 2026"), true, "day first uses the file's own example");
eq(choices[1].detail.includes("03/04/2026 means 4 March 2026"), true, "month first uses the file's own example");

// words a user reads never use the technical ones
const words = [layoutSentence(base), dateSentence(base) ?? "", countsSentence(base), SUPPORTED_FILES_LINE, ...choices.map((c) => c.label + c.detail)].join(" ");
for (const w of ["parse", "schema", "mapping", "delimiter", "header"]) if (new RegExp(w, "i").test(words)) fail(`"${w}" in the preview wording`);

// the screen: nothing is saved by choosing a file; the save is its own button; the undo list stays under it
const screen = readFileSync(new URL("../src/app/components/HistoryUpload.tsx", import.meta.url), "utf8");
const dash = readFileSync(new URL("../src/app/screens/BankDashboard.tsx", import.meta.url), "utf8");
const onFile = screen.match(/function onFile[\s\S]*?\n  }\n/)?.[0] ?? fail("onFile not found");
if (/uploadHistoricalInventorySnapshots/.test(onFile)) fail("choosing a file must only preview, never save");
if (!/previewHistoricalUpload/.test(onFile + screen.match(/async function read[\s\S]*?\n  }\n/)?.[0])) fail("choosing a file must preview it");
for (const t of ["Save to my history", "Choose a different file", "We read your file like this", 'accept=".csv,.xlsx"', 'type="radio"', "<fieldset", "<legend"]) {
  if (!screen.includes(t)) fail(`${t} missing from the upload screen`);
}
if (!/disabled=\{!state\?\.canSave \|\| busy !== null\}/.test(screen)) fail("Save must be disabled until the preview says it can be saved");
if (!/<HistoryUpload [^>]*onSaved=/.test(dash) || dash.indexOf("<HistoryUpload") > dash.indexOf("<UploadHistoryPanel")) fail("the upload sits above the undo list on the Dashboard");
if (/<HistoryUpload/.test(dash) === false || /snapshot_date, blood_type and units/.test(dash)) fail("the old technical help text is gone");

console.log("ok");
