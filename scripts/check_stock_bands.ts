// Asserts the stock band rules in statusTokens.ts. Run: node scripts/check_stock_bands.ts
import { stockStatus as s, isNotStocked as n, STOCK_LABELS as L } from "../src/app/lib/statusTokens.ts";

const eq = (a: unknown, b: unknown, m: string) => { if (a !== b) throw new Error(`${m}: ${a} !== ${b}`); };
eq(s(15, 26), "critical", "B- 15/26"); eq(s(95, 100), "watch", "O+ 95/100"); eq(s(100, 100), "safe", "at min");
eq(s(60, 100), "watch", "exactly 0.6"); eq(s(59, 100), "critical", "under 0.6");
eq(s(0, 0), "safe", "min0 stock0 excluded from counts"); eq(n(0, 0), true, "not stocked"); eq(n(3, 0), false, "min0 with stock");
eq(s(3, 0), "safe", "min0 with stock"); eq(n(0, 5), false, "min>0 zero stock"); eq(s(0, 5), "critical", "zero stock");
for (const bad of ["Low", "Marginal"]) if (Object.values(L).includes(bad)) throw new Error(`reused ${bad}`);
console.log("ok");
