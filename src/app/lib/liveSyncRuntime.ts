// The app's one LiveSync, wired to the real network and the real page. TopNav starts it while a facility account is signed in and stops
// it when the nav goes away (sign-out). Screens import `liveSync` and subscribe to the sections they show.
import { SESSION_EXPIRED_EVENT, syncPoll } from "./api";
import { LiveSync } from "./liveSync";

export const liveSync = new LiveSync({
  poll: syncPoll,
  isVisible: () => document.visibilityState === "visible",
  setTimer: (fn, ms) => window.setTimeout(fn, ms),
  clearTimer: (id) => window.clearTimeout(id as number),
});

const onPageChange = () => liveSync.visibilityChanged();
const onSessionExpired = () => liveSync.stop();

export function startLiveSync(): void {
  liveSync.start();
  document.addEventListener("visibilitychange", onPageChange);
  window.addEventListener("focus", onPageChange);
  window.addEventListener(SESSION_EXPIRED_EVENT, onSessionExpired);
}

export function stopLiveSync(): void {
  document.removeEventListener("visibilitychange", onPageChange);
  window.removeEventListener("focus", onPageChange);
  window.removeEventListener(SESSION_EXPIRED_EVENT, onSessionExpired);
  liveSync.stop();
}
