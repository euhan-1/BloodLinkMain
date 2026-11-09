import type { ReactNode } from "react";
import { unitOutcomeText, type LedgerUnit } from "../lib/requestTimeline";

// The units of one transfer as a table, shared by the receipt (every column) and the request's thread (a few, under the release
// and receipt events), so a unit is written the same way in both. The receipt's own markup and classes, unchanged.

export const TH = "text-left py-1.5 pr-3 text-[12px] font-semibold text-gray-700 align-bottom";
export const TD = "py-1.5 pr-3 text-[13px] text-foreground print:text-black align-top";

export type UnitColumn = "din" | "type" | "component" | "volume" | "collected" | "expires" | "daysRelease" | "daysReceipt" | "origin" | "outcome";

const COLUMNS: Record<UnitColumn, { head: string; cell: (u: LedgerUnit) => ReactNode; className?: string }> = {
  din: { head: "DIN", cell: (u) => u.din, className: "font-mono font-semibold text-left whitespace-nowrap" }, // the row header
  type: { head: "Blood type", cell: (u) => u.blood_type, className: "whitespace-nowrap" },
  component: { head: "Component", cell: (u) => u.component },
  volume: { head: "Volume (mL)", cell: (u) => u.volume_ml, className: "tabular-nums" },
  collected: { head: "Collected", cell: (u) => u.collected_date, className: "whitespace-nowrap" },
  expires: { head: "Expires", cell: (u) => u.expires_date, className: "whitespace-nowrap" },
  daysRelease: { head: "Days left at release", cell: (u) => u.days_remaining_at_release, className: "tabular-nums" },
  daysReceipt: { head: "Days left at receipt", cell: (u) => u.days_remaining_at_receipt ?? "not yet", className: "tabular-nums" },
  origin: { head: "Origin shelf", cell: (u) => u.origin_location },
  outcome: { head: "Outcome", cell: (u) => unitOutcomeText(u) },
};

// What the receipt shows: every column except blood type (the receipt states it once, under "What was requested").
export const RECEIPT_UNIT_COLUMNS: UnitColumn[] = ["din", "component", "volume", "collected", "expires", "daysRelease", "daysReceipt", "origin", "outcome"];

export function UnitsTable({ units, columns = RECEIPT_UNIT_COLUMNS }: { units: LedgerUnit[]; columns?: UnitColumn[] }) {
  const negative = columns.includes("daysReceipt") && units.some((u) => (u.days_remaining_at_receipt ?? 0) < 0);
  return (
    <>
      <div className="overflow-x-auto print:overflow-visible">
        <table className="mt-2 w-full">
          <thead>
            <tr>
              {columns.map((c) => (
                <th key={c} scope="col" className={TH}>{COLUMNS[c].head}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {units.map((u) => (
              <tr key={u.din} className="border-t border-gray-300 print:break-inside-avoid">
                {columns.map((c) =>
                  c === "din" ? (
                    <th key={c} scope="row" className={`${TD} ${COLUMNS[c].className}`}>{COLUMNS[c].cell(u)}</th>
                  ) : (
                    <td key={c} className={`${TD} ${COLUMNS[c].className ?? ""}`.trim()}>{COLUMNS[c].cell(u)}</td>
                  ),
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {negative && (
        <p className="mt-2 text-[12px] text-gray-600">A negative figure is how many days the unit had already expired when receipt was confirmed.</p>
      )}
    </>
  );
}
