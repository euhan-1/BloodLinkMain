import { createPortal } from "react-dom";
import { X } from "lucide-react";

export function Modal({
  title, onClose, children, wide = false,
}: { title: string; onClose: () => void; children: React.ReactNode; wide?: boolean }) {
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
      <div
        className={`w-full ${wide ? "max-w-2xl" : "max-w-md"} bg-card rounded-xl border border-border shadow-xl max-h-[90vh] overflow-y-auto`}
      >
        <div className="flex items-center justify-between px-5 py-4 border-b border-border">
          <h3 className="font-display font-bold text-[16px] text-foreground">{title}</h3>
          <button
            onClick={onClose}
            className="w-7 h-7 flex items-center justify-center rounded-lg hover:bg-secondary text-muted-foreground transition-colors"
          >
            <X size={16} />
          </button>
        </div>
        <div className="p-5">{children}</div>
      </div>
    </div>,
    document.body
  );
}
