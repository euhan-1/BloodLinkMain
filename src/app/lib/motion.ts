import { useEffect, useRef, useState } from "react";
import { flushSync } from "react-dom";

// Wraps a state update that swaps the whole screen (login <-> dashboard) in
// the native View Transitions API, so the hand-off is an actual cross-screen
// animation (see ::view-transition-old/new(root) in theme.css) instead of an
// instant unmount/mount. flushSync forces the DOM update to happen
// synchronously inside the callback, which the API requires in order to
// capture an "after" snapshot on the very next frame. Falls back to a plain
// update wherever the API isn't available (Safari, older browsers) — the
// screen still swaps, just without the transition.
export function withViewTransition(update: () => void): void {
  const doc = document as Document & { startViewTransition?: (callback: () => void) => void };
  if (typeof doc.startViewTransition === "function") {
    doc.startViewTransition(() => flushSync(update));
  } else {
    update();
  }
}

// Briefly returns true when `value` changes after the component's first
// render — used to trigger a short highlight/fade on a piece of UI whose
// underlying data just changed (e.g. a status badge flipping). Deliberately
// suppressed on mount: the first value isn't a "change," it's just the
// initial paint, and flashing every card on load would be noise, not signal.
export function useFlashOnChange<T>(value: T, durationMs = 280): boolean {
  const [flashing, setFlashing] = useState(false);
  const prevRef = useRef(value);
  const mountedRef = useRef(false);

  useEffect(() => {
    if (!mountedRef.current) {
      mountedRef.current = true;
      prevRef.current = value;
      return;
    }
    if (prevRef.current === value) return;
    prevRef.current = value;
    setFlashing(true);
    const t = setTimeout(() => setFlashing(false), durationMs);
    return () => clearTimeout(t);
  }, [value, durationMs]);

  return flashing;
}
