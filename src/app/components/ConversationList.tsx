import { actionLine, initials, listSubline, listTimeText, previewText, type ConversationSummary } from "../lib/conversations";

// The left pane of Messages: one row per other facility, newest conversation with something waiting on me first (the order the server
// sends). Each row: the facility's initials in a circle, its name and the time of the last activity, the last message (or "You: ..."),
// an unread badge, and what is waiting on me. A row is one button (so the whole row is the target and the keyboard has one stop per
// conversation); the selected one is `aria-current`. The list's heading is the screen's one h1.
export function ConversationList({
  list, me, selectedId, loading, error, onSelect, sourcingLabel, onOpenSourcing, className,
}: {
  list: ConversationSummary[] | null;
  me: number | null;
  selectedId: number | null;
  loading: boolean;
  error: string | null;
  onSelect: (facilityId: number) => void;
  sourcingLabel: string;
  onOpenSourcing: () => void;
  className: string;
}) {
  const sub = listSubline(list ?? []);
  return (
    <div className={className}>
      <div className="px-5 py-4 border-b border-border">
        <h1 className="text-lg leading-normal font-semibold text-foreground">Messages</h1>
        {list !== null && list.length > 0 && (
          <p className="text-[13px] text-muted-foreground mt-0.5">
            <span className="font-semibold text-foreground">{sub.action}</span> · {sub.rest}
          </p>
        )}
      </div>
      <div className="flex-1 min-h-0 overflow-y-auto p-2">
        {error && <div role="alert" className="m-2 text-[13px] text-status-critical-text">Failed to load your conversations: {error}</div>}
        {list === null && loading && <div className="text-[13px] text-muted-foreground py-8 text-center">Loading conversations…</div>}
        {list !== null && list.length === 0 && !error && (
          <div className="text-center px-4 py-10">
            <h2 className="text-[14px] font-semibold text-foreground mb-1">No conversations yet</h2>
            <p className="text-[13px] text-muted-foreground leading-relaxed">
              A conversation starts when you send or receive a request.{" "}
              <button type="button" onClick={onOpenSourcing} className="font-semibold text-foreground underline underline-offset-2 hover:no-underline">Go to {sourcingLabel}</button>
            </p>
          </div>
        )}
        {list !== null && list.length > 0 && (
          <ul className="flex flex-col gap-1" aria-label="Conversations">
            {list.map((c) => {
              const action = actionLine(c);
              const selected = c.facility.id === selectedId;
              return (
                <li key={c.facility.id} className={`rounded-xl ${selected ? "bg-primary-tint" : "hover:bg-secondary"} transition-colors`}>
                  <button
                    type="button" id={`conv-row-${c.facility.id}`} onClick={() => onSelect(c.facility.id)}
                    aria-current={selected ? "true" : undefined} className="flex w-full gap-3 p-3 text-left rounded-xl"
                  >
                    <span aria-hidden="true" className="shrink-0 w-11 h-11 rounded-full border border-border bg-card text-foreground text-[14px] font-bold flex items-center justify-center">
                      {initials(c.facility.name)}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="flex items-baseline justify-between gap-2">
                        <span className="truncate text-[15px] font-semibold text-foreground">{c.facility.name}</span>
                        <span className="shrink-0 text-[12px] text-muted-foreground tabular-nums">{listTimeText(c.last_activity_at)}</span>
                      </span>
                      <span className="mt-0.5 flex items-center justify-between gap-2">
                        <span className={`truncate text-[13.5px] ${c.unread > 0 ? "font-semibold text-foreground" : "text-muted-foreground"}`}>{previewText(c, me)}</span>
                        {c.unread > 0 && (
                          <span className="shrink-0 min-w-5 h-5 px-1.5 rounded-full bg-primary text-white text-[12px] font-bold leading-5 text-center">
                            <span aria-hidden="true">{c.unread}</span>
                            <span className="sr-only">{c.unread} unread {c.unread === 1 ? "message" : "messages"}</span>
                          </span>
                        )}
                      </span>
                      <span className={`mt-1.5 flex items-center gap-1.5 text-[12.5px] ${action.tone === "yours" ? "font-semibold text-status-watch-text" : "text-muted-foreground"}`}>
                        <span aria-hidden="true" className={`w-2 h-2 rounded-full ${action.tone === "yours" ? "bg-status-watch" : "bg-gray-400"}`} />
                        {action.text}
                      </span>
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}
