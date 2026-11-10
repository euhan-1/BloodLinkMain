import { useEffect, useRef, type ReactNode, type RefObject } from "react";
import { FOCUSABLE_SELECTOR, tabTarget } from "../lib/dialogFocus";

// The one place dialog behaviour lives. Modal (the centred dialogs) and the two header popovers (account menu, notifications) all
// render through it, so they cannot drift apart. When it mounts it:
//   - moves focus inside: the first control that is not the close button (the first field of a form), else the close button,
//     else the panel itself; an element with data-autofocus wins over both;
//   - keeps Tab and Shift+Tab inside, wrapping at the ends;
//   - closes on Escape (only the topmost one, if two are ever open);
//   - on close, returns focus to whatever opened it (or to `returnFocusRef` when the opener is gone, as the account menu's items
//     are by the time their dialog opens);
//   - is labelled (aria-labelledby) and announced as a dialog (role="dialog" aria-modal="true").
// The close button inside a panel carries data-dialog-close so that "first control" skips it.

const open: object[] = []; // panels currently mounted, the last one on top

const isVisible = (el: HTMLElement) => el.getClientRects().length > 0;

export function DialogPanel({
  labelledBy, onClose, returnFocusRef, focusKey, className, children,
}: {
  labelledBy: string;
  onClose: () => void;
  returnFocusRef?: RefObject<HTMLElement | null>;
  /** When this value changes (a dialog that opens on "Loading…" and shows its form later), and focus is still on the panel or its
   *  close button, move it to the first control that has appeared. */
  focusKey?: unknown;
  className?: string;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  useEffect(() => {
    const panel = ref.current;
    if (!panel) return;
    const token = {};
    open.push(token);
    const active = document.activeElement;
    const opener = active instanceof HTMLElement && active !== document.body ? active : null;
    const items = () => [...panel.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)].filter(isVisible);

    const all = items();
    const first =
      panel.querySelector<HTMLElement>("[data-autofocus]") ??
      all.find((el) => !el.hasAttribute("data-dialog-close")) ??
      all[0] ??
      panel;
    first.focus({ preventScroll: true });

    function onKeyDown(e: KeyboardEvent) {
      if (open[open.length - 1] !== token) return;
      if (e.key === "Escape") {
        e.preventDefault();
        e.stopPropagation();
        onCloseRef.current();
        return;
      }
      if (e.key !== "Tab") return;
      const list = items();
      const target = tabTarget(list.length, list.indexOf(document.activeElement as HTMLElement), e.shiftKey);
      if (target === null) return;
      e.preventDefault();
      (target === "container" ? panel : list[target as number])!.focus();
    }
    document.addEventListener("keydown", onKeyDown, true);

    return () => {
      document.removeEventListener("keydown", onKeyDown, true);
      open.splice(open.indexOf(token), 1);
      const back = returnFocusRef?.current ?? opener;
      if (back && back.isConnected) back.focus({ preventScroll: true });
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const firstRun = useRef(true);
  useEffect(() => {
    if (firstRun.current) { firstRun.current = false; return; }
    const panel = ref.current;
    if (!panel) return;
    const active = document.activeElement;
    if (active !== panel && !(active instanceof HTMLElement && active.hasAttribute("data-dialog-close")) && active !== document.body) return;
    const next = [...panel.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)].filter(isVisible).find((el) => !el.hasAttribute("data-dialog-close"));
    next?.focus({ preventScroll: true });
  }, [focusKey]);

  return (
    <div ref={ref} role="dialog" aria-modal="true" aria-labelledby={labelledBy} tabIndex={-1} className={`${className ?? ""} focus:outline-none`}>
      {children}
    </div>
  );
}
