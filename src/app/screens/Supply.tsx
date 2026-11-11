import { useState, useRef, useEffect } from "react";
import { liveSync } from "../lib/liveSyncRuntime";
import { apiGet, apiPost, getMyFacilityProfile, listConversations } from "../lib/api";
import { getCurrentUser } from "../lib/session";
import { getDevFacilityId, isDevModeEnabled } from "../lib/devMode";
import { type Facility } from "../components/FacilityNetworkMap";
import { HospitalSourcingView } from "./HospitalSourcing";
import { MessagesTab } from "./Messages";
import { type RequestDetail } from "../lib/requestTimeline";
import { BLOOD_COMPONENTS, type BloodComponent } from "../lib/bloodComponents";
import { type EmergencyType } from "../lib/requestTypes";
import { sortConversations, type ConversationList } from "../lib/conversations";
import { TransferHistory } from "./TransferHistory";

// Supply (the top nav's tab, formerly "Requests"): finding blood supply, the conversations with the other facilities that your requests
// are part of, and the history of finished transfers. Three sub-tabs:
//   sourcing  "Emergency sourcing" (a hospital) / "Source supply" (a blood bank)
//   messages  "Messages": one conversation per other facility, with that facility's requests as cards inside it (screens/Messages.tsx)
//   history   "Transfer history"
// The conversation list lives here, not in the Messages tab, so the tab's badge (requests that need this facility's action) is right
// whichever sub-tab is open. It reloads when the shared poller (lib/liveSync.ts) says the messages token or either requests token moved
// (about 15 s after the other facility acts, never while the tab is hidden, and at once when it becomes visible); silent, so a failed
// refresh leaves what is on screen alone.

type SupplyTab = "sourcing" | "messages" | "history";

type ActingFacility = { id: number; name: string; facility_type: string };

export function SupplyScreen({
  initialSearchType, initialQuantity, initialComponent, onConsumedSourcingPrefill,
  initialTab, onConsumedInitialTab, initialHighlightRequestId, onConsumedHighlight,
}: {
  initialSearchType?: string | null;
  initialQuantity?: number | null;
  initialTab?: "messages" | null;
  onConsumedInitialTab?: () => void;
  initialComponent?: string | null;
  onConsumedSourcingPrefill?: () => void;
  initialHighlightRequestId?: number | null;
  onConsumedHighlight?: () => void;
}) {
  const [tab, setTab] = useState<SupplyTab>(initialHighlightRequestId || initialTab ? "messages" : "sourcing");
  useEffect(() => { if (initialTab) onConsumedInitialTab?.(); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const [searchType, setSearchType] = useState(initialSearchType ?? "O-");
  const [quantityNeeded, setQuantityNeeded] = useState(3);

  // Acting facility resolves the same way api.ts resolves it server-side: the dev override (if ?dev=1 and the global DevFacilityBanner
  // picked one) wins, else the real logged-in user's own facility.
  const actingFacilityId = getDevFacilityId() ?? getCurrentUser()?.facility_id ?? null;

  // Only fetched for display-name resolution when dev-overriding to a facility that isn't the logged-in user's own.
  const [facilities, setFacilities] = useState<ActingFacility[]>([]);
  useEffect(() => {
    if (!isDevModeEnabled()) return;
    apiGet<ActingFacility[]>("/facilities").then(setFacilities);
  }, []);

  // Blood banks have no patients, so trauma/scheduled_surgery (which grant non-preemptive priority ahead of restock, see
  // IMMEDIATE_USE_TYPES in main.py) can never legitimately apply to a blood-bank-originated request. The server enforces this regardless
  // of what's sent here; this just keeps the UI honest about the only value that will actually land. Derived from the ACTING facility
  // (dev override aware).
  const isBloodBankRequester = isDevModeEnabled()
    ? facilities.find((f) => f.id === actingFacilityId)?.facility_type === "bloodbank"
    : getCurrentUser()?.facility_type === "bloodbank";
  // A hospital has no reason until it chooses one: the reason decides where the request is listed among the supplier's pending requests
  // (trauma and scheduled surgery ahead of restock), so it is never pre-selected. A blood bank's is always restock.
  const [emergencyType, setEmergencyType] = useState<EmergencyType | null>(isBloodBankRequester ? "restock" : null);
  // Which component to look for and request. Never pre-selected, for either role: release reserves units of exactly this component, and
  // the search below does not run until it is chosen.
  const [component, setComponent] = useState<BloodComponent | null>(null);

  // This facility's own location, for the "You" marker on the sourcing map. Not under the dev facility override, where /facilities/me
  // would describe the wrong facility.
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

  // The conversation open in Messages, and a request whose card to go to once it is loaded.
  const [selectedFacilityId, setSelectedFacilityId] = useState<number | null>(null);
  const [focusRequestId, setFocusRequestId] = useState<number | null>(null);

  // A request id arrives from a notification ("requests:12") or from Transfer history's "Chat": find the facility on the other side of it,
  // open that conversation, and go to the request's card.
  async function openRequest(requestId: number) {
    try {
      const detail = await apiGet<RequestDetail>(`/requests/${requestId}`);
      const { requesting_facility: from, supplying_facility: to } = detail.request;
      setSelectedFacilityId(from.id === actingFacilityId ? to.id : from.id);
      setFocusRequestId(requestId);
    } catch {
      // not a request this facility can open: stay on the list
    }
  }
  useEffect(() => {
    if (initialHighlightRequestId) {
      setTab("messages");
      void openRequest(initialHighlightRequestId);
      onConsumedHighlight?.();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialHighlightRequestId]);

  // Consume each one-shot prefill whenever it actually arrives, keyed off the prop itself, not mount: this screen does NOT remount when a
  // notification is clicked while already sitting on Supply (screen state stays, so only the props change). Each effect re-firing on the
  // same value it just consumed (App() resets the corresponding state to null right after) is harmless: the `if` guard skips it.
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

  const [banks, setBanks] = useState<Facility[]>([]);
  const [banksLoading, setBanksLoading] = useState(true);
  const [banksError, setBanksError] = useState<string | null>(null);

  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [justSentBankId, setJustSentBankId] = useState<number | null>(null);

  // ─── The conversation list ─────────────────────────────────────────────────
  const [conversations, setConversations] = useState<ConversationList | null>(null);
  const [conversationsLoading, setConversationsLoading] = useState(true);
  const [conversationsError, setConversationsError] = useState<string | null>(null);
  const conversationsRequestIdRef = useRef(0);
  // Sequence guard: a slower earlier answer must not land after a faster later one and revert the list.
  function fetchConversations(silent = false) {
    const requestId = ++conversationsRequestIdRef.current;
    if (!silent) {
      setConversationsLoading(true);
      setConversationsError(null);
    }
    listConversations()
      .then((data) => { if (requestId === conversationsRequestIdRef.current) setConversations({ ...data, conversations: sortConversations(data.conversations) }); })
      .catch((err) => { if (!silent && requestId === conversationsRequestIdRef.current) setConversationsError(err instanceof Error ? err.message : "Failed to load"); })
      .finally(() => { if (requestId === conversationsRequestIdRef.current) setConversationsLoading(false); });
  }
  // Declared before the first-load effect on purpose: a screen subscribes just before its first fetch (see liveSync.ts). A message, a
  // read position (messages), a request arriving or moving (either requests token) all change a row of the list.
  useEffect(() => {
    const off = [
      liveSync.subscribe("messages", () => fetchConversations(true)),
      liveSync.subscribe("requestsIncoming", () => fetchConversations(true)),
      liveSync.subscribe("requestsOutgoing", () => fetchConversations(true)),
    ];
    return () => off.forEach((f) => f());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [actingFacilityId]);
  useEffect(() => { fetchConversations(); }, [actingFacilityId]); // eslint-disable-line react-hooks/exhaustive-deps

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
        // Default to the nearest facility that actually has enough stock; fall back to just-nearest if none currently qualify.
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
      fetchConversations(true);
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : "Failed to send request");
    } finally {
      setSubmitting(false);
    }
  }

  const needsAction = conversations?.needs_action ?? 0;
  const sourcingLabel = isBloodBankRequester ? "Source supply" : "Emergency sourcing";
  const TAB_LABELS: Record<SupplyTab, string> = { sourcing: sourcingLabel, messages: "Messages", history: "Transfer history" };

  return (
    <div className="max-w-screen-2xl mx-auto px-4 sm:px-6 py-6">
      {/* Sub-tabs */}
      <div className="flex gap-1 mb-6 bg-secondary rounded-lg p-1 w-fit max-w-full overflow-x-auto">
        {(["sourcing", "messages", "history"] as SupplyTab[]).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            aria-pressed={tab === t}
            className={`relative px-3 sm:px-5 py-2 text-[14px] font-semibold rounded border transition-all duration-200 ease-out whitespace-nowrap ${
              tab === t
                ? "bg-card text-foreground shadow-sm border-border scale-105"
                : "border-transparent text-muted-foreground hover:text-foreground"
            }`}
          >
            {TAB_LABELS[t]}
            {t === "messages" && needsAction > 0 && (
              <span className="absolute -top-1 -right-1 w-4 h-4 bg-primary text-white text-[10px] font-bold rounded-full flex items-center justify-center">
                {needsAction}<span className="sr-only"> {needsAction === 1 ? "request needs" : "requests need"} your action</span>
              </span>
            )}
          </button>
        ))}
      </div>

      {tab === "history" && (
        <TransferHistory isBloodBank={isBloodBankRequester} onOpenThread={(id) => { setTab("messages"); void openRequest(id); }} />
      )}

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
          onOpenRequests={() => setTab("messages")}
        />
      )}

      {tab === "messages" && (
        <MessagesTab
          me={actingFacilityId}
          myFacilityName={getCurrentUser()?.facility_name ?? "your facility"}
          list={conversations}
          listLoading={conversationsLoading}
          listError={conversationsError}
          onListChanged={() => fetchConversations(true)}
          onListLocal={(change) => setConversations((cur) => {
            if (!cur) return cur;
            const next = change(cur);
            return { ...next, conversations: sortConversations(next.conversations) };
          })}
          selectedFacilityId={selectedFacilityId}
          onSelectFacility={setSelectedFacilityId}
          focusRequestId={focusRequestId}
          onFocusConsumed={() => setFocusRequestId(null)}
          sourcingLabel={sourcingLabel}
          onOpenSourcing={() => setTab("sourcing")}
        />
      )}
    </div>
  );
}
