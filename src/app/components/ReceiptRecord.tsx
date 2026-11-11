import type { ElementType } from "react";
import { getCurrentUser } from "../lib/session";
import { EVENT_LABELS, eventAccountText, eventDetail, receiptTimeText, type RequestDetail } from "../lib/requestTimeline";
import { EMERGENCY_TYPE_LABELS } from "../lib/requestTypes";
import { TD, TH, UnitsTable } from "./TransferUnits";

// The transfer record for one request, as the receipt page renders it and as the request's thread shows it inline. It renders
// from GET /requests/{id}, the same response the chat's timeline is built from, so the two cannot disagree. Where a fact was
// never captured (a request from before the ledger and the actor columns) the page says "not recorded" in that place; nothing
// is hidden, so a gap reads as a gap. Three props fit it into the inline view (see the request's thread): `showPrintedLine` is the
// "Printed ... by ..." stamp, for the page that is printed; `showChatLocation` is the sentence saying where the chat can be read,
// which is pointless where the chat is already on screen; `headingLevel` is the title's level (1 on its own page), with its
// sections one level below it and the facilities one below those, so the heading outline stays in order wherever it sits.

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;
const NOT_RECORDED = <span className="text-gray-600">not recorded</span>;

function statusText(r: RequestDetail["request"]): string {
  switch (r.status) {
    case "completed": return "Completed";
    case "failed": return "Failed, no units received";
    case "declined": return "Declined";
    case "cancelled": return "Cancelled";
    case "pending": return "Awaiting a response";
    default: return r.supplier_confirmed_at ? "Released, awaiting confirmation of receipt" : "Accepted, not yet released";
  }
}

function Party({ role, f, Heading }: { role: string; f: RequestDetail["request"]["requesting_facility"]; Heading: ElementType }) {
  return (
    <div>
      <Heading className="text-[13px] font-semibold text-gray-600">{role}</Heading>
      <p className="mt-0.5 text-[16px] font-semibold text-foreground print:text-black">{f.name}</p>
      <p className="text-[14px] text-foreground print:text-black">{f.address || NOT_RECORDED}</p>
    </div>
  );
}

const H2 = "text-[15px] font-semibold text-foreground print:text-black pb-1.5 border-b border-gray-400";

export type HeadingLevel = 1 | 2 | 3 | 4; // at most 4: the facilities sit two levels below the title, and h6 is the last
const HEADINGS = ["h1", "h2", "h3", "h4", "h5", "h6"] as const;

export function ReceiptRecord({
  data, showPrintedLine = true, showChatLocation = true, headingLevel = 1,
}: { data: RequestDetail; showPrintedLine?: boolean; showChatLocation?: boolean; headingLevel?: HeadingLevel }) {
  const Title: ElementType = HEADINGS[headingLevel - 1];
  const Section: ElementType = HEADINGS[headingLevel];
  const Party3: ElementType = HEADINGS[headingLevel + 1];
  const user = getCurrentUser();
  const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const r = data.request;
  const finished = ["completed", "failed", "declined", "cancelled"].includes(r.status);
  return (
    <>
      <header>
        <Title className="font-display font-bold text-[26px] tracking-tight text-foreground print:text-black">Transfer record, request {r.id}</Title>
        <p className="mt-1 text-[15px] font-semibold text-foreground print:text-black">{statusText(r)}</p>
        {showPrintedLine && (
          <p className="mt-1 text-[13px] text-gray-600">
            Printed {receiptTimeText(new Date().toISOString())} (times in {zone}){user ? ` by ${user.email}, ${user.facility_name ?? "BloodLink"}` : ""}
          </p>
        )}
      </header>

      <section className="mt-6 grid grid-cols-2 gap-8 print:break-inside-avoid" aria-label="Facilities">
        <Party role="From, the supplier" f={r.supplying_facility} Heading={Party3} />
        <Party role="To, the requester" f={r.requesting_facility} Heading={Party3} />
      </section>

      <section className="mt-6 print:break-inside-avoid" aria-labelledby="receipt-requested">
        <Section id="receipt-requested" className={H2}>What was requested</Section>
        <dl className="mt-2 grid grid-cols-[10rem_1fr] gap-y-1 text-[14px] text-foreground print:text-black">
          <dt className="text-gray-600">Blood type</dt><dd>{r.blood_type}</dd>
          <dt className="text-gray-600">Component</dt><dd>{r.component ?? NOT_RECORDED}</dd>
          <dt className="text-gray-600">Asked for</dt><dd>{plural(r.quantity, "unit", "units")}</dd>
          <dt className="text-gray-600">Received</dt>
          <dd>
            {r.status === "declined" || r.status === "cancelled"
              ? "Not applicable"
              : data.summary.received === null
                ? "Not yet received"
                : `${data.summary.received} of ${r.quantity}${data.summary.failed ? `, ${data.summary.failed} failed` : ""}`}
          </dd>
          <dt className="text-gray-600">Reason</dt><dd>{EMERGENCY_TYPE_LABELS[r.emergency_type] ?? r.emergency_type}</dd>
        </dl>
      </section>

      <section className="mt-6" aria-labelledby="receipt-timeline">
        <Section id="receipt-timeline" className={H2}>Timeline</Section>
        <table className="mt-2 w-full">
          <thead>
            <tr>
              <th scope="col" className={TH}>Event</th>
              <th scope="col" className={TH}>Time</th>
              <th scope="col" className={TH}>Facility</th>
              <th scope="col" className={TH}>Account</th>
              <th scope="col" className={TH}>Detail</th>
            </tr>
          </thead>
          <tbody>
            {data.timeline.map((e) => (
              <tr key={e.kind} className="border-t border-gray-300 print:break-inside-avoid">
                <th scope="row" className={`${TD} font-semibold text-left whitespace-nowrap`}>{EVENT_LABELS[e.kind]}</th>
                <td className={`${TD} whitespace-nowrap`}>{receiptTimeText(e.at)}</td>
                <td className={TD}>{e.facility_name ?? NOT_RECORDED}</td>
                <td className={`${TD} min-w-[13rem] [overflow-wrap:anywhere]`}>{eventAccountText(e)}</td>
                <td className={TD}>{eventDetail(e, r) ?? ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="mt-6" aria-labelledby="receipt-units">
        <Section id="receipt-units" className={H2}>Units</Section>
        {!data.units_recorded ? (
          <p className="mt-2 text-[14px] text-foreground print:text-black">
            {finished || r.supplier_confirmed_at
              ? "The unit list was not recorded for this transfer."
              : "No units have been reserved yet. They are listed here once the supplier confirms release."}
          </p>
        ) : (
          <UnitsTable units={data.units} />
        )}
      </section>

      <section className="mt-6 print:break-inside-avoid" aria-labelledby="receipt-chat">
        <Section id="receipt-chat" className={H2}>Messages about this request</Section>
        <p className="mt-2 text-[14px] text-foreground print:text-black">
          {data.message_count === 0
            ? "No message was sent about this request."
            : `${plural(data.message_count, "message was", "messages were")} sent about this request between the two facilities.`}
          {data.message_count > 0 && " They are not reproduced here."}
          {data.message_count > 0 && showChatLocation && " They can be viewed in the app, under Supply, Messages, in the conversation with the other facility."}
        </p>
      </section>

      <section className="mt-6 print:break-inside-avoid" aria-labelledby="receipt-not-recorded">
        <Section id="receipt-not-recorded" className={H2}>Not recorded by BloodLink</Section>
        <p className="mt-2 text-[14px] text-foreground print:text-black">
          Condition of the units on arrival, storage temperature, transport and signatures are not recorded by BloodLink.
          This record does not show that any of them were checked.
        </p>
      </section>

      <footer className="mt-8 pt-3 border-t border-gray-300 text-[12px] text-gray-600 print:break-inside-avoid">
        An account is a facility's login, not a named person: BloodLink holds one account per facility. "Not recorded" means
        the information was not captured when the transfer happened, not that it did not happen.
      </footer>
    </>
  );
}
