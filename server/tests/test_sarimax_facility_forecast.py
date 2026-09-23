"""Unit tests for the real-SARIMAX-on-real-data forecast path added to
main.py: dengue_season_index, _resample_daily_series, and
_fit_sarimax_facility_forecast.

Per the same convention as test_prediction_interval.py and
test_historical_upload.py: these test pure functions only (no DB, no HTTP
client). _get_or_fit_cached_sarimax's caching behavior is DB-dependent and
is verified manually against the live dev DB instead, the same way this
project already treats other DB-dependent behavior (see those files'
docstrings) — not because it isn't worth testing, but because it needs a
real Postgres connection to test honestly rather than a mock that could
diverge from the real thing.

Run from the server/ directory:

    .venv\\Scripts\\python.exe -m unittest tests.test_sarimax_facility_forecast -v
"""

import math
import unittest
from datetime import date, timedelta
from unittest.mock import patch

from main import (
    FORECAST_CHECKPOINTS,
    SARIMAX_MIN_DAYS_REQUIRED,
    _fit_sarimax_facility_forecast,
    _resample_daily_series,
    dengue_season_index,
)


class DengueSeasonIndexTests(unittest.TestCase):
    def test_june_through_october_is_dengue_season(self):
        for month in (6, 7, 8, 9, 10):
            with self.subTest(month=month):
                self.assertEqual(dengue_season_index(date(2026, month, 15)), 1.0)

    def test_november_through_may_is_not_dengue_season(self):
        for month in (11, 12, 1, 2, 3, 4, 5):
            year = 2025 if month == 12 or month == 11 else 2026
            with self.subTest(month=month):
                self.assertEqual(dengue_season_index(date(year, month, 15)), 0.0)

    def test_boundary_days(self):
        self.assertEqual(dengue_season_index(date(2026, 5, 31)), 0.0)
        self.assertEqual(dengue_season_index(date(2026, 6, 1)), 1.0)
        self.assertEqual(dengue_season_index(date(2026, 10, 31)), 1.0)
        self.assertEqual(dengue_season_index(date(2026, 11, 1)), 0.0)


class ResampleDailySeriesTests(unittest.TestCase):
    def test_empty_input_returns_empty(self):
        self.assertEqual(_resample_daily_series({}, set(), date(2026, 1, 10)), [])

    def test_no_gaps_returns_rows_unchanged(self):
        d0 = date(2026, 1, 1)
        type_rows = {d0 + timedelta(days=i): 10 + i for i in range(5)}
        observed = set(type_rows)
        result = _resample_daily_series(type_rows, observed, d0 + timedelta(days=4))
        self.assertEqual(result, [(d0 + timedelta(days=i), 10 + i) for i in range(5)])

    def test_facility_observed_but_type_missing_is_a_real_zero(self):
        # Day 1 has a row for this type. Day 2: the facility WAS observed
        # (some other type got a snapshot that day, so day 2 is in
        # `observed`), but this type has no row for day 2 — a real zero,
        # not a gap to carry forward.
        d0 = date(2026, 1, 1)
        d1 = d0 + timedelta(days=1)
        type_rows = {d0: 5}
        observed = {d0, d1}
        result = _resample_daily_series(type_rows, observed, d1)
        self.assertEqual(result, [(d0, 5), (d1, 0)])

    def test_facility_not_observed_at_all_carries_forward(self):
        # Day 2 is NOT in `observed` at all — the dashboard just wasn't
        # loaded that day. The last known level (5, from day 0) is carried
        # forward, not zeroed.
        d0 = date(2026, 1, 1)
        d2 = d0 + timedelta(days=2)
        type_rows = {d0: 5}
        observed = {d0}  # day 1 and day 2 were never observed for ANY type
        result = _resample_daily_series(type_rows, observed, d2)
        self.assertEqual(result, [(d0, 5), (d0 + timedelta(days=1), 5), (d2, 5)])

    def test_series_starts_at_this_types_own_first_date_not_earlier(self):
        # The facility has been observed since day 0, but this particular
        # type's first-ever row is day 3 — nothing should be invented for
        # days 0-2.
        d0 = date(2026, 1, 1)
        d3 = d0 + timedelta(days=3)
        type_rows = {d3: 7}
        observed = {d0, d0 + timedelta(days=1), d0 + timedelta(days=2), d3}
        result = _resample_daily_series(type_rows, observed, d3)
        self.assertEqual(result, [(d3, 7)])


def _build_realistic_series(n_days: int, end: date) -> list[tuple[date, int]]:
    """A deterministic, non-degenerate 30+ day series with a mild downward
    trend and a real weekly wobble, so SARIMAX(0,1,4)x(1,0,1,7) has an actual
    weekly pattern to fit rather than a flat line. Not random — reproducible
    across runs, and independent of the function under test.
    """
    start = end - timedelta(days=n_days - 1)
    series = []
    for i in range(n_days):
        d = start + timedelta(days=i)
        weekday_wobble = [3, 1, 0, -1, 2, 5, 4][d.weekday()]
        trend = -0.3 * i
        value = max(0, round(60 + trend + weekday_wobble))
        series.append((d, value))
    return series


class FitSarimaxFacilityForecastTests(unittest.TestCase):
    def test_below_threshold_returns_none_without_attempting_a_fit(self):
        today = date(2026, 3, 1)
        series = _build_realistic_series(SARIMAX_MIN_DAYS_REQUIRED - 1, today)
        self.assertEqual(len(series), SARIMAX_MIN_DAYS_REQUIRED - 1)
        self.assertIsNone(_fit_sarimax_facility_forecast(series, today))

    def test_series_not_reaching_today_returns_none(self):
        today = date(2026, 3, 1)
        series = _build_realistic_series(SARIMAX_MIN_DAYS_REQUIRED + 5, today - timedelta(days=1))
        self.assertIsNone(_fit_sarimax_facility_forecast(series, today))

    def test_successful_fit_returns_all_checkpoints_with_exog_included(self):
        today = date(2026, 3, 1)
        series = _build_realistic_series(45, today)
        result = _fit_sarimax_facility_forecast(series, today)

        self.assertIsNotNone(result)
        self.assertTrue(result["exog_included"])
        self.assertEqual(set(result["checkpoints"]), set(FORECAST_CHECKPOINTS))

        for checkpoint in FORECAST_CHECKPOINTS:
            cp = result["checkpoints"][checkpoint]
            self.assertIn("units", cp)
            self.assertIn("lower", cp)
            self.assertIn("upper", cp)
            for key in ("units", "lower", "upper"):
                self.assertTrue(math.isfinite(cp[key]))
                self.assertGreaterEqual(cp[key], 0)
            # Prediction interval shape: lower <= point estimate <= upper.
            self.assertLessEqual(cp["lower"], cp["units"])
            self.assertLessEqual(cp["units"], cp["upper"])

        # Checkpoint 0 is today's real observed value, not a model estimate —
        # no manufactured interval around a known fact.
        self.assertEqual(result["checkpoints"][0]["units"], series[-1][1])
        self.assertEqual(result["checkpoints"][0]["lower"], series[-1][1])
        self.assertEqual(result["checkpoints"][0]["upper"], series[-1][1])

        # A real forecast interval should exist by day 30 (lower < upper,
        # strictly) — otherwise nothing was actually estimated.
        self.assertLess(result["checkpoints"][30]["lower"], result["checkpoints"][30]["upper"])

    def test_fit_failure_falls_back_to_none_instead_of_raising(self):
        # Deterministically exercise the except-path: force the SARIMAX
        # constructor itself to raise, and confirm the function swallows it
        # and returns None rather than propagating — this is the contract
        # GET /forecast's per-blood-type fallback depends on to never 500.
        today = date(2026, 3, 1)
        series = _build_realistic_series(45, today)
        # Patched at its defining module, not "main.SARIMAX" — main.py imports
        # SARIMAX lazily (inside _fit_sarimax_facility_forecast, for startup
        # time — see the import comment near the top of main.py), so there's
        # no longer a module-level main.SARIMAX attribute to intercept. The
        # local `from ... import SARIMAX` re-resolves this attribute off the
        # real module on every call, so patching it here still works.
        with patch("statsmodels.tsa.statespace.sarimax.SARIMAX", side_effect=RuntimeError("simulated statsmodels failure")):
            result = _fit_sarimax_facility_forecast(series, today)
        self.assertIsNone(result)

    def test_non_convergence_returns_none_instead_of_an_unverified_forecast(self):
        today = date(2026, 3, 1)
        series = _build_realistic_series(45, today)

        class _FakeFitResult:
            mle_retvals = {"converged": False}

        class _FakeModel:
            def fit(self, *args, **kwargs):
                return _FakeFitResult()

        with patch("statsmodels.tsa.statespace.sarimax.SARIMAX", return_value=_FakeModel()):
            result = _fit_sarimax_facility_forecast(series, today)
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
