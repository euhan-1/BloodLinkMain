import { useState } from "react";
import { CheckCircle, RefreshCw } from "lucide-react";
import { resetPassword } from "../lib/api";
import { BloodDropLogo } from "../components/BloodTypeBadge";

// ─── Reset Password (landed on via the emailed link's ?token=...) ─────────────
// Reachable with no session at all — App.tsx renders this before checking
// currentUser, purely off window.location.pathname, since a person clicking
// the email link is by definition not already logged in on this device.

export function ResetPasswordScreen() {
  const token = new URLSearchParams(window.location.search).get("token") ?? "";

  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

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
      await resetPassword(token, newPassword);
      setDone(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to reset password");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen bg-background flex items-center justify-center px-4">
      <div className="w-full max-w-md bg-card border border-border rounded-xl p-8 shadow-sm">
        <div className="flex items-center gap-2 mb-6">
          <BloodDropLogo size={28} />
          <span className="text-[16px] font-bold tracking-tight text-foreground">
            Blood<span className="text-primary">Link</span>
          </span>
        </div>

        {!token ? (
          <>
            <h2 className="font-display text-2xl font-bold text-foreground mb-2">Invalid reset link</h2>
            <p className="text-muted-foreground text-sm mb-6">
              This link is missing its reset token. Request a new one from the sign-in page.
            </p>
            <a
              href="/"
              className="block w-full h-10 leading-10 text-center bg-primary text-white text-sm font-semibold rounded-md hover:bg-primary-hover transition-colors"
            >
              Back to sign in
            </a>
          </>
        ) : done ? (
          <>
            <div className="flex items-center gap-2 mb-2">
              <CheckCircle size={22} className="text-status-safe-text" />
              <h2 className="font-display text-2xl font-bold text-foreground">Password updated</h2>
            </div>
            <p className="text-muted-foreground text-sm mb-6">
              You can now log in with your new password.
            </p>
            <a
              href="/"
              className="block w-full h-10 leading-10 text-center bg-primary text-white text-sm font-semibold rounded-md hover:bg-primary-hover transition-colors"
            >
              Back to sign in
            </a>
          </>
        ) : (
          <>
            <h2 className="font-display text-2xl font-bold text-foreground mb-1">Set a new password</h2>
            <p className="text-muted-foreground text-sm mb-6">
              Choose a new password for your account.
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
                  <><RefreshCw size={15} className="animate-spin" /> Updating…</>
                ) : (
                  "Update Password"
                )}
              </button>
            </form>
          </>
        )}
      </div>
    </div>
  );
}
