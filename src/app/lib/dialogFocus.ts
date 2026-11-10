// The pure part of dialog focus handling (components/DialogPanel.tsx is the part that touches the page). Kept apart so
// scripts/check_accessibility.ts can test it under plain Node.

/** What Tab can land on inside a dialog. Visibility is checked by the caller (a hidden element still matches). */
export const FOCUSABLE_SELECTOR = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled]):not([type='hidden'])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

/**
 * Where Tab (or Shift+Tab) should send focus so it never leaves the dialog.
 *  - number: focus that item (wrapping from the last forward, or from the first backward, or in from outside the list);
 *  - "container": nothing inside can take focus, so keep it on the dialog itself;
 *  - null: the browser's own move already stays inside, so do nothing.
 * `index` is the position of the focused element among the focusable items, or -1 if it is not one of them (focus is on the
 * dialog itself, or has somehow left it).
 */
export function tabTarget(count: number, index: number, shift: boolean): number | "container" | null {
  if (count === 0) return "container";
  if (index === -1) return shift ? count - 1 : 0;
  if (!shift && index === count - 1) return 0;
  if (shift && index === 0) return count - 1;
  return null;
}
