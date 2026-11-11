// Asserts the Privacy Notice (RA 10173): the contact address is set, the notice version matches the server's, the notice says what it must
// and claims nothing it must not, and /privacy is reachable and linked from sign-in, Register and the account menu.
// Run: node scripts/check_privacy.ts
//
// THIS FAILS WHEN VITE_PRIVACY_CONTACT_EMAIL IS NOT SET (in the environment, .env, .env.local or .env.production: what `vite build` would
// embed). That is deliberate: a notice with no way to reach the people responsible must not ship by accident. Set it, then run again:
//   VITE_PRIVACY_CONTACT_EMAIL=privacy@your-domain node scripts/check_privacy.ts
import { readFileSync } from "node:fs";
import { loadEnv } from "vite";
import {
  CONSENT_LABEL, CONTACT_PERSON_NOTICE, NOTICE_SECTIONS, PRIVACY_NOTICE_EFFECTIVE_DATE, PRIVACY_NOTICE_VERSION, noticeText, privacyContact,
} from "../src/app/lib/privacyNotice.ts";

const eq = (a: unknown, b: unknown, m: string) => { if (JSON.stringify(a) !== JSON.stringify(b)) throw new Error(`${m}: ${JSON.stringify(a)} !== ${JSON.stringify(b)}`); };
const yes = (cond: boolean, m: string) => { if (!cond) throw new Error(m); };
const read = (path: string) => readFileSync(new URL(`../${path}`, import.meta.url), "utf8");

// ── the contact address: the one thing that stops a placeholder shipping
const env = loadEnv("production", process.cwd(), "VITE_");
const configured = privacyContact(env.VITE_PRIVACY_CONTACT_EMAIL);
if (!configured.ok) {
  throw new Error(
    "VITE_PRIVACY_CONTACT_EMAIL is not set to a real address (unset, not an email, or a placeholder such as @example.com). " +
    "The /privacy page would tell every visitor the notice is incomplete. Set it where the build reads it (Vercel project settings, .env.local) and run this again.",
  );
}

// ── the contact helper itself (so the rule above means what it says)
eq(privacyContact("  privacy@school.edu.ph ").ok, true, "a real address passes, trimmed");
for (const bad of [undefined, "", "   ", "not-an-email", "me@", "@x.ph", "a b@x.ph", "privacy@example.com", "privacy@EXAMPLE.org", "x@y.test", "x@bloodlink.invalid", "x@host.localhost"]) {
  eq(privacyContact(bad as string | undefined).ok, false, `${JSON.stringify(bad)} must not count as a contact address`);
}

// ── the version is the server's
const server = read("server/main.py");
const serverVersion = server.match(/^PRIVACY_NOTICE_VERSION\s*=\s*"([^"]+)"/m);
yes(serverVersion !== null, "PRIVACY_NOTICE_VERSION not found in server/main.py");
eq(PRIVACY_NOTICE_VERSION, serverVersion![1], "the notice version the page shows and Register sends is the one the server accepts");
yes(/^\d{1,2} [A-Z][a-z]+ \d{4}$/.test(PRIVACY_NOTICE_EFFECTIVE_DATE), "the effective date is written out, like '10 November 2026'");

// ── structure: the sections the notice must have, in order, each with content, ids unique
eq(NOTICE_SECTIONS.map((s) => s.id), ["who-we-are", "what-we-collect", "lawful-basis", "who-sees-it", "how-long", "your-rights", "security", "version"], "sections, in order");
eq(new Set(NOTICE_SECTIONS.map((s) => s.id)).size, NOTICE_SECTIONS.length, "section ids are unique (they are anchors)");
for (const s of NOTICE_SECTIONS) yes(s.title.trim() !== "" && s.blocks.length > 0, `section ${s.id} has a title and content`);
const contactSections = NOTICE_SECTIONS.filter((s) => s.blocks.some((b) => b.kind === "contact")).map((s) => s.id);
eq(contactSections, ["who-we-are", "your-rights"], "the contact address appears where we say who to contact and how to use the rights");

// ── what it must say (each fact was verified against the code on 2026-11-10: change the notice and the version when one stops being true)
const text = noticeText();
const lower = text.toLowerCase();
for (const must of [
  "University of Batangas", "prototype", "Republic Act 10173", "National Privacy Commission", "privacy.gov.ph",
  "Brevo", "OpenStreetMap", "Nominatim", "Supabase", "Render", "Vercel",
  "bcrypt", "SHA-256", "local storage", "sets no cookies",
  "does not delete anything automatically", "no delete or anonymise function", "Deactivating a facility",
  "does not itself encrypt", "have not verified",
  "To be informed", "To access", "To correct", "To erasure or blocking", "To object", "To data portability",
  "tick a box", "version of the notice", `Version ${PRIVACY_NOTICE_VERSION}`,
  // messages (Messages redesign, 2026-11-11): one conversation per other facility, a message may be about no request, a read position is stored
  "conversation with another facility", "about no particular request", "how far your facility has read", "They are not shown how far you have read",
]) yes(lower.includes(must.toLowerCase()), `the notice must say: ${must}`);

// ── what it must NOT claim (the code does not do these, or nobody checked)
for (const claim of [
  "encrypted at rest", "encryption at rest", "at rest", "fully compliant", "compliant with", "in compliance", "guarantee", "100%", "military",
  "automatically deleted", "deleted after", "deleted within", "retained for", "kept for", "retention period of", "we anonymise", "we anonymize", "securely stored",
  "we never share", "never shared", "iso 27001",
]) yes(!lower.includes(claim), `the notice must not claim: ${claim}`);
yes(!/\b\d+\s*(days|months|years)\b/.test(lower.replace(/10 minutes|1 hour/g, "")), "no retention period in days, months or years: none is enforced by the code");

// ── the consent box and the contact-person line
yes(CONSENT_LABEL.link === "Privacy Notice" && /consent/.test(CONSENT_LABEL.before + CONSENT_LABEL.after), "the consent label links the Privacy Notice and says consent");
yes(CONTACT_PERSON_NOTICE.link === "Privacy Notice", "the contact-person line links the Privacy Notice");

// ── the route and the links (the repo has no component test runner: these read the sources)
const app = read("src/app/App.tsx");
const privacyAt = app.indexOf('window.location.pathname === "/privacy"');
const signedOutAt = app.search(/if \(!currentUser\) \{/); // the signed-out branch (an earlier `if (!currentUser) return;` is an effect)
yes(privacyAt > 0 && signedOutAt > 0 && privacyAt < signedOutAt, "/privacy is handled before the signed-in branches, so it works signed out and signed in");
yes(app.includes("./screens/PrivacyNotice"), "App.tsx loads the Privacy Notice screen");
for (const [file, why] of [["src/app/screens/Login.tsx", "sign-in"], ["src/app/screens/RegisterFacility.tsx", "Register"], ["src/app/components/AccountMenu.tsx", "the account menu"]]) {
  yes(read(file).includes('href="/privacy"'), `${why} links to /privacy`);
}
const register = read("src/app/screens/RegisterFacility.tsx");
yes(/type="checkbox"[^>]*\brequired\b/.test(register.replace(/\s+/g, " ")), "the Register consent box is a required checkbox");
yes(/useState\(false\)/.test(register) && /const \[consented, setConsented\] = useState\(false\)/.test(register), "the consent box starts unticked");
yes(register.includes("privacy_consent: consented") && register.includes("privacy_notice_version: PRIVACY_NOTICE_VERSION"), "Register sends the consent and the version");
yes(!/setConsented\(true\)/.test(register), "nothing ticks the box for the person");

// ── the page: landmarks and headings, and the loud banner when the address is missing
const screen = read("src/app/screens/PrivacyNotice.tsx");
yes(screen.includes("<main") && screen.includes("<h1") && screen.includes("<h2") && screen.includes("aria-labelledby"), "the page has main, h1, section headings and labelled sections");
yes(screen.includes('role="alert"') && screen.includes("VITE_PRIVACY_CONTACT_EMAIL"), "the page shows a visible alert when the contact address is not set");
yes(screen.includes("privacyContact(import.meta.env.VITE_PRIVACY_CONTACT_EMAIL)"), "the page reads the contact address from the build environment only");
yes(!/mailto:\s*[\w.+-]+@/.test(screen + read("src/app/lib/privacyNotice.ts")), "no address is written into the source");

console.log("ok");
