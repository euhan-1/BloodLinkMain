import { ArrowDownLeft, ArrowUpRight } from "lucide-react";
import { BloodTypeBadge } from "./BloodTypeBadge";
import { ReasonTag } from "./ReasonTag";
import { useFlashOnChange } from "../lib/motion";
import { formatDateTime } from "../lib/dateFormat";
import { requestUnitsText } from "../lib/requestTypes";
import { counterpartName, requestPhase, rowActions, type InboxRow, type RowAction } from "../lib/requestInbox";
import { RequestActionButtons, StatusPill } from "./RequestStatusParts";

// One request in the inbox: the row opens its thread, and the buttons under it are its actions (the same as in the thread's header). They are siblings of the
// opening button, not inside it, so a click on one never also opens the thread.
export function RequestInboxRow({
  row, selected, unread, busy, onOpen, onAction,
}: {
  row: InboxRow;
  selected: boolean;
  unread: "new" | "updated" | null;
  busy: boolean;
  onOpen: () => void;
  onAction: (action: RowAction) => void;
}) {
  const { req, direction, position } = row;
  // Flashes once when this request's phase moves (pending -> accepted -> released -> completed), not on every poll.
  const flash = useFlashOnChange(requestPhase(req));
  const Arrow = direction === "incoming" ? ArrowDownLeft : ArrowUpRight;

  return (
    <li className={`rounded-lg border-2 transition-colors ${selected ? "border-primary bg-primary-tint" : "border-border bg-card hover:bg-secondary"} ${flash ? "animate-value-flash-bg" : ""}`}>
      <button type="button" onClick={onOpen} aria-current={selected ? "true" : undefined} className="block w-full text-left p-3 rounded-lg">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <div className="flex items-center gap-1.5 text-[12px] font-semibold text-muted-foreground">
              {position !== undefined && (
                <span className="w-5 h-5 rounded-full bg-primary-tint text-primary text-[11px] font-bold flex items-center justify-center shrink-0">
                  <span className="sr-only">Position </span>{position}
                </span>
              )}
              <Arrow size={12} aria-hidden="true" />
              {direction === "incoming" ? "Incoming" : "Outgoing"}
              {unread && (
                <span className="ml-1 px-1.5 py-0.5 rounded-full bg-primary text-white text-[11px] font-bold leading-none">
                  {unread === "new" ? "New" : "Updated"}
                </span>
              )}
            </div>
            <div className="mt-0.5 text-[14px] font-semibold text-foreground">{counterpartName(row)}</div>
          </div>
          <BloodTypeBadge type={req.blood_type} size="md" />
        </div>
        <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[13px] text-foreground">
          <span>{requestUnitsText(req)}</span>
          <ReasonTag reason={req.emergency_type} />
        </div>
        <div className="mt-2 flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
          <StatusPill row={row} />
          <span className="flex items-center gap-2 text-[12px]">
            <span className="font-mono text-muted-foreground">REQ-{req.id}</span>
            <time dateTime={req.created_at} className="text-gray-600">{formatDateTime(req.created_at)}</time>
          </span>
        </div>
      </button>

      {rowActions(row).length > 0 && (
        <div className="px-3 pb-3">
          <RequestActionButtons row={row} busy={busy} onAction={onAction} block />
        </div>
      )}
    </li>
  );
}
