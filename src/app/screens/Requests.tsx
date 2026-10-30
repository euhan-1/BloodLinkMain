import { useState, useRef, useEffect } from "react";
import { AlertTriangle, ArrowRight, CheckCircle, ChevronDown, Clock, MapPin, MessageSquare, Send } from "lucide-react";
import { apiGet, apiPost, getMyFacilityProfile } from "../lib/api";
import { getCurrentUser } from "../lib/session";
import { getDevFacilityId, isDevModeEnabled } from "../lib/devMode";
import { BloodTypeBadge } from "../components/BloodTypeBadge";
import { Modal } from "../components/Modal";
import { FacilityNetworkMap, type Facility } from "../components/FacilityNetworkMap";
import { HospitalSourcingView } from "./HospitalSourcing";
import { ComponentPicker } from "../components/ComponentPicker";
import { RequestThread } from "../components/RequestThread";
import { listIsBehind, mergeThread, type RequestDetail } from "../lib/requestTimeline";
import { usesRedCellCompatibility, type BloodComponent } from "../lib/bloodComponents";
import { useFlashOnChange } from "../lib/motion";
import { ALL_BLOOD_TYPES, BLOOD_COMPATIBILITY } from "../lib/statusTokens";
import { EMERGENCY_TYPE_LABELS, groupIncomingRequests, requestUnitsText, type EmergencyType, type RequestRow } from "../lib/requestTypes";
import { ReasonTag } from "../components/ReasonTag";
import { TransferHistory } from "./TransferHistory";

// Chat messages poll faster than notifications (App.tsx's NOTIFICATION_POLL_MS)
// since a coordination chat is a live conversation, not a background alert.
const CHAT_MESSAGE_POLL_MS = 4000;

// Both sides' request lists refresh this often while the browser tab is visible (the same interval as the Dashboard's own
// polls), so a status change made by the other facility shows up on this one within the same window whichever side made it:
// a request that arrives at a supplier appears in its place in the priority order, and a requester's "Awaiting response"
// becomes "accepted" without a reload. Silent: no loading state, and a failed refresh leaves what is on screen alone.
const REQUEST_LISTS_POLL_MS = 25000;

type RequestTab = "sourcing" | "transfer" | "history";

// ─── Requests (Emergency Sourcing + Transfer) ─────────────────────────────────

// Outcome of confirm-receipt, shared by both cards. A reserved unit that expired
// before receipt was not transferred; the DINs are listed rather than leaving a
// short count unexplained.
function ReceiptOutcome({ req, size }: { req: RequestRow; size: 12 | 13 }) {
  const text = size === 12 ? "text-[12px]" : "text-[13px]";
  const failed = req.failed_units;
  if (req.status === "completed" && failed.length === 0) {
    return (
      <span className={`flex items-center gap-1 animate-success-pop ${text} font-semibold text-status-safe-text`}>
        <CheckCircle size={size} /> Completed
      </span>
    );
  }
  const received = req.quantity - failed.length;
  return (
    <div className={text}>
      <span className={`flex items-center gap-1 font-semibold ${req.status === "failed" ? "text-status-critical-text" : "text-status-watch-text"}`}>
        <AlertTriangle size={size} />
        {req.status === "failed" ? "Failed — no units received" : `Completed — ${received} of ${req.quantity} received`}
      </span>
      <div className="mt-1 text-muted-foreground">
        Expired before receipt, not transferred:{" "}
        {failed.map((u) => `${u.din} (expired ${u.expires_date})`).join(", ")}
      </div>
    </div>
  );
}

type ActingFacility = { id: number; name: string; facility_type: string };

type RequestAction = "accept" | "decline" | "cancel" | "confirm-release" | "confirm-receipt";

type RequestMessage = {
  id: number;
  request_id: number;
  sender_facility_id: number;
  sender_facility_name: string;
  message: string;
  created_at: string;
};

const FINISHED_STATUSES = ["completed", "failed", "declined", "cancelled"];

// A quiet link to a finished request's transfer record (/receipt/{id}, in a new tab). Muted text, underlined, no fill or
// border, so it never competes with a card's real action (Confirm receipt). It stops the click reaching the card, which
// opens the chat.
function ReceiptLink({ requestId }: { requestId: number }) {
  return (
    <a
      href={`/receipt/${requestId}`}
      target="_blank"
      rel="noopener"
      onClick={(e) => e.stopPropagation()}
      className="shrink-0 text-[12px] text-gray-600 underline underline-offset-2 hover:text-foreground"
    >
      Receipt
    </a>
  );
}

// `position` is given only for a request that is waiting for a decision: its place in the order the supplier should
// work in. Every card shows the reason as the same tag.
function IncomingRequestCard({
  req, busy, onAction, isChatSelected, onOpenChat, position,
}: {
  req: RequestRow;
  busy: boolean;
  onAction: (id: number, action: RequestAction) => void;
  isChatSelected: boolean;
  onOpenChat: (id: number) => void;
  position?: number;
}) {
  // Flashes once when this request's actual phase changes (pending ->
  // accepted -> released -> completed) — not on every poll/re-render, only
  // when the thing the card is reporting has genuinely moved forward.
  const phase = `${req.status}:${req.supplier_confirmed_at ?? ""}:${req.requester_confirmed_at ?? ""}`;
  const flash = useFlashOnChange(phase);
  return (
    <div
      onClick={() => onOpenChat(req.id)}
      title={isChatSelected ? "Viewing chat" : "Open chat"}
      className={`rounded-lg border-2 p-3 cursor-pointer transition-colors ${
        isChatSelected ? "border-primary bg-primary-tint" : "border-info-border bg-card hover:bg-info-tint"
      } ${flash ? "animate-value-flash-bg" : ""}`}
    >
      <div className="flex items-center justify-between gap-2 mb-1.5">
        <div className="flex items-center gap-2 min-w-0">
          {position !== undefined && (
            <span
              aria-label={`Position ${position}`}
              className="w-6 h-6 rounded-full bg-primary-tint text-primary text-[12px] font-bold flex items-center justify-center shrink-0"
            >
              {position}
            </span>
          )}
          <div className="text-[14px] font-semibold text-foreground">
            {req.requesting_facility_name ?? "Unknown facility"}
          </div>
        </div>
        <BloodTypeBadge type={req.blood_type} size="md" />
      </div>
      <div className="flex items-center gap-2 mb-2 text-[13px] text-foreground">
        {position === undefined && <MessageSquare size={12} className="text-info shrink-0" />}
        <span>{requestUnitsText(req)}</span>
        <ReasonTag reason={req.emergency_type} />
      </div>

      {req.status === "pending" && (
        <div className="flex gap-1.5">
          <button
            onClick={(e) => { e.stopPropagation(); onAction(req.id, "accept"); }}
            disabled={busy}
            className="h-7 px-3 bg-primary text-white text-[12px] font-semibold rounded-lg hover:bg-primary-hover transition-colors disabled:opacity-60"
          >
            {busy ? "…" : "Accept"}
          </button>
          <button
            onClick={(e) => { e.stopPropagation(); onAction(req.id, "decline"); }}
            disabled={busy}
            className="h-7 px-3 bg-card border border-border text-[12px] font-semibold text-foreground rounded-lg hover:bg-secondary transition-colors disabled:opacity-60"
          >
            Decline
          </button>
        </div>
      )}

      {req.status === "accepted" && !req.supplier_confirmed_at && (
        <button
          onClick={(e) => { e.stopPropagation(); onAction(req.id, "confirm-release"); }}
          disabled={busy}
          className="w-full h-7 bg-primary text-white text-[12px] font-semibold rounded-lg hover:bg-primary-hover transition-colors disabled:opacity-60"
        >
          {busy ? "Confirming…" : "Confirm Release"}
        </button>
      )}

      {req.status === "accepted" && req.supplier_confirmed_at && !req.requester_confirmed_at && (
        <span className="inline-block animate-success-pop text-[12px] font-semibold text-status-watch-text">Released — awaiting requester confirmation</span>
      )}

      {FINISHED_STATUSES.includes(req.status) && (
        <div className="flex items-end justify-between gap-3">
          <div>
            {(req.status === "completed" || req.status === "failed") && <ReceiptOutcome req={req} size={12} />}
            {req.status === "declined" && <span className="text-[12px] font-semibold text-muted-foreground">Declined</span>}
            {req.status === "cancelled" && <span className="text-[12px] font-semibold text-muted-foreground">Cancelled by requester</span>}
          </div>
          <ReceiptLink requestId={req.id} />
        </div>
      )}
    </div>
  );
}

// A blood bank's incoming requests in three sections: waiting for a decision, in progress, finished. The server lists
// pending requests in priority order but leaves every other row where its creation time puts it, so in one flat list
// the order could not be seen. Split like this, "waiting" reads top to bottom as the order to work in: immediate use
// first (trauma and scheduled surgery), then restock, oldest first in each, numbered 1, 2, 3.
function IncomingSections({
  rows, busyId, onAction, selectedRequestId, onOpenChat,
}: {
  rows: RequestRow[];
  busyId: number | null;
  onAction: (id: number, action: RequestAction) => void;
  selectedRequestId: number | null;
  onOpenChat: (id: number) => void;
}) {
  const g = groupIncomingRequests(rows);
  const [showFinished, setShowFinished] = useState(false);
  const card = (req: RequestRow, position?: number) => (
    <IncomingRequestCard
      key={req.id}
      req={req}
      busy={busyId === req.id}
      onAction={onAction}
      isChatSelected={selectedRequestId === req.id}
      onOpenChat={onOpenChat}
      position={position}
    />
  );
  const tier = (label: string, list: RequestRow[], firstPosition: number) =>
    list.length === 0 ? null : (
      <div>
        <div className="text-[13px] font-semibold text-foreground mb-1.5">{label}</div>
        <div className="space-y-2">{list.map((req, i) => card(req, firstPosition + i))}</div>
      </div>
    );

  return (
    <div className="space-y-5">
      <section aria-labelledby="incoming-waiting">
        <h4 id="incoming-waiting" className="text-[14px] font-semibold text-foreground">Waiting for a decision ({g.waiting.length})</h4>
        {g.waiting.length === 0 ? (
          <p className="text-[13px] text-muted-foreground mt-1.5">Nothing is waiting for a decision.</p>
        ) : (
          <>
            <p className="text-[13px] text-muted-foreground mt-0.5 mb-3">
              In the order to work in: trauma and scheduled surgery first, then restock, oldest first within each.
            </p>
            <div className="space-y-4">
              {tier("Immediate use", g.immediate, 1)}
              {tier("Restock", g.restock, g.immediate.length + 1)}
            </div>
          </>
        )}
      </section>

      {g.inProgress.length > 0 && (
        <section aria-labelledby="incoming-progress">
          <h4 id="incoming-progress" className="text-[14px] font-semibold text-foreground mb-2">In progress ({g.inProgress.length})</h4>
          <div className="space-y-2">{g.inProgress.map((req) => card(req))}</div>
        </section>
      )}

      {g.finished.length > 0 && (
        <section>
          <h4 className="text-[14px] font-semibold text-foreground">
            <button
              type="button"
              onClick={() => setShowFinished((v) => !v)}
              aria-expanded={showFinished}
              className="flex items-center gap-1.5 rounded-sm hover:underline hover:underline-offset-2"
            >
              <ChevronDown size={15} className={`transition-transform ${showFinished ? "" : "-rotate-90"}`} />
              Finished ({g.finished.length})
            </button>
          </h4>
          {showFinished && <div className="space-y-2 mt-2">{g.finished.map((req) => card(req))}</div>}
        </section>
      )}
    </div>
  );
}

function AcceptedRequestCard({
  req, canConfirmReceipt, busy, isChatSelected, onOpenChat, onConfirmReceipt,
}: {
  req: RequestRow;
  canConfirmReceipt: boolean;
  busy: boolean;
  isChatSelected: boolean;
  onOpenChat: () => void;
  onConfirmReceipt: () => void;
}) {
  const phase = `${req.status}:${req.supplier_confirmed_at ?? ""}`;
  const flash = useFlashOnChange(phase);
  return (
    <div
      onClick={onOpenChat}
      title={isChatSelected ? "Viewing chat" : "Open chat"}
      className={`rounded-lg border-2 p-3 cursor-pointer transition-colors ${
        isChatSelected ? "border-primary bg-primary-tint" : "border-info-border bg-card hover:bg-info-tint"
      } ${flash ? "animate-value-flash-bg" : ""}`}
    >
      <div className="flex items-center justify-between mb-2.5">
        <div className="text-[16px] font-semibold text-foreground">{req.supplying_facility_name}</div>
        <BloodTypeBadge type={req.blood_type} size="lg" />
      </div>
      <div className="flex items-center gap-2 mb-2 text-[13px] text-foreground">
        <MessageSquare size={13} className="text-info shrink-0" />
        <span>{requestUnitsText(req)}</span>
        <ReasonTag reason={req.emergency_type} />
      </div>

      {req.status === "completed" || req.status === "failed" ? (
        <div className="flex items-end justify-between gap-3">
          <ReceiptOutcome req={req} size={13} />
          <ReceiptLink requestId={req.id} />
        </div>
      ) : !req.supplier_confirmed_at ? (
        <span className="text-[13px] text-muted-foreground">Waiting for supplier to confirm release</span>
      ) : canConfirmReceipt ? (
        <button
          onClick={(e) => { e.stopPropagation(); onConfirmReceipt(); }}
          disabled={busy}
          className="w-full h-7 bg-primary text-white text-[13px] font-semibold rounded-lg hover:bg-primary-hover transition-colors disabled:opacity-60"
        >
          {busy ? "Confirming…" : "Confirm Receipt"}
        </button>
      ) : (
        <span className="text-[13px] text-muted-foreground">Switch to the requesting facility above to confirm receipt</span>
      )}
    </div>
  );
}

export function RequestsScreen({
  initialSearchType, onConsumedSourcingPrefill,
  initialHighlightRequestId, onConsumedHighlight,
}: {
  initialSearchType?: string | null;
  onConsumedSourcingPrefill?: () => void;
  initialHighlightRequestId?: number | null;
  onConsumedHighlight?: () => void;
}) {
  const [tab, setTab] = useState<RequestTab>(initialHighlightRequestId ? "transfer" : "sourcing");
  const [searchType, setSearchType] = useState(initialSearchType ?? "O-");
  const [quantityNeeded, setQuantityNeeded] = useState(3);

  // Acting facility now resolves the same way api.ts resolves it server-side:
  // dev override (if ?dev=1 and the global DevFacilityBanner picked one) wins,
  // else the real logged-in user's own facility. No local switcher here
  // anymore — selection happens once, globally, in App().
  const actingFacilityId = getDevFacilityId() ?? getCurrentUser()?.facility_id ?? null;

  // Only fetched for display-name resolution when dev-overriding to a facility
  // that isn't the logged-in user's own (whose name we already have via session).
  const [facilities, setFacilities] = useState<ActingFacility[]>([]);
  useEffect(() => {
    if (!isDevModeEnabled()) return;
    apiGet<ActingFacility[]>("/facilities").then(setFacilities);
  }, []);

  // Blood banks have no patients, so trauma/scheduled_surgery (which grant
  // non-preemptive priority ahead of restock — see IMMEDIATE_USE_TYPES in
  // main.py) can never legitimately apply to a blood-bank-originated
  // request. The server enforces this regardless of what's sent here; this
  // just keeps the UI honest about the only value that will actually land.
  // Derived from the ACTING facility (dev override aware), not always the
  // real logged-in account — otherwise using ?dev=1 to act as a different
  // facility type leaves this stuck on the real account's type, which used
  // to hide the entire "Incoming Requests" panel for a dev-overridden
  // blood-bank facility even though its data was being fetched correctly.
  const isBloodBankRequester = isDevModeEnabled()
    ? facilities.find((f) => f.id === actingFacilityId)?.facility_type === "bloodbank"
    : getCurrentUser()?.facility_type === "bloodbank";
  // A hospital has no reason until it chooses one: the reason decides where the request is listed among the
  // supplier's pending requests (trauma and scheduled surgery ahead of restock), so it is never pre-selected. A blood
  // bank's is always restock.
  const [emergencyType, setEmergencyType] = useState<EmergencyType | null>(isBloodBankRequester ? "restock" : null);
  // Which component to look for and request. Never pre-selected, for either role: release reserves units of exactly this
  // component, and the search below does not run until it is chosen.
  const [component, setComponent] = useState<BloodComponent | null>(null);

  // This facility's own location, for the "You" marker on the hospital's map. Not wanted by the blood-bank map, and not
  // under the dev facility override, where /facilities/me would describe the wrong facility.
  const [origin, setOrigin] = useState<[number, number] | null>(null);
  useEffect(() => {
    if (isBloodBankRequester || (isDevModeEnabled() && getDevFacilityId() !== null)) return;
    let cancelled = false;
    getMyFacilityProfile()
      .then((profile) => {
        if (!cancelled && profile.latitude !== null && profile.longitude !== null) setOrigin([profile.latitude, profile.longitude]);
      })
      .catch(() => {}); // the map simply has no "You" marker
    return () => { cancelled = true; };
  }, [isBloodBankRequester]);
  const [selectedBank, setSelectedBank] = useState<number | null>(null);
  const [chatInput, setChatInput] = useState("");
  const [selectedRequestId, setSelectedRequestId] = useState<number | null>(initialHighlightRequestId ?? null);
  const [messages, setMessages] = useState<RequestMessage[]>([]);
  const [messagesLoading, setMessagesLoading] = useState(false);
  const [messagesError, setMessagesError] = useState<string | null>(null);
  // The selected request as the server describes it now (its status events and units), fetched with the messages.
  const [detail, setDetail] = useState<RequestDetail | null>(null);
  // "live" only while the last poll of the thread succeeded; "lost" after one fails, until the next succeeds.
  const [chatLive, setChatLive] = useState<"connecting" | "live" | "lost">("connecting");
  // The two request lists as of the latest render, for the poll below to compare against without going stale.
  const listsRef = useRef<{ requests: RequestRow[]; incoming: RequestRow[] }>({ requests: [], incoming: [] });
  const [sendingMessage, setSendingMessage] = useState(false);
  const chatRef = useRef<HTMLDivElement>(null);

  // Consume each one-shot prefill whenever it actually arrives — keyed off
  // the prop itself, not mount, since this screen does NOT remount when a
  // notification is clicked while already sitting on Requests (screen state
  // stays "requests", so only the props change). Mount-only would silently
  // no-op in exactly that case: a user already on this screen clicking a
  // notification. Each effect re-firing on the same value it just consumed
  // (App() resets the corresponding state to null right after) is harmless —
  // the `if` guard just skips it.
  useEffect(() => {
    if (initialSearchType) {
      setSearchType(initialSearchType);
      onConsumedSourcingPrefill?.();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialSearchType]);

  useEffect(() => {
    if (initialHighlightRequestId) {
      setTab("transfer");
      setSelectedRequestId(initialHighlightRequestId);
      onConsumedHighlight?.();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialHighlightRequestId]);

  const [banks, setBanks] = useState<Facility[]>([]);
  const [banksLoading, setBanksLoading] = useState(true);
  const [banksError, setBanksError] = useState<string | null>(null);

  const [requests, setRequests] = useState<RequestRow[]>([]);
  const [requestsLoading, setRequestsLoading] = useState(true);
  const [requestsError, setRequestsError] = useState<string | null>(null);

  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [justSentBankId, setJustSentBankId] = useState<number | null>(null);

  // /requests and /requests/incoming each only resolve the OTHER side's name
  // (the "us" side is implied) — this fills in "us" for the chat header,
  // whichever side of the transfer that happens to be.
  function resolveFacilityName(id: number, providedName?: string): string {
    if (providedName) return providedName;
    if (id === actingFacilityId) {
      return facilities.find((f) => f.id === id)?.name ?? getCurrentUser()?.facility_name ?? `Facility #${id}`;
    }
    return facilities.find((f) => f.id === id)?.name ?? `Facility #${id}`;
  }

  const [incomingRequests, setIncomingRequests] = useState<RequestRow[]>([]);
  const [incomingLoading, setIncomingLoading] = useState(true);
  const [incomingError, setIncomingError] = useState<string | null>(null);

  const [actionPendingId, setActionPendingId] = useState<number | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  // Which of the requester's own pending requests is showing the "are you
  // sure" confirmation before cancel — cancel is real and non-reversible,
  // unlike accept/decline elsewhere in this screen, so it gets a step the
  // others don't.
  const [cancelConfirmId, setCancelConfirmId] = useState<number | null>(null);

  // Sequence guards, not just mount-cleanup ones: both fetchers are also
  // called directly from event handlers (performRequestAction, sendRequest)
  // that can overlap with an in-flight effect-driven fetch, so a plain
  // "cancelled on unmount" flag wouldn't stop a slower earlier response from
  // landing after a faster later one and silently reverting the list.
  const requestsRequestIdRef = useRef(0);
  // silent = a background refresh, as for fetchIncoming below. The tab badge counts come from this list, so they refresh with it.
  function fetchRequests(silent = false) {
    const requestId = ++requestsRequestIdRef.current;
    if (!silent) {
      setRequestsLoading(true);
      setRequestsError(null);
    }
    apiGet<RequestRow[]>("/requests")
      .then((data) => { if (requestId === requestsRequestIdRef.current) setRequests(data); })
      .catch((err) => { if (!silent && requestId === requestsRequestIdRef.current) setRequestsError(err instanceof Error ? err.message : "Failed to load"); })
      .finally(() => { if (requestId === requestsRequestIdRef.current) setRequestsLoading(false); });
  }

  useEffect(() => {
    fetchRequests();
  }, []);

  const incomingRequestIdRef = useRef(0);
  // silent = a background refresh: no loading state, and a failure leaves what is already on screen alone.
  function fetchIncoming(silent = false) {
    if (actingFacilityId === null) return;
    const requestId = ++incomingRequestIdRef.current;
    if (!silent) {
      setIncomingLoading(true);
      setIncomingError(null);
    }
    apiGet<RequestRow[]>("/requests/incoming")
      .then((data) => { if (requestId === incomingRequestIdRef.current) setIncomingRequests(data); })
      .catch((err) => { if (!silent && requestId === incomingRequestIdRef.current) setIncomingError(err instanceof Error ? err.message : "Failed to load"); })
      // Cleared by whichever request is the latest, silent or not, so a refresh that supersedes a slower load cannot
      // leave "Loading…" stuck on screen.
      .finally(() => { if (requestId === incomingRequestIdRef.current) setIncomingLoading(false); });
  }

  useEffect(() => {
    fetchIncoming();
  }, [actingFacilityId]);

  // Keep both lists fresh while the browser tab is visible, and catch up at once when it becomes visible again (a hidden tab
  // does not poll, so it can be a long way behind). The requester's own list (and the badge counts built from it) is polled
  // on every tab, since the badge is on every tab; the supplier's inbox only on the tab that shows it, and only blood banks
  // have one.
  useEffect(() => {
    const refresh = () => {
      if (document.visibilityState !== "visible") return;
      fetchRequests(true);
      if (isBloodBankRequester && tab === "transfer") fetchIncoming(true);
    };
    const timer = setInterval(refresh, REQUEST_LISTS_POLL_MS);
    document.addEventListener("visibilitychange", refresh);
    return () => { clearInterval(timer); document.removeEventListener("visibilitychange", refresh); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isBloodBankRequester, tab, actingFacilityId]);

  async function performRequestAction(requestId: number, action: RequestAction) {
    if (actingFacilityId === null) return;
    setActionPendingId(requestId);
    setActionError(null);
    try {
      await apiPost(`/requests/${requestId}/${action}`, {});
      fetchRequests();
      fetchIncoming();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Action failed");
    } finally {
      setActionPendingId(null);
    }
  }

  async function sendRequest(bankId: number) {
    const reason = emergencyType ?? (isBloodBankRequester ? "restock" : null);
    if (reason === null || component === null) return; // a hospital must choose a reason first, and anyone a component
    setSubmitting(true);
    setSubmitError(null);
    try {
      await apiPost("/requests", {
        supplying_facility_id: bankId,
        blood_type: searchType,
        component,
        quantity: quantityNeeded,
        emergency_type: reason,
      });
      setJustSentBankId(bankId);
      if (!isBloodBankRequester) setEmergencyType(null); // the next request is chosen afresh, never inherited
      fetchRequests();
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : "Failed to send request");
    } finally {
      setSubmitting(false);
    }
  }

  useEffect(() => {
    if (chatRef.current) {
      chatRef.current.scrollTop = chatRef.current.scrollHeight;
    }
  }, [messages]);

  // The selected request's thread: its messages and its own record (GET /requests/{id}, from which the status events are
  // derived), fetched together every few seconds. Polled rather than pushed, there is no websocket layer in this app.
  // "Live" is the result of the last poll, not a property of having a request selected. A hidden browser tab does not
  // poll, and polls at once on becoming visible. If the server reports the request has moved on from what the lists
  // hold (the other side accepted, released or confirmed receipt), the lists are fetched again.
  useEffect(() => {
    if (selectedRequestId === null) {
      setMessages([]);
      setMessagesError(null);
      setDetail(null);
      return;
    }
    let cancelled = false;
    let inFlight = false;
    let lastSyncedKey = "";
    async function poll() {
      if (inFlight || document.visibilityState !== "visible") return;
      inFlight = true;
      try {
        const [msgs, det] = await Promise.all([
          apiGet<RequestMessage[]>(`/requests/${selectedRequestId}/messages`),
          apiGet<RequestDetail>(`/requests/${selectedRequestId}`),
        ]);
        if (cancelled) return;
        setMessages(msgs);
        setDetail(det);
        setMessagesError(null);
        setChatLive("live");
        const { requests: mine, incoming } = listsRef.current;
        const listed = [...mine, ...incoming].find((r) => r.id === det.request.id);
        const key = `${det.request.status}:${det.request.supplier_confirmed_at}:${det.request.requester_confirmed_at}`;
        if (listIsBehind(listed, det.request) && key !== lastSyncedKey) {
          lastSyncedKey = key; // once per change, so a row that never matches cannot refetch every poll
          fetchRequests(true);
          fetchIncoming(true);
        }
      } catch (err) {
        if (!cancelled) {
          setMessagesError(err instanceof Error ? err.message : "Failed to load");
          setChatLive("lost");
        }
      } finally {
        inFlight = false;
        if (!cancelled) setMessagesLoading(false);
      }
    }
    const onVisible = () => { if (document.visibilityState === "visible") poll(); };
    setMessagesLoading(true);
    setDetail(null);
    setChatLive("connecting");
    poll();
    const interval = setInterval(poll, CHAT_MESSAGE_POLL_MS);
    document.addEventListener("visibilitychange", onVisible);
    return () => { cancelled = true; clearInterval(interval); document.removeEventListener("visibilitychange", onVisible); };
  }, [selectedRequestId]);

  useEffect(() => {
    let cancelled = false;
    setBanksError(null);
    setJustSentBankId(null);
    setSubmitError(null);
    if (component === null) {
      // Stock is counted per component, so there is nothing true to show until one is chosen.
      setBanks([]);
      setSelectedBank(null);
      setBanksLoading(false);
      return;
    }
    setBanksLoading(true);
    const params = new URLSearchParams({ blood_type: searchType, component, quantity: String(quantityNeeded) });
    apiGet<Facility[]>(`/facilities/nearby?${params}`)
      .then((data) => {
        if (cancelled) return;
        setBanks(data);
        // Default to the nearest facility that actually has enough stock; fall
        // back to just-nearest if none currently qualify.
        const nearestAvailable = data.find((b) => b.available);
        setSelectedBank((nearestAvailable ?? data[0])?.id ?? null);
      })
      .catch((err) => {
        if (!cancelled) setBanksError(err instanceof Error ? err.message : "Failed to load");
      })
      .finally(() => {
        if (!cancelled) setBanksLoading(false);
      });
    return () => { cancelled = true; };
  }, [searchType, quantityNeeded, component]);

  async function sendMessage() {
    if (!chatInput.trim() || selectedRequestId === null || sendingMessage) return;
    setSendingMessage(true);
    try {
      const sent = await apiPost<RequestMessage>(`/requests/${selectedRequestId}/messages`, {
        message: chatInput.trim(),
      });
      setMessages((prev) => [...prev, sent]);
      setChatInput("");
      setMessagesError(null);
    } catch (err) {
      setMessagesError(err instanceof Error ? err.message : "Failed to send message");
    } finally {
      setSendingMessage(false);
    }
  }

  listsRef.current = { requests, incoming: incomingRequests };
  const pendingOnly = requests.filter((r) => r.status === "pending");

  return (
    <div className="max-w-screen-2xl mx-auto px-6 py-6">
      {/* Sub-tabs */}
      <div className="flex gap-1 mb-6 bg-secondary rounded-lg p-1 w-fit">
        {(["sourcing", "transfer", "history"] as RequestTab[]).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`relative px-5 py-2 text-[14px] font-semibold rounded border transition-all duration-200 ease-out ${
              tab === t
                ? "bg-card text-foreground shadow-sm border-border scale-105"
                : "border-transparent text-muted-foreground hover:text-foreground"
            }`}
          >
            {t === "sourcing" ? "Emergency sourcing" : t === "transfer" ? "Request and chats" : "Transfer history"}
            {t === "transfer" && pendingOnly.length > 0 && (
              <span className="absolute -top-1 -right-1 w-4 h-4 bg-primary text-white text-[10px] font-bold rounded-full flex items-center justify-center">
                {pendingOnly.length}
              </span>
            )}
          </button>
        ))}
      </div>

      {tab === "history" && <TransferHistory isBloodBank={isBloodBankRequester} />}

      {tab === "sourcing" && !isBloodBankRequester && (
        <HospitalSourcingView
          searchType={searchType}
          onSearchType={setSearchType}
          component={component}
          onComponent={setComponent}
          quantity={quantityNeeded}
          onQuantity={setQuantityNeeded}
          banks={banks}
          loading={banksLoading}
          error={banksError}
          selectedBank={selectedBank}
          onSelectBank={setSelectedBank}
          origin={origin}
          reason={emergencyType}
          onReason={setEmergencyType}
          submitting={submitting}
          submitError={submitError}
          justSentBankId={justSentBankId}
          onSend={sendRequest}
          onOpenRequests={() => setTab("transfer")}
        />
      )}

      {tab === "sourcing" && isBloodBankRequester && (
        <div className="grid lg:grid-cols-5 gap-5">
          {/* Left: search + results */}
          <div className="lg:col-span-2 space-y-4">
            <div className="bg-card border border-border rounded-xl p-5">
              <h3 className="font-semibold text-foreground mb-4">Find blood supply</h3>
              <div className="mb-3">
                <ComponentPicker value={component} onChange={setComponent} />
              </div>
              <div className="mb-3">
                <label className="text-[13px] font-semibold text-muted-foreground block mb-1.5">
                  Blood type needed
                </label>
                <div className="grid grid-cols-4 gap-1.5">
                  {ALL_BLOOD_TYPES.map((bt) => (
                    <button
                      key={bt}
                      onClick={() => setSearchType(bt)}
                      className={`py-2 text-[14px] font-display font-bold rounded-lg border transition-all duration-200 ease-out ${
                        searchType === bt
                          ? "bg-primary text-white border-primary scale-110"
                          : "bg-primary-tint text-primary border-transparent hover:border-primary/40"
                      }`}
                    >
                      {bt}
                    </button>
                  ))}
                </div>
              </div>
              <div className="mb-4">
                <label className="text-[13px] font-semibold text-muted-foreground block mb-1.5">
                  Units needed
                </label>
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => setQuantityNeeded(Math.max(1, quantityNeeded - 1))}
                    className="w-8 h-8 border border-border rounded-lg text-foreground font-bold hover:bg-secondary flex items-center justify-center"
                  >
                    −
                  </button>
                  <span className="text-lg font-bold text-foreground w-8 text-center">{quantityNeeded}</span>
                  <button
                    onClick={() => setQuantityNeeded(quantityNeeded + 1)}
                    className="w-8 h-8 border border-border rounded-lg text-foreground font-bold hover:bg-secondary flex items-center justify-center"
                  >
                    +
                  </button>
                </div>
              </div>
            </div>

            {/* Blood type compatibility notice */}
            {(() => {
              const compatible = usesRedCellCompatibility(component) ? (BLOOD_COMPATIBILITY[searchType] ?? []).filter((t) => t !== searchType) : [];
              // Only claim "no exact match" once the list has loaded and nothing in it is available.
              const noExactMatch = !banksLoading && !banksError && !banks.some((b) => b.available);
              return compatible.length > 0 ? (
                <div className="bg-info-tint border border-info-border rounded-xl px-4 py-3 flex gap-3">
                  <AlertTriangle size={15} className="text-info shrink-0 mt-0.5" />
                  <div>
                    <div className="text-[13px] font-semibold text-info-text mb-1">
                      {noExactMatch ? "No exact match. Compatible alternatives:" : "Compatible alternatives if needed:"}
                    </div>
                    <div className="flex flex-wrap gap-1.5">
                      {compatible.map((t) => (
                        <button
                          key={t}
                          onClick={() => setSearchType(t)}
                          className="px-2.5 py-0.5 text-[13px] font-bold rounded border bg-card border-info-border text-info hover:bg-info-tint transition-colors"
                        >
                          {t}
                        </button>
                      ))}
                    </div>
                    <div className="text-[11px] text-info mt-1.5 leading-snug">
                      Based on standard donor-recipient compatibility. Always verify with clinical staff.
                    </div>
                  </div>
                </div>
              ) : null;
            })()}

            <div className="space-y-2">
              <div className="text-[14px] font-semibold text-foreground px-1">
                Nearest facilities — {searchType}{component ? ` ${component}` : ""} available
              </div>
              {component === null && (
                <div className="bg-card border border-border rounded-xl p-6 text-center text-[13px] text-muted-foreground">
                  Choose a component to see which facilities can supply it.
                </div>
              )}
              {banksLoading && (
                <div className="bg-card border border-border rounded-xl p-6 text-center text-[13px] text-muted-foreground">
                  Loading facilities…
                </div>
              )}
              {!banksLoading && banksError && (
                <div className="bg-status-critical-tint border border-status-critical-border rounded-xl p-4 text-center text-[13px] text-status-critical-text">
                  Failed to load facilities: {banksError}
                </div>
              )}
              {!banksLoading && !banksError && banks.map((bank, i) => (
                <div
                  key={bank.id}
                  onClick={() => setSelectedBank(bank.id)}
                  className={`w-full text-left bg-card border rounded-xl p-4 transition-all cursor-pointer ${
                    selectedBank === bank.id
                      ? "border-primary ring-2 ring-primary/20"
                      : "border-border hover:border-primary/40"
                  }`}
                >
                  <div className="flex items-start justify-between gap-2">
                    <div className="flex gap-3">
                      <div className="w-6 h-6 rounded-full bg-primary/10 text-primary font-bold text-[12px] flex items-center justify-center shrink-0 mt-0.5">
                        {i + 1}
                      </div>
                      <div>
                        <div className="font-semibold text-[14px] text-foreground leading-tight">
                          {bank.name}
                        </div>
                        <div className="text-[12px] text-muted-foreground mt-0.5">{bank.address}</div>
                        <div className="flex items-center gap-1 mt-1.5">
                          <MapPin size={11} className="text-muted-foreground" />
                          <span className="text-[13px] font-semibold text-foreground">{bank.distance_km} km</span>
                        </div>
                      </div>
                    </div>
                    <div className="shrink-0">
                      {bank.available ? (
                        <span className="flex items-center gap-1 text-[12px] font-semibold bg-status-safe-tint text-status-safe-text px-2 py-0.5 rounded-full border border-status-safe-border">
                          <CheckCircle size={11} /> Available
                        </span>
                      ) : (
                        <span className="flex items-center gap-1 text-[12px] font-semibold bg-gray-100 text-foreground px-2 py-0.5 rounded-full border border-gray-200">
                          Unavailable
                        </span>
                      )}
                    </div>
                  </div>
                  {!bank.available && bank.compatible_alternatives.length > 0 && (
                    <div className="mt-2 pt-2 border-t border-border">
                      <div className="text-[12px] text-info-text font-medium mb-1">
                        {searchType} unavailable here — compatible alternative{bank.compatible_alternatives.length > 1 ? "s" : ""} in stock:
                      </div>
                      <div className="flex flex-wrap gap-2">
                        {bank.compatible_alternatives.map((alt) => (
                          <button
                            key={alt.blood_type}
                            onClick={(e) => { e.stopPropagation(); setSearchType(alt.blood_type); }}
                            className="flex items-center gap-1.5 rounded-lg hover:opacity-80 transition-opacity"
                          >
                            <BloodTypeBadge type={alt.blood_type} size="lg" />
                            <span className="flex items-center gap-1 text-[12px] font-semibold bg-status-safe-tint text-status-safe-text px-2 py-0.5 rounded-full border border-status-safe-border">
                              <CheckCircle size={11} /> Available
                            </span>
                          </button>
                        ))}
                      </div>
                    </div>
                  )}
                  {selectedBank === bank.id && bank.available && (
                    <div className="mt-3 pt-3 border-t border-border">
                      {justSentBankId === bank.id ? (
                        <div className="w-full h-8 bg-status-safe-tint text-status-safe-text border border-status-safe-border rounded-lg text-[13px] font-semibold flex items-center justify-center gap-1.5">
                          <CheckCircle size={14} /> Request Sent
                        </div>
                      ) : (
                        <button
                          onClick={() => sendRequest(bank.id)}
                          disabled={submitting}
                          className="w-full h-8 bg-primary text-white text-[13px] font-semibold rounded-lg hover:bg-primary-hover transition-colors flex items-center justify-center gap-1.5 disabled:opacity-60"
                        >
                          <ArrowRight size={14} /> {submitting ? "Sending…" : "Request from this facility"}
                        </button>
                      )}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>

          {/* Facility detail panel */}
          <div className="lg:col-span-3 bg-card border border-border rounded-xl flex flex-col">
            {banksLoading && (
              <div className="flex-1 flex items-center justify-center text-center px-8 py-16 text-[14px] text-muted-foreground">
                Loading facilities…
              </div>
            )}
            {!banksLoading && banksError && (
              <div className="flex-1 flex items-center justify-center text-center px-8 py-16 text-[14px] text-status-critical-text">
                Failed to load facilities: {banksError}
              </div>
            )}
            {!banksLoading && !banksError && (selectedBank ? (() => {
              const bank = banks.find((b) => b.id === selectedBank)!;
              return (
                <>
                  <div className="px-6 py-4 border-b border-border flex items-center justify-between">
                    <div>
                      <h3 className="font-semibold text-foreground">{bank.name}</h3>
                      <p className="text-[13px] text-muted-foreground mt-0.5">{bank.address}</p>
                    </div>
                    {bank.available ? (
                      <span className="flex items-center gap-1 text-[13px] font-semibold bg-status-safe-tint text-status-safe-text px-3 py-1 rounded-full border border-status-safe-border">
                        <CheckCircle size={13} /> Available
                      </span>
                    ) : (
                      <span className="text-[13px] font-semibold bg-gray-100 text-foreground px-3 py-1 rounded-full border border-gray-200">
                        Unavailable
                      </span>
                    )}
                  </div>

                  {/* Real map — every ranked candidate is a real pin at its
                      real coordinates; the selected one is the larger, primary-
                      colored pin. Click any pin to select that facility. */}
                  <div className="mx-6 mt-5 rounded-xl overflow-hidden border border-border" style={{ height: 360 }}>
                    <FacilityNetworkMap key={`${searchType}-${component}`} banks={banks} selectedBankId={selectedBank} onSelectBank={setSelectedBank} />
                  </div>

                  <div className="px-6 py-5 space-y-3">
                    <div className="grid grid-cols-2 gap-3 text-[14px]">
                      {[
                        { label: "Distance", value: `${bank.distance_km} km` },
                        { label: "Availability", value: bank.available ? "Available" : "Unavailable" },
                        { label: "Blood type needed", value: `${searchType}${component ? ` ${component}` : ""} (${quantityNeeded} units)` },
                        // No exact stock number here — a facility never sees another
                        // facility's raw inventory count, matching the paper's
                        // Availability Status definition. "Availability" above is
                        // the complete signal; a separate tile repeating it as a
                        // number-shaped fact would be misleading, not just redundant.
                      ].map(({ label, value }) => (
                        <div key={label} className="bg-secondary rounded-lg px-4 py-3">
                          <div className="text-[12px] text-muted-foreground font-medium mb-0.5">{label}</div>
                          <div className="font-semibold text-foreground">{value}</div>
                        </div>
                      ))}
                    </div>

                    {!bank.available && bank.compatible_alternatives.length > 0 && (
                      <div className="bg-info-tint border border-info-border rounded-lg px-4 py-3">
                        <div className="text-[13px] font-semibold text-info-text mb-1.5">
                          {searchType} unavailable here — compatible alternatives in stock:
                        </div>
                        <div className="flex flex-wrap gap-2.5">
                          {bank.compatible_alternatives.map((alt) => (
                            <button
                              key={alt.blood_type}
                              onClick={() => setSearchType(alt.blood_type)}
                              className="flex items-center gap-1.5 rounded-lg hover:opacity-80 transition-opacity"
                            >
                              <BloodTypeBadge type={alt.blood_type} size="xl" />
                              <span className="flex items-center gap-1 text-[13px] font-semibold bg-status-safe-tint text-status-safe-text px-2.5 py-1 rounded-full border border-status-safe-border">
                                <CheckCircle size={13} /> Available
                              </span>
                            </button>
                          ))}
                        </div>
                        <div className="text-[11px] text-info mt-1.5 leading-snug">
                          Selecting a type re-runs the search — you'll need to review and confirm before sending a request for a different type.
                        </div>
                      </div>
                    )}

                    {bank.available && (
                      <>
                        <div>
                          <label className="text-[13px] font-semibold text-muted-foreground block mb-1.5">
                            Reason for request
                          </label>
                          {isBloodBankRequester ? (
                            <div className="py-2 px-3 text-[13px] font-bold rounded-lg border border-border bg-secondary text-foreground w-fit">
                              Restock
                            </div>
                          ) : (
                            <div className="grid grid-cols-3 gap-1.5">
                              {(Object.keys(EMERGENCY_TYPE_LABELS) as EmergencyType[]).map((et) => (
                                <button
                                  key={et}
                                  onClick={() => setEmergencyType(et)}
                                  className={`py-2 text-[13px] font-bold rounded-lg border transition-all duration-200 ease-out ${
                                    emergencyType === et
                                      ? "bg-primary text-white border-primary scale-105"
                                      : "bg-card text-foreground border-border hover:border-primary/40"
                                  }`}
                                >
                                  {EMERGENCY_TYPE_LABELS[et]}
                                </button>
                              ))}
                            </div>
                          )}
                        </div>

                        {submitError && (
                          <div className="text-[13px] text-status-critical-text bg-status-critical-tint border border-status-critical-border rounded-lg px-3 py-2">
                            Failed to send request: {submitError}
                          </div>
                        )}

                        {justSentBankId === bank.id ? (
                          <div className="w-full h-10 bg-status-safe-tint text-status-safe-text border border-status-safe-border rounded-lg text-[14px] font-semibold flex items-center justify-center gap-2">
                            <CheckCircle size={15} /> Request Sent
                          </div>
                        ) : (
                          <button
                            onClick={() => sendRequest(bank.id)}
                            disabled={submitting}
                            className="w-full h-10 bg-primary text-white text-[14px] font-semibold rounded-lg hover:bg-primary-hover transition-colors flex items-center justify-center gap-2 disabled:opacity-60"
                          >
                            <ArrowRight size={15} /> {submitting ? "Sending…" : "Send Request to This Facility"}
                          </button>
                        )}
                      </>
                    )}
                  </div>
                </>
              );
            })() : (
              <div className="flex-1 flex flex-col items-center justify-center text-center px-8 py-16">
                <div className="w-14 h-14 rounded-full bg-secondary flex items-center justify-center mb-4">
                  <MapPin size={22} className="text-muted-foreground" />
                </div>
                <h3 className="font-semibold text-foreground mb-1">Select a facility</h3>
                <p className="text-[14px] text-muted-foreground max-w-xs leading-relaxed">
                  Choose a blood bank from the list to view its details and send a request.
                </p>
              </div>
            ))}
          </div>
        </div>
      )}

      {tab === "transfer" && (
        <>
        <div className="grid lg:grid-cols-5 gap-5">
          {/* Transfer confirmation flow */}
          <div className="lg:col-span-2 space-y-4">
            {actionError && (
              <div className="text-[13px] text-status-critical-text bg-status-critical-tint border border-status-critical-border rounded-lg px-3 py-2">
                {actionError}
              </div>
            )}

            {/* Incoming requests — supplier-side actions. Only blood banks can
                ever be a supplying facility (see /requests' bloodbank-only
                check server-side), so a hospital never has anything to show
                here — the card is hidden rather than always rendering an
                empty "No incoming requests" state. */}
            {isBloodBankRequester && (
              <div className="bg-card border border-border rounded-xl p-5">
                <h3 className="font-semibold text-foreground mb-1">
                  Incoming requests — {
                    facilities.find((f) => f.id === actingFacilityId)?.name
                    ?? getCurrentUser()?.facility_name
                    ?? "…"
                  }
                </h3>
                <p className="text-[13px] text-muted-foreground mb-4">
                  Requests directed to you as the supplying facility
                </p>

                {incomingLoading && (
                  <div className="text-[13px] text-muted-foreground py-4 text-center">Loading…</div>
                )}
                {!incomingLoading && incomingError && (
                  <div className="text-[13px] text-status-critical-text py-4 text-center">Failed to load: {incomingError}</div>
                )}
                {!incomingLoading && !incomingError && incomingRequests.length === 0 && (
                  <div className="text-[13px] text-muted-foreground py-4 text-center">
                    No incoming requests for this facility.
                  </div>
                )}
                {!incomingLoading && !incomingError && incomingRequests.length > 0 && (
                  <IncomingSections
                    rows={incomingRequests}
                    busyId={actionPendingId}
                    onAction={performRequestAction}
                    selectedRequestId={selectedRequestId}
                    onOpenChat={setSelectedRequestId}
                  />
                )}
              </div>
            )}

            {/* Outgoing requests — requester-side confirmation */}
            <div className="bg-card border border-border rounded-xl p-5">
              <h3 className="font-semibold text-foreground mb-1">Your requests awaiting confirmation</h3>
              <p className="text-[13px] text-muted-foreground mb-4">Requests you sent that have been accepted</p>

              {requestsLoading && (
                <div className="text-[13px] text-muted-foreground py-4 text-center">Loading…</div>
              )}
              {!requestsLoading && requestsError && (
                <div className="text-[13px] text-status-critical-text py-4 text-center">Failed to load: {requestsError}</div>
              )}
              {!requestsLoading && !requestsError && (() => {
                const accepted = requests.filter((r) => r.status === "accepted" || r.status === "completed" || r.status === "failed");
                if (accepted.length === 0) {
                  return (
                    <div className="text-[13px] text-muted-foreground py-4 text-center">
                      Nothing accepted yet.
                    </div>
                  );
                }
                return (
                  <div className="space-y-2">
                    {accepted.map((req) => (
                      <AcceptedRequestCard
                        key={req.id}
                        req={req}
                        canConfirmReceipt={actingFacilityId === req.requesting_facility_id}
                        busy={actionPendingId === req.id}
                        isChatSelected={selectedRequestId === req.id}
                        onOpenChat={() => setSelectedRequestId(req.id)}
                        onConfirmReceipt={() => performRequestAction(req.id, "confirm-receipt")}
                      />
                    ))}
                  </div>
                );
              })()}
            </div>
          </div>

          {/* Coordination chat — real, per-request messages (see request_messages table) */}
          <div className="lg:col-span-3 bg-card border border-border rounded-xl flex flex-col" style={{ height: 600 }}>
            {(() => {
              const selectedRequest = [...requests, ...incomingRequests].find((r) => r.id === selectedRequestId);
              return (
                <>
                  <div className="px-5 py-4 border-b border-border flex items-center justify-between">
                    <div>
                      <h3 className="font-semibold text-foreground">Coordination chat</h3>
                      <p className="text-[13px] text-muted-foreground">
                        {selectedRequest
                          ? `REQ-${selectedRequest.id} · ${resolveFacilityName(selectedRequest.requesting_facility_id, selectedRequest.requesting_facility_name)} ↔ ${resolveFacilityName(selectedRequest.supplying_facility_id, selectedRequest.supplying_facility_name)} · ${selectedRequest.blood_type}${selectedRequest.component ? ` ${selectedRequest.component}` : ""} · ${selectedRequest.quantity} units`
                          : "Select a request from the left to view its coordination chat"}
                      </p>
                    </div>
                    {selectedRequestId !== null && (
                      <div className="flex items-center gap-4">
                        {detail !== null && (detail.request.supplier_confirmed_at || FINISHED_STATUSES.includes(detail.request.status)) && (
                          <a
                            href={`/receipt/${detail.request.id}`}
                            target="_blank"
                            rel="noopener"
                            className="h-8 px-3 inline-flex items-center rounded-md border border-border bg-card text-[13px] font-semibold text-foreground hover:bg-secondary transition-colors"
                          >
                            Receipt
                          </a>
                        )}
                      <div
                        role="status"
                        className={`flex items-center gap-1.5 text-[13px] font-semibold ${
                          chatLive === "live" ? "text-status-safe-text" : chatLive === "lost" ? "text-status-critical-text" : "text-muted-foreground"
                        }`}
                      >
                        <span
                          aria-hidden="true"
                          className={`w-2 h-2 rounded-full ${
                            chatLive === "live" ? "bg-status-safe animate-pulse" : chatLive === "lost" ? "bg-status-critical" : "bg-gray-400"
                          }`}
                        />
                        {chatLive === "live" ? "Live" : chatLive === "lost" ? "Connection lost, retrying" : "Connecting…"}
                      </div>
                      </div>
                    )}
                  </div>

                  <div ref={chatRef} className="flex-1 overflow-y-auto px-5 py-4 space-y-4">
                    {selectedRequestId === null && (
                      <div className="h-full flex flex-col items-center justify-center text-center px-8">
                        <div className="w-14 h-14 rounded-full bg-secondary flex items-center justify-center mb-4">
                          <MessageSquare size={22} className="text-muted-foreground" />
                        </div>
                        <h3 className="font-semibold text-foreground mb-1">No request selected</h3>
                        <p className="text-[14px] text-muted-foreground max-w-xs leading-relaxed">
                          Click "Open chat" on a request in either list to the left.
                        </p>
                      </div>
                    )}
                    {selectedRequestId !== null && messagesLoading && messages.length === 0 && (
                      <div className="text-center text-[13px] text-muted-foreground py-8">Loading messages…</div>
                    )}
                    {selectedRequestId !== null && messagesError && (
                      <div className="text-center text-[13px] text-status-critical-text py-2">{messagesError}</div>
                    )}
                    {selectedRequestId !== null && !messagesLoading && messages.length === 0 && !messagesError && detail === null && (
                      <div className="h-full flex flex-col items-center justify-center text-center px-8">
                        <div className="w-14 h-14 rounded-full bg-primary-tint flex items-center justify-center mb-4">
                          <MessageSquare size={22} className="text-primary" />
                        </div>
                        <h3 className="font-semibold text-foreground mb-1">No messages yet</h3>
                        <p className="text-[14px] text-muted-foreground max-w-xs leading-relaxed">
                          Say hello below to get coordination started with the other facility.
                        </p>
                      </div>
                    )}
                    {detail !== null && (
                      <RequestThread
                        items={mergeThread(messages, detail.timeline)}
                        request={detail.request}
                        actingFacilityId={actingFacilityId}
                      />
                    )}
                    {detail !== null && messages.length === 0 && (
                      <p className="text-center text-[13px] text-muted-foreground pt-1">No messages yet.</p>
                    )}
                  </div>

                  {(() => {
                    // Mirrors the backend's send_request_message guard exactly —
                    // sending is only meaningful once the other side can actually
                    // see it (the requester's own chat access only opens up once
                    // accepted), so a still-pending request can't send either.
                    const currentStatus = detail?.request.status ?? selectedRequest?.status;
                    const canSend = currentStatus !== undefined && ["accepted", "completed", "failed"].includes(currentStatus);
                    const placeholder =
                      selectedRequestId === null
                        ? "Select a request first…"
                        : canSend
                        ? "Type a message…"
                        : "Messaging opens once this request is accepted";
                    return (
                      <div className="px-4 py-3 border-t border-border flex gap-2">
                        <input
                          type="text"
                          value={chatInput}
                          onChange={(e) => setChatInput(e.target.value)}
                          onKeyDown={(e) => e.key === "Enter" && sendMessage()}
                          placeholder={placeholder}
                          disabled={!canSend || sendingMessage}
                          className="flex-1 h-9 px-3 text-[14px] border border-border rounded-lg bg-input-background focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary disabled:opacity-60"
                        />
                        <button
                          onClick={sendMessage}
                          disabled={!canSend || sendingMessage || !chatInput.trim()}
                          className="h-9 w-9 bg-primary text-white rounded-lg flex items-center justify-center hover:bg-primary-hover transition-colors disabled:opacity-50"
                        >
                          <Send size={15} />
                        </button>
                      </div>
                    );
                  })()}
                </>
              );
            })()}
          </div>
        </div>

        {/* Outgoing requests still awaiting a response — folded in from the
            former standalone "Pending Requests" tab so acting on a request
            (Incoming), watching one you sent (below), and confirming receipt
            (above) don't require switching tabs. */}
        <div className="space-y-4 mt-5">
          <div className="flex items-center justify-between">
            <div>
              <h3 className="font-semibold text-foreground">Outgoing requests</h3>
              <p className="text-[14px] text-muted-foreground mt-0.5">
                Immediate-use requests shown first, then by submission time — accepted requests aren't reshuffled
              </p>
            </div>
            <span className="text-[13px] font-semibold text-muted-foreground bg-secondary px-3 py-1 rounded-full border border-border">
              {pendingOnly.length} pending
            </span>
          </div>

          {requestsLoading && (
            <div className="bg-card border border-border rounded-xl p-10 text-center text-[14px] text-muted-foreground">
              Loading requests…
            </div>
          )}
          {!requestsLoading && requestsError && (
            <div className="bg-status-critical-tint border border-status-critical-border rounded-xl p-6 text-center text-[14px] text-status-critical-text">
              Failed to load requests: {requestsError}
            </div>
          )}
          {!requestsLoading && !requestsError && pendingOnly.length === 0 && (
            <div className="bg-card border border-border rounded-xl p-10 text-center text-[14px] text-muted-foreground">
              No pending requests. Send one from the Emergency Sourcing tab.
            </div>
          )}
          {!requestsLoading && !requestsError && pendingOnly.length > 0 && (
            <div className="bg-card border border-border rounded-xl overflow-hidden">
              <table className="w-full text-[14px]">
                <thead>
                  <tr className="border-b border-border bg-secondary">
                    {["Request ID", "Supplying facility", "Blood type", "Component", "Qty", "Reason", "Submitted", "Status", ""].map((h) => (
                      <th key={h} className="text-left py-3 px-4 text-[13px] font-semibold text-gray-600 whitespace-nowrap">
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {pendingOnly
                    .map((req) => {
                      const submittedTime = new Date(req.created_at).toLocaleTimeString([], {
                        hour: "2-digit",
                        minute: "2-digit",
                      });
                      return (
                        <tr key={req.id} className="border-b border-border last:border-0 hover:bg-secondary transition-colors">
                          <td className="py-3 px-4">
                            <span className="font-mono text-[13px] text-muted-foreground">REQ-{req.id}</span>
                          </td>
                          <td className="py-3 px-4 font-medium text-foreground">{req.supplying_facility_name}</td>
                          <td className="py-3 px-4">
                            <BloodTypeBadge type={req.blood_type} size="md" />
                          </td>
                          <td className="py-3 px-4 text-[14px] text-foreground whitespace-nowrap">{req.component ?? "Not recorded"}</td>
                          <td className="py-3 px-4 font-semibold text-foreground tabular-nums">{req.quantity}</td>
                          <td className="py-3 px-4">
                            <ReasonTag reason={req.emergency_type} />
                          </td>
                          <td className="py-3 px-4 font-mono text-[13px] text-muted-foreground">{submittedTime}</td>
                          <td className="py-3 px-4">
                            <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[12px] font-semibold bg-status-watch-tint text-status-watch-text border border-status-watch-border">
                              <Clock size={11} /> Awaiting response
                            </span>
                          </td>
                          <td className="py-3 px-4 text-right">
                            <button
                              onClick={() => setCancelConfirmId(req.id)}
                              disabled={actionPendingId === req.id}
                              className="h-7 px-3 bg-card border border-border text-[12px] font-semibold text-status-critical-text rounded-lg hover:bg-status-critical-tint transition-colors disabled:opacity-60"
                            >
                              Cancel Request
                            </button>
                          </td>
                        </tr>
                      );
                    })}
                </tbody>
              </table>
            </div>
          )}
        </div>
        </>
      )}

      {cancelConfirmId !== null && (() => {
        const req = requests.find((r) => r.id === cancelConfirmId);
        if (!req) return null;
        return (
          <Modal title="Cancel Request" onClose={() => setCancelConfirmId(null)}>
            <div className="space-y-4">
              <p className="text-[14px] text-muted-foreground">
                Cancel the {req.quantity}-unit {req.blood_type}{req.component ? ` ${req.component}` : ""} request to {req.supplying_facility_name}?
                This can't be undone — you'll need to submit a new request if you still need this blood type.
              </p>
              <div className="flex gap-2">
                <button
                  onClick={() => setCancelConfirmId(null)}
                  className="flex-1 h-10 border border-border rounded-md text-sm font-semibold text-foreground hover:bg-secondary transition-colors"
                >
                  Keep Request
                </button>
                <button
                  onClick={async () => {
                    setCancelConfirmId(null);
                    await performRequestAction(req.id, "cancel");
                  }}
                  className="flex-1 h-10 bg-status-critical-text text-white text-sm font-bold rounded-md hover:opacity-90 transition-colors"
                >
                  Cancel Request
                </button>
              </div>
            </div>
          </Modal>
        );
      })()}
    </div>
  );
}

