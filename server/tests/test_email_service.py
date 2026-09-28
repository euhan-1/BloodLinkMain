"""email_service.py — provider selection (EMAIL_PROVIDER) and the SMTP path
added alongside the existing Resend one. Pure/mocked, no real network or DB:
smtplib.SMTP is patched throughout, so nothing here actually sends mail.

Run from the server/ directory:

    .venv\\Scripts\\python.exe -m unittest tests.test_email_service -v
"""

import contextlib
import io
import unittest
from unittest.mock import patch

import email_service


class ProviderSelectionTests(unittest.TestCase):
    """send_email() dispatches to the right underlying sender based on
    EMAIL_PROVIDER — patched directly on the module rather than via
    environment + reimport, since it's read once at import time."""

    def test_smtp_selected_when_provider_is_smtp(self):
        with patch.object(email_service, "EMAIL_PROVIDER", "smtp"), \
             patch.object(email_service, "_send_via_smtp") as mock_smtp, \
             patch.object(email_service, "_send_via_resend") as mock_resend:
            email_service.send_email(to="a@example.com", subject="s", html="<p>hi</p>")
        mock_smtp.assert_called_once_with("a@example.com", "s", "<p>hi</p>")
        mock_resend.assert_not_called()

    def test_resend_selected_when_provider_is_resend(self):
        with patch.object(email_service, "EMAIL_PROVIDER", "resend"), \
             patch.object(email_service, "_send_via_smtp") as mock_smtp, \
             patch.object(email_service, "_send_via_resend") as mock_resend:
            email_service.send_email(to="a@example.com", subject="s", html="<p>hi</p>")
        mock_resend.assert_called_once_with("a@example.com", "s", "<p>hi</p>")
        mock_smtp.assert_not_called()

    def test_unrecognized_value_falls_back_to_smtp(self):
        """Not "resend" -> smtp, whatever the value — smtp is the primary
        provider, so a typo or a stale value must not silently mean resend."""
        with patch.object(email_service, "EMAIL_PROVIDER", "sendgrid"), \
             patch.object(email_service, "_send_via_smtp") as mock_smtp, \
             patch.object(email_service, "_send_via_resend") as mock_resend:
            email_service.send_email(to="a@example.com", subject="s", html="<p>hi</p>")
        mock_smtp.assert_called_once()
        mock_resend.assert_not_called()

    def test_provider_is_logged(self):
        buf = io.StringIO()
        with patch.object(email_service, "EMAIL_PROVIDER", "smtp"), \
             patch.object(email_service, "_send_via_smtp"), \
             contextlib.redirect_stdout(buf):
            email_service.send_email(to="a@example.com", subject="s", html="<p>hi</p>")
        self.assertIn("provider=smtp", buf.getvalue())

    def test_smtp_password_never_appears_in_log_output(self):
        buf = io.StringIO()
        with patch.object(email_service, "EMAIL_PROVIDER", "smtp"), \
             patch.object(email_service, "SMTP_PASSWORD", "super-secret-app-password"), \
             patch.object(email_service, "_send_via_smtp"), \
             contextlib.redirect_stdout(buf):
            email_service.send_email(to="a@example.com", subject="s", html="<p>hi</p>")
        self.assertNotIn("super-secret-app-password", buf.getvalue())


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
