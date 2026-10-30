// Asserts the request component list is the server's and that red-cell compatibility is shown only where it applies.
// Run: node scripts/check_components.ts
import { readFileSync } from "node:fs";
import { BLOOD_COMPONENTS, RED_CELL_COMPONENTS, usesRedCellCompatibility } from "../src/app/lib/bloodComponents.ts";

const eq = (a: unknown, b: unknown, m: string) => { if (JSON.stringify(a) !== JSON.stringify(b)) throw new Error(`${m}: ${JSON.stringify(a)} !== ${JSON.stringify(b)}`); };

const server = readFileSync(new URL("../server/main.py", import.meta.url), "utf8");
const strings = (s: string) => [...(s.match(/"([^"]+)"/g) ?? [])].map((x) => x.slice(1, -1));
const components = server.match(/^COMPONENTS\s*=\s*\(([^)]*)\)/m);
const red = server.match(/^RED_CELL_COMPONENTS\s*=\s*\{([^}]*)\}/m);
if (!components || !red) throw new Error("COMPONENTS or RED_CELL_COMPONENTS not found in server/main.py");
eq(strings(components[1]), [...BLOOD_COMPONENTS], "frontend and server components, in order");
eq(strings(red[1]).sort(), [...RED_CELL_COMPONENTS].sort(), "frontend and server red-cell components");

eq(BLOOD_COMPONENTS.map(usesRedCellCompatibility), [true, false], "compatibility line: Packed RBC yes, Platelets no");
eq(usesRedCellCompatibility(null), false, "no component chosen yet: no compatibility line");
if (!RED_CELL_COMPONENTS.every((c) => (BLOOD_COMPONENTS as readonly string[]).includes(c))) throw new Error("a red-cell component is not a component");

console.log("ok");
