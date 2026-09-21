import json
import os
import urllib.error
import urllib.request

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
RESEND_FROM_EMAIL = os.environ.get("RESEND_FROM_EMAIL", "BloodLink <onboarding@resend.dev>")
RESEND_API_URL = "https://api.resend.com/emails"


class EmailSendError(Exception):
    """Raised when Resend rejects or can't be reached. Deliberately never
    includes the email body in its own message — only Resend's HTTP error
    detail — so a caller that logs this exception can't accidentally leak
    a password-reset link into server logs."""


def send_email(to: str, subject: str, html: str) -> None:
    """Sends one transactional email via Resend's HTTP API (no SDK dependency
    — this is the only outbound email this app sends, so a single POST via
    the standard library is simpler than adding a new package for it).

    Raises EmailSendError on any non-2xx response or network failure. Callers
    decide whether that should surface to the user; this function itself
    never logs `html` (which may contain a raw, single-use token).
    """
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
