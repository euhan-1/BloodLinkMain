"""Unit tests for the restock-recommendation translation layer
(_compute_restock_recommendation / _restock_status in main.py) — the plain-
language "restock N units by <date>" recommendation the Dashboard's forecast
card now leads with.

This is deliberately pure-function, DB-free, unlike test_expired_inventory.py
(same style as test_prediction_interval.py): _compute_restock_recommendation
takes an already-computed point forecast (a plain dict, not a live SARIMAX/
linear-trend fit) and a facility's min/max thresholds, and returns a
recommendation or None. It never re-derives or re-fits a forecast itself, so
these tests don't touch the DB or the forecasting math at all — every
point_by_checkpoint dict below is hand-constructed, and every expected value
is hand-derived in the comment above it, not obtained by calling the function
under test.

Run from the server/ directory:

    .venv\\Scripts\\python.exe -m unittest tests.test_restock_recommendation -v
"""

import unittest
from datetime import date

from main import FORECAST_CHECKPOINTS, _compute_restock_recommendation, _restock_status

TODAY = date(2026, 9, 23)


class ComputeRestockRecommendationTests(unittest.TestCase):
    def test_on_track_when_point_forecast_never_dips_below_minimum(self):
        # Every checkpoint stays at/above the 40-unit minimum.
        points = {0: 60, 5: 55, 10: 50, 15: 48, 20: 45, 25: 43, 30: 41}
        self.assertEqual(set(points), set(FORECAST_CHECKPOINTS))
        result = _compute_restock_recommendation("O+", points, minimum=40, maximum=100, today=TODAY)
        self.assertIsNone(result)

    def test_already_below_minimum_today_breaches_at_day_zero(self):
        # day0 (30) is already below the 40-unit minimum — no "days until"
        # to interpolate, so this must resolve to today, not some fraction
        # of the way through the first 5-day gap.
        # Low point across the whole window is day5 (28), even though it
        # dips before partially recovering afterward (32, 35, 38, 42, 45) —
        # recommended_units must be sized to that 28, not day0's 30.
        points = {0: 30, 5: 28, 10: 32, 15: 35, 20: 38, 25: 42, 30: 45}
        result = _compute_restock_recommendation("B-", points, minimum=40, maximum=80, today=TODAY)
        self.assertIsNotNone(result)
        self.assertEqual(result["blood_type"], "B-")
        self.assertEqual(result["days_until_breach"], 0)
        self.assertEqual(result["breach_date"], TODAY.isoformat())
        # recommended_units = ceil(maximum - lowest point in window) = ceil(80 - 28) = 52
        self.assertEqual(result["recommended_units"], 52)

    def test_interpolates_fractional_days_until_breach_between_checkpoints(self):
        # Monotonic decline, minimum=40:
        #   day0=60 day5=52 day10=44 day15=34 day20=26 day25=20 day30=16
        # First checkpoint under 40 is day15 (34); day10 (44) is still clear.
        # frac = (prev_units - minimum) / (prev_units - breach_units)
        #      = (44 - 40) / (44 - 34) = 4/10 = 0.4
        # days_until_breach = 10 + 0.4 * (15 - 10) = 12.0 exactly
        points = {0: 60, 5: 52, 10: 44, 15: 34, 20: 26, 25: 20, 30: 16}
        result = _compute_restock_recommendation("A+", points, minimum=40, maximum=80, today=TODAY)
        self.assertIsNotNone(result)
        self.assertEqual(result["days_until_breach"], 12)
        self.assertEqual(result["breach_date"], date(2026, 10, 5).isoformat())  # TODAY + 12 days
        # Low point across the window is day30 (16): ceil(80 - 16) = 64
        self.assertEqual(result["recommended_units"], 64)

    def test_recommended_units_uses_worst_point_in_window_not_the_breach_point(self):
        # minimum=40. day10=42 is still clear; day15=39 is the first breach
        # (prev=day10). But the trajectory keeps falling to day20=25 (the
        # true low point of the whole window) before partially recovering.
        # A recommendation sized only to the breach-point value (39) would
        # under-stock for the actual worst point (25) — this is exactly the
        # scenario the "worst point in window" rule exists to cover.
        points = {0: 55, 5: 50, 10: 42, 15: 39, 20: 25, 25: 33, 30: 36}
        result = _compute_restock_recommendation("O-", points, minimum=40, maximum=80, today=TODAY)
        self.assertIsNotNone(result)
        # frac = (42 - 40) / (42 - 39) = 2/3; days_until_breach = 10 + (2/3)*5 = 13.333... -> round() = 13
        self.assertEqual(result["days_until_breach"], 13)
        # ceil(80 - 25) = 55, not ceil(80 - 39) = 41
        self.assertEqual(result["recommended_units"], 55)

    def test_recommended_units_rounds_up_a_fractional_gap(self):
        # Low point in window is 24.5 (a fractional point estimate, e.g. from
        # an average). maximum=80. 80 - 24.5 = 55.5 -> math.ceil -> 56, never
        # rounded down to 55 (recommendations round up, not to nearest).
        points = {0: 60, 5: 50, 10: 39, 15: 24.5, 20: 30, 25: 35, 30: 38}
        result = _compute_restock_recommendation("AB+", points, minimum=40, maximum=80, today=TODAY)
        self.assertIsNotNone(result)
        self.assertEqual(result["recommended_units"], 56)

    def test_no_minimum_threshold_configured_returns_none(self):
        points = {0: 10, 5: 8, 10: 6, 15: 4, 20: 2, 25: 0, 30: 0}
        result = _compute_restock_recommendation("AB-", points, minimum=None, maximum=50, today=TODAY)
        self.assertIsNone(result)

    def test_no_maximum_threshold_configured_returns_none(self):
        # Even though this type is clearly breaching, there's no target
        # ceiling to size a recommendation against, so this must stay honest
        # and return nothing rather than guessing a target.
        points = {0: 10, 5: 8, 10: 6, 15: 4, 20: 2, 25: 0, 30: 0}
        result = _compute_restock_recommendation("AB-", points, minimum=40, maximum=None, today=TODAY)
        self.assertIsNone(result)

    def test_recommended_units_never_negative(self):
        # A pathological/misconfigured threshold pair (maximum below the
        # projected low) must still return 0, never a negative "recommendation".
        points = {0: 50, 5: 45, 10: 38, 15: 35, 20: 33, 25: 32, 30: 30}
        result = _compute_restock_recommendation("A-", points, minimum=40, maximum=30, today=TODAY)
        self.assertIsNotNone(result)
        self.assertEqual(result["recommended_units"], 0)


class RestockStatusTests(unittest.TestCase):
    def test_no_recommendations_is_on_track(self):
        self.assertEqual(_restock_status([]), "on_track")

    def test_any_recommendation_is_action_needed(self):
        self.assertEqual(
            _restock_status([{"blood_type": "O-", "breach_date": "2026-10-01", "days_until_breach": 8, "recommended_units": 10}]),
            "action_needed",
        )


if __name__ == "__main__":
    unittest.main()
