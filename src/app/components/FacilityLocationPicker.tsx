import { useState, useEffect } from "react";
import { MapContainer, TileLayer, Marker, useMap } from "react-leaflet";
import L from "leaflet";
import { Search } from "lucide-react";
import { DEFAULT_MAP_CENTER, DEFAULT_MAP_ZOOM, PIN_PLACED_ZOOM } from "../lib/mapConstants";

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

// Shared by CompleteProfileScreen (first-time onboarding) and
// EditFacilityProfileModal (Account menu, after the fact) — the exact same
// address/geocode/map/department/DOH-license fields, just wrapped in
// different layouts with different submit handling. All state is
// controlled from the parent so both callers can prefill it differently.
export function FacilityLocationFields({
  address, onAddressChange,
  position, onPositionChange,
  department, onDepartmentChange,
  dohLicense, onDohLicenseChange,
}: {
  address: string;
  onAddressChange: (v: string) => void;
  position: [number, number] | null;
  onPositionChange: (p: [number, number]) => void;
  department: string;
  onDepartmentChange: (v: string) => void;
  dohLicense: string;
  onDohLicenseChange: (v: string) => void;
}) {
  const [geocoding, setGeocoding] = useState(false);
  const [geocodeError, setGeocodeError] = useState<string | null>(null);

  async function handleLookUpAddress() {
    if (!address.trim()) return;
    setGeocoding(true);
    setGeocodeError(null);
    try {
      // OSM Nominatim's public search endpoint — free, no API key, rate-limited
      // to ~1 req/sec which this "look up on click" pattern respects (never
      // fires on keystroke).
      const res = await fetch(
        `https://nominatim.openstreetmap.org/search?format=json&limit=1&q=${encodeURIComponent(address)}`
      );
      if (!res.ok) throw new Error("Address lookup failed");
      const results = await res.json();
      if (results.length === 0) {
        setGeocodeError("No match found — try a more specific address, or drop the pin manually on the map.");
        return;
      }
      onPositionChange([parseFloat(results[0].lat), parseFloat(results[0].lon)]);
    } catch (err) {
      setGeocodeError(err instanceof Error ? err.message : "Address lookup failed");
    } finally {
      setGeocoding(false);
    }
  }

  return (
    <>
      <div>
        <label className="text-[14px] font-semibold text-foreground block mb-1.5">Address</label>
        <div className="flex gap-2">
          <input
            type="text"
            required
            value={address}
            onChange={(e) => onAddressChange(e.target.value)}
            placeholder="Street, barangay, city"
            className="flex-1 h-10 px-3 text-sm border border-border rounded-md bg-card focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary transition-all"
          />
          <button
            type="button"
            onClick={handleLookUpAddress}
            disabled={geocoding || !address.trim()}
            className="h-10 px-4 bg-card border border-border rounded-md text-[14px] font-semibold text-foreground hover:bg-secondary transition-colors disabled:opacity-60 flex items-center gap-1.5 shrink-0"
          >
            <Search size={15} /> {geocoding ? "Looking up…" : "Look up"}
          </button>
        </div>
        {geocodeError && <p className="text-[13px] text-status-critical-text mt-1.5">{geocodeError}</p>}
      </div>

      <div>
        <label className="text-[14px] font-semibold text-foreground block mb-1.5">
          Confirm location on map
        </label>
        <p className="text-[13px] text-muted-foreground mb-2">
          Look up your address above, or click anywhere on the map to place the pin. Drag it to fine-tune.
        </p>
        <div className="h-72 rounded-lg overflow-hidden border border-border">
          <MapContainer center={position ?? DEFAULT_MAP_CENTER} zoom={position ? PIN_PLACED_ZOOM : DEFAULT_MAP_ZOOM} style={{ height: "100%", width: "100%" }}>
            <MapReadySizer />
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
        {position && (
          <p className="text-[12px] text-muted-foreground mt-1.5 font-mono">
            {position[0].toFixed(5)}, {position[1].toFixed(5)}
          </p>
        )}
      </div>

      <div className="grid sm:grid-cols-2 gap-4">
        <div>
          <label className="text-[14px] font-semibold text-foreground block mb-1.5">Department / Branch</label>
          <input
            type="text"
            required
            value={department}
            onChange={(e) => onDepartmentChange(e.target.value)}
            placeholder="e.g. Blood Services Unit"
            className="w-full h-10 px-3 text-sm border border-border rounded-md bg-card focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary transition-all"
          />
        </div>
        <div>
          <label className="text-[14px] font-semibold text-foreground block mb-1.5">DOH license number</label>
          <input
            type="text"
            required
            value={dohLicense}
            onChange={(e) => onDohLicenseChange(e.target.value)}
            placeholder="e.g. DOH-BSU-00123"
            className="w-full h-10 px-3 text-sm border border-border rounded-md bg-card focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary transition-all"
          />
        </div>
      </div>
    </>
  );
}

