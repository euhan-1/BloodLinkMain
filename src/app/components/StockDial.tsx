import { BloodTypeBadge } from "./BloodTypeBadge";
import { STATUS_STYLES, STOCK_LABELS, isNotStocked, stockStatus, type StatusLevel } from "../lib/statusTokens";

// A speedometer-style gauge for one blood type's stock: a 270-degree arc (gap at the bottom) filled from 0 to the units held,
// drawn against the type's own maximum, with a tick where the minimum sits. The colour is stockStatus, the one rule used
// everywhere stock is judged. StockBar is the linear version of the same reading.

// theme.css's --status-* tokens, so the fill follows light and dark.
const FILL: Record<StatusLevel, string> = {
  safe: "var(--status-safe)",
  watch: "var(--status-watch)",
  critical: "var(--status-critical)",
};

// Geometry, in a 100 x 100 viewBox. A circle's stroke starts at 3 o'clock and runs clockwise; rotating it 135deg puts the start
// at the bottom left, so a dash of 270/360 of the circumference ends at the bottom right and leaves the gap underneath.
const CENTRE = 50;
const RADIUS = 40;
const STROKE = 10;
const START_DEG = 135;
const SWEEP_DEG = 270;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;
const ARC = CIRCUMFERENCE * (SWEEP_DEG / 360);
const TICK_REACH = STROKE / 2 + 3; // past each edge of the arc, so the tick reads on top of the fill and the track

// The clay look (theme.css, the [data-surface="clay"] block) molds the arc into a tube and the track into a groove with thin
// highlight and shade strokes laid inside the original 10-unit band, each the same dash as the stroke it decorates and shifted
// toward or away from the light (top left). They are drawn as hidden groups: display="none" is a presentation attribute, so
// nothing draws unless that CSS rule sets display back, and with the switch off (or under print, prefers-contrast: more or
// forced-colors, where the clay block does not apply) the render is exactly what it was. The colours are derived in CSS from the
// group's `color`, which is the same token the arc or the track already uses, so a status never changes colour.
const CLAY_OFFSET = 1.5; // how far a highlight or shade is shifted along the light direction, in viewBox units (about 2.1 diagonally)
const clayLayer = (kind: "hi" | "lo" | "groove" | "lip", color: string, dash: string, width: number, dir: 1 | -1, base: Record<string, unknown>) => (
  <g key={kind} className={`dial-clay dial-clay-${kind}`} display="none" color={color} transform={`translate(${dir * CLAY_OFFSET} ${dir * CLAY_OFFSET})`}>
    <circle {...base} strokeWidth={width} strokeDasharray={dash} />
  </g>
);

const plural = (n: number, one: string, many: string) => (n === 1 ? one : many);

export function StockDial({ units, minimum, maximum, bloodType }: { units: number; minimum: number; maximum: number; bloodType: string }) {
  const level = stockStatus(units, minimum);
  const notStocked = isNotStocked(units, minimum);
  const hasScale = Number.isFinite(maximum) && maximum > 0 && maximum >= minimum;
  const fraction = hasScale ? Math.min(1, Math.max(0, units / maximum)) : 0; // over the maximum: full arc, true number
  const showFill = hasScale && !notStocked && fraction > 0; // a zero-length round-capped dash would still draw a dot
  const showTick = hasScale && !notStocked && minimum > 0;

  const tickAngle = ((START_DEG + SWEEP_DEG * (minimum / (hasScale ? maximum : 1))) * Math.PI) / 180;
  const tickPoint = (r: number) => ({ x: CENTRE + r * Math.cos(tickAngle), y: CENTRE + r * Math.sin(tickAngle) });
  const inner = tickPoint(RADIUS - TICK_REACH);
  const outer = tickPoint(RADIUS + TICK_REACH);

  const count = `${units} ${plural(units, "unit", "units")}`;
  const scale = notStocked ? "not stocked" : `minimum ${minimum}${hasScale ? `, maximum ${maximum}` : ""}`;
  const label = `${bloodType}: ${count}, ${scale}${notStocked ? "" : `, status ${STOCK_LABELS[level]}`}`; // the status word is the one the card shows

  const circle = { cx: CENTRE, cy: CENTRE, r: RADIUS, fill: "none", strokeWidth: STROKE, strokeLinecap: "round" as const, transform: `rotate(${START_DEG} ${CENTRE} ${CENTRE})` };
  return (
    <div className="relative mx-auto aspect-square w-full max-w-[9rem]">
      <svg viewBox="0 0 100 100" role="img" aria-label={label} className="absolute inset-0 h-full w-full">
        <title>{count} — {scale}</title>
        <circle {...circle} stroke="var(--dial-track)" strokeDasharray={`${ARC} ${CIRCUMFERENCE}`} />
        {[clayLayer("groove", "var(--dial-track)", `${ARC} ${CIRCUMFERENCE}`, 2, -1, circle), clayLayer("lip", "var(--dial-track)", `${ARC} ${CIRCUMFERENCE}`, 1.4, 1, circle)]}
        {showFill && <circle {...circle} stroke={FILL[level]} strokeDasharray={`${ARC * fraction} ${CIRCUMFERENCE}`} />}
        {showFill && [clayLayer("hi", FILL[level], `${ARC * fraction} ${CIRCUMFERENCE}`, 2.2, -1, circle), clayLayer("lo", FILL[level], `${ARC * fraction} ${CIRCUMFERENCE}`, 2.2, 1, circle)]}
        {/* Two layers: no one colour clears 3:1 against both the track and the bright fills, so the tick is a --foreground
            line on a wider --card one, and it reads against whatever it crosses. */}
        {showTick && (
          <>
            <line x1={inner.x} y1={inner.y} x2={outer.x} y2={outer.y} stroke="var(--card)" strokeWidth={5} />
            <line x1={inner.x} y1={inner.y} x2={outer.x} y2={outer.y} stroke="var(--foreground)" strokeWidth={2} />
          </>
        )}
      </svg>
      <div aria-hidden="true" className="absolute inset-0 flex flex-col items-center justify-center gap-1">
        <BloodTypeBadge type={bloodType} size="sm" />
        <span className={`font-display font-bold text-[26px] leading-none tabular-nums ${level === "safe" ? "text-foreground" : STATUS_STYLES[level].text}`}>
          {units}
        </span>
        {!notStocked && minimum > 0 && <span className="text-[11px] leading-none tabular-nums text-muted-foreground">min {minimum}</span>}
      </div>
    </div>
  );
}
