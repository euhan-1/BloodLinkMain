import { lazy, Suspense, useState, useRef, useEffect, useId, type RefObject } from "react";
import { Bell, Building2, CheckCircle, ChevronDown, KeyRound, LogOut, Moon, RefreshCw, ShieldCheck, Sun, User } from "lucide-react";
import {
  updatePassword as apiUpdatePassword,
  requestPasswordReset,
  listNotifications, markNotificationRead, markAllNotificationsRead, type NotificationItem,
} from "../lib/api";
import {
  agoText, appendOlder, badgeLabel, isArrival, markEveryRead, markOneRead, mayHaveMore, oldestId, unreadText,
} from "../lib/notifications";
import { formatDateTime } from "../lib/dateFormat";
import { type SessionUser } from "../lib/session";
import { getStoredTheme, setTheme, type Theme } from "../lib/theme";
import { Modal } from "./Modal";
import { DialogPanel } from "./DialogPanel";
import { liveSync } from "../lib/liveSyncRuntime";

// Lazy on purpose: the edit-profile form embeds the map picker, and Leaflet would otherwise ride along in the first chunk and be
// downloaded by every visitor to the sign-in page.
const EditFacilityProfileModal = lazy(() => import("./EditFacilityProfileModal").then((m) => ({ default: m.EditFacilityProfileModal })));

// ─── Account Menu (Change Password / Edit Facility Profile / Log out) ─────

function ChangePasswordModal({ email, onClose, returnFocusRef }: { email: string; onClose: () => void; returnFocusRef?: RefObject<HTMLElement | null> }) {
  const uid = useId(); // ties each label to its field, so the fields have names for assistive technology
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
    <Modal title="Change Password" onClose={onClose} returnFocusRef={returnFocusRef}>
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
              <label htmlFor={`${uid}-current`} className="text-[14px] font-semibold text-foreground">Current password</label>
              {forgotSent ? (
                <span className="text-[12.5px] text-status-safe font-semibold">Check your email</span>
              ) : (
                <button
                  type="button"
                  onClick={handleForgotClick}
                  disabled={forgotSending}
                  className="text-[12.5px] font-semibold text-role-accent-text hover:underline hover:underline-offset-2 disabled:opacity-60"
                >
                  {forgotSending ? "Sending…" : "Forgot your current password?"}
                </button>
              )}
            </div>
            <input
              id={`${uid}-current`}
              data-autofocus
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
            <label htmlFor={`${uid}-new`} className="text-[14px] font-semibold text-foreground block mb-1.5">New password</label>
            <input
              id={`${uid}-new`}
              type="password"
              required
              autoComplete="new-password"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              className="w-full h-10 px-3 text-sm border border-border rounded-md bg-card focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary transition-all"
            />
          </div>
          <div>
            <label htmlFor={`${uid}-confirm`} className="text-[14px] font-semibold text-foreground block mb-1.5">Confirm new password</label>
            <input
              id={`${uid}-confirm`}
              type="password"
              required
              autoComplete="new-password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              className="w-full h-10 px-3 text-sm border border-border rounded-md bg-card focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary transition-all"
            />
          </div>

          {error && (
            <div role="alert" className="text-[13px] text-status-critical-text bg-status-critical-tint border border-status-critical-border rounded-md px-3 py-2">
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

const NOTIFICATION_PAGE = 50;

// The header's inner row, for every role: TopNav (facility accounts, App.tsx) and the Admin screen's header both use it, so the logo
// and the account button sit at the same x on every screen and at every width, and the bar is one height. 4rem tall (it was 3.5rem):
// the controls inside are at least 44 px (2.75rem), the WCAG 2.5.5 / Apple HIG target size.
export const HEADER_ROW_CLASS = "max-w-screen-2xl mx-auto px-4 sm:px-6 flex items-center gap-2 sm:gap-8 h-16 min-w-0";

// Bell trigger + dropdown for facility accounts (TopNav only — admins have no
// facility_id, so notifications, which are facility-scoped, never apply to
// them; AdminDashboardScreen's header renders AccountMenu without this).
//
// The badge is the server's count of every unread notification, delivered by the one shared poller (lib/liveSync.ts, GET /sync,
// about every 15 s while the browser tab is visible, and at once when it becomes visible again), not a count of whatever page
// happens to be loaded. The list loads when it is opened, 50 at a time, with "Show older" for the rest.
export function NotificationBell({ onNavigate }: { onNavigate: (n: NotificationItem) => void }) {
  const [notifications, setNotifications] = useState<NotificationItem[]>([]);
  const [unread, setUnread] = useState(0);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [justArrived, setJustArrived] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const headingId = useId();
  // null until the first answer: nothing to compare against yet, so the first load never pulses.
  const knownLatestIdRef = useRef<number | null>(null);
  const openRef = useRef(false);
  const pulseTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  openRef.current = open;

  async function loadFirstPage() {
    setLoading(true);
    try {
      const page = await listNotifications({ limit: NOTIFICATION_PAGE });
      setNotifications(page);
      setHasMore(mayHaveMore(page.length, NOTIFICATION_PAGE));
      setLoadError(false);
    } catch {
      setLoadError(true);
    } finally {
      setLoading(false);
    }
  }

  async function loadOlder() {
    const before = oldestId(notifications);
    if (before === null || loadingMore) return;
    setLoadingMore(true);
    try {
      const older = await listNotifications({ limit: NOTIFICATION_PAGE, beforeId: before });
      setNotifications((prev) => appendOlder(prev, older));
      setHasMore(mayHaveMore(older.length, NOTIFICATION_PAGE));
    } catch {
      setLoadError(true);
    } finally {
      setLoadingMore(false);
    }
  }

  // The two numbers arrive with every answer of the shared poller (and at once on subscribing, if it already has them). A failed
  // poll is the poller's business (it backs off); the bell just keeps what it shows.
  useEffect(() => {
    const unsubscribe = liveSync.subscribeNotifications(({ unread: count, latest_id }) => {
      setUnread(count);
      if (isArrival(knownLatestIdRef.current, latest_id)) {
        setJustArrived(true);
        if (pulseTimeoutRef.current !== null) clearTimeout(pulseTimeoutRef.current);
        pulseTimeoutRef.current = setTimeout(() => setJustArrived(false), 450);
        if (openRef.current) loadFirstPage();
      }
      knownLatestIdRef.current = latest_id;
    });
    return () => {
      unsubscribe();
      if (pulseTimeoutRef.current !== null) clearTimeout(pulseTimeoutRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!open) return;
    loadFirstPage();
    function handleClick(e: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  async function handleNotificationClick(n: NotificationItem) {
    setOpen(false);
    if (n.read_at === null) {
      const at = new Date().toISOString();
      setNotifications((prev) => markOneRead(prev, n.id, at));
      setUnread((u) => Math.max(0, u - 1));
      try {
        await markNotificationRead(n.id);
      } catch {
        // It did not save: put it back as unread rather than let the next poll quietly undo it.
        setNotifications((prev) => prev.map((x) => (x.id === n.id ? { ...x, read_at: null } : x)));
        setUnread((u) => u + 1);
      }
    }
    onNavigate(n);
  }

  async function handleMarkAllRead() {
    try {
      await markAllNotificationsRead();
      setNotifications((prev) => markEveryRead(prev, new Date().toISOString()));
      setUnread(0);
    } catch {
      // best-effort — the next poll will reconcile the real count
    }
  }

  const badge = badgeLabel(unread);

  // Below `sm` the wrapper is not positioned, so the panel anchors to the (full-width, sticky) header and spans the viewport with a
  // 1rem gutter; anchored to the bell it was 320 px wide and ran off the left edge of a phone (-28 px at 390 px wide, before this round).
  return (
    <div className="sm:relative" ref={menuRef}>
      <button
        ref={triggerRef}
        onClick={() => setOpen((o) => !o)}
        aria-label={unread > 0 ? `Notifications, ${unread} unread` : "Notifications"}
        aria-haspopup="dialog"
        aria-expanded={open}
        className="flex items-center justify-center w-11 h-11 text-muted-foreground hover:text-foreground rounded transition-colors"
      >
        {/* The badge is anchored to the icon, not to the button, so it keeps its place on the icon however big the target around it is. */}
        <span className="relative inline-flex">
          <Bell size={22} className={justArrived ? "animate-bell-pulse" : ""} />
          {badge !== null && (
            <span className="absolute -top-2.5 -right-2.5 min-w-[18px] h-[18px] px-1 bg-primary text-white text-[12px] font-bold rounded-full flex items-center justify-center leading-none">
              {badge}
            </span>
          )}
        </span>
      </button>

      {open && (
        <DialogPanel
          labelledBy={headingId} onClose={() => setOpen(false)} returnFocusRef={triggerRef}
          className="absolute left-4 right-4 sm:left-auto sm:right-0 top-full mt-2 sm:w-80 bg-card border border-border rounded-xl shadow-lg z-50 overflow-hidden"
        >
          <div className="px-4 py-3 border-b border-border flex items-center justify-between gap-3">
            <div>
              <h2 id={headingId} className="font-display font-bold text-[15px] text-foreground">Notifications</h2>
              <div className="text-[12px] text-foreground" role="status">{unreadText(unread)}</div>
            </div>
            {unread > 0 && (
              <button onClick={handleMarkAllRead} className="text-[12px] font-semibold text-role-accent-text hover:underline hover:underline-offset-2 shrink-0">
                Mark all as read
              </button>
            )}
          </div>
          <div className="max-h-96 overflow-y-auto">
            {loading && notifications.length === 0 && <div className="px-4 py-8 text-center text-[14px] text-muted-foreground">Loading…</div>}
            {loadError && notifications.length === 0 && !loading && (
              <div className="px-4 py-8 text-center text-[14px] text-status-critical-text">Couldn't load notifications.</div>
            )}
            {!loading && !loadError && notifications.length === 0 && (
              <div className="px-4 py-8 text-center text-[14px] text-muted-foreground">No notifications yet.</div>
            )}
            {notifications.map((n) => (
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
                  <time dateTime={n.created_at} title={formatDateTime(n.created_at)} className="block text-[12px] text-muted-foreground mt-0.5 tabular-nums">
                    {agoText(n.created_at)}
                    <span className="sr-only">, {formatDateTime(n.created_at)}</span>
                  </time>
                </div>
              </button>
            ))}
            {hasMore && (
              <button
                onClick={loadOlder}
                disabled={loadingMore}
                className="w-full px-4 py-3 text-[13px] font-semibold text-role-accent-text hover:bg-secondary transition-colors disabled:opacity-60"
              >
                {loadingMore ? "Loading…" : "Show older notifications"}
              </button>
            )}
          </div>
        </DialogPanel>
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
  const triggerRef = useRef<HTMLButtonElement>(null);
  const headingId = useId();
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
        ref={triggerRef}
        onClick={() => setOpen((o) => !o)}
        aria-label={`Account menu, ${user.email}`}
        aria-haspopup="dialog"
        aria-expanded={open}
        className="flex items-center gap-2.5 min-h-11 pl-3.5 pr-2.5 py-1 border-l border-border rounded-lg hover:bg-secondary transition-colors"
      >
        <div className="w-8 h-8 rounded-full bg-primary/10 flex items-center justify-center shrink-0">
          <User size={18} className="text-role-accent-text" />
        </div>
        {/* Hidden below lg (not md — the nav's own labels also turn on at
            md, and together with this they don't actually fit until lg;
            768-1023px overflowed the whole header horizontally with the md
            threshold). Capped width + truncate on top, so a genuinely long
            email or facility name can't reopen the same overflow on its own
            even at lg+. Avatar + chevron alone still identify the control;
            the menu itself still shows the full email/facility name once
            opened. */}
        <div className="hidden lg:block text-[14px] text-left max-w-[180px] min-w-0">
          <div className="font-semibold text-foreground leading-tight truncate">{user.email}</div>
          <div className="text-muted-foreground leading-tight truncate">{isAdmin ? "Administrator" : user.facility_name}</div>
        </div>
        <ChevronDown size={17} className={`text-muted-foreground transition-transform shrink-0 ${open ? "rotate-180" : ""}`} />
      </button>

      {open && (
        <DialogPanel
          labelledBy={headingId} onClose={() => setOpen(false)} returnFocusRef={triggerRef}
          className="absolute right-0 top-full mt-2 w-72 bg-card border border-border rounded-xl shadow-lg z-50 overflow-hidden"
        >
          <div className="px-4 py-3 border-b border-border">
            <h2 id={headingId} className="sr-only">Account menu</h2>
            <div className="text-[14px] font-bold text-foreground">{user.email}</div>
            {!isAdmin && <div className="text-[13px] text-muted-foreground mt-0.5">{user.facility_name}</div>}
            <span className="inline-flex items-center mt-1.5 px-2 py-0.5 rounded-full text-[11.5px] font-bold uppercase tracking-wide bg-primary-tint text-role-accent-text">
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
                <Building2 size={15} className="text-muted-foreground" /> Edit facility profile
              </button>
            )}
            <a
              href="/privacy" target="_blank" rel="noopener" onClick={() => setOpen(false)}
              className="w-full flex items-center gap-2.5 px-4 py-2 text-[14px] font-semibold text-foreground hover:bg-secondary transition-colors"
            >
              <ShieldCheck size={15} className="text-muted-foreground" /> Privacy notice
            </a>
          </div>

          <div className="border-t border-border py-1.5">
            <button
              onClick={onLogout}
              className="w-full flex items-center gap-2.5 px-4 py-2 text-[14px] font-semibold text-status-critical-text hover:bg-status-critical-tint transition-colors"
            >
              <LogOut size={15} /> Log out
            </button>
          </div>
        </DialogPanel>
      )}

      {showChangePassword && (
        <ChangePasswordModal email={user.email} onClose={() => setShowChangePassword(false)} returnFocusRef={triggerRef} />
      )}
      {showEditProfile && !isAdmin && (
        <Suspense
          fallback={
            <Modal title="Edit facility profile" onClose={() => setShowEditProfile(false)} wide returnFocusRef={triggerRef}>
              <div className="py-8 text-center text-[15px] text-muted-foreground">Loading…</div>
            </Modal>
          }
        >
          <EditFacilityProfileModal
            facilityName={user.facility_name}
            onClose={() => setShowEditProfile(false)}
            returnFocusRef={triggerRef}
            onSaved={() => {}}
          />
        </Suspense>
      )}
    </div>
  );
}

