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
  const daysLeft = Math.ceil((new Date(row.expires_date).getTime() - Date.now()) / msPerDay);
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
