import { useState, useRef, useEffect } from "react";
import { AlertTriangle, ArrowRight, CheckCircle, Clock, MapPin, MessageSquare, Send } from "lucide-react";
import { apiGet, apiPost } from "../lib/api";
import { getCurrentUser } from "../lib/session";
import { getDevFacilityId, isDevModeEnabled } from "../lib/devMode";
import { BloodTypeBadge } from "../components/BloodTypeBadge";
import { Modal } from "../components/Modal";
import { FacilityNetworkMap, type Facility } from "../components/FacilityNetworkMap";
import { useFlashOnChange } from "../lib/motion";

// Chat messages poll faster than notifications (App.tsx's NOTIFICATION_POLL_MS)
// since a coordination chat is a live conversation, not a background alert.
const CHAT_MESSAGE_POLL_MS = 4000;

type RequestTab = "sourcing" | "transfer";

const bloodCompatibility: Record<string, string[]> = {
  "A+":  ["A+", "A-", "O+", "O-"],
  "A-":  ["A-", "O-"],
  "B+":  ["B+", "B-", "O+", "O-"],
  "B-":  ["B-", "O-"],
  "O+":  ["O+", "O-"],
  "O-":  ["O-"],
  "AB+": ["A+", "A-", "B+", "B-", "O+", "O-", "AB+", "AB-"],
  "AB-": ["A-", "B-", "O-", "AB-"],
};

// ─── Requests (Emergency Sourcing + Transfer) ─────────────────────────────────

type EmergencyType = "trauma" | "scheduled_surgery" | "restock";

const EMERGENCY_TYPE_LABELS: Record<EmergencyType, string> = {
  trauma: "Trauma",
  scheduled_surgery: "Scheduled Surgery",
  restock: "Restock",
};

type RequestRow = {
  id: number;
  requesting_facility_id: number;
  supplying_facility_id: number;
  blood_type: string;
  quantity: number;
  emergency_type: EmergencyType;
  status: string;
  created_at: string;
  supplier_confirmed_at: string | null;
  requester_confirmed_at: string | null;
  supplying_facility_name?: string;
  requesting_facility_name?: string;
};

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

function IncomingRequestCard({
  req, busy, onAction, isChatSelected, onOpenChat,
}: {
  req: RequestRow;
  busy: boolean;
  onAction: (id: number, action: RequestAction) => void;
  isChatSelected: boolean;
  onOpenChat: (id: number) => void;
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
      <div className="flex items-center justify-between mb-1.5">
        <div className="text-[14px] font-semibold text-foreground">
          {req.requesting_facility_name ?? "Unknown facility"}
        </div>
        <BloodTypeBadge type={req.blood_type} size="md" />
      </div>
      <div className="flex items-center gap-1.5 mb-2 text-[12px] text-muted-foreground">
        <MessageSquare size={12} className="text-info shrink-0" />
        {req.quantity} units · {EMERGENCY_TYPE_LABELS[req.emergency_type]}
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

      {req.status === "completed" && (
        <span className="flex items-center gap-1 animate-success-pop text-[12px] font-semibold text-status-safe-text">
          <CheckCircle size={12} /> Completed
        </span>
      )}

      {req.status === "declined" && (
        <span className="text-[12px] font-semibold text-muted-foreground">Declined</span>
      )}

      {req.status === "cancelled" && (
        <span className="text-[12px] font-semibold text-muted-foreground">Cancelled by requester</span>
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
      <div className="flex items-center gap-1.5 mb-2 text-[13px] text-muted-foreground">
        <MessageSquare size={13} className="text-info shrink-0" />
        {req.quantity} units · {EMERGENCY_TYPE_LABELS[req.emergency_type]}
      </div>

      {req.status === "completed" ? (
        <span className="flex items-center gap-1 animate-success-pop text-[13px] font-semibold text-status-safe-text">
          <CheckCircle size={13} /> Completed
        </span>
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
  // Blood banks have no patients, so trauma/scheduled_surgery (which grant
  // non-preemptive priority ahead of restock — see IMMEDIATE_USE_TYPES in
  // main.py) can never legitimately apply to a blood-bank-originated
  // request. The server enforces this regardless of what's sent here; this
  // just keeps the UI honest about the only value that will actually land.
  const isBloodBankRequester = getCurrentUser()?.facility_type === "bloodbank";
  const [emergencyType, setEmergencyType] = useState<EmergencyType>(isBloodBankRequester ? "restock" : "trauma");
  const [selectedBank, setSelectedBank] = useState<number | null>(null);
  const [chatInput, setChatInput] = useState("");
  const [selectedRequestId, setSelectedRequestId] = useState<number | null>(initialHighlightRequestId ?? null);
  const [messages, setMessages] = useState<RequestMessage[]>([]);
  const [messagesLoading, setMessagesLoading] = useState(false);
  const [messagesError, setMessagesError] = useState<string | null>(null);
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

  function fetchRequests() {
    setRequestsLoading(true);
    setRequestsError(null);
    apiGet<RequestRow[]>("/requests")
      .then((data) => setRequests(data))
      .catch((err) => setRequestsError(err instanceof Error ? err.message : "Failed to load"))
      .finally(() => setRequestsLoading(false));
  }

  useEffect(() => {
    fetchRequests();
  }, []);

  function fetchIncoming() {
    if (actingFacilityId === null) return;
    setIncomingLoading(true);
    setIncomingError(null);
    apiGet<RequestRow[]>("/requests/incoming")
      .then((data) => setIncomingRequests(data))
      .catch((err) => setIncomingError(err instanceof Error ? err.message : "Failed to load"))
      .finally(() => setIncomingLoading(false));
  }

  useEffect(() => {
    fetchIncoming();
  }, [actingFacilityId]);

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
    setSubmitting(true);
    setSubmitError(null);
    try {
      await apiPost("/requests", {
        supplying_facility_id: bankId,
        blood_type: searchType,
        quantity: quantityNeeded,
        emergency_type: emergencyType,
      });
      setJustSentBankId(bankId);
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

  // Real coordination-chat history for whichever request is selected, scoped
  // server-side to the two facilities on either side of it. Polled rather than
  // pushed — there's no websocket layer in this app, and 4s is close enough to
  // "live" at this scale.
  useEffect(() => {
    if (selectedRequestId === null) {
      setMessages([]);
      setMessagesError(null);
      return;
    }
    let cancelled = false;
    function fetchMessages() {
      apiGet<RequestMessage[]>(`/requests/${selectedRequestId}/messages`)
        .then((data) => {
          if (cancelled) return;
          setMessages(data);
          setMessagesError(null);
        })
        .catch((err) => {
          if (!cancelled) setMessagesError(err instanceof Error ? err.message : "Failed to load");
        })
        .finally(() => {
          if (!cancelled) setMessagesLoading(false);
        });
    }
    setMessagesLoading(true);
    fetchMessages();
    const interval = setInterval(fetchMessages, CHAT_MESSAGE_POLL_MS);
    return () => { cancelled = true; clearInterval(interval); };
  }, [selectedRequestId]);

  useEffect(() => {
    let cancelled = false;
    setBanksLoading(true);
    setBanksError(null);
    setJustSentBankId(null);
    setSubmitError(null);
    const params = new URLSearchParams({ blood_type: searchType, quantity: String(quantityNeeded) });
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
  }, [searchType, quantityNeeded]);

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

  const bloodTypes = ["A+", "A-", "B+", "B-", "O+", "O-", "AB+", "AB-"];
  const pendingOnly = requests.filter((r) => r.status === "pending");

  return (
    <div className="max-w-screen-2xl mx-auto px-6 py-6">
      {/* Sub-tabs */}
      <div className="flex gap-1 mb-6 bg-secondary rounded-lg p-1 w-fit">
        {(["sourcing", "transfer"] as RequestTab[]).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`relative px-5 py-2 text-[14px] font-semibold rounded border transition-all duration-200 ease-out ${
              tab === t
                ? "bg-card text-foreground shadow-sm border-border scale-105"
                : "border-transparent text-muted-foreground hover:text-foreground"
            }`}
          >
            {t === "sourcing" ? "Emergency Sourcing" : "Request and Chats"}
            {t === "transfer" && pendingOnly.length > 0 && (
              <span className="absolute -top-1 -right-1 w-4 h-4 bg-primary text-white text-[10px] font-bold rounded-full flex items-center justify-center">
                {pendingOnly.length}
              </span>
            )}
          </button>
        ))}
      </div>

      {tab === "sourcing" && (
        <div className="grid lg:grid-cols-5 gap-5">
          {/* Left: search + results */}
          <div className="lg:col-span-2 space-y-4">
            <div className="bg-card border border-border rounded-xl p-5">
              <h3 className="font-semibold text-foreground mb-4">Find Blood Supply</h3>
              <div className="mb-3">
                <label className="text-[13px] font-semibold text-muted-foreground block mb-1.5">
                  Blood type needed
                </label>
                <div className="grid grid-cols-4 gap-1.5">
                  {bloodTypes.map((bt) => (
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
              const compatible = (bloodCompatibility[searchType] ?? []).filter((t) => t !== searchType);
              return compatible.length > 0 ? (
                <div className="bg-info-tint border border-info-border rounded-xl px-4 py-3 flex gap-3">
                  <AlertTriangle size={15} className="text-info shrink-0 mt-0.5" />
                  <div>
                    <div className="text-[13px] font-semibold text-info-text mb-1">
                      No exact match? Compatible alternatives:
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
              <div className="text-[12px] font-bold uppercase tracking-wider text-muted-foreground px-1">
                Nearest Facilities — {searchType} Available
              </div>
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
                    <FacilityNetworkMap key={searchType} banks={banks} selectedBankId={selectedBank} onSelectBank={setSelectedBank} />
                  </div>

                  <div className="px-6 py-5 space-y-3">
                    <div className="grid grid-cols-2 gap-3 text-[14px]">
                      {[
                        { label: "Distance", value: `${bank.distance_km} km` },
                        { label: "Availability", value: bank.available ? "Available" : "Unavailable" },
                        { label: "Blood type needed", value: `${searchType} (${quantityNeeded} units)` },
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
                  Incoming Requests — {
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
                  <div className="space-y-2">
                    {incomingRequests.map((req) => (
                      <IncomingRequestCard
                        key={req.id}
                        req={req}
                        busy={actionPendingId === req.id}
                        onAction={performRequestAction}
                        isChatSelected={selectedRequestId === req.id}
                        onOpenChat={setSelectedRequestId}
                      />
                    ))}
                  </div>
                )}
              </div>
            )}

            {/* Outgoing requests — requester-side confirmation */}
            <div className="bg-card border border-border rounded-xl p-5">
              <h3 className="font-semibold text-foreground mb-1">Your Requests Awaiting Confirmation</h3>
              <p className="text-[13px] text-muted-foreground mb-4">Requests you sent that have been accepted</p>

              {requestsLoading && (
                <div className="text-[13px] text-muted-foreground py-4 text-center">Loading…</div>
              )}
              {!requestsLoading && requestsError && (
                <div className="text-[13px] text-status-critical-text py-4 text-center">Failed to load: {requestsError}</div>
              )}
              {!requestsLoading && !requestsError && (() => {
                const accepted = requests.filter((r) => r.status === "accepted" || r.status === "completed");
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
                      <h3 className="font-semibold text-foreground">Coordination Chat</h3>
                      <p className="text-[13px] text-muted-foreground">
                        {selectedRequest
                          ? `REQ-${selectedRequest.id} · ${resolveFacilityName(selectedRequest.requesting_facility_id, selectedRequest.requesting_facility_name)} ↔ ${resolveFacilityName(selectedRequest.supplying_facility_id, selectedRequest.supplying_facility_name)} · ${selectedRequest.blood_type} · ${selectedRequest.quantity} units`
                          : "Select a request from the left to view its coordination chat"}
                      </p>
                    </div>
                    {selectedRequestId !== null && (
                      <div className="flex items-center gap-1.5 text-[13px] font-semibold text-status-safe-text">
                        <span className="w-2 h-2 rounded-full bg-status-safe animate-pulse" />
                        Live
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
                    {selectedRequestId !== null && !messagesLoading && messages.length === 0 && !messagesError && (
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
                    {messages.map((msg) => {
                      const isOwn = msg.sender_facility_id === actingFacilityId;
                      const time = new Date(msg.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
                      return (
                        <div key={msg.id} className={`flex flex-col ${isOwn ? "items-end" : "items-start"}`}>
                          <div className={`text-[12px] font-semibold mb-1 ${isOwn ? "text-primary" : "text-muted-foreground"}`}>
                            {msg.sender_facility_name} · {time}
                          </div>
                          <div
                            className={`max-w-[80%] px-4 py-3 rounded-2xl text-[14px] leading-relaxed ${
                              isOwn
                                ? "bg-primary text-white rounded-br-md"
                                : "bg-gray-200 text-foreground rounded-bl-md"
                            }`}
                          >
                            {msg.message}
                          </div>
                        </div>
                      );
                    })}
                  </div>

                  {(() => {
                    // Mirrors the backend's send_request_message guard exactly —
                    // sending is only meaningful once the other side can actually
                    // see it (the requester's own chat access only opens up once
                    // accepted), so a still-pending request can't send either.
                    const canSend = selectedRequest
                      ? selectedRequest.status === "accepted" || selectedRequest.status === "completed"
                      : false;
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
              <h3 className="font-semibold text-foreground">Outgoing Requests</h3>
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
                    {["Request ID", "Supplying Facility", "Blood Type", "Qty", "Reason", "Submitted", "Status", ""].map((h) => (
                      <th key={h} className="text-left py-3 px-4 text-[12px] font-bold uppercase tracking-wide text-muted-foreground whitespace-nowrap">
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {pendingOnly
                    .map((req) => {
                      const reasonStyle = {
                        trauma: "bg-status-critical-tint text-status-critical-text border-status-critical-border",
                        scheduled_surgery: "bg-info-tint text-info-text border-info-border",
                        restock: "bg-secondary text-muted-foreground border-border",
                      }[req.emergency_type];
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
                          <td className="py-3 px-4 font-semibold text-foreground tabular-nums">{req.quantity}</td>
                          <td className="py-3 px-4">
                            <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-[12px] font-semibold border ${reasonStyle}`}>
                              {EMERGENCY_TYPE_LABELS[req.emergency_type]}
                            </span>
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
                Cancel the {req.quantity}-unit {req.blood_type} request to {req.supplying_facility_name}?
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

