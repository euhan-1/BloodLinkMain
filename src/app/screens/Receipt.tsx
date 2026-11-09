import { useEffect, useState } from "react";
import { Printer } from "lucide-react";
import { apiGet } from "../lib/api";
import type { RequestDetail } from "../lib/requestTimeline";
import { ReceiptRecord } from "../components/ReceiptRecord";

// The transfer record for one request as its own printable page (the document itself is components/ReceiptRecord.tsx, which the
// request's thread also shows inline). Both facilities see the same page. A print stylesheet (the print: classes, and @page in
// theme.css) is all the printing there is: no PDF library.

export function ReceiptScreen({ requestId }: { requestId: number }) {
  const [data, setData] = useState<RequestDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

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

        {data && <ReceiptRecord data={data} />}
      </article>
    </div>
  );
}
