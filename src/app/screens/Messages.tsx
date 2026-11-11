import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { ArrowLeft, MessageSquare } from "lucide-react";
import { apiPost, errorStatus, getConversation, markConversationRead, sendConversationMessage } from "../lib/api";
import { Modal } from "../components/Modal";
import { ConversationList } from "../components/ConversationList";
import {
  DaySeparator, EventChip, FinishedSummaryRow, MessageBubble, MessageComposer, NewMessagesDivider, RequestCardView,
} from "../components/ThreadParts";
import {
  SEND_LIMIT_TEXT, composerState, firstWaitingRequest, markedRead, mergeEarlier, readTarget, threadRows, threadSummaryText, withMessage,
  type ConversationList as ConversationListData, type ConversationThread, type RequestCard,
} from "../lib/conversations";
import { type RowAction } from "../lib/requestInbox";

// The Messages tab of Supply: the conversation list (one row per other facility) and, beside it, one thread. On a narrow screen the
// list is the whole screen until a conversation is opened, then the thread replaces it with a way back.
//
// Live: the list belongs to the parent (Supply), which refetches it when the shared poller's messages or requests tokens move. The OPEN
// thread polls every THREAD_POLL_MS, only while it is open and the browser tab is visible (and at once when it becomes visible), as the
// old per-request chat did. Opening a thread, and each time a newer message arrives while it is open and visible, tells the server
// how far this facility has read (POST /conversations/{id}/read); the "N new messages" divider is drawn from the read position the
// thread had when it was OPENED, so it does not vanish the moment it is read.

const THREAD_POLL_MS = 4000;
const WAIT_FOR_EARLIER_PAGES = 20; // when a card to focus is older than the pages loaded, load up to this many more

type ActionError = { id: number; message: string };

// "Live" is the result of the last poll of the open thread, not a property of having one open: "Connection lost, retrying" after a failed
// poll, until the next one succeeds.
function LiveState({ live }: { live: "connecting" | "live" | "lost" }) {
  return (
    <div
      role="status"
      className={`flex items-center gap-1.5 text-[13px] font-semibold ${live === "live" ? "text-status-safe-text" : live === "lost" ? "text-status-critical-text" : "text-muted-foreground"}`}
    >
      <span aria-hidden="true" className={`w-2 h-2 rounded-full ${live === "live" ? "bg-status-safe animate-pulse" : live === "lost" ? "bg-status-critical" : "bg-gray-400"}`} />
      {live === "live" ? "Live" : live === "lost" ? "Connection lost, retrying" : "Connecting…"}
    </div>
  );
}

export function MessagesTab({
  me, myFacilityName, list, listLoading, listError, onListChanged, onListLocal, selectedFacilityId, onSelectFacility,
  focusRequestId, onFocusConsumed, sourcingLabel, onOpenSourcing,
}: {
  me: number | null;
  myFacilityName: string;
  list: ConversationListData | null;
  listLoading: boolean;
  listError: string | null;
  onListChanged: () => void;                              // refetch the list now (after an action or a send)
  onListLocal: (change: (l: ConversationListData) => ConversationListData) => void; // change the list the screen holds, without a fetch
  selectedFacilityId: number | null;
  onSelectFacility: (id: number | null) => void;
  focusRequestId: number | null;                          // scroll to and focus this request's card once it is loaded
  onFocusConsumed: () => void;
  sourcingLabel: string;
  onOpenSourcing: () => void;
}) {
  const facilityId = selectedFacilityId;
  const [base, setBase] = useState<ConversationThread | null>(null);
  const [pages, setPages] = useState<ConversationThread[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [live, setLive] = useState<"connecting" | "live" | "lost">("connecting");
  const [expanded, setExpanded] = useState<ReadonlySet<number>>(new Set());
  const [readAtOpen, setReadAtOpen] = useState<number | null>(null);
  const [text, setText] = useState("");
  const [about, setAbout] = useState<number | null>(null);
  const [sending, setSending] = useState(false);
  const [composerError, setComposerError] = useState<string | null>(null);
  const [actionPendingId, setActionPendingId] = useState<number | null>(null);
  const [actionError, setActionError] = useState<ActionError | null>(null);
  const [cancelConfirmId, setCancelConfirmId] = useState<number | null>(null);
  const [visible, setVisible] = useState(() => document.visibilityState === "visible");
  const [loadingEarlier, setLoadingEarlier] = useState(false);

  const logRef = useRef<HTMLDivElement>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const stickRef = useRef(true);                  // the reader is at the bottom: new rows scroll into view; scrolled up: they do not
  const scrolledFor = useRef<number | null>(null);// the facility whose first scroll (to the divider, or the bottom) has been done
  const lastMarked = useRef<number | null>(null); // the newest message id reported as read in this thread; null = not yet this open
  const refreshRef = useRef<(() => Promise<void>) | null>(null);
  const pendingFocus = useRef<string[] | null>(null);
  const returnFocusTo = useRef<number | null>(null);

  // Only a thread that belongs to the conversation now selected: for the one render after a switch, the previous one is still in state.
  const thread = useMemo(() => (base && base.facility.id === facilityId ? mergeEarlier(base, pages) : null), [base, pages, facilityId]);
  const rows = useMemo(() => (thread ? threadRows(thread, me, { expanded, readAtOpen }) : []), [thread, me, expanded, readAtOpen]);
  const cards = useMemo(() => new Map((thread?.requests ?? []).map((c) => [c.request.id, c])), [thread]);
  const listedName = list?.conversations.find((c) => c.facility.id === facilityId)?.facility.name ?? "this facility";
  const name = thread?.facility.name ?? listedName;

  useEffect(() => {
    const onVisibility = () => setVisible(document.visibilityState === "visible");
    document.addEventListener("visibilitychange", onVisibility);
    return () => document.removeEventListener("visibilitychange", onVisibility);
  }, []);

  // A different conversation: nothing of the last one carries over.
  useEffect(() => {
    setBase(null); setPages([]); setExpanded(new Set()); setReadAtOpen(null); setText(""); setAbout(null); setComposerError(null);
    setActionError(null); setLoadError(null); setLive("connecting");
    scrolledFor.current = null; lastMarked.current = null; stickRef.current = true;
    if (facilityId !== null) returnFocusTo.current = facilityId;
  }, [facilityId]);

  // The open thread: one load, then a poll every few seconds while it is open and the tab is visible.
  useEffect(() => {
    if (facilityId === null) return;
    let cancelled = false;
    let inFlight = false;
    async function load(first: boolean) {
      if (inFlight || document.visibilityState !== "visible") return;
      inFlight = true;
      if (first) setLoading(true);
      try {
        const next = await getConversation(facilityId!);
        if (cancelled) return;
        setBase(next);
        setReadAtOpen((current) => (current === null ? next.read_position : current));
        setLoadError(null);
        setLive("live");
      } catch (err) {
        if (cancelled) return;
        setLoadError(err instanceof Error ? err.message : "Failed to load");
        setLive("lost");
      } finally {
        inFlight = false;
        if (!cancelled) setLoading(false);
      }
    }
    refreshRef.current = () => load(false);
    void load(true);
    const interval = setInterval(() => void load(false), THREAD_POLL_MS);
    const onVisible = () => { if (document.visibilityState === "visible") void load(false); };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      cancelled = true; clearInterval(interval); document.removeEventListener("visibilitychange", onVisible);
      refreshRef.current = null;
    };
  }, [facilityId]);

  // Read position: once the thread is on screen (loaded, tab visible), and again whenever a newer message is in it.
  useEffect(() => {
    if (facilityId === null || thread === null || !visible) return;
    const target = readTarget(thread, lastMarked.current);
    if (target === null) return;
    lastMarked.current = target;
    markConversationRead(facilityId, target)
      .then(() => onListLocal((l) => ({ ...l, conversations: markedRead(l.conversations, facilityId) })))
      .catch(() => { lastMarked.current = null; }); // try again at the next poll
  }, [facilityId, thread?.last_message_id, thread === null, visible]); // eslint-disable-line react-hooks/exhaustive-deps

  // Where the log sits: the first time, at the "N new messages" divider if there is one, else at the bottom; afterwards at the bottom
  // only while the reader is already there.
  useLayoutEffect(() => {
    const el = logRef.current;
    if (!el || rows.length === 0 || facilityId === null) return;
    if (scrolledFor.current !== facilityId) {
      scrolledFor.current = facilityId;
      const divider = el.querySelector<HTMLElement>("[data-new-messages]");
      el.scrollTop = divider ? Math.max(0, divider.offsetTop - 24) : el.scrollHeight;
      stickRef.current = !divider;
    } else if (stickRef.current) {
      el.scrollTop = el.scrollHeight;
    }
  }, [rows, facilityId]);

  // Focus requests made by an action or by opening: after the render that shows their target.
  useEffect(() => {
    const ids = pendingFocus.current;
    if (!ids) return;
    const el = ids.map((id) => document.getElementById(id)).find((e): e is HTMLElement => e !== null);
    if (el) { pendingFocus.current = null; el.focus(); }
  });

  // A card to go to (a notification, Transfer history's "Chat"): expanded if it is a finished one, loaded if it is older than the pages
  // held, scrolled to and focused.
  useEffect(() => {
    if (focusRequestId === null || thread === null) return;
    const id = focusRequestId;
    if (cards.has(id)) {
      setExpanded((cur) => (cur.has(id) ? cur : new Set(cur).add(id)));
      pendingFocus.current = [`req-card-${id}`];
      requestAnimationFrame(() => document.getElementById(`req-card-${id}`)?.scrollIntoView({ block: "center" }));
      stickRef.current = false;
      onFocusConsumed();
    } else if (thread.finished.has_more && pages.length < WAIT_FOR_EARLIER_PAGES && !loadingEarlier) {
      void loadEarlier();
    } else if (!thread.finished.has_more) {
      onFocusConsumed(); // not in this conversation after all
    }
  }, [focusRequestId, thread, cards]); // eslint-disable-line react-hooks/exhaustive-deps

  // Back to the list on a phone: focus returns to the row that was opened.
  useEffect(() => {
    if (facilityId === null && returnFocusTo.current !== null) {
      const id = returnFocusTo.current;
      returnFocusTo.current = null;
      requestAnimationFrame(() => document.getElementById(`conv-row-${id}`)?.focus());
    }
  }, [facilityId]);

  const loadEarlier = useCallback(async () => {
    if (facilityId === null || !thread || thread.finished.next_cursor === null) return;
    setLoadingEarlier(true);
    try {
      const page = await getConversation(facilityId, thread.finished.next_cursor);
      setPages((cur) => [...cur, page]);
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "Failed to load earlier requests");
    } finally {
      setLoadingEarlier(false);
    }
  }, [facilityId, thread]);

  function openConversation(id: number) {
    pendingFocus.current = ["conversation-heading"];
    onSelectFacility(id);
  }

  function expand(id: number) {
    setExpanded((cur) => new Set(cur).add(id));
    pendingFocus.current = [`req-card-${id}`];
  }
  function collapse(id: number) {
    setExpanded((cur) => { const next = new Set(cur); next.delete(id); return next; });
    pendingFocus.current = [`req-summary-${id}`];
  }

  async function perform(requestId: number, action: RowAction) {
    setActionPendingId(requestId);
    setActionError(null);
    try {
      await apiPost(`/requests/${requestId}/${action}`, {});
      // A request this action just ended (receipt confirmed, declined, cancelled) stays a card, with its result, instead of folding into its
      // one-line summary under the reader's eyes; it folds the next time the conversation is opened, or with "Hide history".
      setExpanded((cur) => (cur.has(requestId) ? cur : new Set(cur).add(requestId)));
      await refreshRef.current?.();
      onListChanged();
      pendingFocus.current = [`req-status-${requestId}`, `req-summary-${requestId}`, `req-card-${requestId}`];
    } catch (err) {
      setActionError({ id: requestId, message: err instanceof Error ? err.message : "Action failed" });
    } finally {
      setActionPendingId(null);
    }
  }
  function onAction(card: RequestCard, action: RowAction) {
    if (action === "cancel") setCancelConfirmId(card.request.id); // cancelling is real and cannot be undone: it asks first
    else void perform(card.request.id, action);
  }

  async function send() {
    if (facilityId === null || sending || text.trim() === "") return;
    setSending(true);
    setComposerError(null);
    try {
      const sent = await sendConversationMessage(facilityId, text.trim(), about);
      setBase((cur) => (cur ? withMessage(cur, sent) : cur));
      setText("");
      setAbout(null);
      stickRef.current = true;
      onListChanged();
    } catch (err) {
      setComposerError(errorStatus(err) === 429 ? SEND_LIMIT_TEXT : err instanceof Error ? err.message : "Failed to send the message");
    } finally {
      setSending(false);
      inputRef.current?.focus(); // focus stays in the composer, sent or not
    }
  }

  const composer = composerState(thread);
  const waiting = thread ? firstWaitingRequest(thread) : null;
  const summary = thread ? threadSummaryText(thread.counts) : null;
  const cancelCard = cancelConfirmId !== null ? cards.get(cancelConfirmId) ?? null : null;

  return (
    <>
      <div className="grid lg:grid-cols-5 gap-5">
        <ConversationList
          className={`min-w-0 lg:col-span-2 bg-card border border-border rounded-xl flex-col lg:h-[600px] ${facilityId !== null ? "hidden lg:flex" : "flex"}`}
          list={list ? list.conversations : null} me={me} selectedId={facilityId} loading={listLoading} error={listError}
          onSelect={openConversation} sourcingLabel={sourcingLabel} onOpenSourcing={onOpenSourcing}
        />

        <section
          aria-label={facilityId === null ? "Conversation" : `Conversation with ${name}`}
          className={`min-w-0 lg:col-span-3 bg-card border border-border rounded-xl flex-col h-[600px] max-h-[85vh] ${facilityId === null ? "hidden lg:flex" : "flex"}`}
        >
          {facilityId === null ? (
            <div className="flex-1 flex flex-col items-center justify-center text-center px-8">
              <div className="w-14 h-14 rounded-full bg-secondary flex items-center justify-center mb-4">
                <MessageSquare size={22} aria-hidden="true" className="text-muted-foreground" />
              </div>
              <h2 className="text-lg leading-normal font-semibold text-foreground mb-1">Select a conversation</h2>
              <p className="text-[14px] text-muted-foreground max-w-xs leading-relaxed">Choose a facility from the list to see its requests and talk to it.</p>
            </div>
          ) : (
            <>
              <div className="flex-none px-5 py-3 border-b border-border">
                {/* A phone: the way back and the live state share a line above the title; from lg up the live state sits beside the title. */}
                <div className="lg:hidden flex items-center justify-between gap-3 mb-1">
                  <button
                    type="button" onClick={() => onSelectFacility(null)}
                    className="inline-flex items-center gap-1 text-[13px] font-semibold text-muted-foreground hover:text-foreground transition-colors"
                  >
                    <ArrowLeft size={14} aria-hidden="true" /> Messages
                  </button>
                  <LiveState live={live} />
                </div>
                <div className="flex items-start justify-between gap-4">
                <div className="min-w-0 flex-1">
                  <h2 id="conversation-heading" ref={headingRef} tabIndex={-1} className="text-lg leading-normal font-semibold text-foreground truncate outline-none">{name}</h2>
                  {summary && (
                    <p className="text-[13px] text-muted-foreground">
                      <span className="font-semibold text-foreground">{summary.open}</span>
                      {summary.action && <> · <span className="font-semibold text-status-watch-text">{summary.action}</span></>}
                      {waiting !== null && (
                        <>
                          <span aria-hidden="true" className="hidden sm:inline"> · </span>
                          <button
                            type="button"
                            onClick={() => { document.getElementById(`req-card-${waiting}`)?.scrollIntoView({ block: "center" }); document.getElementById(`req-status-${waiting}`)?.focus(); }}
                            className="block sm:inline text-[13px] font-semibold text-foreground underline underline-offset-2 hover:no-underline"
                          >Go to the first one</button>
                        </>
                      )}
                    </p>
                  )}
                </div>
                <div className="hidden lg:block shrink-0"><LiveState live={live} /></div>
                </div>
              </div>

              <div
                ref={logRef} role="log" aria-live="polite" aria-relevant="additions" tabIndex={0}
                aria-label={`Messages and requests with ${name}`}
                onScroll={() => { const el = logRef.current; if (el) stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80; }}
                className="flex-1 overflow-y-auto px-5 py-4 flex flex-col gap-3.5"
              >
                {loading && thread === null && <p className="text-center text-[13px] text-muted-foreground py-8">Loading the conversation…</p>}
                {loadError && <p role="alert" className="text-center text-[13px] text-status-critical-text py-2">{loadError}</p>}
                {thread?.finished.has_more && (
                  <div className="flex justify-center">
                    <button
                      type="button" onClick={() => void loadEarlier()} disabled={loadingEarlier}
                      className="text-[13px] font-semibold text-foreground underline underline-offset-2 hover:no-underline disabled:opacity-60"
                    >
                      {loadingEarlier ? "Loading…" : `Show earlier requests (${thread.finished.total - thread.finished.shown} more)`}
                    </button>
                  </div>
                )}
                {rows.map((row) => {
                  switch (row.kind) {
                    case "day": return <DaySeparator key={row.key} row={row} />;
                    case "new-messages": return <NewMessagesDivider key={row.key} row={row} />;
                    case "message": return <MessageBubble key={row.key} row={row} />;
                    case "event": {
                      const card = cards.get(row.requestId);
                      return card ? <EventChip key={row.key} row={row} card={card} /> : null;
                    }
                    case "finished": return <FinishedSummaryRow key={row.key} row={row} onExpand={expand} />;
                    case "request":
                      return (
                        <RequestCardView
                          key={row.key} row={row} me={me} busy={actionPendingId === row.card.request.id}
                          error={actionError?.id === row.card.request.id ? actionError.message : null}
                          onAction={onAction}
                          onMessageAbout={(id) => { setAbout(id); inputRef.current?.focus(); }}
                          onCollapse={expanded.has(row.card.request.id) ? collapse : undefined}
                        />
                      );
                  }
                })}
                {thread && thread.messages_truncated && (
                  <p className="text-center text-[12.5px] text-muted-foreground">Only the newest messages are shown.</p>
                )}
                {thread && rows.length === 0 && !loading && <p className="text-center text-[13px] text-muted-foreground py-6">No messages yet.</p>}
              </div>

              <MessageComposer
                facilityName={name} value={text} onChange={setText} onSend={() => void send()} sending={sending}
                enabled={composer.enabled} hint={composer.hint} error={composerError}
                aboutRequestId={about} onClearAbout={() => { setAbout(null); inputRef.current?.focus(); }} inputRef={inputRef}
              />
            </>
          )}
        </section>
      </div>

      {cancelCard && (
        <Modal title="Cancel Request" onClose={() => setCancelConfirmId(null)}>
          <div className="space-y-4">
            <p className="text-[14px] text-muted-foreground">
              Cancel the {cancelCard.request.quantity}-unit {cancelCard.request.blood_type}{cancelCard.request.component ? ` ${cancelCard.request.component}` : ""} request
              to {cancelCard.request.supplying_facility.name}? This can't be undone — you'll need to submit a new request if you still need this blood type.
            </p>
            <div className="flex gap-2">
              <button
                onClick={() => setCancelConfirmId(null)}
                className="flex-1 h-10 border border-border rounded-md text-sm font-semibold text-foreground hover:bg-secondary transition-colors"
              >
                Keep Request
              </button>
              <button
                onClick={async () => { const id = cancelCard.request.id; setCancelConfirmId(null); await perform(id, "cancel"); }}
                className="flex-1 h-10 bg-status-critical-text text-white text-sm font-bold rounded-md hover:opacity-90 transition-colors"
              >
                Cancel Request
              </button>
            </div>
          </div>
        </Modal>
      )}
    </>
  );
}
