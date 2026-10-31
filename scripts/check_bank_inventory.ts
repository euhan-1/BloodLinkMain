// Asserts what the blood bank Inventory says about its units (src/app/lib/bankInventory.ts). Run: node scripts/check_bank_inventory.ts
import { readFileSync } from "node:fs";
import {
  PAGE_SIZE, RELEASE_FLOOR_DAYS, RELEASE_MARGIN_DAYS, SEARCH_LIMIT, backlog, filterUnits, pageOf, releaseCutoffDays, searchUnits, sortUnits, typeSummaries,
  unitNote, unitState, NO_UNIT_FILTERS,
} from "../src/app/lib/bankInventory.ts";
import { formatCalendarDate } from "../src/app/lib/dateFormat.ts";
import type { InventorySummaryRow, InventoryUnit } from "../src/app/lib/inventoryTypes.ts";

const eq = (a: unknown, b: unknown, m: string) => { if (JSON.stringify(a) !== JSON.stringify(b)) throw new Error(`${m}: ${JSON.stringify(a)} !== ${JSON.stringify(b)}`); };
let n = 0;
const unit = (over: Partial<InventoryUnit> = {}): InventoryUnit => ({
  din: `W0412 26 ${String(100000 + n++)}`, type: "O+", component: "Packed RBC", location: "Bay 1", volume: 450, collected: "2026-10-01", expires: "2026-11-20",
  daysLeft: 20, reservedForRequestId: null, ...over,
});

// ── the release margin mirrors the server's, so the note never disagrees with what release does
const server = readFileSync(new URL("../server/main.py", import.meta.url), "utf8");
const floor = Number(server.match(/^RELEASE_EXPIRY_FLOOR_DAYS\s*=\s*(\d+)/m)?.[1]);
const dict = server.match(/^MIN_DAYS_TO_EXPIRY_AT_RELEASE\s*=\s*\{([^}]*)\}/m)?.[1] ?? "";
const margins = Object.fromEntries([...dict.matchAll(/"([^"]+)"\s*:\s*(\d+)/g)].map((m) => [m[1], Number(m[2])]));
eq(RELEASE_FLOOR_DAYS, floor, "the floor is the server's");
eq(RELEASE_MARGIN_DAYS, margins, "the per-component margins are the server's");
eq([releaseCutoffDays("Packed RBC"), releaseCutoffDays("Platelets"), releaseCutoffDays("Something new")], [3, 1, 1], "cutoff: the margin, never under the floor");

// ── why a unit is not available
eq(unitState(unit({ daysLeft: -1 })), "expired", "expired");
eq(unitState(unit({ daysLeft: -1, reservedForRequestId: 5 })), "expired", "expired outranks reserved");
eq(unitState(unit({ daysLeft: 20, reservedForRequestId: 205 })), "reserved", "reserved");
eq(unitState(unit({ daysLeft: 2, reservedForRequestId: 205 })), "reserved", "reserved outranks the margin");
eq(unitState(unit({ daysLeft: 2 })), "inside_margin", "inside the margin: 2 days for packed RBC");
eq(unitState(unit({ daysLeft: 3 })), "available", "exactly at the cutoff is offered");
eq(unitState(unit({ daysLeft: 0, component: "Platelets" })), "inside_margin", "expiring today is never offered");
eq(unitState(unit({ daysLeft: 1, component: "Platelets" })), "available", "platelets need only the floor");
eq(unitNote(unit({ daysLeft: 20, reservedForRequestId: 205 })), { text: "Reserved for request 205" }, "the reserved note names the request");
eq(unitNote(unit({ daysLeft: 1 }))?.text, "Not released: inside the margin", "the margin note");
eq(unitNote(unit({ daysLeft: 1 }))?.title, "Release does not offer Packed RBC with fewer than 3 days left.", "and says the number");
eq(unitNote(unit({ daysLeft: 20 })), null, "an available unit has no note");
eq(unitNote(unit({ daysLeft: -4 })), null, "an expired unit's time left says it, so no note");

// ── order: live units soonest first (release's order), expired last
const mixed = [unit({ din: "E-old", daysLeft: -20 }), unit({ din: "L-30", daysLeft: 30 }), unit({ din: "E-new", daysLeft: -2 }), unit({ din: "L-1", daysLeft: 1 }),
  unit({ din: "L-5b", daysLeft: 5 }), unit({ din: "L-5a", daysLeft: 5 }), unit({ din: "L-0", daysLeft: 0 })];
eq(sortUnits(mixed).map((u) => u.din), ["L-0", "L-1", "L-5a", "L-5b", "L-30", "E-new", "E-old"], "live soonest first with DIN ties, then expired most recent first");
const before = mixed.map((u) => u.din);
sortUnits(mixed);
eq(mixed.map((u) => u.din), before, "the input is left alone");

// ── filters
const set = [unit({ din: "A", daysLeft: 2 }), unit({ din: "B", daysLeft: 6 }), unit({ din: "C", daysLeft: 20 }), unit({ din: "D", daysLeft: -3 }),
  unit({ din: "E", daysLeft: 20, component: "Platelets" }), unit({ din: "F", daysLeft: 40, reservedForRequestId: 9 })];
const ids = (f: Partial<typeof NO_UNIT_FILTERS>) => filterUnits(set, { ...NO_UNIT_FILTERS, ...f }).map((u) => u.din);
eq(ids({}), ["A", "B", "C", "D", "E", "F"], "no filter");
eq(ids({ component: "Platelets" }), ["E"], "component");
eq(ids({ window: "7" }), ["A", "B"], "within 7 days is live units only, never the expired");
eq(ids({ window: "3" }), ["A"], "within 3 days");
eq(ids({ window: "30" }), ["A", "B", "C", "E"], "within 30 days");
eq(ids({ window: "expired" }), ["D"], "expired");
eq(ids({ state: "reserved" }), ["F"], "reserved");
eq(ids({ state: "inside_margin" }), ["A"], "inside the margin");
eq(ids({ state: "available" }), ["B", "C", "E"], "available");
eq(ids({ component: "Packed RBC", window: "30", state: "available" }), ["B", "C"], "filters combine");

// ── paging, fifty to a page
eq(PAGE_SIZE, 50, "fifty to a page");
const many = Array.from({ length: 121 }, (_, i) => i);
eq(pageOf(many, 1), { items: many.slice(0, 50), page: 1, pages: 3, from: 1, to: 50, total: 121 }, "first page");
eq(pageOf(many, 3).items.length, 21, "the last page is the remainder");
eq([pageOf(many, 3).from, pageOf(many, 3).to], [101, 121], "and says which");
eq(pageOf(many, 99).page, 3, "past the end clamps");
eq(pageOf(many, -4).page, 1, "before the start clamps");
eq(pageOf([], 1), { items: [], page: 1, pages: 1, from: 0, to: 0, total: 0 }, "nothing");

// ── search across types
const stock = [
  unit({ din: "W0412 26 100200", type: "O-", daysLeft: 9 }), unit({ din: "W0412 26 100201", type: "A+", daysLeft: 3 }), unit({ din: "X9999 26 100200", type: "B+", daysLeft: 1 }),
  unit({ din: "W0412 26 555100", type: "O+", location: "Bay 2", daysLeft: 12 }), unit({ din: "Q1", type: "AB+", location: "Cold room", component: "Platelets", daysLeft: 4 }),
];
eq(searchUnits(stock, "W0412 26 1002").results.map((u) => u.din), ["W0412 26 100201", "W0412 26 100200"], "a DIN prefix, soonest expiry first");
eq(searchUnits(stock, "w041226100200").results.map((u) => u.din), ["W0412 26 100200"], "a scan with no spaces, any case, is one DIN");
eq(searchUnits(stock, "100200").results.map((u) => u.din), ["X9999 26 100200", "W0412 26 100200"], "a DIN substring, across types, soonest expiry first");
eq(searchUnits(stock, "bay 2").results.map((u) => u.din), ["W0412 26 555100"], "a shelf");
eq(searchUnits(stock, "platelets").results.map((u) => u.din), ["Q1"], "a component");
eq(searchUnits(stock, "").total, 0, "an empty query matches nothing");
eq(searchUnits(stock, "   ").total, 0, "so does blank");
eq(searchUnits(stock, "nothing like this"), { results: [], total: 0 }, "no match");
// a DIN match outranks a shelf or component match, whatever the expiry
const ranked = searchUnits([unit({ din: "ZZZ", location: "100200 shelf", daysLeft: 1 }), unit({ din: "W 100200", daysLeft: 30 })], "100200");
eq(ranked.results.map((u) => u.din), ["W 100200", "ZZZ"], "DIN before shelf");
const lots = Array.from({ length: 80 }, (_, i) => unit({ din: `LOT-${String(i).padStart(3, "0")}`, daysLeft: i }));
const capped = searchUnits(lots, "lot");
eq([capped.results.length, capped.total, SEARCH_LIMIT], [50, 80, 50], "capped at fifty, and says how many matched");
eq(capped.results[0].din, "LOT-000", "soonest expiry first within the same rank");
// within a rank, live units come before expired ones even when the expired ones are long past: the same order as an opened type
const withOld = searchUnits([unit({ din: "LOT-OLD", daysLeft: -30 }), unit({ din: "LOT-LATE", daysLeft: 40 }), unit({ din: "LOT-SOON", daysLeft: 1 }), unit({ din: "LOT-NEW", daysLeft: -1 })], "lot");
eq(withOld.results.map((u) => u.din), ["LOT-SOON", "LOT-LATE", "LOT-NEW", "LOT-OLD"], "search lists live units first, soonest expiry, then expired, most recent first");
eq(withOld.results.map((u) => u.din), sortUnits(withOld.results).map((u) => u.din), "and that is exactly the order of an opened type");

// ── the table of types
const summary: Record<string, InventorySummaryRow> = {
  "O+": { blood_type: "O+", units: 8, minimum_units: 10, maximum_units: 40 }, "O-": { blood_type: "O-", units: 30, minimum_units: 10, maximum_units: 40 },
};
const rows = [
  ...Array.from({ length: 8 }, () => unit({ type: "O+", daysLeft: 20 })), unit({ type: "O+", daysLeft: 4 }), unit({ type: "O+", daysLeft: 6, reservedForRequestId: 3 }),
  unit({ type: "O+", daysLeft: -2 }), unit({ type: "O+", daysLeft: -9 }),
];
const t = typeSummaries(rows, summary);
eq(t.map((r) => r.type), ["A-", "A+", "AB-", "AB+", "B-", "B+", "O-", "O+"], "every type, in the app's order, even with no units");
const o = t.find((r) => r.type === "O+")!;
eq([o.units, o.minimum, o.maximum, o.level, o.notStocked], [8, 10, 40, "watch", false], "O+ stock against its minimum");
eq([o.expiring, o.expired, o.held], [1, 2, 12], "expiring leaves out the reserved unit; expired and held are exact");
const none = t.find((r) => r.type === "A-")!;
eq([none.units, none.level, none.expiring, none.expired, none.held], [0, null, 0, 0, 0], "a type with no thresholds and no units");

// ── the expired backlog: the number on the button is the number it clears
const stale = [...Array.from({ length: 5 }, () => unit({ daysLeft: -3 })), unit({ daysLeft: -1, reservedForRequestId: 8 }), unit({ daysLeft: 9 }), unit({ daysLeft: 0 })];
eq(backlog(stale), { expired: 6, clearable: 5, held: 1, usable: 2, total: 8 }, "the backlog, what clearing takes, and what stays");
eq(backlog([]), { expired: 0, clearable: 0, held: 0, usable: 0, total: 0 }, "an empty list");

// ── dates are written out, not left to the browser
eq(formatCalendarDate("2026-11-04"), "4 Nov 2026", "a calendar date");

// ── the views use neither the browser's locale nor tracked capitals
for (const f of ["screens/BankInventory.tsx", "lib/bankInventory.ts"]) {
  const code = readFileSync(new URL(`../src/app/${f}`, import.meta.url), "utf8").replace(/\/\/.*$/gm, "");
  if (/toLocale/.test(code)) throw new Error(`${f} must not rely on the browser's locale`);
  if (/uppercase|tracking-wide|tracking-wider/.test(code)) throw new Error(`${f} has tracked capitals`);
  if (/\(est\.\)/.test(code)) throw new Error(`${f} shows an estimate where a count belongs`);
}

// the field the notes need is the field the server sends
if (!/"reserved_for_request_id": r\["reserved_for_request_id"\]/.test(server)) throw new Error("GET /inventory does not return reserved_for_request_id");

console.log("ok");
