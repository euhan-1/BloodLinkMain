import { STATUS_STYLES, EXPIRY_STYLES, type StatusLevel, type ExpiryStatus } from "../lib/statusTokens";

export function BloodDropLogo({ size = 32, className = "" }: { size?: number; className?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" fill="none" className={className}>
      <path
        d="M16 4C16 4 7 14.5 7 20C7 24.97 11.03 29 16 29C20.97 29 25 24.97 25 20C25 14.5 16 4 16 4Z"
        fill="#8C1B3A"
      />
      <path
        d="M16 10C16 10 11 17 11 20.5C11 23.538 13.239 26 16 26"
        stroke="rgba(255,255,255,0.35)"
        strokeWidth="1.5"
        strokeLinecap="round"
      />
    </svg>
  );
}

export function StatusDot({ status }: { status: StatusLevel }) {
  return <span className={`inline-block w-2 h-2 rounded-full ${STATUS_STYLES[status].dot} mr-1.5`} />;
}

// The product's signature element — one shared badge for every blood type
// display, from table cells to search-picker buttons, so O-, A+, etc. always
// read the same way no matter where they appear. Fixed width, not min-width
// — a min-width let 3-character types (AB-, AB+) render visibly wider than
// every 2-character type at the same size, so a column of badges never
// actually lined up. Fixed width is sized for the widest case (AB-/AB+) and
// every type sits inside the identical box regardless of its own length.
export const BLOOD_BADGE_SIZES = {
  sm: "h-6 w-[38px] text-[11.5px]",
  md: "h-7 w-11 text-[13px]",
  lg: "h-9 w-[54px] text-[16px]",
  // "Stamp" scale — the label-system treatment (Inventory group headers,
  // Emergency Sourcing's compatible-alternative badges), not for ordinary
  // inline use.
  xl: "h-14 w-20 text-[24px]",
} as const;

export function BloodTypeBadge({ type, size = "md", selected = false, className = "" }: {
  type: string;
  size?: keyof typeof BLOOD_BADGE_SIZES;
  selected?: boolean;
  className?: string;
}) {
  return (
    <span
      className={`inline-flex items-center justify-center rounded-md font-display font-bold tracking-tight leading-none ${BLOOD_BADGE_SIZES[size]} ${
        selected
          ? "bg-primary text-white"
          : "bg-card text-primary border border-primary/25 shadow-sm"
      } ${className}`}
    >
      {type}
    </span>
  );
}

// Inventory's DIN cell — wide-tracked mono text. Used only where a DIN is
// shown at label weight (Inventory rows).
export function DinLabel({ din }: { din: string }) {
  return (
    <span className="inline-flex items-center font-mono text-[13px] tracking-wider uppercase text-foreground">
      {din}
    </span>
  );
}

// The "stamped" expiry callout — a bordered box colored by the same
// EXPIRY_STYLES status language used everywhere else, deliberately more
// visually weighted than the plain collection-date text next to it (mirrors
// how a real bag's printed expiry is the prominent, boxed date).
export function DateStamp({ date, status }: { date: string; status: ExpiryStatus }) {
  const style = EXPIRY_STYLES[status];
  return (
    <span className={`inline-block font-mono text-[13px] font-semibold tracking-tight px-1.5 py-0.5 rounded border ${style.badge}`}>
      {date}
    </span>
  );
}
