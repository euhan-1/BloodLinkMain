// The words of the historical stock upload's preview step. Pure, so scripts/check_history_upload.ts can test them under Node.
// Plain language: no "parse", "schema" or "mapping" anywhere a user reads.
import type { HistoryPreview } from "./api.ts";
import { formatCalendarDate } from "./dateFormat.ts";

export const SUPPORTED_FILES_LINE = ".csv or .xlsx, one date per row.";

const num = (n: number) => n.toLocaleString("en-US");
const plural = (n: number, one: string, many: string) => (n === 1 ? one : many);

/** Which columns were used, in words. */
export function layoutSentence(p: HistoryPreview): string {
  const m = p.mapping;
  if (p.layout === "long") {
    return `Each row has a date, a blood type and a number of units. We used your columns “${m.date}”, “${m.blood_type}” and “${m.units}”.`;
  }
  if (p.layout === "wide") {
    const types = Object.values(m.blood_types ?? {}).join(", ");
    return `Each row is one date, with a column for each blood type (${types}). Each date and blood type becomes one row. We used your column “${m.date}” for the dates.`;
  }
  return "";
}

export function dateSentence(p: HistoryPreview): string | null {
  return p.date_format ? `Dates in your file: ${p.date_format}.` : null;
}

export function sheetSentence(p: HistoryPreview): string | null {
  if (p.file_type !== "xlsx" || !p.sheet) return null;
  const others = p.other_sheets.length;
  return others > 0
    ? `This workbook has ${others} other ${plural(others, "sheet", "sheets")} with data (${p.other_sheets.join(", ")}). We only read the first sheet, “${p.sheet}”.`
    : `We read the sheet “${p.sheet}”.`;
}

/** "1,204 rows will be saved, 6 skipped." */
export function countsSentence(p: HistoryPreview): string {
  const { usable, skipped } = p.row_counts;
  return `${num(usable)} ${plural(usable, "row", "rows")} will be saved${skipped > 0 ? `, ${num(skipped)} skipped` : ""}.`;
}

export function repeatedSentence(p: HistoryPreview): string | null {
  return p.repeated > 0
    ? `${num(p.repeated)} ${plural(p.repeated, "row repeats", "rows repeat")} a date and blood type that appears again later in the file; only the last one is kept.`
    : null;
}

export function problemLine(e: { row: number | string; reason: string }): string {
  const reason = e.reason.charAt(0).toUpperCase() + e.reason.slice(1);
  return Number(e.row) > 0 ? `Row ${e.row}: ${reason}` : reason;
}

export function moreProblemsLine(p: HistoryPreview): string | null {
  return p.more_problems > 0 ? `and ${num(p.more_problems)} more` : null;
}

/** A sample row's date, written out ("10 Nov 2026"). */
export const sampleDate = formatCalendarDate;

/** Whether Save is offered, and if not, the one reason shown beside it. */
export function saveState(p: HistoryPreview, needsChoice: boolean): { canSave: boolean; why: string | null } {
  if (p.fatal) return { canSave: false, why: null }; // the problem is already on screen
  if (needsChoice) return { canSave: false, why: "Choose how the dates are written to continue." };
  if (p.row_counts.usable === 0) return { canSave: false, why: "Nothing in this file can be saved." };
  return { canSave: true, why: null };
}

export function savedSentence(rowsSaved: number, skipped: number): string {
  return `${num(rowsSaved)} ${plural(rowsSaved, "row", "rows")} saved${skipped > 0 ? `, ${num(skipped)} skipped` : ""}.`;
}

/** The two answers when a date such as 03/04/2026 could be read either way, with the file's own example. */
export function dateChoices(example: { text: string; day_first: string; month_first: string } | null) {
  const eg = (reading: string) => (example ? ` For example, ${example.text} means ${reading}.` : "");
  return [
    { value: "dmy" as const, label: "Day first", detail: `The day comes before the month.${eg(example?.day_first ?? "")}` },
    { value: "mdy" as const, label: "Month first", detail: `The month comes before the day.${eg(example?.month_first ?? "")}` },
  ];
}
