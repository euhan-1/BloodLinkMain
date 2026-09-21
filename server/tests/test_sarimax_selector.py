"""Tests for the AI-assisted SARIMAX model-selection pipeline
(sarimax_selector_common.py / train_sarimax_selector.py): feature
extraction and candidate library validity.

This feature is NOT wired into main.py. train_sarimax_selector.py's own
evaluation (AI-recommended config vs the fixed default vs a full
Box-Jenkins search, on held-out synthetic series) did not clear the bar set
in advance for either check — recommendations never varied across more than
2 of the 12 candidates, and the AI did not beat the fixed default on
held-out RMSE/MAPE. Per that script's own STOP condition, no model artifact
was saved (models/sarimax_selector.joblib does not exist) and main.py still
hard-codes FACILITY_SARIMAX_ORDER/FACILITY_SARIMAX_SEASONAL_ORDER exactly as
it did before this work started — see main.py's "NOTE ON THE AI
MODEL-SELECTION FEATURE" comment just above _fit_sarimax_facility_forecast.

These tests therefore only cover the parts that exist independent of that
verdict: the pure feature-extraction function and the candidate library's
own shape/validity. If a future retrain reaches a GO verdict and the
integration work is redone, this file should grow the "tree loads and
returns a valid config" / integration / fallback tests the original task
asked for.

Run from the server/ directory:

    .venv\\Scripts\\python.exe -m unittest tests.test_sarimax_selector -v
"""

import math
import unittest

import sarimax_selector_common as sel
from sarimax_selector_common import (
    CANDIDATE_LIBRARY,
    FEATURE_NAMES,
    SARIMAX_DEFAULT_INDEX,
    extract_series_features,
)


def _synthetic_series(n=60, base=80.0):
    """A plain, deterministic non-degenerate series — enough variation for
    every feature to compute without special-casing."""
    return [base + (i % 7) * 2 - 0.1 * i for i in range(n)]


class ExtractSeriesFeaturesTests(unittest.TestCase):
    def test_returns_every_declared_feature_name(self):
        features = extract_series_features(_synthetic_series())
        self.assertEqual(set(features.keys()), set(FEATURE_NAMES))

    def test_all_values_are_finite_floats(self):
        features = extract_series_features(_synthetic_series())
        for name, value in features.items():
            self.assertIsInstance(value, float, name)
            self.assertTrue(math.isfinite(value), f"{name}={value}")

    def test_n_obs_matches_series_length(self):
        features = extract_series_features(_synthetic_series(n=45))
        self.assertEqual(features["n_obs"], 45.0)

    def test_strong_trend_gives_high_trend_r2(self):
        flat = [80.0 + (i % 7) for i in range(90)]
        trending = [80.0 + (i % 7) + 0.6 * i for i in range(90)]
        flat_r2 = extract_series_features(flat)["trend_r2"]
        trend_r2 = extract_series_features(trending)["trend_r2"]
        self.assertGreater(trend_r2, flat_r2)
        self.assertGreater(trend_r2, 0.5)

    def test_strong_weekly_pattern_gives_high_seasonal_strength(self):
        no_weekly = [80.0 - 0.05 * i + (i * 37 % 5) for i in range(120)]
        weekly_pattern = [0, -3, -5, 2, -2, 8, 5]
        weekly = [80.0 - 0.05 * i + weekly_pattern[i % 7] for i in range(120)]
        weak = extract_series_features(no_weekly)["seasonal_strength"]
        strong = extract_series_features(weekly)["seasonal_strength"]
        self.assertGreater(strong, weak)
        self.assertGreater(strong, 0.3)

    def test_short_series_does_not_raise(self):
        for n in (1, 3, 8, 14):
            with self.subTest(n=n):
                features = extract_series_features(_synthetic_series(n=n))
                self.assertEqual(len(features), len(FEATURE_NAMES))


class CandidateLibraryTests(unittest.TestCase):
    def test_library_size_within_locked_range(self):
        self.assertGreaterEqual(len(CANDIDATE_LIBRARY), 5)
        self.assertLessEqual(len(CANDIDATE_LIBRARY), 15)

    def test_every_entry_is_a_valid_order_seasonal_order_pair(self):
        for order, seasonal_order in CANDIDATE_LIBRARY:
            self.assertEqual(len(order), 3)
            self.assertEqual(len(seasonal_order), 4)
            self.assertTrue(all(isinstance(v, int) and v >= 0 for v in order))
            self.assertTrue(all(isinstance(v, int) and v >= 0 for v in seasonal_order))

    def test_no_duplicate_candidates(self):
        self.assertEqual(len(CANDIDATE_LIBRARY), len(set(CANDIDATE_LIBRARY)))

    def test_includes_the_validated_default(self):
        self.assertEqual(CANDIDATE_LIBRARY[SARIMAX_DEFAULT_INDEX], ((0, 1, 4), (1, 0, 1, 7)))
        self.assertEqual(sel.SARIMAX_DEFAULT_ORDER, (0, 1, 4))
        self.assertEqual(sel.SARIMAX_DEFAULT_SEASONAL_ORDER, (1, 0, 1, 7))


class ModelArtifactNotIntegratedTests(unittest.TestCase):
    """Documents the actual current state as an executable assertion,
    rather than just a comment: no artifact was saved, because the
    evaluation didn't pass, so there is nothing for main.py to load."""

    def test_no_model_artifact_was_saved(self):
        self.assertFalse(
            sel.DEFAULT_MODEL_PATH.exists(),
            "A model artifact exists, but the last known evaluation STOPped — "
            "if this is a fresh, passing retrain, main.py's AI integration needs to be redone "
            "(see the 'NOTE ON THE AI MODEL-SELECTION FEATURE' comment there) and this test updated.",
        )


if __name__ == "__main__":
    unittest.main()
