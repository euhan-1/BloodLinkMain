import { Ban, CheckCircle, Package, PackageCheck, Send, XCircle, type LucideIcon } from "lucide-react";
import {
  describeEvent, eventAccountText, eventTimeText, type RequestDetail, type ThreadItem, type TimelineEvent, type TimelineKind,
} from "../lib/requestTimeline";

type Message = { id: number; sender_facility_id: number; sender_facility_name: string; message: string; created_at: string };

const ICONS: Record<TimelineKind, LucideIcon> = {
  sent: Send, accepted: CheckCircle, released: Package, received: PackageCheck, declined: XCircle, cancelled: Ban,
};

// A status event is the record, not conversation, so it does not look like a message: full width and square-cornered with
// a neutral fill and an icon, where messages are rounded bubbles aligned to a side. Which facility acted, when, and which
// of its accounts is on its second line, with "not recorded" where the request never captured it.
function EventRow({ event, request }: { event: TimelineEvent; request: RequestDetail["request"] }) {
  const Icon = ICONS[event.kind];
  return (
    <div className="flex gap-3 rounded-md border border-border bg-secondary px-3 py-2.5">
      <Icon size={16} aria-hidden="true" className="mt-0.5 shrink-0 text-foreground" />
      <div className="min-w-0">
        <p className="text-[14px] leading-snug text-foreground">
          <span className="sr-only">Status event: </span>
          {describeEvent(event, request)}
        </p>
        <p className="mt-0.5 flex flex-wrap gap-x-3 text-[12px] text-gray-600">
          <time dateTime={event.at ?? undefined}>{eventTimeText(event)}</time>
          <span>{eventAccountText(event)}</span>
        </p>
      </div>
    </div>
  );
}

function MessageBubble({ msg, own }: { msg: Message; own: boolean }) {
  const time = new Date(msg.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
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

// The coordination chat and the request's history as one thread, in time order.
export function RequestThread({
  items, request, actingFacilityId,
}: { items: ThreadItem<Message>[]; request: RequestDetail["request"]; actingFacilityId: number | null }) {
  return (
    <>
      {items.map((item) =>
        item.type === "event" ? (
          <EventRow key={item.key} event={item.event} request={request} />
        ) : (
          <MessageBubble key={item.key} msg={item.message} own={item.message.sender_facility_id === actingFacilityId} />
        ),
      )}
    </>
  );
}
