// Shared by Dashboard (expiry warnings) and Inventory (the full table) so
// both screens describe a blood unit the same way.
export type InventoryUnit = {
  din: string;
  type: string;
  component: string;
  location: string;
  volume: number;
  collected: string;
  expires: string;
  daysLeft: number;
  // The request this unit is held for, if any: a reserved unit is not available to anyone else.
  reservedForRequestId: number | null;
};

export type InventoryApiRow = {
  din: string;
  blood_type: string;
  component: string;
  location: string;
  volume_ml: number;
  collected_date: string;
  expires_date: string;
  reserved_for_request_id?: number | null; // absent from an older API
};

export function toInventoryUnit(row: InventoryApiRow): InventoryUnit {
  const msPerDay = 1000 * 60 * 60 * 24;
  // The system's business day is Asia/Manila (see business_today() in server/main.py),
  // so "today" is the Manila calendar date whatever timezone this browser is set to.
  // Both sides are calendar dates compared as UTC midnights, so there is no DST or
  // offset arithmetic in the difference.
  const [expiresYear, expiresMonth, expiresDay] = row.expires_date.split("-").map(Number);
  const [todayYear, todayMonth, todayDay] = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Manila" })
    .format(new Date()).split("-").map(Number);
  const daysLeft = Math.round((Date.UTC(expiresYear, expiresMonth - 1, expiresDay) - Date.UTC(todayYear, todayMonth - 1, todayDay)) / msPerDay);
  return {
    din: row.din,
    type: row.blood_type,
    component: row.component,
    location: row.location,
    volume: row.volume_ml,
    collected: row.collected_date,
    expires: row.expires_date,
    daysLeft,
    reservedForRequestId: row.reserved_for_request_id ?? null,
  };
}

// One row of GET /inventory/summary: current usable stock for a type against the facility's own thresholds.
// can_give is a blood bank's: per component, the releasable units above this type's minimum, by the same rule release and
// the nearby search use ({} for a hospital, and absent from an older API).
export type InventorySummaryRow = {
  blood_type: string; minimum_units: number; maximum_units: number; units: number; can_give?: Record<string, number>;
};
