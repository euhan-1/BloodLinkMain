import { EMERGENCY_TYPE_LABELS, isImmediateUse, type EmergencyType } from "../lib/requestTypes";

// The reason as a tag. Two looks, because the server has two tiers: immediate use (trauma and scheduled surgery) and
// restock. Scheduled surgery used to be blue, which meant nothing here. Shared by the request cards, the outgoing table
// and the transfer history, so a reason looks the same wherever it appears.
export function ReasonTag({ reason }: { reason: string }) {
  const label = EMERGENCY_TYPE_LABELS[reason as EmergencyType] ?? reason;
  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded-full text-[12px] font-semibold border ${
        isImmediateUse(reason)
          ? "bg-status-critical-tint text-status-critical-text border-status-critical-border"
          : "bg-secondary text-foreground border-border"
      }`}
    >
      {label.charAt(0) + label.slice(1).toLowerCase()}
    </span>
  );
}
