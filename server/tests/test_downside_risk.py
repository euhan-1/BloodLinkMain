"""The "at risk" state between "on track" and a restock recommendation: the central projection holds at or above the minimum,
but the lower bound of the 95% interval drops below it at some checkpoint (_compute_downside_risk, and what GET /forecast
returns as `downside_risks` and `type_bounds`).

It is display only: it must never create a recommendation, change recommended_units or touch the alert path. The pure
cases use hand-built dicts with the expected values worked out in the comments; the endpoint cases use a real scratch DB
with the SARIMAX fit stubbed, so this runs in seconds.

Run from server/: .venv\\Scripts\\python.exe -m pytest tests/test_downside_risk.py -q
"""
import unittest
import uuid
from datetime import date, timedelta
from unittest import mock

from sqlalchemy import text

import main as app
from database import engine
from tests.test_bulk_upload import upload
from tests.test_forecast_incremental import TODAY as REAL_TODAY, TYPES

TODAY = date(2026, 11, 1)
CP = app.FORECAST_CHECKPOINTS  # [0, 5, 10, 15, 20, 25, 30]


def flat(v):
    return {c: v for c in CP}


class ComputeDownsideRiskTests(unittest.TestCase):
    def test_the_earliest_checkpoint_whose_lower_bound_is_below_the_minimum(self):
        # minimum 40: lower is 45 until day 10, 38 at day 15 (first breach), 20 at day 20. The date is day 15, not day 20
        # and not interpolated: TODAY + 15 = 16 Nov.
        lower = {0: 50, 5: 47, 10: 45, 15: 38, 20: 20, 25: 20, 30: 20}
        r = app._compute_downside_risk("B+", flat(55), lower, 40, TODAY)
        self.assertEqual(r, {"blood_type": "B+", "risk_date": "2026-11-16", "days_until_risk": 15, "lower_units": 38})

    def test_nothing_when_the_lower_bound_stays_at_or_above_the_minimum(self):
        # exactly at the minimum is not below it
        self.assertIsNone(app._compute_downside_risk("B+", flat(55), flat(40), 40, TODAY))

    def test_a_type_the_projection_already_breaches_is_not_at_risk(self):
        # day 20 point is 39 < 40: that type has a recommendation, and "projected to hold" would be false
        point = {**flat(55), 20: 39}
        self.assertIsNone(app._compute_downside_risk("B+", point, flat(10), 40, TODAY))

    def test_today_is_known_so_checkpoint_zero_never_makes_a_type_at_risk(self):
        lower = {**flat(45), 0: 10}
        self.assertIsNone(app._compute_downside_risk("B+", flat(55), lower, 40, TODAY))

    def test_a_missing_bound_cannot_make_a_type_at_risk(self):
        # no interval (a type too sparse for one, the synthetic stand-in): never a claim
        self.assertIsNone(app._compute_downside_risk("B+", flat(55), {c: None for c in CP}, 40, TODAY))
        # a gap in the bounds does not hide a later real breach
        lower = {**flat(None), 25: 30}
        self.assertEqual(app._compute_downside_risk("B+", flat(55), lower, 40, TODAY)["days_until_risk"], 25)

    def test_no_minimum_no_risk(self):
        self.assertIsNone(app._compute_downside_risk("B+", flat(55), flat(0), None, TODAY))


class SharedBase(unittest.TestCase):
    MINIMUM, MAXIMUM = 40, 100
    DAYS = 40  # recorded days before today; get_forecast adds today's snapshot

    def setUp(self):
        with engine.begin() as c:
            self.fid = c.execute(text("INSERT INTO facilities (name, facility_type, is_active, profile_completed) "
                                      "VALUES (:n, 'bloodbank', true, true) RETURNING id"), {"n": f"Risk {uuid.uuid4().hex[:6]}"}).scalar()
        rows = ["snapshot_date,blood_type,units"] + [
            f"{REAL_TODAY - timedelta(days=d)},{t},{100 + (d * 7) % 5}" for d in range(1, self.DAYS + 1) for t in TYPES]
        body, _, _ = upload(app.upload_historical_inventory_snapshots, self.fid, "\n".join(rows))
        self.assertEqual(body["errors"], [])

    def tearDown(self):
        with engine.begin() as c:
            for t in ("notifications", "forecast_alert_state", "facility_forecast_cache", "inventory_snapshot_write_log",
                      "blood_units", "inventory_snapshots", "upload_history", "blood_type_thresholds"):
                c.execute(text(f"DELETE FROM {t} WHERE facility_id = :f"), {"f": self.fid})
            c.execute(text("DELETE FROM facilities WHERE id = :f"), {"f": self.fid})
            c.execute(text("DELETE FROM synthetic_forecast_cache WHERE model_order = 'test-downside-risk'"))
        app._SARIMAX_FIT_FAILED.clear()

    def set_minimum(self, types):
        with engine.begin() as c:
            for t in types:
                c.execute(text("INSERT INTO blood_type_thresholds (facility_id, blood_type, minimum_units, maximum_units) "
                               "VALUES (:f, :t, :mn, :mx)"), {"f": self.fid, "t": t, "mn": self.MINIMUM, "mx": self.MAXIMUM})

    def forecast(self, per_type):
        """per_type: {blood_type: {checkpoint: (units, lower, upper)}}. These types get exactly that SARIMAX result;
        every other type has none, so it takes the linear-trend path."""
        results = {bt: {"checkpoints": {c: {"units": u, "lower": lo, "upper": hi} for c, (u, lo, hi) in cps.items()},
                        "exog_included": True, "model_order": app.FACILITY_SARIMAX_LABEL}
                   for bt, cps in per_type.items()}
        with mock.patch.object(app, "_collect_sarimax_results", return_value=(results, [])):
            return app.get_forecast(facility_id=self.fid)


class ForecastEndpointTests(SharedBase):
    def setUp(self):
        super().setUp()
        self.set_minimum(["O+", "O-", "A+", "A-"])  # B+ has no threshold at all

    def scenario(self):
        return self.forecast({
            # holds at 50, the low end goes 45 -> 35 from day 15: at risk, first at day 15
            "O+": {c: (50, 45 if c < 15 else 35, 60) for c in CP},
            # holds, low end stays above the minimum: on track
            "O-": {c: (50, 45, 60) for c in CP},
            # the projection itself breaches (30 < 40): a recommendation, never at risk
            "A+": {c: (30, 20, 40) for c in CP},
            # only today's bound is low: skipped
            "A-": {c: (50, 10 if c == 0 else 45, 60) for c in CP},
            # no threshold: nothing to be at risk of
            "B+": {c: (50, 0, 60) for c in CP},
        })

    def test_only_a_type_that_holds_with_a_low_end_below_the_minimum_is_at_risk(self):
        r = self.scenario()
        self.assertEqual(r["downside_risks"], [{
            "blood_type": "O+", "risk_date": (REAL_TODAY + timedelta(days=15)).isoformat(), "days_until_risk": 15, "lower_units": 35}])
        self.assertEqual([x["blood_type"] for x in r["restock_recommendations"]], ["A+"])

    def test_it_changes_neither_the_recommendations_nor_the_alerts(self):
        with_risk = self.scenario()
        self.tearDown(); self.setUp()  # same facility shape, but every lower bound wide of the minimum
        no_risk = self.forecast({
            "O+": {c: (50, 45, 60) for c in CP}, "O-": {c: (50, 45, 60) for c in CP},
            "A+": {c: (30, 20, 40) for c in CP}, "A-": {c: (50, 45, 60) for c in CP}, "B+": {c: (50, 0, 60) for c in CP}})
        self.assertEqual(no_risk["downside_risks"], [])
        self.assertEqual(with_risk["restock_recommendations"], no_risk["restock_recommendations"])
        self.assertEqual(with_risk["restock_status"], no_risk["restock_status"])
        self.assertEqual(with_risk["alerts"], no_risk["alerts"])
        self.assertEqual([p["units"] for p in with_risk["series"]], [p["units"] for p in no_risk["series"]])

    def test_the_per_type_bounds_are_exposed_and_sum_to_the_facility_band(self):
        r = self.scenario()
        bounds = r["type_bounds"]
        self.assertEqual(set(bounds), set(r["sarimax_orders"]) | set(r["sarimax_fallback_types"]))
        o = bounds["O+"]
        self.assertEqual([p["day"] for p in o], ["Today"] + [f"Day {c}" for c in CP[1:]])
        self.assertEqual((o[0]["units"], o[0]["lower"], o[0]["upper"]), (50, 45, 60))
        self.assertEqual((o[3]["date"], o[3]["lower"]), ((REAL_TODAY + timedelta(days=15)).isoformat(), 35))
        # the facility series is these, added up (a type with no interval adds its point estimate, as the series always did)
        for i, p in enumerate(r["series"]):
            self.assertEqual(p["lower"], sum(b[i]["units"] if b[i]["lower"] is None else b[i]["lower"] for b in bounds.values()), i)
            self.assertEqual(p["units"], sum(b[i]["units"] for b in bounds.values()), i)

    def test_a_type_with_no_interval_has_null_bounds_and_is_never_at_risk(self):
        # AB+/AB-/B- have a one-day series (snapshotted as 0 today): linear trend, too sparse for an interval
        r = self.scenario()
        sparse = [bt for bt in r["type_bounds"] if bt in r["sarimax_fallback_types"]]
        self.assertTrue(sparse)
        for bt in sparse:
            self.assertTrue(all(p["lower"] is None and p["upper"] is None for p in r["type_bounds"][bt]), bt)
            self.assertNotIn(bt, [x["blood_type"] for x in r["downside_risks"]])


class HistoryGateTests(SharedBase):
    """Below SARIMAX_MIN_DAYS_REQUIRED (30 recorded days of the type's own history, today included) a type with no
    recommendation is never "on track" and never "at risk": it is listed in `insufficient_history`. A recommendation
    is not withheld, but carries `limited_history` with the day count. At 30 days or more nothing is gated."""

    OTHER = set(app.VALID_BLOOD_TYPES) - set(TYPES)  # snapshotted only today, as an explicit 0: one recorded day each

    def rebuild(self, days):
        self.DAYS = days
        self.tearDown()
        self.setUp()

    def stock_today(self):
        # today's snapshot is the live count; without units it would be 0, an outlier that drags the whole trend down
        with engine.begin() as c:
            for t in TYPES:
                c.execute(text("INSERT INTO blood_units (din, blood_type, component, location, volume_ml, collected_date, expires_date, facility_id) "
                               "SELECT :p || g, :t, 'Packed RBC', 'Fridge A', 280, :d, :e, :f FROM generate_series(1, 100) g"),
                          {"p": f"GATE{self.fid}{t}", "t": t, "d": REAL_TODAY, "e": REAL_TODAY + timedelta(days=30), "f": self.fid})

    def forecast_with(self, minimum, maximum=900, half_width=None):
        self.stock_today()
        self.set_minimum(TYPES)
        with engine.begin() as c:
            c.execute(text("UPDATE blood_type_thresholds SET minimum_units = :mn, maximum_units = :mx WHERE facility_id = :f"),
                      {"f": self.fid, "mn": minimum, "mx": maximum})
        patches = [mock.patch.object(app, "_collect_sarimax_results", return_value=({}, []))]
        if half_width is not None:
            patches.append(mock.patch.object(app, "_prediction_interval_half_width", return_value=half_width))
        with patches[0]:
            if half_width is None:
                return app.get_forecast(facility_id=self.fid)
            with patches[1]:
                return app.get_forecast(facility_id=self.fid)

    def test_29_recorded_days_never_reads_on_track(self):
        # ~100 units against a minimum of 40 with a narrow real interval: no recommendation, no risk. With a long
        # history that is "on track"; with 29 days it must be "not enough history" instead.
        self.rebuild(28)
        r = self.forecast_with(minimum=40)
        self.assertEqual(r["restock_recommendations"], [])
        self.assertEqual(r["downside_risks"], [])
        gated = {x["blood_type"]: x for x in r["insufficient_history"]}
        self.assertEqual(set(gated), set(TYPES) | self.OTHER, "every type without a recommendation, thresholds or not")
        self.assertEqual(gated["O+"], {"blood_type": "O+", "observed_days": 29, "min_days_required": 30})
        self.assertEqual(gated["AB+"]["observed_days"], 1, "the gate counts the type's own days")

    def test_29_recorded_days_never_reads_at_risk_either(self):
        self.rebuild(28)
        r = self.forecast_with(minimum=60, half_width=50.0)  # low end ~50 < 60 while the projection holds
        self.assertEqual(r["downside_risks"], [], "a gated type is never also at risk")
        self.assertEqual({x["blood_type"] for x in r["insufficient_history"]}, set(TYPES) | self.OTHER)

    def test_30_recorded_days_is_not_gated(self):
        self.rebuild(29)  # + today = 30
        quiet = self.forecast_with(minimum=40)
        self.assertEqual({x["blood_type"] for x in quiet["insufficient_history"]}, self.OTHER, "only the one-day types are gated")
        self.assertEqual(quiet["downside_risks"], [])
        self.tearDown(); self.setUp()
        wide = self.forecast_with(minimum=60, half_width=50.0)
        self.assertEqual({x["blood_type"] for x in wide["downside_risks"]}, set(TYPES))

    def test_a_recommendation_stays_but_says_it_rests_on_limited_history(self):
        # minimum 500, maximum 900: every uploaded type is far below. MIN_DAYS_REQUIRED is 3, so the system still
        # recommends on 29 days (and on 3): see the limitations in sarimax_selector_rules.json
        self.rebuild(28)
        r = self.forecast_with(minimum=500)
        recs = {x["blood_type"]: x for x in r["restock_recommendations"]}
        self.assertEqual(set(recs), set(TYPES))
        for rec in recs.values():
            self.assertEqual(rec["limited_history"], {"observed_days": 29, "min_days_required": 30})
        self.assertNotIn("O+", {x["blood_type"] for x in r["insufficient_history"]}, "a recommendation is not also 'not enough history'")

    def test_a_recommendation_at_30_days_carries_no_note(self):
        self.rebuild(29)
        r = self.forecast_with(minimum=500)
        self.assertEqual({x["blood_type"] for x in r["restock_recommendations"]}, set(TYPES))
        for rec in r["restock_recommendations"]:
            self.assertNotIn("limited_history", rec)

    def test_the_recommendation_itself_is_unchanged_by_the_note(self):
        self.rebuild(28)
        short = self.forecast_with(minimum=500)
        fields = ("blood_type", "breach_date", "days_until_breach", "recommended_units", "projected_low_units")
        for rec in short["restock_recommendations"]:
            self.assertEqual(set(rec) - set(fields), {"limited_history"})


class NoIntervalPathsTests(SharedBase):
    def test_the_synthetic_stand_in_has_no_risk_and_never_reads_on_track(self):
        # below MIN_DAYS_REQUIRED the stand-in answers; it has no interval, so no risk, and with one recorded day
        # every type is either a limited-history recommendation or "not enough history"
        self.set_minimum(TYPES)
        with engine.begin() as c:
            c.execute(text("DELETE FROM inventory_snapshots WHERE facility_id = :f AND snapshot_date < :d"), {"f": self.fid, "d": REAL_TODAY})
            for t in app.VALID_BLOOD_TYPES:
                for cp in CP:
                    c.execute(text("INSERT INTO synthetic_forecast_cache (blood_type, forecast_date, forecast_units, model_order) "
                                   "VALUES (:t, :d, 100, 'test-downside-risk')"), {"t": t, "d": REAL_TODAY + timedelta(days=cp)})
        r = app.get_forecast(facility_id=self.fid)
        self.assertEqual(r["forecast_source"], "synthetic_model_stand_in")
        self.assertEqual(r.get("downside_risks", []), [])
        self.assertNotIn("type_bounds", r)
        self.assertTrue(all(p.get("lower") is None for p in r["series"]))
        recs = {x["blood_type"]: x for x in r["restock_recommendations"]}
        gated = {x["blood_type"] for x in r["insufficient_history"]}
        self.assertEqual(set(recs) | gated, set(app.VALID_BLOOD_TYPES), "every type is one or the other")
        self.assertFalse(set(recs) & gated)
        for rec in recs.values():
            self.assertEqual(rec["limited_history"], {"observed_days": 1, "min_days_required": 30})
