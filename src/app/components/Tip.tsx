import { cloneElement, isValidElement, useId, useState, type ReactElement, type ReactNode } from "react";

// A tooltip that works without a mouse. It replaces a native `title` attribute where the title is what explains something: a title
// shows on mouse hover only, never on keyboard focus and not at all on touch.
//   - Opens on hover, on keyboard focus, and on tap (a tap focuses it); Escape closes it; the bubble is inside the wrapper, so moving the
//     pointer onto it keeps it open.
//   - The text is always in the page for screen readers: when closed it is visually hidden (sr-only), and the element it describes
//     points at it with aria-describedby, so nothing depends on the bubble being open.
//   - `focusable`: the wrapper itself becomes a tab stop (for plain text that has no control of its own). Without it the child is
//     expected to be a control (a button) that is already focusable; it gets the aria-describedby.
// The bubble reuses the app's popover look (card background, border, small text), so it adds no new visual language.
export function Tip({
  text, children, focusable = false, align = "start", className = "inline-block",
}: {
  text: string;
  children: ReactNode;
  focusable?: boolean;
  align?: "start" | "end";
  /** The wrapper's display (and any text styling the wrapped text had): "inline-block" by default, "block" where the old element was a div. */
  className?: string;
}) {
  const id = useId();
  const [shown, setShown] = useState(false);
  const child =
    !focusable && isValidElement(children)
      ? cloneElement(children as ReactElement<{ "aria-describedby"?: string }>, { "aria-describedby": id })
      : children;
  return (
    <span
      className={`relative ${className}`}
      {...(focusable ? { tabIndex: 0, "aria-describedby": id } : {})}
      onMouseEnter={() => setShown(true)}
      onMouseLeave={() => setShown(false)}
      onFocus={() => setShown(true)}
      onBlur={() => setShown(false)}
      onClick={() => setShown(true)}
      onKeyDown={(e) => {
        if (e.key === "Escape" && shown) { e.stopPropagation(); setShown(false); }
      }}
    >
      {child}
      <span
        role="tooltip"
        id={id}
        className={
          shown
            ? `absolute ${align === "end" ? "right-0" : "left-0"} top-full z-40 mt-1 w-max max-w-[18rem] rounded-md border border-border bg-card px-2.5 py-1.5 text-left text-[12px] font-normal normal-case leading-snug tracking-normal text-foreground shadow-md`
            : "sr-only"
        }
      >
        {text}
      </span>
    </span>
  );
}
