// Canonical display order for the 8 blood types — matches the order the
// Dashboard's own "Inventory by Blood Type" chart renders in, reused here
// (Inventory's grouped view) so the app doesn't have two conventions.
export const BLOOD_TYPE_ORDER = ["A-", "A+", "AB-", "AB+", "B-", "B+", "O-", "O+"];

// A second, differently-ordered list of the same 8 types — used for dropdown/
// filter options (Requests, Donors) where positive-before-negative-per-group
// reads more naturally than BLOOD_TYPE_ORDER's grouping. Kept as its own
// constant (not derived from BLOOD_TYPE_ORDER) rather than reordering either
// list to match the other, since changing either changes visible UI order.
export const ALL_BLOOD_TYPES = ["A+", "A-", "B+", "B-", "O+", "O-", "AB+", "AB-"];

// Mirrors the backend's NOTIFY_EXPIRY_CRITICAL_DAYS / NOTIFY_EXPIRY_NEAR_DAYS
// (server/main.py) — can't literally share a constant across languages, but
// the two values must stay in sync: this is what decides "critical"/"near
// expiry" here, and the backend uses the same cutoffs to decide when an
// expiry notification actually fires. Named here (not inlined) so the two
// places in this file that care about "is this expiring soon" both go
// through one definition instead of each hardcoding 3/7 independently.
export const EXPIRY_CRITICAL_DAYS = 3;
export const EXPIRY_NEAR_DAYS = 7;

// Single source of truth for expiry status, shared by Inventory and Dashboard
// so the two screens can't quietly drift onto different day cutoffs.
export type ExpiryStatus = "expired" | "critical" | "near-expiry" | "ok";

export function getExpiryStatus(daysLeft: number): ExpiryStatus {
  if (daysLeft < 0) return "expired";
  if (daysLeft <= EXPIRY_CRITICAL_DAYS) return "critical";
  if (daysLeft <= EXPIRY_NEAR_DAYS) return "near-expiry";
  return "ok";
}

// The 3-state status language (safe/watch/critical — see theme.css's
// --status-* tokens) used identically everywhere status appears. "expired"
// is deliberately a 4th, separate neutral state — it isn't a warning level,
// it's inventory that's already unusable, so it gets its own gray treatment
// rather than being folded into "critical".
export type StatusLevel = "safe" | "watch" | "critical";

export const STATUS_STYLES: Record<StatusLevel, { badge: string; dot: string; panel: string; solid: string; text: string }> = {
  safe: {
    badge: "bg-status-safe-tint text-status-safe-text border-status-safe-border",
    dot: "bg-status-safe",
    // "status-panel" opts into the gradient overlay in theme.css's @layer
    // base — deliberately NOT the plain tint+border combo alone, since that
    // same combo now also covers ordinary error/warning banners app-wide
    // (see Login/Inventory/Requests/etc.), which should stay a flat tint,
    // not take on this panel's gradient treatment.
    panel: "status-panel bg-status-safe-tint border border-status-safe-border",
    solid: "bg-status-safe text-white",
    text: "text-status-safe-text",
  },
  watch: {
    badge: "bg-status-watch-tint text-status-watch-text border-status-watch-border",
    dot: "bg-status-watch",
    panel: "status-panel bg-status-watch-tint border border-status-watch-border",
    solid: "bg-status-watch text-white",
    text: "text-status-watch-text",
  },
  critical: {
    badge: "bg-status-critical-tint text-status-critical-text border-status-critical-border",
    dot: "bg-status-critical",
    panel: "status-panel bg-status-critical-tint border border-status-critical-border",
    solid: "bg-status-critical text-white",
    text: "text-status-critical-text",
  },
};

export const EXPIRED_STYLE = {
  badge: "bg-gray-100 text-foreground border-gray-300",
  dot: "bg-gray-400",
  panel: "bg-gray-50 border border-gray-200",
  solid: "bg-gray-500 text-white",
  text: "text-foreground",
};

export const EXPIRY_STYLES: Record<ExpiryStatus, { badge: string; dot: string; rowTint: string; panel: string; solid: string; text: string; label: (daysLeft: number) => string }> = {
  expired: { ...EXPIRED_STYLE, rowTint: "bg-gray-50", label: () => "Expired" },
  critical: { ...STATUS_STYLES.critical, rowTint: "bg-status-critical-tint/50", label: (d) => `${d}d — Critical` },
  "near-expiry": { ...STATUS_STYLES.watch, rowTint: "bg-status-watch-tint/40", label: (d) => `${d}d — Near Expiry` },
  ok: { ...STATUS_STYLES.safe, rowTint: "", label: () => "OK" },
};

// units/min ratio → status level, the one rule used everywhere stock is
// judged against a minimum (dashboard grid, chart bars, threshold dots).
export function stockStatus(units: number, min: number): StatusLevel {
  const ratio = min > 0 ? units / min : units > 0 ? 1 : 0;
  if (ratio < 0.6) return "critical";
  if (ratio < 0.9) return "watch";
  return "safe";
}

// Raw hex twins of the --status-* tokens, for the few spots (SVG/Recharts
// `fill`) that can't take a Tailwind class and need an actual color value.
export const STATUS_HEX: Record<StatusLevel, string> = {
  safe: "#0F766E",
  watch: "#B8860B",
  critical: "#BD4024",
};
