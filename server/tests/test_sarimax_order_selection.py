"""Per-type SARIMAX order selection: the pure ranking rule (fabricated candidates,
no fitting), the live fitter's selected-vs-fallback lookup and labeling, the cache
read reporting the stored label (regression test for a bug fixed alongside this),
and an end-to-end run of _run_order_selection_for_facility against the real DB with
a shrunk grid (real fits, but 16 instead of 36 per type, to keep this fast).

Run from server/: .venv\\Scripts\\python.exe -m unittest tests.test_sarimax_order_selection -v
"""
import unittest
import uuid
from datetime import timedelta
from unittest import mock

from sqlalchemy import text

import main as app
from database import engine
from tests.test_bulk_upload import q, upload
from tests.test_forecast_incremental import TYPES, TODAY, stub_result


def candidate(p, q, sp, sq, aic, lb14, lb21, lb28):
    return {"p": p, "d": 1, "q": q, "seasonal_p": sp, "seasonal_d": 0, "seasonal_q": sq, "seasonal_s": 7,
            "aic": aic, "lb_p": {14: lb14, 21: lb21, 28: lb28}}


class RankCandidatesTests(unittest.TestCase):
    """Pure — no SARIMAX fitting at all."""

    def test_picks_lowest_aic_among_those_that_pass_all_three_lags(self):
        candidates = [
            candidate(0, 1, 0, 0, aic=100.0, lb14=0.9, lb21=0.9, lb28=0.9),   # passes, worse AIC
            candidate(1, 1, 1, 1, aic=90.0, lb14=0.06, lb21=0.20, lb28=0.20),  # passes, best AIC among passers
            candidate(2, 2, 1, 1, aic=80.0, lb14=0.9, lb21=0.9, lb28=0.03),   # fails lag 28 despite best raw AIC
        ]
        winner = app._rank_candidates_and_select(candidates)
        self.assertEqual((winner["p"], winner["q"], winner["seasonal_p"], winner["seasonal_q"]), (1, 1, 1, 1))
        self.assertTrue(winner["criterion_satisfied"])
        self.assertEqual(winner["n_candidates_converged"], 3)

    def test_all_three_lags_required_two_of_three_is_not_enough(self):
        # passes 14 and 21 but not 28 -> must NOT be treated as a passer
        candidates = [candidate(1, 1, 1, 1, aic=50.0, lb14=0.9, lb21=0.9, lb28=0.04)]
        winner = app._rank_candidates_and_select(candidates)
        self.assertFalse(winner["criterion_satisfied"])

    def test_none_passing_falls_back_to_best_aic_overall_marked_unsatisfied(self):
        candidates = [
            candidate(0, 0, 0, 0, aic=200.0, lb14=0.01, lb21=0.01, lb28=0.01),
            candidate(1, 1, 1, 1, aic=150.0, lb14=0.02, lb21=0.02, lb28=0.02),  # best AIC, still fails
        ]
        winner = app._rank_candidates_and_select(candidates)
        self.assertEqual(winner["aic"], 150.0)
        self.assertFalse(winner["criterion_satisfied"])

    def test_a_lag_with_none_pvalue_df_guard_counts_as_not_passing(self):
        candidates = [candidate(2, 2, 1, 1, aic=10.0, lb14=None, lb21=0.9, lb28=0.9)]
        winner = app._rank_candidates_and_select(candidates)
        self.assertFalse(winner["criterion_satisfied"])

    def test_empty_input_returns_none(self):
        self.assertIsNone(app._rank_candidates_and_select([]))


class OrderLabelTests(unittest.TestCase):
    def test_fallback_is_suffixed_selected_is_not(self):
        self.assertEqual(app._order_label(1, 1, 1, 1, 0, 1, 7, selected=True), "SARIMAX(1,1,1)x(1,0,1,7)+dengue")
        self.assertEqual(app._order_label(0, 1, 4, 1, 0, 1, 7, selected=False), "SARIMAX(0,1,4)x(1,0,1,7)+dengue [fallback]")


class FitAndCacheUsesSelectedOrderTests(unittest.TestCase):
    def setUp(self):
        with engine.begin() as c:
            self.fid = c.execute(text("INSERT INTO facilities (name, facility_type, is_active, profile_completed) "
                                      "VALUES (:n, 'bloodbank', true, true) RETURNING id"), {"n": f"OrderSel {uuid.uuid4().hex[:6]}"}).scalar()

    def tearDown(self):
        with engine.begin() as c:
            for t in ("facility_forecast_cache", "facility_sarimax_order"):
                c.execute(text(f"DELETE FROM {t} WHERE facility_id = :f"), {"f": self.fid})
            c.execute(text("DELETE FROM facilities WHERE id = :f"), {"f": self.fid})

    def series(self):
        return [(TODAY - timedelta(days=d), 50) for d in range(40, -1, -1)]

    def test_uses_fixed_order_and_fallback_label_when_nothing_selected(self):
        with mock.patch.object(app, "_fit_sarimax_facility_forecast", side_effect=stub_result) as fit:
            app._fit_and_cache_sarimax(self.fid, "O+", self.series(), TODAY)
        _, kwargs = fit.call_args
        self.assertEqual((kwargs["order"], kwargs["seasonal_order"]), (app.FACILITY_SARIMAX_ORDER, app.FACILITY_SARIMAX_SEASONAL_ORDER))
        self.assertTrue(kwargs["model_label"].endswith("[fallback]"))

    def test_uses_the_stored_selected_order_and_unsuffixed_label(self):
        with engine.begin() as c:
            c.execute(text(
                "INSERT INTO facility_sarimax_order (facility_id, blood_type, p, d, q, seasonal_p, seasonal_d, "
                "seasonal_q, seasonal_s, aic, lb_p_14_in_sample, lb_p_21_in_sample, lb_p_28_in_sample, "
                "criterion_satisfied, n_candidates_converged, trained_through_date, evaluated_through_date) "
                "VALUES (:f, 'O+', 1, 1, 1, 1, 0, 1, 7, 500.0, 0.5, 0.5, 0.5, true, 36, :t, :t)"),
                {"f": self.fid, "t": TODAY})
        with mock.patch.object(app, "_fit_sarimax_facility_forecast", side_effect=stub_result) as fit:
            app._fit_and_cache_sarimax(self.fid, "O+", self.series(), TODAY)
        _, kwargs = fit.call_args
        self.assertEqual((kwargs["order"], kwargs["seasonal_order"]), ((1, 1, 1), (1, 0, 1, 7)))
        self.assertEqual(kwargs["model_label"], "SARIMAX(1,1,1)x(1,0,1,7)+dengue")

    def test_cache_read_reports_the_stored_label_not_a_hardcoded_one(self):
        # regression test: _read_cached_sarimax used to return FACILITY_SARIMAX_LABEL
        # unconditionally instead of the label actually stored on the cache row.
        with mock.patch.object(app, "_fit_sarimax_facility_forecast",
                               side_effect=lambda *a, **kw: stub_result(model_label="SARIMAX(1,1,1)x(1,0,1,7)+dengue")):
            app._fit_and_cache_sarimax(self.fid, "O+", self.series(), TODAY)
        with engine.connect() as c:
            cached = app._read_cached_sarimax(c, self.fid, "O+", self.series(), TODAY)
        self.assertEqual(cached["model_order"], "SARIMAX(1,1,1)x(1,0,1,7)+dengue")


class OrderSelectionRunTests(unittest.TestCase):
    """Real fits, against the real DB, with the grid shrunk to 16 candidates/type
    (SARIMAX_ORDER_GRID_PQ patched to (0,1) instead of (0,1,2)) to keep this fast.
    70 days uploaded: ORDER_SELECTION_MIN_DAYS is SARIMAX_MIN_DAYS_REQUIRED(30) +
    ORDER_SELECTION_HOLDOUT_DAYS(30) = 60, so this leaves a 40-day training window."""

    UPLOAD_DAYS = 70

    def setUp(self):
        with engine.begin() as c:
            self.fid = c.execute(text("INSERT INTO facilities (name, facility_type, is_active, profile_completed) "
                                      "VALUES (:n, 'bloodbank', true, true) RETURNING id"), {"n": f"OrderRun {uuid.uuid4().hex[:6]}"}).scalar()
        rows = ["snapshot_date,blood_type,units"] + [
            f"{TODAY - timedelta(days=d)},{t},{100 + (d * 7) % 5}" for d in range(1, self.UPLOAD_DAYS + 1) for t in TYPES[:4]]
        body, _, _ = upload(app.upload_historical_inventory_snapshots, self.fid, "\n".join(rows))
        self.assertEqual(body["errors"], [])
        self.small_grid = mock.patch.object(app, "SARIMAX_ORDER_GRID_PQ", (0, 1))
        self.small_grid.start()

    def tearDown(self):
        self.small_grid.stop()
        with engine.begin() as c:
            for t in ("notifications", "forecast_alert_state", "facility_forecast_cache", "facility_sarimax_order",
                      "inventory_snapshot_write_log", "blood_units", "inventory_snapshots", "upload_history"):
                c.execute(text(f"DELETE FROM {t} WHERE facility_id = :f"), {"f": self.fid})
            c.execute(text("DELETE FROM facilities WHERE id = :f"), {"f": self.fid})

    def selected_rows(self):
        return {r[0]: r[1:] for r in q(
            "SELECT blood_type, p, d, q, seasonal_p, seasonal_q, criterion_satisfied, n_candidates_converged, "
            "lb_p_14_holdout_onestep, holdout_mape, holdout_rmse, holdout_baseline_mape, holdout_baseline_rmse, "
            "trained_through_date, evaluated_through_date "
            "FROM facility_sarimax_order WHERE facility_id=:f", f=self.fid)}

    def test_selects_and_stores_an_order_per_type_and_invalidates_that_types_cache(self):
        # seed a stale cache entry, as if a forecast had already been fit before selection ran
        with engine.begin() as c:
            for cpt in range(7):
                c.execute(text("INSERT INTO facility_forecast_cache (facility_id, blood_type, forecast_date, "
                               "forecast_units, lower_units, upper_units, trained_through_date, model_order) "
                               "VALUES (:f,'O+',:d,10,5,15,:t,'stale')"),
                          {"f": self.fid, "d": TODAY + timedelta(days=cpt * 5), "t": TODAY})
        app._run_order_selection_for_facility(self.fid)
        rows = self.selected_rows()
        self.assertEqual(set(rows), set(TYPES[:4]))
        for bt, (p, d, qq, sp, sq, satisfied, n_conv, lb_holdout, mape, rmse, base_mape, base_rmse,
                trained_through, evaluated_through) in rows.items():
            self.assertEqual(d, 1)
            self.assertIn(p, (0, 1))
            self.assertIn(qq, (0, 1))
            self.assertIn(sp, (0, 1))
            self.assertIn(sq, (0, 1))
            self.assertTrue(0 < n_conv <= 16, n_conv)  # the shrunk grid: 2*2*2*2 candidates; not all necessarily converge
            self.assertIsInstance(satisfied, bool)
            # hold-out diagnostics: computed on data the selection above never saw
            self.assertIsNotNone(lb_holdout)
            self.assertIsNotNone(mape)
            self.assertGreaterEqual(rmse, 0)
            self.assertIsNotNone(base_mape)
            self.assertGreaterEqual(base_rmse, 0)
            self.assertEqual(trained_through, TODAY - timedelta(days=app.ORDER_SELECTION_HOLDOUT_DAYS))
            self.assertEqual(evaluated_through, TODAY)
        self.assertEqual(q("SELECT count(*) FROM facility_forecast_cache WHERE facility_id=:f AND blood_type='O+'", f=self.fid)[0][0], 0)

    def test_max_types_bounds_a_run_and_prioritizes_never_selected_types(self):
        with engine.begin() as c:
            c.execute(text("DELETE FROM facility_sarimax_order WHERE facility_id=:f"), {"f": self.fid})
        app._run_order_selection_for_facility(self.fid, max_types=2)
        first_pass = set(self.selected_rows())
        self.assertEqual(len(first_pass), 2)
        app._run_order_selection_for_facility(self.fid, max_types=2)
        second_pass = set(self.selected_rows())
        self.assertEqual(len(second_pass), 4)
        self.assertTrue(first_pass.issubset(second_pass), "the first run's types must not be redone before the rest have any selection")
        self.assertEqual(second_pass, set(TYPES[:4]))

    def test_a_failure_partway_keeps_the_type_already_committed(self):
        real = app._select_sarimax_order_for_series
        calls = {"n": 0}

        def dies_on_second(y, exog):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("simulated interruption")
            return real(y, exog)

        with mock.patch.object(app, "_select_sarimax_order_for_series", side_effect=dies_on_second):
            with self.assertRaises(RuntimeError):
                app._run_order_selection_for_facility(self.fid)
        self.assertEqual(len(self.selected_rows()), 1, "the first type's selection must survive the second type's failure")

    def test_concurrent_run_is_skipped_not_queued(self):
        holder = engine.connect()
        try:
            holder.execute(text("SELECT pg_advisory_lock(:n,:f)"), {"n": app._ORDER_SELECTION_LOCK_NAMESPACE, "f": self.fid})
            with mock.patch.object(app, "_select_sarimax_order_for_series") as sel:
                app._run_order_selection_for_facility(self.fid)
            sel.assert_not_called()
        finally:
            holder.execute(text("SELECT pg_advisory_unlock(:n,:f)"), {"n": app._ORDER_SELECTION_LOCK_NAMESPACE, "f": self.fid})
            holder.close()

    def test_a_type_with_no_converged_candidate_is_left_unselected(self):
        with mock.patch.object(app, "_select_sarimax_order_for_series", return_value=None):
            app._run_order_selection_for_facility(self.fid)
        self.assertEqual(self.selected_rows(), {})


class UploadTriggersBackgroundSelectionTests(unittest.TestCase):
    def setUp(self):
        with engine.begin() as c:
            self.fid = c.execute(text("INSERT INTO facilities (name, facility_type, is_active, profile_completed) "
                                      "VALUES (:n, 'bloodbank', true, true) RETURNING id"), {"n": f"OrderTrig {uuid.uuid4().hex[:6]}"}).scalar()

    def tearDown(self):
        with engine.begin() as c:
            for t in ("inventory_snapshot_write_log", "inventory_snapshots", "upload_history"):
                c.execute(text(f"DELETE FROM {t} WHERE facility_id = :f"), {"f": self.fid})
            c.execute(text("DELETE FROM facilities WHERE id = :f"), {"f": self.fid})

    def test_successful_upload_schedules_the_background_task_but_never_runs_it_inline(self):
        from fastapi import BackgroundTasks
        bg = BackgroundTasks()
        with mock.patch.object(app, "_run_order_selection_for_facility") as run:
            import asyncio
            import io
            from starlette.datastructures import UploadFile
            csv = f"snapshot_date,blood_type,units\n{TODAY - timedelta(days=1)},O+,10\n"
            asyncio.run(app.upload_historical_inventory_snapshots(
                file=UploadFile(file=io.BytesIO(csv.encode()), filename="h.csv"),
                facility_id=self.fid, uploaded_by=None, background_tasks=bg))
            run.assert_not_called()  # scheduled, not run inline
            self.assertEqual(len(bg.tasks), 1)
            task = bg.tasks[0]
            self.assertEqual(task.args, (self.fid, app.MAX_ORDER_SELECTIONS_PER_BACKGROUND_RUN))

    def test_empty_upload_schedules_nothing(self):
        from fastapi import BackgroundTasks
        bg = BackgroundTasks()
        import asyncio
        import io
        from starlette.datastructures import UploadFile
        asyncio.run(app.upload_historical_inventory_snapshots(
            file=UploadFile(file=io.BytesIO(b"snapshot_date,blood_type,units\n"), filename="h.csv"),
            facility_id=self.fid, uploaded_by=None, background_tasks=bg))
        self.assertEqual(len(bg.tasks), 0)


if __name__ == "__main__":
    unittest.main()
