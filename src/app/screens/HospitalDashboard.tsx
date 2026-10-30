import { useState } from "react";
import { CheckCircle } from "lucide-react";
import { BloodTypeBadge } from "../components/BloodTypeBadge";
import { ExpiryWarningRow } from "../components/ExpiryWarningRow";
import { Skeleton } from "../components/Skeleton";
import { BLOOD_TYPE_ORDER, EXPIRY_NEAR_DAYS, STATUS_STYLES, STOCK_LABELS, isNotStocked, stockStatus, type StatusLevel } from "../lib/statusTokens";
import { type InventorySummaryRow, type InventoryUnit } from "../lib/inventoryTypes";
import {
  EMERGENCY_TYPE_LABELS, isOutstandingRequest, needsRequesterAction, requesterStatusText, type RequestRow,
} from "../lib/requestTypes";

// What a hospital came for: which blood types it is short of, whether it can get them, and what it has already
// asked for. A hospital consumes blood rather than managing a large stock, so there is no forecast here.
//
// Depth: exactly two raised surfaces (Action Required, Your requests), no border, edge defined by
// --shadow-raised. Everything else sits flat on the page background.
// Colour: three states and neutrals. Teal = adequate (at or above minimum), gold = below minimum, red-orange =
// critical (under 60% of minimum, the band stockStatus already defines). Each state also carries a word, so colour
// is never the only signal. Everything else is neutral.

export type ActionPrompt = {
  blood_type: string;
  units: number;
  minimum_units: number;
  deficit: number;
  message: string;
  requires_confirmation: boolean;
};

export type ThresholdView = {
  view: "threshold_status";
  is_dengue_season: boolean;
  thresholds: { blood_type: string; units: number; minimum_units: number; status: "ok" | "below_minimum"; deficit: number }[];
  action_prompts: ActionPrompt[];
};

const VISIBLE_ITEM_LIMIT = 3;
// How "Stock against minimum" scales its rows. true: every row spans 0 to that type's own maximum, so the band sits
// in the same place on every line and "inside or short of the band" reads identically for AB- (3 of 10) and O+ (52
// of 40). false: one 0..largest axis shared by all rows, so lengths compare across types. The numbers on the
// right give absolute volume either way.
const PER_ROW_SCALE = true;
const RAISED = "shadow-[var(--shadow-raised)]";

const plural = (n: number, one: string, many: string) => (n === 1 ? one : many);
const levelOf = (s: { units: number; minimum_units: number }): StatusLevel => stockStatus(s.units, s.minimum_units);
// Tint and divider for the Action Required surface follow its worst shortage.
const ALERT_TINT = { watch: "bg-status-watch-tint", critical: "bg-status-critical-tint" } as const;
const ALERT_RULE = { watch: "border-status-watch-border/30", critical: "border-status-critical-border/30" } as const;

export type HospitalDashboardProps = {
  summary: InventorySummaryRow[];
  summaryLoading: boolean;
  summaryError: string | null;
  expiringRows: InventoryUnit[];
  expiryLoading: boolean;
  expiryError: string | null;
  thresholdView: ThresholdView | null;
  forecastLoading: boolean;
  forecastError: string | null;
  onRetryForecast: () => void;
  requests: RequestRow[] | null;
  requestsFailed: boolean;
  onRequestBloodType: (bloodType: string) => void;
  onViewRequests: () => void;
};

export function HospitalDashboardView(p: HospitalDashboardProps) {
  const outstanding = p.requests?.filter(isOutstandingRequest) ?? null;
  return (
    <div className="max-w-screen-2xl mx-auto px-4 sm:px-6 py-6 sm:py-8 space-y-6">
      <SummaryLine {...p} openRequests={outstanding?.length ?? null} />

      {/* Phone order is DOM order (action, requests, chart, expiry). From lg the requests column spans the three
          rows on the right; the last row is 1fr so a tall column never opens a gap between the left sections. */}
      <div className="grid grid-cols-1 lg:grid-cols-3 lg:grid-rows-[auto_auto_1fr] gap-x-8 gap-y-8 lg:gap-y-10">
        <div className="lg:col-span-2 lg:col-start-1 lg:row-start-1">
          <ActionRequired {...p} />
        </div>
        <div className="lg:col-start-3 lg:row-start-1 lg:row-span-3 self-start">
          <YourRequests requests={outstanding} failed={p.requestsFailed} onViewRequests={p.onViewRequests} />
        </div>
        <div className="lg:col-span-2 lg:col-start-1 lg:row-start-2">
          <StockAgainstMinimum summary={p.summary} loading={p.summaryLoading} error={p.summaryError} />
        </div>
        <div className="lg:col-span-2 lg:col-start-1 lg:row-start-3">
          <ExpiringSoon rows={p.expiringRows} loading={p.expiryLoading} error={p.expiryError} />
        </div>
      </div>
    </div>
  );
}

// ─── Summary line ───────────────────────────────────────────────────────────

function SummaryLine(p: HospitalDashboardProps & { openRequests: number | null }) {
  if (p.summaryLoading || p.expiryLoading) return <Skeleton className="h-5 w-full max-w-[34rem]" />;
  if (p.summaryError || p.expiryError) {
    return <p className="text-[15px] text-status-critical-text">Failed to load supply status: {p.summaryError || p.expiryError}</p>;
  }
  const critical = p.summary.filter((s) => levelOf(s) === "critical").length;
  const watch = p.summary.filter((s) => levelOf(s) === "watch").length;
  const total = p.summary.reduce((sum, s) => sum + s.units, 0);
  const expiring = p.expiringRows.length;
  const open = p.openRequests;
  return (
    <p className="text-[15px] text-foreground leading-relaxed">
      <strong
        className={`font-bold ${critical > 0 ? "text-status-critical-text" : watch > 0 ? "text-status-watch-text" : "text-status-safe-text"}`}
      >
        {critical > 0 && watch > 0
          ? `${critical} blood ${plural(critical, "type is", "types are")} critically low and ${watch} below minimum.`
          : critical > 0
          ? `${critical} blood ${plural(critical, "type is", "types are")} critically low.`
          : watch > 0
          ? `${watch} blood ${plural(watch, "type is", "types are")} below minimum.`
          : "Every blood type is at or above its minimum."}
      </strong>{" "}
      {total} {plural(total, "unit", "units")} in stock, {expiring} expiring within {EXPIRY_NEAR_DAYS} days
      {open === null ? "." : `, and ${open} open ${plural(open, "request", "requests")}.`}
    </p>
  );
}

// ─── Action Required (raised) ───────────────────────────────────────────────

function ActionRequired(p: HospitalDashboardProps) {
  const [showAll, setShowAll] = useState(false);
  const prompts = p.thresholdView?.action_prompts ?? [];
  const hasPrompts = prompts.length > 0;
  const worst: "watch" | "critical" = prompts.some((pr) => levelOf(pr) === "critical") ? "critical" : "watch";
  const visible = showAll ? prompts : prompts.slice(0, VISIBLE_ITEM_LIMIT);

  return (
    <section
      aria-labelledby="action-required-heading"
      // The tint is the alert: it appears here, and only when something is actually short. It takes the colour of
      // the worst shortage, so a surface that is only gold never claims to be critical.
      className={`rounded-2xl p-6 sm:p-7 ${RAISED} ${hasPrompts ? ALERT_TINT[worst] : "bg-card"}`}
    >
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h2 id="action-required-heading" className="font-display font-bold text-2xl tracking-tight text-foreground">
          Action required
        </h2>
        {prompts.length > VISIBLE_ITEM_LIMIT && (
          <button
            onClick={() => setShowAll((v) => !v)}
            className={`text-[14px] font-semibold rounded-sm hover:underline hover:underline-offset-2 ${STATUS_STYLES[worst].text}`}
          >
            {showAll ? "Show fewer" : `Show ${prompts.length - VISIBLE_ITEM_LIMIT} more`}
          </button>
        )}
      </div>
      <p className="text-[15px] text-foreground mt-1.5 mb-5">
        Current stock against each type's minimum. Nothing is sent until you confirm each request yourself.
      </p>

      {p.forecastLoading && !p.thresholdView && (
        <div className="space-y-4" aria-busy="true">
          {[0, 1].map((i) => (
            <div key={i} className="flex items-center gap-4">
              <Skeleton className="h-9 w-[54px]" />
              <div className="flex-1 space-y-2">
                <Skeleton className="h-4 w-40" />
                <Skeleton className="h-3.5 w-52" />
              </div>
              <Skeleton className="h-10 w-56 hidden sm:block" />
            </div>
          ))}
        </div>
      )}
      {!p.forecastLoading && p.forecastError && (
        <div role="alert" className="text-[15px] text-status-critical-text">
          Failed to load: {p.forecastError}
          <div className="mt-3">
            <button
              onClick={p.onRetryForecast}
              className="h-10 px-4 bg-primary text-white rounded-md text-sm font-semibold hover:bg-primary-hover transition-colors"
            >
              Retry
            </button>
          </div>
        </div>
      )}
      {!p.forecastLoading && !p.forecastError && p.thresholdView && !hasPrompts && (
        <div className="flex items-start gap-3">
          <CheckCircle size={22} className="text-status-safe-text shrink-0 mt-0.5" />
          <div>
            <div className="text-[17px] font-semibold text-foreground">Nothing to order</div>
            <p className="text-[15px] text-foreground">Every blood type is at or above its minimum.</p>
          </div>
        </div>
      )}
      {!p.forecastError && hasPrompts && (
        <>
          <ul>
            {visible.map((prompt) => {
              const level = levelOf(prompt);
              return (
              <li
                key={prompt.blood_type}
                className={`py-4 first:pt-0 last:pb-0 border-t first:border-t-0 flex flex-col sm:flex-row sm:items-center gap-3 sm:gap-6 ${ALERT_RULE[worst]}`}
              >
                <div className="flex items-center gap-4 flex-1 min-w-0">
                  <BloodTypeBadge type={prompt.blood_type} size="lg" />
                  <div>
                    <div className={`text-[17px] font-semibold ${STATUS_STYLES[level].text}`}>
                      {level === "critical" ? "Critically low, short by" : "Below minimum, short by"} {prompt.deficit} {plural(prompt.deficit, "unit", "units")}
                    </div>
                    <div className="text-[14px] text-foreground">
                      {prompt.units} on hand, minimum {prompt.minimum_units}.
                    </div>
                  </div>
                </div>
                <button
                  onClick={() => p.onRequestBloodType(prompt.blood_type)}
                  className="w-full sm:w-auto h-11 sm:h-10 px-4 bg-primary text-white rounded-md text-sm font-semibold hover:bg-primary-hover transition-colors"
                >
                  Request {prompt.blood_type} from nearby blood banks
                </button>
              </li>
              );
            })}
          </ul>
          {p.thresholdView?.is_dengue_season && (
            <p className={`mt-5 pt-4 border-t text-[14px] text-foreground ${ALERT_RULE[worst]}`}>
              Coincides with dengue season, a period of historically elevated demand in this region.
            </p>
          )}
        </>
      )}
    </section>
  );
}

// ─── Your requests (raised) ─────────────────────────────────────────────────

function YourRequests({ requests, failed, onViewRequests }: { requests: RequestRow[] | null; failed: boolean; onViewRequests: () => void }) {
  return (
    <aside aria-labelledby="your-requests-heading" className={`rounded-2xl bg-card p-6 ${RAISED}`}>
      <h2 id="your-requests-heading" className="font-display font-bold text-xl tracking-tight text-foreground">
        Your requests
      </h2>
      <p className="text-[14px] text-foreground mt-1 mb-4">Sent by you and not yet received.</p>

      {requests === null && !failed && (
        <div className="space-y-4" aria-busy="true">
          {[0, 1].map((i) => (
            <div key={i} className="flex gap-3">
              <Skeleton className="h-7 w-11" />
              <div className="flex-1 space-y-2">
                <Skeleton className="h-4 w-32" />
                <Skeleton className="h-3.5 w-44" />
              </div>
            </div>
          ))}
        </div>
      )}
      {requests === null && failed && (
        <p role="alert" className="text-[14px] text-status-critical-text">Couldn't load your requests. Open the Requests screen to see them.</p>
      )}
      {requests !== null && requests.length === 0 && (
        <p className="text-[15px] text-foreground">
          No open requests. When you send one from the list, it appears here with its status.
        </p>
      )}
      {requests !== null && requests.length > 0 && (
        <ul>
          {requests.map((req) => {
            const sent = new Date(req.created_at);
            return (
              <li key={req.id} className="flex gap-3 py-3.5 border-t border-gray-200 first:border-t-0 first:pt-0">
                <BloodTypeBadge type={req.blood_type} size="md" />
                <div className="min-w-0 flex-1">
                  <div className="text-[15px] font-semibold text-foreground">
                    {req.quantity} {plural(req.quantity, "unit", "units")}{req.component ? ` of ${req.component}` : ""} for {EMERGENCY_TYPE_LABELS[req.emergency_type].toLowerCase()}
                  </div>
                  <div className="text-[14px] text-foreground">{req.supplying_facility_name ?? `Facility ${req.supplying_facility_id}`}</div>
                  <div className={`text-[14px] font-semibold mt-0.5 ${needsRequesterAction(req) ? "text-primary" : "text-foreground"}`}>
                    {requesterStatusText(req)}
                  </div>
                  <div className="text-[13px] text-gray-600 mt-0.5">
                    Sent {sent.toLocaleDateString(undefined, { month: "short", day: "numeric" })} at{" "}
                    {sent.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}
                  </div>
                </div>
              </li>
            );
          })}
        </ul>
      )}

      <button
        onClick={onViewRequests}
        className="mt-4 text-[14px] font-semibold text-primary rounded-sm hover:underline hover:underline-offset-2"
      >
        View all requests
      </button>
    </aside>
  );
}

// ─── Stock against minimum (flat) ───────────────────────────────────────────

// One bar per type for current stock, sitting against a track that runs from that type's minimum to its
// maximum. A bar that ends before the track starts is below minimum; the bar and the track answer the only
// question this chart is for. Every row shares one scale so lengths compare across types.
function StockAgainstMinimum({ summary, loading, error }: { summary: InventorySummaryRow[]; loading: boolean; error: string | null }) {
  const rows = [...summary].sort((a, b) => BLOOD_TYPE_ORDER.indexOf(a.blood_type) - BLOOD_TYPE_ORDER.indexOf(b.blood_type));
  const sharedMax = Math.max(1, ...rows.map((r) => Math.max(r.units, r.minimum_units, r.maximum_units)));
  // A row's full width is this many units. Per row, that is the type's own maximum (or its stock, if it is over it).
  const scaleOf = (r: InventorySummaryRow) => (PER_ROW_SCALE ? Math.max(r.maximum_units, r.units, 1) : sharedMax);
  const pct = (n: number, scale: number) => `${Math.min(100, Math.max(0, (n / scale) * 100))}%`;

  return (
    <section aria-labelledby="stock-heading">
      <h2 id="stock-heading" className="font-display font-bold text-xl tracking-tight text-foreground">
        Stock against minimum
      </h2>
      <p className="text-[14px] text-foreground mt-1 max-w-[62ch]">
        Each bar is the stock you hold now. The outlined track runs from the minimum to the maximum you set for that type.
        {PER_ROW_SCALE && " Every row is drawn to its own maximum, so a bar reads the same way for every type; the numbers give the volume."}
        {" "}A bar is critical when it is under 60% of the minimum.
      </p>
      <div className="flex flex-wrap gap-x-5 gap-y-1.5 mt-3 mb-5 text-[13px] text-foreground">
        <span className="flex items-center gap-2"><span className="w-3 h-2.5 rounded-sm bg-status-safe" /> Adequate</span>
        <span className="flex items-center gap-2"><span className="w-3 h-2.5 rounded-sm bg-status-watch" /> Below minimum</span>
        <span className="flex items-center gap-2"><span className="w-3 h-2.5 rounded-sm bg-status-critical" /> Critical</span>
        <span className="flex items-center gap-2"><span className="w-5 h-3 rounded-sm border border-gray-500" /> Minimum to maximum</span>
      </div>

      {loading && (
        <div className="space-y-3" aria-busy="true">
          {Array.from({ length: 8 }).map((_, i) => <Skeleton key={i} className="h-9 w-full" />)}
        </div>
      )}
      {!loading && error && <p role="alert" className="text-[15px] text-status-critical-text">Failed to load inventory summary: {error}</p>}
      {!loading && !error && (
        <>
          <ul className="space-y-3">
            {rows.map((r) => {
              const level = levelOf(r);
              const notStocked = isNotStocked(r.units, r.minimum_units);
              const scale = scaleOf(r);
              const hasTrack = r.maximum_units > r.minimum_units;
              return (
                <li key={r.blood_type} className="grid grid-cols-[38px_1fr_6.5rem] items-center gap-3 sm:gap-4">
                  <BloodTypeBadge type={r.blood_type} size="sm" />
                  <div className="relative h-5" aria-hidden="true">
                    {/* The baseline runs from zero to where the track starts, so a short bar shows the gap it leaves. */}
                    <div className="absolute left-0 top-1/2 h-px bg-gray-300" style={{ width: hasTrack ? pct(r.minimum_units, scale) : "100%" }} />
                    {/* An outline, not a fill: a filled track puts grey above and below the bar's end, and teal on grey
                        looks darker than teal on paper. The bar is drawn after it, so the outline's left edge goes
                        behind the bar and only two small stubs mark where the minimum is. */}
                    {hasTrack && (
                      <div
                        className="absolute inset-y-0 rounded-sm border border-gray-500"
                        style={{ left: pct(r.minimum_units, scale), width: pct(r.maximum_units - r.minimum_units, scale) }}
                      />
                    )}
                    {r.units > 0 && (
                      <div
                        className={`absolute top-1/2 -translate-y-1/2 h-2.5 rounded-sm ${STATUS_STYLES[level].dot}`}
                        style={{ left: 0, width: pct(r.units, scale) }}
                      />
                    )}
                  </div>
                  <div className="text-right leading-tight">
                    <div className={`text-[15px] font-bold tabular-nums ${level === "safe" ? "text-foreground" : STATUS_STYLES[level].text}`}>
                      {notStocked ? "Not stocked" : r.units}
                    </div>
                    {!notStocked && <div className="text-[12.5px] text-foreground tabular-nums">minimum {r.minimum_units}</div>}
                    <span className="sr-only">
                      {r.blood_type}: {r.units} {plural(r.units, "unit", "units")}, minimum {r.minimum_units}, maximum {r.maximum_units}, {STOCK_LABELS[level].toLowerCase()}
                    </span>
                  </div>
                </li>
              );
            })}
          </ul>
          {/* A shared axis means something; per-row scales have no single axis to label. */}
          {!PER_ROW_SCALE && (
            <div className="grid grid-cols-[38px_1fr_6.5rem] gap-3 sm:gap-4 mt-2 text-[12px] text-foreground tabular-nums" aria-hidden="true">
              <span />
              <div className="flex justify-between"><span>0</span><span>{sharedMax} units</span></div>
              <span />
            </div>
          )}
        </>
      )}
    </section>
  );
}

// ─── Expiring soon (flat) ───────────────────────────────────────────────────

function ExpiringSoon({ rows, loading, error }: { rows: InventoryUnit[]; loading: boolean; error: string | null }) {
  const [showAll, setShowAll] = useState(false);
  const visible = showAll ? rows : rows.slice(0, VISIBLE_ITEM_LIMIT);
  return (
    <section aria-labelledby="expiry-heading">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4">
        <h2 id="expiry-heading" className="font-display font-bold text-xl tracking-tight text-foreground">
          Expiring soon
        </h2>
        {rows.length > VISIBLE_ITEM_LIMIT && (
          <button
            onClick={() => setShowAll((v) => !v)}
            className="text-[14px] font-semibold text-primary rounded-sm hover:underline hover:underline-offset-2"
          >
            {showAll ? "Show fewer" : `Show ${rows.length - VISIBLE_ITEM_LIMIT} more`}
          </button>
        )}
      </div>
      <div className="mt-3">
        {loading && <Skeleton className="h-14 w-full" />}
        {!loading && error && <p role="alert" className="text-[15px] text-status-critical-text">Failed to load: {error}</p>}
        {!loading && !error && rows.length === 0 && (
          <p className="text-[15px] text-foreground">No units expire within {EXPIRY_NEAR_DAYS} days.</p>
        )}
        {!loading && !error && visible.map((row) => <ExpiryWarningRow key={row.din} row={row} variant="flat" />)}
      </div>
    </section>
  );
}
