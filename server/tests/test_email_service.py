"""email_service.py — provider selection (EMAIL_PROVIDER) and the SMTP/Brevo
paths added alongside the original Resend one. Pure/mocked, no real network
or DB: smtplib.SMTP and urllib.request.urlopen are patched throughout, so
nothing here actually sends mail.

Run from the server/ directory:

    .venv\\Scripts\\python.exe -m unittest tests.test_email_service -v
"""

import contextlib
import io
import json
import unittest
import urllib.error
from unittest.mock import patch

import email_service


class ProviderSelectionTests(unittest.TestCase):
    """send_email() dispatches to the right underlying sender based on
    EMAIL_PROVIDER — patched directly on the module rather than via
    environment + reimport, since it's read once at import time."""

    def test_smtp_selected_when_provider_is_smtp(self):
        with patch.object(email_service, "EMAIL_PROVIDER", "smtp"), \
             patch.object(email_service, "_send_via_smtp") as mock_smtp, \
             patch.object(email_service, "_send_via_resend") as mock_resend, \
             patch.object(email_service, "_send_via_brevo") as mock_brevo:
            email_service.send_email(to="a@example.com", subject="s", html="<p>hi</p>")
        mock_smtp.assert_called_once_with("a@example.com", "s", "<p>hi</p>")
        mock_resend.assert_not_called()
        mock_brevo.assert_not_called()

    def test_resend_selected_when_provider_is_resend(self):
        with patch.object(email_service, "EMAIL_PROVIDER", "resend"), \
             patch.object(email_service, "_send_via_smtp") as mock_smtp, \
             patch.object(email_service, "_send_via_resend") as mock_resend, \
             patch.object(email_service, "_send_via_brevo") as mock_brevo:
            email_service.send_email(to="a@example.com", subject="s", html="<p>hi</p>")
        mock_resend.assert_called_once_with("a@example.com", "s", "<p>hi</p>")
        mock_smtp.assert_not_called()
        mock_brevo.assert_not_called()

    def test_brevo_selected_when_provider_is_brevo(self):
        with patch.object(email_service, "EMAIL_PROVIDER", "brevo"), \
             patch.object(email_service, "_send_via_smtp") as mock_smtp, \
             patch.object(email_service, "_send_via_resend") as mock_resend, \
             patch.object(email_service, "_send_via_brevo") as mock_brevo:
            email_service.send_email(to="a@example.com", subject="s", html="<p>hi</p>")
        mock_brevo.assert_called_once_with("a@example.com", "s", "<p>hi</p>")
        mock_smtp.assert_not_called()
        mock_resend.assert_not_called()

    def test_unrecognized_or_unset_value_falls_back_to_brevo(self):
        """Not "resend" and not "smtp" -> brevo, whatever the value —
        Render blocks outbound SMTP entirely, so a typo or a missing
        variable must not silently mean a provider that can never succeed
        on that host."""
        with patch.object(email_service, "EMAIL_PROVIDER", "sendgrid"), \
             patch.object(email_service, "_send_via_smtp") as mock_smtp, \
             patch.object(email_service, "_send_via_resend") as mock_resend, \
             patch.object(email_service, "_send_via_brevo") as mock_brevo:
            email_service.send_email(to="a@example.com", subject="s", html="<p>hi</p>")
        mock_brevo.assert_called_once()
        mock_smtp.assert_not_called()
        mock_resend.assert_not_called()

    def test_provider_is_logged(self):
        buf = io.StringIO()
        with patch.object(email_service, "EMAIL_PROVIDER", "brevo"), \
             patch.object(email_service, "_send_via_brevo"), \
             contextlib.redirect_stdout(buf):
            email_service.send_email(to="a@example.com", subject="s", html="<p>hi</p>")
        self.assertIn("provider=brevo", buf.getvalue())

    def test_smtp_password_never_appears_in_log_output(self):
        buf = io.StringIO()
        with patch.object(email_service, "EMAIL_PROVIDER", "smtp"), \
             patch.object(email_service, "SMTP_PASSWORD", "super-secret-app-password"), \
             patch.object(email_service, "_send_via_smtp"), \
             contextlib.redirect_stdout(buf):
            email_service.send_email(to="a@example.com", subject="s", html="<p>hi</p>")
        self.assertNotIn("super-secret-app-password", buf.getvalue())

    def test_brevo_api_key_never_appears_in_log_output(self):
        buf = io.StringIO()
        with patch.object(email_service, "EMAIL_PROVIDER", "brevo"), \
             patch.object(email_service, "BREVO_API_KEY", "super-secret-brevo-key"), \
             patch.object(email_service, "_send_via_brevo"), \
             contextlib.redirect_stdout(buf):
            email_service.send_email(to="a@example.com", subject="s", html="<p>hi</p>")
        self.assertNotIn("super-secret-brevo-key", buf.getvalue())


class SmtpSendTests(unittest.TestCase):
    def test_missing_config_raises_email_send_error(self):
        with patch.object(email_service, "SMTP_HOST", ""):
            with self.assertRaises(email_service.EmailSendError):
                email_service._send_via_smtp("to@example.com", "subject", "<p>hi</p>")

    def test_connection_failure_surfaces_as_email_send_error_not_oserror(self):
        with patch.object(email_service, "SMTP_HOST", "smtp.example.com"), \
             patch.object(email_service, "MAIL_FROM", "sender@example.com"), \
             patch("email_service.smtplib.SMTP") as mock_smtp_cls:
            mock_smtp_cls.return_value.__enter__.side_effect = OSError("connection refused")
            with self.assertRaises(email_service.EmailSendError):
                email_service._send_via_smtp("to@example.com", "subject", "<p>hi</p>")

    def test_smtp_exception_surfaces_as_email_send_error(self):
        import smtplib
        with patch.object(email_service, "SMTP_HOST", "smtp.example.com"), \
             patch.object(email_service, "MAIL_FROM", "sender@example.com"), \
             patch.object(email_service, "SMTP_USER", "user@example.com"), \
             patch("email_service.smtplib.SMTP") as mock_smtp_cls:
            server = mock_smtp_cls.return_value.__enter__.return_value
            server.login.side_effect = smtplib.SMTPAuthenticationError(535, b"Authentication failed")
            with self.assertRaises(email_service.EmailSendError):
                email_service._send_via_smtp("to@example.com", "subject", "<p>hi</p>")

    def test_successful_send_uses_starttls_and_login(self):
        with patch.object(email_service, "SMTP_HOST", "smtp.example.com"), \
             patch.object(email_service, "MAIL_FROM", "sender@example.com"), \
             patch.object(email_service, "SMTP_USER", "user@example.com"), \
             patch.object(email_service, "SMTP_PASSWORD", "app-password"), \
             patch("email_service.smtplib.SMTP") as mock_smtp_cls:
            server = mock_smtp_cls.return_value.__enter__.return_value
            email_service._send_via_smtp("to@example.com", "subject", "<p>hi</p>")

        mock_smtp_cls.assert_called_once_with("smtp.example.com", email_service.SMTP_PORT, timeout=email_service.SMTP_TIMEOUT_SECONDS)
        server.starttls.assert_called_once()
        server.login.assert_called_once_with("user@example.com", "app-password")
        server.send_message.assert_called_once()

    def test_login_skipped_when_no_smtp_user(self):
        with patch.object(email_service, "SMTP_HOST", "smtp.example.com"), \
             patch.object(email_service, "MAIL_FROM", "sender@example.com"), \
             patch.object(email_service, "SMTP_USER", ""), \
             patch("email_service.smtplib.SMTP") as mock_smtp_cls:
            server = mock_smtp_cls.return_value.__enter__.return_value
            email_service._send_via_smtp("to@example.com", "subject", "<p>hi</p>")
        server.login.assert_not_called()

    def test_message_is_multipart_with_text_and_html_parts(self):
        captured = {}
        with patch.object(email_service, "SMTP_HOST", "smtp.example.com"), \
             patch.object(email_service, "MAIL_FROM", "sender@example.com"), \
             patch("email_service.smtplib.SMTP") as mock_smtp_cls:
            server = mock_smtp_cls.return_value.__enter__.return_value
            server.send_message.side_effect = lambda msg: captured.setdefault("msg", msg)
            email_service._send_via_smtp("to@example.com", "subject", "<p>Hello there</p>")

        message = captured["msg"]
        self.assertTrue(message.is_multipart())
        content_types = {part.get_content_type() for part in message.walk()}
        self.assertIn("text/plain", content_types)
        self.assertIn("text/html", content_types)


class BrevoSendTests(unittest.TestCase):
    """Mirrors SmtpSendTests / the Resend path's own shape — urllib.request
    is mocked, nothing hits Brevo's real API."""

    def test_missing_api_key_raises_email_send_error(self):
        with patch.object(email_service, "BREVO_API_KEY", ""), \
             patch.object(email_service, "MAIL_FROM", "sender@example.com"):
            with self.assertRaises(email_service.EmailSendError):
                email_service._send_via_brevo("to@example.com", "subject", "<p>hi</p>")

    def test_missing_mail_from_raises_email_send_error(self):
        with patch.object(email_service, "BREVO_API_KEY", "key123"), \
             patch.object(email_service, "MAIL_FROM", ""):
            with self.assertRaises(email_service.EmailSendError):
                email_service._send_via_brevo("to@example.com", "subject", "<p>hi</p>")

    def test_http_error_response_body_is_included_in_message(self):
        """This is what made a prior 403 diagnosable from the logs alone —
        the same reasoning the Resend path already relies on."""
        error_body = b'{"code":"unauthorized","message":"Key not found"}'
        with patch.object(email_service, "BREVO_API_KEY", "bad-key"), \
             patch.object(email_service, "MAIL_FROM", "sender@example.com"), \
             patch("email_service.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = urllib.error.HTTPError(
                url=email_service.BREVO_API_URL, code=403, msg="Forbidden",
                hdrs=None, fp=io.BytesIO(error_body),
            )
            with self.assertRaises(email_service.EmailSendError) as ctx:
                email_service._send_via_brevo("to@example.com", "subject", "<p>hi</p>")
        self.assertIn("403", str(ctx.exception))
        self.assertIn("Key not found", str(ctx.exception))

    def test_network_failure_surfaces_as_email_send_error(self):
        with patch.object(email_service, "BREVO_API_KEY", "key123"), \
             patch.object(email_service, "MAIL_FROM", "sender@example.com"), \
             patch("email_service.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = urllib.error.URLError("network unreachable")
            with self.assertRaises(email_service.EmailSendError):
                email_service._send_via_brevo("to@example.com", "subject", "<p>hi</p>")

    def test_successful_send_posts_expected_payload_and_headers(self):
        with patch.object(email_service, "BREVO_API_KEY", "key123"), \
             patch.object(email_service, "MAIL_FROM", "sender@example.com"), \
             patch.object(email_service, "MAIL_FROM_NAME", "BloodLink"), \
             patch("email_service.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value.__enter__.return_value.read.return_value = b"{}"
            email_service._send_via_brevo("to@example.com", "subject", "<p>Hello</p>")

        args, kwargs = mock_urlopen.call_args
        req = args[0]
        self.assertEqual(kwargs.get("timeout"), email_service.HTTP_PROVIDER_TIMEOUT_SECONDS)
        self.assertEqual(req.full_url, email_service.BREVO_API_URL)
        self.assertEqual(req.get_header("Api-key"), "key123")

        payload = json.loads(req.data)
        self.assertEqual(payload["sender"], {"email": "sender@example.com", "name": "BloodLink"})
        self.assertEqual(payload["to"], [{"email": "to@example.com"}])
        self.assertEqual(payload["htmlContent"], "<p>Hello</p>")
        self.assertIn("Hello", payload["textContent"])

    def test_api_key_never_appears_in_error_message(self):
        with patch.object(email_service, "BREVO_API_KEY", "super-secret-brevo-key"), \
             patch.object(email_service, "MAIL_FROM", "sender@example.com"), \
             patch("email_service.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = urllib.error.URLError("network unreachable")
            with self.assertRaises(email_service.EmailSendError) as ctx:
                email_service._send_via_brevo("to@example.com", "subject", "<p>hi</p>")
        self.assertNotIn("super-secret-brevo-key", str(ctx.exception))


class HtmlToPlainTextTests(unittest.TestCase):
    """Pure — no DB, no network."""

    def test_strips_tags(self):
        result = email_service._html_to_plain_text("<div><p>Hello</p></div>")
        self.assertNotIn("<", result)
        self.assertIn("Hello", result)

    def test_unescapes_entities(self):
        result = email_service._html_to_plain_text("<p>Terms &amp; Conditions</p>")
        self.assertIn("Terms & Conditions", result)

    def test_br_becomes_newline(self):
        result = email_service._html_to_plain_text("Line one<br>Line two")
        self.assertIn("Line one\nLine two", result)


if __name__ == "__main__":
    unittest.main()
