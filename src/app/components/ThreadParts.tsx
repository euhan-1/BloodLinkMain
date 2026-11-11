import { useId, useState, type RefObject } from "react";
import { Ban, CheckCircle, ChevronDown, Package, PackageCheck, Send, X, XCircle, type LucideIcon } from "lucide-react";
import { BloodTypeBadge } from "./BloodTypeBadge";
import { ReasonTag } from "./ReasonTag";
import { RequestActionButtons, StatusPill } from "./RequestStatusParts";
import { UnitsTable, type UnitColumn } from "./TransferUnits";
import { formatTime } from "../lib/dateFormat";
import { eventAccountText, type TimelineEvent, type TimelineKind } from "../lib/requestTimeline";
import { rowActions, rowFromDetail, type RowAction } from "../lib/requestInbox";
import { cardWhatText, hasReceipt, isFinished, type DayRow, type DividerRow, type FinishedRow, type LiveCardRow, type MessageRow, type RequestCard, type TrailRow } from "../lib/conversations";

// The pieces of a conversation thread (screens/Messages.tsx lays them out). Every row is aligned by who did it: mine on the right, the
// other facility's on the left, and every one carries a visible label ("You" or the facility's name, and the time) so alignment is
// never the only cue. A message is a bubble; a request is ONE live compact card, at its latest status event, and its earlier events are
// one-line trails; none is ever full width or centred. Colour: my bubbles are the role's accent with white text (maroon for a bank, navy for
// a hospital), the other facility's are neutral; every bubble, card and trail has a border, so a forced-colors (high-contrast) user still sees
// where each one begins. The clay look (theme.css, "message bubbles") is selectors on the marker classes msg-bubble, msg-bubble-own and
// msg-bubble-other, so with it off they look as written here.

const ICONS: Record<TimelineKind, LucideIcon> = { sent: Send, accepted: CheckCircle, released: Package, received: PackageCheck, declined: XCircle, cancelled: Ban };
const RELEASED_COLUMNS: UnitColumn[] = ["din", "type", "component", "volume", "expires", "daysRelease", "origin"];
const RECEIVED_COLUMNS: UnitColumn[] = ["din", "type", "component", "expires", "daysReceipt", "outcome"];

// "You · 10:05" / "Riverside General Hospital · 10:05": the name is bold in the foreground colour in both cases (an accent-coloured label
// was under 3:1 on a dark card), the time and anything else is muted.
function Label({ text, at, side, extra }: { text: string; at: string | null; side: "me" | "them"; extra?: string }) {
  return (
    <div className={`flex flex-wrap items-baseline gap-x-1.5 text-[12px] text-muted-foreground ${side === "me" ? "justify-end" : ""}`}>
      <span className="font-bold text-foreground">{text}</span>
      <span aria-hidden="true">·</span>
      {at ? <time dateTime={at}>{formatTime(at)}</time> : <span>time not recorded</span>}
      {extra && <><span aria-hidden="true">·</span><span>{extra}</span></>}
    </div>
  );
}

function EventIcon({ kind }: { kind: TimelineKind }) {
  const Icon = ICONS[kind];
  return <Icon size={16} aria-hidden="true" className="mt-0.5 shrink-0" />;
}

const lane = (side: "me" | "them") => `flex ${side === "me" ? "justify-end" : "justify-start"}`;
const stack = (side: "me" | "them", width: string) => `flex flex-col gap-1 min-w-0 ${width} ${side === "me" ? "items-end" : "items-start"}`;

export function DaySeparator({ row }: { row: DayRow }) {
  return (
    <div role="separator" aria-label={row.label} className="flex items-center gap-3 text-[12px] font-semibold text-gray-600">
      <span aria-hidden="true" className="flex-1 border-t border-gray-300" />
      <span aria-hidden="true">{row.label}</span>
      <span aria-hidden="true" className="flex-1 border-t border-gray-300" />
    </div>
  );
}

export function NewMessagesDivider({ row }: { row: DividerRow }) {
  return (
    <div data-new-messages="" className="flex items-center gap-3 text-[12px] font-bold text-status-watch-text">
      <span aria-hidden="true" className="flex-1 border-t border-status-watch-border" />
      <span>{row.count} new {row.count === 1 ? "message" : "messages"}</span>
      <span aria-hidden="true" className="flex-1 border-t border-status-watch-border" />
    </div>
  );
}

export function MessageBubble({ row }: { row: MessageRow }) {
  const mine = row.side === "me";
  return (
    <div className={lane(row.side)}>
      <div className={stack(row.side, "max-w-[82%]")}>
        <Label text={row.label} at={row.at} side={row.side} extra={row.requestId !== null ? `about request #${row.requestId}` : undefined} />
        <div
          className={`msg-bubble border px-3.5 py-2.5 text-[14px] leading-relaxed whitespace-pre-wrap [overflow-wrap:anywhere] ${
            mine ? "msg-bubble-own bg-primary text-white border-primary rounded-[18px_18px_6px_18px]" : "msg-bubble-other bg-gray-200 text-foreground border-border rounded-[18px_18px_18px_6px]"
          }`}
        >
          {row.text}
        </div>
      </div>
    </div>
  );
}

// "Units released (9)" / "Units and outcome (9)": the ledger behind a release or receipt event, from the same shared table the receipt
// prints. The account that recorded the event is in here too (never on the card or the line itself): the transfer record needs it, the
// thread does not.
function EventDetails({ label, eventKind, event, card }: { label: string | null; eventKind: TimelineKind; event: TimelineEvent; card: RequestCard }) {
  const id = useId();
  const [open, setOpen] = useState(false);
  if (!label) return null;
  return (
    <div className="mt-1.5">
      <button
        type="button" onClick={() => setOpen((v) => !v)} aria-expanded={open} aria-controls={id}
        className="inline-flex items-center gap-1 text-[13px] font-semibold text-foreground underline underline-offset-2 hover:no-underline"
      >
        {label}
        <ChevronDown size={14} aria-hidden="true" className={`transition-transform ${open ? "rotate-180" : ""}`} />
      </button>
      {open && (
        <div id={id} className="mt-1.5 max-w-full overflow-x-auto">
          <p className="mb-1.5 text-[12px] text-muted-foreground">Recorded by {eventAccountText(event)}</p>
          <div className="min-w-[30rem]"><UnitsTable units={card.units} columns={eventKind === "released" ? RELEASED_COLUMNS : RECEIVED_COLUMNS} /></div>
        </div>
      )}
    </div>
  );
}

// An EARLIER status event of a request: one muted line where it happened, on the side of the facility that did it, no buttons (only the
// request's live card has any, so nothing can disagree). "Details" opens what the event was, and its units if it moved some.
export function TrailRowView({ row }: { row: TrailRow }) {
  const id = useId();
  const [open, setOpen] = useState(false);
  return (
    <div className={lane(row.side)}>
      <div className={stack(row.side, "max-w-[82%]")}>
        <Label text={row.label} at={row.at} side={row.side} />
        <div className="thread-trail min-w-0 max-w-full border border-border rounded-lg px-3 py-1.5 text-[12.5px] leading-snug text-muted-foreground">
          <div className="flex flex-wrap items-center gap-x-2.5 gap-y-0.5">
            <span>{row.line}</span>
            <button
              type="button" onClick={() => setOpen((v) => !v)} aria-expanded={open} aria-controls={id}
              className="inline-flex items-center gap-1 text-[12.5px] font-semibold text-foreground underline underline-offset-2 hover:no-underline"
            >
              Details<span className="sr-only"> of request #{row.requestId}, {row.event.kind}</span>
              <ChevronDown size={13} aria-hidden="true" className={`transition-transform ${open ? "rotate-180" : ""}`} />
            </button>
          </div>
          {open && (
            <div id={id} className="mt-1.5 text-[13px] text-foreground">
              <p><span className="sr-only">Status event: </span>{row.sentence}</p>
              <EventDetails label={row.detailsLabel} eventKind={row.eventKind} event={row.event} card={row.card} />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

const linkClass = "text-[13px] font-semibold text-foreground underline underline-offset-2 hover:no-underline rounded-sm";

// THE live card of a request, compact, at the request's latest status event and on the side of the facility that did it (lib/conversations.ts,
// LiveCardRow). It says what that event was (the sentence, with its units behind a toggle), where the request stands, and has the buttons
// rowActions gives this facility (the same list the nav badge counts, so they cannot disagree): one button per step, Accept and Decline the only
// pair. An action's error is shown here, and afterwards focus goes to the status text (`req-status-<id>`), which is where the result is said.
export function RequestCardView({
  row, me, busy, error, onAction, onMessageAbout, onCollapse,
}: {
  row: LiveCardRow;
  me: number | null;
  busy: boolean;
  error: string | null;
  onAction: (card: RequestCard, action: RowAction) => void;
  onMessageAbout: (requestId: number) => void;
  onCollapse?: (requestId: number) => void; // a finished request that was expanded can be folded back to its line
}) {
  const { card } = row;
  const req = card.request;
  const inbox = rowFromDetail(req, me);
  const finished = isFinished(req.status);
  return (
    <div className={lane(row.side)}>
      <div className={stack(row.side, "w-full max-w-[420px]")}>
        <Label text={row.label} at={row.at} side={row.side} />
        <article
          id={`req-card-${req.id}`} tabIndex={-1} aria-label={`Request #${req.id}`}
          className="bg-card border border-border rounded-xl w-full p-3.5 flex flex-col gap-2.5 text-left outline-none"
        >
          <div className="flex items-start justify-between gap-2">
            <h3 className="text-[15px] leading-snug font-bold text-foreground">Request #{req.id}</h3>
            <ReasonTag reason={req.emergency_type} />
          </div>
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[14px] text-foreground">
            <BloodTypeBadge type={req.blood_type} size="sm" />
            <span>{cardWhatText(card)}</span>
          </div>
          {row.sentence && (
            <div className="text-[13.5px] leading-snug text-foreground">
              <p className="flex items-start gap-2">
                <EventIcon kind={row.event.kind} />
                <span><span className="sr-only">Status event: </span>{row.sentence}</span>
              </p>
              <EventDetails label={row.detailsLabel} eventKind={row.event.kind} event={row.event} card={card} />
            </div>
          )}
          <div>
            <span id={`req-status-${req.id}`} tabIndex={-1} className="inline-block rounded-full outline-none focus-visible:outline-2 focus-visible:outline-offset-2">
              <StatusPill row={inbox} />
            </span>
          </div>
          {rowActions(inbox).length > 0 && <RequestActionButtons row={inbox} busy={busy} onAction={(a) => onAction(card, a)} size="md" />}
          {error && <p role="alert" className="text-[13px] text-status-critical-text">{error}</p>}
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
            <button type="button" onClick={() => onMessageAbout(req.id)} className={linkClass}>Message about this<span className="sr-only"> request #{req.id}</span></button>
            {hasReceipt(req) && (
              <a href={`/receipt/${req.id}`} target="_blank" rel="noopener" className={linkClass} aria-label={`Receipt for request ${req.id}`}>Receipt</a>
            )}
            {finished && onCollapse && (
              <button type="button" onClick={() => onCollapse(req.id)} aria-expanded="true" aria-controls={`req-card-${req.id}`} className={linkClass}>Hide history</button>
            )}
          </div>
        </article>
      </div>
    </div>
  );
}

// A finished request: one line, expanding in place (its own events and its card appear where this line is).
export function FinishedSummaryRow({ row, onExpand }: { row: FinishedRow; onExpand: (requestId: number) => void }) {
  const req = row.card.request;
  return (
    <div className={lane(row.side)}>
      <div className={stack(row.side, "w-full max-w-[420px]")}>
        <Label text={row.label} at={row.at} side={row.side} />
        <div className="bg-card border border-border rounded-xl w-full px-3.5 py-2.5 flex items-center justify-between gap-3 text-[13.5px] text-foreground">
          <span className="min-w-0">
            <b className="font-bold">Request #{req.id}</b> · {cardWhatText(row.card)} · <span id={`req-status-${req.id}`} tabIndex={-1} className="outline-none">{row.summary}</span>
          </span>
          <button
            type="button" id={`req-summary-${req.id}`} onClick={() => onExpand(req.id)} aria-expanded="false" aria-controls={`req-card-${req.id}`}
            className={`${linkClass} shrink-0`}
          >
            Show history<span className="sr-only"> of request #{req.id}</span>
          </button>
        </div>
      </div>
    </div>
  );
}

// ─── The composer ───────────────────────────────────────────────────────────

export function MessageComposer({
  facilityName, value, onChange, onSend, sending, enabled, hint, error, aboutRequestId, onClearAbout, inputRef,
}: {
  facilityName: string;
  value: string;
  onChange: (v: string) => void;
  onSend: () => void;
  sending: boolean;
  enabled: boolean;
  hint: string | null;
  error: string | null;
  aboutRequestId: number | null;
  onClearAbout: () => void;
  inputRef: RefObject<HTMLInputElement>;
}) {
  const uid = useId();
  const noteId = `${uid}-note`;
  return (
    <form
      onSubmit={(e) => { e.preventDefault(); onSend(); }}
      className="flex-none border-t border-border px-4 pt-2.5 pb-3 flex flex-col gap-2"
    >
      {aboutRequestId !== null && (
        <div className="flex">
          <span className="inline-flex items-center gap-1 h-7 pl-2.5 pr-1 rounded-full border border-border bg-primary-tint text-[12.5px] font-semibold text-foreground whitespace-nowrap">
            About: request #{aboutRequestId}
            <button
              type="button" onClick={onClearAbout} aria-label={`Remove request #${aboutRequestId} from this message`}
              className="w-5 h-5 inline-flex items-center justify-center rounded-full text-foreground hover:bg-secondary"
            >
              <X size={13} aria-hidden="true" />
            </button>
          </span>
        </div>
      )}
      <div className="flex gap-2">
        <label htmlFor={`${uid}-input`} className="sr-only">Message {facilityName}</label>
        <input
          id={`${uid}-input`} ref={inputRef} type="text" value={value} maxLength={2000} autoComplete="off"
          onChange={(e) => onChange(e.target.value)}
          disabled={!enabled}
          placeholder={enabled ? `Message ${facilityName}` : "Messaging is not available yet"}
          aria-describedby={error || hint ? noteId : undefined}
          aria-invalid={error ? true : undefined}
          className="flex-1 min-w-0 h-11 px-4 text-[15px] border border-border rounded-full bg-input-background placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary disabled:opacity-60"
        />
        <button
          type="submit" aria-label="Send message" disabled={!enabled || sending || value.trim() === ""}
          className="h-11 w-11 shrink-0 bg-primary text-white rounded-full flex items-center justify-center hover:bg-primary-hover transition-colors disabled:opacity-50"
        >
          <Send size={17} aria-hidden="true" />
        </button>
      </div>
      <div id={noteId}>
        {error && <p role="alert" className="text-[13px] text-status-critical-text">{error}</p>}
        {!error && hint && <p className="text-[12.5px] text-muted-foreground">{hint}</p>}
      </div>
    </form>
  );
}
