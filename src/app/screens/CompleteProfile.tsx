import { useState } from "react";
import { RefreshCw } from "lucide-react";
import { completeFacilityProfile } from "../lib/api";
import { type SessionUser } from "../lib/session";
import { BloodDropLogo } from "../components/BloodTypeBadge";
import { FacilityLocationFields } from "../components/FacilityLocationPicker";

export function CompleteProfileScreen({ user, onComplete }: { user: SessionUser; onComplete: () => void }) {
  const [address, setAddress] = useState("");
  const [department, setDepartment] = useState("");
  const [dohLicense, setDohLicense] = useState("");
  const [position, setPosition] = useState<[number, number] | null>(null);

  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitError(null);
    if (!position) {
      setSubmitError("Confirm this facility's location on the map before continuing");
      return;
    }
    setSubmitting(true);
    try {
      await completeFacilityProfile({
        address,
        latitude: position[0],
        longitude: position[1],
        department,
        doh_license_number: dohLicense,
      });
      onComplete();
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : "Failed to save profile");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="min-h-screen bg-background flex items-center justify-center p-6">
      <div className="w-full max-w-3xl bg-card border border-border rounded-xl p-8">
        <div className="flex items-center gap-3 mb-2">
          <BloodDropLogo size={32} />
          <span className="text-lg font-bold">Blood<span className="text-primary">Link</span></span>
        </div>
        <h2 className="font-display text-2xl font-bold text-foreground mb-1">Complete your facility profile</h2>
        <p className="text-muted-foreground text-sm mb-6">
          {user.facility_name} — one last step before you can access the dashboard.
        </p>

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
            className="w-full h-10 bg-primary text-white text-sm font-semibold rounded-md hover:bg-primary-hover transition-colors disabled:opacity-60 flex items-center justify-center gap-2"
          >
            {submitting ? (
              <><RefreshCw size={15} className="animate-spin" /> Saving…</>
            ) : (
              "Save and continue"
            )}
          </button>
        </form>
      </div>
    </div>
  );
}
