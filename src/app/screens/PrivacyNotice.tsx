import { useEffect } from "react";
import { BloodDropLogo } from "../components/BloodTypeBadge";
import { FOCUS } from "../lib/formStyles";
import {
  NOTICE_SECTIONS, PRIVACY_NOTICE_EFFECTIVE_DATE, PRIVACY_NOTICE_VERSION, privacyContact,
  type NoticeBlock,
} from "../lib/privacyNotice";

// ─── Privacy Notice (/privacy) ────────────────────────────────────────────────
// Reachable with no session, like /register and /reset-password: App.tsx renders it off window.location.pathname before it looks at
// the signed-in user. The text is data (lib/privacyNotice.ts) so scripts/check_privacy.ts can assert it; this file only lays it out.
//
// The contact address comes from VITE_PRIVACY_CONTACT_EMAIL. Unset (or a placeholder), the page says so in a banner at the top and where
// the address belongs, and `node scripts/check_privacy.ts` fails: a notice without a way to reach us must not ship by accident.

const LINK = `text-primary font-semibold underline underline-offset-2 hover:text-primary-hover rounded-sm ${FOCUS}`;

function Contact({ lead }: { lead: string }) {
  const contact = privacyContact(import.meta.env.VITE_PRIVACY_CONTACT_EMAIL);
  return (
    <p className="text-[15px] leading-relaxed text-foreground">
      {lead}{" "}
      {contact.ok ? (
        <a href={`mailto:${contact.email}`} className={LINK}>{contact.email}</a>
      ) : (
        <strong className="text-status-critical-text">[contact address not set: see the notice at the top of this page]</strong>
      )}
    </p>
  );
}

function Block({ block }: { block: NoticeBlock }) {
  switch (block.kind) {
    case "p":
      return <p className="text-[15px] leading-relaxed text-foreground">{block.text}</p>;
    case "h3":
      return <h3 className="text-[15px] font-semibold text-foreground mt-2">{block.text}</h3>;
    case "list":
      return (
        <ul className="list-disc pl-5 space-y-2 text-[15px] leading-relaxed text-foreground marker:text-muted-foreground">
          {block.items.map((item) => <li key={item}>{item}</li>)}
        </ul>
      );
    case "contact":
      return <Contact lead={block.lead} />;
  }
}

export function PrivacyNoticeScreen() {
  const contact = privacyContact(import.meta.env.VITE_PRIVACY_CONTACT_EMAIL);

  useEffect(() => {
    const before = document.title;
    document.title = "Privacy Notice - BloodLink";
    return () => { document.title = before; };
  }, []);

  return (
    <div className="min-h-screen bg-background">
      {!contact.ok && (
        <div role="alert" className="bg-status-critical-tint border-b-2 border-status-critical-border text-status-critical-text px-6 py-3 text-[14px] font-semibold text-center">
          This notice is incomplete: no privacy contact address has been set for this build (VITE_PRIVACY_CONTACT_EMAIL). Do not publish it like this.
        </div>
      )}
      <header className="max-w-3xl mx-auto px-6 pt-8 flex items-center justify-between gap-4">
        <a href="/" className={`flex items-center gap-3 rounded-sm ${FOCUS}`} aria-label="BloodLink home">
          <BloodDropLogo size={28} />
          <span className="text-lg font-bold text-foreground">Blood<span className="text-primary">Link</span></span>
        </a>
        <a href="/" className={`text-[14px] text-muted-foreground hover:text-foreground rounded-sm ${FOCUS}`}>Back to BloodLink</a>
      </header>

      <main className="max-w-3xl mx-auto px-6 pb-16">
        <h1 className="font-display text-3xl font-bold text-foreground mt-8 mb-2">Privacy Notice</h1>
        <p className="text-[15px] text-muted-foreground mb-6">
          How BloodLink handles personal information, under the Data Privacy Act of 2012 (Republic Act 10173). Version {PRIVACY_NOTICE_VERSION}, effective {PRIVACY_NOTICE_EFFECTIVE_DATE}.
        </p>

        <nav aria-label="On this page" className="mb-8 bg-card border border-border rounded-lg px-5 py-4">
          <p className="text-[13px] font-semibold text-foreground mb-2">On this page</p>
          <ol className="list-decimal pl-5 space-y-1 text-[14px]">
            {NOTICE_SECTIONS.map((s) => (
              <li key={s.id}><a href={`#${s.id}`} className={LINK}>{s.title}</a></li>
            ))}
          </ol>
        </nav>

        <div className="space-y-9">
          {NOTICE_SECTIONS.map((s) => (
            <section key={s.id} id={s.id} aria-labelledby={`${s.id}-title`} className="space-y-3 scroll-mt-6">
              <h2 id={`${s.id}-title`} className="font-display text-xl font-bold text-foreground">{s.title}</h2>
              {s.blocks.map((b, i) => <Block key={i} block={b} />)}
            </section>
          ))}
        </div>
      </main>
    </div>
  );
}
