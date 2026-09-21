import { useState, useRef, useEffect } from "react";
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell,
  ComposedChart, Area, Line, ReferenceLine,
} from "recharts";
import { AlertTriangle, ArrowRight, Bell, CheckCircle, Clock, FlaskConical, TrendingUp, Upload, Zap } from "lucide-react";
import {
  apiGet, uploadHistoricalInventorySnapshots, notifyNearbyHospitals,
  type HistoricalUploadResult, type NotifyNearbyHospitalsResult,
} from "../lib/api";
import {
  getExpiryStatus, EXPIRY_STYLES, type StatusLevel, STATUS_STYLES, stockStatus, STATUS_HEX,
} from "../lib/statusTokens";
import { StatusDot, BloodTypeBadge } from "../components/BloodTypeBadge";
import { Skeleton } from "../components/Skeleton";
import { UploadHistoryPanel } from "../components/UploadHistoryPanel";
import { useFlashOnChange } from "../lib/motion";
import { type InventoryUnit, type InventoryApiRow, toInventoryUnit } from "../lib/inventoryTypes";

// Narrowed to just the fields this screen actually reads — the full request
// shape (RequestRow) belongs to the Requests screen; Dashboard only ever
// counts how many are active/emergency, so it doesn't need the rest.
type ActiveRequestSummary = { status: string; emergency_type: string };

// Same order of magnitude as AccountMenu's NOTIFICATION_POLL_MS — frequent
// enough that a status change is visible within one sitting, infrequent
// enough not to matter for API load.
const SUMMARY_POLL_MS = 25000;

// ─── Dashboard ────────────────────────────────────────────────────────────────

function ExpiryWarningRow({ row }: { row: InventoryUnit }) {
  const [notifyState, setNotifyState] = useState<"idle" | "sending" | "sent" | "error">("idle");
  const [notifyResult, setNotifyResult] = useState<NotifyNearbyHospitalsResult | null>(null);
  const [notifyError, setNotifyError] = useState<string | null>(null);
  const status = getExpiryStatus(row.daysLeft);
  const style = EXPIRY_STYLES[status];

  async function handleNotify() {
    setNotifyState("sending");
    setNotifyError(null);
    try {
      const result = await notifyNearbyHospitals(row.din);
      setNotifyResult(result);
      setNotifyState("sent");
    } catch (err) {
      setNotifyError(err instanceof Error ? err.message : "Failed to notify nearby hospitals");
      setNotifyState("error");
    }
  }

  return (
    <div className={`rounded-lg px-3 py-2.5 text-[14px] ${style.panel}`}>
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2.5 min-w-0">
          <span className={`font-display font-bold leading-none tracking-tight text-[14px] w-9 h-6 flex items-center justify-center rounded shrink-0 ${style.solid}`}>
            {row.type}
          </span>
          <div className="min-w-0">
            <div className="font-mono text-[14px] font-semibold text-foreground truncate">{row.din}</div>
            <div className="text-foreground text-[13px] truncate">{row.component}</div>
          </div>
        </div>
        <div className={`font-bold text-right shrink-0 ${style.text}`}>
          {status === "expired" ? "Expired" : `${row.daysLeft}d`}
          {status !== "expired" && <div className="text-foreground text-[12px] font-normal">left</div>}
        </div>
      </div>
      <div className="mt-2 pt-2 border-t border-black/5 dark:border-white/10">
        {notifyState === "sent" ? (
          <span className="flex items-start gap-1 text-[13px] font-semibold text-status-safe-text leading-snug">
            <CheckCircle size={13} className="shrink-0 mt-0.5" />
            {notifyResult && notifyResult.notified_count > 0
              ? `${notifyResult.notified_count} nearby hospital${notifyResult.notified_count === 1 ? "" : "s"} alerted`
              : "No nearby hospitals to alert"}
          </span>
        ) : (
          <button
            onClick={handleNotify}
            disabled={notifyState === "sending"}
            className={`w-full h-6 px-2 rounded text-[13px] font-semibold transition-colors flex items-center justify-center gap-1 ${style.solid} hover:opacity-90 disabled:opacity-60`}
          >
            <Bell size={12} className="shrink-0" />
            <span className="min-w-0 truncate">{notifyState === "sending" ? "Notifying…" : "Notify Nearby Hospitals"}</span>
          </button>
        )}
        {notifyState === "error" && (
          <div className="mt-1.5 text-[12px] text-status-critical-text">{notifyError}</div>
        )}
      </div>
    </div>
  );
}

// Recharts' default Tooltip would list every series on the chart, including
// the two Area layers that only exist to draw the shaded confidence band —
// "lower: 320, band: 45, units: 340" means nothing to a reader. This shows
// just the projected total and, when available, its predicted range.
function ForecastTooltip({
  active, payload, label,
}: {
  active?: boolean;
  payload?: { payload: { units: number; lower: number | null; upper: number | null } }[];
  label?: string;
}) {
  if (!active || !payload || payload.length === 0) return null;
  const d = payload[0].payload;
  return (
    <div className="bg-card border border-border rounded-md px-3 py-2 text-[13px] shadow-sm">
      <div className="font-bold text-foreground mb-0.5">{label}</div>
      <div className="text-foreground">{d.units} units</div>
      {d.lower !== null && d.upper !== null && (
        <div className="text-foreground text-[12px]">
          Likely range: {d.lower}–{d.upper}
        </div>
      )}
    </div>
  );
}

type InventorySummaryRow = { blood_type: string; minimum_units: number; maximum_units: number; units: number };

type ForecastAlert = {
  type: string;
  severity: "critical" | "warn";
  reason: string;
  days_until_threshold: number;
};

// Blood-bank-type facilities: /forecast returns this shape (Steps 4 + SARIMAX work).
type ForecastView = {
  view: "forecast";
  has_sufficient_history: boolean;
  days_of_history: number;
  min_days_required: number;
  is_dengue_season: boolean;
  forecast_source: "sarimax_facility_history" | "linear_trend" | "synthetic_model_stand_in" | "none";
  synthetic_model_label?: string;
  // Only present when forecast_source is "sarimax_facility_history" — see
  // _fit_sarimax_facility_forecast in main.py.
  sarimax_model_label?: string;
  sarimax_fallback_types?: string[];
  sarimax_min_days_required?: number;
  // Present (non-null) on both the linear_trend and sarimax_facility_history
  // paths once at least one blood type has an interval to report — see
  // _prediction_interval_half_width / _fit_sarimax_facility_forecast in
  // main.py. null on every other path (synthetic never has one; "none" has
  // no series at all).
  interval_confidence: number | null;
  series: { day: string; units: number; lower: number | null; upper: number | null }[];
  alerts: ForecastAlert[];
};

// True once the forecast is derived from this facility's own real history —
// via either method (SARIMAX once there's enough of it, a linear trend
// before that) — as opposed to the synthetic stand-in or no data at all.
function isRealForecastSource(source: ForecastView["forecast_source"]): boolean {
  return source === "sarimax_facility_history" || source === "linear_trend";
}

// Hospital-type facilities: /forecast returns this instead — no forecast at
// all, just current-stock-vs-minimum and an explicit, staff-confirmed action
// per shortage. See _build_hospital_threshold_view in main.py.
type ThresholdEntry = {
  blood_type: string;
  units: number;
  minimum_units: number;
  status: "ok" | "below_minimum";
  deficit: number;
};

type ActionPrompt = {
  blood_type: string;
  units: number;
  minimum_units: number;
  deficit: number;
  message: string;
  requires_confirmation: boolean;
};

type ThresholdView = {
  view: "threshold_status";
  is_dengue_season: boolean;
  thresholds: ThresholdEntry[];
  action_prompts: ActionPrompt[];
};

type DashboardData = ForecastView | ThresholdView;

// A single blood type's card in the "Status by Type" grid. Split out from
// the grid's .map so it can hold its own useFlashOnChange — a brief ring
// pulse plays exactly when this type's status actually flips (e.g. Adequate
// -> Marginal), not on every render the grid happens to re-render for.
function StatusTypeTile({ bloodType, units, minimumUnits }: { bloodType: string; units: number; minimumUnits: number }) {
  const status = stockStatus(units, minimumUnits);
  const label = status === "critical" ? "Low" : status === "watch" ? "Marginal" : "Adequate";
  const flash = useFlashOnChange(status);
  return (
    <div
      className={`rounded-xl border p-4 ${STATUS_STYLES[status].panel} ${flash ? "animate-value-flash-ring" : ""}`}
      style={{ "--flash-ring-color": STATUS_HEX[status] } as React.CSSProperties}
    >
      <div className="flex items-center justify-between mb-1.5">
        <BloodTypeBadge type={bloodType} size="md" />
        <StatusDot status={status} />
      </div>
      <div className={`text-3xl font-display font-bold leading-tight tabular-nums ${STATUS_STYLES[status].text}`}>{units}</div>
      <div className={`text-[14px] font-semibold mt-0.5 ${STATUS_STYLES[status].text}`}>{label}</div>
    </div>
  );
}

export function DashboardScreen({ onRequestBloodType }: { onRequestBloodType: (bloodType: string) => void }) {
  const [summary, setSummary] = useState<InventorySummaryRow[]>([]);
  const [summaryLoading, setSummaryLoading] = useState(true);
  const [summaryError, setSummaryError] = useState<string | null>(null);

  const [expiryUnits, setExpiryUnits] = useState<InventoryUnit[]>([]);
  const [expiryLoading, setExpiryLoading] = useState(true);
  const [expiryError, setExpiryError] = useState<string | null>(null);

  // Both lists default to a 3-item preview so a facility with many shortages/
  // expiring units doesn't force its card (and, since these two cards sit in
  // the same grid row, its sibling too) to grow arbitrarily tall.
  const VISIBLE_ITEM_LIMIT = 3;
  const [showAllActions, setShowAllActions] = useState(false);
  const [showAllExpiry, setShowAllExpiry] = useState(false);

  // Named for what it actually is now — either a blood bank's forecast or a
  // hospital's threshold view, discriminated by "view" (see DashboardData).
  const [dashboardData, setDashboardData] = useState<DashboardData | null>(null);
  const [forecastLoading, setForecastLoading] = useState(true);
  const [forecastError, setForecastError] = useState<string | null>(null);

  const [activeRequestsCount, setActiveRequestsCount] = useState<number | null>(null);
  const [activeRequestsEmergencyCount, setActiveRequestsEmergencyCount] = useState<number | null>(null);

  // Historical inventory-snapshot backfill — only ever rendered when
  // dashboardData.view === "forecast", i.e. never for hospitals (see
  // DashboardData and _build_hospital_threshold_view server-side).
  const historyFileInputRef = useRef<HTMLInputElement>(null);
  const [uploadingHistory, setUploadingHistory] = useState(false);
  const [historyUploadResult, setHistoryUploadResult] = useState<HistoricalUploadResult | null>(null);
  const [historyUploadError, setHistoryUploadError] = useState<string | null>(null);
  const [backfillUploadHistoryKey, setBackfillUploadHistoryKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    // Silent background refresh (no loading flag on subsequent ticks) so
    // the Status by Type grid reflects changes made by other facilities —
    // and so a status that flips while this screen is open actually plays
    // the value-flash instead of only ever showing the state as of page load.
    function loadSummary(isFirstLoad: boolean) {
      apiGet<InventorySummaryRow[]>("/inventory/summary")
        .then((data) => { if (!cancelled) setSummary(data); })
        .catch((err) => { if (!cancelled && isFirstLoad) setSummaryError(err instanceof Error ? err.message : "Failed to load"); })
        .finally(() => { if (!cancelled && isFirstLoad) setSummaryLoading(false); });
    }
    loadSummary(true);
    const interval = setInterval(() => loadSummary(false), SUMMARY_POLL_MS);
    return () => { cancelled = true; clearInterval(interval); };
  }, []);

  useEffect(() => {
    let cancelled = false;
    apiGet<InventoryApiRow[]>("/inventory")
      .then((data) => { if (!cancelled) setExpiryUnits(data.map(toInventoryUnit)); })
      .catch((err) => { if (!cancelled) setExpiryError(err instanceof Error ? err.message : "Failed to load"); })
      .finally(() => { if (!cancelled) setExpiryLoading(false); });
    return () => { cancelled = true; };
  }, []);

  function loadForecast() {
    let cancelled = false;
    setForecastLoading(true);
    setForecastError(null);
    apiGet<DashboardData>("/forecast")
      .then((data) => { if (!cancelled) setDashboardData(data); })
      .catch((err) => { if (!cancelled) setForecastError(err instanceof Error ? err.message : "Failed to load"); })
      .finally(() => { if (!cancelled) setForecastLoading(false); });
    return () => { cancelled = true; };
  }
  useEffect(() => loadForecast(), []);

  async function handleHistoryFileSelected(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploadingHistory(true);
    setHistoryUploadError(null);
    setHistoryUploadResult(null);
    try {
      const result = await uploadHistoricalInventorySnapshots(file);
      setHistoryUploadResult(result);
      loadForecast();
      setBackfillUploadHistoryKey((k) => k + 1);
    } catch (err) {
      setHistoryUploadError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setUploadingHistory(false);
      if (historyFileInputRef.current) historyFileInputRef.current.value = "";
    }
  }

  useEffect(() => {
    if (!dashboardData?.view) return;
    let cancelled = false;
    // Hospitals count their own outgoing requests; blood banks count the
    // requests directed at them as the supplier — /requests/incoming is the
    // same endpoint the Transfer tab's "Incoming Requests" panel already uses.
    const endpoint = dashboardData.view === "threshold_status" ? "/requests" : "/requests/incoming";
    apiGet<ActiveRequestSummary[]>(endpoint)
      .then((rows) => {
        if (cancelled) return;
        const active = rows.filter((r) => r.status === "pending" || r.status === "accepted");
        const emergency = active.filter((r) => r.emergency_type === "trauma" || r.emergency_type === "scheduled_surgery");
        setActiveRequestsCount(active.length);
        setActiveRequestsEmergencyCount(emergency.length);
      })
      .catch(() => {}); // KPI card falls back to "…" below — not worth a dedicated error state
    return () => { cancelled = true; };
  }, [dashboardData?.view]);

  const totalUnits = summary.reduce((a, b) => a + b.units, 0);
  // Sum of every type's own minimum — the forecast chart plots TOTAL units
  // (all 8 types combined), so "minimum" only means anything there as this
  // combined figure; it's not any single type's own threshold.
  const totalMinimumUnits = summary.reduce((a, b) => a + b.minimum_units, 0);
  const expiringRows = expiryUnits.filter((r) => {
    const status = getExpiryStatus(r.daysLeft);
    return status === "critical" || status === "near-expiry";
  });

  // One overall read on "are we okay," not four competing numbers — worst
  // condition present wins: any type below minimum outranks anything running
  // low, which outranks everything being fine. See stockStatus/STATUS_STYLES.
  const criticalTypes = summary.filter((b) => stockStatus(b.units, b.minimum_units) === "critical");
  const watchTypes = summary.filter((b) => stockStatus(b.units, b.minimum_units) === "watch");
  const heroStatus: StatusLevel =
    criticalTypes.length > 0 ? "critical" : watchTypes.length > 0 || expiringRows.length > 0 ? "watch" : "safe";
  const heroHeadline =
    heroStatus === "critical"
      ? `${criticalTypes.length} blood type${criticalTypes.length === 1 ? "" : "s"} below minimum`
      : heroStatus === "watch" && watchTypes.length > 0
      ? `${watchTypes.length} blood type${watchTypes.length === 1 ? "" : "s"} running low`
      : heroStatus === "watch"
      ? `${expiringRows.length} unit${expiringRows.length === 1 ? "" : "s"} expiring within 7 days`
      : "Supply steady across all types";
  const heroDetail =
    heroStatus === "critical"
      ? `${criticalTypes.map((t) => t.blood_type).join(", ")} below minimum threshold`
      : heroStatus === "watch" && watchTypes.length > 0
      ? `${watchTypes.map((t) => t.blood_type).join(", ")} approaching minimum — see the breakdown below`
      : heroStatus === "watch"
      ? "See Expiry Warnings below for which units"
      : "No shortages or expiring stock flagged right now";

  return (
    <div className="max-w-screen-2xl mx-auto px-6 py-6 space-y-6">
      {/* Hero status — the one thing to know at a glance. Shaped like the
          real hero banner below (headline + detail + 3 stats) so the layout
          doesn't jump once real data arrives — this is the panel most
          exposed to Render's cold-start delay, since it's the first thing
          rendered after login. */}
      {(summaryLoading || expiryLoading) && (
        <div className="rounded-xl border border-border bg-card p-6">
          <div className="flex items-start justify-between gap-6 flex-wrap">
            <div className="flex-1 min-w-[260px] space-y-2.5">
              <Skeleton className="h-3.5 w-28" />
              <Skeleton className="h-7 w-72 max-w-full" />
              <Skeleton className="h-4 w-52 max-w-full" />
            </div>
            <div className="flex items-center gap-6 sm:gap-8 flex-wrap">
              {[0, 1, 2].map((i) => (
                <div key={i} className="space-y-1.5">
                  <Skeleton className="h-3 w-20" />
                  <Skeleton className="h-7 w-12" />
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
      {!summaryLoading && !expiryLoading && (summaryError || expiryError) && (
        <div className="rounded-xl border border-status-critical-border bg-status-critical-tint p-6 text-center text-[15px] text-status-critical-text">
          Failed to load supply status: {summaryError || expiryError}
        </div>
      )}
      {!summaryLoading && !expiryLoading && !summaryError && !expiryError && (
        <div className={`rounded-xl border p-7 animate-card-rise-in ${STATUS_STYLES[heroStatus].panel}`} style={{ animationDelay: "250ms" }}>
          <div className="flex items-start justify-between gap-6 flex-wrap">
            <div className="flex-1 min-w-[260px]">
              <div className="flex items-center gap-2 mb-2">
                <StatusDot status={heroStatus} />
                <span className={`text-[13px] font-bold uppercase tracking-wider ${STATUS_STYLES[heroStatus].text}`}>
                  Supply Status
                </span>
              </div>
              <h2 className={`font-display font-extrabold text-[30px] leading-tight tracking-tight text-balance ${STATUS_STYLES[heroStatus].text}`}>
                {heroHeadline}
              </h2>
              <p className="text-[15px] text-foreground mt-1.5">{heroDetail}</p>
            </div>

            <div className="flex items-center gap-6 sm:gap-8 flex-wrap">
              <div>
                <div className="text-[12px] font-bold uppercase tracking-wide text-foreground mb-1">Total Units</div>
                <div className="text-3xl font-display font-bold tabular-nums text-foreground">{totalUnits}</div>
              </div>
              <div className="w-px h-10 bg-border hidden sm:block" />
              <div>
                <div className="text-[12px] font-bold uppercase tracking-wide text-foreground mb-1">Expiring ≤7d</div>
                <div className="text-3xl font-display font-bold tabular-nums text-foreground">{expiringRows.length}</div>
              </div>
              <div className="w-px h-10 bg-border hidden sm:block" />
              <div
                title={
                  activeRequestsCount !== null
                    ? `${activeRequestsEmergencyCount ?? 0} emergency, ${activeRequestsCount - (activeRequestsEmergencyCount ?? 0)} routine`
                    : undefined
                }
              >
                <div className="text-[12px] font-bold uppercase tracking-wide text-foreground mb-1">Active Requests</div>
                <div className="text-3xl font-display font-bold tabular-nums text-foreground">
                  {activeRequestsCount === null ? "…" : activeRequestsCount}
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Blood type breakdown */}
      {summaryLoading && (
        <div className="grid lg:grid-cols-3 gap-4">
          <div className="lg:col-span-2 bg-card border border-border rounded-xl p-6">
            <Skeleton className="h-4 w-44 mb-2" />
            <Skeleton className="h-3.5 w-56 mb-5" />
            <Skeleton className="h-[220px] w-full" />
          </div>
          <div className="bg-card border border-border rounded-xl p-6">
            <Skeleton className="h-4 w-28 mb-4" />
            <div className="grid grid-cols-2 gap-2">
              {Array.from({ length: 8 }).map((_, i) => (
                <div key={i} className="rounded-lg border border-border p-3 space-y-2">
                  <div className="flex items-center justify-between">
                    <Skeleton className="h-6 w-9" />
                    <Skeleton className="h-2.5 w-2.5 rounded-full" />
                  </div>
                  <Skeleton className="h-6 w-10" />
                  <Skeleton className="h-3.5 w-16" />
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
      {!summaryLoading && summaryError && (
        <div className="bg-status-critical-tint border border-status-critical-border rounded-xl p-6 text-center text-[14px] text-status-critical-text">
          Failed to load inventory summary: {summaryError}
        </div>
      )}
      {!summaryLoading && !summaryError && (
        <div className="grid lg:grid-cols-3 gap-4 animate-card-rise-in" style={{ animationDelay: "750ms" }}>
          <div className="lg:col-span-2 bg-card border border-border rounded-xl p-6 flex flex-col">
            <div className="flex items-center justify-between mb-5">
              <div>
                <h3 className="font-semibold text-foreground text-[19px]">Inventory by Blood Type</h3>
                <p className="text-[15px] text-foreground mt-0.5">
                  Current units vs. minimum/maximum thresholds
                </p>
              </div>
              {/* Swatch colors are picked to exactly match what's actually
                  drawn in the chart below, not approximated with a generic
                  Tailwind shade — "Current" uses the same teal as a
                  fully-stocked bar (its most common real state; watch/critical
                  types still render amber/red in the chart itself), and
                  Minimum/Maximum reuse their bars' literal hex values. */}
              <div className="flex gap-3.5 text-[15px] font-semibold text-foreground">
                <span className="flex items-center gap-2">
                  <span className="w-3.5 h-3.5 rounded-sm inline-block" style={{ backgroundColor: "#0F766E" }} /> Current
                </span>
                <span className="flex items-center gap-2">
                  <span className="w-3.5 h-3.5 rounded-sm inline-block" style={{ backgroundColor: "#A9A29A" }} /> Minimum
                </span>
                <span className="flex items-center gap-2">
                  <span className="w-3.5 h-3.5 rounded-sm inline-block border-2 border-info" style={{ backgroundColor: "#DCE7FB" }} /> Maximum
                </span>
              </div>
            </div>
            {/* flex-1 lets the chart claim whatever height the grid row
                actually stretched this card to (set by the taller "Status by
                Type" card alongside it), instead of staying a fixed 220px
                and leaving the rest of the card blank. min-h keeps it usable
                if this card is ever the tall one instead. */}
            <div className="flex-1 min-h-[220px]">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={summary} barGap={2} barCategoryGap="25%">
                <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" strokeOpacity={0.35} vertical={false} />
                <XAxis
                  dataKey="blood_type"
                  tick={{ fontSize: 15, fontFamily: "Plus Jakarta Sans", fill: "var(--foreground)", fontWeight: 700 }}
                  axisLine={false}
                  tickLine={false}
                />
                <YAxis
                  tick={{ fontSize: 13, fontFamily: "Plus Jakarta Sans", fill: "var(--foreground)", fontWeight: 700 }}
                  axisLine={false}
                  tickLine={false}
                  width={34}
                />
                <Tooltip
                  contentStyle={{
                    backgroundColor: "var(--card)",
                    border: "1px solid var(--border)",
                    borderRadius: "8px",
                    fontSize: 14,
                    fontFamily: "Plus Jakarta Sans",
                  }}
                  labelStyle={{ color: "var(--foreground)", fontWeight: 700, marginBottom: 4 }}
                  itemStyle={{ color: "var(--foreground)", fontWeight: 700 }}
                  cursor={{ fill: "var(--secondary)" }}
                />
                <Bar dataKey="units" radius={[4, 4, 0, 0]} name="Units">
                  {summary.map((entry, i) => (
                    <Cell key={`bt-${i}`} fill={STATUS_HEX[stockStatus(entry.units, entry.minimum_units)]} />
                  ))}
                </Bar>
                {/* var(--gray-400) is the app's neutral-scale token — reads
                    correctly against the chart background in both themes,
                    unlike a hardcoded hex snapshot of just the light value. */}
                <Bar dataKey="minimum_units" fill="var(--gray-400)" radius={[4, 4, 0, 0]} name="Minimum" />
                <Bar dataKey="maximum_units" fill="var(--info-tint)" stroke="var(--info)" strokeWidth={1.5} radius={[4, 4, 0, 0]} name="Maximum" />
              </BarChart>
            </ResponsiveContainer>
            </div>
          </div>

          {/* Type grid */}
          <div className="bg-card border border-border rounded-xl p-6">
            <h3 className="font-semibold text-foreground text-[19px] mb-4">Status by Type</h3>
            <div className="grid grid-cols-2 gap-2">
              {summary.map((bt) => (
                <StatusTypeTile key={bt.blood_type} bloodType={bt.blood_type} units={bt.units} minimumUnits={bt.minimum_units} />
              ))}
            </div>
          </div>
        </div>
      )}

      {/* Forecast/Threshold + Alert/Action + Expiry */}
      <div className="grid lg:grid-cols-3 gap-4">
        {dashboardData?.view === "forecast" && (
          <>
            {/* 30-day forecast */}
            <div className="lg:col-span-1 bg-card border border-border rounded-xl p-6">
              <div className="flex items-center justify-between mb-1">
                <h3 className="font-semibold text-foreground text-[19px]">30-Day Shortage Forecast</h3>
                {dashboardData.forecast_source === "synthetic_model_stand_in" && (
                  <span className="flex items-center gap-1 text-[13px] font-bold text-synthetic-text bg-synthetic-tint px-2 py-0.5 rounded-full border border-synthetic-border">
                    <FlaskConical size={13} /> Synthetic Model
                  </span>
                )}
                {isRealForecastSource(dashboardData.forecast_source) && dashboardData.alerts.length > 0 && (
                  <span className="flex items-center gap-1 text-[13px] font-bold text-status-watch-text bg-status-watch-tint px-2 py-0.5 rounded-full border border-status-watch-border">
                    <AlertTriangle size={13} /> At Risk
                  </span>
                )}
                {isRealForecastSource(dashboardData.forecast_source) && dashboardData.alerts.length === 0 && (
                  <span className="flex items-center gap-1 text-[13px] font-bold text-status-safe-text bg-status-safe-tint px-2 py-0.5 rounded-full border border-status-safe-border">
                    <CheckCircle size={13} /> Stable
                  </span>
                )}
                {dashboardData.forecast_source === "none" && (
                  <span className="flex items-center gap-1 text-[13px] font-bold text-foreground bg-secondary px-2 py-0.5 rounded-full border border-border">
                    <Clock size={13} /> Collecting Data
                  </span>
                )}
              </div>
              <p className="text-[15px] text-foreground mb-4">
                Total units across every blood type combined
                {dashboardData.forecast_source === "synthetic_model_stand_in" && " — synthetic, not this facility's real history — see banner below"}
                {dashboardData.forecast_source === "sarimax_facility_history" && " — SARIMAX model fit to this facility's own history"}
                {dashboardData.forecast_source === "linear_trend" && " — linear trend fit to this facility's own history"}
                . A steady total doesn't mean every type is steady.
              </p>

              {/* Historical backfill — lets a facility with real past
                  records skip the days_of_history wait instead of it. */}
              <input
                ref={historyFileInputRef}
                type="file"
                accept=".csv"
                onChange={handleHistoryFileSelected}
                className="hidden"
              />

              {/* Lead with the answer: which type(s), how urgent — before any
                  data-provenance housekeeping. See Predictive Shortage Alert
                  (right) for the full per-type breakdown; this is just the
                  pointer that sends someone there. */}
              {dashboardData.forecast_source !== "none" && dashboardData.alerts.length > 0 && (
                <div className="mb-4 rounded-lg border border-status-watch-border bg-status-watch-tint px-3.5 py-3 flex items-start gap-2.5">
                  <AlertTriangle size={16} className="text-status-watch-text shrink-0 mt-0.5" />
                  <p className="text-[14px] text-status-watch-text leading-snug">
                    <strong className="font-bold">{dashboardData.alerts.map((a) => a.type).join(", ")}</strong>{" "}
                    projected to fall below minimum within{" "}
                    <strong className="font-bold">
                      {Math.max(...dashboardData.alerts.map((a) => a.days_until_threshold))} days
                    </strong>
                    {" "}— see Predictive Shortage Alert for detail.
                  </p>
                </div>
              )}

              {forecastLoading && (
                <div className="py-8 text-center text-[15px] text-foreground">Loading forecast…</div>
              )}
              {!forecastLoading && forecastError && (
                <div className="py-8 text-center text-[15px] text-status-critical-text">Failed to load: {forecastError}</div>
              )}
              {!forecastLoading && !forecastError && dashboardData.forecast_source === "none" && (
                <div className="py-6 text-center text-[15px] text-foreground leading-relaxed">
                  Not enough history yet to forecast a trend.
                  <br />
                  Collecting daily snapshots: <strong className="text-foreground">{dashboardData.days_of_history}</strong> of{" "}
                  <strong className="text-foreground">{dashboardData.min_days_required}</strong> days needed.
                </div>
              )}
              {!forecastLoading && !forecastError && dashboardData.forecast_source !== "none" && (() => {
                // The backend omits this key entirely (rather than sending
                // null) on the synthetic path, so `!== null` alone lets
                // `undefined` through and produces "NaN%" below — check the
                // actual type instead of trusting the declared null-only union.
                const hasInterval = typeof dashboardData.interval_confidence === "number";
                return (
                <>
                  <ResponsiveContainer width="100%" height={200}>
                    <ComposedChart
                      data={dashboardData.series.map((d) => ({
                        ...d,
                        band: d.lower !== null && d.upper !== null ? d.upper - d.lower : 0,
                      }))}
                      margin={{ top: 8, right: 8, left: 0, bottom: 0 }}
                    >
                      <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" strokeOpacity={0.35} vertical={false} />
                      <XAxis
                        dataKey="day"
                        // interval={0} forces every tick to render instead of
                        // Recharts silently dropping whichever ones don't fit
                        // (which is what produced the uneven "Day 20 ... Day
                        // 30" gap with Day 25 missing) — the tickFormatter
                        // shortens labels to "5"/"10"/etc. so all 7 fit
                        // evenly spaced without overlapping.
                        interval={0}
                        tickFormatter={(value: string) => (value === "Today" ? "Today" : value.replace("Day ", ""))}
                        tick={{ fontSize: 12, fill: "var(--foreground)", fontFamily: "Plus Jakarta Sans", fontWeight: 700 }}
                        axisLine={false}
                        tickLine={false}
                      />
                      <YAxis
                        tick={{ fontSize: 11, fill: "var(--foreground)", fontFamily: "Plus Jakarta Sans", fontWeight: 700 }}
                        axisLine={false}
                        tickLine={false}
                        width={34}
                      />
                      <Tooltip content={<ForecastTooltip />} cursor={{ stroke: "var(--border)", strokeDasharray: "3 3" }} />
                      {totalMinimumUnits > 0 && (
                        <ReferenceLine y={totalMinimumUnits} stroke="var(--gray-400)" strokeWidth={1.5} strokeDasharray="4 4" />
                      )}
                      {hasInterval && (
                        <>
                          {/* Stacked-area trick for a shaded confidence band:
                              an invisible area up to `lower`, then a visible
                              one for the `lower..upper` gap stacked on top —
                              reads as "the range this could fall in," far
                              more legible than the old whisker caps, and
                              gets visibly wider further into the future. */}
                          <Area dataKey="lower" stackId="ci" stroke="none" fill="transparent" isAnimationActive={false} />
                          <Area
                            dataKey="band"
                            stackId="ci"
                            stroke={dashboardData.forecast_source === "synthetic_model_stand_in" ? "var(--synthetic)" : "var(--role-accent)"}
                            strokeWidth={1}
                            strokeOpacity={0.35}
                            fill={dashboardData.forecast_source === "synthetic_model_stand_in" ? "var(--synthetic)" : "var(--role-accent)"}
                            fillOpacity={0.3}
                            isAnimationActive={false}
                          />
                        </>
                      )}
                      <Line
                        type="monotone"
                        dataKey="units"
                        stroke={dashboardData.forecast_source === "synthetic_model_stand_in" ? "var(--synthetic)" : "var(--role-accent)"}
                        strokeWidth={3}
                        dot={{ r: 3.5, strokeWidth: 0, fill: dashboardData.forecast_source === "synthetic_model_stand_in" ? "var(--synthetic)" : "var(--role-accent)" }}
                        activeDot={{ r: 5 }}
                        isAnimationActive={false}
                      />
                    </ComposedChart>
                  </ResponsiveContainer>
                  {/* A small legend instead of labeling the reference line
                      inline on the chart — this data tends to sit flat and
                      close to its own minimum, so an inline label kept
                      landing right on top of the last data point. */}
                  <div className="flex items-center gap-4 text-[12px] font-semibold text-foreground -mt-1 mb-1">
                    <span className="flex items-center gap-1.5">
                      <span
                        className="w-3 h-0.5 rounded-full inline-block"
                        style={{ backgroundColor: dashboardData.forecast_source === "synthetic_model_stand_in" ? "var(--synthetic)" : "var(--role-accent)" }}
                      />
                      Projected total
                    </span>
                    {totalMinimumUnits > 0 && (
                      <span className="flex items-center gap-1.5">
                        <span className="w-3 h-0.5 rounded-full inline-block border-t-2 border-dashed border-gray-400" />
                        Combined minimum ({totalMinimumUnits})
                      </span>
                    )}
                  </div>
                  <p className="text-[12.5px] text-foreground leading-snug">
                    {hasInterval
                      ? `The shaded band is a ${Math.round((dashboardData.interval_confidence as number) * 100)}% prediction interval — wider with less history on file or further into the future, narrowing as more real days accumulate.`
                      : "Line shows the projected trend for total units on file."}
                  </p>
                  {dashboardData.alerts.length === 0 && (
                    <p className="mt-2 text-[14px] text-foreground">
                      {dashboardData.forecast_source === "synthetic_model_stand_in"
                        ? "No blood types currently trending toward shortage, based on the synthetic reference model."
                        : `No blood types currently trending toward shortage, based on ${dashboardData.days_of_history} days of history.`}
                    </p>
                  )}
                </>
                );
              })()}

              {/* Data provenance — moved below the chart/alert, since it's
                  a caveat and audit trail rather than the answer someone
                  came here for. */}
              <div className="mt-4 pt-4 border-t border-border">
                {!isRealForecastSource(dashboardData.forecast_source) ? (
                  <div className="rounded-lg border border-role-accent-border bg-primary-tint p-3.5">
                    <div className="flex items-center gap-2.5 mb-3">
                      <div className="w-8 h-8 rounded-lg bg-primary/10 flex items-center justify-center shrink-0">
                        <TrendingUp size={16} className="text-primary" />
                      </div>
                      <div>
                        <div className="text-[14px] font-bold text-foreground leading-tight">Unlock your real forecast</div>
                        <div className="text-[13px] text-muted-foreground leading-tight">
                          Have past stock records? Upload them to switch off the synthetic model now.
                        </div>
                      </div>
                    </div>

                    <div className="mb-3">
                      <div className="flex items-center justify-between text-[12px] font-bold text-muted-foreground uppercase tracking-wide mb-1">
                        <span>History on file</span>
                        <span className="font-mono tabular-nums normal-case">
                          {dashboardData.days_of_history} of {dashboardData.min_days_required} days
                        </span>
                      </div>
                      <div className="h-2.5 rounded-full bg-card border border-border overflow-hidden">
                        <div
                          className="h-full bg-primary rounded-full transition-all duration-500"
                          style={{ width: `${Math.min(100, (dashboardData.days_of_history / dashboardData.min_days_required) * 100)}%` }}
                        />
                      </div>
                    </div>

                    <button
                      onClick={() => historyFileInputRef.current?.click()}
                      disabled={uploadingHistory}
                      title="Columns: snapshot_date, blood_type, units. Dates must be before today — today's stock is recorded automatically."
                      className="w-full h-9 bg-primary text-white text-[13.5px] font-bold rounded-lg hover:bg-primary-hover transition-colors disabled:opacity-60 flex items-center justify-center gap-1.5"
                    >
                      <Upload size={14} /> {uploadingHistory ? "Uploading…" : "Upload Historical Data"}
                    </button>
                    <p className="text-[11.5px] text-muted-foreground mt-1.5 leading-snug">
                      CSV columns: snapshot_date, blood_type, units — one row per day per blood type, dated before today.
                    </p>
                  </div>
                ) : (
                  <div className="flex items-center justify-between gap-3 rounded-lg border border-status-safe-border bg-status-safe-tint px-3 py-2.5">
                    <div className="flex items-center gap-1.5 text-[13px] font-bold text-status-safe-text">
                      <CheckCircle size={14} />
                      {dashboardData.forecast_source === "sarimax_facility_history"
                        ? `Real SARIMAX forecast active — ${dashboardData.days_of_history} days of history on file`
                        : `Real forecast active — ${dashboardData.days_of_history} days of history on file`}
                    </div>
                    <button
                      onClick={() => historyFileInputRef.current?.click()}
                      disabled={uploadingHistory}
                      className="text-[12px] font-bold text-primary hover:underline hover:underline-offset-2 disabled:opacity-60 shrink-0"
                    >
                      {uploadingHistory ? "Uploading…" : "Add more history"}
                    </button>
                  </div>
                )}

                {historyUploadError && (
                  <div className="mt-3 text-[12px] text-status-critical-text bg-status-critical-tint border border-status-critical-border rounded-lg px-2.5 py-2">
                    {historyUploadError}
                  </div>
                )}
                {historyUploadResult && (
                  <div className="mt-3 text-[12px] bg-secondary rounded-lg px-2.5 py-2 space-y-1">
                    <div className="font-semibold text-foreground">
                      {historyUploadResult.rows_processed} day{historyUploadResult.rows_processed === 1 ? "" : "s"}-of-type
                      record{historyUploadResult.rows_processed === 1 ? "" : "s"} processed
                      {historyUploadResult.errors.length > 0 && `, ${historyUploadResult.errors.length} row${historyUploadResult.errors.length === 1 ? "" : "s"} skipped`}
                    </div>
                    <div className="text-muted-foreground">
                      Now {historyUploadResult.days_of_history} of {historyUploadResult.min_days_required} days of history on file.
                    </div>
                    {historyUploadResult.errors.map((e, i) => (
                      <div key={i} className="text-status-critical-text">
                        Row {e.row}: {e.reason}
                      </div>
                    ))}
                  </div>
                )}

                <div className="mt-3">
                  <UploadHistoryPanel uploadType="historical_stock" refreshKey={backfillUploadHistoryKey} onUndone={loadForecast} />
                </div>

                {dashboardData.forecast_source === "synthetic_model_stand_in" && (
                  <div className="mt-3 rounded-lg border border-synthetic-border bg-synthetic-tint px-3 py-2 text-[14px] text-synthetic-text leading-relaxed">
                    <strong>SYNTHETIC — not real data.</strong> Only {dashboardData.days_of_history} of{" "}
                    {dashboardData.min_days_required} days of this facility's own history have been collected, so this
                    trajectory comes from {dashboardData.synthetic_model_label ?? "a synthetic reference model"} trained on
                    generated data, rescaled to today's real stock. It will switch to a real facility-derived trend once
                    enough history accumulates.
                  </div>
                )}
              </div>
            </div>

            {/* Shortage Alert */}
            <div className="lg:col-span-1 bg-card border border-border rounded-xl p-6 flex flex-col">
              <div className="flex items-center gap-2 mb-4">
                <div className="w-7 h-7 rounded-lg bg-status-critical-tint flex items-center justify-center">
                  <Zap size={15} className="text-status-critical-text" />
                </div>
                <h3 className="font-semibold text-foreground text-[19px]">Predictive Shortage Alert</h3>
              </div>

              <div className="flex-1 flex flex-col">
                {forecastLoading && (
                  <div className="py-8 text-center text-[15px] text-foreground">Loading…</div>
                )}
                {!forecastLoading && forecastError && (
                  <div className="py-8 text-center text-[15px] text-status-critical-text">Failed to load: {forecastError}</div>
                )}
                {!forecastLoading && !forecastError && dashboardData.forecast_source === "none" && (
                  <div className="flex flex-col items-center text-center py-6">
                    <div className="w-12 h-12 rounded-full bg-secondary flex items-center justify-center mb-3">
                      <Clock size={22} className="text-muted-foreground" />
                    </div>
                    <div className="font-semibold text-foreground mb-1">Not enough data yet</div>
                    <p className="text-[14px] text-foreground leading-relaxed max-w-[240px]">
                      {dashboardData.days_of_history} of {dashboardData.min_days_required} days of history collected.
                      Shortage predictions require a real trend, not a guess.
                    </p>
                  </div>
                )}
                {!forecastLoading && !forecastError && dashboardData.forecast_source !== "none" && dashboardData.alerts.length === 0 && (
                  <div className="flex flex-col items-center text-center py-6">
                    <div className="w-12 h-12 rounded-full bg-status-safe-tint flex items-center justify-center mb-3">
                      <CheckCircle size={22} className="text-status-safe-text" />
                    </div>
                    <div className="font-semibold text-foreground mb-1">No shortages predicted</div>
                    <p className="text-[14px] text-foreground leading-relaxed max-w-[240px]">
                      {dashboardData.forecast_source === "synthetic_model_stand_in"
                        ? "Based on the synthetic reference model."
                        : `Based on a ${dashboardData.days_of_history}-day trend.`}
                    </p>
                  </div>
                )}
                {!forecastLoading && !forecastError && dashboardData.forecast_source !== "none" && dashboardData.alerts.length > 0 && (
                  <div className="space-y-3">
                    {dashboardData.alerts.map((alert, i) => {
                      const level: StatusLevel = alert.severity === "critical" ? "critical" : "watch";
                      return (
                        <div key={i} className={`rounded-lg p-3 flex gap-3 ${STATUS_STYLES[level].panel}`}>
                          <div className={`w-8 h-8 rounded-md font-display font-bold text-[15px] flex items-center justify-center shrink-0 ${STATUS_STYLES[level].solid}`}>
                            {alert.type}
                          </div>
                          <div>
                            <div className={`text-[15px] font-bold mb-0.5 ${STATUS_STYLES[level].text}`}>
                              {alert.severity === "critical" ? "Critical shortage likely" : "Shortage warning"}
                            </div>
                            <div className="text-[14px] text-foreground leading-snug">{alert.reason}</div>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            </div>
          </>
        )}

        {dashboardData?.view === "threshold_status" && (
          <div className="lg:col-span-2 self-start bg-card border border-border rounded-xl p-6 flex flex-col">
            <div className="flex items-center justify-between mb-4 gap-3">
              <div className="flex items-center gap-2">
                <div className="w-7 h-7 rounded-lg bg-status-critical-tint flex items-center justify-center">
                  <Zap size={15} className="text-status-critical-text" />
                </div>
                <h3 className="font-semibold text-foreground text-[19px]">Action Required</h3>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                {dashboardData.action_prompts.length > 0 ? (
                  <span className="flex items-center gap-1 text-[13px] font-bold text-status-critical-text bg-status-critical-tint px-2 py-0.5 rounded-full border border-status-critical-border">
                    <AlertTriangle size={13} /> {dashboardData.action_prompts.length} below minimum
                  </span>
                ) : (
                  <span className="flex items-center gap-1 text-[13px] font-bold text-status-safe-text bg-status-safe-tint px-2 py-0.5 rounded-full border border-status-safe-border">
                    <CheckCircle size={13} /> Fully stocked
                  </span>
                )}
                {dashboardData.action_prompts.length > VISIBLE_ITEM_LIMIT && (
                  <button
                    onClick={() => setShowAllActions((v) => !v)}
                    className="text-[13px] font-semibold text-status-critical-text hover:underline hover:underline-offset-2"
                  >
                    {showAllActions ? "Show less" : `Show ${dashboardData.action_prompts.length - VISIBLE_ITEM_LIMIT} more`}
                  </button>
                )}
              </div>
            </div>
            <p className="text-[15px] text-foreground mb-4">
              Current stock vs. minimum threshold, per type. Sending a request is never automatic — confirm each one yourself.
            </p>

            <div className="flex-1 flex flex-col">
              {forecastLoading && (
                <div className="py-8 text-center text-[15px] text-foreground">Loading…</div>
              )}
              {!forecastLoading && forecastError && (
                <div className="py-8 text-center text-[15px] text-status-critical-text">Failed to load: {forecastError}</div>
              )}
              {!forecastLoading && !forecastError && dashboardData.action_prompts.length === 0 && (
                <div className="flex flex-col items-center text-center py-6">
                  <div className="w-12 h-12 rounded-full bg-status-safe-tint flex items-center justify-center mb-3">
                    <CheckCircle size={22} className="text-status-safe-text" />
                  </div>
                  <div className="font-semibold text-foreground mb-1">All blood types fully stocked</div>
                  <p className="text-[14px] text-foreground leading-relaxed max-w-[280px]">
                    Every type is at or above its minimum threshold. No action needed right now.
                  </p>
                </div>
              )}
              {!forecastLoading && !forecastError && dashboardData.action_prompts.length > 0 && (() => {
                const visible = showAllActions
                  ? dashboardData.action_prompts
                  : dashboardData.action_prompts.slice(0, VISIBLE_ITEM_LIMIT);
                return (
                  <div className="space-y-3">
                    {visible.map((prompt) => (
                      <div key={prompt.blood_type} className="rounded-lg p-3 flex items-start gap-3 bg-status-critical-tint border border-status-critical-border">
                        <div className="w-8 h-8 rounded-md font-bold text-[15px] flex items-center justify-center shrink-0 bg-status-critical text-white">
                          {prompt.blood_type}
                        </div>
                        <div className="flex-1">
                          <div className="text-[15px] font-bold mb-0.5 text-status-critical-text">
                            {prompt.units} of {prompt.minimum_units} units — short by {prompt.deficit}
                          </div>
                          <div className="text-[14px] text-foreground leading-snug mb-2">{prompt.message}</div>
                          <button
                            onClick={() => onRequestBloodType(prompt.blood_type)}
                            className="flex items-center gap-1.5 h-8 px-3 bg-primary text-white rounded-lg text-[13px] font-semibold hover:bg-primary-hover transition-colors"
                          >
                            <ArrowRight size={14} /> Request {prompt.blood_type} from nearby blood banks
                          </button>
                        </div>
                      </div>
                    ))}
                  </div>
                );
              })()}
            </div>
          </div>
        )}

        {/* Expiry warnings */}
        <div className="lg:col-span-1 self-start bg-card border border-border rounded-xl p-6 flex flex-col">
          <div className="flex items-center justify-between mb-4 gap-3">
            <div className="flex items-center gap-2">
              <div className="w-7 h-7 rounded-lg bg-status-watch-tint flex items-center justify-center">
                <Clock size={15} className="text-status-watch" />
              </div>
              <h3 className="font-semibold text-foreground text-[19px]">Expiry Warnings</h3>
            </div>
            {expiringRows.length > VISIBLE_ITEM_LIMIT && (
              <button
                onClick={() => setShowAllExpiry((v) => !v)}
                className="text-[13px] font-semibold text-status-watch-text hover:underline hover:underline-offset-2 shrink-0"
              >
                {showAllExpiry ? "Show less" : `Show ${expiringRows.length - VISIBLE_ITEM_LIMIT} more`}
              </button>
            )}
          </div>
          <div className="flex-1 flex flex-col">
          {expiryLoading && (
            <div className="py-8 text-center text-[15px] text-foreground">Loading…</div>
          )}
          {!expiryLoading && expiryError && (
            <div className="py-8 text-center text-[15px] text-status-critical-text">Failed to load: {expiryError}</div>
          )}
          {!expiryLoading && !expiryError && expiringRows.length === 0 && (
            <div className="flex flex-col items-center text-center py-6">
              <div className="w-12 h-12 rounded-full bg-status-safe-tint flex items-center justify-center mb-3">
                <CheckCircle size={22} className="text-status-safe-text" />
              </div>
              <div className="font-semibold text-foreground mb-1">Nothing expiring soon</div>
              <p className="text-[14px] text-foreground leading-relaxed max-w-[220px]">
                No units are inside the near-expiry window right now.
              </p>
            </div>
          )}
          {!expiryLoading && !expiryError && expiringRows.length > 0 && (() => {
            const visible = showAllExpiry ? expiringRows : expiringRows.slice(0, VISIBLE_ITEM_LIMIT);
            return (
              <div className="space-y-2">
                {visible.map((row) => (
                  <ExpiryWarningRow key={row.din} row={row} />
                ))}
              </div>
            );
          })()}
          </div>
        </div>
      </div>
    </div>
  );
}
