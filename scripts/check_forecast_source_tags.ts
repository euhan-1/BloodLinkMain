// Asserts the per-type forecast source tag in forecastSource.ts. Run: node scripts/check_forecast_source_tags.ts
import { readFileSync } from "node:fs";
import { forecastSourceTag as tag } from "../src/app/lib/forecastSource.ts";

const eq = (a: unknown, b: unknown, m: string) => { if (JSON.stringify(a) !== JSON.stringify(b)) throw new Error(`${m}: ${JSON.stringify(a)} !== ${JSON.stringify(b)}`); };
const G = "SARIMAX(1,0,2)x(1,0,1,7)+dengue [grid-searched]";
const A = "SARIMAX(0,1,4)x(0,0,0,7)+dengue [ai-recommended]";
const D = "SARIMAX(0,1,4)x(1,0,1,7)+dengue [default-order]";
const sarimax = (orders: Record<string, string> | undefined, pending: string[] = []) =>
  ({ forecast_source: "sarimax_facility_history", sarimax_orders: orders, pending_types: pending });

// the three mechanisms: words on the tag, the full label in the tooltip
eq(tag(sarimax({ "O+": G }), "O+"), { text: "grid-searched", title: G }, "grid");
eq(tag(sarimax({ "O+": A }), "O+"), { text: "AI-recommended", title: A }, "ai");
eq(tag(sarimax({ "O+": D }), "O+"), { text: "default order", title: D }, "default");

// Northside today: four grid-searched, four on the linear trend (absent from sarimax_orders)
const northside = sarimax({ "O+": G, "O-": G, "B+": G, "B-": G });
for (const t of ["O+", "O-", "B+", "B-"]) eq(tag(northside, t)?.text, "grid-searched", `northside ${t}`);
for (const t of ["A+", "A-", "AB+", "AB-"]) eq(tag(northside, t)?.text, "linear trend", `northside ${t}`);
// facility 158 today: eight default order
for (const t of ["A+", "A-", "AB+", "AB-", "B+", "B-", "O+", "O-"]) eq(tag(sarimax(Object.fromEntries(["A+", "A-", "AB+", "AB-", "B+", "B-", "O+", "O-"].map((x) => [x, D]))), t)?.text, "default order", `158 ${t}`);

// a pending type shows no tag, even though it is absent from sarimax_orders
eq(tag(sarimax({ "O+": G }, ["A+"]), "A+"), null, "pending");
// the facility-level linear trend source: every type is on it
eq(tag({ forecast_source: "linear_trend" }, "A+")?.text, "linear trend", "linear source");
// nothing to say: synthetic stand-in, no forecast, or an API response without sarimax_orders
eq(tag({ forecast_source: "synthetic_model_stand_in" }, "A+"), null, "synthetic");
eq(tag({ forecast_source: "none" }, "A+"), null, "none");
eq(tag(sarimax(undefined), "O+"), null, "older API without sarimax_orders");
// an empty map is a real answer: every type is on the linear trend
eq(tag(sarimax({}), "O+")?.text, "linear trend", "empty map");
// a label this code does not recognize is not guessed at
eq(tag(sarimax({ "O+": "SARIMAX(0,1,4)x(1,0,1,7)+dengue [mystery]" }), "O+"), null, "unknown suffix");
eq(tag(sarimax({ "O+": "SARIMAX(0,1,4)x(1,0,1,7)+dengue" }), "O+"), null, "no suffix");

// no accuracy wording in the tags, tooltips or the module's own comments
const words = /\b(better|best|worse|optimi[sz]|improv|accura|reliab|quality|good|great|superior|precise|robust)/i;
const shown = [G, A, D].map((l) => tag(sarimax({ x: l }), "x")).concat(tag({ forecast_source: "linear_trend" }, "x"));
for (const s of shown) if (words.test(`${s?.text} ${s?.title}`)) throw new Error(`accuracy wording in tag: ${s?.text} / ${s?.title}`);
const src = readFileSync(new URL("../src/app/lib/forecastSource.ts", import.meta.url), "utf8");
if (words.test(src)) throw new Error(`accuracy wording in forecastSource.ts: ${src.match(words)?.[0]}`);
console.log("ok");
