import { useId, useState } from "react";
import { Building2, CheckCircle, FlaskConical, RefreshCw } from "lucide-react";
import { registerFacility, geocodeAddress, verifyFacilityRegistrationEmail, type RegisterFacilityBody } from "../lib/api";
import { BloodDropLogo } from "../components/BloodTypeBadge";
import { FacilityLocationFields, EMPTY_ADDRESS_PARTS, type AddressParts } from "../components/FacilityLocationPicker";
import { FOCUS } from "../lib/formStyles";
import { CONSENT_LABEL, CONTACT_PERSON_NOTICE, PRIVACY_NOTICE_VERSION } from "../lib/privacyNotice";

// The same field and focus treatment as the sign-in screen (see lib/formStyles.ts).
const FIELD = `w-full h-10 px-3 text-sm border border-border rounded-md bg-card focus:border-primary transition-colors ${FOCUS}`;

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
  const uid = useId(); // prefixes every field id, so none can collide with another form's
  const [facilityName, setFacilityName] = useState("");
  const [facilityType, setFacilityType] = useState<"hospital" | "bloodbank">("hospital");
  const [contactPerson, setContactPerson] = useState("");
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  // Never ticked for the person: the box starts empty, the browser blocks submit until it is ticked, and the server checks again.
  const [consented, setConsented] = useState(false);

  const [address, setAddress] = useState("");
  // The four parts the picker shows; `address` is their joined string. Held here so they survive the form remounting.
  const [addressParts, setAddressParts] = useState<AddressParts>(EMPTY_ADDRESS_PARTS);
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
    if (!consented) {
      setSubmitError("Please read the Privacy Notice and tick the consent box to register.");
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
        contact_person: contactPerson,
        email,
        phone,
        password,
        privacy_consent: consented,
        privacy_notice_version: PRIVACY_NOTICE_VERSION,
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
              <h1 className="font-display text-2xl font-bold text-foreground">Email verified</h1>
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
            <h1 className="font-display text-2xl font-bold text-foreground mb-1">Check your email</h1>
            <p className="text-muted-foreground text-sm mb-6">
              We've sent a 6-digit verification code to <strong className="text-foreground">{email}</strong>. Enter it below to confirm this address.
            </p>

            <form onSubmit={handleVerifySubmit} className="space-y-4">
              <div>
                <label htmlFor={`${uid}-code`} className="text-[14px] font-semibold text-foreground block mb-1.5">Verification code</label>
                <input
                  id={`${uid}-code`}
                  type="text"
                  inputMode="numeric"
                  pattern="[0-9]{6}"
                  maxLength={6}
                  required
                  autoComplete="one-time-code"
                  value={code}
                  onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
                  placeholder="000000"
                  className={`${FIELD} tracking-[0.3em] text-center font-mono`}
                />
              </div>

              {verifyError && (
                <div role="alert" className="text-[13px] text-status-critical-text bg-status-critical-tint border border-status-critical-border rounded-md px-3 py-2">
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
            <h1 className="font-display text-2xl font-bold text-foreground mb-1">Register your facility</h1>
            <p className="text-muted-foreground text-sm mb-6">
              Submit your facility's details for review. An administrator reviews every registration before approving access.
            </p>

            <form onSubmit={handleSubmit} className="space-y-5">
              <div>
                <label id={`${uid}-type`} className="text-[14px] font-semibold text-foreground block mb-1.5">Facility type</label>
                <div role="group" aria-labelledby={`${uid}-type`} className="flex gap-1 bg-secondary rounded-lg p-1 w-fit">
                  {(["hospital", "bloodbank"] as const).map((type) => (
                    <button
                      key={type}
                      type="button"
                      aria-pressed={facilityType === type}
                      onClick={() => setFacilityType(type)}
                      className={`flex items-center justify-center gap-1.5 px-4 py-2 text-[14px] font-semibold rounded border transition-all duration-200 ease-out ${
                        facilityType === type
                          ? "bg-card text-foreground shadow-sm border-border"
                          : "border-transparent text-muted-foreground hover:text-foreground"
                      }`}
                    >
                      {type === "hospital" ? <Building2 size={15} aria-hidden="true" /> : <FlaskConical size={15} aria-hidden="true" />}
                      {type === "hospital" ? "Hospital" : "Blood Bank"}
                    </button>
                  ))}
                </div>
              </div>

              <div>
                <label htmlFor={`${uid}-name`} className="text-[14px] font-semibold text-foreground block mb-1.5">Facility name</label>
                <input
                  id={`${uid}-name`}
                  type="text" required value={facilityName} onChange={(e) => setFacilityName(e.target.value)}
                  className={FIELD}
                />
              </div>

              <FacilityLocationFields
                address={address} onAddressChange={setAddress}
                position={position} onPositionChange={setPosition}
                lookupAddress={geocodeAddress}
                showDepartment={false}
                splitAddress
                addressParts={addressParts} onAddressPartsChange={setAddressParts}
              />

              <div>
                <div className="grid sm:grid-cols-2 gap-4">
                  <div>
                    <label htmlFor={`${uid}-contact`} className="text-[14px] font-semibold text-foreground block mb-1.5">Contact person</label>
                    <input
                      id={`${uid}-contact`}
                      type="text" required value={contactPerson} onChange={(e) => setContactPerson(e.target.value)}
                      className={FIELD}
                    />
                  </div>
                  <div>
                    <label htmlFor={`${uid}-phone`} className="text-[14px] font-semibold text-foreground block mb-1.5">Phone</label>
                    <input
                      id={`${uid}-phone`}
                      type="tel" required value={phone} onChange={(e) => setPhone(e.target.value)}
                      placeholder="09XXXXXXXXX"
                      className={FIELD}
                    />
                  </div>
                </div>
                <p className="text-[12px] text-muted-foreground mt-1.5">
                  {CONTACT_PERSON_NOTICE.before}
                  <a href="/privacy" target="_blank" rel="noopener" className={`text-role-accent-text font-semibold underline underline-offset-2 rounded-sm ${FOCUS}`}>{CONTACT_PERSON_NOTICE.link}</a>
                  {CONTACT_PERSON_NOTICE.after}
                </p>
              </div>

              <div>
                <label htmlFor={`${uid}-email`} className="text-[14px] font-semibold text-foreground block mb-1.5">Work email</label>
                <input
                  id={`${uid}-email`}
                  type="email" required autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)}
                  placeholder="name@facility.com"
                  aria-describedby={`${uid}-email-hint`}
                  className={FIELD}
                />
                <p id={`${uid}-email-hint`} className="text-[12px] text-muted-foreground mt-1.5">
                  This address is used to sign in, to receive your verification code, and for notices about your registration.
                </p>
              </div>

              <div className="grid sm:grid-cols-2 gap-4">
                <div>
                  <label htmlFor={`${uid}-password`} className="text-[14px] font-semibold text-foreground block mb-1.5">Password</label>
                  <input
                    id={`${uid}-password`}
                    type="password" required autoComplete="new-password" value={password}
                    aria-describedby={`${uid}-password-hint`}
                    onChange={(e) => setPassword(e.target.value)}
                    className={FIELD}
                  />
                  <p id={`${uid}-password-hint`} className="text-[12px] text-muted-foreground mt-1.5">
                    At least 10 characters, with at least one letter and one digit. Very common passwords are not accepted.
                  </p>
                </div>
                <div>
                  <label htmlFor={`${uid}-confirm`} className="text-[14px] font-semibold text-foreground block mb-1.5">Confirm password</label>
                  <input
                    id={`${uid}-confirm`}
                    type="password" required autoComplete="new-password" value={confirmPassword}
                    onChange={(e) => setConfirmPassword(e.target.value)}
                    className={FIELD}
                  />
                </div>
              </div>

              <div className="flex items-start gap-3">
                <input
                  id={`${uid}-consent`}
                  type="checkbox" required checked={consented} onChange={(e) => setConsented(e.target.checked)}
                  className={`mt-0.5 h-4 w-4 shrink-0 accent-primary ${FOCUS}`}
                />
                <label htmlFor={`${uid}-consent`} className="text-[14px] text-foreground leading-snug">
                  {CONSENT_LABEL.before}
                  <a href="/privacy" target="_blank" rel="noopener" className={`text-role-accent-text font-semibold underline underline-offset-2 rounded-sm ${FOCUS}`}>{CONSENT_LABEL.link}</a>
                  {CONSENT_LABEL.after}
                </label>
              </div>

              {submitError && (
                <div role="alert" className="text-[13px] text-status-critical-text bg-status-critical-tint border border-status-critical-border rounded-md px-3 py-2">
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
