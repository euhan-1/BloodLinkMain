import { Clock } from "lucide-react";
import { inboxStatus, rowActions, type InboxRow, type RowAction, type StatusTone } from "../lib/requestInbox";

// The two pieces a request's row in the inbox and its header above the thread share, so they say and offer the same things.

const TONE: Record<StatusTone, string> = {
  yours: "bg-primary-tint text-role-accent-text border-border",
  theirs: "bg-status-watch-tint text-status-watch-text border-status-watch-border",
  done: "bg-secondary text-foreground border-border",
};

export function StatusPill({ row }: { row: InboxRow }) {
  const status = inboxStatus(row);
  return (
    <span className={`inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full border text-[12px] font-semibold ${TONE[status.tone]}`}>
      {status.tone === "theirs" && <Clock size={11} aria-hidden="true" />}
      {status.text}
    </span>
  );
}

const SIZE = { sm: "h-7 px-3 text-[12px] rounded-lg", md: "h-8 px-3 text-[13px] rounded-md" } as const;
const PRIMARY = "bg-primary text-white hover:bg-primary-hover";
const SECONDARY = "bg-card border border-border text-foreground hover:bg-secondary";
const CANCEL = "bg-card border border-border text-status-critical-text hover:bg-status-critical-tint";

const LABEL: Record<RowAction, string> = {
  accept: "Accept", decline: "Decline", cancel: "Cancel request", "confirm-release": "Confirm release", "confirm-receipt": "Confirm receipt",
};
const BUSY: Partial<Record<RowAction, string>> = { accept: "…", "confirm-release": "Confirming…", "confirm-receipt": "Confirming…" };

// The buttons rowActions says this facility has for the request: Accept and Decline, Cancel, Confirm release or Confirm receipt.
// Nothing at all when there are none. `block` stretches a confirm button to the full width (the inbox row does).
export function RequestActionButtons({
  row, busy, onAction, size = "sm", block = false,
}: {
  row: InboxRow;
  busy: boolean;
  onAction: (action: RowAction) => void;
  size?: keyof typeof SIZE;
  block?: boolean;
}) {
  const actions = rowActions(row);
  if (actions.length === 0) return null;
  return (
    <div className={block ? "flex gap-1.5 w-full" : "flex flex-wrap gap-1.5"}>
      {actions.map((action) => (
        <button
          key={action}
          type="button"
          onClick={() => onAction(action)}
          disabled={busy}
          className={`${SIZE[size]} font-semibold transition-colors disabled:opacity-60 ${
            action === "decline" ? SECONDARY : action === "cancel" ? CANCEL : PRIMARY
          } ${block && action.startsWith("confirm") ? "flex-1" : ""}`}
        >
          {busy ? BUSY[action] ?? LABEL[action] : LABEL[action]}
        </button>
      ))}
    </div>
  );
}
