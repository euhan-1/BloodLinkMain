import { useId, useState } from "react";
import { ArrowDownLeft, ArrowUpRight, ChevronDown } from "lucide-react";
import { BloodTypeBadge } from "./BloodTypeBadge";
import { ReasonTag } from "./ReasonTag";
import { RequestActionButtons, StatusPill } from "./RequestStatusParts";
import { formatDateTime } from "../lib/dateFormat";
import { requestUnitsText } from "../lib/requestTypes";
import { counterpartName, type InboxRow, type RowAction } from "../lib/requestInbox";

// The open request's details, pinned above its thread the way a listing sits above its conversation. Rendered from the request
// as the screen already holds it; never sent or stored as a message. Two lines (who, and what) and the actions; the rest is
// behind "Details". The actions are the inbox row's (rowActions), so the two never disagree.
export function RequestHeader({
  row, requester, supplier, busy, actionError, onAction,
}: {
  row: InboxRow;
  requester: string; // both parties' names, for the line under "Details"
  supplier: string;
  busy: boolean;
  actionError: string | null;
  onAction: (action: RowAction) => void;
}) {
  const detailsId = useId();
  const [open, setOpen] = useState(false);
  const { req, direction } = row;
  const Arrow = direction === "incoming" ? ArrowDownLeft : ArrowUpRight;

  return (
    <div className="flex-none border-b border-border bg-card px-5 py-3">
      <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1.5">
        <div className="min-w-0 flex flex-wrap items-center gap-x-2 gap-y-0.5">
          <span className="font-mono text-[12px] text-muted-foreground">REQ-{req.id}</span>
          <span className="inline-flex items-center gap-1 text-[12px] font-semibold text-muted-foreground">
            <Arrow size={12} aria-hidden="true" />
            {direction === "incoming" ? "Incoming from" : "Outgoing to"}
          </span>
          <span className="text-[14px] font-semibold text-foreground">{counterpartName(row)}</span>
        </div>
        <StatusPill row={row} />
      </div>

      <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[13px] text-foreground">
        <BloodTypeBadge type={req.blood_type} size="md" />
        <span>{requestUnitsText(req)}</span>
        <ReasonTag reason={req.emergency_type} />
      </div>

      <div className="mt-2 flex flex-wrap items-center justify-between gap-x-3 gap-y-1.5">
        <RequestActionButtons row={row} busy={busy} onAction={onAction} size="md" />
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          aria-controls={detailsId}
          className="ml-auto inline-flex items-center gap-1 text-[13px] font-semibold text-muted-foreground hover:text-foreground transition-colors"
        >
          Details <ChevronDown size={14} aria-hidden="true" className={`transition-transform ${open ? "rotate-180" : ""}`} />
        </button>
      </div>
      {actionError && <p role="alert" className="mt-1.5 text-[13px] text-status-critical-text">{actionError}</p>}

      {open && (
        <dl id={detailsId} className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-[13px]">
          <dt className="text-muted-foreground">Requested by</dt>
          <dd className="text-foreground">{requester}{direction === "outgoing" ? " (you)" : ""}</dd>
          <dt className="text-muted-foreground">Supplied by</dt>
          <dd className="text-foreground">{supplier}{direction === "incoming" ? " (you)" : ""}</dd>
          <dt className="text-muted-foreground">Submitted</dt>
          <dd className="text-foreground"><time dateTime={req.created_at}>{formatDateTime(req.created_at)}</time></dd>
        </dl>
      )}
    </div>
  );
}
