import { useEffect, useState } from "react";
import { Building2, CheckCircle, FlaskConical, RefreshCw, ShieldCheck, Zap } from "lucide-react";
import {
  login as apiLogin, changePassword as apiChangePassword, requestPasswordResetOtp, resetPasswordOtp, warmBackend,
} from "../lib/api";
import { type SessionUser } from "../lib/session";
import { BloodDropLogo } from "../components/BloodTypeBadge";

// ─── Login Screen ─────────────────────────────────────────────────────────────

// Permanent demo accounts (seeded server-side) so the two dashboard
// experiences — forecast for blood banks, threshold/action for hospitals,
// see Section 2.7 of the status report — are one click away to try, without
// needing to know real facility credentials.
const DEMO_ACCOUNTS = {
  hospital: { email: "demo.hospital@example.com", password: "BloodLinkDemo123!", label: "Hospital" },
  bloodbank: { email: "demo.bloodbank@example.com", password: "BloodLinkDemo123!", label: "Blood Bank" },
  admin: { email: "demo.admin@example.com", password: "BloodLinkDemo123!", label: "Admin" },
} as const;

type DemoAccountType = keyof typeof DEMO_ACCOUNTS;

// Only ever produced by an admin creating/resetting an account (a one-time
// temp password) — the self-service "Forgot password?" flow no longer feeds
// this at all, since that reset now genuinely goes out by email instead of
// handing a token straight back to the requester (see forgotSent below).
type PendingReset = { resetToken: string; email: string; facilityName: string };

export function LoginScreen({ onLogin }: { onLogin: (user: SessionUser) => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedDemo, setSelectedDemo] = useState<DemoAccountType | null>(null);
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

  function handleSelectDemo(type: DemoAccountType) {
    setSelectedDemo(type);
    setEmail(DEMO_ACCOUNTS[type].email);
    setPassword(DEMO_ACCOUNTS[type].password);
    setError(null);
  }

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
      <div className="hidden lg:flex flex-col justify-between w-[480px] bg-primary text-white p-12 animate-panel-slide-left">
        <div>
          <div className="flex items-center gap-3 mb-16">
            <BloodDropLogo size={36} />
            <span className="text-xl font-bold tracking-tight">BloodLink</span>
          </div>
          <h1 className="text-4xl font-bold leading-tight mb-6">
            AI-assisted blood supply coordination for modern healthcare
          </h1>
          <p className="text-white/70 text-[16px] leading-relaxed">
            Real-time inventory management, emergency sourcing and response, and donor coordination — unified for blood banks and hospitals across the network.
          </p>
        </div>

        <div className="space-y-4">
          {[
            { icon: <ShieldCheck size={16} />, text: "Verified healthcare facilities only" },
            { icon: <Zap size={16} />, text: "AI-assisted forecasting and shortage alerts" },
            { icon: <RefreshCw size={16} />, text: "Donor outreach and coordination tools" },
          ].map((item, i) => (
            <div
              key={i}
              className="flex items-center gap-3 text-white/80 text-sm animate-bullet-rise-in"
              style={{ animationDelay: `${360 + i * 90}ms` }}
            >
              <div className="w-7 h-7 rounded bg-white/15 flex items-center justify-center shrink-0">
                {item.icon}
              </div>
              {item.text}
            </div>
          ))}
        </div>
      </div>

      {/* Right panel */}
      <div className="flex-1 flex items-center justify-center p-8">
        <div className="w-full max-w-[400px] animate-panel-slide-right">
          <div className="flex items-center gap-2 mb-2 lg:hidden">
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
                  <h2 className="font-display text-2xl font-bold text-foreground">Password updated</h2>
                </div>
                <p className="text-muted-foreground text-sm mb-6">
                  You can now log in with your new password.
                </p>
                <button
                  type="button"
                  onClick={() => {
                    setForgotMode(false); setForgotSent(false); setResetDone(false); setForgotEmail("");
                    setResetCode(""); setResetNewPassword(""); setResetConfirmPassword("");
                  }}
                  className="w-full h-10 bg-primary text-white text-sm font-semibold rounded-md hover:bg-primary-hover transition-colors"
                >
                  Back to sign in
                </button>
              </>
            ) : forgotSent ? (
              <>
                <h2 className="font-display text-2xl font-bold text-foreground mb-1">Enter your reset code</h2>
                <p className="text-muted-foreground text-sm mb-6">
                  If an account exists for <strong className="text-foreground">{forgotEmail}</strong>, we've sent a
                  6-digit code to it. Enter it below along with your new password. The code expires in 10 minutes.
                </p>

                <form onSubmit={handleResetOtpSubmit} className="space-y-4">
                  <div>
                    <label className="text-[14px] font-semibold text-foreground block mb-1.5">Reset code</label>
                    <input
                      type="text"
                      inputMode="numeric"
                      pattern="[0-9]{6}"
                      maxLength={6}
                      required
                      autoComplete="one-time-code"
                      value={resetCode}
                      onChange={(e) => setResetCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
                      placeholder="000000"
                      className="w-full h-10 px-3 text-sm tracking-[0.3em] text-center font-mono border border-border rounded-md bg-card focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary transition-all"
                    />
                  </div>
                  <div>
                    <label className="text-[14px] font-semibold text-foreground block mb-1.5">New password</label>
                    <input
                      type="password"
                      required
                      autoComplete="new-password"
                      value={resetNewPassword}
                      onChange={(e) => setResetNewPassword(e.target.value)}
                      className="w-full h-10 px-3 text-sm border border-border rounded-md bg-card focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary transition-all"
                    />
                  </div>
                  <div>
                    <label className="text-[14px] font-semibold text-foreground block mb-1.5">Confirm new password</label>
                    <input
                      type="password"
                      required
                      autoComplete="new-password"
                      value={resetConfirmPassword}
                      onChange={(e) => setResetConfirmPassword(e.target.value)}
                      className="w-full h-10 px-3 text-sm border border-border rounded-md bg-card focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary transition-all"
                    />
                  </div>

                  {resetError && (
                    <div className="text-[13px] text-status-critical-text bg-status-critical-tint border border-status-critical-border rounded-md px-3 py-2">
                      {resetError}
                    </div>
                  )}

                  <button
                    type="submit"
                    disabled={resetLoading || resetCode.length !== 6}
                    className="w-full h-10 bg-primary text-white text-sm font-semibold rounded-md hover:bg-primary-hover transition-colors disabled:opacity-60 flex items-center justify-center gap-2"
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
                    className="w-full text-[13px] text-muted-foreground hover:text-foreground transition-colors"
                  >
                    Didn't get a code? Go back
                  </button>
                </form>
              </>
            ) : (
              <>
                <h2 className="font-display text-2xl font-bold text-foreground mb-1">Reset your password</h2>
                <p className="text-muted-foreground text-sm mb-6">
                  Enter the email address on your facility's account and we'll send you a code to reset your password.
                </p>

                <form onSubmit={handleForgotSubmit} className="space-y-4">
                  <div>
                    <label className="text-[14px] font-semibold text-foreground block mb-1.5">
                      Email address
                    </label>
                    <input
                      type="email"
                      required
                      autoComplete="email"
                      value={forgotEmail}
                      onChange={(e) => setForgotEmail(e.target.value)}
                      className="w-full h-10 px-3 text-sm border border-border rounded-md bg-card focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary transition-all"
                    />
                  </div>

                  {forgotError && (
                    <div className="text-[13px] text-status-critical-text bg-status-critical-tint border border-status-critical-border rounded-md px-3 py-2">
                      {forgotError}
                    </div>
                  )}

                  <button
                    type="submit"
                    disabled={forgotLoading}
                    className="w-full h-10 bg-primary text-white text-sm font-semibold rounded-md hover:bg-primary-hover transition-colors disabled:opacity-60 flex items-center justify-center gap-2"
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
                    className="w-full text-[13px] text-muted-foreground hover:text-foreground transition-colors"
                  >
                    Back to sign in
                  </button>
                </form>
              </>
            )
          ) : (
            <>
              <h2 className="font-display text-2xl font-bold text-foreground mb-1">Sign in</h2>
              <p className="text-muted-foreground text-sm mb-4">
                Access your facility's blood management dashboard.
              </p>

              <div className="mb-5">
                <p className="text-[13px] font-semibold text-muted-foreground mb-1.5">Quick demo login</p>
                <div className="flex gap-1 bg-secondary rounded-lg p-1">
                  {(Object.keys(DEMO_ACCOUNTS) as DemoAccountType[]).map((type) => (
                    <button
                      key={type}
                      type="button"
                      onClick={() => handleSelectDemo(type)}
                      className={`flex-1 flex items-center justify-center gap-1.5 px-3 py-2 text-[14px] font-semibold rounded border transition-all duration-200 ease-out ${
                        selectedDemo === type
                          ? "bg-card text-foreground shadow-sm border-border scale-105"
                          : "border-transparent text-muted-foreground hover:text-foreground"
                      }`}
                    >
                      {type === "hospital" ? <Building2 size={15} /> : type === "bloodbank" ? <FlaskConical size={15} /> : <ShieldCheck size={15} />}
                      {DEMO_ACCOUNTS[type].label}
                    </button>
                  ))}
                </div>
              </div>

              <form onSubmit={handleSubmit} className="space-y-4">
                <div>
                  <label className="text-[14px] font-semibold text-foreground block mb-1.5">
                    Email address
                  </label>
                  <input
                    type="email"
                    required
                    autoComplete="email"
                    value={email}
                    onChange={(e) => { setEmail(e.target.value); setSelectedDemo(null); }}
                    className="w-full h-10 px-3 text-sm border border-border rounded-md bg-card focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary transition-all"
                  />
                </div>
                <div>
                  <div className="flex justify-between mb-1.5">
                    <label className="text-[14px] font-semibold text-foreground">Password</label>
                    <button
                      type="button"
                      onClick={() => { setForgotMode(true); setError(null); setForgotEmail(email); }}
                      className="text-[13px] text-primary hover:underline hover:underline-offset-2"
                    >
                      Forgot password?
                    </button>
                  </div>
                  <input
                    type="password"
                    required
                    autoComplete="current-password"
                    value={password}
                    onChange={(e) => { setPassword(e.target.value); setSelectedDemo(null); }}
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
                  className="w-full h-10 bg-primary text-white text-sm font-semibold rounded-md hover:bg-primary-hover transition-colors disabled:opacity-60 flex items-center justify-center gap-2"
                >
                  {loading ? (
                    <><RefreshCw size={15} className="animate-spin" /> {slowLogin ? "Starting up the server, this may take a moment…" : "Signing in…"}</>
                  ) : (
                    "Sign in"
                  )}
                </button>
              </form>

              <div className="mt-8 p-4 rounded-lg bg-status-watch-tint border border-status-watch-border flex gap-3">
                <ShieldCheck size={16} className="text-status-watch shrink-0 mt-0.5" />
                <p className="text-[13px] text-status-watch-text leading-relaxed">
                  <strong>Verified facilities only.</strong> New registrations are reviewed by a BloodLink administrator, who checks your DOH license before approving access.
                </p>
              </div>

              <p className="mt-6 text-center text-[13px] text-muted-foreground">
                New facility?{" "}
                <a href="/register" className="text-primary hover:underline hover:underline-offset-2">
                  Register here
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
      <h2 className="font-display text-2xl font-bold text-foreground mb-1">Set a new password</h2>
      <p className="text-muted-foreground text-sm mb-1">
        {pendingReset.facilityName} — {pendingReset.email}
      </p>
      <p className="text-muted-foreground text-sm mb-6">
        This account was created with a temporary password. Choose a new one to continue.
      </p>

      <form onSubmit={handleSubmit} className="space-y-4">
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
          className="w-full h-10 bg-primary text-white text-sm font-semibold rounded-md hover:bg-primary-hover transition-colors disabled:opacity-60 flex items-center justify-center gap-2"
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
          className="w-full text-[13px] text-muted-foreground hover:text-foreground transition-colors"
        >
          Back to sign in
        </button>
      </form>
    </>
  );
}

