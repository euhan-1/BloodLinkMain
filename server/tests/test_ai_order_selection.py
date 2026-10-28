"""AI-recommended SARIMAX order: the rules loader, the series -> order helper, the
grid > AI > default-order precedence in the live fitter, every way the AI can fail
(each must end in the default order, never an error), and the /forecast response's
per-type sarimax_orders. Real DB (scratch) for the precedence/response tests; the
SARIMAX fit itself is stubbed, so this runs in seconds.

Run from server/: .venv\\Scripts\\python.exe -m pytest tests/test_ai_order_selection.py -q
"""
import json
import tempfile
import unittest
import uuid
import warnings
from datetime import timedelta
from pathlib import Path
from unittest import mock

import numpy as np
from sqlalchemy import text

import main as app
import sarimax_selector_common as sel
from database import engine
from tests.test_bulk_upload import q, upload
from tests.test_forecast_incremental import TODAY, stub_result

COMMITTED_RULES = Path(__file__).resolve().parent.parent / "models" / "sarimax_selector_rules.json"
LEAF_7 = (( 0, 1, 2), (1, 0, 1, 7))  # CANDIDATE_LIBRARY[7], what the one-leaf test rules always recommend


def noisy_series(n=60, seed=0):
    rng = np.random.default_rng(seed)
    x = np.arange(n)
    return np.maximum(0, 80 + 0.05 * x + 6 * np.sin(2 * np.pi * x / 7) + rng.normal(0, 5, n)).round()


def rules_doc(**override):
    doc = {
        "tree": {"leaf": 7}, "feature_names": sel.FEATURE_NAMES,
        "candidate_library": [[list(o), list(s)] for o, s in sel.CANDIDATE_LIBRARY], "default_index": 5,
    }
    doc.update(override)
    return doc


class RulesFileMixin:
    """Points sel.DEFAULT_RULES_PATH at a throwaway file per test (the loader caches per path, so
    every test gets its own path and never sees another's cache entry)."""

    def use_rules(self, doc=None, raw=None):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / f"rules_{uuid.uuid4().hex[:8]}.json"
        if doc is not None or raw is not None:
            path.write_text(raw if raw is not None else json.dumps(doc), encoding="utf-8")
        patcher = mock.patch.object(sel, "DEFAULT_RULES_PATH", path)
        patcher.start()
        self.addCleanup(patcher.stop)
        return path


class CommittedRulesTests(unittest.TestCase):
    def test_the_committed_file_loads_even_though_it_is_not_deploy_recommended(self):
        rules = sel.load_selector_rules(COMMITTED_RULES)
        self.assertIsNotNone(rules, "the committed rules must pass their own validation")
        self.assertIs(rules["deploy_recommended"], False, "the measured verdict travels with the artifact")
        self.assertIn("ai", rules["evaluation_summary"])
        self.assertIn("default", rules["evaluation_summary"])

    def test_the_committed_file_carries_its_provenance(self):
        training = json.loads(COMMITTED_RULES.read_text(encoding="utf-8"))["training"]
        self.assertFalse(training["quick"], "a --quick demo run must never be the committed artifact")
        self.assertIsInstance(training["random_seed"], int)
        self.assertGreater(training["wall_clock_seconds"], 0)
        self.assertIn("synthetic", training["data"])

    def test_the_primary_evaluation_numbers_are_paired_and_the_dropped_series_are_accounted_for(self):
        ev = json.loads(COMMITTED_RULES.read_text(encoding="utf-8"))["evaluation_summary"]
        self.assertEqual(ev["basis"], "paired")
        for method in ("ai", "default", "oracle"):
            self.assertEqual(ev[method]["n"], ev["n_paired"], "every method is averaged over the SAME series")
        self.assertEqual(ev["n_paired"] + len(ev["dropped_series"]), ev["n_eval_series"])
        for dropped in ev["dropped_series"]:
            self.assertTrue(dropped["unscored"], "a dropped series says which method failed and how")
        self.assertEqual(set(ev["unpaired"]), {"ai", "default", "oracle"}, "the unpaired means are kept beside, labelled as such")

    def test_the_limitations_travel_with_the_artifact(self):
        doc = json.loads(COMMITTED_RULES.read_text(encoding="utf-8"))
        text_ = " ".join(doc["limitations"])
        self.assertIn("synthetic", text_)
        self.assertIn("30 days", text_)  # 21-day CV labels vs the 30-day live horizon
        for feature in ("n_obs", "mean_level"):
            self.assertIn(feature, text_)
            self.assertIn(feature, doc["split_features"], "a limitation about a split feature the tree doesn't use would be false")

    def test_the_committed_file_matches_the_current_feature_list(self):
        # fails when FEATURE_NAMES or CANDIDATE_LIBRARY changes without retraining
        doc = json.loads(COMMITTED_RULES.read_text(encoding="utf-8"))
        self.assertEqual(doc["feature_names"], sel.FEATURE_NAMES)
        self.assertEqual([[list(o), list(s)] for o, s in sel.CANDIDATE_LIBRARY], doc["candidate_library"])

    def test_a_realistic_series_gets_a_candidate_from_the_library(self):
        rules = sel.load_selector_rules(COMMITTED_RULES)
        order, seasonal = sel.recommend_order_from_series(noisy_series(154), rules)
        self.assertIn((order, seasonal), [(tuple(o), tuple(s)) for o, s in sel.CANDIDATE_LIBRARY])


class RulesLoadingTests(RulesFileMixin, unittest.TestCase):
    def test_a_valid_file_loads(self):
        path = self.use_rules(rules_doc())
        self.assertEqual(sel.load_selector_rules(path)["tree"], {"leaf": 7})

    def test_every_unusable_file_is_none_not_an_exception(self):
        bad = {
            "missing file": None,
            "corrupt json": "{ not json",
            "empty file": "",
            "feature list drifted from FEATURE_NAMES": rules_doc(feature_names=sel.FEATURE_NAMES[:-1]),
            "leaf outside the library": rules_doc(tree={"leaf": 99}),
            "negative leaf": rules_doc(tree={"leaf": -1}),
            "empty library": rules_doc(candidate_library=[]),
            "malformed candidate": rules_doc(candidate_library=[[[0, 1], [0, 0, 0, 7]]]),
            "split on an unknown feature": rules_doc(tree={"feature": "bogus", "threshold": 1.0, "left": {"leaf": 0}, "right": {"leaf": 1}}),
            "NaN threshold": rules_doc(tree={"feature": "cv", "threshold": float("nan"), "left": {"leaf": 0}, "right": {"leaf": 1}}),
            "no tree key": {k: v for k, v in rules_doc().items() if k != "tree"},
        }
        for name, content in bad.items():
            with self.subTest(name):
                path = self.use_rules(**({"raw": content} if isinstance(content, str) else {"doc": content}))
                self.assertIsNone(sel.load_selector_rules(path))

    def test_a_failed_load_is_cached_so_a_missing_file_is_read_once_per_process(self):
        path = self.use_rules()  # nothing written
        with mock.patch.object(Path, "read_text", side_effect=FileNotFoundError("gone")) as read:
            for _ in range(3):
                self.assertIsNone(sel.load_selector_rules(path))
        self.assertEqual(read.call_count, 1)

    def test_a_successful_load_is_cached_too(self):
        path = self.use_rules(rules_doc())
        first = sel.load_selector_rules(path)
        path.unlink()  # a cached load must not touch the disk again
        self.assertIs(sel.load_selector_rules(path), first)


class RecommendationTests(unittest.TestCase):
    RULES = rules_doc()

    def test_a_nan_feature_raises_instead_of_silently_walking_right(self):
        features = sel.extract_series_features(noisy_series(60))
        walk_tree = {"feature": "acf7_diff", "threshold": 0.0, "left": {"leaf": 0}, "right": {"leaf": 1}}
        features["acf7_diff"] = float("nan")
        # the premise: without the guard NaN <= threshold is False, so this would quietly return the right leaf
        self.assertFalse(features["acf7_diff"] <= walk_tree["threshold"])
        with self.assertRaisesRegex(ValueError, "acf7_diff"):
            sel.recommend_candidate_index_from_rules(features, {"tree": walk_tree})

    def test_every_one_of_the_13_features_is_checked_not_just_the_ones_the_tree_splits_on(self):
        for name in sel.FEATURE_NAMES:
            for bad in (float("nan"), float("inf")):
                with self.subTest(name=name, value=bad):
                    features = sel.extract_series_features(noisy_series(60))
                    features[name] = bad
                    with self.assertRaises(ValueError):
                        sel.recommend_candidate_index_from_rules(features, {"tree": {"leaf": 0}})

    def test_constant_and_all_zero_series_have_non_finite_features_and_are_refused(self):
        for label, values in (("constant", np.full(60, 5.0)), ("all-zero", np.zeros(60))):
            with self.subTest(label):
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    features = sel.extract_series_features(values)
                self.assertTrue(any(not np.isfinite(v) for v in features.values()), "premise: extraction yields NaN here")
                with self.assertRaises(ValueError):
                    sel.recommend_order_from_series(values, self.RULES)

    def test_extraction_warnings_do_not_leak_to_the_caller(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            with self.assertRaises(ValueError):
                sel.recommend_order_from_series(np.full(60, 5.0), self.RULES)
        self.assertEqual([str(w.message) for w in caught], [])

    def test_a_valid_series_returns_the_leafs_candidate_as_tuples(self):
        self.assertEqual(sel.recommend_order_from_series(noisy_series(60), self.RULES), LEAF_7)

    def test_the_tree_is_walked_on_the_named_feature(self):
        tree = {"feature": "n_obs", "threshold": 100.0, "left": {"leaf": 1}, "right": {"leaf": 2}}
        rules = rules_doc(tree=tree)
        self.assertEqual(sel.recommend_order_from_series(noisy_series(60), rules), sel.CANDIDATE_LIBRARY[1])
        self.assertEqual(sel.recommend_order_from_series(noisy_series(154), rules), sel.CANDIDATE_LIBRARY[2])


class ResolveOrderTests(RulesFileMixin, unittest.TestCase):
    """grid > ai > default-order, through the real _fit_and_cache_sarimax and the real DB."""

    def setUp(self):
        with engine.begin() as c:
            self.fid = c.execute(text("INSERT INTO facilities (name, facility_type, is_active, profile_completed) "
                                      "VALUES (:n, 'bloodbank', true, true) RETURNING id"), {"n": f"AiOrder {uuid.uuid4().hex[:6]}"}).scalar()

    def tearDown(self):
        with engine.begin() as c:
            for t in ("facility_forecast_cache", "facility_sarimax_order"):
                c.execute(text(f"DELETE FROM {t} WHERE facility_id = :f"), {"f": self.fid})
            c.execute(text("DELETE FROM facilities WHERE id = :f"), {"f": self.fid})
        app._SARIMAX_FIT_FAILED.clear()

    def series(self, values=None):
        values = noisy_series(41) if values is None else values
        return [(TODAY - timedelta(days=len(values) - 1 - i), int(v)) for i, v in enumerate(values)]

    def insert_grid_row(self):
        with engine.begin() as c:
            c.execute(text(
                "INSERT INTO facility_sarimax_order (facility_id, blood_type, p, d, q, seasonal_p, seasonal_d, "
                "seasonal_q, seasonal_s, aic, lb_p_14_in_sample, lb_p_21_in_sample, lb_p_28_in_sample, "
                "criterion_satisfied, n_candidates_converged, trained_through_date, evaluated_through_date) "
                "VALUES (:f, 'O+', 1, 1, 1, 1, 0, 1, 7, 500.0, 0.5, 0.5, 0.5, true, 36, :t, :t)"), {"f": self.fid, "t": TODAY})

    def fit(self, series=None, fitter=None):
        with mock.patch.object(app, "_fit_sarimax_facility_forecast", side_effect=fitter or stub_result) as f:
            result = app._fit_and_cache_sarimax(self.fid, "O+", self.series() if series is None else series, TODAY)
        return f, result

    def test_a_grid_row_wins_over_the_ai_and_the_ai_is_not_even_asked(self):
        self.use_rules(rules_doc())
        self.insert_grid_row()
        with mock.patch.object(app, "_ai_recommended_order", side_effect=AssertionError("must not be consulted")):
            fitter, _ = self.fit()
        _, kw = fitter.call_args
        self.assertEqual((kw["order"], kw["seasonal_order"]), ((1, 1, 1), (1, 0, 1, 7)))
        self.assertEqual(kw["model_label"], "SARIMAX(1,1,1)x(1,0,1,7)+dengue [grid-searched]")

    def test_the_ai_wins_over_the_default_order_when_there_is_no_grid_row(self):
        self.use_rules(rules_doc())
        fitter, _ = self.fit()
        _, kw = fitter.call_args
        self.assertEqual((kw["order"], kw["seasonal_order"]), LEAF_7)
        self.assertEqual(kw["model_label"], "SARIMAX(0,1,2)x(1,0,1,7)+dengue [ai-recommended]")
        self.assertIsNone(kw["start_params"])
        self.assertIsNone(kw["baseline_aic_per_obs"])

    def test_the_label_that_produced_the_forecast_is_what_the_cache_row_stores(self):
        self.use_rules(rules_doc())
        self.fit()
        self.assertEqual({r[0] for r in q("SELECT model_order FROM facility_forecast_cache WHERE facility_id=:f", f=self.fid)},
                         {"SARIMAX(0,1,2)x(1,0,1,7)+dengue [ai-recommended]"})

    def test_every_way_the_ai_can_fail_ends_in_the_default_order_and_never_an_error(self):
        constant, zeros = self.series(np.full(41, 50.0)), self.series(np.zeros(41))
        cases = {
            "rules file missing": (None, self.series()),
            "rules file corrupt": ("{ nope", self.series()),
            "rules index out of range": (json.dumps(rules_doc(tree={"leaf": 99})), self.series()),
            "stale feature list": (json.dumps(rules_doc(feature_names=["n_obs"])), self.series()),
            "constant series (NaN features)": (json.dumps(rules_doc()), constant),
            "all-zero series (NaN features)": (json.dumps(rules_doc()), zeros),
        }
        for name, (raw, series) in cases.items():
            with self.subTest(name):
                self.use_rules(**({"raw": raw} if raw is not None else {}))
                fitter, result = self.fit(series)
                self.assertIsNotNone(result)
                _, kw = fitter.call_args
                self.assertEqual((kw["order"], kw["seasonal_order"]), (app.FACILITY_SARIMAX_ORDER, app.FACILITY_SARIMAX_SEASONAL_ORDER))
                self.assertEqual(kw["model_label"], "SARIMAX(0,1,4)x(1,0,1,7)+dengue [default-order]")

    def test_feature_extraction_raising_is_the_default_order(self):
        self.use_rules(rules_doc())
        with mock.patch.object(sel, "extract_series_features", side_effect=RuntimeError("statsmodels blew up")):
            fitter, _ = self.fit()
        _, kw = fitter.call_args
        self.assertEqual(kw["model_label"], "SARIMAX(0,1,4)x(1,0,1,7)+dengue [default-order]")

    def test_the_loader_itself_raising_is_the_default_order(self):
        with mock.patch.object(sel, "load_selector_rules", side_effect=RuntimeError("disk on fire")):
            self.assertIsNone(app._ai_recommended_order([1.0] * 60))

    def test_a_failed_fit_of_the_ai_order_goes_to_the_linear_trend_with_no_retry_on_the_default_order(self):
        self.use_rules(rules_doc())
        fitter, result = self.fit(fitter=lambda *a, **kw: None)
        self.assertIsNone(result)
        self.assertEqual(fitter.call_count, 1, "a retry would add a second 12-20 s fit and weaken the per-request bound")
        self.assertIn((self.fid, "O+", TODAY), app._SARIMAX_FIT_FAILED)
        self.assertEqual(q("SELECT count(*) FROM facility_forecast_cache WHERE facility_id=:f", f=self.fid)[0][0], 0)


class ForecastResponseTests(RulesFileMixin, unittest.TestCase):
    """The /forecast response states, per type, which mechanism produced the order."""

    TYPES = ["O+", "O-", "A+", "A-", "B+"]

    def setUp(self):
        with engine.begin() as c:
            self.fid = c.execute(text("INSERT INTO facilities (name, facility_type, is_active, profile_completed) "
                                      "VALUES (:n, 'bloodbank', true, true) RETURNING id"), {"n": f"AiResp {uuid.uuid4().hex[:6]}"}).scalar()
        rows = ["snapshot_date,blood_type,units"]
        for i, t in enumerate(self.TYPES):
            values = np.zeros(40) if t == "A-" else noisy_series(40, seed=i)  # A-: all zero, and today's live stock is 0 too -> NaN features
            rows += [f"{TODAY - timedelta(days=40 - d)},{t},{int(v)}" for d, v in enumerate(values)]  # 40..1 days ago: today is recorded by the app
        body, _, _ = upload(app.upload_historical_inventory_snapshots, self.fid, "\n".join(rows))
        self.assertEqual(body["errors"], [])
        with engine.begin() as c:  # O+ has a grid-searched order
            c.execute(text(
                "INSERT INTO facility_sarimax_order (facility_id, blood_type, p, d, q, seasonal_p, seasonal_d, "
                "seasonal_q, seasonal_s, aic, lb_p_14_in_sample, lb_p_21_in_sample, lb_p_28_in_sample, "
                "criterion_satisfied, n_candidates_converged, trained_through_date, evaluated_through_date) "
                "VALUES (:f, 'O+', 1, 1, 1, 1, 0, 1, 7, 500.0, 0.5, 0.5, 0.5, true, 36, :t, :t)"), {"f": self.fid, "t": TODAY})
        self.use_rules(rules_doc())

    def tearDown(self):
        with engine.begin() as c:
            for t in ("notifications", "forecast_alert_state", "facility_forecast_cache", "facility_sarimax_order",
                      "inventory_snapshot_write_log", "blood_units", "inventory_snapshots", "upload_history"):
                c.execute(text(f"DELETE FROM {t} WHERE facility_id = :f"), {"f": self.fid})
            c.execute(text("DELETE FROM facilities WHERE id = :f"), {"f": self.fid})
        app._SARIMAX_FIT_FAILED.clear()

    def complete_forecast(self):
        with mock.patch.object(app, "_fit_sarimax_facility_forecast", side_effect=stub_result) as fit:
            for _ in range(4):
                r = app.get_forecast(facility_id=self.fid)
                if not r["pending_types"]:
                    return r, fit
        self.fail("forecast never completed")

    EXPECTED = {
        "O+": "SARIMAX(1,1,1)x(1,0,1,7)+dengue [grid-searched]",
        "O-": "SARIMAX(0,1,2)x(1,0,1,7)+dengue [ai-recommended]",
        "A+": "SARIMAX(0,1,2)x(1,0,1,7)+dengue [ai-recommended]",
        "A-": "SARIMAX(0,1,4)x(1,0,1,7)+dengue [default-order]",
        "B+": "SARIMAX(0,1,2)x(1,0,1,7)+dengue [ai-recommended]",
    }

    def test_sarimax_orders_shows_all_three_mechanisms_from_fresh_fits(self):
        r, _ = self.complete_forecast()
        self.assertEqual(r["sarimax_orders"], self.EXPECTED)

    def test_sarimax_orders_is_identical_when_served_from_the_cache(self):
        first, fit = self.complete_forecast()
        fits_before = fit.call_count
        with mock.patch.object(app, "_fit_sarimax_facility_forecast", side_effect=AssertionError("cache hit must not refit")):
            again = app.get_forecast(facility_id=self.fid)
        self.assertEqual(fits_before, 5)
        self.assertEqual(again["sarimax_orders"], first["sarimax_orders"])

    def test_cache_rows_in_the_legacy_spellings_are_reported_in_todays(self):
        self.complete_forecast()
        with engine.begin() as c:
            c.execute(text("UPDATE facility_forecast_cache SET model_order = 'SARIMAX(1,1,1)x(1,0,1,7)+dengue' "
                           "WHERE facility_id=:f AND blood_type='O+'"), {"f": self.fid})
            c.execute(text("UPDATE facility_forecast_cache SET model_order = 'SARIMAX(0,1,4)x(1,0,1,7)+dengue [fallback]' "
                           "WHERE facility_id=:f AND blood_type='A-'"), {"f": self.fid})
        with mock.patch.object(app, "_fit_sarimax_facility_forecast", side_effect=AssertionError("cache hit must not refit")):
            r = app.get_forecast(facility_id=self.fid)
        self.assertEqual(r["sarimax_orders"]["O+"], self.EXPECTED["O+"])
        self.assertEqual(r["sarimax_orders"]["A-"], self.EXPECTED["A-"])

    def test_sarimax_model_label_states_only_what_is_true_for_every_type(self):
        r, _ = self.complete_forecast()
        label = r["sarimax_model_label"]
        self.assertEqual(label, app.FACILITY_SARIMAX_FAMILY_LABEL)
        self.assertFalse(any(ch.isdigit() for ch in label), "no order digits: the order differs per type")
        self.assertNotIn("[", label)

    def test_a_pending_type_is_not_in_sarimax_orders_yet(self):
        with mock.patch.object(app, "_fit_sarimax_facility_forecast", side_effect=stub_result):
            r = app.get_forecast(facility_id=self.fid)  # at most 2 fits per request
        self.assertEqual(len(r["pending_types"]), 3)
        self.assertEqual(set(r["sarimax_orders"]), set(self.TYPES) - set(r["pending_types"]))
