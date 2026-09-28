import { useState } from "react";
import { Building2, CheckCircle, FlaskConical, RefreshCw } from "lucide-react";
import { registerFacility, geocodeAddress, verifyFacilityRegistrationEmail, type RegisterFacilityBody } from "../lib/api";
import { BloodDropLogo } from "../components/BloodTypeBadge";
import { FacilityLocationFields } from "../components/FacilityLocationPicker";

// ─── Facility self-registration ────────────────────────────────────────────
// Reachable with no session, same as ResetPasswordScreen — App.tsx renders
// this off window.location.pathname before checking currentUser.
//
// This submits a facility_registration_requests row, never a live account —
// an administrator reviews it once the email step below is done (see
// server/main.py's register_facility). Verification is a typed 6-digit code,
// not a clicked link: institutional mail scanners at hospital/government
// addresses consume single-use links before the recipient ever sees them.
// The code-entry step itself lands with the verify endpoint (Step 5) — for
// now this screen ends at "check your email" once the code has been sent,
// matching the backend's own response shape.

export function RegisterFacilityScreen() {
  const [facilityName, setFacilityName] = useState("");
  const [facilityType, setFacilityType] = useState<"hospital" | "bloodbank">("hospital");
  const [contactPerson, setContactPerson] = useState("");
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");

  const [address, setAddress] = useState("");
  const [dohLicense, setDohLicense] = useState("");
  const [position, setPosition] = useState<[number, number] | null>(null);

  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitted, setSubmitted] = useState(false);

  const [code, setCode] = useState("");
  const [verifying, setVerifying] = useState(false);
  const [verifyError, setVerifyError] = useState<string | null>(null);
  const [verified, setVerified] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitError(null);
    if (password !== confirmPassword) {
      setSubmitError("Passwords don't match");
      return;
    }
    if (!position) {
      setSubmitError("Confirm this facility's location on the map before continuing");
      return;
    }
    setSubmitting(true);
    try {
      const body: RegisterFacilityBody = {
        facility_name: facilityName,
        facility_type: facilityType,
        address,
        latitude: position[0],
        longitude: position[1],
        doh_license_number: dohLicense,
        contact_person: contactPerson,
        email,
        phone,
        password,
      };
      await registerFacility(body);
      setSubmitted(true);
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : "Failed to submit registration");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleVerifySubmit(e: React.FormEvent) {
    e.preventDefault();
    setVerifyError(null);
    setVerifying(true);
    try {
      await verifyFacilityRegistrationEmail(email, code);
      setVerified(true);
    } catch (err) {
      setVerifyError(err instanceof Error ? err.message : "Failed to verify code");
    } finally {
      setVerifying(false);
    }
  }

  return (
    <div className="min-h-screen bg-background flex items-center justify-center p-6">
      <div className="w-full max-w-3xl bg-card border border-border rounded-xl p-8">
        <div className="flex items-center gap-3 mb-2">
          <BloodDropLogo size={32} />
          <span className="text-lg font-bold">Blood<span className="text-primary">Link</span></span>
        </div>

        {verified ? (
          <>
            <div className="flex items-center gap-2 mb-2">
              <CheckCircle size={22} className="text-status-safe-text" />
              <h2 className="font-display text-2xl font-bold text-foreground">Email verified</h2>
            </div>
            <p className="text-muted-foreground text-sm mb-6">
              An administrator will review your application and confirm your access. You'll be notified once it's approved.
            </p>
            <a
              href="/"
              className="block w-full h-10 leading-10 text-center bg-primary text-white text-sm font-semibold rounded-md hover:bg-primary-hover transition-colors"
            >
              Back to sign in
            </a>
          </>
        ) : submitted ? (
          <>
            <h2 className="font-display text-2xl font-bold text-foreground mb-1">Check your email</h2>
            <p className="text-muted-foreground text-sm mb-6">
              We've sent a 6-digit verification code to <strong className="text-foreground">{email}</strong>. Enter it below to confirm this address.
            </p>

            <form onSubmit={handleVerifySubmit} className="space-y-4">
              <div>
                <label className="text-[14px] font-semibold text-foreground block mb-1.5">Verification code</label>
                <input
                  type="text"
                  inputMode="numeric"
                  pattern="[0-9]{6}"
                  maxLength={6}
                  required
                  autoComplete="one-time-code"
                  value={code}
                  onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
                  placeholder="000000"
                  className="w-full h-10 px-3 text-sm tracking-[0.3em] text-center font-mono border border-border rounded-md bg-card focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary transition-all"
                />
              </div>

              {verifyError && (
                <div className="text-[13px] text-status-critical-text bg-status-critical-tint border border-status-critical-border rounded-md px-3 py-2">
                  {verifyError}
                </div>
              )}

              <button
                type="submit"
                disabled={verifying || code.length !== 6}
                className="w-full h-10 bg-primary text-white text-sm font-semibold rounded-md hover:bg-primary-hover transition-colors disabled:opacity-60 flex items-center justify-center gap-2"
              >
                {verifying ? (
                  <><RefreshCw size={15} className="animate-spin" /> Verifying…</>
                ) : (
                  "Verify email"
                )}
              </button>
              <button
                type="button"
                onClick={() => { setSubmitted(false); setVerifyError(null); setCode(""); }}
                className="w-full text-[13px] text-muted-foreground hover:text-foreground transition-colors"
              >
                Wrong details? Go back and resubmit
              </button>
            </form>
          </>
        ) : (
          <>
            <h2 className="font-display text-2xl font-bold text-foreground mb-1">Register your facility</h2>
            <p className="text-muted-foreground text-sm mb-6">
              Submit your facility's details for review. An administrator verifies your DOH license before approving access.
            </p>

            <form onSubmit={handleSubmit} className="space-y-5">
              <div>
                <label className="text-[14px] font-semibold text-foreground block mb-1.5">Facility type</label>
                <div className="flex gap-1 bg-secondary rounded-lg p-1 w-fit">
                  {(["hospital", "bloodbank"] as const).map((type) => (
                    <button
                      key={type}
                      type="button"
                      onClick={() => setFacilityType(type)}
                      className={`flex items-center justify-center gap-1.5 px-4 py-2 text-[14px] font-semibold rounded border transition-all duration-200 ease-out ${
                        facilityType === type
                          ? "bg-card text-foreground shadow-sm border-border"
                          : "border-transparent text-muted-foreground hover:text-foreground"
                      }`}
                    >
                      {type === "hospital" ? <Building2 size={15} /> : <FlaskConical size={15} />}
                      {type === "hospital" ? "Hospital" : "Blood Bank"}
                    </button>
                  ))}
                </div>
              </div>

              <div>
                <label className="text-[14px] font-semibold text-foreground block mb-1.5">Facility name</label>
                <input
                  type="text" required value={facilityName} onChange={(e) => setFacilityName(e.target.value)}
                  className="w-full h-10 px-3 text-sm border border-border rounded-md bg-card focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary transition-all"
                />
              </div>

              <FacilityLocationFields
                address={address} onAddressChange={setAddress}
                position={position} onPositionChange={setPosition}
                dohLicense={dohLicense} onDohLicenseChange={setDohLicense}
                lookupAddress={geocodeAddress}
                showDepartment={false}
              />

              <div className="grid sm:grid-cols-2 gap-4">
                <div>
                  <label className="text-[14px] font-semibold text-foreground block mb-1.5">Contact person</label>
                  <input
                    type="text" required value={contactPerson} onChange={(e) => setContactPerson(e.target.value)}
                    className="w-full h-10 px-3 text-sm border border-border rounded-md bg-card focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary transition-all"
                  />
                </div>
                <div>
                  <label className="text-[14px] font-semibold text-foreground block mb-1.5">Phone</label>
                  <input
                    type="tel" required value={phone} onChange={(e) => setPhone(e.target.value)}
                    placeholder="09XXXXXXXXX"
                    className="w-full h-10 px-3 text-sm border border-border rounded-md bg-card focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary transition-all"
                  />
                </div>
              </div>

              <div>
                <label className="text-[14px] font-semibold text-foreground block mb-1.5">Work email</label>
                <input
                  type="email" required autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)}
                  className="w-full h-10 px-3 text-sm border border-border rounded-md bg-card focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary transition-all"
                />
              </div>

              <div className="grid sm:grid-cols-2 gap-4">
                <div>
                  <label className="text-[14px] font-semibold text-foreground block mb-1.5">Password</label>
                  <input
                    type="password" required autoComplete="new-password" value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    className="w-full h-10 px-3 text-sm border border-border rounded-md bg-card focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary transition-all"
                  />
                  <p className="text-[12px] text-muted-foreground mt-1.5">
                    At least 10 characters, including a letter and a number.
                  </p>
                </div>
                <div>
                  <label className="text-[14px] font-semibold text-foreground block mb-1.5">Confirm password</label>
                  <input
                    type="password" required autoComplete="new-password" value={confirmPassword}
                    onChange={(e) => setConfirmPassword(e.target.value)}
                    className="w-full h-10 px-3 text-sm border border-border rounded-md bg-card focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary transition-all"
                  />
                </div>
              </div>

              {submitError && (
                <div className="text-[13px] text-status-critical-text bg-status-critical-tint border border-status-critical-border rounded-md px-3 py-2">
                  {submitError}
                </div>
              )}

              <button
                type="submit"
                disabled={submitting}
                className="w-full h-10 bg-primary text-white text-sm font-semibold rounded-md hover:bg-primary-hover transition-colors disabled:opacity-60 flex items-center justify-center gap-2"
              >
                {submitting ? (
                  <><RefreshCw size={15} className="animate-spin" /> Submitting…</>
                ) : (
                  "Submit for review"
                )}
              </button>
              <a href="/" className="block text-center text-[13px] text-muted-foreground hover:text-foreground transition-colors">
                Back to sign in
              </a>
            </form>
          </>
        )}
      </div>
    </div>
  );
}
