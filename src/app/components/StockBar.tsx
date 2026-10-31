import { STATUS_STYLES, stockStatus } from "../lib/statusTokens";

// One bar for the stock held, against an outlined track from the minimum to the maximum. Each row is drawn to its own maximum,
// so a bar reads the same way for every type; the Units column gives the volume. Shared by the blood bank's Dashboard and
// Inventory, so the two draw a type's stock identically.
export function StockBar({ units, minimum, maximum }: { units: number; minimum: number; maximum: number }) {
  const level = stockStatus(units, minimum);
  const scale = Math.max(maximum, units, 1);
  const pct = (n: number) => `${Math.min(100, Math.max(0, (n / scale) * 100))}%`;
  const hasTrack = maximum > minimum;
  return (
    <div className="relative h-5" aria-hidden="true">
      <div className="absolute left-0 top-1/2 h-px bg-gray-300" style={{ width: hasTrack ? pct(minimum) : "100%" }} />
      {hasTrack && (
        <div className="absolute inset-y-0 rounded-sm border border-gray-500" style={{ left: pct(minimum), width: pct(maximum - minimum) }} />
      )}
      {units > 0 && (
        <div className={`absolute top-1/2 -translate-y-1/2 h-2.5 rounded-sm ${STATUS_STYLES[level].dot}`} style={{ left: 0, width: pct(units) }} />
      )}
    </div>
  );
}
