import { useEffect, useRef, useState } from "react";
import { Bell, CheckCircle, ChevronDown, FlaskConical, TrendingUp, Upload } from "lucide-react";
import {
  Area, CartesianGrid, ComposedChart, Line, ReferenceArea, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import {
  getExpiringInventory, notifyNearbyHospitalsForGroup, uploadHistoricalInventorySnapshots,
  type ExpiringGroup, type ExpiringInventory, type HistoricalUploadResult,
} from "../lib/api";
import { BloodTypeBadge, StatusDot } from "../components/BloodTypeBadge";
import { ReasonTag } from "../components/ReasonTag";
import { Skeleton } from "../components/Skeleton";
import { StockBar } from "../components/StockBar";
import { UploadHistoryPanel } from "../components/UploadHistoryPanel";
import { type InventorySummaryRow } from "../lib/inventoryTypes";
import { isRealForecastSource, type ForecastView } from "../lib/forecastTypes";
import { STATUS_STYLES, STOCK_LABELS, isNotStocked, stockStatus } from "../lib/statusTokens";
import { buildTrendChart, shortDate, tickLabel, type TrendPoint } from "../lib/trendChart";
import { manilaToday } from "../lib/transfers";
import { requestUnitsText, type RequestRow } from "../lib/requestTypes";
import {
  basisFor, basisSummary, canGiveText, expiryLine, orderLabel, orderPrefill, outlookFor, sortedRows, statusWord, supplySentence,
  waitingRequests, type Outlook,
} from "../lib/bankDashboard";

// What a blood bank came for: what to do today. It holds a stock it manages rather than consumes, so the page is three
// queues in the order they matter: what to restock (and the order link that turns the forecast into an action), what other
// facilities are asking of it, and what is about to expire. Current stock is a measured fact and the outlook is an estimate,
// so they are separate columns, and each row says what its outlook rests on.
//
// Depth: exactly two raised surfaces, the stock and outlook table and "Waiting for you". Everything else is flat, with hairlines.
// Colour: the three stock states and neutrals; the outlook's basis is a neutral tag, not a state.

const RAISED = "shadow-[var(--shadow-raised)]";
const VISIBLE_WAITING = 5;
const VISIBLE_EXPIRING = 5;
const EXPIRY_POLL_MS = 25000;
const COLUMNS = "lg:grid-cols-[3.25rem_minmax(7.5rem,1.2fr)_4rem_8.5rem_6.5rem_minmax(11.5rem,1.6fr)]";
const plural = (n: number, one: string, many: string) => (n === 1 ? one : many);

export type BankDashboardProps = {
  summary: InventorySummaryRow[];
  summaryLoading: boolean;
  summaryError: string | null;
  forecast: ForecastView | null;
  forecastLoading: boolean;
  forecastError: string | null;
  onRetryForecast: () => void;
  onForecastChanged: () => void; // after a history upload or its undo, so the forecast is asked for again
  requests: RequestRow[] | null; // this bank's incoming requests
  requestsFailed: boolean;
  onOrder: (bloodType: string, quantity: number, component: string) => void;
  onViewRequests: () => void;
  onViewInventory: () => void;
};

export function BankDashboardView(p: BankDashboardProps) {
  const [expiring, setExpiring] = useState<ExpiringInventory | null>(null);
  const [expiringError, setExpiringError] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    const load = (first: boolean) =>
      getExpiringInventory()
        .then((d) => { if (!cancelled) { setExpiring(d); setExpiringError(null); } })
        .catch((e) => { if (!cancelled && first) setExpiringError(e instanceof Error ? e.message : "Failed to load"); }); // a failed refresh keeps what is shown
    load(true);
    const timer = setInterval(() => load(false), EXPIRY_POLL_MS);
    return () => { cancelled = true; clearInterval(timer); };
  }, []);

  const waiting = waitingRequests(p.requests);
  return (
    <div className="max-w-screen-2xl mx-auto px-4 sm:px-6 py-6 sm:py-8 space-y-8">
      <SupplyLine {...p} waiting={waiting} expiring={expiring} expiringError={expiringError} />

      {/* Phone: "Waiting for you" above the table. From lg the table is the wide left column and the requests sit beside it. */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-x-8 gap-y-8">
        <div className="order-2 lg:order-none lg:col-span-2 min-w-0">
          <StockAndOutlook {...p} />
        </div>
        <div className="order-1 lg:order-none lg:col-span-1 self-start">
          <WaitingForYou requests={waiting} failed={p.requestsFailed} onViewRequests={p.onViewRequests} />
        </div>
      </div>

      <ExpiringUnits data={expiring} error={expiringError} onViewInventory={p.onViewInventory} />
      <ForecastDetails {...p} />
    </div>
  );
}

// ─── The sentence ───────────────────────────────────────────────────────────

function SupplyLine(p: BankDashboardProps & { waiting: RequestRow[] | null; expiring: ExpiringInventory | null; expiringError: string | null }) {
  if (p.summaryLoading || (p.expiring === null && p.expiringError === null)) return <Skeleton className="h-6 w-full max-w-[44rem]" />;
  if (p.summaryError) return <p role="alert" className="text-[17px] text-status-critical-text">Failed to load supply status: {p.summaryError}</p>;
  const s = supplySentence({
    summary: p.summary,
    waiting: p.waiting === null ? null : p.waiting.length,
    expiring: p.expiring === null ? null : p.expiring.total_units,
    windowDays: p.expiring?.window_days ?? 7,
  });
  return (
    <p className={`font-display font-bold text-[22px] sm:text-[26px] leading-snug tracking-tight text-balance ${STATUS_STYLES[s.level].text}`}>
      {s.text}
    </p>
  );
}

// ─── Stock and outlook (raised) ─────────────────────────────────────────────

function StockAndOutlook(p: BankDashboardProps) {
  const rows = sortedRows(p.summary, p.forecast);
  const today = manilaToday();
  const pending = p.forecast?.pending_types ?? [];
  const summaryLine = basisSummary(p.forecast);
  const anyOrder = (p.forecast?.restock_recommendations.length ?? 0) > 0;

  return (
    <section aria-labelledby="stock-outlook-heading" className={`rounded-2xl bg-card p-5 sm:p-6 ${RAISED}`}>
      <h2 id="stock-outlook-heading" className="font-display font-bold text-xl tracking-tight text-foreground">
        Stock and outlook by blood type
      </h2>
      <p className="text-[14px] text-foreground mt-1 max-w-[70ch]">
        What you hold, and what you can give another facility: the units above each type's minimum that a search would find
        available. The outlook is a 30-day estimate, shown apart from the stock and with the method it rests on.
      </p>

      {p.summaryLoading && (
        <div className="mt-5 space-y-2" aria-busy="true">
          {Array.from({ length: 8 }).map((_, i) => <Skeleton key={i} className="h-11 w-full" />)}
        </div>
      )}
      {!p.summaryLoading && p.summaryError && <p role="alert" className="mt-5 text-[15px] text-status-critical-text">Failed to load inventory summary: {p.summaryError}</p>}

      {!p.summaryLoading && !p.summaryError && (
        <div role="table" aria-label="Stock and outlook by blood type" className="mt-4">
          <div role="row" className={`hidden lg:grid ${COLUMNS} items-end gap-x-4 pb-1.5 border-b border-gray-300 text-[13px] font-semibold text-gray-600`}>
            <div role="columnheader">Type</div>
            <div role="columnheader">Stock against minimum</div>
            <div role="columnheader" className="text-right">Units</div>
            <div role="columnheader">Status</div>
            <div role="columnheader" className="text-right">Can give</div>
            <div role="columnheader" className="pl-4">Outlook, next 30 days (estimate)</div>
          </div>
          {rows.map((r) => (
            <TypeRow
              key={r.blood_type}
              row={r}
              outlook={p.forecastLoading && p.forecast === null ? null : outlookFor(r.blood_type, p.forecast)}
              basis={basisFor(p.forecast, r.blood_type)}
              today={today}
              onOrder={p.onOrder}
              forecast={p.forecast}
            />
          ))}
        </div>
      )}

      {/* What the outlook column rests on, and anything that is not final yet. */}
      {!p.summaryLoading && !p.summaryError && (
        <div className="mt-4 space-y-1.5 text-[14px] text-foreground">
          {p.forecastError && !p.forecast && (
            <p role="alert" className="text-status-critical-text">
              The outlook didn't load. Your stock above is unaffected.{" "}
              <button onClick={p.onRetryForecast} className="font-semibold underline underline-offset-2">Retry</button>
            </p>
          )}
          {pending.length > 0 && p.forecast && (
            <p role="status">
              Still calculating {pending.length} of {p.forecast.types_total} blood types ({pending.join(", ")}). This updates by itself.
            </p>
          )}
          {summaryLine && <p>Outlook basis: {summaryLine} Details are under Forecast details.</p>}
          {p.forecast?.forecast_source === "synthetic_model_stand_in" && (
            <p className="text-synthetic-text font-semibold">
              This outlook comes from a reference model, not this facility's own history yet. See Forecast details.
            </p>
          )}
          {p.forecast?.forecast_source === "none" && (
            <p>
              Collecting data: {p.forecast.days_of_history} of {p.forecast.min_days_required} days of daily stock records are on file. The
              outlook starts once there are enough.
            </p>
          )}
          {anyOrder && p.forecast?.is_dengue_season && (
            <p>Coincides with dengue season, a period of historically elevated demand in this region.</p>
          )}
        </div>
      )}
    </section>
  );
}

// One blood type. From lg the cells are grid columns in DOM order (type, bar, units, status, can give, outlook); below it they
// wrap into two lines by `order-N`: type, bar, units and what can be given; then the status and the outlook.
function TypeRow({
  row, outlook, basis, today, onOrder, forecast,
}: {
  row: InventorySummaryRow;
  outlook: Outlook | null; // null: the forecast has not answered yet
  basis: { text: string; title: string } | null;
  today: string;
  onOrder: BankDashboardProps["onOrder"];
  forecast: ForecastView | null;
}) {
  const level = stockStatus(row.units, row.minimum_units);
  const notStocked = isNotStocked(row.units, row.minimum_units);
  const give = canGiveText(row);
  return (
    <div
      role="row"
      className={`flex flex-wrap items-center gap-x-4 gap-y-1.5 py-3 lg:py-2 lg:min-h-[3.25rem] lg:grid ${COLUMNS} lg:gap-x-4 border-b border-gray-200 last:border-b-0`}
    >
      <div role="cell" className="order-1 lg:order-none"><BloodTypeBadge type={row.blood_type} size="md" /></div>
      <div role="cell" className="order-2 lg:order-none flex-1 min-w-[6rem]"><StockBar units={row.units} minimum={row.minimum_units} maximum={row.maximum_units} /></div>
      <div role="cell" className={`order-3 lg:order-none text-right text-[16px] font-bold tabular-nums ${level === "safe" ? "text-foreground" : STATUS_STYLES[level].text}`}>
        {row.units}
        <span className="sr-only"> {plural(row.units, "unit", "units")}, minimum {row.minimum_units}</span>
      </div>
      <div aria-hidden="true" className="order-5 basis-full h-0 lg:hidden" />
      <div role="cell" className="order-6 lg:order-none shrink-0 flex items-center gap-2 text-[14px] font-semibold">
        {notStocked ? (
          <span className="text-foreground">Not stocked</span>
        ) : (
          <>
            <StatusDot status={level} />
            <span className={STATUS_STYLES[level].text}>{statusWord(row, STOCK_LABELS)}</span>
          </>
        )}
      </div>
      <div role="cell" className="order-4 lg:order-none lg:text-right text-[14px] text-foreground tabular-nums whitespace-nowrap">
        <span className="lg:hidden text-gray-600">Can give </span>
        {give === null ? "—" : <span className="font-semibold">{give.main}</span>}
        {give?.extra && <div className="text-[12.5px] text-foreground lg:text-right">{give.extra}</div>}
      </div>
      <div role="cell" className="order-7 flex-1 min-w-[9rem] lg:flex-none lg:min-w-0 lg:order-none lg:pl-4 lg:border-l lg:border-gray-200">
        <OutlookCell blood_type={row.blood_type} outlook={outlook} basis={basis} today={today} onOrder={onOrder} forecast={forecast} />
      </div>
    </div>
  );
}

function OutlookCell({
  blood_type, outlook, basis, today, onOrder, forecast,
}: {
  blood_type: string; outlook: Outlook | null; basis: { text: string; title: string } | null; today: string;
  onOrder: BankDashboardProps["onOrder"]; forecast: ForecastView | null;
}) {
  if (outlook === null) return <Skeleton className="h-9 w-40" />;
  return (
    <div className="text-[14px] leading-snug">
      {outlook.kind === "order" && (
        <>
          <button
            type="button"
            title={outlook.sentence}
            aria-label={`${orderLabel(outlook, today)} ${blood_type} Packed RBC. Opens Source supply with these filled in.`}
            onClick={() => { const t = orderPrefill(blood_type, outlook); onOrder(t.bloodType, t.quantity, t.component); }}
            className="font-semibold text-primary text-left rounded-sm hover:underline hover:underline-offset-2"
          >
            {orderLabel(outlook, today)}
          </button>
          {outlook.projectedLow !== null && <div className="text-foreground tabular-nums">Projected low {outlook.projectedLow}</div>}
        </>
      )}
      {outlook.kind === "on_track" && <span className="font-semibold text-status-safe-text">On track</span>}
      {outlook.kind === "calculating" && <span className="font-semibold text-foreground">Calculating</span>}
      {outlook.kind === "collecting" && (
        <span className="text-foreground">
          <span className="font-semibold">Collecting data</span> <span className="tabular-nums">({outlook.have} of {outlook.need} days)</span>
        </span>
      )}
      {outlook.kind === "unavailable" && <span className="text-gray-600">Not available</span>}
      {basis && forecast && (
        <span title={basis.title} className="mt-1 block w-fit max-w-full rounded-full border border-border bg-secondary px-2 py-px text-[12px] font-semibold text-foreground">
          {basis.text}
        </span>
      )}
    </div>
  );
}

// ─── Waiting for you (raised) ───────────────────────────────────────────────

function WaitingForYou({ requests, failed, onViewRequests }: { requests: RequestRow[] | null; failed: boolean; onViewRequests: () => void }) {
  const visible = requests?.slice(0, VISIBLE_WAITING) ?? [];
  return (
    <aside aria-labelledby="waiting-heading" className={`rounded-2xl bg-card p-5 sm:p-6 ${RAISED}`}>
      <h2 id="waiting-heading" className="font-display font-bold text-xl tracking-tight text-foreground">Waiting for you</h2>
      <p className="text-[14px] text-foreground mt-1 mb-4">Requests from other facilities for you to accept or decline, most urgent first.</p>
      {requests === null && !failed && (
        <div className="space-y-4" aria-busy="true">
          {[0, 1].map((i) => <Skeleton key={i} className="h-12 w-full" />)}
        </div>
      )}
      {requests === null && failed && <p role="alert" className="text-[14px] text-status-critical-text">Couldn't load your requests. Open the incoming list to see them.</p>}
      {requests !== null && requests.length === 0 && <p className="text-[15px] text-foreground">Nothing is waiting for a decision.</p>}
      {requests !== null && requests.length > 0 && (
        <ol>
          {visible.map((req, i) => (
            <li key={req.id} className="flex gap-3 py-3 border-t border-gray-200 first:border-t-0 first:pt-0">
              <span aria-label={`Position ${i + 1}`} className="w-6 h-6 rounded-full bg-primary-tint text-primary text-[12px] font-bold flex items-center justify-center shrink-0 mt-0.5">{i + 1}</span>
              <div className="min-w-0 flex-1">
                <div className="text-[15px] font-semibold text-foreground">{req.requesting_facility_name ?? `Facility ${req.requesting_facility_id}`}</div>
                <div className="mt-1 flex flex-wrap items-center gap-x-2.5 gap-y-1 text-[14px] text-foreground">
                  <BloodTypeBadge type={req.blood_type} size="sm" />
                  <span>{requestUnitsText(req)}</span>
                  <ReasonTag reason={req.emergency_type} />
                </div>
              </div>
            </li>
          ))}
        </ol>
      )}
      {requests !== null && requests.length > VISIBLE_WAITING && (
        <p className="mt-1 text-[14px] text-foreground">and {requests.length - VISIBLE_WAITING} more.</p>
      )}
      <button onClick={onViewRequests} className="mt-4 text-[14px] font-semibold text-primary rounded-sm hover:underline hover:underline-offset-2">
        Open the incoming list
      </button>
    </aside>
  );
}

// ─── Expiring units (flat) ──────────────────────────────────────────────────

function ExpiringUnits({ data, error, onViewInventory }: { data: ExpiringInventory | null; error: string | null; onViewInventory: () => void }) {
  const [showAll, setShowAll] = useState(false);
  const groups = data?.groups ?? [];
  const visible = showAll ? groups : groups.slice(0, VISIBLE_EXPIRING);
  return (
    <section aria-labelledby="expiring-heading">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h2 id="expiring-heading" className="font-display font-bold text-xl tracking-tight text-foreground">
          Expiring within {data?.window_days ?? 7} days
        </h2>
        <button onClick={onViewInventory} className="text-[14px] font-semibold text-primary rounded-sm hover:underline hover:underline-offset-2">
          Review in inventory
        </button>
      </div>
      <div className="mt-3">
        {data === null && error === null && <Skeleton className="h-14 w-full" />}
        {error && data === null && <p role="alert" className="text-[15px] text-status-critical-text">Failed to load: {error}</p>}
        {data !== null && groups.length === 0 && <p className="text-[15px] text-foreground">No units expire within {data.window_days} days.</p>}
        {visible.map((g) => <ExpiringGroupRow key={`${g.expires_date}-${g.blood_type}-${g.component}`} group={g} />)}
        {groups.length > VISIBLE_EXPIRING && (
          <button onClick={() => setShowAll((v) => !v)} className="mt-2 text-[14px] font-semibold text-primary rounded-sm hover:underline hover:underline-offset-2">
            {showAll ? "Show fewer" : `Show ${groups.length - VISIBLE_EXPIRING} more`}
          </button>
        )}
      </div>
    </section>
  );
}

// One group: all the units of a blood type and component that expire the same day, with one notify action for the lot.
function ExpiringGroupRow({ group }: { group: ExpiringGroup }) {
  const [state, setState] = useState<"idle" | "sending" | "sent" | "error">("idle");
  const [alerted, setAlerted] = useState(0);
  const [error, setError] = useState<string | null>(null);
  async function notify() {
    setState("sending");
    setError(null);
    try {
      const r = await notifyNearbyHospitalsForGroup(group);
      setAlerted(r.notified_count);
      setState("sent");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to notify nearby hospitals");
      setState("error");
    }
  }
  const urgent = group.days_left <= 3;
  return (
    <div className="py-3 border-t border-gray-200 first:border-t-0 first:pt-0 flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
      <div className="flex items-center gap-3 min-w-0">
        <BloodTypeBadge type={group.blood_type} size="sm" />
        <span className={`text-[15px] ${urgent ? "font-semibold text-status-critical-text" : "text-foreground"}`}>{expiryLine(group)}</span>
      </div>
      {state === "sent" ? (
        <span className="flex items-center gap-1.5 text-[13px] font-semibold text-status-safe-text">
          <CheckCircle size={14} className="shrink-0" /> {alerted > 0 ? `${alerted} nearby ${plural(alerted, "hospital", "hospitals")} alerted` : "No nearby hospitals to alert"}
        </span>
      ) : (
        <button
          onClick={notify}
          disabled={state === "sending"}
          className="h-9 px-3 rounded-md border border-border bg-card text-[13px] font-semibold text-foreground hover:bg-secondary transition-colors disabled:opacity-60 inline-flex items-center gap-1.5"
        >
          <Bell size={13} className="shrink-0" aria-hidden="true" />
          {state === "sending" ? "Notifying…" : "Notify nearby hospitals"}
        </button>
      )}
      {state === "error" && <div role="alert" className="basis-full text-[13px] text-status-critical-text">{error}</div>}
    </div>
  );
}

// ─── Forecast details (a disclosure) ────────────────────────────────────────

function TrendTooltip({ active, payload, todayISO }: { active?: boolean; payload?: { payload: TrendPoint }[]; todayISO: string | null }) {
  if (!active || !payload || payload.length === 0) return null;
  const d = payload[0].payload;
  const projected = d.forecastUnits !== null && d.t > 0;
  const heading = d.t === 0 ? "Today" : d.date ? shortDate(d.date) : tickLabel(d.t, todayISO);
  return (
    <div className="bg-card border border-border rounded-md px-3 py-2 text-[13px] shadow-sm">
      <div className="font-bold text-foreground mb-0.5">{projected && d.label ? `${heading} (${d.label})` : heading}</div>
      <div className="text-foreground">{d.units} units</div>
      {projected && d.lower !== null && d.band !== null && (
        <div className="text-foreground text-[12px]">Likely range: {d.lower}–{d.lower + d.band}</div>
      )}
      {d.recorded === true && <div className="text-foreground text-[12px]">Recorded</div>}
      {d.recorded === false && (
        <div className="text-foreground text-[12px]">Not recorded{d.carriedFrom ? `, carried forward from ${shortDate(d.carriedFrom)}` : ""}</div>
      )}
    </div>
  );
}

// Everything that is not a daily decision: the trend chart, what the forecast rests on, and the one-time upload of past stock
// records with its undo. Closed by default.
function ForecastDetails(p: BankDashboardProps) {
  const [open, setOpen] = useState(false);
  return (
    <section aria-labelledby="forecast-details-heading" className="border-t border-gray-300 pt-4">
      <h2 id="forecast-details-heading" className="font-display font-bold text-xl tracking-tight text-foreground">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          className="flex items-center gap-2 rounded-sm hover:underline hover:underline-offset-2"
        >
          <ChevronDown size={18} aria-hidden="true" className={`transition-transform ${open ? "" : "-rotate-90"}`} />
          Forecast details
        </button>
      </h2>
      {open && <ForecastDetailsBody {...p} />}
    </section>
  );
}

function ForecastDetailsBody(p: BankDashboardProps) {
  const f = p.forecast;
  const [showInterval, setShowInterval] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [result, setResult] = useState<HistoricalUploadResult | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [historyKey, setHistoryKey] = useState(0);

  async function onFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploading(true);
    setUploadError(null);
    setResult(null);
    try {
      setResult(await uploadHistoricalInventorySnapshots(file));
      p.onForecastChanged();
      setHistoryKey((k) => k + 1);
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  const real = f !== null && isRealForecastSource(f.forecast_source);
  const hasTrend = f !== null && (f.series.length > 0 || (f.history?.length ?? 0) > 0);
  const totalMinimum = p.summary.reduce((a, b) => a + b.minimum_units, 0);
  const summaryLine = basisSummary(f);

  return (
    <div className="mt-4 space-y-8">
      {f === null && <p className="text-[15px] text-foreground">{p.forecastLoading ? "Loading the forecast…" : "The forecast is not available right now."}</p>}

      {f !== null && (
        <div className="space-y-1.5 text-[15px] text-foreground max-w-[75ch]">
          <p>
            {f.forecast_source === "none" && `Collecting data: ${f.days_of_history} of ${f.min_days_required} days of daily stock records are on file.`}
            {f.forecast_source === "synthetic_model_stand_in" && "The outlook above comes from a reference model, not this facility's own history yet."}
            {real && `Built from ${f.days_of_history} days of this facility's recorded stock. These are estimates, not guarantees.`}
          </p>
          {summaryLine && <p>{summaryLine}</p>}
          {f.forecast_source === "synthetic_model_stand_in" && (
            <p className="rounded-md border border-synthetic-border bg-synthetic-tint px-3 py-2 text-synthetic-text">
              <FlaskConical size={14} className="inline mr-1.5 -mt-0.5" aria-hidden="true" />
              <strong>Synthetic, not real data.</strong> Only {f.days_of_history} of {f.min_days_required} days of this facility's own history have been collected,
              so this trajectory comes from {f.synthetic_model_label ?? "a synthetic reference model"} trained on generated data, rescaled to today's real stock. It
              switches to a trend from your own history once enough has accumulated.
            </p>
          )}
        </div>
      )}

      {f !== null && hasTrend && (() => {
        const hasInterval = typeof f.interval_confidence === "number";
        const isStandIn = f.forecast_source === "synthetic_model_stand_in";
        const chart = buildTrendChart({ history: f.history, series: f.series, standIn: isStandIn });
        const forecastColor = isStandIn ? "var(--synthetic)" : "var(--role-accent)";
        const historyColor = chart.historyTone === "neutral" ? "var(--gray-500)" : "var(--role-accent)";
        const dotsOnly = chart.mode === "dots";
        const widestRun = chart.filledRuns.reduce<{ from: number; to: number } | null>((best, r) => (best === null || r.to - r.from > best.to - best.from ? r : best), null);
        return (
          <div>
            <h3 className="text-[15px] font-semibold text-foreground mb-1 flex items-center gap-1.5"><TrendingUp size={15} aria-hidden="true" /> Total stock, recorded and projected</h3>
            <p className="text-[14px] text-foreground mb-2 max-w-[75ch]">
              Total units across every blood type combined. A steady total doesn't mean every type is steady; see the table above.
            </p>
            {!chart.hasForecast && <p className="text-[14px] font-semibold text-foreground mb-2">The forecast is still calculating. Recorded stock so far is shown.</p>}
            <ResponsiveContainer width="100%" height={220}>
              <ComposedChart data={chart.points} margin={{ top: 8, right: chart.hasForecast ? 8 : 22, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" strokeOpacity={0.35} vertical={false} />
                <XAxis
                  type="number" dataKey="t" domain={chart.domain} ticks={chart.ticks} interval={0}
                  tickFormatter={(t: number) => tickLabel(t, chart.todayISO)}
                  tick={{ fontSize: 12, fill: "var(--foreground)", fontFamily: "var(--font-body)", fontWeight: 700 }} axisLine={false} tickLine={false}
                />
                <YAxis tick={{ fontSize: 11, fill: "var(--foreground)", fontFamily: "var(--font-body)", fontWeight: 700 }} axisLine={false} tickLine={false} width={34} />
                <Tooltip content={<TrendTooltip todayISO={chart.todayISO} />} cursor={{ stroke: "var(--border)", strokeDasharray: "3 3" }} />
                {chart.filledRuns.map((r) => (
                  <ReferenceArea
                    key={r.from} x1={r.from} x2={r.to} fill="var(--gray-400)" fillOpacity={0.18} stroke="none" ifOverflow="hidden"
                    label={r === widestRun ? { value: "Not recorded", position: "insideBottom", fontSize: 11, fontWeight: 700, fill: "var(--foreground)" } : undefined}
                  />
                ))}
                {chart.minimumSegment && totalMinimum > 0 && (
                  <ReferenceLine
                    segment={[{ x: chart.minimumSegment[0], y: totalMinimum }, { x: chart.minimumSegment[1], y: totalMinimum }]}
                    stroke="var(--gray-400)" strokeWidth={1.5} strokeDasharray="4 4"
                  />
                )}
                {hasInterval && chart.hasForecast && (
                  <>
                    <Area dataKey="lower" stackId="ci" stroke="none" fill="transparent" isAnimationActive={false} />
                    <Area dataKey="band" stackId="ci" stroke={forecastColor} strokeWidth={1} strokeOpacity={0.35} fill={forecastColor} fillOpacity={0.3} isAnimationActive={false} />
                  </>
                )}
                {!dotsOnly && (
                  <Line type="linear" dataKey="filledUnits" stroke="var(--gray-500)" strokeWidth={2} strokeDasharray="5 4" dot={false} activeDot={false} connectNulls={false} isAnimationActive={false} />
                )}
                <Line
                  type="linear" dataKey="observedUnits" stroke={dotsOnly ? "none" : historyColor} strokeWidth={2.5}
                  dot={{ r: dotsOnly ? 4 : 0, strokeWidth: 0, fill: historyColor }} activeDot={{ r: 4.5, fill: historyColor }} connectNulls={false} isAnimationActive={false}
                />
                <Line type="monotone" dataKey="forecastUnits" stroke={forecastColor} strokeWidth={3} dot={{ r: 3.5, strokeWidth: 0, fill: forecastColor }} activeDot={{ r: 5 }} isAnimationActive={false} />
              </ComposedChart>
            </ResponsiveContainer>
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[13px] font-semibold text-foreground mt-1 mb-1">
              {chart.observedCount > 0 && <span className="flex items-center gap-1.5"><span className="w-3 h-0.5 rounded-full inline-block" style={{ backgroundColor: historyColor }} /> Recorded</span>}
              {chart.filledRuns.length > 0 && (
                <span className="flex items-center gap-1.5">
                  <span className="w-3 h-3 inline-block rounded-sm border border-dashed border-gray-500" style={{ backgroundColor: "color-mix(in srgb, var(--gray-400) 18%, transparent)" }} /> Not recorded, carried forward
                </span>
              )}
              {chart.hasForecast && <span className="flex items-center gap-1.5"><span className="w-3 h-0.5 rounded-full inline-block" style={{ backgroundColor: forecastColor }} /> Projected total</span>}
              {chart.hasForecast && totalMinimum > 0 && (
                <span className="flex items-center gap-1.5"><span className="w-3 h-0.5 rounded-full inline-block border-t-2 border-dashed border-gray-400" /> Combined minimum ({totalMinimum})</span>
              )}
            </div>
            {dotsOnly && chart.observedCount > 0 && (
              <p className="text-[13px] text-foreground leading-snug mb-1">
                Only {chart.observedCount} {plural(chart.observedCount, "day", "days")} of stock {plural(chart.observedCount, "has", "have")} been recorded so far, so just{" "}
                {chart.observedCount === 1 ? "that day is" : "those days are"} shown. No line is drawn through the days nobody recorded.
              </p>
            )}
            {chart.hasForecast && (
              <p className="text-[13px] text-foreground leading-snug flex items-start gap-1.5">
                <span>{hasInterval ? "The shaded band after Today shows the range this could realistically fall in." : "Line shows the projected trend for total units on file."}</span>
                {hasInterval && (
                  <button
                    type="button" onClick={() => setShowInterval((v) => !v)} aria-expanded={showInterval}
                    aria-label="What does the shaded band mean, technically?" title="What does the shaded band mean, technically?"
                    className="shrink-0 w-4 h-4 rounded-full border border-border text-[10px] font-bold text-muted-foreground hover:text-foreground hover:border-foreground flex items-center justify-center leading-none"
                  >
                    ?
                  </button>
                )}
              </p>
            )}
            {hasInterval && chart.hasForecast && showInterval && (
              <p className="text-[12.5px] text-muted-foreground leading-snug mt-1">
                Technically, this is a {Math.round((f.interval_confidence as number) * 100)}% prediction interval: wider with less history on file or further into the
                future, narrowing as more real days accumulate.
              </p>
            )}
          </div>
        );
      })()}

      {/* The one-time setup: past stock records bring the real forecast forward. Not a daily decision, so it lives here. */}
      <div className="border-t border-gray-200 pt-6 max-w-[75ch]">
        <h3 className="text-[15px] font-semibold text-foreground">Historical stock upload</h3>
        <p className="text-[14px] text-foreground mt-1">
          Have past stock records? Upload them to build the forecast from your own history sooner. A CSV of snapshot_date, blood_type and units, one row
          per day per blood type, dated before today. Today's stock is recorded automatically.
        </p>
        {f !== null && !real && (
          <div className="mt-3">
            <div className="flex items-center justify-between text-[13px] text-foreground mb-1">
              <span>History on file</span>
              <span className="tabular-nums">{f.days_of_history} of {f.min_days_required} days</span>
            </div>
            <div className="h-2.5 rounded-full bg-secondary border border-border overflow-hidden">
              <div className="h-full bg-primary rounded-full" style={{ width: `${Math.min(100, (f.days_of_history / f.min_days_required) * 100)}%` }} />
            </div>
          </div>
        )}
        <input ref={fileRef} type="file" accept=".csv" onChange={onFile} className="hidden" />
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <button
            onClick={() => fileRef.current?.click()}
            disabled={uploading}
            className="h-9 px-3 rounded-md border border-border bg-card text-[13px] font-semibold text-foreground hover:bg-secondary transition-colors disabled:opacity-60 inline-flex items-center gap-1.5"
          >
            <Upload size={14} aria-hidden="true" /> {uploading ? "Uploading…" : real ? "Add more history" : "Upload historical data"}
          </button>
          {real && f !== null && <span className="text-[14px] text-foreground">{f.days_of_history} days of history on file.</span>}
        </div>
        {uploadError && <div role="alert" className="mt-3 text-[13px] text-status-critical-text bg-status-critical-tint border border-status-critical-border rounded-md px-3 py-2">{uploadError}</div>}
        {result && (
          <div className="mt-3 text-[13px] bg-secondary rounded-md px-3 py-2 space-y-1">
            <div className="font-semibold text-foreground">
              {result.rows_processed} {plural(result.rows_processed, "day-of-type record", "day-of-type records")} processed
              {result.errors.length > 0 && `, ${result.errors.length} ${plural(result.errors.length, "row", "rows")} skipped`}
            </div>
            <div className="text-foreground">Now {result.days_of_history} of {result.min_days_required} days of history on file.</div>
            {result.errors.map((e, i) => <div key={i} className="text-status-critical-text">Row {e.row}: {e.reason}</div>)}
          </div>
        )}
        <div className="mt-4">
          <UploadHistoryPanel uploadType="historical_stock" refreshKey={historyKey} onUndone={p.onForecastChanged} variant="flat" />
        </div>
      </div>
    </div>
  );
}
