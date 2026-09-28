"""CORS origin parsing (main.py) — pure, no DB. Run: python -m pytest tests/test_cors_origins.py"""
import unittest

from main import _parse_cors_origins


class CorsOriginsTests(unittest.TestCase):
    def test_unset_fails_closed(self):
        self.assertEqual(_parse_cors_origins(None), [])

    def test_blank_fails_closed(self):
        self.assertEqual(_parse_cors_origins("   "), [])

    def test_never_defaults_to_wildcard(self):
        self.assertNotIn("*", _parse_cors_origins(None))

    def test_single_origin_normalized(self):
        self.assertEqual(_parse_cors_origins(" HTTPS://example.com/ "), ["https://example.com"])

    def test_multiple_comma_separated(self):
        self.assertEqual(
            _parse_cors_origins("http://localhost:5173,https://app.example.com"),
            ["http://localhost:5173", "https://app.example.com"],
        )


if __name__ == "__main__":
    unittest.main()
