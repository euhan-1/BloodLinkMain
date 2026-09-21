import { useState } from "react";
import { Building2, CheckCircle, FlaskConical, RefreshCw, ShieldCheck, Zap } from "lucide-react";
import {
  login as apiLogin, changePassword as apiChangePassword, requestPasswordReset,
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
      await requestPasswordReset(forgotEmail);
      setForgotSent(true);
    } catch (err) {
      setForgotError(err instanceof Error ? err.message : "Failed to request a reset");
    } finally {
      setForgotLoading(false);
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
            forgotSent ? (
              <>
                <div className="flex items-center gap-2 mb-2">
                  <CheckCircle size={22} className="text-status-safe-text" />
                  <h2 className="font-display text-2xl font-bold text-foreground">Check your email</h2>
                </div>
                <p className="text-muted-foreground text-sm mb-6">
                  If an account exists for <strong className="text-foreground">{forgotEmail}</strong>, we've sent a
                  password reset link to it. The link expires in 1 hour and can only be used once.
                </p>
                <button
                  type="button"
                  onClick={() => { setForgotMode(false); setForgotSent(false); setForgotEmail(""); }}
                  className="w-full h-10 bg-primary text-white text-sm font-semibold rounded-md hover:bg-primary-hover transition-colors"
                >
                  Back to sign in
                </button>
              </>
            ) : (
              <>
                <h2 className="font-display text-2xl font-bold text-foreground mb-1">Reset your password</h2>
                <p className="text-muted-foreground text-sm mb-6">
                  Enter the email address on your facility's account and we'll send you a link to reset your password.
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
                      "Send reset link"
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
                    <><RefreshCw size={15} className="animate-spin" /> Signing in…</>
                  ) : (
                    "Sign in"
                  )}
                </button>
              </form>

              <div className="mt-8 p-4 rounded-lg bg-status-watch-tint border border-status-watch-border flex gap-3">
                <ShieldCheck size={16} className="text-status-watch shrink-0 mt-0.5" />
                <p className="text-[13px] text-status-watch-text leading-relaxed">
                  <strong>Admin-provisioned accounts.</strong> New facilities are onboarded by a BloodLink administrator, who issues a temporary password for first sign-in — there's no self-service registration.
                </p>
              </div>

              <p className="mt-6 text-center text-[13px] text-muted-foreground">
                Need access? Contact your BloodLink administrator.
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

