import type { ReactNode } from "react";
import { Ban, CheckCircle, Package, PackageCheck, Send, XCircle, type LucideIcon } from "lucide-react";
import { formatTime } from "../lib/dateFormat";
import {
  describeEvent, eventAccountText, eventTimeText, withDaySeparators, type DayMarker, type RequestDetail, type ThreadItem, type TimelineEvent,
  type TimelineKind,
} from "../lib/requestTimeline";

type Message = { id: number; sender_facility_id: number; sender_facility_name: string; message: string; created_at: string };

const ICONS: Record<TimelineKind, LucideIcon> = {
  sent: Send, accepted: CheckCircle, released: Package, received: PackageCheck, declined: XCircle, cancelled: Ban,
};

// A status event is the record, not conversation, so it does not look like a message: always full width, square-cornered,
// with a neutral fill and an icon, where messages are rounded bubbles aligned to a side. It is never aligned left or right to
// say who acted. That is said inside the event instead: the facility's name leads the sentence in bold, "(you)" follows it
// when it is this facility, and a bar on the left edge is the facility's accent colour for its own events and grey for the
// other's, the same own/other colouring the bubbles' sender names use. When, and which of its accounts, is on the second line,
// with "not recorded" where the request never captured it. `action` is a step this event calls for (confirm release on
// the accept event, confirm receipt on the release event), shown only while that step is the one waiting.
function EventRow({
  event, request, own, action,
}: { event: TimelineEvent; request: RequestDetail["request"]; own: boolean; action?: ReactNode }) {
  const Icon = ICONS[event.kind];
  const who = event.facility_name ?? "A facility";
  const sentence = describeEvent(event, request);
  return (
    <div className={`flex gap-3 rounded-md border border-border border-l-[3px] bg-secondary px-3 py-2.5 ${own ? "border-l-primary" : "border-l-gray-400"}`}>
      <Icon size={16} aria-hidden="true" className="mt-0.5 shrink-0 text-foreground" />
      <div className="min-w-0">
        <p className="text-[14px] leading-snug text-foreground">
          <span className="sr-only">Status event: </span>
          {sentence.startsWith(who) ? (
            <>
              <strong className="font-bold">{who}</strong>{own ? " (you)" : ""}{sentence.slice(who.length)}
            </>
          ) : sentence}
        </p>
        <p className="mt-0.5 flex flex-wrap gap-x-3 text-[12px] text-gray-600">
          <time dateTime={event.at ?? undefined}>{eventTimeText(event)}</time>
          <span>{eventAccountText(event)}</span>
        </p>
        {action}
      </div>
    </div>
  );
}

function MessageBubble({ msg, own }: { msg: Message; own: boolean }) {
  const time = formatTime(msg.created_at);
  return (
    <div className={`flex flex-col ${own ? "items-end" : "items-start"}`}>
      <div className={`text-[12px] font-semibold mb-1 ${own ? "text-primary" : "text-muted-foreground"}`}>
        {msg.sender_facility_name} · {time}
      </div>
      <div
        className={`max-w-[80%] px-4 py-3 rounded-2xl text-[14px] leading-relaxed ${
          own ? "bg-primary text-white rounded-br-md" : "bg-gray-200 text-foreground rounded-bl-md"
        }`}
      >
        {msg.message}
      </div>
    </div>
  );
}

// "31 Oct 2026" between runs of bubbles on different days: a hairline either side, the date in the middle.
function DaySeparator({ label }: { label: string }) {
  return (
    <div role="separator" aria-label={label} className="flex items-center gap-3 text-[12px] font-semibold text-gray-600">
      <span aria-hidden="true" className="flex-1 border-t border-gray-300" />
      <span aria-hidden="true">{label}</span>
      <span aria-hidden="true" className="flex-1 border-t border-gray-300" />
    </div>
  );
}

// The coordination chat and the request's history as one thread, in time order.
export function RequestThread({
  items, request, actingFacilityId, renderAction,
}: {
  items: ThreadItem<Message>[];
  request: RequestDetail["request"];
  actingFacilityId: number | null;
  renderAction?: (event: TimelineEvent) => ReactNode;
}) {
  return (
    <>
      {withDaySeparators(items).map((item) =>
        item.type === "day" ? (
          <DaySeparator key={(item as DayMarker).key} label={(item as DayMarker).label} />
        ) : item.type === "event" ? (
          <EventRow key={item.key} event={item.event} request={request} own={item.event.facility_id === actingFacilityId} action={renderAction?.(item.event)} />
        ) : (
          <MessageBubble key={item.key} msg={item.message} own={item.message.sender_facility_id === actingFacilityId} />
        ),
      )}
    </>
  );
}
