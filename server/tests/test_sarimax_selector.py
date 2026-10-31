"""Tests for the AI-assisted SARIMAX model-selection pipeline
(sarimax_selector_common.py / train_sarimax_selector.py): feature
extraction and candidate library validity.

This feature is NOT wired into main.py. The default run of
train_sarimax_selector.py stops at its Stage 1 gate: the oracle (per-series
config selection by forecast error) must beat the fixed default by >= 5% and
doesn't — 18.983 RMSE / 28.11 MAPE vs the default's 18.506 / 26.94, i.e.
worse. So no classifier is trained and no artifact is saved
(models/sarimax_selector.joblib does not exist).

With --force-stage2 a depth-4 tree does train and recommends 5 of the 12
candidates on held-out series, but doesn't beat the default (AI 17.241 / 25.12
vs default 16.882 / 24.61) and lands within 0.3% of the oracle (17.236 /
25.06). It reaches the ceiling of its label, so the limit is the premise —
per-series selection on short histories — not the model. Forced artifacts are
stamped deploy_recommended=False. All figures are from synthetic series, not
real facility data. main.py still hard-codes
FACILITY_SARIMAX_ORDER/FACILITY_SARIMAX_SEASONAL_ORDER — see its "NOTE ON THE
AI MODEL-SELECTION FEATURE" comment above _fit_sarimax_facility_forecast.

These tests therefore cover only what exists independent of that verdict:
feature extraction, the candidate library's shape, and the JSON-rules export.
If a future retrain passes the gate and integration is redone, this file
should grow "tree loads and returns a valid config" / fallback tests.

Run via the full suite (conftest needs TEST_DATABASE_URL set to a local
scratch Postgres), from the repo root:

    server\\.venv\\Scripts\\python.exe -m pytest server/tests -q

Not `python -m unittest` — that skips conftest and hits .env's production
DATABASE_URL.
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


class TreeRulesExportTests(unittest.TestCase):
    """The exported JSON rules must give the same answer as the sklearn tree
    they came from — the rules are what would run on Render, where
    scikit-learn isn't installed."""

    def test_rules_match_sklearn_tree(self):
        import numpy as np
        from sklearn.tree import DecisionTreeClassifier
        from train_sarimax_selector import tree_to_rules

        rng = np.random.default_rng(0)
        X = rng.normal(size=(300, len(FEATURE_NAMES)))
        y = np.where(X[:, 0] > 0.3, 5, np.where(X[:, 3] > 0, 2, 9))
        clf = DecisionTreeClassifier(max_depth=4, random_state=0).fit(X, y)
        rules = {"tree": tree_to_rules(clf, FEATURE_NAMES)}
        for row, expected in zip(X, clf.predict(X)):
            got = sel.recommend_candidate_index_from_rules(dict(zip(FEATURE_NAMES, row)), rules)
            self.assertEqual(got, int(expected))


def _errs(rmse, mape):
    return {"rmse": rmse, "mape": mape}


class PairedSummaryTests(unittest.TestCase):
    """The headline AI-vs-default comparison is only a comparison on the series every method scored."""

    def rows(self, default_errors):
        base = {"oracle_errors": _errs(8.0, 20.0), "default_errors": _errs(10.0, 30.0), "seed": 1, "length": 120}
        return [
            {**base, "seed": 1},
            {**base, "seed": 2, "default_errors": default_errors, "length": 200},
            {**base, "seed": 3},
        ], [{"errors": _errs(6.0, 10.0)}, {"errors": _errs(100.0, 90.0)}, {"errors": _errs(6.0, 10.0)}]

    def test_a_series_one_method_could_not_score_is_dropped_from_every_method(self):
        from train_sarimax_selector import _paired_summary

        rows, ai = self.rows(default_errors=None)  # default failed on series 2, where the AI was terrible
        out = _paired_summary(rows, ai)
        self.assertEqual((out["n_paired"], out["n_eval_series"]), (2, 3))
        self.assertEqual({m: out[m]["n"] for m in ("ai", "default", "oracle")}, {"ai": 2, "default": 2, "oracle": 2})
        self.assertEqual(out["ai"]["rmse"], 6.0, "the AI's bad series 2 must not leak into the paired mean")
        self.assertEqual(out["dropped_series"], [{"seed": 2, "length": 200, "unscored": {"default": "no_forecast"}}])

    def test_undefined_mape_is_named_as_such(self):
        from train_sarimax_selector import _paired_summary

        rows, ai = self.rows(default_errors=_errs(10.0, None))
        out = _paired_summary(rows, ai)
        self.assertEqual(out["dropped_series"][0]["unscored"], {"default": "mape_undefined"})

    def test_a_diagnosis_replaces_the_generic_reason(self):
        from train_sarimax_selector import _paired_summary

        rows, ai = self.rows(default_errors=None)
        out = _paired_summary(rows, ai, diagnose=lambda row, a, method: f"{method} did not converge on seed {row['seed']}")
        self.assertEqual(out["dropped_series"][0]["unscored"], {"default": "default did not converge on seed 2"})

    def test_nothing_dropped_when_everything_scored(self):
        from train_sarimax_selector import _paired_summary

        rows, ai = self.rows(default_errors=_errs(10.0, 30.0))
        out = _paired_summary(rows, ai)
        self.assertEqual((out["n_paired"], out["dropped_series"]), (3, []))

    def test_split_features_lists_each_feature_once(self):
        from train_sarimax_selector import _split_features

        tree = {"feature": "cv", "threshold": 1.0, "left": {"leaf": 0},
                "right": {"feature": "n_obs", "threshold": 2.0, "left": {"feature": "cv", "threshold": 3.0, "left": {"leaf": 1}, "right": {"leaf": 2}}, "right": {"leaf": 3}}}
        self.assertEqual(_split_features(tree), ["cv", "n_obs"])
