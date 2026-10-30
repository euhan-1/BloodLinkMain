import { useEffect, useState } from "react";
import { Printer } from "lucide-react";
import { apiGet } from "../lib/api";
import { getCurrentUser } from "../lib/session";
import {
  EVENT_LABELS, eventAccountText, eventDetail, receiptTimeText, unitOutcomeText, type RequestDetail,
} from "../lib/requestTimeline";
import { EMERGENCY_TYPE_LABELS } from "../lib/requestTypes";

// The transfer record for one request: the chat's timeline frozen into a document, printable. Both facilities see the same
// page. It reads GET /requests/{id}, the same response the chat's timeline is built from, so the two cannot disagree.
// Where a fact was never captured (a request from before the ledger and the actor columns) the page says "not recorded"
// in that place; nothing is hidden, so a gap reads as a gap. A print stylesheet (the print: classes, and @page in theme.css)
// is all the printing there is: no PDF library.

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

function Party({ role, f }: { role: string; f: RequestDetail["request"]["requesting_facility"] }) {
  return (
    <div>
      <h3 className="text-[13px] font-semibold text-gray-600">{role}</h3>
      <p className="mt-0.5 text-[16px] font-semibold text-foreground print:text-black">{f.name}</p>
      <p className="text-[14px] text-foreground print:text-black">{f.address || NOT_RECORDED}</p>
      <p className="text-[14px] text-foreground print:text-black">DOH licence {f.doh_license_number || "not on file"}</p>
    </div>
  );
}

const H2 = "text-[15px] font-semibold text-foreground print:text-black pb-1.5 border-b border-gray-400";
const TH = "text-left py-1.5 pr-3 text-[12px] font-semibold text-gray-700 align-bottom";
const TD = "py-1.5 pr-3 text-[13px] text-foreground print:text-black align-top";

export function ReceiptScreen({ requestId }: { requestId: number }) {
  const [data, setData] = useState<RequestDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const user = getCurrentUser();

  useEffect(() => {
    let cancelled = false;
    apiGet<RequestDetail>(`/requests/${requestId}`)
      .then((d) => { if (!cancelled) setData(d); })
      .catch((e) => { if (!cancelled) setError(e instanceof Error ? e.message : "Failed to load"); });
    return () => { cancelled = true; };
  }, [requestId]);

  useEffect(() => {
    document.title = `Transfer record, request ${requestId}`;
  }, [requestId]);

  const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;

  return (
    <div className="min-h-screen bg-background print:min-h-0 print:bg-white">
      <div className="print:hidden border-b border-border bg-card">
        <div className="max-w-[960px] mx-auto px-6 h-14 flex items-center justify-between">
          <a href="/" className="text-[14px] font-semibold text-foreground hover:underline underline-offset-2">Back to BloodLink</a>
          <button
            type="button"
            onClick={() => window.print()}
            disabled={data === null}
            className="h-9 px-4 inline-flex items-center gap-2 rounded-md border border-border bg-card text-[14px] font-semibold text-foreground hover:bg-secondary transition-colors disabled:opacity-60"
          >
            <Printer size={15} aria-hidden="true" /> Print
          </button>
        </div>
      </div>

      <article className="max-w-[960px] mx-auto px-6 py-8 print:max-w-none print:p-0">
        {error && <p role="alert" className="text-[15px] text-status-critical-text">Could not load this record: {error}</p>}
        {!error && data === null && <p aria-busy="true" className="text-[15px] text-foreground">Loading the record…</p>}

        {data && (() => {
          const r = data.request;
          const finished = ["completed", "failed", "declined", "cancelled"].includes(r.status);
          const negative = data.units.some((u) => (u.days_remaining_at_receipt ?? 0) < 0);
          return (
            <>
              <header>
                <h1 className="font-display font-bold text-[26px] tracking-tight text-foreground print:text-black">Transfer record, request {r.id}</h1>
                <p className="mt-1 text-[15px] font-semibold text-foreground print:text-black">{statusText(r)}</p>
                <p className="mt-1 text-[13px] text-gray-600">
                  Printed {receiptTimeText(new Date().toISOString())} (times in {zone}){user ? ` by ${user.email}, ${user.facility_name ?? "BloodLink"}` : ""}
                </p>
              </header>

              <section className="mt-6 grid grid-cols-2 gap-8 print:break-inside-avoid" aria-label="Facilities">
                <Party role="From, the supplier" f={r.supplying_facility} />
                <Party role="To, the requester" f={r.requesting_facility} />
              </section>

              <section className="mt-6 print:break-inside-avoid" aria-labelledby="receipt-requested">
                <h2 id="receipt-requested" className={H2}>What was requested</h2>
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
                <h2 id="receipt-timeline" className={H2}>Timeline</h2>
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
                <h2 id="receipt-units" className={H2}>Units</h2>
                {!data.units_recorded ? (
                  <p className="mt-2 text-[14px] text-foreground print:text-black">
                    {finished || r.supplier_confirmed_at
                      ? "The unit list was not recorded for this transfer."
                      : "No units have been reserved yet. They are listed here once the supplier confirms release."}
                  </p>
                ) : (
                  <>
                    <div className="overflow-x-auto print:overflow-visible">
                      <table className="mt-2 w-full">
                        <thead>
                          <tr>
                            {["DIN", "Component", "Volume (mL)", "Collected", "Expires", "Days left at release", "Days left at receipt", "Origin shelf", "Outcome"].map((h) => (
                              <th key={h} scope="col" className={TH}>{h}</th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {data.units.map((u) => (
                            <tr key={u.din} className="border-t border-gray-300 print:break-inside-avoid">
                              <th scope="row" className={`${TD} font-mono font-semibold text-left whitespace-nowrap`}>{u.din}</th>
                              <td className={TD}>{u.component}</td>
                              <td className={`${TD} tabular-nums`}>{u.volume_ml}</td>
                              <td className={`${TD} whitespace-nowrap`}>{u.collected_date}</td>
                              <td className={`${TD} whitespace-nowrap`}>{u.expires_date}</td>
                              <td className={`${TD} tabular-nums`}>{u.days_remaining_at_release}</td>
                              <td className={`${TD} tabular-nums`}>{u.days_remaining_at_receipt ?? "not yet"}</td>
                              <td className={TD}>{u.origin_location}</td>
                              <td className={TD}>{unitOutcomeText(u)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                    {negative && (
                      <p className="mt-2 text-[12px] text-gray-600">A negative figure is how many days the unit had already expired when receipt was confirmed.</p>
                    )}
                  </>
                )}
              </section>

              <section className="mt-6 print:break-inside-avoid" aria-labelledby="receipt-chat">
                <h2 id="receipt-chat" className={H2}>Coordination chat</h2>
                <p className="mt-2 text-[14px] text-foreground print:text-black">
                  {data.message_count === 0
                    ? "The coordination chat for this request holds no messages."
                    : `The coordination chat for this request holds ${plural(data.message_count, "message", "messages")} between the two facilities.`}
                  {data.message_count > 0 && " They are not reproduced here. They can be viewed in the app, under Requests, Request and chats."}
                </p>
              </section>

              <section className="mt-6 print:break-inside-avoid" aria-labelledby="receipt-not-recorded">
                <h2 id="receipt-not-recorded" className={H2}>Not recorded by BloodLink</h2>
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
        })()}
      </article>
    </div>
  );
}
