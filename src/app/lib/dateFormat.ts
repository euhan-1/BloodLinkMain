// How dates are written in records: "30 Oct 2026" and "30 Oct 2026, 20:27". Written out here, not left to the browser's
// locale, so the transfer history and the receipt (and anything else that uses these) always agree. The browser only supplies
// the numbers for a given time zone (Intl's formatToParts with a fixed locale and the 24-hour clock); the month's name is
// ours, so there is no "Sept", no AM/PM and no day-month-year reordering depending on whose browser it is.

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"] as const;

type Parts = { day: number; month: number; year: number; hour: number; minute: number };

function parts(at: string | Date, timeZone?: string): Parts {
  const f = new Intl.DateTimeFormat("en-US", {
    timeZone, year: "numeric", month: "numeric", day: "numeric", hour: "numeric", minute: "numeric", hourCycle: "h23", numberingSystem: "latn",
  });
  const get = (type: Intl.DateTimeFormatPartTypes) => Number(f.formatToParts(new Date(at)).find((p) => p.type === type)?.value);
  return { day: get("day"), month: get("month"), year: get("year"), hour: get("hour"), minute: get("minute") };
}

// "30 Oct 2026". `timeZone` is an IANA name; without one it is the viewer's own.
export function formatDate(at: string | Date, timeZone?: string): string {
  const p = parts(at, timeZone);
  return `${p.day} ${MONTHS[p.month - 1]} ${p.year}`;
}

// "2026-10-30": which calendar day an instant falls on, in `timeZone` (default the viewer's own). For comparing days, not display.
export function dayKey(at: string | Date, timeZone?: string): string {
  const p = parts(at, timeZone);
  return `${p.year}-${String(p.month).padStart(2, "0")}-${String(p.day).padStart(2, "0")}`;
}

// "20:27", the same 24-hour clock, for places that already say which day (a chat message under its day's events).
export function formatTime(at: string | Date, timeZone?: string): string {
  const p = parts(at, timeZone);
  return `${String(p.hour).padStart(2, "0")}:${String(p.minute).padStart(2, "0")}`;
}

// "30 Oct 2026, 20:27".
export function formatDateTime(at: string | Date, timeZone?: string): string {
  const p = parts(at, timeZone);
  return `${p.day} ${MONTHS[p.month - 1]} ${p.year}, ${String(p.hour).padStart(2, "0")}:${String(p.minute).padStart(2, "0")}`;
}
