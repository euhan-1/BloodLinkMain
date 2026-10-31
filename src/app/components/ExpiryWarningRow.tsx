import { useState } from "react";
import { Bell, CheckCircle } from "lucide-react";
import { notifyNearbyHospitals, type NotifyNearbyHospitalsResult } from "../lib/api";
import { EXPIRY_CRITICAL_DAYS } from "../lib/statusTokens";
import { BloodTypeBadge } from "./BloodTypeBadge";
import { type InventoryUnit } from "../lib/inventoryTypes";

// One unit that is close to expiring, with the action to tell nearby hospitals about it: the plain row the hospital
// dashboard uses, where colour is reserved for stock being short or fine. (The blood bank's Dashboard lists expiring
// units grouped instead, and offers them with one notification per group.)
export function ExpiryWarningRow({ row }: { row: InventoryUnit }) {
  const [notifyState, setNotifyState] = useState<"idle" | "sending" | "sent" | "error">("idle");
  const [notifyResult, setNotifyResult] = useState<NotifyNearbyHospitalsResult | null>(null);
  const [notifyError, setNotifyError] = useState<string | null>(null);

  async function handleNotify() {
    setNotifyState("sending");
    setNotifyError(null);
    try {
      const result = await notifyNearbyHospitals(row.din);
      setNotifyResult(result);
      setNotifyState("sent");
    } catch (err) {
      setNotifyError(err instanceof Error ? err.message : "Failed to notify nearby hospitals");
      setNotifyState("error");
    }
  }

  const sentText =
    notifyResult && notifyResult.notified_count > 0
      ? `${notifyResult.notified_count} nearby hospital${notifyResult.notified_count === 1 ? "" : "s"} alerted`
      : "No nearby hospitals to alert";

  const urgent = row.daysLeft <= EXPIRY_CRITICAL_DAYS;
  return (
    <div className="py-3.5 border-t border-gray-200 first:border-t-0 first:pt-0 flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
      <div className="flex items-center gap-3 min-w-0">
        <BloodTypeBadge type={row.type} size="sm" />
        <div className="min-w-0">
          <div className="font-mono text-[13px] text-foreground truncate">{row.din}</div>
          <div className="text-[14px] text-foreground truncate">{row.component}</div>
        </div>
      </div>
      <div className="flex items-center gap-4">
        <span className={`text-[14px] font-semibold tabular-nums ${urgent ? "text-status-critical-text" : "text-foreground"}`}>
          {row.daysLeft === 0 ? "Expires today" : `${row.daysLeft} ${row.daysLeft === 1 ? "day" : "days"} left`}
        </span>
        {notifyState === "sent" ? (
          <span className="flex items-center gap-1.5 text-[13px] font-semibold text-status-safe-text">
            <CheckCircle size={14} className="shrink-0" /> {sentText}
          </span>
        ) : (
          <button
            onClick={handleNotify}
            disabled={notifyState === "sending"}
            className="h-9 px-3 rounded-md border border-border bg-card text-[13px] font-semibold text-foreground hover:bg-secondary transition-colors disabled:opacity-60 flex items-center gap-1.5"
          >
            <Bell size={13} className="shrink-0" />
            {notifyState === "sending" ? "Notifying…" : "Notify nearby hospitals"}
          </button>
        )}
      </div>
      {notifyState === "error" && <div className="basis-full text-[13px] text-status-critical-text">{notifyError}</div>}
    </div>
  );
}
