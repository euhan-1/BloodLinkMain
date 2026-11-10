import { useId, type RefObject } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";
import { DialogPanel } from "./DialogPanel";

// Every centred dialog in the app. Focus handling, Escape, the Tab trap, returning focus and the dialog role and label all come from
// DialogPanel, so no dialog repeats them. `returnFocusRef`: where focus goes on close when the element that opened the dialog no
// longer exists (the account menu's items are gone by then). `focusKey`: change it when the dialog's first control appears late.
export function Modal({
  title, onClose, children, wide = false, returnFocusRef, focusKey,
}: { title: string; onClose: () => void; children: React.ReactNode; wide?: boolean; returnFocusRef?: RefObject<HTMLElement | null>; focusKey?: unknown }) {
  const titleId = useId();
  // Portaled to document.body rather than rendered in place: this is always
  // opened from inside <header> (AccountMenu's dropdown), and that header
  // carries an entrance animation with animation-fill-mode: both — which
  // means its computed transform stays a non-"none" matrix forever after
  // the animation ends, not just while it's playing. Per the CSS spec, any
  // ancestor with a transform other than none becomes the containing block
  // for position:fixed descendants, so without the portal this modal's
  // "fixed inset-0" backdrop resolved to the header's own 56px-tall box
  // instead of the viewport — centering the whole modal a few pixels from
  // the top of the page with most of its height cut off above the fold.
  // Escaping to body sidesteps this regardless of what animations any
  // future ancestor ends up with.
  return createPortal(
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/40 px-4">
      <DialogPanel
        labelledBy={titleId} onClose={onClose} returnFocusRef={returnFocusRef} focusKey={focusKey}
        className={`w-full ${wide ? "max-w-2xl" : "max-w-md"} bg-card rounded-xl border border-border shadow-xl max-h-[90vh] overflow-y-auto`}
      >
        <div className="flex items-center justify-between px-5 py-4 border-b border-border">
          <h2 id={titleId} className="font-display font-bold text-[16px] text-foreground">{title}</h2>
          <button
            type="button"
            onClick={onClose}
            aria-label={`Close ${title}`}
            data-dialog-close
            className="w-7 h-7 flex items-center justify-center rounded-lg hover:bg-secondary text-muted-foreground transition-colors"
          >
            <X size={16} aria-hidden="true" />
          </button>
        </div>
        <div className="p-5">{children}</div>
      </DialogPanel>
    </div>,
    document.body
  );
}
