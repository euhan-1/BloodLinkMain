// The Privacy Notice (RA 10173) as data: the /privacy page renders it, scripts/check_privacy.ts asserts it.
//
// Every statement here was checked against the code, the schema or the running system on 2026-11-10 (see CLAUDE.md, "Privacy notice").
// Nothing is promised that the code does not do. Where something is not true yet it says so, and where it is not known it is left out.
// When a statement stops being true (a retention job, an anonymise function, a new third party), change it here AND raise the version.
//
// Pure on purpose (no import.meta.env, no React): the check script runs it under plain Node. The contact address comes from the build's
// environment in screens/PrivacyNotice.tsx and goes through privacyContact() below.

/** Must equal PRIVACY_NOTICE_VERSION in server/main.py (check_privacy.ts compares the two). Raise both when the notice changes. */
export const PRIVACY_NOTICE_VERSION = "1.0";
export const PRIVACY_NOTICE_EFFECTIVE_DATE = "10 November 2026";

/** A paragraph, a list, a small heading, or the place where the contact address goes. */
export type NoticeBlock =
  | { kind: "p"; text: string }
  | { kind: "h3"; text: string }
  | { kind: "list"; items: string[] }
  | { kind: "contact"; lead: string };

export type NoticeSection = { id: string; title: string; blocks: NoticeBlock[] };

export type PrivacyContact = { ok: true; email: string } | { ok: false };

// An address that is only a stand-in must not pass for a real one: the reserved example domains and top-level names no mailbox can have.
const PLACEHOLDER_DOMAINS = ["example.com", "example.org", "example.net"];
const PLACEHOLDER_TLDS = ["test", "invalid", "example", "localhost"];

/** The contact address from VITE_PRIVACY_CONTACT_EMAIL, or { ok: false } when it is unset, not an address, or an obvious placeholder. */
export function privacyContact(raw: string | undefined): PrivacyContact {
  const email = (raw ?? "").trim();
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) return { ok: false };
  const domain = email.split("@")[1].toLowerCase();
  const tld = domain.split(".").pop() ?? "";
  if (PLACEHOLDER_DOMAINS.includes(domain) || PLACEHOLDER_TLDS.includes(tld)) return { ok: false };
  return { ok: true, email };
}

/** The words next to the Register form's consent box; the link goes to /privacy. */
export const CONSENT_LABEL = {
  before: "I have read the ",
  link: "Privacy Notice",
  after: " and I consent to BloodLink collecting and using the details on this form as it describes.",
};

/** The one-line notice by the contact person's name and phone number (the only place another person's details are entered). */
export const CONTACT_PERSON_NOTICE = {
  before: "You are giving us this person's name and phone number, so let them know. See the ",
  link: "Privacy Notice",
  after: ".",
};

export const NOTICE_SECTIONS: NoticeSection[] = [
  {
    id: "who-we-are",
    title: "Who we are and who to contact",
    blocks: [
      {
        kind: "p",
        text: "BloodLink is a blood-supply and emergency-request prototype, built as a Bachelor of Science in Computer Science thesis at the University of Batangas. It is a student project. It is not a live service run by a hospital, a blood bank or the government.",
      },
      {
        kind: "p",
        text: "The thesis team decides what BloodLink collects and why, so we are responsible for it. Please do not enter real patient details.",
      },
      { kind: "contact", lead: "Questions, requests about your data, or complaints:" },
    ],
  },
  {
    id: "what-we-collect",
    title: "What we collect and why",
    blocks: [
      {
        kind: "list",
        items: [
          "Your facility: its name, type (hospital or blood bank), address, map position (latitude and longitude) and an optional department. We use these to run the service and to let other facilities find yours when they need blood.",
          "Your sign-in: a work email address and a password. We use the email to sign you in and to send verification codes, password reset links and notices about your registration. The password is stored only as a bcrypt hash.",
          "The contact person on a registration: a name and a phone number, entered only on the Register form. An administrator uses them to review the application, and they are kept with the application afterwards.",
          "Blood stock and transfers: the units you record or upload, your stock limits, and requests between facilities, including when each step happened and the email of the account that accepted, released or received a transfer.",
          "Messages and notifications: what you type in a request's chat, and the notices BloodLink creates for your facility. A chat is free text and BloodLink does not check it, so do not put patient names or other personal details in it.",
          "Uploaded files: for stock uploads we keep the original text of the file so you can download it again, and a record of which account uploaded it and when.",
          "Donor lists (blood banks only): if a blood bank uploads a donor list, we store each donor's name, blood type and phone number for that bank. Blood type is health information, which the Data Privacy Act treats as sensitive. BloodLink does not collect this from donors themselves and does not record their consent, and for now it simulates donor messages instead of sending real ones. The bank that uploads a list is responsible for having the donors' permission.",
          "In your browser: a sign-in token and your basic account details (email, facility name and type) in the browser's local storage, your light or dark theme, and which requests you have already seen. Our code sets no cookies and BloodLink uses no advertising or analytics trackers.",
          "While the service runs: to slow down password guessing, the server keeps the email address and network address of recent sign-in and email attempts in memory for a short time. If sending an email fails, the address may be written to the server log. Our hosting providers keep their own logs (see below).",
        ],
      },
    ],
  },
  {
    id: "lawful-basis",
    title: "Why we may use it",
    blocks: [
      {
        kind: "p",
        text: "When you register you tick a box to say you have read this notice and consent. We record which version of the notice you agreed to, and when.",
      },
      {
        kind: "p",
        text: "Some use of your data is also needed to give you the service you signed up for: signing you in, showing your facility to the facilities that need blood, and keeping a record of transfers.",
      },
      {
        kind: "p",
        text: "Accounts created before this notice existed, and accounts an administrator created without the Register form, have no consent on file yet.",
      },
    ],
  },
  {
    id: "who-sees-it",
    title: "Who can see it and who receives it",
    blocks: [
      { kind: "h3", text: "Inside BloodLink" },
      {
        kind: "list",
        items: [
          "Other signed-in facilities see your facility's name, type, address and map position, in the search for blood and on the map. On a request you share with another facility, they also see the quantities, the chat, and the email of the account that accepted, released or received it.",
          "Your own facility sees its stock, requests, notifications and uploads. Each facility has one login.",
          "BloodLink administrators see registration applications (facility name, address, contact person, phone and email), every facility, and the email of each account.",
          "The thesis team runs the database and can read everything stored in it.",
          "A donor list is visible only to the blood bank that uploaded it.",
        ],
      },
      { kind: "h3", text: "Outside BloodLink: services that receive data" },
      {
        kind: "list",
        items: [
          "Brevo sends our emails. It receives the recipient's address and the message: your facility name, verification codes, password reset links, and approval or rejection notices (including the reason for a rejection).",
          "OpenStreetMap, through its Nominatim search, receives the address you type to find a map position. On the Register form our server sends it. On the profile screens your browser sends it directly, so OpenStreetMap also sees your network address. Map pictures are loaded from OpenStreetMap's servers, which see your network address and the part of the map you look at.",
          "Supabase stores our database. According to our project notes its servers are in Tokyo, Japan, which is outside the Philippines.",
          "Render runs our server, and Vercel serves the web pages and passes your requests on to Render. Both handle everything you send and keep their own logs. We have not checked what they record or for how long.",
        ],
      },
      { kind: "p", text: "We do not sell your data and we do not send it to advertisers." },
    ],
  },
  {
    id: "how-long",
    title: "How long we keep it, and what deletion means here",
    blocks: [
      {
        kind: "p",
        text: "BloodLink does not delete anything automatically. No retention period is built in, so records stay until someone removes them by hand. That includes registration applications, approved or rejected (with their contact person, phone and hashed password), used and expired sign-in codes and reset records, chats, notifications, uploaded files and transfer records.",
      },
      {
        kind: "p",
        text: "Deactivating a facility stops its account from signing in. It does not delete or anonymise anything. BloodLink has no delete or anonymise function yet.",
      },
      {
        kind: "p",
        text: "To have your data erased, write to us and an administrator will do it by hand. Some records may have to stay because they are also another facility's record of a transfer. We will tell you what we removed and what we kept. Our hosting providers' logs follow their own rules.",
      },
    ],
  },
  {
    id: "your-rights",
    title: "Your rights",
    blocks: [
      { kind: "p", text: "The Data Privacy Act of 2012 (Republic Act 10173) gives you these rights:" },
      {
        kind: "list",
        items: [
          "To be informed: that is what this notice is for.",
          "To access: ask for a copy of what we hold about you.",
          "To correct: your facility's address, map position and department can be changed in the account menu, under Edit facility profile. For anything else, ask us.",
          "To erasure or blocking: ask us to remove your data or to stop using it. Deactivating an account blocks sign-in.",
          "To object: tell us if you object to how your data is used.",
          "To data portability: ask for your data in a common file format. There is no export button yet, so we would do it by hand.",
        ],
      },
      { kind: "contact", lead: "To use any of these, write to:" },
      {
        kind: "p",
        text: "Apart from correcting your address, map position and department, these are done by hand, so they take time. You can also complain to the National Privacy Commission (privacy.gov.ph).",
      },
    ],
  },
  {
    id: "security",
    title: "Security measures in place",
    blocks: [
      {
        kind: "list",
        items: [
          "Passwords are stored as bcrypt hashes, so nobody at BloodLink can read them. Verification codes and password reset links are stored only as SHA-256 hashes. Codes expire after 10 minutes and links after 1 hour.",
          "Sign-in attempts, and the requests that send you an email (registration and password reset), are rate-limited per account and per network address, to slow down guessing. A verification code allows only 5 wrong guesses.",
          "Every request after sign-in checks that the account still exists and its facility is still active, so a deactivated facility loses access. A facility can read only its own stock and requests, apart from what is shared as described above. Administrator functions need an administrator account.",
          "Your browser talks to our server over HTTPS (we confirmed that the server redirects plain HTTP to HTTPS), and the server is set up to require an encrypted connection to the database.",
          "A sign-in token lasts 24 hours and is kept in your browser. Logging out deletes it from your browser, but the server cannot cancel it earlier, so log out on shared computers.",
        ],
      },
      {
        kind: "p",
        text: "What we cannot promise: BloodLink does not itself encrypt the data it stores, and we have not verified how Supabase, Render or Vercel protect it. This is a student prototype, not a hardened production system.",
      },
    ],
  },
  {
    id: "version",
    title: "Effective date and version",
    blocks: [
      { kind: "p", text: `Version ${PRIVACY_NOTICE_VERSION}, effective ${PRIVACY_NOTICE_EFFECTIVE_DATE}.` },
      {
        kind: "p",
        text: "When this notice changes, the version number changes. The version you agreed to is stored with your account.",
      },
    ],
  },
];

/** Every sentence of the notice as one string (for the check script's wording rules). */
export function noticeText(): string {
  return NOTICE_SECTIONS.flatMap((s) => [
    s.title,
    ...s.blocks.flatMap((b) => (b.kind === "list" ? b.items : b.kind === "contact" ? [b.lead] : [b.text])),
  ]).join("\n");
}
