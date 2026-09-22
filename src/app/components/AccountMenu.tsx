import { useState, useRef, useEffect } from "react";
import { Bell, Building2, CheckCircle, ChevronDown, KeyRound, LogOut, Moon, RefreshCw, Sun, User } from "lucide-react";
import {
  completeFacilityProfile,
  getMyFacilityProfile, updatePassword as apiUpdatePassword,
  requestPasswordReset,
  listNotifications, markNotificationRead, markAllNotificationsRead, type NotificationItem,
} from "../lib/api";
import { type SessionUser } from "../lib/session";
import { getStoredTheme, setTheme, type Theme } from "../lib/theme";
import { Modal } from "./Modal";
import { FacilityLocationFields } from "./FacilityLocationPicker";

// ─── Account Menu (Change Password / Edit Facility Profile / Log out) ─────

function ChangePasswordModal({ email, onClose }: { email: string; onClose: () => void }) {
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);
  const [forgotSending, setForgotSending] = useState(false);
  const [forgotSent, setForgotSent] = useState(false);

  async function handleForgotClick() {
    setForgotSending(true);
    try {
      await requestPasswordReset(email);
    } catch {
      // Same non-committal outcome either way — the backend never reveals
      // whether the send actually succeeded, so there's nothing more useful
      // to show on failure than on success.
    } finally {
      setForgotSending(false);
      setForgotSent(true);
    }
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (newPassword.length < 8) {
      setError("New password must be at least 8 characters");
      return;
    }
    if (newPassword !== confirmPassword) {
      setError("New passwords don't match");
      return;
    }
    setLoading(true);
    try {
      await apiUpdatePassword(currentPassword, newPassword);
      setSuccess(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to change password");
    } finally {
      setLoading(false);
    }
  }

  return (
    <Modal title="Change Password" onClose={onClose}>
      {success ? (
        <div className="text-center py-3">
          <div className="w-12 h-12 rounded-full bg-status-safe-tint flex items-center justify-center mx-auto mb-3">
            <CheckCircle size={22} className="text-status-safe" />
          </div>
          <div className="font-display font-bold text-foreground mb-1">Password updated</div>
          <p className="text-[14px] text-muted-foreground mb-4">Use your new password next time you sign in.</p>
          <button
            onClick={onClose}
            className="w-full h-10 bg-primary text-white text-sm font-bold rounded-md hover:bg-primary-hover transition-colors"
          >
            Done
          </button>
        </div>
      ) : (
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <div className="flex items-center justify-between mb-1.5">
              <label className="text-[14px] font-semibold text-foreground">Current password</label>
              {forgotSent ? (
                <span className="text-[12.5px] text-status-safe font-semibold">Check your email</span>
              ) : (
                <button
                  type="button"
                  onClick={handleForgotClick}
                  disabled={forgotSending}
                  className="text-[12.5px] font-semibold text-primary hover:underline hover:underline-offset-2 disabled:opacity-60"
                >
                  {forgotSending ? "Sending…" : "Forgot your current password?"}
                </button>
              )}
            </div>
            <input
              type="password"
              required
              autoComplete="current-password"
              value={currentPassword}
              onChange={(e) => setCurrentPassword(e.target.value)}
              className="w-full h-10 px-3 text-sm border border-border rounded-md bg-card focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary transition-all"
            />
            {forgotSent && (
              <p className="text-[12.5px] text-muted-foreground mt-1.5">
                If an account exists for {email}, a password reset link has been sent — open it to set a new password, then come back and sign in.
              </p>
            )}
          </div>
          <div>
            <label className="text-[14px] font-semibold text-foreground block mb-1.5">New password</label>
            <input
              type="password"
              required
              autoComplete="new-password"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              className="w-full h-10 px-3 text-sm border border-border rounded-md bg-card focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary transition-all"
            />
          </div>
          <div>
            <label className="text-[14px] font-semibold text-foreground block mb-1.5">Confirm new password</label>
            <input
              type="password"
              required
              autoComplete="new-password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              className="w-full h-10 px-3 text-sm border border-border rounded-md bg-card focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary transition-all"
            />
          </div>

          {error && (
            <div className="text-[13px] text-status-critical-text bg-status-critical-tint border border-status-critical-border rounded-md px-3 py-2">
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={loading}
            className="w-full h-10 bg-primary text-white text-sm font-bold rounded-md hover:bg-primary-hover transition-colors disabled:opacity-60 flex items-center justify-center gap-2"
          >
            {loading ? (
              <><RefreshCw size={15} className="animate-spin" /> Updating…</>
            ) : (
              "Update Password"
            )}
          </button>
        </form>
      )}
    </Modal>
  );
}

function EditFacilityProfileModal({ facilityName, onClose, onSaved }: {
  facilityName: string | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [address, setAddress] = useState("");
  const [department, setDepartment] = useState("");
  const [dohLicense, setDohLicense] = useState("");
  const [position, setPosition] = useState<[number, number] | null>(null);

  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  useEffect(() => {
    getMyFacilityProfile()
      .then((profile) => {
        setAddress(profile.address ?? "");
        setDepartment(profile.department ?? "");
        setDohLicense(profile.doh_license_number ?? "");
        if (profile.latitude !== null && profile.longitude !== null) {
          setPosition([profile.latitude, profile.longitude]);
        }
      })
      .catch((err) => setLoadError(err instanceof Error ? err.message : "Failed to load profile"))
      .finally(() => setLoading(false));
  }, []);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitError(null);
    if (!position) {
      setSubmitError("Confirm this facility's location on the map before saving");
      return;
    }
    setSubmitting(true);
    try {
      await completeFacilityProfile({
        address, latitude: position[0], longitude: position[1],
        department, doh_license_number: dohLicense,
      });
      setSuccess(true);
      onSaved();
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : "Failed to save profile");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal title="Edit Facility Profile" onClose={onClose} wide>
      {loading && <div className="py-8 text-center text-[15px] text-muted-foreground">Loading…</div>}
      {!loading && loadError && (
        <div className="text-[14px] text-status-critical-text bg-status-critical-tint border border-status-critical-border rounded-md px-3 py-2">
          {loadError}
        </div>
      )}
      {!loading && !loadError && success && (
        <div className="text-center py-3">
          <div className="w-12 h-12 rounded-full bg-status-safe-tint flex items-center justify-center mx-auto mb-3">
            <CheckCircle size={22} className="text-status-safe" />
          </div>
          <div className="font-display font-bold text-foreground mb-1">Profile updated</div>
          <p className="text-[14px] text-muted-foreground mb-4">{facilityName}'s address, location, and license are saved.</p>
          <button
            onClick={onClose}
            className="w-full h-10 bg-primary text-white text-sm font-bold rounded-md hover:bg-primary-hover transition-colors"
          >
            Done
          </button>
        </div>
      )}
      {!loading && !loadError && !success && (
        <form onSubmit={handleSubmit} className="space-y-5">
          <FacilityLocationFields
            address={address} onAddressChange={setAddress}
            position={position} onPositionChange={setPosition}
            department={department} onDepartmentChange={setDepartment}
            dohLicense={dohLicense} onDohLicenseChange={setDohLicense}
          />

          {submitError && (
            <div className="text-[13px] text-status-critical-text bg-status-critical-tint border border-status-critical-border rounded-md px-3 py-2">
              {submitError}
            </div>
          )}

          <button
            type="submit"
            disabled={submitting}
            className="w-full h-10 bg-primary text-white text-sm font-bold rounded-md hover:bg-primary-hover transition-colors disabled:opacity-60 flex items-center justify-center gap-2"
          >
            {submitting ? (
              <><RefreshCw size={15} className="animate-spin" /> Saving…</>
            ) : (
              "Save Changes"
            )}
          </button>
        </form>
      )}
    </Modal>
  );
}

function formatRelativeTime(iso: string): string {
  const diffMs = Date.now() - new Date(iso).getTime();
  const diffMin = Math.floor(diffMs / 60000);
  if (diffMin < 1) return "just now";
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.floor(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  const diffDay = Math.floor(diffHr / 24);
  if (diffDay < 7) return `${diffDay}d ago`;
  return new Date(iso).toLocaleDateString();
}

const NOTIFICATION_POLL_MS = 20000;

// Bell trigger + dropdown for facility accounts (TopNav only — admins have no
// facility_id, so notifications, which are facility-scoped, never apply to
// them; AdminDashboardScreen's header renders AccountMenu without this).
export function NotificationBell({ onNavigate }: { onNavigate: (n: NotificationItem) => void }) {
  const [notifications, setNotifications] = useState<NotificationItem[]>([]);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const [justArrived, setJustArrived] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  // null until the first poll resolves — distinguishes "nothing to compare
  // against yet" (don't pulse on initial load) from "compared and nothing's
  // new" (also don't pulse). Only a ID present now that wasn't in the
  // previous poll's set counts as a real arrival.
  const knownIdsRef = useRef<Set<number> | null>(null);

  const pulseTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  function loadNotifications() {
    listNotifications()
      .then((data) => {
        setNotifications(data);
        const ids = new Set(data.map((n) => n.id));
        if (knownIdsRef.current !== null && data.some((n) => !knownIdsRef.current!.has(n.id))) {
          setJustArrived(true);
          if (pulseTimeoutRef.current !== null) clearTimeout(pulseTimeoutRef.current);
          pulseTimeoutRef.current = setTimeout(() => setJustArrived(false), 450);
        }
        knownIdsRef.current = ids;
      })
      // A failed poll shouldn't surface an error banner on every screen —
      // it'll just quietly retry on the next interval tick.
      .catch(() => {})
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    loadNotifications();
    const interval = setInterval(loadNotifications, NOTIFICATION_POLL_MS);
    return () => {
      clearInterval(interval);
      if (pulseTimeoutRef.current !== null) clearTimeout(pulseTimeoutRef.current);
    };
  }, []);

  useEffect(() => {
    if (!open) return;
    function handleClick(e: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [open]);

  const unreadCount = notifications.filter((n) => n.read_at === null).length;

  function handleNotificationClick(n: NotificationItem) {
    setOpen(false);
    if (n.read_at === null) {
      markNotificationRead(n.id).catch(() => {});
      setNotifications((prev) => prev.map((x) => (x.id === n.id ? { ...x, read_at: new Date().toISOString() } : x)));
    }
    onNavigate(n);
  }

  async function handleMarkAllRead() {
    try {
      await markAllNotificationsRead();
      setNotifications((prev) => prev.map((n) => ({ ...n, read_at: n.read_at ?? new Date().toISOString() })));
    } catch {
      // best-effort — next poll will reconcile actual state
    }
  }

  return (
    <div className="relative" ref={menuRef}>
      <button
        onClick={() => setOpen((o) => !o)}
        className="relative p-1.5 text-muted-foreground hover:text-foreground rounded transition-colors"
      >
        <Bell size={19} className={justArrived ? "animate-bell-pulse" : ""} />
        {unreadCount > 0 && (
          <span className="absolute -top-1 -right-1 min-w-[16px] h-4 px-1 bg-primary text-white text-[11px] font-bold rounded-full flex items-center justify-center leading-none">
            {unreadCount > 9 ? "9+" : unreadCount}
          </span>
        )}
      </button>

      {open && (
        <div className="absolute right-0 top-full mt-2 w-80 bg-card border border-border rounded-xl shadow-lg z-50 overflow-hidden">
          <div className="px-4 py-3 border-b border-border flex items-center justify-between">
            <h3 className="font-display font-bold text-[15px] text-foreground">Notifications</h3>
            {unreadCount > 0 && (
              <button onClick={handleMarkAllRead} className="text-[12px] font-semibold text-primary hover:underline hover:underline-offset-2">
                Mark all as read
              </button>
            )}
          </div>
          <div className="max-h-96 overflow-y-auto">
            {loading && <div className="px-4 py-8 text-center text-[14px] text-muted-foreground">Loading…</div>}
            {!loading && notifications.length === 0 && (
              <div className="px-4 py-8 text-center text-[14px] text-muted-foreground">No notifications yet.</div>
            )}
            {!loading && notifications.map((n) => (
              <button
                key={n.id}
                onClick={() => handleNotificationClick(n)}
                className={`w-full text-left px-4 py-3 border-b border-border last:border-0 hover:bg-secondary transition-colors flex gap-2.5 ${
                  n.read_at === null ? "bg-primary-tint/40" : ""
                }`}
              >
                <span className={`w-1.5 h-1.5 rounded-full mt-1.5 shrink-0 ${n.read_at === null ? "bg-primary" : "bg-transparent"}`} />
                <div className="min-w-0">
                  <div className={`text-[14px] leading-snug ${n.read_at === null ? "font-semibold text-foreground" : "text-muted-foreground"}`}>
                    {n.message}
                  </div>
                  <div className="text-[12px] text-muted-foreground mt-0.5 font-mono">
                    {formatRelativeTime(n.created_at)}
                  </div>
                </div>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// Trigger + dropdown shared by TopNav (facility accounts) and
// AdminDashboardScreen's header (admin accounts) — the admin variant just
// omits Edit Facility Profile, since role='admin' has no facility_id to edit.
export function AccountMenu({ user, onLogout }: { user: SessionUser; onLogout: () => void }) {
  const [open, setOpen] = useState(false);
  const [showChangePassword, setShowChangePassword] = useState(false);
  const [showEditProfile, setShowEditProfile] = useState(false);
  const [theme, setThemeState] = useState<Theme>(() => getStoredTheme());
  const menuRef = useRef<HTMLDivElement>(null);
  const isAdmin = user.role === "admin";

  function handleThemeChange(next: Theme) {
    setTheme(next);
    setThemeState(next);
  }

  useEffect(() => {
    if (!open) return;
    function handleClick(e: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [open]);

  return (
    <div className="relative" ref={menuRef}>
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-2 pl-3 pr-2 py-1 border-l border-border rounded-lg hover:bg-secondary transition-colors"
      >
        <div className="w-7 h-7 rounded-full bg-primary/10 flex items-center justify-center shrink-0">
          <User size={16} className="text-primary" />
        </div>
        {/* Hidden below lg (not md — the nav's own labels also turn on at
            md, and together with this they don't actually fit until lg;
            768-1023px overflowed the whole header horizontally with the md
            threshold). Capped width + truncate on top, so a genuinely long
            email or facility name can't reopen the same overflow on its own
            even at lg+. Avatar + chevron alone still identify the control;
            the menu itself still shows the full email/facility name once
            opened. */}
        <div className="hidden lg:block text-[13px] text-left max-w-[180px] min-w-0">
          <div className="font-semibold text-foreground leading-tight truncate">{user.email}</div>
          <div className="text-muted-foreground leading-tight truncate">{isAdmin ? "Administrator" : user.facility_name}</div>
        </div>
        <ChevronDown size={15} className={`text-muted-foreground transition-transform shrink-0 ${open ? "rotate-180" : ""}`} />
      </button>

      {open && (
        <div className="absolute right-0 top-full mt-2 w-72 bg-card border border-border rounded-xl shadow-lg z-50 overflow-hidden">
          <div className="px-4 py-3 border-b border-border">
            <div className="text-[14px] font-bold text-foreground">{user.email}</div>
            {!isAdmin && <div className="text-[13px] text-muted-foreground mt-0.5">{user.facility_name}</div>}
            <span className="inline-flex items-center mt-1.5 px-2 py-0.5 rounded-full text-[11.5px] font-bold uppercase tracking-wide bg-primary-tint text-primary">
              {isAdmin ? "Admin" : user.role}
            </span>
          </div>

          <div className="px-4 py-3 border-b border-border">
            <p className="text-[12px] font-bold text-muted-foreground uppercase tracking-wide mb-1.5">Appearance</p>
            <div className="flex gap-1 bg-secondary rounded-lg p-1">
              <button
                type="button"
                onClick={() => handleThemeChange("light")}
                className={`flex-1 flex items-center justify-center gap-1.5 px-3 py-1.5 text-[13px] font-semibold rounded border transition-all duration-200 ease-out ${
                  theme === "light"
                    ? "bg-card text-foreground shadow-sm border-border"
                    : "border-transparent text-muted-foreground hover:text-foreground"
                }`}
              >
                <Sun size={14} /> Light
              </button>
              <button
                type="button"
                onClick={() => handleThemeChange("dark")}
                className={`flex-1 flex items-center justify-center gap-1.5 px-3 py-1.5 text-[13px] font-semibold rounded border transition-all duration-200 ease-out ${
                  theme === "dark"
                    ? "bg-card text-foreground shadow-sm border-border"
                    : "border-transparent text-muted-foreground hover:text-foreground"
                }`}
              >
                <Moon size={14} /> Dark
              </button>
            </div>
          </div>

          <div className="py-1.5">
            <button
              onClick={() => { setShowChangePassword(true); setOpen(false); }}
              className="w-full flex items-center gap-2.5 px-4 py-2 text-[14px] font-semibold text-foreground hover:bg-secondary transition-colors"
            >
              <KeyRound size={15} className="text-muted-foreground" /> Change Password
            </button>
            {!isAdmin && (
              <button
                onClick={() => { setShowEditProfile(true); setOpen(false); }}
                className="w-full flex items-center gap-2.5 px-4 py-2 text-[14px] font-semibold text-foreground hover:bg-secondary transition-colors"
              >
                <Building2 size={15} className="text-muted-foreground" /> Edit Facility Profile
              </button>
            )}
          </div>

          <div className="border-t border-border py-1.5">
            <button
              onClick={onLogout}
              className="w-full flex items-center gap-2.5 px-4 py-2 text-[14px] font-semibold text-status-critical-text hover:bg-status-critical-tint transition-colors"
            >
              <LogOut size={15} /> Log out
            </button>
          </div>
        </div>
      )}

      {showChangePassword && (
        <ChangePasswordModal email={user.email} onClose={() => setShowChangePassword(false)} />
      )}
      {showEditProfile && !isAdmin && (
        <EditFacilityProfileModal
          facilityName={user.facility_name}
          onClose={() => setShowEditProfile(false)}
          onSaved={() => {}}
        />
      )}
    </div>
  );
}

