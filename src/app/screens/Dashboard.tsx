import { useState, useRef, useEffect } from "react";
import { apiGet } from "../lib/api";
import { getExpiryStatus } from "../lib/statusTokens";
import { getCurrentUser } from "../lib/session";
import { isDevModeEnabled } from "../lib/devMode";
import { type InventoryUnit, type InventoryApiRow, type InventorySummaryRow, toInventoryUnit } from "../lib/inventoryTypes";
import { HospitalDashboardView, type ThresholdView } from "./HospitalDashboard";
import { BankDashboardView } from "./BankDashboard";
import { type RequestRow } from "../lib/requestTypes";
import { type ForecastView } from "../lib/forecastTypes";
import { liveSync } from "../lib/liveSyncRuntime";

// No timers here: the summary, the hospital's unit list and the request list reload when the shared poller (lib/liveSync.ts) says
// their token moved, about 15 s after another facility changes something and never while the tab is hidden. The forecast is not
// polled at all: it is cached per Manila day and loaded once (and again, by its own rules, while types are still pending).

type DashboardData = ForecastView | ThresholdView;

// ─── Dashboard ───────────────────────────────────────────────────────────────
// The data both facility types share (the stock summary, /forecast, the request list) is loaded here, and each type has its
// own view: HospitalDashboardView for a hospital's threshold view, BankDashboardView for a blood bank's forecast view. The
// server decides which one once /forecast has answered (it follows the acting facility, which ?dev=1 can override).

export function DashboardScreen({
  onRequestBloodType, onViewRequests, onViewInventory,
}: {
  onRequestBloodType: (bloodType: string, quantity?: number, component?: string) => void;
  onViewRequests: () => void;
  onViewInventory: () => void;
}) {
  const [summary, setSummary] = useState<InventorySummaryRow[]>([]);
  const [summaryLoading, setSummaryLoading] = useState(true);
  const [summaryError, setSummaryError] = useState<string | null>(null);

  // A hospital's expiring units come from the full unit list. A blood bank's come grouped from /inventory/expiring (see
  // BankDashboardView), so it does not load every unit just to find the few that expire.
  const [expiryUnits, setExpiryUnits] = useState<InventoryUnit[]>([]);
  const [expiryLoading, setExpiryLoading] = useState(true);
  const [expiryError, setExpiryError] = useState<string | null>(null);

  const [dashboardData, setDashboardData] = useState<DashboardData | null>(null);
  const [forecastLoading, setForecastLoading] = useState(true);
  const [forecastError, setForecastError] = useState<string | null>(null);
  const FORECAST_PENDING_POLL_MS = 2000;

  // The requests this facility is party to: a hospital's own outgoing ones, a blood bank's incoming ones. null until
  // the first answer; requestsFailed says that answer never came.
  const [requestRows, setRequestRows] = useState<RequestRow[] | null>(null);
  const [requestsFailed, setRequestsFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    // latestRequestId guards against response reordering, not just unmount: a slow initial request (Render's cold start)
    // can resolve AFTER a later refresh already landed, and would silently revert the numbers.
    let latestRequestId = 0;
    // Silent background refresh (no loading flag after the first load) so the numbers reflect changes made by other facilities.
    function loadSummary(isFirstLoad: boolean) {
      const requestId = ++latestRequestId;
      apiGet<InventorySummaryRow[]>("/inventory/summary")
        .then((data) => { if (!cancelled && requestId === latestRequestId) setSummary(data); })
        .catch((err) => { if (!cancelled && isFirstLoad) setSummaryError(err instanceof Error ? err.message : "Failed to load"); })
        .finally(() => { if (!cancelled && isFirstLoad) setSummaryLoading(false); });
    }
    const unsubscribe = liveSync.subscribe("inventory", () => loadSummary(false)); // before the first fetch: see lib/liveSync.ts
    loadSummary(true);
    return () => { cancelled = true; unsubscribe(); };
  }, []);

  useEffect(() => {
    // The account's own type is the best guess before /forecast answers; under the dev override it can be wrong, so load then.
    if (!isDevModeEnabled() && getCurrentUser()?.facility_type === "bloodbank") {
      setExpiryLoading(false);
      return;
    }
    let cancelled = false;
    const load = (first: boolean) =>
      apiGet<InventoryApiRow[]>("/inventory")
        .then((data) => { if (!cancelled) { setExpiryUnits(data.map(toInventoryUnit)); setExpiryError(null); } })
        .catch((err) => { if (!cancelled && first) setExpiryError(err instanceof Error ? err.message : "Failed to load"); })
        .finally(() => { if (!cancelled) setExpiryLoading(false); });
    const unsubscribe = liveSync.subscribe("inventory", () => load(false)); // a hospital's expiring list follows its stock
    load(true);
    return () => { cancelled = true; unsubscribe(); };
  }, []);

  // Shared across every call site (initial mount, post-upload, undo) so a slow earlier request can never clobber a faster
  // later one.
  const forecastRequestIdRef = useRef(0);
  // silent = a follow-up poll for still-pending blood types: keep showing what we have instead of flipping back to "Loading…".
  function loadForecast(silent = false) {
    const requestId = ++forecastRequestIdRef.current;
    if (!silent) setForecastLoading(true);
    setForecastError(null);
    // Shared with any other caller and kept for 30 s, except an answer that still lists pending types: that one is asked for
    // again (see below) and must never be served from memory. The user's own writes (uploads, undo) clear it.
    apiGet<DashboardData>("/forecast", undefined, {
      ttlMs: 30_000,
      cacheIf: (d) => d.view !== "forecast" || (d.pending_types?.length ?? 0) === 0,
    })
      .then((data) => { if (requestId === forecastRequestIdRef.current) setDashboardData(data); })
      .catch((err) => { if (requestId === forecastRequestIdRef.current) setForecastError(err instanceof Error ? err.message : "Failed to load"); })
      .finally(() => { if (requestId === forecastRequestIdRef.current) setForecastLoading(false); });
  }
  useEffect(() => { loadForecast(); }, []);

  // The server fits only a couple of blood types per request and lists the rest as pending; ask again (after the previous
  // answer, never overlapping) until none are.
  const pendingCount = dashboardData?.view === "forecast" ? (dashboardData.pending_types?.length ?? 0) : 0;
  useEffect(() => {
    if (pendingCount === 0 || forecastLoading || forecastError) return;
    const timer = setTimeout(() => loadForecast(true), FORECAST_PENDING_POLL_MS);
    return () => clearTimeout(timer);
  }, [dashboardData, forecastLoading, forecastError]);

  useEffect(() => {
    if (!dashboardData?.view) return;
    let cancelled = false;
    // Hospitals see their own outgoing requests; blood banks the requests directed at them as the supplier.
    const endpoint = dashboardData.view === "threshold_status" ? "/requests" : "/requests/incoming";
    function load() {
      apiGet<RequestRow[]>(endpoint)
        .then((rows) => { if (!cancelled) { setRequestRows(rows); setRequestsFailed(false); } })
        .catch(() => { if (!cancelled) setRequestsFailed(true); }); // a failed refresh keeps what is already on screen
    }
    // Both sides' request lists change while the page is open: a hospital's statuses, a bank's waiting requests.
    const unsubscribe = liveSync.subscribe(dashboardData.view === "threshold_status" ? "requestsOutgoing" : "requestsIncoming", load);
    load();
    return () => { cancelled = true; unsubscribe(); };
  }, [dashboardData?.view]);

  const expiringRows = expiryUnits.filter((r) => {
    const status = getExpiryStatus(r.daysLeft);
    return status === "critical" || status === "near-expiry";
  });

  const isHospitalView = dashboardData
    ? dashboardData.view === "threshold_status"
    : getCurrentUser()?.facility_type === "hospital";
  if (isHospitalView) {
    return (
      <HospitalDashboardView
        summary={summary}
        summaryLoading={summaryLoading}
        summaryError={summaryError}
        expiringRows={expiringRows}
        expiryLoading={expiryLoading}
        expiryError={expiryError}
        thresholdView={dashboardData?.view === "threshold_status" ? dashboardData : null}
        forecastLoading={forecastLoading}
        forecastError={forecastError}
        onRetryForecast={() => loadForecast()}
        requests={requestRows}
        requestsFailed={requestsFailed}
        onRequestBloodType={onRequestBloodType}
        onViewRequests={onViewRequests}
      />
    );
  }

  return (
    <BankDashboardView
      summary={summary}
      summaryLoading={summaryLoading}
      summaryError={summaryError}
      forecast={dashboardData?.view === "forecast" ? dashboardData : null}
      forecastLoading={forecastLoading}
      forecastError={forecastError}
      onRetryForecast={() => loadForecast()}
      onForecastChanged={() => loadForecast()}
      requests={requestRows}
      requestsFailed={requestsFailed}
      onOrder={onRequestBloodType}
      onViewRequests={onViewRequests}
      onViewInventory={onViewInventory}
    />
  );
}
