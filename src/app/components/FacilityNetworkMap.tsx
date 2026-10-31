import { useEffect } from "react";
import { MapContainer, TileLayer, Marker, Tooltip as LeafletTooltip, useMap, useMapEvents } from "react-leaflet";
import L from "leaflet";
// Side-effect import (marker-icon path fix) — belongs here, not App.tsx, so
// leaflet's ~176KB (JS + CSS) only loads when a screen that actually renders
// a map is reached, instead of on every page load including the login
// screen. See leafletSetup.ts. FacilityLocationPicker.tsx carries the same
// import for the same reason, since it's the other (only other) Leaflet
// consumer and isn't guaranteed to load after this module.
import "../lib/leafletSetup";
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

// A click on the empty map (not on a pin, and not the end of a drag) clears the selection.
function BackgroundClick({ onClear }: { onClear: () => void }) {
  useMapEvents({ click: () => onClear() });
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

// Every pin carries its rank, so a pin and its row in the list are the same thing. Teal = has what you
// need, grey = does not. The selected one is larger and ringed in the role accent.
function rankedMarkerIcon(rank: number, available: boolean, selected: boolean): L.DivIcon {
  const size = selected ? 38 : 30;
  const ring = selected ? "0 0 0 3px var(--role-accent), 0 2px 8px rgba(28,25,23,0.45)" : "0 1px 4px rgba(28,25,23,0.4)";
  return L.divIcon({
    className: "",
    html: `<div style="width:${size}px;height:${size}px;border-radius:50%;background:${available ? "var(--status-safe)" : "var(--gray-500)"};border:2px solid white;box-shadow:${ring};color:white;font:700 ${selected ? 15 : 13}px/1 var(--font-display);display:flex;align-items:center;justify-content:center;">${rank}</div>`,
    iconSize: [size, size],
    iconAnchor: [size / 2, size / 2],
  });
}

// This facility's own location: a small square in the role accent, so it cannot be mistaken for a round candidate pin,
// drawn above every pin so the reference point is never hidden by a candidate near it. Its "You" label is text only
// (see .origin-label in theme.css): a boxed label would cover the pins around it.
const originIcon = L.divIcon({
  className: "",
  html: `<div style="width:16px;height:16px;border-radius:4px;background:var(--role-accent);border:2px solid white;box-shadow:0 1px 4px rgba(28,25,23,0.55);color:white;font:800 12px/12px var(--font-display);text-align:center;">+</div>`,
  iconSize: [16, 16],
  iconAnchor: [8, 8],
});

// Real Leaflet/OpenStreetMap map for Emergency Sourcing — replaces the old
// hand-drawn SVG city grid. `key`'d by search type at the call site so a new
// search (a genuinely different candidate set from GET /facilities/nearby)
// remounts with a fresh bounds fit, rather than trying to imperatively
// re-fit an existing map instance.
//
// Numbered, availability-coloured pins, the facility's own location (`origin`) and background-click-to-clear. One look for
// both roles: Source supply (blood bank) and Find blood supply (hospital) are the same screen.
export function FacilityNetworkMap({
  banks, selectedBankId, onSelectBank, origin = null, onClearSelection,
}: {
  banks: Facility[];
  selectedBankId: number | null;
  onSelectBank: (id: number) => void;
  origin?: [number, number] | null;
  onClearSelection?: () => void;
}) {
  // Everything that should be in view: every candidate, plus this facility itself.
  const positions = [
    ...banks.map((b): [number, number] => [b.latitude, b.longitude]),
    ...(origin ? [origin] : []),
  ];
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
      boundsOptions={{ padding: [44, 44] }}
      style={{ height: "100%", width: "100%" }}
      scrollWheelZoom={false}
    >
      <MapReadySizer />
      {onClearSelection && <BackgroundClick onClear={onClearSelection} />}
      <TileLayer
        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
        url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
      />
      {origin && (
        <Marker position={origin} icon={originIcon} interactive={false} keyboard={false} zIndexOffset={2000}>
          <LeafletTooltip permanent direction="bottom" offset={[0, 9]} className="origin-label">You</LeafletTooltip>
        </Marker>
      )}
      {banks.map((bank, i) => (
        <Marker
          key={bank.id}
          position={[bank.latitude, bank.longitude]}
          icon={rankedMarkerIcon(i + 1, bank.available, bank.id === selectedBankId)}
          zIndexOffset={bank.id === selectedBankId ? 1000 : 0}
          title={`${i + 1}. ${bank.name}`}
          eventHandlers={{ click: () => onSelectBank(bank.id) }}
        >
          <LeafletTooltip direction="top" offset={[0, -14]}>
            <span className="font-semibold">{bank.name}</span>
            <br />
            {bank.distance_km} km, {bank.available ? "available" : "unavailable"}
          </LeafletTooltip>
        </Marker>
      ))}
    </MapContainer>
  );
}
