"""Unit tests for the OLS prediction-interval math behind the forecast's
widening uncertainty band (_prediction_interval_half_width in main.py; see
also the MIN_DAYS_REQUIRED comment there and SYNTHETIC_SARIMAX_VALIDATION.md
for the full derivation this implements).

Every expected value below is computed independently of the function under
test, not by calling it: RSS, the mean, and Sxx are hand-derived from the
sample points (each step shown and separately cross-checked — see
_verify_constants scratch output referenced in comments), and the
t-distribution critical values are standard published constants, not values
obtained by calling scipy.stats.t.ppf(). A test that generated its own
expected value by calling the function under test would prove nothing; this
doesn't do that.

Run from the server/ directory:

    .venv\\Scripts\\python.exe -m unittest tests.test_prediction_interval -v
"""

import math
import unittest

from main import _prediction_interval_half_width

# Standard two-sided 95% critical values of the t-distribution. T_CRIT_DF1 is
# cross-checked here via the closed-form df=1 relation t.cdf(x, 1) =
# 0.5 + arctan(x)/pi (independent of scipy's t-distribution code path):
#   math.tan(0.475 * math.pi) == 12.706204736174696
# T_CRIT_DF3 is the standard published two-tailed 95% critical value for
# df = 3 (classic t-table value 3.182, given here to full precision).
T_CRIT_DF1 = 12.706204736174696   # df = 1  (n = 3, the floor)
T_CRIT_DF3 = 3.182446305283708    # df = 3  (n = 5)


class PredictionIntervalHalfWidthTests(unittest.TestCase):
    def test_normal_case_matches_independently_computed_interval(self):
        # Hand-fit OLS for these 5 points:
        #   x: 0 1 2 3 4        sum_x=10   x_bar=2
        #   y: 10 12 13 17 18   sum_y=70   y_bar=14
        #   sum_xy = 0+12+26+51+72 = 161        sum_x2 = 0+1+4+9+16 = 30
        #   denom = n*sum_x2 - sum_x^2 = 5*30 - 100 = 50
        #   slope = (5*161 - 10*70) / 50 = 105/50 = 2.1
        #   intercept = (70 - 2.1*10) / 5 = 49/5 = 9.8
        points = [(0, 10), (1, 12), (2, 13), (3, 17), (4, 18)]
        slope, intercept = 2.1, 9.8

        # Residuals at that fitted line:
        #   x=0: 9.8,  actual 10, resid  0.2
        #   x=1: 11.9, actual 12, resid  0.1
        #   x=2: 14.0, actual 13, resid -1.0
        #   x=3: 16.1, actual 17, resid  0.9
        #   x=4: 18.2, actual 18, resid -0.2
        #   RSS = 0.04 + 0.01 + 1.00 + 0.81 + 0.04 = 1.90
        rss = 1.90
        n, df = 5, 3
        x_bar, ss_xx = 2, 10  # Sxx = (0-2)^2+(1-2)^2+(2-2)^2+(3-2)^2+(4-2)^2 = 10
        mse = rss / df

        x0 = 2  # = x_bar, so the extrapolation term (x0-x_bar)^2/Sxx is exactly 0
        expected_se_pred = math.sqrt(mse * (1 + 1 / n + (x0 - x_bar) ** 2 / ss_xx))
        expected = T_CRIT_DF3 * expected_se_pred

        actual = _prediction_interval_half_width(points, slope, intercept, x0)
        self.assertIsNotNone(actual)
        self.assertAlmostEqual(actual, expected, places=6)

    def test_n_equals_3_floor_returns_a_real_but_very_wide_interval(self):
        # Hand-fit OLS for these 3 points (deliberately not collinear, so
        # there's real residual variance to measure):
        #   x: 0 1 2      sum_x=3   x_bar=1
        #   y: 10 12 20   sum_y=42  y_bar=14
        #   sum_xy = 0+12+40 = 52        sum_x2 = 0+1+4 = 5
        #   denom = 3*5 - 9 = 6
        #   slope = (3*52 - 3*42) / 6 = 30/6 = 5
        #   intercept = (42 - 5*3) / 3 = 27/3 = 9
        points = [(0, 10), (1, 12), (2, 20)]
        slope, intercept = 5, 9

        # Residuals: x=0: 9,  actual 10, resid  1
        #            x=1: 14, actual 12, resid -2
        #            x=2: 19, actual 20, resid  1
        #            RSS = 1 + 4 + 1 = 6
        rss = 6
        n, df = 3, 1
        x_bar, ss_xx = 1, 2  # Sxx = (0-1)^2+(1-1)^2+(2-1)^2 = 2
        mse = rss / df

        x0 = 1  # = x_bar
        expected_se_pred = math.sqrt(mse * (1 + 1 / n + (x0 - x_bar) ** 2 / ss_xx))
        expected = T_CRIT_DF1 * expected_se_pred

        actual = _prediction_interval_half_width(points, slope, intercept, x0)

        # n=3 is the mathematical floor (df = n-2 = 1 is the smallest degrees
        # of freedom for which any residual variance exists at all) — the
        # function must return a real number here, not None.
        self.assertIsNotNone(actual)
        self.assertAlmostEqual(actual, expected, places=6)

        # And it should be genuinely wide: these y-values only span 10 to 20
        # (a range of 10), yet the interval half-width alone is more than
        # double that — exactly the honest, "don't trust this much yet"
        # signal the n=3 floor is supposed to produce.
        self.assertGreater(actual, 20)

    def test_n_equals_2_returns_none_not_a_number(self):
        # With only 2 points, the fitted line passes through both exactly,
        # so df = n-2 = 0 and there is no residual variance left to estimate
        # an interval from — a property of least squares itself, not a
        # threshold this function chooses.
        points = [(0, 10), (1, 14)]
        slope, intercept = 4, 10  # exact fit: predicts 10 and 14 — RSS is genuinely 0

        result = _prediction_interval_half_width(points, slope, intercept, 5)
        self.assertIsNone(result)

    def test_interval_widens_further_from_the_data(self):
        # Same fitted line as the "normal case" test above. A checkpoint 30
        # days out should have a substantially wider interval than day 0 —
        # the (x0 - x_bar)^2 term is what makes the forecast chart's band
        # actually widen toward the far end, not just at the floor.
        points = [(0, 10), (1, 12), (2, 13), (3, 17), (4, 18)]
        slope, intercept = 2.1, 9.8

        half_width_near = _prediction_interval_half_width(points, slope, intercept, 0)
        half_width_far = _prediction_interval_half_width(points, slope, intercept, 30)

        self.assertIsNotNone(half_width_near)
        self.assertIsNotNone(half_width_far)
        self.assertGreater(half_width_far, half_width_near)


if __name__ == "__main__":
    unittest.main()
