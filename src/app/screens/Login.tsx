import { useEffect, useId, useState } from "react";
import { CheckCircle, RefreshCw, ShieldCheck } from "lucide-react";
import {
  login as apiLogin, changePassword as apiChangePassword, requestPasswordResetOtp, resetPasswordOtp, warmBackend,
} from "../lib/api";
import { type SessionUser } from "../lib/session";
import { BloodDropLogo } from "../components/BloodTypeBadge";
import { FOCUS } from "../lib/formStyles";

// ─── Login Screen ─────────────────────────────────────────────────────────────

// FOCUS (lib/formStyles.ts) is the focus outline every control here uses; the fields' own 30%-ring was too faint.
const FIELD = `w-full h-10 px-3 text-sm border border-border rounded-md bg-card placeholder:text-secondary-foreground/60 focus:border-primary transition-colors ${FOCUS}`;
const BTN = `w-full h-10 bg-primary text-white text-sm font-semibold rounded-md hover:bg-primary-hover transition-colors disabled:opacity-60 flex items-center justify-center gap-2 ${FOCUS}`;
const LINK_BTN = `text-[13px] text-muted-foreground hover:text-foreground transition-colors rounded-sm ${FOCUS}`;

// Only ever produced by an admin creating/resetting an account (a one-time
// temp password) — the self-service "Forgot password?" flow no longer feeds
// this at all, since that reset now genuinely goes out by email instead of
// handing a token straight back to the requester (see forgotSent below).
type PendingReset = { resetToken: string; email: string; facilityName: string };

export function LoginScreen({ onLogin }: { onLogin: (user: SessionUser) => void }) {
  const uid = useId(); // prefixes the ids of the reset-flow fields, so no two fields on the page can share one
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pendingReset, setPendingReset] = useState<PendingReset | null>(null);

  const [forgotMode, setForgotMode] = useState(false);
  const [forgotEmail, setForgotEmail] = useState("");
  const [forgotLoading, setForgotLoading] = useState(false);
  const [forgotError, setForgotError] = useState<string | null>(null);
  // Set once the request succeeds — the confirmation message stays generic
  // ("if an account exists...") on purpose, matching the backend's own
  // refusal to reveal whether the email matched anything.
  const [forgotSent, setForgotSent] = useState(false);

  // Typed-code step (Step 7) — replaces the old "check your email for a
  // link" terminal state. A clicked-link reset breaks for institutional
  // mail scanners that consume single-use tokens before the recipient ever
  // sees them; a typed code has nothing for a scanner to consume.
  const [resetCode, setResetCode] = useState("");
  const [resetNewPassword, setResetNewPassword] = useState("");
  const [resetConfirmPassword, setResetConfirmPassword] = useState("");
  const [resetLoading, setResetLoading] = useState(false);
  const [resetError, setResetError] = useState<string | null>(null);
  const [resetDone, setResetDone] = useState(false);

  // Fires the moment this screen mounts, not on submit — so a Render
  // free-tier cold backend is already waking up while someone's still
  // typing their email/password, instead of only starting once they click
  // Sign in. Purely a warm-up: the response is never read.
  useEffect(() => {
    warmBackend();
  }, []);

  // True once the in-flight login request has been pending long enough that
  // it's almost certainly a cold start, not a normal round trip — so the
  // button can say so instead of leaving a spinner that reads as "broken"
  // for however long Render takes to wake up.
  const [slowLogin, setSlowLogin] = useState(false);
  useEffect(() => {
    if (!loading) {
      setSlowLogin(false);
      return;
    }
    const timer = setTimeout(() => setSlowLogin(true), 3000);
    return () => clearTimeout(timer);
  }, [loading]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const result = await apiLogin(email, password);
      if (result.mustChangePassword) {
        // No access token was issued — see api.ts's LoginResult. Nothing to
        // do here but hand off to the forced-reset form; onLogin only fires
        // once that form actually establishes a real session.
        setPendingReset({ resetToken: result.resetToken, email: result.email, facilityName: result.facilityName });
      } else {
        onLogin(result.user);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed");
    } finally {
      setLoading(false);
    }
  }

  async function handleForgotSubmit(e: React.FormEvent) {
    e.preventDefault();
    setForgotLoading(true);
    setForgotError(null);
    try {
      await requestPasswordResetOtp(forgotEmail);
      setForgotSent(true);
    } catch (err) {
      setForgotError(err instanceof Error ? err.message : "Failed to request a reset");
    } finally {
      setForgotLoading(false);
    }
  }

  async function handleResetOtpSubmit(e: React.FormEvent) {
    e.preventDefault();
    setResetError(null);
    if (resetNewPassword.length < 8) {
      setResetError("Password must be at least 8 characters");
      return;
    }
    if (resetNewPassword !== resetConfirmPassword) {
      setResetError("Passwords don't match");
      return;
    }
    setResetLoading(true);
    try {
      await resetPasswordOtp(forgotEmail, resetCode, resetNewPassword);
      setResetDone(true);
    } catch (err) {
      setResetError(err instanceof Error ? err.message : "Failed to reset password");
    } finally {
      setResetLoading(false);
    }
  }

  return (
    <div className="min-h-screen bg-background flex">
      {/* Left panel */}
      {/* Rows 1fr / auto / 1fr: the content block sits at the panel's own vertical centre, which is where the form
          centres on the right, so the two halves share a centre line. The logo lives in the top row. */}
      <div className="hidden lg:grid grid-rows-[1fr_auto_1fr] w-2/5 shrink-0 bg-[color:var(--login-panel)] text-white px-10 xl:px-14 py-12 animate-panel-slide-left">
        <div className="flex items-start gap-4">
          {/* The drop is a fixed oxblood fill, the same colour as this panel. */}
          <span className="grid place-items-center w-14 h-14 rounded-xl bg-white">
            <BloodDropLogo size={40} />
          </span>
          <span className="text-3xl font-bold tracking-tight leading-[3.5rem]">BloodLink</span>
        </div>

        <div>
          <p className="font-login-serif text-[clamp(1.75rem,2.4vw,2.5rem)] font-bold leading-[1.1] tracking-[-0.02em] text-balance mb-8">
            An AI-Powered Blood Supply Management and Emergency Response System
          </p>
          <div className="max-w-[34ch]">
            <p className="text-white text-[16px] font-[500] leading-[1.7]">
              Real-time inventory management, demand forecasting and emergency sourcing — unified for blood banks and hospitals across the network.
            </p>
            <div
              className="mt-10 flex items-start gap-3 pt-6 border-t border-white/25 text-white text-sm font-[500] leading-relaxed animate-bullet-rise-in"
              style={{ animationDelay: "360ms" }}
            >
              <ShieldCheck size={16} className="shrink-0 mt-0.5" />
              Every facility is reviewed and approved by an administrator before access is granted.
            </div>
          </div>
        </div>
      </div>

      {/* Right panel */}
      <div className="flex-1 flex items-center justify-center px-6 py-10 sm:p-10">
        <div className="w-full max-w-[380px] animate-panel-slide-right">
          <div className="flex items-center gap-2 mb-8 lg:hidden">
            <BloodDropLogo size={28} />
            <span className="text-lg font-bold">Blood<span className="text-primary">Link</span></span>
          </div>

          {pendingReset ? (
            <ForcePasswordChangeForm
              pendingReset={pendingReset}
              onCancel={() => setPendingReset(null)}
              onSuccess={onLogin}
            />
          ) : forgotMode ? (
            resetDone ? (
              <>
                <div className="flex items-center gap-2 mb-2">
                  <CheckCircle size={22} className="text-status-safe-text" />
                  <h1 className="font-display text-2xl font-bold text-foreground">Password updated</h1>
                </div>
                <p className="text-muted-foreground text-sm mb-6">
                  You can now log in with your new password.
                </p>
                <button
                  type="button"
                  onClick={() => {
                    setForgotMode(false); setForgotSent(false); setResetDone(false); setForgotEmail("");
                    setResetCode(""); setResetNewPassword(""); setResetConfirmPassword("");
                    setError(null); setResetError(null);
                  }}
                  className={BTN}
                >
                  Back to sign in
                </button>
              </>
            ) : forgotSent ? (
              <>
                <h1 className="font-display text-2xl font-bold text-foreground mb-1">Enter your reset code</h1>
                <p className="text-muted-foreground text-sm mb-6">
                  If an account exists for <strong className="text-foreground">{forgotEmail}</strong>, we've sent a
                  6-digit code to it. Enter it below along with your new password. The code expires in 10 minutes.
                </p>

                {/* The three forms below sit at the same spot in the tree with the same element types, so without distinct keys
                    React reuses their DOM nodes when the view switches: "Back to sign in" (type="button") becomes the sign-in
                    form's submit button mid-click, and the browser then submits. Keys make each view its own element. */}
                <form key="reset-code" onSubmit={handleResetOtpSubmit} className="space-y-4">
                  <div>
                    <label htmlFor={`${uid}-reset-code`} className="text-[14px] font-semibold text-foreground block mb-1.5">Reset code</label>
                    <input
                      id={`${uid}-reset-code`}
                      type="text"
                      inputMode="numeric"
                      pattern="[0-9]{6}"
                      maxLength={6}
                      required
                      autoComplete="one-time-code"
                      value={resetCode}
                      onChange={(e) => setResetCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
                      placeholder="000000"
                      className={`${FIELD} tracking-[0.3em] text-center font-mono`}
                    />
                  </div>
                  <div>
                    <label htmlFor={`${uid}-reset-new`} className="text-[14px] font-semibold text-foreground block mb-1.5">New password</label>
                    <input
                      id={`${uid}-reset-new`}
                      type="password"
                      required
                      autoComplete="new-password"
                      value={resetNewPassword}
                      onChange={(e) => setResetNewPassword(e.target.value)}
                      className={FIELD}
                    />
                  </div>
                  <div>
                    <label htmlFor={`${uid}-reset-confirm`} className="text-[14px] font-semibold text-foreground block mb-1.5">Confirm new password</label>
                    <input
                      id={`${uid}-reset-confirm`}
                      type="password"
                      required
                      autoComplete="new-password"
                      value={resetConfirmPassword}
                      onChange={(e) => setResetConfirmPassword(e.target.value)}
                      className={FIELD}
                    />
                  </div>

                  {resetError && (
                    <div role="alert" className="text-[13px] text-status-critical-text bg-status-critical-tint border border-status-critical-border rounded-md px-3 py-2">
                      {resetError}
                    </div>
                  )}

                  <button
                    type="submit"
                    disabled={resetLoading || resetCode.length !== 6}
                    className={BTN}
                  >
                    {resetLoading ? (
                      <><RefreshCw size={15} className="animate-spin" /> Updating…</>
                    ) : (
                      "Update password"
                    )}
                  </button>
                  <button
                    type="button"
                    onClick={() => { setForgotSent(false); setResetError(null); setResetCode(""); }}
                    className={`w-full ${LINK_BTN}`}
                  >
                    Didn't get a code? Go back
                  </button>
                </form>
              </>
            ) : (
              <>
                <h1 className="font-display text-2xl font-bold text-foreground mb-1">Reset your password</h1>
                <p className="text-muted-foreground text-sm mb-6">
                  Enter the email address on your facility's account and we'll send you a code to reset your password.
                </p>

                <form key="forgot-email" onSubmit={handleForgotSubmit} className="space-y-4">
                  <div>
                    <label htmlFor={`${uid}-forgot-email`} className="text-[14px] font-semibold text-foreground block mb-1.5">
                      Email address
                    </label>
                    <input
                      id={`${uid}-forgot-email`}
                      type="email"
                      required
                      autoComplete="email"
                      value={forgotEmail}
                      onChange={(e) => setForgotEmail(e.target.value)}
                      className={FIELD}
                    />
                  </div>

                  {forgotError && (
                    <div role="alert" className="text-[13px] text-status-critical-text bg-status-critical-tint border border-status-critical-border rounded-md px-3 py-2">
                      {forgotError}
                    </div>
                  )}

                  <button
                    type="submit"
                    disabled={forgotLoading}
                    className={BTN}
                  >
                    {forgotLoading ? (
                      <><RefreshCw size={15} className="animate-spin" /> Sending…</>
                    ) : (
                      "Send reset code"
                    )}
                  </button>
                  <button
                    type="button"
                    onClick={() => { setForgotMode(false); setForgotError(null); }}
                    className={`w-full ${LINK_BTN}`}
                  >
                    Back to sign in
                  </button>
                </form>
              </>
            )
          ) : (
            <>
              <h1 className="font-display text-2xl font-bold text-foreground mb-1">Sign in</h1>
              <p className="text-muted-foreground text-sm font-[500] mb-8">
                Access your facility's blood management dashboard.
              </p>

              <form key="sign-in" onSubmit={handleSubmit} className="space-y-4">
                <div>
                  <label htmlFor="login-email" className="text-[14px] font-semibold text-foreground block mb-1.5">
                    Email address
                  </label>
                  <input
                    id="login-email"
                    type="email"
                    required
                    autoComplete="email"
                    placeholder="name@facility.com"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    className={FIELD}
                  />
                </div>
                <div>
                  <div className="flex justify-between mb-1.5">
                    <label htmlFor="login-password" className="text-[14px] font-semibold text-foreground">Password</label>
                    <button
                      type="button"
                      onClick={() => { setForgotMode(true); setError(null); setForgotEmail(email); }}
                      className={`text-[13px] text-role-accent-text hover:underline hover:underline-offset-2 rounded-sm ${FOCUS}`}
                    >
                      Forgot password?
                    </button>
                  </div>
                  <input
                    id="login-password"
                    type="password"
                    required
                    autoComplete="current-password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    className={FIELD}
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
                  className={BTN}
                >
                  {loading ? (
                    <><RefreshCw size={15} className="animate-spin" /> {slowLogin ? "Starting up the server, this may take a moment…" : "Signing in…"}</>
                  ) : (
                    "Sign in"
                  )}
                </button>
              </form>

              <p className="mt-10 pt-5 border-t border-gray-300 text-[11px] uppercase tracking-wider font-[400] text-secondary-foreground/75">
                Verified facilities only.
              </p>

              <p className="mt-5 text-[13px] font-[500] text-muted-foreground">
                New facility?{" "}
                <a href="/register" className={`text-role-accent-text font-semibold hover:underline hover:underline-offset-2 rounded-sm ${FOCUS}`}>
                  Register here
                </a>
              </p>

              <p className="mt-3 text-[13px] font-[500] text-muted-foreground">
                <a href="/privacy" className={`hover:underline hover:underline-offset-2 rounded-sm ${FOCUS}`}>
                  Privacy Notice
                </a>
              </p>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function ForcePasswordChangeForm({
  pendingReset,
  onCancel,
  onSuccess,
}: {
  pendingReset: PendingReset;
  onCancel: () => void;
  onSuccess: (user: SessionUser) => void;
}) {
  const [newPassword, setNewPassword] = useState("");
  const uid = useId();
  const [confirmPassword, setConfirmPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (newPassword.length < 8) {
      setError("Password must be at least 8 characters");
      return;
    }
    if (newPassword !== confirmPassword) {
      setError("Passwords don't match");
      return;
    }
    setLoading(true);
    try {
      const user = await apiChangePassword(pendingReset.resetToken, newPassword);
      onSuccess(user);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to set new password");
    } finally {
      setLoading(false);
    }
  }

  return (
    <>
      <h1 className="font-display text-2xl font-bold text-foreground mb-1">Set a new password</h1>
      <p className="text-muted-foreground text-sm mb-1">
        {pendingReset.facilityName} — {pendingReset.email}
      </p>
      <p className="text-muted-foreground text-sm mb-6">
        This account was created with a temporary password. Choose a new one to continue.
      </p>

      <form onSubmit={handleSubmit} className="space-y-4">
        <div>
          <label htmlFor={`${uid}-new`} className="text-[14px] font-semibold text-foreground block mb-1.5">New password</label>
          <input
            id={`${uid}-new`}
            type="password"
            required
            autoComplete="new-password"
            value={newPassword}
            onChange={(e) => setNewPassword(e.target.value)}
            className={FIELD}
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
            className={FIELD}
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
          className={BTN}
        >
          {loading ? (
            <><RefreshCw size={15} className="animate-spin" /> Setting password…</>
          ) : (
            "Set password and continue"
          )}
        </button>
        <button
          type="button"
          onClick={onCancel}
          className={`w-full ${LINK_BTN}`}
        >
          Back to sign in
        </button>
      </form>
    </>
  );
}

