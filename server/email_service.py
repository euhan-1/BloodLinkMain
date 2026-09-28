import html as html_module
import json
import os
import re
import smtplib
import urllib.error
import urllib.request
from email.message import EmailMessage

# "resend" is the only other recognized value; anything else (unset, typo,
# empty string) means smtp — smtp is the primary provider now, so a missing
# or mistyped variable must not silently fall back to the provider we're no
# longer using.
EMAIL_PROVIDER = os.environ.get("EMAIL_PROVIDER", "smtp").strip().lower()

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
RESEND_FROM_EMAIL = os.environ.get("RESEND_FROM_EMAIL", "BloodLink <onboarding@resend.dev>")
RESEND_API_URL = "https://api.resend.com/emails"

SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
MAIL_FROM = os.environ.get("MAIL_FROM", "")
MAIL_FROM_NAME = os.environ.get("MAIL_FROM_NAME", "BloodLink")

# A live Gmail-style SMTP round trip (connect, EHLO, STARTTLS, EHLO, LOGIN,
# MAIL/RCPT/DATA, QUIT) normally completes in well under a second per step.
# 5s per socket operation (smtplib applies this timeout to the connection
# and to every subsequent read) is enough slack for a slow network without
# letting a hung server eat into the 120s Vercel proxy ceiling — this is
# "hanging," not "slow," that we're guarding against.
SMTP_TIMEOUT_SECONDS = 5


class EmailSendError(Exception):
    """Raised when either provider rejects the message or can't be reached.
    Deliberately never includes the email body, and never the raw SMTP
    password, in its own message — only the provider's own error detail —
    so a caller that logs this exception can't accidentally leak a
    password-reset link, an OTP code, or SMTP_PASSWORD into server logs."""


def _html_to_plain_text(html_content: str) -> str:
    """Crude but sufficient stdlib-only HTML -> plain text: block-level tags
    become line breaks, everything else is stripped, entities are unescaped.
    Good enough for a text/plain alternative part — the point isn't a
    faithful rendering, it's that mail from a Gmail address with no text
    part at all is far more likely to be filtered as spam than one with a
    slightly rough plain-text fallback."""
    text = re.sub(r"<br\s*/?>", "\n", html_content, flags=re.IGNORECASE)
    text = re.sub(r"</(p|div|h[1-6]|li)\s*>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = html_module.unescape(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def send_email(to: str, subject: str, html: str) -> None:
    """Sends one transactional email via whichever provider EMAIL_PROVIDER
    selects — "resend" for Resend's HTTP API, anything else (including
    unset) for SMTP. No SDK dependency for either path: a single
    urllib.request POST for Resend, stdlib smtplib for SMTP.

    Raises EmailSendError on any failure from either provider. Callers
    decide whether that should surface to the user; this function itself
    never logs `html` (which may contain a raw, single-use token or OTP
    code) or SMTP_PASSWORD.
    """
    provider = "resend" if EMAIL_PROVIDER == "resend" else "smtp"
    # Logged unconditionally, before the attempt — so if the send below
    # raises, the log still answers "which provider did this even try,"
    # which is the actual question the next time mail goes missing.
    print(f"[email_service] sending email via provider={provider}")

    if provider == "resend":
        _send_via_resend(to, subject, html)
    else:
        _send_via_smtp(to, subject, html)


def _send_via_resend(to: str, subject: str, html: str) -> None:
    if not RESEND_API_KEY:
        raise EmailSendError("RESEND_API_KEY is not configured")

    payload = json.dumps({
        "from": RESEND_FROM_EMAIL,
        "to": [to],
        "subject": subject,
        "html": html,
    }).encode("utf-8")

    req = urllib.request.Request(
        RESEND_API_URL,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
            # Resend's API sits behind Cloudflare, which blocks the default
            # "Python-urllib/x.y" User-Agent as a bot signature (Cloudflare
            # error 1010) before the request ever reaches Resend itself — a
            # real-looking UA is enough to get past it.
            "User-Agent": "BloodLink-Server/1.0 (+https://github.com/euhan-1/bloodlink)",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
    except urllib.error.HTTPError as e:
        # Resend's error body describes the request (bad "to", bad "from"
        # domain, etc.) but never echoes back the html we sent, so it's safe
        # to include verbatim here.
        detail = e.read().decode("utf-8", errors="replace")
        raise EmailSendError(f"Resend API error {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise EmailSendError(f"failed to reach Resend: {e.reason}") from e


def _send_via_smtp(to: str, subject: str, html: str) -> None:
    if not SMTP_HOST or not MAIL_FROM:
        raise EmailSendError("SMTP_HOST/MAIL_FROM is not configured")

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = f"{MAIL_FROM_NAME} <{MAIL_FROM}>" if MAIL_FROM_NAME else MAIL_FROM
    message["To"] = to
    # Plain text first (set_content), HTML as the alternative — an HTML-only
    # message from a Gmail address is a much stronger spam signal than one
    # with a text part alongside it.
    message.set_content(_html_to_plain_text(html))
    message.add_alternative(html, subtype="html")

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT_SECONDS) as server:
            server.starttls()
            if SMTP_USER:
                server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(message)
    except (smtplib.SMTPException, OSError) as e:
        # smtplib's own exceptions carry the SMTP server's response code/text
        # (e.g. "535 Authentication failed") — never the password we sent,
        # and never the message body, so this is safe to include verbatim.
        raise EmailSendError(f"SMTP send failed: {e}") from e
