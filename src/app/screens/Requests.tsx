import { useState, useRef, useEffect } from "react";
import { ArrowLeft, MessageSquare, Send } from "lucide-react";
import { apiGet, apiPost, getMyFacilityProfile } from "../lib/api";
import { getCurrentUser } from "../lib/session";
import { getDevFacilityId, isDevModeEnabled } from "../lib/devMode";
import { Modal } from "../components/Modal";
import { type Facility } from "../components/FacilityNetworkMap";
import { HospitalSourcingView } from "./HospitalSourcing";
import { RequestThread } from "../components/RequestThread";
import { listIsBehind, mergeThread, type RequestDetail } from "../lib/requestTimeline";
import { BLOOD_COMPONENTS, type BloodComponent } from "../lib/bloodComponents";
import { type EmergencyType, type RequestRow } from "../lib/requestTypes";
import { RequestInboxRow } from "../components/RequestInboxRow";
import { buildInbox, inboxCounts, loadSeen, requestPhase, saveSeen, seedSeen, unreadKind, type SeenMap } from "../lib/requestInbox";
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


export function RequestsScreen({
  initialSearchType, initialQuantity, initialComponent, onConsumedSourcingPrefill,
  initialTab, onConsumedInitialTab, initialHighlightRequestId, onConsumedHighlight,
}: {
  initialSearchType?: string | null;
  initialQuantity?: number | null;
  initialTab?: "transfer" | null;
  onConsumedInitialTab?: () => void;
  initialComponent?: string | null;
  onConsumedSourcingPrefill?: () => void;
  initialHighlightRequestId?: number | null;
  onConsumedHighlight?: () => void;
}) {
  const [tab, setTab] = useState<RequestTab>(initialHighlightRequestId || initialTab ? "transfer" : "sourcing");
  useEffect(() => { if (initialTab) onConsumedInitialTab?.(); }, []); // eslint-disable-line react-hooks/exhaustive-deps
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

  // This facility's own location, for the "You" marker on the sourcing map (a blood bank's too: the search ranks by distance
  // from it). Not under the dev facility override, where /facilities/me would describe the wrong facility.
  const [origin, setOrigin] = useState<[number, number] | null>(null);
  useEffect(() => {
    if (isDevModeEnabled() && getDevFacilityId() !== null) return;
    let cancelled = false;
    getMyFacilityProfile()
      .then((profile) => {
        if (!cancelled && profile.latitude !== null && profile.longitude !== null) setOrigin([profile.latitude, profile.longitude]);
      })
      .catch(() => {}); // the map simply has no "You" marker
    return () => { cancelled = true; };
  }, []);
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
  // The open thread's poll, so an action taken from the thread (or a card) shows its event at once, not at the next poll.
  const refreshThreadRef = useRef<(() => void) | null>(null);
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
      if (initialQuantity && initialQuantity >= 1) setQuantityNeeded(initialQuantity); // "Order 90 by 4 Nov" arrives with its 90
      if (initialComponent && (BLOOD_COMPONENTS as readonly string[]).includes(initialComponent)) setComponent(initialComponent as BloodComponent);
      setTab("sourcing");
      onConsumedSourcingPrefill?.();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialSearchType, initialQuantity]);

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
  // silent = a background refresh, as for fetchIncoming below. The tab badge's outgoing part (receipts to confirm) comes from this list, so it refreshes with it.
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
  // does not poll, so it can be a long way behind). The requester's own list is polled on every tab, since the badge is on
  // every tab; the supplier's inbox only on the tab that shows it, and only blood banks have one. So the badge's incoming part
  // (decisions and releases) is as fresh as the last visit to this tab, or the load when the screen mounted.
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
      refreshThreadRef.current?.();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Action failed");
      actedRef.current.delete(requestId); // it did not happen, so the next change to this request is not this facility's own
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
    refreshThreadRef.current = poll;
    setMessagesLoading(true);
    setDetail(null);
    setChatLive("connecting");
    poll();
    const interval = setInterval(poll, CHAT_MESSAGE_POLL_MS);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      cancelled = true; clearInterval(interval); document.removeEventListener("visibilitychange", onVisible);
      if (refreshThreadRef.current === poll) refreshThreadRef.current = null;
    };
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

  // The inbox, and which of its rows have moved since this browser last had them open (see lib/requestInbox.ts).
  // `counts.action` is the tab's badge and the first figure above the list: one number, so they cannot disagree.
  const inbox = buildInbox(requests, incomingRequests, selectedRequestId);
  const counts = inboxCounts(inbox);
  const inboxLoading = requestsLoading || (isBloodBankRequester && incomingLoading);
  const [seen, setSeen] = useState<SeenMap | null>(() => loadSeen(actingFacilityId));
  // Requests this facility just acted on from a row, with the phase they were in, so its own Accept is not flagged "Updated".
  const actedRef = useRef<Map<number, string>>(new Map());
  // First load in this browser: what is on screen counts as seen. Not after a failed load, which would seed an empty list.
  useEffect(() => {
    if (seen === null && !inboxLoading && !requestsError && !incomingError) setSeen(seedSeen([...requests, ...incomingRequests]));
  }, [seen, inboxLoading, requestsError, incomingError, requests, incomingRequests]);
  // The open request counts as seen, and stays seen as it moves while it is open.
  const openRow = inbox.find((r) => r.req.id === selectedRequestId);
  const openPhase = openRow ? requestPhase(openRow.req) : null;
  useEffect(() => {
    if (seen === null || selectedRequestId === null || openPhase === null) return;
    if (seen[selectedRequestId] !== openPhase) setSeen({ ...seen, [selectedRequestId]: openPhase });
  }, [seen, selectedRequestId, openPhase]);
  useEffect(() => {
    if (seen === null || actedRef.current.size === 0) return;
    const next = { ...seen };
    let changed = false;
    for (const [id, before] of actedRef.current) {
      const row = [...requests, ...incomingRequests].find((r) => r.id === id);
      if (row && requestPhase(row) !== before) { next[id] = requestPhase(row); actedRef.current.delete(id); changed = true; }
    }
    if (changed) setSeen(next);
  }, [seen, requests, incomingRequests]);
  useEffect(() => { if (seen) saveSeen(actingFacilityId, seen); }, [seen]); // eslint-disable-line react-hooks/exhaustive-deps

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
            {t === "sourcing" ? (isBloodBankRequester ? "Source supply" : "Emergency sourcing") : t === "transfer" ? "Request and chats" : "Transfer history"}
            {t === "transfer" && counts.action > 0 && (
              <span className="absolute -top-1 -right-1 w-4 h-4 bg-primary text-white text-[10px] font-bold rounded-full flex items-center justify-center">
                {counts.action}<span className="sr-only"> {counts.action === 1 ? "request needs" : "requests need"} your action</span>
              </span>
            )}
          </button>
        ))}
      </div>

      {tab === "history" && <TransferHistory isBloodBank={isBloodBankRequester} onOpenThread={(id) => { setSelectedRequestId(id); setTab("transfer"); }} />}

      {tab === "sourcing" && (
        <HospitalSourcingView
          role={isBloodBankRequester ? "bloodbank" : "hospital"}
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

      {tab === "transfer" && (
        <>
        <div className="grid lg:grid-cols-5 gap-5">
          {/* The inbox: every request this facility is a party to, in one list. On a narrow screen the list is the whole screen
              until a request is opened, then the thread replaces it (with a way back); from lg up the two sit side by side. */}
          <div className={`lg:col-span-2 bg-card border border-border rounded-xl flex-col lg:h-[600px] ${selectedRequestId !== null ? "hidden lg:flex" : "flex"}`}>
            <div className="px-5 py-4 border-b border-border">
              <h3 className="font-semibold text-foreground">Requests</h3>
              <p className="text-[13px] text-muted-foreground mt-0.5">
                <span className="font-semibold text-foreground">{counts.action} {counts.action === 1 ? "needs" : "need"} your action · {counts.other} waiting on the other facility</span>
                <br />
                Waiting for a decision, then accepted. Immediate-use requests shown first, then by submission time — accepted requests aren't reshuffled
              </p>
            </div>
            <div className="flex-1 min-h-0 overflow-y-auto p-3">
              {actionError && (
                <div role="alert" className="mb-2 text-[13px] text-status-critical-text bg-status-critical-tint border border-status-critical-border rounded-lg px-3 py-2">
                  {actionError}
                </div>
              )}
              {requestsError && (
                <div role="alert" className="mb-2 text-[13px] text-status-critical-text">Failed to load your requests: {requestsError}</div>
              )}
              {isBloodBankRequester && incomingError && (
                <div role="alert" className="mb-2 text-[13px] text-status-critical-text">Failed to load incoming requests: {incomingError}</div>
              )}
              {inbox.length === 0 && inboxLoading && (
                <div className="text-[13px] text-muted-foreground py-8 text-center">Loading requests…</div>
              )}
              {inbox.length === 0 && !inboxLoading && !requestsError && !incomingError && (
                <div className="text-center px-4 py-10">
                  <h4 className="text-[14px] font-semibold text-foreground mb-1">No requests in progress</h4>
                  <p className="text-[13px] text-muted-foreground leading-relaxed">
                    Send one from {isBloodBankRequester ? "Source supply" : "Emergency sourcing"}. Finished requests are in Transfer history.
                  </p>
                </div>
              )}
              {inbox.length > 0 && (
                <ul className="space-y-2">
                  {inbox.map((row) => (
                    <RequestInboxRow
                      key={row.req.id}
                      row={row}
                      selected={selectedRequestId === row.req.id}
                      unread={unreadKind(row, seen)}
                      busy={actionPendingId === row.req.id}
                      onOpen={() => setSelectedRequestId(row.req.id)}
                      onAction={(action) => {
                        if (action === "cancel") { setCancelConfirmId(row.req.id); return; } // cancel is not undoable: confirm first
                        actedRef.current.set(row.req.id, requestPhase(row.req));
                        performRequestAction(row.req.id, action);
                      }}
                    />
                  ))}
                </ul>
              )}
            </div>
          </div>

          {/* Coordination chat — real, per-request messages (see request_messages table) */}
          <div className={`lg:col-span-3 bg-card border border-border rounded-xl flex-col h-[600px] max-h-[85vh] ${selectedRequestId === null ? "hidden lg:flex" : "flex"}`}>
            {(() => {
              const selectedRequest = [...requests, ...incomingRequests].find((r) => r.id === selectedRequestId);
              return (
                <>
                  <div className="px-5 py-4 border-b border-border flex items-center justify-between">
                    <div>
                      {selectedRequestId !== null && (
                        <button
                          type="button"
                          onClick={() => setSelectedRequestId(null)}
                          className="lg:hidden mb-1.5 inline-flex items-center gap-1 text-[13px] font-semibold text-muted-foreground hover:text-foreground transition-colors"
                        >
                          <ArrowLeft size={14} aria-hidden="true" /> Requests
                        </button>
                      )}
                      <h3 className="font-semibold text-foreground">Coordination chat</h3>
                      <p className="text-[13px] text-muted-foreground">
                        {selectedRequest
                          ? `REQ-${selectedRequest.id} · ${resolveFacilityName(selectedRequest.requesting_facility_id, selectedRequest.requesting_facility_name)} ↔ ${resolveFacilityName(selectedRequest.supplying_facility_id, selectedRequest.supplying_facility_name)} · ${selectedRequest.blood_type}${selectedRequest.component ? ` ${selectedRequest.component}` : ""} · ${selectedRequest.quantity} units`
                          : "Open a request from the list to see its thread"}
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
                        <h3 className="font-semibold text-foreground mb-1">Open a request</h3>
                        <p className="text-[14px] text-muted-foreground max-w-xs leading-relaxed">
                          Choose one from the list to see its history and talk to the other facility.
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
                        renderAction={(event) => {
                          // The accept event is where the supplier confirms release; the release event is where the requester
                          // confirms receipt. Only while that step is the one waiting, and only for the facility that owns it.
                          const r = detail.request;
                          const waitingOnRelease = r.status === "accepted" && !r.supplier_confirmed_at;
                          const waitingOnReceipt = r.status === "accepted" && !!r.supplier_confirmed_at && !r.requester_confirmed_at;
                          const action: RequestAction | null =
                            event.kind === "accepted" && waitingOnRelease && actingFacilityId === r.supplying_facility.id ? "confirm-release"
                            : event.kind === "released" && waitingOnReceipt && actingFacilityId === r.requesting_facility.id ? "confirm-receipt"
                            : null;
                          if (action === null) return null;
                          const busy = actionPendingId === r.id;
                          return (
                            <div className="mt-2">
                              <button
                                type="button"
                                onClick={() => performRequestAction(r.id, action)}
                                disabled={busy}
                                className="h-8 px-3 bg-primary text-white text-[13px] font-semibold rounded-md hover:bg-primary-hover transition-colors disabled:opacity-60"
                              >
                                {busy ? "Confirming…" : action === "confirm-release" ? "Confirm release" : "Confirm receipt"}
                              </button>
                              {actionError && <p role="alert" className="mt-1.5 text-[13px] text-status-critical-text">{actionError}</p>}
                            </div>
                          );
                        }}
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

