// The undo confirmation's words for a historical upload (GET /upload-history/{id}/undo-preview -> `summary`). Pure, so
// scripts/check_undo_upload.ts can test them under Node. The numbers come from the server; the wording and the warning rule live here.
import type { UndoSummary } from "./api.ts";
import { formatCalendarDate } from "./dateFormat.ts";

export const UNDO_ACK_LABEL = "I understand this removes this history";

const num = (n: number) => n.toLocaleString("en-US");
const days = (n: number) => `${num(n)} ${n === 1 ? "day" : "days"}`;

function range(first: string | null, last: string | null): string {
  if (!first || !last) return "";
  return first === last ? formatCalendarDate(first) : `${formatCalendarDate(first)} to ${formatCalendarDate(last)}`;
}

/** Many days leave, or too few would be left for a forecast: the user must tick a box first. A facility that already has too few
 *  days and loses none is not warned: nothing is being taken away. */
export function undoNeedsAcknowledgement(s: UndoSummary): boolean {
  return s.days_deleted >= 30 || (s.days_deleted > 0 && s.days_remaining < s.forecast_min_days);
}

/** "This will permanently remove 150 days of history (24 Apr 2026 to 20 Sep 2026). You would have 24 days left. The forecast needs at least 30 days." */
export function undoSummarySentence(s: UndoSummary): string {
  const parts: string[] = [];
  if (s.days_deleted > 0) {
    parts.push(`This will permanently remove ${days(s.days_deleted)} of history (${range(s.deleted_first_date, s.deleted_last_date)}).`);
    if (s.days_restored > 0) parts.push(`${days(s.days_restored)} will stay, with an earlier value.`);
  } else if (s.days_restored > 0) {
    parts.push(`No days will be removed. ${days(s.days_restored)} will go back to an earlier value (${range(s.first_date, s.last_date)}).`);
  } else {
    parts.push("No days of history will change.");
  }
  parts.push(`You would have ${days(s.days_remaining)} left.`);
  if (s.days_deleted > 0 && s.days_remaining < s.forecast_min_days) parts.push(`The forecast needs at least ${s.forecast_min_days} days.`);
  return parts.join(" ");
}
