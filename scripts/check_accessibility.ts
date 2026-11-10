// Asserts the accessibility rules the app is built to (batch 9). Run: node scripts/check_accessibility.ts
//
// The repo has no component test runner, so most of this reads the sources, and the focus logic that can be pure is tested directly
// (src/app/lib/dialogFocus.ts). What a browser does with it (focus really moving, Tab really wrapping, Escape really closing, one
// <h1> really on screen in every state) is checked against the built app in headless Chrome, not here.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { FOCUSABLE_SELECTOR, tabTarget } from "../src/app/lib/dialogFocus.ts";

const eq = (a: unknown, b: unknown, m: string) => { if (JSON.stringify(a) !== JSON.stringify(b)) throw new Error(`${m}: ${JSON.stringify(a)} !== ${JSON.stringify(b)}`); };
const yes = (cond: boolean, m: string) => { if (!cond) throw new Error(m); };
const root = fileURLToPath(new URL("..", import.meta.url));
const read = (path: string) => readFileSync(join(root, path), "utf8");
const walk = (dir: string): string[] => readdirSync(join(root, dir)).flatMap((f) => {
  const rel = `${dir}/${f}`;
  return statSync(join(root, rel)).isDirectory() ? walk(rel) : [rel];
});
const sources = walk("src/app").filter((f) => f.endsWith(".tsx"));

// ── one <h1> per screen state. A file may hold several (sign-in has one per state: sign in, reset, code, done, forced change), but
//    only one renders at a time, so the number below is the number of states, not of headings on a page.
const H1: Record<string, number> = {
  "src/app/screens/Login.tsx": 5,           // Sign in / Reset your password / Enter your reset code / Password updated / Set a new password
  "src/app/screens/RegisterFacility.tsx": 3, // Register your facility / Check your email / Email verified
  "src/app/screens/ResetPassword.tsx": 3,    // Invalid reset link / Password updated / Set a new password
  "src/app/screens/CompleteProfile.tsx": 1,
  "src/app/screens/PrivacyNotice.tsx": 1,
  "src/app/screens/BankDashboard.tsx": 1,    // sr-only "Dashboard": the page has a headline sentence, not a title
  "src/app/screens/HospitalDashboard.tsx": 1,
  "src/app/screens/Inventory.tsx": 1,
  "src/app/screens/HospitalSourcing.tsx": 1, // the Source supply / Emergency sourcing tab
  "src/app/screens/Requests.tsx": 1,         // the Request and chats tab (its other two tabs have their own: HospitalSourcing, TransferHistory)
  "src/app/screens/TransferHistory.tsx": 1,
  "src/app/screens/Admin.tsx": 1,            // the header's "BloodLink Admin"
};
const count = (src: string, re: RegExp) => (src.match(re) ?? []).length;
for (const [file, n] of Object.entries(H1)) eq(count(read(file), /<h1[\s>]/g), n, `${file}: <h1> per screen state`);
// Any other file with an <h1> would add a second one to a page. ReceiptRecord is the receipt page's own document (headingLevel 1 there only).
const others = sources.filter((f) => !(f in H1) && /<h1[\s>]/.test(read(f)));
eq(others, [], "no other file adds an <h1>");
yes(/headingLevel\?: HeadingLevel/.test(read("src/app/components/ReceiptRecord.tsx")) && read("src/app/screens/Requests.tsx").includes("headingLevel={3}"),
  "the receipt shown inside the chat is a level-3 heading under the chat's h2, not a second h1");
// The page-level titles that were visible headings keep their size: theme.css gives h1/h2/h3 different default sizes, so a bare tag change
// would resize them. Every h1 and h2 in a screen names its size.
for (const file of Object.keys(H1)) {
  for (const m of read(file).matchAll(/<h([12])\b([^>]*)>/g)) {
    if (m[2].includes("sr-only")) continue;
    yes(/(^|[\s"])text-(\[[\d.]+px\]|xs|sm|base|lg|xl|2xl|3xl)(?=[\s"]|$)|clamp/.test(m[2]), `${file}: <h${m[1]}${m[2]}> must name its text size (theme.css's default differs by level)`);
  }
}

// ── no control relies on a native title alone. A `title` attribute is only a mouse tooltip: it is never shown on keyboard focus or on
//    touch. So any JSX element with one must also have an accessible name or description (aria-label, aria-labelledby or
//    aria-describedby); where the explanation matters, the Tip component carries it instead (hover, focus and tap).
type Tag = { name: string; text: string; line: number };
function jsxTags(src: string): Tag[] {
  const tags: Tag[] = [];
  const open = /<([A-Za-z][\w.]*)(?=[\s/>])/g;
  for (let m = open.exec(src); m; m = open.exec(src)) {
    const before = src.slice(0, m.index).trimEnd().slice(-1);
    if (/[\w\])]/.test(before) && before !== ")") continue; // a generic (useState<string>), not JSX
    let i = open.lastIndex, depth = 0, quote = "";
    for (; i < src.length; i++) {
      const c = src[i];
      if (quote) { if (c === quote && src[i - 1] !== "\\") quote = ""; continue; }
      if (c === '"' || c === "'" || c === "`") { if (depth > 0 || c !== "'") quote = c; continue; }
      if (c === "{") depth++;
      else if (c === "}") depth--;
      else if (c === ">" && depth === 0) break;
    }
    // `text` keeps only what is outside {...}: an attribute whose value is JSX (Suspense's fallback={<Modal title=...>}) must not lend its
    // own attributes to the outer tag. The attribute names before each { are kept, so title={x} and aria-label={x} are still seen.
    let flat = "", d = 0;
    for (const c of src.slice(m.index, i + 1)) {
      if (c === "{") { if (d === 0) flat += "{"; d++; continue; }
      if (c === "}") { d--; if (d === 0) flat += "}"; continue; }
      if (d === 0) flat += c;
    }
    tags.push({ name: m[1], text: flat, line: src.slice(0, m.index).split("\n").length });
  }
  return tags;
}
// Where a title is deliberately the supplement, and why. Each needs its reason; the list must not grow quietly.
const TITLE_OK: { file: string; tag: string; reason: string }[] = [
  { file: "src/app/components/AccountMenu.tsx", tag: "time", reason: "the exact time of a notification; the same date is also in visually hidden text inside the element, so the title is only a mouse extra" },
  { file: "src/app/components/FacilityNetworkMap.tsx", tag: "Marker", reason: "Leaflet makes the marker a focusable button and uses this as its accessible name; the same facts are in the ranked list beside the map" },
];
let titled = 0;
for (const file of sources) {
  for (const t of jsxTags(read(file))) {
    if (t.name === "Modal" || !/\stitle=/.test(t.text)) continue; // Modal's title is its heading, not an attribute on the page
    titled++;
    if (TITLE_OK.some((ok) => ok.file === file && ok.tag === t.name)) continue;
    yes(/\saria-(label|labelledby|describedby)=/.test(t.text), `${file}:${t.line} <${t.name}> has a title and no aria-label, aria-labelledby or aria-describedby: a title alone is invisible to keyboard and touch (use aria-label, or <Tip>)`);
  }
}
eq(TITLE_OK.length, 2, "the reviewed list of title exceptions");
yes(titled > 0, "the title scan found elements (it would pass trivially if it parsed nothing)");
// The explanations that used to live in titles are in Tip now.
for (const file of ["src/app/screens/BankDashboard.tsx", "src/app/screens/Inventory.tsx", "src/app/screens/BankInventory.tsx"]) {
  yes(read(file).includes("<Tip"), `${file} explains through <Tip>`);
}
const tip = read("src/app/components/Tip.tsx");
yes(tip.includes('role="tooltip"') && tip.includes("aria-describedby") && tip.includes("onFocus") && tip.includes("Escape") && tip.includes("sr-only"),
  "Tip: a tooltip role, aria-describedby, opens on focus, closes on Escape, and its text is always in the page for screen readers");

// ── dialogs: the logic (pure) ...
eq(tabTarget(0, -1, false), "container", "nothing focusable: keep focus on the dialog");
eq(tabTarget(3, 2, false), 0, "Tab on the last control wraps to the first");
eq(tabTarget(3, 0, true), 2, "Shift+Tab on the first control wraps to the last");
eq(tabTarget(3, 1, false), null, "Tab in the middle: the browser's own move stays inside");
eq(tabTarget(3, 1, true), null, "Shift+Tab in the middle: likewise");
eq(tabTarget(3, -1, false), 0, "focus outside the list (on the dialog itself) goes in at the start");
eq(tabTarget(3, -1, true), 2, "and Shift+Tab goes in at the end");
eq(tabTarget(1, 0, false), 0, "one control: Tab stays on it");
eq(tabTarget(1, 0, true), 0, "one control: Shift+Tab stays on it");
for (const part of ["a[href]", "button:not([disabled])", "input:not([disabled])", "select:not([disabled])", "textarea:not([disabled])", "[tabindex]"]) {
  yes(FOCUSABLE_SELECTOR.includes(part), `the focusable selector covers ${part}`);
}
// ... and the wiring: one shared component, used by every dialog and both header popovers, and no overlay of its own anywhere else
const panel = read("src/app/components/DialogPanel.tsx");
yes(/<div ref=\{ref\} role="dialog" aria-modal="true" aria-labelledby=\{labelledBy\}/.test(panel), "DialogPanel renders role=dialog, aria-modal and aria-labelledby on its element (not just in a comment)");
for (const part of ['"Escape"', "tabTarget(", "returnFocusRef", "first.focus(", "document.addEventListener(\"keydown\""]) {
  yes(panel.includes(part), `DialogPanel: ${part}`);
}
const modal = read("src/app/components/Modal.tsx");
yes(modal.includes("<DialogPanel") && modal.includes("useId()") && /aria-label=\{`Close \$\{title\}`\}/.test(modal) && modal.includes("data-dialog-close"),
  "Modal renders through DialogPanel, labels it by its title, and its close button has a name");
yes(!/<h3\b/.test(modal), "a dialog's title is a heading of its own (h2), not an h3 under nothing");
const menu = read("src/app/components/AccountMenu.tsx");
eq(count(menu, /<DialogPanel\b/g), 2, "the notifications panel and the account menu both use DialogPanel");
yes(menu.includes("aria-haspopup=\"dialog\"") && menu.includes("Account menu, ${user.email}"), "the account menu's button names itself (it is only an avatar below the lg breakpoint) and says it opens a dialog");
yes(count(menu, /returnFocusRef=\{triggerRef\}/g) >= 4, "dialogs opened from the account menu return focus to its button (their own opener is gone by then)");
const overlays = sources.filter((f) => /fixed inset-0/.test(read(f)) && !f.endsWith("Modal.tsx"));
eq(overlays, [], "no overlay is built by hand outside Modal (it would skip the focus handling)");
for (const file of sources) {
  for (const t of jsxTags(read(file))) {
    if (t.name === "Modal") yes(/\stitle=/.test(t.text), `${file}:${t.line} a <Modal> needs a title: it is the dialog's accessible name`);
  }
}

// ── charts have a text alternative, and status is never colour alone
const dial = read("src/app/components/StockDial.tsx");
yes(dial.includes('role="img"') && dial.includes("aria-label={label}") && dial.includes("status ${STOCK_LABELS[level]}"), "the gauge is an image whose label says the value, the scale and the status word");
const bank = read("src/app/screens/BankDashboard.tsx");
yes(/role="img" aria-label=\{describeTrendChart\(/.test(bank), "the trend chart is an image labelled by describeTrendChart (its data as text)");
for (const file of ["src/app/screens/BankDashboard.tsx", "src/app/screens/HospitalDashboard.tsx", "src/app/screens/BankInventory.tsx"]) {
  const lines = read(file).split("\n");
  lines.forEach((line, i) => {
    if (!line.includes("<StatusDot")) return;
    yes(/STOCK_LABELS|statusWord/.test(lines.slice(i, i + 3).join(" ")), `${file}:${i + 1} a status dot with no status word beside it: colour would be the only signal`);
  });
}

// ── the password rule the Register form states is the server's (and the other forms' 8 are the server's 8)
const auth = read("server/auth.py");
const serverRegistration = auth.match(/len\(password\) < (\d+)/);
yes(serverRegistration !== null, "server/auth.py: the registration length rule was not found");
const hint = read("src/app/screens/RegisterFacility.tsx").match(/At least (\d+) characters, with at least one letter and one digit\./);
yes(hint !== null, "the Register hint must state the length, a letter and a digit");
eq(Number(hint![1]), Number(serverRegistration![1]), "the Register hint's length is the server's");
yes(auth.includes("isalpha()") && auth.includes("isdigit()") && /_WEAK_PASSWORDS/.test(auth), "the server still has the letter, digit and common-password rules the hint names");
const main = read("server/main.py");
const otherMinimums = [...main.matchAll(/len\(v\) < (\d+)\)?:?\s*\n\s*raise ValueError\("password must be at least (\d+) characters"\)/g)].map((m) => Number(m[1]));
yes(otherMinimums.length >= 4 && otherMinimums.every((n) => n === 8), `the server's other password endpoints all require 8 (found ${JSON.stringify(otherMinimums)})`);
for (const file of ["src/app/screens/Login.tsx", "src/app/screens/ResetPassword.tsx", "src/app/components/AccountMenu.tsx"]) {
  yes(/\.length < 8\b/.test(read(file)) && /at least 8 characters/.test(read(file)), `${file} checks and says 8, as the server does`);
}

console.log("ok");
