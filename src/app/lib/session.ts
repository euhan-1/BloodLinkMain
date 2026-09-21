export type SessionUser = {
  id: number;
  email: string;
  // null only for role='admin' accounts — admins don't belong to a facility.
  facility_id: number | null;
  facility_name: string | null;
  // "hospital" | "bloodbank" | null (null only for admin). Drives the
  // --role-accent CSS token (see theme.css / App.tsx's data-role effect) —
  // present from login/refresh, not a separate fetch.
  facility_type: string | null;
  role: string;
  profile_completed: boolean;
};

const STORAGE_KEY = "bloodlink_session";

type SessionState = { token: string; user: SessionUser } | null;

function loadSession(): SessionState {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

let session: SessionState = loadSession();

export function getToken(): string | null {
  return session?.token ?? null;
}

export function getCurrentUser(): SessionUser | null {
  return session?.user ?? null;
}

export function setSession(token: string, user: SessionUser) {
  session = { token, user };
  localStorage.setItem(STORAGE_KEY, JSON.stringify(session));
}

// Patches the stored user in place (e.g. after GET /auth/me or completing
// the profile flow flips profile_completed) without touching the token.
export function updateSessionUser(patch: Partial<SessionUser>) {
  if (!session) return;
  session = { ...session, user: { ...session.user, ...patch } };
  localStorage.setItem(STORAGE_KEY, JSON.stringify(session));
}

export function clearSession() {
  session = null;
  localStorage.removeItem(STORAGE_KEY);
}
