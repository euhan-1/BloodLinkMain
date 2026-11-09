import { useEffect, useState } from "react";
import { useMap } from "react-leaflet";

// For a MapContainer with scrollWheelZoom={false}: a wheel over the map scrolls the page instead of trapping it, and this
// component decides when the wheel may zoom the map. Leaflet already makes the container focusable (arrow keys pan, + and -
// zoom) and its pins and zoom buttons tab stops, so keyboard use is unchanged in both modes. The hint is not shown on touch
// screens, which have no wheel. Shared by the sourcing map and the location picker's map, which differ in one way:
//
//   "click" (default): the wheel switches on when the map is clicked or focused, and off again when the pointer leaves or focus
//     moves out. For a map where a click has no side effect (the sourcing map). The hint shows while the wheel is off.
//   "modifier": the wheel zooms only while Ctrl or Cmd is held, and scrolls the page otherwise. For a map where a click does
//     something (the picker's click drops the pin), so a click cannot be the opt-in. The hint is always shown.
export function MapScrollZoomOptIn({ mode = "click" }: { mode?: "click" | "modifier" }) {
  const map = useMap();
  const [on, setOn] = useState(false);
  useEffect(() => {
    const el = map.getContainer();
    if (mode === "modifier") {
      // Leaflet's wheel zoom stays on, and a wheel event without Ctrl or Cmd is stopped on the way in (capture phase, before
      // Leaflet's own listener sees it) so the browser scrolls the page. With the modifier held Leaflet handles it, and stops
      // the browser zooming the whole page.
      map.scrollWheelZoom.enable();
      const guard = (e: WheelEvent) => { if (!(e.ctrlKey || e.metaKey)) e.stopPropagation(); };
      el.addEventListener("wheel", guard, { capture: true });
      return () => {
        el.removeEventListener("wheel", guard, { capture: true });
        map.scrollWheelZoom.disable();
      };
    }
    const set = (v: boolean) => {
      if (v) map.scrollWheelZoom.enable();
      else map.scrollWheelZoom.disable();
      setOn(v);
    };
    const enable = () => set(true);
    const disable = () => set(false);
    const focusOut = (e: FocusEvent) => { if (!el.contains(e.relatedTarget as Node | null)) disable(); };
    el.addEventListener("click", enable);
    el.addEventListener("focusin", enable);
    el.addEventListener("mouseleave", disable);
    el.addEventListener("focusout", focusOut);
    return () => {
      el.removeEventListener("click", enable);
      el.removeEventListener("focusin", enable);
      el.removeEventListener("mouseleave", disable);
      el.removeEventListener("focusout", focusOut);
    };
  }, [map, mode]);
  if (mode === "click" && on) return null;
  return (
    <div
      aria-hidden="true"
      className="pointer-events-none pointer-coarse:hidden absolute bottom-2 left-2 z-[1000] rounded-md border border-border bg-card px-2 py-1 text-[12px] text-muted-foreground font-[family-name:var(--font-body)]"
    >
      {mode === "modifier" ? "Hold Ctrl (⌘ on Mac) and scroll to zoom" : "Click the map to zoom with the scroll wheel"}
    </div>
  );
}
