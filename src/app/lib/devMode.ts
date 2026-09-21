const DEV_FACILITY_KEY = "bloodlink_dev_facility_id";

export function isDevModeEnabled(): boolean {
  return new URLSearchParams(window.location.search).get("dev") === "1";
}

// sessionStorage on purpose — a testing convenience that should clear when the
// tab closes, distinct from the real login session in localStorage.
export function getDevFacilityId(): number | null {
  if (!isDevModeEnabled()) return null;
  const raw = sessionStorage.getItem(DEV_FACILITY_KEY);
  return raw ? Number(raw) : null;
}

export function setDevFacilityId(id: number): void {
  sessionStorage.setItem(DEV_FACILITY_KEY, String(id));
}
