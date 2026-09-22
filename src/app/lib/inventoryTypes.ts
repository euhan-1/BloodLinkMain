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
};

export type InventoryApiRow = {
  din: string;
  blood_type: string;
  component: string;
  location: string;
  volume_ml: number;
  collected_date: string;
  expires_date: string;
};

export function toInventoryUnit(row: InventoryApiRow): InventoryUnit {
  const msPerDay = 1000 * 60 * 60 * 24;
  // expires_date is a date-only string ("2026-09-25"), which `new Date(...)`
  // parses as UTC midnight — diffing that against Date.now() (the current
  // instant) under-counts by a day for roughly the second half of each local
  // day at any positive UTC offset (e.g. the Philippines, UTC+8). Comparing
  // local-calendar midnights on both sides instead makes "days left" match
  // what a local user would count on a calendar.
  const [expiresYear, expiresMonth, expiresDay] = row.expires_date.split("-").map(Number);
  const expiresLocalMidnight = new Date(expiresYear, expiresMonth - 1, expiresDay).getTime();
  const now = new Date();
  const todayLocalMidnight = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const daysLeft = Math.round((expiresLocalMidnight - todayLocalMidnight) / msPerDay);
  return {
    din: row.din,
    type: row.blood_type,
    component: row.component,
    location: row.location,
    volume: row.volume_ml,
    collected: row.collected_date,
    expires: row.expires_date,
    daysLeft,
  };
}
