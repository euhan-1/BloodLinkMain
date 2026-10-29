// Asserts the trend chart's data builder in trendChart.ts. Run: node scripts/check_trend_chart.ts
import { readFileSync } from "node:fs";
import { buildTrendChart, dateAtOffset, shortDate, tickLabel, MIN_OBSERVED_FOR_LINES, type HistoryPoint, type SeriesPoint } from "../src/app/lib/trendChart.ts";

const eq = (a: unknown, b: unknown, m: string) => { if (JSON.stringify(a) !== JSON.stringify(b)) throw new Error(`${m}: ${JSON.stringify(a)} !== ${JSON.stringify(b)}`); };

const TODAY = "2026-10-29";
const iso = (t: number) => dateAtOffset(TODAY, t);
const CHECKPOINTS = [0, 5, 10, 15, 20, 25, 30];
const series = (total = 400): SeriesPoint[] => CHECKPOINTS.map((c) => ({
  day: c === 0 ? "Today" : `Day ${c}`, date: iso(c), units: total + c, lower: c === 0 ? total : total + c - 10, upper: c === 0 ? total : total + c + 10,
}));
// history over t = from..0; `recorded(t)` says which days somebody recorded; a hole carries the last recorded value
const history = (from: number, recorded: (t: number) => boolean): HistoryPoint[] => {
  const out: HistoryPoint[] = [];
  let last = 0;
  for (let t = from; t <= 0; t++) {
    if (recorded(t)) last = 300 + t;
    out.push({ date: iso(t), units: last, observed: recorded(t) });
  }
  return out;
};

// ---- Northside-like: 90 days, October 1-25 not recorded (t = -28..-4), the rest recorded
const northside = history(-89, (t) => !(t >= -28 && t <= -4));
const n = buildTrendChart({ history: northside, series: series() });
eq(n.mode, "lines", "northside mode");
eq(n.observedCount, 65, "northside observed count");
eq(n.filledRuns, [{ from: -28.5, to: -3.5 }], "the plateau is one shaded region, padded half a day");
eq(n.minimumSegment, [0, 30], "minimum line spans the forecast only");
eq(n.domain, [-89, 30], "domain");
eq(n.todayISO, TODAY, "today");
eq(n.historyTone, "accent", "real forecast tone");
const at = (t: number) => n.points.find((p) => p.t === t)!;
// one point per day, then the forecast checkpoints (0 is shared, not duplicated)
eq(n.points.length, 90 + 6, "point count");
eq(new Set(n.points.map((p) => p.t)).size, n.points.length, "no duplicate t");
eq(n.points.map((p) => p.t), [...n.points.map((p) => p.t)].sort((a, b) => a - b), "sorted");
// solid where recorded, dashed where not, and the dashed line touches the recorded days either side so the two join
eq([at(-29).observedUnits, at(-29).filledUnits], [271, 271], "last recorded day before the hole is on both lines");
eq([at(-28).observedUnits, at(-28).filledUnits, at(-28).recorded], [null, 271, false], "hole day: dashed only");
eq([at(-4).observedUnits, at(-4).filledUnits], [null, 271], "last hole day carries the same value");
eq([at(-3).observedUnits, at(-3).filledUnits], [297, 297], "first recorded day after the hole is on both lines");
eq([at(-2).observedUnits, at(-2).filledUnits], [298, null], "recorded day away from the hole is solid only");
eq(at(-50).carriedFrom, null, "recorded day has no carriedFrom");
eq(at(-50).recorded, true, "recorded");
eq(at(-28).carriedFrom, iso(-29), "a carried day names the recorded day its value comes from");
eq(at(-4).carriedFrom, iso(-29), "...for every day of the run");
// day 0 joins: one point that is both the last recorded day and the start of the forecast, zero-width band
eq([at(0).observedUnits, at(0).forecastUnits, at(0).lower, at(0).band, at(0).label], [300, 400, 400, 0, "Today"], "day 0 is shared");
eq([at(5).observedUnits, at(5).filledUnits, at(5).forecastUnits, at(5).band, at(5).recorded], [null, null, 405, 20, null], "forecast point");
// the forward series is not interpolated: only the checkpoints exist after today
eq(n.points.filter((p) => p.t > 0).map((p) => p.t), [5, 10, 15, 20, 25, 30], "no interpolation");
eq(n.points.filter((p) => p.t < 0).every((p) => p.forecastUnits === null && p.band === null && p.lower === null), true, "history has no forecast fields");

// ---- two separate holes -> two regions
const two = buildTrendChart({ history: history(-20, (t) => !(t >= -15 && t <= -12) && !(t >= -5 && t <= -4)), series: series() });
eq(two.filledRuns, [{ from: -15.5, to: -11.5 }, { from: -5.5, to: -3.5 }], "two holes");
// a hole running up to today (the nightly job missed) still closes its region
const upTo = buildTrendChart({ history: history(-20, (t) => t < -3), series: series() });
eq(upTo.filledRuns, [{ from: -3.5, to: 0.5 }], "hole to today");

// ---- a facility with almost no history (2, 3, 4, 6): dots only, nothing is drawn through the empty days
const sparse = history(-89, (t) => t === -60 || t === -33 || t === -1 || t === 0);
const s = buildTrendChart({ history: sparse, series: series() });
eq(s.mode, "dots", "sparse mode");
eq(s.observedCount, 4, "sparse observed count");
eq(s.filledRuns, [], "no shaded span");
eq(s.points.filter((p) => p.t < 0).map((p) => p.t), [-60, -33, -1], "only the recorded days are points");
eq(s.points.every((p) => p.filledUnits === null), true, "no dashed values");
eq(s.domain, [-60, 30], "domain starts at the first recorded day");
// the 6 vs 7 boundary
eq(MIN_OBSERVED_FOR_LINES, 7, "threshold");
eq(buildTrendChart({ history: history(-6, () => true), series: series() }).mode, "lines", "7 recorded days");
eq(buildTrendChart({ history: history(-5, () => true), series: series() }).mode, "dots", "6 recorded days");
// dots mode never invents a span even when most of the 90 days are carried forward
eq(buildTrendChart({ history: history(-89, (t) => t > -4), series: series() }).filledRuns, [], "4 recorded of 90: no span");

// ---- forecast still pending: history only, no forecast, no minimum line
const pending = buildTrendChart({ history: northside, series: [] });
eq([pending.hasForecast, pending.minimumSegment, pending.domain], [false, null, [-89, 0]], "pending");
eq(pending.points.every((p) => p.forecastUnits === null && p.band === null), true, "pending has no forecast values");
eq(pending.filledRuns, [{ from: -28.5, to: -3.5 }], "pending still shows the plateau");
eq(pending.ticks.includes(30), false, "pending has no forward ticks");

// ---- synthetic stand-in: same shape, but history is toned neutral so it never reads as part of the violet stand-in
eq(buildTrendChart({ history: northside, series: series(), standIn: true }).historyTone, "neutral", "stand-in tone");

// ---- an older API response: no dates on the series, no history
const old = buildTrendChart({ series: series().map(({ date: _d, ...rest }) => rest) });
eq(old.points.map((p) => p.t), CHECKPOINTS, "labels place the points when dates are absent");
eq([old.mode, old.filledRuns, old.todayISO], ["dots", [], null], "no history");
eq(old.minimumSegment, [0, 30], "forecast-only chart keeps its minimum line");
// series dates win over labels when both exist
eq(buildTrendChart({ history: [{ date: TODAY, units: 5, observed: true }], series: [{ day: "Day 99", date: iso(5), units: 1, lower: null, upper: null }] }).points.map((p) => p.t), [0, 5], "dates win");
// a series point with no interval has no band
eq(buildTrendChart({ series: [{ day: "Today", units: 1, lower: null, upper: null }] }).points[0].band, null, "no interval, no band");

// ---- nothing at all
const empty = buildTrendChart({});
eq([empty.points, empty.mode, empty.domain, empty.ticks, empty.minimumSegment], [[], "dots", [0, 0], [0], null], "empty");

// ---- ticks stay inside the domain, include today, and are sorted and unique
for (const c of [n, s, two, upTo, pending, old, empty]) {
  eq(c.ticks.every((t) => t >= c.domain[0] && t <= c.domain[1]), true, "ticks inside domain");
  eq(c.ticks, [...new Set(c.ticks)].sort((a, b) => a - b), "ticks sorted and unique");
}
eq(n.ticks, [-89, -60, -30, 0, 15, 30], "northside ticks");

// ---- labels
eq([tickLabel(0, TODAY), tickLabel(15, TODAY), tickLabel(-28, TODAY), tickLabel(-4, null)], ["Today", "15", "Oct 1", "-4"], "tick labels");
eq([shortDate("2026-10-04"), shortDate("2026-01-31"), shortDate(dateAtOffset("2026-10-29", -29))], ["Oct 4", "Jan 31", "Sep 30"], "short dates");
eq(dateAtOffset("2026-12-31", 1), "2027-01-01", "year rollover");

// ---- no accuracy wording in the module's own text (identifiers and comments included)
const src = readFileSync(new URL("../src/app/lib/trendChart.ts", import.meta.url), "utf8");
const words = /(better|best|worse|optimi[sz]|improv|accura|reliab|quality|good|great|superior|precise|robust)/i;
const hit = src.match(words);
if (hit) throw new Error(`accuracy wording in trendChart.ts: ${hit[0]}`);
console.log("ok");
