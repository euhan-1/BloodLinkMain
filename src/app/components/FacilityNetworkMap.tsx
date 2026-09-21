import { useEffect } from "react";
import { MapContainer, TileLayer, Marker, Tooltip as LeafletTooltip, useMap } from "react-leaflet";
import L from "leaflet";
import { DEFAULT_MAP_CENTER } from "../lib/mapConstants";

// Leaflet measures its container's pixel size once, at construction, to
// convert between lat/lng and screen position — including the initial
// bounds-fit this map does on mount. This map sits inside a card in a
// flex/grid layout that can still be settling its own height at that exact
// moment, so without re-measuring after the first real paint, the bounds
// fit (and therefore every marker position) can end up computed against a
// stale container size. Same fix as FacilityLocationPicker's map.
function MapReadySizer() {
  const map = useMap();
  useEffect(() => {
    const id = requestAnimationFrame(() => map.invalidateSize());
    return () => cancelAnimationFrame(id);
  }, [map]);
  return null;
}

export type CompatibleAlternative = { blood_type: string; usable_units: number; label: string };

export type Facility = {
  id: number;
  name: string;
  facility_type: string;
  address: string;
  latitude: number;
  longitude: number;
  distance_km: number;
  matching_units: number;
  usable_units: number;
  available: boolean;
  compatible_alternatives: CompatibleAlternative[];
};

// Selected candidate reads as the primary pin (larger, brand red); every
// other ranked candidate stays visible but visually secondary (small, muted)
// — panning across the real map is how you see the whole ranked list at once.
function facilityMarkerIcon(selected: boolean): L.DivIcon {
  const size = selected ? 24 : 13;
  const color = selected ? "#8C1B3A" : "#9CA3AF";
  const border = selected ? 3 : 2;
  return L.divIcon({
    className: "",
    html: `<div style="width:${size}px;height:${size}px;border-radius:50%;background:${color};border:${border}px solid white;box-shadow:0 1px 5px rgba(17,24,39,0.35);"></div>`,
    iconSize: [size, size],
    iconAnchor: [size / 2, size / 2],
  });
}

// Real Leaflet/OpenStreetMap map for Emergency Sourcing — replaces the old
// hand-drawn SVG city grid. `key`'d by search type at the call site so a new
// search (a genuinely different candidate set from GET /facilities/nearby)
// remounts with a fresh bounds fit, rather than trying to imperatively
// re-fit an existing map instance.
export function FacilityNetworkMap({
  banks, selectedBankId, onSelectBank,
}: {
  banks: Facility[];
  selectedBankId: number | null;
  onSelectBank: (id: number) => void;
}) {
  const positions = banks.map((b): [number, number] => [b.latitude, b.longitude]);
  const bounds: [[number, number], [number, number]] =
    positions.length > 0
      ? [
          [Math.min(...positions.map((p) => p[0])), Math.min(...positions.map((p) => p[1]))],
          [Math.max(...positions.map((p) => p[0])), Math.max(...positions.map((p) => p[1]))],
        ]
      : [DEFAULT_MAP_CENTER, DEFAULT_MAP_CENTER];

  // Deliberately no auto-recenter on selection — the whole ranked list stays
  // in view (the initial bounds fit above), and the selected pin just grows
  // and changes color in place. Re-centering on every click would fight the
  // "see the whole network at once" point of showing every candidate.
  return (
    <MapContainer
      bounds={bounds}
      boundsOptions={{ padding: [36, 36] }}
      style={{ height: "100%", width: "100%" }}
      scrollWheelZoom={false}
    >
      <MapReadySizer />
      <TileLayer
        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
        url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
      />
      {banks.map((bank) => (
        <Marker
          key={bank.id}
          position={[bank.latitude, bank.longitude]}
          icon={facilityMarkerIcon(bank.id === selectedBankId)}
          eventHandlers={{ click: () => onSelectBank(bank.id) }}
        >
          <LeafletTooltip direction="top" offset={[0, -6]}>
            <span className="font-semibold">{bank.name}</span> — {bank.distance_km} km
          </LeafletTooltip>
        </Marker>
      ))}
    </MapContainer>
  );
}
