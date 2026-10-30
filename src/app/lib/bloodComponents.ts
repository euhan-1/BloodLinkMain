// The components a transfer request can ask for. Adding one is one line here and one in COMPONENTS in server/main.py;
// scripts/check_components.ts fails if the two differ. These are the two values blood_units.component holds in
// production. Do not add one the system has never handled.

export const BLOOD_COMPONENTS = ["Packed RBC", "Platelets"] as const;
export type BloodComponent = (typeof BLOOD_COMPONENTS)[number];

// The ABO/Rh compatibility table in statusTokens.ts is red-cell compatibility. It does not describe platelets, so the
// "also compatible with" line is shown for these components only.
export const RED_CELL_COMPONENTS: readonly BloodComponent[] = ["Packed RBC"];

export function usesRedCellCompatibility(component: BloodComponent | null): boolean {
  return component !== null && RED_CELL_COMPONENTS.includes(component);
}
