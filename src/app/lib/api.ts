import { getDevFacilityId } from "./devMode";
import { clearSession, getToken, setSession, updateSessionUser, type SessionUser } from "./session";
import { type InventoryApiRow } from "./inventoryTypes";

// Production always calls same-origin, through Vercel's /api/:path* rewrite
// (see vercel.json) to the real Render backend — not the Render URL
// directly. PLDT (a major Philippine ISP) can't reach
// bloodlink-backend-t688.onrender.com at all (browser requests fail with
// ERR_CONNECTION after ~8s), but can reach Vercel fine, so the fix is
// routing every API call through the domain that's actually reachable.
// VITE_API_BASE_URL still drives local dev (see .env.local /
// .env.example — defaults to the local backend on :8000) since there's no
// Vercel rewrite in play there; import.meta.env.DEV is Vite's own
// build-time flag (true under `vite dev`, false in a production build), so
// this can't accidentally ship the dev value into a real deploy.
const API_BASE_URL = import.meta.env.DEV ? import.meta.env.VITE_API_BASE_URL : "/api";

// Every request auto-attaches the real bearer token (if logged in) and, only
// when dev mode is active (?dev=1) and a facility has been picked in the dev
// banner, the X-Dev-Facility-Id override header — mirrors get_acting_facility_id
// server-side exactly, so no individual screen needs to think about either.
function withAuthHeaders(headers?: HeadersInit): HeadersInit {
  const token = getToken();
  const devFacilityId = getDevFacilityId();
  return {
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...(devFacilityId !== null ? { "X-Dev-Facility-Id": String(devFacilityId) } : {}),
    ...headers,
  };
}

// Surfaces the backend's real detail message (e.g. "this donor was not
// messaged as part of this blast") instead of a generic status code —
// matters a lot once error content itself is meaningful, as with the dev tools.
async function extractErrorMessage(res: Response): Promise<string> {
  try {
    const body = await res.json();
    if (typeof body.detail === "string") return body.detail;
    if (Array.isArray(body.detail)) {
      return body.detail.map((d: { msg?: string }) => d.msg ?? JSON.stringify(d)).join("; ");
    }
  } catch {
    // response wasn't JSON — fall through to the generic message
  }
  return `${res.status} ${res.statusText}`;
}

// Fired on window whenever an authenticated call comes back 401 — App.tsx
// listens for this to drop back to the login screen instead of every screen
// independently rendering "Failed to load: 401 Unauthorized" forever with no
// way out but a manual logout. Only used by the authenticated helpers below;
// login/changePassword/requestPasswordReset/resetPassword call fetch directly
// and treat their own 401s as "wrong credentials", not session expiry.
export const SESSION_EXPIRED_EVENT = "bloodlink:session-expired";

async function throwForResponse(res: Response): Promise<never> {
  if (res.status === 401) {
    clearSession();
    window.dispatchEvent(new Event(SESSION_EXPIRED_EVENT));
    throw new Error("Your session has expired — please log in again.");
  }
  throw new Error(await extractErrorMessage(res));
}

export async function apiGet<T>(path: string, headers?: HeadersInit): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, { headers: withAuthHeaders(headers) });
  if (!res.ok) {
    return throwForResponse(res);
  }
  return res.json();
}

export async function apiPost<T>(path: string, body: unknown, headers?: HeadersInit): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...withAuthHeaders(headers) },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    return throwForResponse(res);
  }
  return res.json();
}

export async function apiPut<T>(path: string, body: unknown, headers?: HeadersInit): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json", ...withAuthHeaders(headers) },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    return throwForResponse(res);
  }
  return res.json();
}

export async function apiPatch<T>(path: string, body: unknown, headers?: HeadersInit): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json", ...withAuthHeaders(headers) },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    return throwForResponse(res);
  }
  return res.json();
}

export async function apiDelete<T>(path: string, body: unknown, headers?: HeadersInit): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    method: "DELETE",
    headers: { "Content-Type": "application/json", ...withAuthHeaders(headers) },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    return throwForResponse(res);
  }
  return res.json();
}

export async function apiUploadFile<T>(path: string, file: File): Promise<T> {
  const formData = new FormData();
  formData.append("file", file);
  // No Content-Type header here — the browser sets the correct multipart
  // boundary automatically when the body is a FormData instance.
  const res = await fetch(`${API_BASE_URL}${path}`, {
    method: "POST",
    headers: withAuthHeaders(),
    body: formData,
  });
  if (!res.ok) {
    return throwForResponse(res);
  }
  return res.json();
}

// Fire-and-forget ping to wake a cold Render free-tier backend while someone
// is still typing their credentials on the login screen — see Login.tsx's
// mount effect. Deliberately swallows every failure/rejection: nothing
// reads this response, and a warm-up that fails (offline, backend still
// booting) must never surface as an error anywhere. Not routed through
// apiGet, since this must work with no token and must never throw.
export function warmBackend(): void {
  fetch(`${API_BASE_URL}/health`).catch(() => {});
}

// Discriminated on must_change_password: an admin-onboarded account (or any
// account an admin has reset) logs in successfully but gets no access_token
// at all — just a narrowly-scoped reset_token, good for exactly one call to
// changePassword below. Nothing else is reachable until that happens.
export type LoginResult =
  | { mustChangePassword: true; resetToken: string; email: string; facilityName: string }
  | { mustChangePassword: false; user: SessionUser };

// Not using apiPost here since login must work with no token present yet.
export async function login(email: string, password: string): Promise<LoginResult> {
  const res = await fetch(`${API_BASE_URL}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  if (!res.ok) {
    throw new Error(await extractErrorMessage(res));
  }
  const data = await res.json();
  if (data.must_change_password) {
    return { mustChangePassword: true, resetToken: data.reset_token, email: data.email, facilityName: data.facility_name };
  }
  setSession(data.access_token, data.user);
  return { mustChangePassword: false, user: data.user as SessionUser };
}

// Uses the short-lived reset_token from a must-change-password login result
// — not the normal session token, since there isn't one yet. Success
// establishes the real session, same as a normal login.
export async function changePassword(resetToken: string, newPassword: string): Promise<SessionUser> {
  const res = await fetch(`${API_BASE_URL}/auth/change-password`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${resetToken}` },
    body: JSON.stringify({ new_password: newPassword }),
  });
  if (!res.ok) {
    throw new Error(await extractErrorMessage(res));
  }
  const data = await res.json();
  setSession(data.access_token, data.user);
  return data.user as SessionUser;
}

// Self-service "forgot password" — genuinely email-based now, separate from
// login's must_change_password branch (an admin-issued temp password, still
// handled by changePassword/apiChangePassword above). The backend always
// returns the same generic message regardless of whether the email actually
// matched an account — see server/main.py's _FORGOT_PASSWORD_RESPONSE — so
// there is deliberately no email/facilityName/token in this response to
// carry into an immediate set-new-password step the way the old flow did.
export async function requestPasswordReset(email: string): Promise<{ message: string }> {
  const res = await fetch(`${API_BASE_URL}/auth/forgot-password`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email }),
  });
  if (!res.ok) {
    throw new Error(await extractErrorMessage(res));
  }
  return res.json();
}

// Completes a reset started by requestPasswordReset, using the token from
// the emailed link's ?token= query param — not an Authorization header,
// since this token is a single-use DB row (see password_reset_requests), not
// a JWT the way the admin-onboarding reset_token is.
export async function resetPassword(token: string, newPassword: string): Promise<{ message: string }> {
  const res = await fetch(`${API_BASE_URL}/auth/reset-password`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token, new_password: newPassword }),
  });
  if (!res.ok) {
    throw new Error(await extractErrorMessage(res));
  }
  return res.json();
}

export type CompleteProfileBody = {
  address: string;
  latitude: number;
  longitude: number;
  department: string;
  doh_license_number: string;
};

export async function completeFacilityProfile(body: CompleteProfileBody): Promise<void> {
  await apiPost("/facilities/profile", body);
  updateSessionUser({ profile_completed: true });
}

export type FacilityProfile = {
  id: number;
  name: string;
  facility_type: string;
  address: string | null;
  latitude: number | null;
  longitude: number | null;
  department: string | null;
  doh_license_number: string | null;
  profile_completed: boolean;
};

// Read-only twin of completeFacilityProfile's payload shape — the Edit
// Facility Profile form (Account menu) prefills from this.
export function getMyFacilityProfile(): Promise<FacilityProfile> {
  return apiGet<FacilityProfile>("/facilities/me");
}

// Self-service change for an already-logged-in user who knows their current
// password — distinct from changePassword() above, which only ever handles
// the forced-reset flow off a one-time reset_token.
export async function updatePassword(currentPassword: string, newPassword: string): Promise<void> {
  await apiPost("/auth/update-password", { current_password: currentPassword, new_password: newPassword });
}

// Re-checks the session against the DB — used on app load so a session
// persisted from a previous visit doesn't act on stale profile_completed
// state (e.g. completed in a different tab, or reset by an admin since).
export async function refreshCurrentUser(): Promise<SessionUser> {
  const user = await apiGet<SessionUser>("/auth/me");
  updateSessionUser(user);
  return user;
}

export function logout() {
  clearSession();
}

// ─── Admin (role='admin' only — every call here 403s for anyone else) ──────

export type AdminFacilityAccount = { id: number; email: string; must_change_password: boolean };

export type AdminFacility = {
  id: number;
  name: string;
  facility_type: string;
  profile_completed: boolean;
  is_active: boolean;
  accounts: AdminFacilityAccount[];
};

export function adminListFacilities(): Promise<AdminFacility[]> {
  return apiGet<AdminFacility[]>("/admin/facilities");
}

export type CreateFacilityAccountResult = {
  facility: { id: number; name: string; facility_type: string; profile_completed: boolean; is_active: boolean };
  user: { id: number; email: string; facility_id: number; role: string; created_at: string };
  temporary_password: string;
};

export function adminCreateFacility(body: { name: string; facility_type: string; email: string }): Promise<CreateFacilityAccountResult> {
  return apiPost<CreateFacilityAccountResult>("/admin/facilities", body);
}

export function adminSetFacilityActive(facilityId: number, isActive: boolean): Promise<AdminFacility> {
  return apiPatch<AdminFacility>(`/admin/facilities/${facilityId}`, { is_active: isActive });
}

export type AdminPasswordResetResult = { id: number; email: string; temporary_password: string };

export function adminResetAccountPassword(userId: number): Promise<AdminPasswordResetResult> {
  return apiPost<AdminPasswordResetResult>(`/admin/accounts/${userId}/reset-password`, {});
}

// For a facility that already exists but has zero accounts — POST
// /admin/facilities (above) always creates a brand new facility alongside
// its first account, so it can't close this gap. Server 409s if the
// facility already has an account (one account per facility, by design).
export function adminCreateAccountForFacility(facilityId: number, email: string): Promise<CreateFacilityAccountResult> {
  return apiPost<CreateFacilityAccountResult>(`/admin/facilities/${facilityId}/accounts`, { email });
}

// ─── Historical inventory-snapshot backfill (blood banks only — the server
// rejects this for hospitals regardless of what the frontend shows) ────────

export type HistoricalUploadResult = {
  rows_processed: number;
  errors: { row: number | string; reason: string }[];
  days_of_history: number;
  min_days_required: number;
};

export function uploadHistoricalInventorySnapshots(file: File): Promise<HistoricalUploadResult> {
  return apiUploadFile<HistoricalUploadResult>("/forecast/historical-upload", file);
}

export function deleteInventoryUnit(din: string): Promise<{ deleted: true; din: string }> {
  return apiDelete(`/inventory/${encodeURIComponent(din)}`, {});
}

// Bulk-clears the expired backlog for the logged-in facility — soft-delete
// only (sets archived_at server-side), never removes historical rows.
export function archiveExpiredInventory(): Promise<{ archived_count: number }> {
  return apiPost<{ archived_count: number }>("/inventory/archive-expired", {});
}

export type CreateInventoryUnitBody = {
  din: string;
  blood_type: string;
  component: string;
  location: string;
  volume_ml: number;
  collected_date: string;
  expires_date: string;
};

export function createInventoryUnit(body: CreateInventoryUnitBody): Promise<InventoryApiRow> {
  return apiPost<InventoryApiRow>("/inventory", body);
}

// ─── Blood-type thresholds — still global across facilities, not a
// per-facility policy table (see /inventory/summary's docstring server-side).

export type ThresholdRow = { blood_type: string; minimum_units: number; maximum_units: number };

export function updateThreshold(bloodType: string, minimumUnits: number, maximumUnits: number): Promise<ThresholdRow> {
  return apiPut<ThresholdRow>(`/thresholds/${encodeURIComponent(bloodType)}`, {
    minimum_units: minimumUnits,
    maximum_units: maximumUnits,
  });
}

export type NotifyNearbyHospitalsResult = { notified_count: number; notified_facilities: string[] };

export function notifyNearbyHospitals(din: string): Promise<NotifyNearbyHospitalsResult> {
  return apiPost<NotifyNearbyHospitalsResult>(`/inventory/${encodeURIComponent(din)}/notify-nearby-hospitals`, {});
}

// ─── Notifications ──────────────────────────────────────────────────────────

export type NotificationItem = {
  id: number;
  type: string;
  message: string;
  link: string | null;
  read_at: string | null;
  created_at: string;
};

export function listNotifications(): Promise<NotificationItem[]> {
  return apiGet<NotificationItem[]>("/notifications");
}

export function markNotificationRead(notificationId: number): Promise<{ id: number; read?: boolean; already_read_or_not_found?: boolean }> {
  return apiPost(`/notifications/${notificationId}/read`, {});
}

export function markAllNotificationsRead(): Promise<{ marked_read: number }> {
  return apiPost("/notifications/read-all", {});
}

// ─── Upload History ─────────────────────────────────────────────────────────
// Shared log behind all three CSV upload flows. raw_content only ever exists
// for inventory/historical uploads server-side — has_raw_content is how the
// frontend knows whether a "Download original file" action makes sense for
// a given row, without needing to fetch the (possibly large) file to find out.

export type UploadType = "inventory" | "donors" | "historical_stock";

export type UploadHistoryEntry = {
  id: number;
  upload_type: UploadType;
  filename: string | null;
  uploaded_at: string;
  rows_processed: number;
  rows_failed: number;
  error_details: { row: number | string; reason: string }[];
  uploaded_by_email: string | null;
  has_raw_content: boolean;
  undone_at: string | null;
};

export function listUploadHistory(uploadType: UploadType): Promise<UploadHistoryEntry[]> {
  return apiGet<UploadHistoryEntry[]>(`/upload-history?upload_type=${uploadType}`);
}

// Row shapes differ per upload type (a historical-stock row is a date/type/
// unit-count; an inventory row is a DIN/type; a donor row is a name/phone/
// type) — deliberately loose here rather than three near-identical types,
// since every caller already knows uploadType and reads the fields that
// make sense for it (see formatUndoRow in App.tsx).
export type UndoRow = Record<string, string | number>;
export type UndoBlockedRow = UndoRow & { reason: string };

export type UndoPreviewResult = {
  upload_type: UploadType;
  already_undone: boolean;
  eligible: UndoRow[];
  blocked: UndoBlockedRow[];
};

export type UndoApplyResult = {
  removed_count: number;
  blocked: UndoBlockedRow[];
  undone_at: string;
};

// Read-only — recomputes eligibility live every call, no side effects. This
// is what the confirmation step shows before anything is actually removed.
export function previewUploadUndo(uploadId: number): Promise<UndoPreviewResult> {
  return apiGet<UndoPreviewResult>(`/upload-history/${uploadId}/undo-preview`);
}

// The upload_history log row is never deleted by this — only the records it
// created, and only the ones still eligible (server recomputes eligibility
// fresh rather than trusting whatever the preview showed a moment earlier).
export function applyUploadUndo(uploadId: number): Promise<UndoApplyResult> {
  return apiPost<UndoApplyResult>(`/upload-history/${uploadId}/undo`, {});
}

// Fetches the stored CSV and hands it to the browser as a file save, the
// same end result as clicking a normal download link — there's no <a href>
// to point at directly since the request needs the auth header apiGet's
// siblings already centralize.
export async function downloadUploadHistoryFile(uploadId: number, fallbackFilename: string): Promise<void> {
  const res = await fetch(`${API_BASE_URL}/upload-history/${uploadId}/download`, { headers: withAuthHeaders() });
  if (!res.ok) {
    return throwForResponse(res);
  }
  const disposition = res.headers.get("Content-Disposition") ?? "";
  const match = disposition.match(/filename="([^"]+)"/);
  const filename = match?.[1] ?? fallbackFilename;
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
