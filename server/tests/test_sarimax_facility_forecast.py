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

import csv
import math
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from main import (
    FACILITY_SARIMAX_MAXITER,
    FORECAST_CHECKPOINTS,
    SARIMAX_AIC_PER_OBS_DEGRADATION_THRESHOLD,
    SARIMAX_MIN_DAYS_REQUIRED,
    _fit_sarimax_facility_forecast,
    _resample_daily_series,
    _sarimax_fit_is_sane,
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


class SarimaxConvergenceRecoveryTests(unittest.TestCase):
    """Regression test for a real convergence failure found in Northside's O+ series
    (2026-09, GENERATED DEMONSTRATION DATA — see docs/methodology/run_methodology.py):
    SARIMAX(1,0,2)x(1,0,1,7), refit on the full 420-day series from statsmodels' default
    start, reported mle_retvals.converged=True but had landed on a materially worse,
    non-invertible optimum (ar.L1 -> 1.000011, a seasonal MA coefficient at -2.87) than
    the SAME order's own training-window (selection) fit (corrected Ljung-Box 2e-8 vs
    0.44-0.80, 9/28 vs 0/28 residual ACF lags outside bound). Real data, real fits —
    this IS the bug, not a stand-in for it. See the module comment above
    SARIMAX_AIC_PER_OBS_DEGRADATION_THRESHOLD in main.py for the full investigation."""

    ORDER = (1, 0, 2)
    SEASONAL_ORDER = (1, 0, 1, 7)
    HOLDOUT_DAYS = 30  # ORDER_SELECTION_HOLDOUT_DAYS at the time this was investigated

    @classmethod
    def setUpClass(cls):
        csv_path = Path(__file__).resolve().parents[2] / "docs" / "methodology" / "northside_420d_history.csv"
        rows = list(csv.DictReader(open(csv_path)))
        o_plus = [(date.fromisoformat(r["snapshot_date"]), int(float(r["units"])))
                  for r in rows if r["blood_type"] == "O+"]
        o_plus.sort()
        cls.full_series = o_plus
        cls.today = o_plus[-1][0]

    def _fit_training_window(self):
        """The SAME order's SELECTION fit (training window only) — what a real
        select_orders.py run would already have computed and stored as start_params /
        selection_aic_per_obs. Reproduced directly here rather than running the full
        72-candidate grid search just to get this one baseline fit."""
        import pandas as pd
        from statsmodels.tsa.statespace.sarimax import SARIMAX

        train = self.full_series[:-self.HOLDOUT_DAYS]
        dates = [d for d, _ in train]
        idx = pd.DatetimeIndex(dates)
        endog = pd.Series([v for _, v in train], index=idx, dtype=float)
        exog = pd.DataFrame({"dengue_season": [dengue_season_index(d) for d in dates]}, index=idx)
        fit = SARIMAX(
            endog, exog=exog, order=self.ORDER, seasonal_order=self.SEASONAL_ORDER, trend=None,
            enforce_stationarity=False, enforce_invertibility=False,
        ).fit(disp=False, maxiter=FACILITY_SARIMAX_MAXITER)
        self.assertTrue(fit.mle_retvals.get("converged"), "the selection fit itself must converge for this test to mean anything")
        return fit, len(train)

    def test_default_start_on_the_full_series_is_caught_as_unsound(self):
        """Reproduces the failure directly: the SAME default-start fit
        run_methodology.py's production-fit section used, on the SAME 420-day series,
        judged against the SAME order's own selection-fit AIC/observation — must be
        rejected by _sarimax_fit_is_sane even though statsmodels itself reports it as
        converged."""
        import pandas as pd
        from statsmodels.tsa.statespace.sarimax import SARIMAX

        sel_fit, n_train = self._fit_training_window()
        baseline_aic_per_obs = sel_fit.aic / n_train

        dates = [d for d, _ in self.full_series]
        idx = pd.DatetimeIndex(dates)
        endog = pd.Series([v for _, v in self.full_series], index=idx, dtype=float)
        exog = pd.DataFrame({"dengue_season": [dengue_season_index(d) for d in dates]}, index=idx)
        bad_fit = SARIMAX(
            endog, exog=exog, order=self.ORDER, seasonal_order=self.SEASONAL_ORDER, trend=None,
            enforce_stationarity=False, enforce_invertibility=False,
        ).fit(disp=False, maxiter=FACILITY_SARIMAX_MAXITER)
        self.assertTrue(bad_fit.mle_retvals.get("converged"), "this IS the bug: converged=True at a bad optimum")

        ok, reason, diag = _sarimax_fit_is_sane(bad_fit, len(self.full_series), baseline_aic_per_obs)
        self.assertFalse(ok)
        self.assertEqual(reason, "aic_degraded_vs_baseline")
        self.assertGreater(diag["aic_per_obs_delta_vs_baseline"], SARIMAX_AIC_PER_OBS_DEGRADATION_THRESHOLD)

    def test_fit_sarimax_facility_forecast_recovers_and_still_serves_a_forecast(self):
        """The full path with no start_params available (the worst case — a
        pre-migration row with nothing stored yet) but a baseline available: the
        default-start attempt is unsound, the nm->lbfgs retry finds the good basin, and
        a real forecast is served instead of either the degenerate fit or a silent
        None."""
        sel_fit, n_train = self._fit_training_window()
        baseline_aic_per_obs = sel_fit.aic / n_train

        result = _fit_sarimax_facility_forecast(
            self.full_series, self.today, order=self.ORDER, seasonal_order=self.SEASONAL_ORDER,
            model_label="SARIMAX(1,0,2)x(1,0,1,7)+dengue", start_params=None,
            baseline_aic_per_obs=baseline_aic_per_obs,
        )
        self.assertIsNotNone(result, "the retry must recover a usable fit, not silently give up")
        for c in FORECAST_CHECKPOINTS:
            cp = result["checkpoints"][c]
            for key in ("units", "lower", "upper"):
                self.assertTrue(math.isfinite(cp[key]))
        self.assertLess(result["checkpoints"][30]["lower"], result["checkpoints"][30]["upper"])

    def test_warm_start_from_the_selection_fit_avoids_the_bad_basin_on_the_first_attempt(self):
        """PREVENT, not just RECOVER: passing the selection fit's own params as
        start_params (what a real facility_sarimax_order row now stores) reaches a
        sound fit on the FIRST attempt — no "primary fit unsound" retry log — instead
        of relying on the nm/lbfgs safety net every time."""
        sel_fit, n_train = self._fit_training_window()
        baseline_aic_per_obs = sel_fit.aic / n_train

        with patch("builtins.print") as mocked_print:
            result = _fit_sarimax_facility_forecast(
                self.full_series, self.today, order=self.ORDER, seasonal_order=self.SEASONAL_ORDER,
                model_label="SARIMAX(1,0,2)x(1,0,1,7)+dengue", start_params=sel_fit.params.tolist(),
                baseline_aic_per_obs=baseline_aic_per_obs,
            )
        self.assertIsNotNone(result)
        mocked_print.assert_not_called()


if __name__ == "__main__":
    unittest.main()
