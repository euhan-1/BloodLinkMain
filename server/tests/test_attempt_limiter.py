"""AttemptLimiter (auth.py) — pure, fake clock, no DB. Run: python -m pytest tests/test_attempt_limiter.py"""
import unittest

from auth import AttemptLimiter


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


class AttemptLimiterTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.lim = AttemptLimiter(limit=5, window=60, clock=self.clock)

    def test_allows_up_to_limit_then_blocks_with_retry_after(self):
        for _ in range(5):
            self.assertEqual(self.lim.retry_after("k"), 0)
            self.lim.record("k")
        self.clock.t = 20
        self.assertEqual(self.lim.retry_after("k"), 40)

    def test_window_expiry_unblocks(self):
        for _ in range(5):
            self.lim.record("k")
        self.clock.t = 60
        self.assertEqual(self.lim.retry_after("k"), 0)

    def test_clear_resets_and_keys_are_independent(self):
        for _ in range(5):
            self.lim.record("a")
        self.assertGreater(self.lim.retry_after("a"), 0)
        self.assertEqual(self.lim.retry_after("b"), 0)
        self.lim.clear("a")
        self.assertEqual(self.lim.retry_after("a"), 0)


if __name__ == "__main__":
    unittest.main()
