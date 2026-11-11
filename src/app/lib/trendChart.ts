// Turns GET /forecast's `history` (recorded totals, oldest first, ending today) and `series` (the forward
// checkpoints) into the points the Dashboard's trend chart draws on ONE date axis. Pure, so it is checked by
// scripts/check_trend_chart.ts. The chart's wording says what was recorded and what was projected; nothing here
// says anything else about the numbers.

export type HistoryPoint = { date: string; units: number; observed: boolean };
export type SeriesPoint = { day: string; date?: string; units: number; lower: number | null; upper: number | null };

// Fewer recorded days than this and the history is drawn as dots only: a dashed span from a handful of points
// would be mostly invention.
export const MIN_OBSERVED_FOR_LINES = 7;

export type TrendPoint = {
  t: number; // days from today: negative = history, 0 = today, positive = forecast
  date: string | null; // ISO; null only for an old API response whose series has no dates
  label: string | null; // "Today" / "Day 5", forward points only
  units: number;
  recorded: boolean | null; // history points; null for forward-only points
  carriedFrom: string | null; // for a not-recorded day: the last recorded date its value comes from
  observedUnits: number | null; // solid line / dots
  filledUnits: number | null; // dashed line: not-recorded days plus the recorded days bounding each run
  forecastUnits: number | null;
  lower: number | null;
  band: number | null; // upper - lower, for the stacked-area band
};

export type TrendChart = {
  points: TrendPoint[];
  mode: "lines" | "dots";
  observedCount: number;
  filledRuns: { from: number; to: number }[]; // shaded regions, in t, padded half a day each side
  hasForecast: boolean;
  minimumSegment: [number, number] | null; // the combined minimum is drawn over the forecast only
  domain: [number, number];
  ticks: number[];
  todayISO: string | null;
  historyTone: "accent" | "neutral"; // neutral keeps real history visually apart from a synthetic stand-in
};

const parseISO = (iso: string): [number, number, number] => {
  const [y, m, d] = iso.split("-").map(Number);
  return [y, m, d];
};

export function daysBetween(fromISO: string, toISO: string): number {
  const [fy, fm, fd] = parseISO(fromISO);
  const [ty, tm, td] = parseISO(toISO);
  return Math.round((Date.UTC(ty, tm - 1, td) - Date.UTC(fy, fm - 1, fd)) / 86400000);
}

export function dateAtOffset(todayISO: string, t: number): string {
  const [y, m, d] = parseISO(todayISO);
  return new Date(Date.UTC(y, m - 1, d + t)).toISOString().slice(0, 10);
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

// "2026-10-04" -> "Oct 4", the same calendar-date reading as formatBreachDate (no UTC-vs-local shift).
export function shortDate(iso: string): string {
  const [, m, d] = parseISO(iso);
  return `${MONTHS[m - 1]} ${d}`;
}

// x-axis tick: a calendar date before today, "Today", then days ahead (the labels the forecast chart always had).
export function tickLabel(t: number, todayISO: string | null): string {
  if (t === 0) return "Today";
  if (t > 0) return String(t);
  return todayISO ? shortDate(dateAtOffset(todayISO, t)) : String(t);
}

// An old API response has no `date` on series points; "Today" / "Day N" still says where they sit.
const offsetFromLabel = (day: string): number => (day === "Today" ? 0 : Number(day.replace("Day ", "")));

export function buildTrendChart(input: { history?: HistoryPoint[]; series?: SeriesPoint[]; standIn?: boolean }): TrendChart {
  const history = input.history ?? [];
  const series = input.series ?? [];
  const todayISO = history.length ? history[history.length - 1].date : series[0]?.date ?? null;
  const observedCount = history.filter((h) => h.observed).length;
  const mode: TrendChart["mode"] = observedCount >= MIN_OBSERVED_FOR_LINES ? "lines" : "dots";

  const byT = new Map<number, TrendPoint>();
  const blank = (t: number): TrendPoint => ({
    t, date: null, label: null, units: 0, recorded: null, carriedFrom: null,
    observedUnits: null, filledUnits: null, forecastUnits: null, lower: null, band: null,
  });

  const filledRuns: TrendChart["filledRuns"] = [];
  let lastRecorded: string | null = null;
  let run: { from: number; to: number } | null = null;
  history.forEach((h, i) => {
    const t = todayISO ? daysBetween(h.date, todayISO) * -1 : 0;
    if (h.observed) {
      lastRecorded = h.date;
      if (run) { filledRuns.push({ from: run.from - 0.5, to: run.to + 0.5 }); run = null; }
    } else if (mode === "lines") {
      run = run ? { from: run.from, to: t } : { from: t, to: t };
    }
    if (mode === "dots" && !h.observed) return; // dots only: recorded days and nothing else
    const p = byT.get(t) ?? blank(t);
    Object.assign(p, {
      date: h.date, units: h.units, recorded: h.observed, carriedFrom: h.observed ? null : lastRecorded,
      observedUnits: h.observed ? h.units : null,
    });
    // the dashed line also touches the recorded days either side of a not-recorded run, so it joins the solid one
    const touchesFilled = mode === "lines" && (!h.observed || history[i - 1]?.observed === false || history[i + 1]?.observed === false);
    if (touchesFilled) p.filledUnits = h.units;
    byT.set(t, p);
  });
  if (run) filledRuns.push({ from: (run as { from: number; to: number }).from - 0.5, to: (run as { from: number; to: number }).to + 0.5 });

  for (const sp of series) {
    const t = sp.date && todayISO ? daysBetween(todayISO, sp.date) : offsetFromLabel(sp.day);
    const p = byT.get(t) ?? blank(t);
    Object.assign(p, {
      date: sp.date ?? p.date, label: sp.day, forecastUnits: sp.units,
      lower: sp.lower, band: sp.lower !== null && sp.upper !== null ? sp.upper - sp.lower : null,
    });
    if (p.recorded === null) p.units = sp.units;
    byT.set(t, p);
  }

  const points = [...byT.values()].sort((a, b) => a.t - b.t);
  const ts = points.map((p) => p.t);
  const minT = ts.length ? Math.min(...ts) : 0;
  const maxT = ts.length ? Math.max(...ts) : 0;
  const hasForecast = series.length > 0;

  const ticks = new Set<number>(hasForecast ? [0, 15, 30].filter((t) => t <= maxT) : [0]);
  for (let t = -30; t >= minT; t -= 30) ticks.add(t);
  if (minT < 0 && [...ticks].every((t) => Math.abs(t - minT) >= 12)) ticks.add(minT);

  return {
    points, mode, observedCount, filledRuns, hasForecast,
    minimumSegment: hasForecast ? [0, maxT] : null,
    domain: [minT, maxT], ticks: [...ticks].sort((a, b) => a - b), todayISO,
    historyTone: input.standIn ? "neutral" : "accent",
  };
}


// ── A text version of the chart, for screen readers (the chart itself is a drawing). It says only what the points already hold: the
// latest recorded total and its range, the projection, and whether the projection stays at or above the combined minimum. Pure, so
// scripts/check_trend_chart.ts checks it.
const plural = (n: number, one: string, many: string) => (n === 1 ? one : many);

export function describeTrendChart(
  chart: TrendChart,
  opts: { totalMinimum: number; standIn?: boolean; hasInterval?: boolean },
): string {
  const where = (p: TrendPoint) => (p.date ? shortDate(p.date) : p.t === 0 ? "today" : `day ${p.t}`);
  const parts: string[] = ["Total stock across every blood type, recorded and projected."];

  const recorded = chart.points.filter((p) => p.observedUnits !== null);
  if (recorded.length === 0) {
    parts.push("No recorded days yet.");
  } else {
    const values = recorded.map((p) => p.observedUnits as number);
    const last = recorded[recorded.length - 1];
    parts.push(
      `Recorded on ${recorded.length} ${plural(recorded.length, "day", "days")}` +
        (recorded.length > 1 ? `, ${where(recorded[0])} to ${where(last)}` : `, ${where(last)}`) +
        `: latest ${last.observedUnits} ${plural(last.observedUnits as number, "unit", "units")}` +
        (recorded.length > 1 ? `, lowest ${Math.min(...values)}, highest ${Math.max(...values)}.` : "."),
    );
    if (chart.mode === "dots") parts.push("Too few days to draw a line, so only the recorded days are shown.");
    if (chart.filledRuns.length > 0) parts.push("Days nobody recorded are shaded and carry the last recorded value.");
  }

  if (!chart.hasForecast) {
    parts.push("The projection is still being calculated.");
    return parts.join(" ");
  }

  const ahead = chart.points.filter((p) => p.forecastUnits !== null && p.t > 0);
  if (ahead.length > 0) {
    parts.push(
      "Projected total: " +
        ahead.map((p) => `${p.forecastUnits} ${plural(p.forecastUnits as number, "unit", "units")} by ${where(p)}`).join(", ") +
        ".",
    );
    const end = ahead[ahead.length - 1];
    if (opts.hasInterval && end.lower !== null && end.band !== null) {
      parts.push(`The shaded range for ${where(end)} runs from ${end.lower} to ${end.lower + end.band}.`);
    }
  }
  if (opts.totalMinimum > 0) {
    const breach = ahead.find((p) => (p.forecastUnits as number) < opts.totalMinimum);
    parts.push(
      breach
        ? `Status: projected to fall below the combined minimum of ${opts.totalMinimum} units by ${where(breach)}.`
        : `Status: stays at or above the combined minimum of ${opts.totalMinimum} units throughout the projection.`,
    );
  }
  if (opts.standIn) parts.push("The projection comes from an example pattern, not this facility's own records.");
  return parts.join(" ");
}
