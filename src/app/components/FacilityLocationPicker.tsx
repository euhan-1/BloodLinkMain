import { useState, useEffect, useId } from "react";
import { MapContainer, TileLayer, Marker, useMap } from "react-leaflet";
import L from "leaflet";
// See the same import in FacilityNetworkMap.tsx — kept in both Leaflet
// consumers (not App.tsx) so leaflet's marker-icon fix and CSS only ship to
// whichever screen actually renders a map.
import "../lib/leafletSetup";
import { Search } from "lucide-react";
import { DEFAULT_MAP_CENTER, DEFAULT_MAP_ZOOM, PIN_PLACED_ZOOM } from "../lib/mapConstants";
import { FOCUS } from "../lib/formStyles";
import { MapScrollZoomOptIn } from "./MapScrollZoomOptIn";

function RecenterMap({ position, zoom }: { position: [number, number]; zoom: number }) {
  const map = useMap();
  useEffect(() => {
    // invalidateSize() first: Leaflet measures its container's pixel size
    // once, at construction, to convert between lat/lng and screen position.
    // This map is mounted inside a modal/form that's still settling layout
    // at that moment (surrounding fields, async-loaded content above it) —
    // without re-measuring, Leaflet keeps using that stale size forever, so
    // setView's "center" ends up computed against the wrong container
    // dimensions and the map visibly renders off from wherever the marker
    // actually is.
    map.invalidateSize();
    map.setView(position, zoom);
  }, [position[0], position[1]]);
  return null;
}

// Mounted unconditionally (not just once a position exists) so the map
// re-measures itself right after its first real paint too — covers the
// position-not-chosen-yet case (a brand new facility with no saved lat/lng),
// where RecenterMap above never even runs.
function MapReadySizer() {
  const map = useMap();
  useEffect(() => {
    const id = requestAnimationFrame(() => map.invalidateSize());
    return () => cancelAnimationFrame(id);
  }, [map]);
  return null;
}

function LocationClickHandler({ onPick }: { onPick: (lat: number, lng: number) => void }) {
  useMap().on("click", (e) => onPick(e.latlng.lat, e.latlng.lng));
  return null;
}

// Default lookup: calls OSM Nominatim directly from the browser. Browser
// fetch can't set a custom User-Agent (Chrome/Firefox silently drop it), so
// this only works for CompleteProfileScreen/EditFacilityProfileModal because
// their callers are already-logged-in facilities behind an existing
// relationship with the service, not a fresh anonymous applicant. Kept as
// the default so both existing callers need zero changes.
async function lookUpAddressViaNominatim(address: string): Promise<[number, number] | null> {
  const res = await fetch(
    `https://nominatim.openstreetmap.org/search?format=json&limit=1&countrycodes=ph&q=${encodeURIComponent(address)}`
  );
  if (!res.ok) throw new Error("Address lookup failed");
  const results = await res.json();
  if (results.length === 0) return null;
  return [parseFloat(results[0].lat), parseFloat(results[0].lon)];
}

// The four parts of splitAddress mode, in Philippine order. All four are required by default, so a new address always joins to
// exactly four parts and splitSavedAddress below reads it back exactly.
const ADDRESS_PARTS = [
  { key: "street", label: "Street / House no.", placeholder: "e.g. 12 Rizal Avenue" },
  { key: "barangay", label: "Barangay", placeholder: "e.g. Poblacion" },
  { key: "city", label: "City or Municipality", placeholder: "e.g. Batangas City" },
  { key: "province", label: "Province", placeholder: "e.g. Batangas" },
] as const;
type AddressPart = (typeof ADDRESS_PARTS)[number]["key"];
export type AddressParts = Record<AddressPart, string>;
export const EMPTY_ADDRESS_PARTS: AddressParts = { street: "", barangay: "", city: "", province: "" };

// The one string the rest of the app sees: parts in order, comma-separated, empty ones skipped.
const joinAddress = (parts: AddressParts) =>
  ADDRESS_PARTS.map(({ key }) => parts[key].trim()).filter(Boolean).join(", ");

// Seeds the four parts from a saved address string, by one rule and no heuristic: split on ", "; exactly four pieces map to
// Street, Barangay, City, Province in order; anything else goes whole into Street with the other three empty, and `fallback`
// says so (so the form can ask the user to split it). An empty saved address is not a fallback: there is nothing to split.
export function splitSavedAddress(saved: string): { parts: AddressParts; fallback: boolean } {
  const pieces = saved.split(", ");
  if (pieces.length === 4) {
    const [street, barangay, city, province] = pieces;
    return { parts: { street, barangay, city, province }, fallback: false };
  }
  return { parts: { ...EMPTY_ADDRESS_PARTS, street: saved }, fallback: saved.trim() !== "" };
}

// Shared by CompleteProfileScreen (first-time onboarding), EditFacilityProfileModal
// (Account menu, after the fact), and RegisterFacility (self-registration) —
// the exact same address/geocode/map/department fields, just
// wrapped in different layouts with different submit handling. All state is
// controlled from the parent so callers can prefill it differently.
//
// lookupAddress defaults to a direct-from-the-browser Nominatim call
// (lookUpAddressViaNominatim); RegisterFacility overrides it with a call to
// GET /facilities/geocode instead, which sets a real identifying User-Agent
// as Nominatim's usage policy asks for — something only a server-side call
// can do.
export function FacilityLocationFields({
  address, onAddressChange,
  position, onPositionChange,
  department, onDepartmentChange,
  lookupAddress = lookUpAddressViaNominatim,
  showDepartment = true,
  addressParts,
  onAddressPartsChange,
  addressNeedsSplitting = false,
  requireAllParts = true,
}: {
  address: string;
  onAddressChange: (v: string) => void;
  position: [number, number] | null;
  onPositionChange: (p: [number, number]) => void;
  department?: string;
  onDepartmentChange?: (v: string) => void;
  lookupAddress?: (address: string) => Promise<[number, number] | null>;
  // false for RegisterFacility: self-registration doesn't collect department
  // (see facility_registration_requests' column list) — an approved facility
  // fills it in later via the same post-login CompleteProfile flow an
  // admin-onboarded facility already goes through.
  showDepartment?: boolean;
  // With addressParts: four fields (street, barangay, city, province) instead of one, still emitted through onAddressChange
  // as a single joined string. The parts are the parent's state (like address and position), so they survive this component
  // remounting. A caller with a saved address string seeds them with splitSavedAddress, the only place a string is split.
  // addressNeedsSplitting: that seed fell back to putting the whole string in Street. The line asking the user to split it
  // shows until any of the other three fields has something in it.
  addressNeedsSplitting?: boolean;
  // false: only Street is required. For the edit modal, where an address that predates the four fields must not block an
  // unrelated edit (the department). Registration and first-time profile keep all four required.
  requireAllParts?: boolean;
} & (
  | { splitAddress: true; addressParts: AddressParts; onAddressPartsChange: (p: AddressParts) => void }
  | { splitAddress?: false; addressParts?: undefined; onAddressPartsChange?: undefined }
)) {
  const uid = useId();
  const [geocoding, setGeocoding] = useState(false);
  const [geocodeError, setGeocodeError] = useState<string | null>(null);
  // What Look up geocodes: the joined parts, or the single field's value.
  const query = addressParts ? joinAddress(addressParts) : address;

  function changePart(key: AddressPart, value: string) {
    if (!addressParts) return;
    const next = { ...addressParts, [key]: value };
    onAddressPartsChange?.(next);
    onAddressChange(joinAddress(next));
  }

  async function handleLookUpAddress() {
    if (!query.trim()) return;
    setGeocoding(true);
    setGeocodeError(null);
    try {
      const result = await lookupAddress(query);
      if (result === null) {
        setGeocodeError("No match found — try a more specific address, or drop the pin manually on the map.");
        return;
      }
      onPositionChange(result);
    } catch (err) {
      setGeocodeError(err instanceof Error ? err.message : "Address lookup failed");
    } finally {
      setGeocoding(false);
    }
  }

  const lookUpButton = (
    <button
      type="button"
      onClick={handleLookUpAddress}
      disabled={geocoding || !query.trim()}
      className="h-10 px-4 bg-card border border-border rounded-md text-[14px] font-semibold text-foreground hover:bg-secondary transition-colors disabled:opacity-60 flex items-center gap-1.5 shrink-0"
    >
      <Search size={15} /> {geocoding ? "Looking up…" : "Look up"}
    </button>
  );

  return (
    <>
      <div>
        {addressParts ? (
          <div role="group" aria-labelledby={`${uid}-address`}>
            <p id={`${uid}-address`} className="text-[14px] font-semibold text-foreground mb-1.5">Address</p>
            {addressNeedsSplitting && !addressParts.barangay && !addressParts.city && !addressParts.province && (
              <p className="text-[13px] text-muted-foreground mb-2">Your saved address needs splitting into these fields.</p>
            )}
            <div className="grid sm:grid-cols-2 gap-4">
              {ADDRESS_PARTS.map(({ key, label, placeholder }) => (
                <div key={key}>
                  <label htmlFor={`${uid}-${key}`} className="text-[14px] font-semibold text-foreground block mb-1.5">{label}</label>
                  <input
                    id={`${uid}-${key}`}
                    type="text"
                    required={requireAllParts || key === "street"}
                    value={addressParts[key]}
                    onChange={(e) => changePart(key, e.target.value)}
                    placeholder={placeholder}
                    className={`w-full h-10 px-3 text-sm border border-border rounded-md bg-card focus:border-primary transition-colors ${FOCUS}`}
                  />
                </div>
              ))}
            </div>
            <div className="mt-3">{lookUpButton}</div>
          </div>
        ) : (
          <>
            <label htmlFor={`${uid}-address`} className="text-[14px] font-semibold text-foreground block mb-1.5">Address</label>
            <div className="flex gap-2">
              <input
                id={`${uid}-address`}
                type="text"
                required
                value={address}
                onChange={(e) => onAddressChange(e.target.value)}
                placeholder="Street, barangay, city"
                className={`flex-1 h-10 px-3 text-sm border border-border rounded-md bg-card focus:border-primary transition-colors ${FOCUS}`}
              />
              {lookUpButton}
            </div>
          </>
        )}
        {geocodeError && <p role="alert" className="text-[13px] text-status-critical-text mt-1.5">{geocodeError}</p>}
      </div>

      <div>
        <label className="text-[14px] font-semibold text-foreground block mb-1.5">
          Confirm location on map
        </label>
        <p className="text-[13px] text-muted-foreground mb-2">
          Look up your address above, or click anywhere on the map to place the pin. Drag it to fine-tune.
        </p>
        <div className="h-72 rounded-lg overflow-hidden border border-border">
          <MapContainer center={position ?? DEFAULT_MAP_CENTER} zoom={position ? PIN_PLACED_ZOOM : DEFAULT_MAP_ZOOM} style={{ height: "100%", width: "100%" }} scrollWheelZoom={false}>
            <MapReadySizer />
            <MapScrollZoomOptIn mode="modifier" />
            <TileLayer
              attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
              url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
            />
            <LocationClickHandler onPick={(lat, lng) => onPositionChange([lat, lng])} />
            {position && (
              <>
                <RecenterMap position={position} zoom={PIN_PLACED_ZOOM} />
                <Marker
                  position={position}
                  draggable
                  eventHandlers={{
                    dragend: (e) => {
                      const marker = e.target as L.Marker;
                      const { lat, lng } = marker.getLatLng();
                      onPositionChange([lat, lng]);
                    },
                  }}
                />
              </>
            )}
          </MapContainer>
        </div>
      </div>

      {showDepartment && (
        <div>
          <label htmlFor={`${uid}-department`} className="text-[14px] font-semibold text-foreground block mb-1.5">Department / Branch</label>
          <input
            id={`${uid}-department`}
            type="text"
            required
            value={department}
            onChange={(e) => onDepartmentChange?.(e.target.value)}
            placeholder="e.g. Blood Services Unit"
            className={`w-full h-10 px-3 text-sm border border-border rounded-md bg-card focus:border-primary transition-colors ${FOCUS}`}
          />
        </div>
      )}
    </>
  );
}

