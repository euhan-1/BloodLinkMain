import { useEffect, useRef, useState } from "react";
import { CheckCircle, Minus, Plus } from "lucide-react";
import { BloodTypeBadge } from "../components/BloodTypeBadge";
import { FacilityNetworkMap, type Facility } from "../components/FacilityNetworkMap";
import { Skeleton } from "../components/Skeleton";
import { ComponentPicker } from "../components/ComponentPicker";
import { usesRedCellCompatibility, type BloodComponent } from "../lib/bloodComponents";
import { ALL_BLOOD_TYPES, BLOOD_COMPATIBILITY } from "../lib/statusTokens";
import { EMERGENCY_TYPE_LABELS, type EmergencyType } from "../lib/requestTypes";

// Emergency Sourcing for a hospital: find a blood bank that has what you need, then ask it for it. The map is the
// primary element and the ranked list sits beside it. Selecting a facility (a row or a pin) is only selecting; the one
// Send button lives in the request panel, under the list, and nowhere else.
//
// Depth: exactly two raised surfaces, the map and the request panel. The controls and the list are flat on the page,
// separated by hairlines. Colour: teal = has what you need, neutral grey = does not. There is no stock status here, so
// gold and red are not used, and there is no blue.

const plural = (n: number, one: string, many: string) => (n === 1 ? one : many);
const sentence = (s: string) => s.charAt(0) + s.slice(1).toLowerCase();
const REASONS = Object.keys(EMERGENCY_TYPE_LABELS) as EmergencyType[];
const RAISED = "shadow-[var(--shadow-raised)]";
// "O+ Packed RBC": what is being searched for and requested. A request names both, so the screen does too.
const what = (p: { searchType: string; component: BloodComponent | null }) => (p.component ? `${p.searchType} ${p.component}` : p.searchType);

export type HospitalSourcingProps = {
  searchType: string;
  onSearchType: (type: string) => void;
  component: BloodComponent | null;
  onComponent: (component: BloodComponent) => void;
  quantity: number;
  onQuantity: (n: number) => void;
  banks: Facility[];
  loading: boolean;
  error: string | null;
  selectedBank: number | null;
  onSelectBank: (id: number | null) => void;
  origin: [number, number] | null;
  reason: EmergencyType | null;
  onReason: (reason: EmergencyType) => void;
  submitting: boolean;
  submitError: string | null;
  justSentBankId: number | null;
  onSend: (bankId: number) => void;
  onOpenRequests: () => void;
};

export function HospitalSourcingView(p: HospitalSourcingProps) {
  const selected = p.banks.find((b) => b.id === p.selectedBank) ?? null;

  // From lg the list scrolls beside the map, so the request panel never leaves the first screen. Two things keep that
  // honest: a fade while more rows sit below the edge (an overlay scrollbar hides itself), and a selection made on the
  // map (a pin) scrolls its row into view.
  const listRef = useRef<HTMLDivElement>(null);
  const [moreBelow, setMoreBelow] = useState(false);
  const updateMoreBelow = () => {
    const c = listRef.current;
    if (c) setMoreBelow(c.scrollHeight - c.scrollTop - c.clientHeight > 4);
  };
  useEffect(() => {
    updateMoreBelow();
    const c = listRef.current;
    if (!c || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(updateMoreBelow);
    ro.observe(c);
    if (c.firstElementChild) ro.observe(c.firstElementChild);
    return () => ro.disconnect();
  }, [p.banks, p.loading]);
  useEffect(() => {
    const c = listRef.current;
    const row = c?.querySelector<HTMLElement>('[data-selected="true"]');
    if (!c || !row || c.scrollHeight <= c.clientHeight + 1) return;
    const cb = c.getBoundingClientRect();
    const rb = row.getBoundingClientRect();
    if (rb.top < cb.top) c.scrollTop -= cb.top - rb.top;
    else if (rb.bottom > cb.bottom) c.scrollTop += rb.bottom - cb.bottom + 8;
  }, [p.selectedBank]);

  return (
    <div className="space-y-6">
      <Controls {...p} />

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-x-8 gap-y-6">
        <div className="lg:col-span-7">
          <div className={`rounded-2xl overflow-hidden bg-gray-100 h-[260px] md:h-[360px] lg:h-[560px] ${RAISED}`}>
            {p.loading && p.banks.length === 0 ? (
              <Skeleton className="h-full w-full rounded-none" />
            ) : p.error ? (
              <p role="alert" className="p-6 text-[15px] text-status-critical-text">Failed to load facilities: {p.error}</p>
            ) : (
              <FacilityNetworkMap
                key={`${p.searchType}-${p.component}-${p.origin ? "origin" : "no-origin"}`}
                variant="ranked"
                banks={p.banks}
                origin={p.origin}
                selectedBankId={p.selectedBank}
                onSelectBank={p.onSelectBank}
                onClearSelection={() => p.onSelectBank(null)}
              />
            )}
          </div>
        </div>

        {/* Phone: controls, map, request panel, list. The nearest available facility is already selected, so the usual
            path is one scroll to Send, with the list directly below for anyone changing the selection. The reorder is CSS
            `order` below md only, so the DOM (and keyboard focus) stays list then panel, which is the visual order from md up. */}
        <div className="lg:col-span-5 flex flex-col md:grid md:grid-cols-5 md:gap-6 lg:flex lg:flex-col lg:gap-0 lg:h-[560px]">
          <div className="relative order-2 md:order-none md:col-span-3 lg:flex-1 lg:min-h-0">
            <div ref={listRef} onScroll={updateMoreBelow} className="lg:h-full lg:overflow-y-auto">
              <FacilityList {...p} />
            </div>
            {moreBelow && (
              <div aria-hidden="true" className="pointer-events-none absolute inset-x-0 bottom-0 h-10 hidden lg:block bg-linear-to-t from-background to-transparent" />
            )}
          </div>
          <div className="order-1 md:order-none mb-6 md:mb-0 md:col-span-2 lg:shrink-0 lg:mt-3">
            <RequestPanel {...p} selected={selected} />
          </div>
        </div>
      </div>
    </div>
  );
}

// ─── Controls (flat) ────────────────────────────────────────────────────────

function Controls(p: HospitalSourcingProps) {
  // Red-cell compatibility only: it does not describe platelets, so for them (or before a component is chosen) it is not shown.
  const compatible = usesRedCellCompatibility(p.component) ? (BLOOD_COMPATIBILITY[p.searchType] ?? []).filter((t) => t !== p.searchType) : [];
  // Only claim "nothing available" once the list has loaded.
  const nobodyHasIt = !p.loading && !p.error && !p.banks.some((b) => b.available);
  return (
    <section aria-labelledby="sourcing-heading">
      <h2 id="sourcing-heading" className="font-display font-bold text-2xl tracking-tight text-foreground">
        Find blood supply
      </h2>
      <div className="mt-4 flex flex-wrap items-end gap-x-10 gap-y-4">
        <ComponentPicker value={p.component} onChange={p.onComponent} />
        <fieldset>
          <legend className="text-[14px] font-semibold text-foreground mb-1.5">Blood type needed</legend>
          <div className="grid grid-cols-4 gap-1.5 sm:flex">
            {ALL_BLOOD_TYPES.map((bt) => (
              <button
                key={bt}
                type="button"
                aria-pressed={p.searchType === bt}
                onClick={() => p.onSearchType(bt)}
                className={`h-10 sm:w-[3.25rem] rounded-md border font-display font-bold text-[15px] transition-colors ${
                  p.searchType === bt
                    ? "bg-primary text-white border-primary"
                    : "bg-card text-foreground border-border hover:bg-secondary"
                }`}
              >
                {bt}
              </button>
            ))}
          </div>
        </fieldset>
        <div>
          <div id="units-label" className="text-[14px] font-semibold text-foreground mb-1.5">Units needed</div>
          <div role="group" aria-labelledby="units-label" className="flex items-center gap-1.5">
            <button
              type="button"
              aria-label="Fewer units"
              onClick={() => p.onQuantity(Math.max(1, p.quantity - 1))}
              className="w-10 h-10 border border-border rounded-md bg-card text-foreground hover:bg-secondary flex items-center justify-center"
            >
              <Minus size={16} />
            </button>
            <span aria-live="polite" className="w-10 text-center text-xl font-bold tabular-nums text-foreground">{p.quantity}</span>
            <button
              type="button"
              aria-label="More units"
              onClick={() => p.onQuantity(p.quantity + 1)}
              className="w-10 h-10 border border-border rounded-md bg-card text-foreground hover:bg-secondary flex items-center justify-center"
            >
              <Plus size={16} />
            </button>
          </div>
        </div>
      </div>

      {/* The one place this screen admits it is not a clinical authority. Keep the caveat attached to the list of types:
          the types are a suggestion, and staff verify them. */}
      {compatible.length > 0 && (
        <p className="mt-4 flex flex-wrap items-center gap-x-2 gap-y-1.5 text-[14px] text-foreground">
          {nobodyHasIt && <span className="font-semibold">No facility has {what(p)} right now.</span>}
          <span>Also compatible with {p.searchType}:</span>
          {compatible.map((t) => (
            <button
              key={t}
              type="button"
              onClick={() => p.onSearchType(t)}
              aria-label={`Search for ${t} instead`}
              className="rounded-md hover:opacity-70 transition-opacity"
            >
              <BloodTypeBadge type={t} size="sm" />
            </button>
          ))}
          <span>— always verify with clinical staff</span>
        </p>
      )}
    </section>
  );
}

// ─── Ranked list (flat) ─────────────────────────────────────────────────────

function RankDot({ rank, available }: { rank: number; available: boolean }) {
  // Matches the pin on the map: same number, same colour.
  return (
    <span
      aria-hidden="true"
      className={`w-6 h-6 rounded-full text-white text-[12px] font-bold flex items-center justify-center shrink-0 ${available ? "bg-status-safe" : "bg-gray-500"}`}
    >
      {rank}
    </span>
  );
}

function FacilityList(p: HospitalSourcingProps) {
  if (p.error) return <p role="alert" className="text-[15px] text-status-critical-text">Failed to load facilities: {p.error}</p>;
  if (p.loading && p.banks.length === 0) {
    return (
      <div className="space-y-2" aria-busy="true" aria-label="Loading facilities">
        {[0, 1, 2].map((i) => <Skeleton key={i} className="h-14 w-full" />)}
      </div>
    );
  }
  if (p.component === null) return <p className="text-[15px] text-foreground">Choose a component to see which facilities can supply it.</p>;
  if (p.banks.length === 0) return <p className="text-[15px] text-foreground">No blood banks with a known location were found near you.</p>;

  // Rank is the server's order: exact type available, then unavailable with an alternative in stock, then the rest.
  const ranked = p.banks.map((bank, i) => ({ bank, rank: i + 1 }));
  const can = ranked.filter((r) => r.bank.available);
  const cant = ranked.filter((r) => !r.bank.available);

  return (
    <div aria-busy={p.loading} className={p.loading ? "opacity-60 transition-opacity" : "transition-opacity"}>
      <h3 className="text-[15px] font-semibold text-foreground pb-2 border-b border-gray-300">Can supply {what(p)}</h3>
      {can.length === 0 ? (
        <p className="py-3 text-[15px] text-foreground">No facility has {what(p)} right now.</p>
      ) : (
        <ul>
          {can.map(({ bank, rank }) => (
            <AvailableRow key={bank.id} bank={bank} rank={rank} selected={p.selectedBank === bank.id} onSelect={() => p.onSelectBank(bank.id)} />
          ))}
        </ul>
      )}
      {cant.length > 0 && (
        <>
          <h3 className="mt-4 text-[15px] font-semibold text-foreground pb-2 border-b border-gray-300">Can't supply {what(p)}</h3>
          <ul>
            {cant.map(({ bank, rank }) => (
              <UnavailableRow
                key={bank.id}
                bank={bank}
                rank={rank}
                selected={p.selectedBank === bank.id}
                onSelect={() => p.onSelectBank(bank.id)}
                onSearchType={p.onSearchType}
              />
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

// Selection is a highlight and an accent bar, never a button. The row (not a Send control) is what you press.
const ROW = "relative border-b border-gray-200 cursor-pointer transition-colors";
const SELECTED_BAR = "before:content-[''] before:absolute before:left-0 before:inset-y-0 before:w-[3px] before:bg-primary";

function AvailableRow({ bank, rank, selected, onSelect }: { bank: Facility; rank: number; selected: boolean; onSelect: () => void }) {
  return (
    <li onClick={onSelect} data-selected={selected} className={`${ROW} ${selected ? `bg-secondary ${SELECTED_BAR}` : "hover:bg-secondary/60"}`}>
      <button
        type="button"
        aria-pressed={selected}
        className="w-full text-left flex items-start gap-3 py-3 pl-3 pr-2 focus-visible:outline-offset-[-2px]"
      >
        <RankDot rank={rank} available />
        <span className="min-w-0 flex-1">
          <span className="flex items-baseline justify-between gap-3">
            <span className="text-[15px] font-semibold text-foreground">{bank.name}</span>
            <span className="text-[15px] font-bold tabular-nums text-foreground shrink-0">{bank.distance_km} km</span>
          </span>
          <span className="flex items-baseline justify-between gap-3 mt-0.5">
            <span className="text-[13px] text-foreground truncate">{bank.address}</span>
            <span className="inline-flex items-center gap-1.5 text-[13px] font-semibold text-status-safe-text shrink-0">
              <span aria-hidden="true" className="w-2 h-2 rounded-full bg-status-safe" /> Available
            </span>
          </span>
        </span>
      </button>
    </li>
  );
}

// One quiet line. Alternatives sit on the same line when they fit (and wrap under it when they do not), so an
// unavailable facility is never taller than one that has what you need.
function UnavailableRow({
  bank, rank, selected, onSelect, onSearchType,
}: { bank: Facility; rank: number; selected: boolean; onSelect: () => void; onSearchType: (t: string) => void }) {
  return (
    <li onClick={onSelect} data-selected={selected} className={`${ROW} ${selected ? `bg-secondary ${SELECTED_BAR}` : "hover:bg-secondary/60"}`}>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 py-2 pl-3 pr-2">
        <button
          type="button"
          aria-pressed={selected}
          className="flex-1 min-w-0 basis-48 flex items-center gap-3 text-left focus-visible:outline-offset-[-2px]"
        >
          <RankDot rank={rank} available={false} />
          <span className="truncate text-[14px] font-[500] text-gray-700">{bank.name}</span>
          <span className="ml-auto text-[14px] font-[500] tabular-nums text-gray-700 shrink-0">{bank.distance_km} km</span>
          <span className="sr-only">Unavailable</span>
        </button>
        {bank.compatible_alternatives.length > 0 && (
          <span className="flex items-center gap-1.5 text-[13px] text-gray-700 ml-9 sm:ml-0">
            In stock
            {bank.compatible_alternatives.map((alt) => (
              <button
                key={alt.blood_type}
                type="button"
                aria-label={`Search for ${alt.blood_type} instead`}
                onClick={(e) => { e.stopPropagation(); onSearchType(alt.blood_type); }}
                className="rounded-md hover:opacity-70 transition-opacity"
              >
                <BloodTypeBadge type={alt.blood_type} size="sm" />
              </button>
            ))}
          </span>
        )}
      </div>
    </li>
  );
}

// ─── Request panel (raised) ─────────────────────────────────────────────────

function RequestPanel(p: HospitalSourcingProps & { selected: Facility | null }) {
  const { selected } = p;
  const units = `${p.quantity} ${plural(p.quantity, "unit", "units")}`;
  const sent = selected !== null && p.justSentBankId === selected.id;
  const anyAvailable = p.banks.some((b) => b.available);

  return (
    <section aria-labelledby="request-heading" className={`rounded-2xl bg-card p-5 ${RAISED}`}>
      {/* Nothing selected: before the list loads, with no facilities, or after clicking the empty map. */}
      {selected === null && (
        <>
          <h2 id="request-heading" className="font-display font-bold text-lg tracking-tight text-foreground">Request</h2>
          <p className="mt-1.5 text-[15px] text-foreground">
            {p.component === null ? "Choose a component, then select a facility from the list or the map." : "Select a facility from the list or the map."}
            {!p.loading && !p.error && p.banks.length > 0 && !anyAvailable && ` No facility has ${what(p)} right now.`}
          </p>
        </>
      )}

      {selected !== null && !selected.available && (
        <>
          <h2 id="request-heading" className="font-display font-bold text-lg tracking-tight text-foreground">
            {selected.name} can't supply {what(p)}
          </h2>
          {selected.compatible_alternatives.length > 0 ? (
            <>
              <p className="mt-1.5 text-[15px] text-foreground">It does have these. Choosing one searches again for that type.</p>
              <div className="mt-3 flex flex-wrap gap-2">
                {selected.compatible_alternatives.map((alt) => (
                  <button
                    key={alt.blood_type}
                    type="button"
                    onClick={() => p.onSearchType(alt.blood_type)}
                    aria-label={`Search for ${alt.blood_type} instead`}
                    className="rounded-md hover:opacity-70 transition-opacity"
                  >
                    <BloodTypeBadge type={alt.blood_type} size="lg" />
                  </button>
                ))}
              </div>
            </>
          ) : (
            <p className="mt-1.5 text-[15px] text-foreground">No compatible type in stock there either.</p>
          )}
        </>
      )}

      {selected !== null && selected.available && sent && (
        <div role="status">
          <h2 id="request-heading" className="flex items-center gap-2 font-display font-bold text-lg tracking-tight text-foreground">
            <CheckCircle size={20} className="text-status-safe-text shrink-0" /> Request sent
          </h2>
          <p className="mt-2 text-[15px] text-foreground">
            {selected.name} has your request for {units} of {what(p)}. Follow its status and message them in Request and chats.
            The number on that tab counts requests still waiting for a reply.
          </p>
          <button
            type="button"
            onClick={p.onOpenRequests}
            className="mt-4 w-full h-11 border border-border rounded-md bg-card text-[15px] font-semibold text-foreground hover:bg-secondary transition-colors"
          >
            Open Request and chats
          </button>
        </div>
      )}

      {selected !== null && selected.available && !sent && (
        <>
          <h2 id="request-heading" className="font-display font-bold text-lg tracking-tight text-foreground">
            <span className="block">Request {units} of {what(p)}</span>{" "}
            <span className="block text-[14px] font-[500] tracking-normal font-sans text-foreground mt-0.5">from {selected.name}</span>
          </h2>

          {/* Required: nothing is pre-selected. The server uses the reason for one thing, the order of the supplier's pending
              requests (_apply_priority_sort: trauma and scheduled surgery ahead of restock). The sentence below says only that. */}
          <div role="radiogroup" aria-labelledby="reason-label" aria-required="true" className="mt-3.5">
            <div id="reason-label" className="text-[14px] font-semibold text-foreground mb-1.5">Reason for request</div>
            <div className="grid grid-cols-3 gap-1.5">
              {REASONS.map((r) => (
                <button
                  key={r}
                  type="button"
                  role="radio"
                  aria-checked={p.reason === r}
                  onClick={() => p.onReason(r)}
                  className={`min-h-10 px-1.5 py-2 rounded-md border text-[14px] font-semibold leading-tight transition-colors ${
                    p.reason === r
                      ? "bg-primary text-white border-primary"
                      : "bg-card text-foreground border-border hover:bg-secondary"
                  }`}
                >
                  {sentence(EMERGENCY_TYPE_LABELS[r])}
                </button>
              ))}
            </div>
            <p className="mt-2 text-[13px] text-foreground text-balance">Trauma and scheduled surgery are listed ahead of restock in the supplier's pending requests.</p>
          </div>

          {p.submitError && (
            <div role="alert" className="mt-4 text-[14px] text-status-critical-text bg-status-critical-tint border border-status-critical-border rounded-md px-3 py-2">
              Failed to send request: {p.submitError}
            </div>
          )}

          <button
            type="button"
            onClick={() => p.onSend(selected.id)}
            disabled={p.reason === null || p.submitting}
            className="mt-3.5 w-full h-11 bg-primary text-white text-[15px] font-semibold rounded-md hover:bg-primary-hover transition-colors disabled:opacity-60"
          >
            {p.submitting ? "Sending…" : p.reason === null ? "Choose a reason to send" : "Send request"}
          </button>
        </>
      )}
    </section>
  );
}
