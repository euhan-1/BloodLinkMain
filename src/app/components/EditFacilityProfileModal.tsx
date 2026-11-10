import { useEffect, useState, type RefObject } from "react";
import { CheckCircle, RefreshCw } from "lucide-react";
import { completeFacilityProfile, getMyFacilityProfile } from "../lib/api";
import { Modal } from "./Modal";
import { FacilityLocationFields, EMPTY_ADDRESS_PARTS, splitSavedAddress, type AddressParts } from "./FacilityLocationPicker";

// Its own file so AccountMenu (which every signed-in screen, and the app's first chunk, includes) can load this lazily: the form
// embeds the map picker, and the map libraries (Leaflet) should only be fetched when someone opens it.
export function EditFacilityProfileModal({ facilityName, onClose, onSaved, returnFocusRef }: {
  facilityName: string | null;
  onClose: () => void;
  onSaved: () => void;
  returnFocusRef?: RefObject<HTMLElement | null>;
}) {
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [address, setAddress] = useState("");
  const [addressParts, setAddressParts] = useState<AddressParts>(EMPTY_ADDRESS_PARTS);
  const [addressNeedsSplitting, setAddressNeedsSplitting] = useState(false);
  const [department, setDepartment] = useState("");
  const [position, setPosition] = useState<[number, number] | null>(null);

  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  useEffect(() => {
    getMyFacilityProfile()
      .then((profile) => {
        // `address` stays the saved string until a field is edited, so saving without touching it changes nothing.
        const saved = profile.address ?? "";
        const seeded = splitSavedAddress(saved);
        setAddress(saved);
        setAddressParts(seeded.parts);
        setAddressNeedsSplitting(seeded.fallback);
        setDepartment(profile.department ?? "");
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
        department,
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
    <Modal title="Edit facility profile" onClose={onClose} wide returnFocusRef={returnFocusRef} focusKey={loading}>
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
          <p className="text-[14px] text-muted-foreground mb-4">{facilityName}'s address, location, and department are saved.</p>
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
            splitAddress addressParts={addressParts} onAddressPartsChange={setAddressParts}
            addressNeedsSplitting={addressNeedsSplitting} requireAllParts={false}
          />

          {submitError && (
            <div className="text-[13px] text-status-critical-text bg-status-critical-tint border border-status-critical-border rounded-md px-3 py-2">
              {submitError}
            </div>
          )}

          <div className="flex gap-2">
            <button
              type="button"
              onClick={onClose}
              disabled={submitting}
              className="flex-1 h-10 border border-border rounded-md text-sm font-semibold text-foreground hover:bg-secondary transition-colors disabled:opacity-60"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={submitting}
              className="flex-1 h-10 bg-primary text-white text-sm font-bold rounded-md hover:bg-primary-hover transition-colors disabled:opacity-60 flex items-center justify-center gap-2"
            >
              {submitting ? (
                <><RefreshCw size={15} className="animate-spin" /> Saving…</>
              ) : (
                "Save changes"
              )}
            </button>
          </div>
        </form>
      )}
    </Modal>
  );
}
