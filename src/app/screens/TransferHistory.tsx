import { useEffect, useRef, useState } from "react";
import { apiGet } from "../lib/api";
import { BloodTypeBadge } from "../components/BloodTypeBadge";
import { ReasonTag } from "../components/ReasonTag";
import { Skeleton } from "../components/Skeleton";
import { ALL_BLOOD_TYPES } from "../lib/statusTokens";
import { BLOOD_COMPONENTS } from "../lib/bloodComponents";
import {
  DATE_PRESET_LABELS, NO_FILTERS, OUTCOME_LABELS, datePreset, directionText, hasFilters, isRefused, manilaToday, otherFacilityName, outcomeOptions,
  totalsSentence, transferDateText, transfersQuery, type DatePreset, type TransferFilters, type TransferRow, type TransfersResponse,
} from "../lib/transfers";

// The transfer history: every transfer this facility has been part of, both directions, in one table with its filters above
// it. A table, not a search page: the filters are one compact row, the rows are flat with hairlines (as in the inventory),
// and the answer the filters add up to is stated in a line above the table. Filtering and totals are the server's
// (GET /transfers), so "this quarter" is a Manila business-date range and the totals cover every match, not only the rows
// on screen. Below `lg` a row becomes lines of text, not a horizontal scroll.

const HAIRLINE = "border-gray-200";
const COLUMNS = "xl:grid-cols-[6.5rem_7.5rem_minmax(9rem,1.4fr)_4.5rem_6.5rem_3.5rem_4.5rem_9.5rem_11.5rem_8rem]";
const FIELD = "h-9 px-2.5 text-[14px] text-foreground border border-border rounded-md bg-card";
const LABEL = "block text-[13px] font-semibold text-foreground mb-1";

const OUTCOME_TONE: Record<TransferRow["outcome"], string> = {
  received_in_full: "text-status-safe-text",
  partly_received: "text-status-watch-text",
  none_received: "text-status-critical-text",
  in_progress: "text-foreground",
  declined: "text-gray-600",
  cancelled: "text-gray-600",
};

export function TransferHistory({ isBloodBank, onOpenThread }: { isBloodBank: boolean; onOpenThread: (requestId: number) => void }) {
  const [filters, setFilters] = useState<TransferFilters>(NO_FILTERS);
  const [data, setData] = useState<TransfersResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const seq = useRef(0);
  const set = (patch: Partial<TransferFilters>) => setFilters((f) => ({ ...f, ...patch }));

  useEffect(() => {
    const mine = ++seq.current;
    setLoading(true);
    setError(null);
    apiGet<TransfersResponse>(`/transfers?${transfersQuery(filters)}`)
      .then((d) => { if (mine === seq.current) setData(d); })
      .catch((e) => { if (mine === seq.current) setError(e instanceof Error ? e.message : "Failed to load"); })
      .finally(() => { if (mine === seq.current) setLoading(false); });
  }, [filters]);

  const applyPreset = (kind: DatePreset) => {
    const { from, to } = datePreset(kind, manilaToday());
    set({ dateFrom: from, dateTo: to });
  };

  return (
    <section aria-labelledby="history-heading">
      <h2 id="history-heading" className="font-display font-bold text-2xl tracking-tight text-foreground">Transfer history</h2>
      <p className="mt-1 text-[15px] text-foreground">
        {isBloodBank ? "Every transfer this blood bank has supplied or requested." : "Every transfer this hospital has received."}{" "}
        Finished transfers are shown unless you include those still in progress. Declined and cancelled requests are not
        transfers; choose either under Outcome to see them.
      </p>

      {/* Filters: one compact, flat row. */}
      <div className="mt-4 grid grid-cols-2 gap-x-3 gap-y-3 sm:flex sm:flex-wrap sm:items-end sm:gap-x-4">
        <div>
          <label htmlFor="th-from" className={LABEL}>From</label>
          <input id="th-from" type="date" value={filters.dateFrom} max={filters.dateTo || undefined} onChange={(e) => set({ dateFrom: e.target.value })} className={`${FIELD} w-full sm:w-[9.5rem]`} />
        </div>
        <div>
          <label htmlFor="th-to" className={LABEL}>To</label>
          <input id="th-to" type="date" value={filters.dateTo} min={filters.dateFrom || undefined} onChange={(e) => set({ dateTo: e.target.value })} className={`${FIELD} w-full sm:w-[9.5rem]`} />
        </div>
        <div>
          <label htmlFor="th-facility" className={LABEL}>Facility</label>
          <select id="th-facility" value={filters.otherFacilityId} onChange={(e) => set({ otherFacilityId: e.target.value })} className={`${FIELD} w-full sm:w-[15rem]`}>
            <option value="">Any facility</option>
            {(data?.other_facilities ?? []).map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}
          </select>
        </div>
        <div>
          <label htmlFor="th-type" className={LABEL}>Blood type</label>
          <select id="th-type" value={filters.bloodType} onChange={(e) => set({ bloodType: e.target.value })} className={`${FIELD} w-full sm:w-[7.5rem]`}>
            <option value="">Any type</option>
            {ALL_BLOOD_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
        </div>
        <div>
          <label htmlFor="th-component" className={LABEL}>Component</label>
          <select id="th-component" value={filters.component} onChange={(e) => set({ component: e.target.value })} className={`${FIELD} w-full sm:w-[9rem]`}>
            <option value="">Any component</option>
            {BLOOD_COMPONENTS.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        </div>
        <div>
          <label htmlFor="th-outcome" className={LABEL}>Outcome</label>
          <select id="th-outcome" value={filters.outcome} onChange={(e) => set({ outcome: e.target.value })} className={`${FIELD} w-full sm:w-[12.5rem]`}>
            {outcomeOptions(filters.includeActive).map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </div>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-2 text-[14px] text-foreground">
        <label className="inline-flex items-center gap-2 cursor-pointer">
          <input
            type="checkbox"
            checked={filters.includeActive}
            onChange={(e) => set({ includeActive: e.target.checked, outcome: !e.target.checked && filters.outcome === "in_progress" ? "" : filters.outcome })}
            className="w-4 h-4 accent-[var(--role-accent)]"
          />
          Include transfers in progress
        </label>
        <span className="inline-flex flex-wrap items-center gap-x-3">
          <span className="text-gray-600">Date range</span>
          {(Object.keys(DATE_PRESET_LABELS) as DatePreset[]).map((k) => (
            <button key={k} type="button" onClick={() => applyPreset(k)} className="underline underline-offset-2 hover:text-primary">{DATE_PRESET_LABELS[k]}</button>
          ))}
        </span>
        {hasFilters(filters) && (
          <button type="button" onClick={() => setFilters(NO_FILTERS)} className="underline underline-offset-2 hover:text-primary">Clear filters</button>
        )}
      </div>

      {error && <p role="alert" className="mt-5 text-[15px] text-status-critical-text">Failed to load transfers: {error}</p>}

      {!error && (
        <p aria-live="polite" className="mt-5 text-[15px] font-semibold text-foreground">
          {data === null ? "Loading transfers…" : totalsSentence(data.totals, isBloodBank, filters.outcome)}
        </p>
      )}

      {!error && data === null && (
        <div aria-busy="true" aria-label="Loading transfers" className="mt-3 space-y-2">
          {[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-10 w-full" />)}
        </div>
      )}

      {!error && data !== null && (
        <div aria-busy={loading} className={`mt-2 transition-opacity ${loading ? "opacity-60" : ""}`}>
          {data.transfers.length === 0 ? (
            <p className="text-[15px] text-foreground py-3">
              {hasFilters(filters)
                ? "No transfers match these filters."
                : "No finished transfers yet. A transfer appears here once the requesting facility confirms receipt."}
            </p>
          ) : (
            <div role="table" aria-label="Transfers">
              <div role="row" className={`hidden xl:grid ${COLUMNS} items-end gap-x-3 pb-1.5 border-b border-gray-300 text-[13px] font-semibold text-gray-600`}>
                <div role="columnheader">Date</div>
                <div role="columnheader">Direction</div>
                <div role="columnheader">Other facility</div>
                <div role="columnheader">Blood type</div>
                <div role="columnheader">Component</div>
                <div role="columnheader" className="text-right">Asked</div>
                <div role="columnheader" className="text-right">Received</div>
                <div role="columnheader">Reason</div>
                <div role="columnheader">Outcome</div>
                <div role="columnheader"><span className="sr-only">Receipt</span></div>
              </div>
              {data.transfers.map((t) => <TransferLine key={t.request.id} t={t} onOpenThread={onOpenThread} />)}
            </div>
          )}
          {data.truncated && (
            <p className="mt-3 text-[14px] text-foreground">Showing the newest {data.transfers.length} of {data.total}. Narrow the filters to see the rest.</p>
          )}
        </div>
      )}
    </section>
  );
}

// One transfer. From `xl` the cells are grid columns in DOM order; below it the row wraps into lines, ordered by `order-N`:
// who and the outcome, then when, which way, what, how many, why, and the receipt.
function TransferLine({ t, onOpenThread }: { t: TransferRow; onOpenThread: (requestId: number) => void }) {
  const r = t.request;
  const s = t.summary;
  return (
    <div
      role="row"
      className={`flex flex-wrap items-baseline gap-x-4 gap-y-1 py-3 xl:py-0 xl:min-h-12 xl:grid ${COLUMNS} xl:gap-x-3 xl:items-center border-b ${HAIRLINE} last:border-b-0`}
    >
      <div role="cell" className="text-[14px] text-foreground tabular-nums whitespace-nowrap order-4 xl:order-none">{transferDateText(t.transfer_at)}</div>
      <div role="cell" className="text-[14px] text-foreground order-5 xl:order-none">{directionText(t.direction)}</div>
      <div role="cell" className="text-[15px] font-semibold text-foreground order-1 flex-1 min-w-0 xl:order-none xl:flex-none xl:truncate xl:text-[14px] xl:font-normal">
        {otherFacilityName(t)}
      </div>
      <div role="cell" className="order-6 xl:order-none"><BloodTypeBadge type={r.blood_type} size="sm" /></div>
      <div role="cell" className="text-[14px] text-foreground order-7 xl:order-none">{r.component ?? <span className="text-gray-600">not recorded</span>}</div>
      <div role="cell" className="hidden xl:block text-[14px] text-foreground tabular-nums text-right">{s.asked}</div>
      <div role="cell" className="hidden xl:block text-[14px] text-foreground tabular-nums text-right">{s.received ?? (isRefused(t.outcome) ? <><span aria-hidden="true">—</span><span className="sr-only">no units</span></> : <span className="text-gray-600">pending</span>)}</div>
      <div role="cell" className="text-[14px] text-foreground tabular-nums order-8 xl:hidden">
        {s.received === null ? `${s.asked} asked` : `${s.received} of ${s.asked} received`}
      </div>
      <div role="cell" className="order-9 xl:order-none"><ReasonTag reason={r.emergency_type} /></div>
      <div role="cell" className={`text-[14px] font-semibold order-2 ml-auto xl:ml-0 xl:order-none ${OUTCOME_TONE[t.outcome]}`}>
        {OUTCOME_LABELS[t.outcome]}
        {s.failed ? <span className="font-normal text-foreground">{`, ${s.failed} expired`}</span> : null}
      </div>
      <div role="cell" className="order-10 ml-auto xl:ml-0 xl:order-none flex items-baseline justify-end gap-4">
        {/* The thread for this one transfer, in Request and chats: a finished transfer is not listed there any more. */}
        <button
          type="button"
          onClick={() => onOpenThread(r.id)}
          aria-label={`Open the thread for request ${r.id}`}
          className="text-[14px] font-semibold text-primary underline-offset-2 hover:underline"
        >
          Chat
        </button>
        <a
          href={`/receipt/${r.id}`}
          target="_blank"
          rel="noopener"
          aria-label={`Receipt for request ${r.id}`}
          className="text-[14px] font-semibold text-primary underline-offset-2 hover:underline"
        >
          Receipt
        </a>
      </div>
      {/* Phone: ends the first line (who and the outcome) so the details start on the next. */}
      <div aria-hidden="true" className="basis-full h-0 order-3 xl:hidden" />
    </div>
  );
}
