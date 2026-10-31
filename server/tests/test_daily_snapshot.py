"""POST /internal/daily-snapshot and the shared _write_inventory_snapshots writer.

The job records today's inventory for EVERY active facility (hospitals included), all eight
blood types, a type with no usable units as an explicit 0. That is the fix for the Northside
stockout: an empty shelf used to write no rows, the day looked unobserved, and
_resample_daily_series carried the previous stock forward. GET /forecast now uses the same
writer, so the two can never leave a stale count behind. Real (scratch) DB, disposable
facilities; the endpoint is also driven through the ASGI app, since a direct call cannot catch
a wrong header name.

Run from server/: .venv\\Scripts\\python.exe -m pytest tests/test_daily_snapshot.py -q
"""
import asyncio
import json
import os
import unittest
import uuid
from datetime import timedelta
from unittest import mock

from fastapi import HTTPException
from sqlalchemy import text

import main as app
from database import engine
from tests.test_bulk_upload import q
from tests.test_expired_inventory import _add_unit, _cleanup_facility, _make_facility

TODAY = app.business_today()
SECRET = "test-cron-secret-not-a-real-one"
EIGHT = sorted(app.VALID_BLOOD_TYPES)


def asgi_post(headers=()):
    """POST /internal/daily-snapshot through the real ASGI app -> (status, json body).
    Header names are lower-cased, as every ASGI server (uvicorn) delivers them."""
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
             "path": "/internal/daily-snapshot", "raw_path": b"/internal/daily-snapshot", "query_string": b"",
             "root_path": "", "scheme": "http", "server": ("test", 80), "client": ("test", 1),
             "headers": [(b"host", b"test"), *[(k.lower().encode(), v.encode("utf-8")) for k, v in headers]]}
    asyncio.run(app.app(scope, receive, send))
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return status, json.loads(body)


class DailySnapshotTests(unittest.TestCase):
    def setUp(self):
        tag = uuid.uuid4().hex[:6]
        with engine.begin() as c:
            self.bank = _make_facility(c, f"__test_snap_bank_{tag}__")
            self.empty_bank = _make_facility(c, f"__test_snap_empty_{tag}__")
            self.hospital = _make_facility(c, f"__test_snap_hosp_{tag}__", "hospital")
            self.inactive = _make_facility(c, f"__test_snap_inactive_{tag}__")
            c.execute(text("UPDATE facilities SET is_active = false WHERE id = :f"), {"f": self.inactive})
            for _ in range(3):
                _add_unit(c, self.bank, TODAY + timedelta(days=10), "O+")
            _add_unit(c, self.bank, TODAY - timedelta(days=1), "O+")      # expired yesterday: not usable
            _add_unit(c, self.bank, TODAY, "A-")                          # expires today: still usable
            _add_unit(c, self.bank, TODAY, "A-")
            archived = _add_unit(c, self.bank, TODAY + timedelta(days=10), "B+")
            c.execute(text("UPDATE blood_units SET archived_at = now() WHERE din = :d"), {"d": archived})
            _add_unit(c, self.hospital, TODAY + timedelta(days=5), "O+")
            _add_unit(c, self.inactive, TODAY + timedelta(days=5), "O+")
        self.fids = [self.bank, self.empty_bank, self.hospital, self.inactive]
        self.env = mock.patch.dict(os.environ, {"CRON_SECRET": SECRET})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        with engine.begin() as c:
            for f in self.fids:
                c.execute(text("DELETE FROM facility_forecast_cache WHERE facility_id = :f"), {"f": f})
                _cleanup_facility(c, f)
        app._SARIMAX_FIT_FAILED.clear()

    def snaps(self, fid, on=None):
        return {r[0]: r[1] for r in q(
            "SELECT blood_type, units FROM inventory_snapshots WHERE facility_id=:f AND snapshot_date=:d",
            f=fid, d=on or TODAY)}

    def run_job(self):
        return app.daily_snapshot(x_cron_secret=SECRET)

    # ---- auth: fails closed ----------------------------------------------
    def test_unset_or_blank_secret_is_503_even_with_a_header(self):
        for label, env in (("unset", {}), ("blank", {"CRON_SECRET": ""}), ("whitespace", {"CRON_SECRET": "  "})):
            with self.subTest(label), mock.patch.dict(os.environ, env):
                if label == "unset":
                    os.environ.pop("CRON_SECRET", None)
                with self.assertRaises(HTTPException) as cm:
                    app.daily_snapshot(x_cron_secret=SECRET)
                self.assertEqual(cm.exception.status_code, 503)
        self.assertEqual(self.snaps(self.bank), {}, "a refused call must write nothing")

    def test_missing_or_wrong_secret_is_401_and_writes_nothing(self):
        for label, header in (("missing", None), ("wrong", "nope"), ("prefix", SECRET[:-1]), ("empty", ""),
                              ("non-ascii", "sécret-ünïcode")):
            with self.subTest(label), self.assertRaises(HTTPException) as cm:
                app.daily_snapshot(x_cron_secret=header)
            self.assertEqual(cm.exception.status_code, 401, label)
        self.assertEqual(self.snaps(self.bank), {})

    def test_over_http_header_name_status_codes_and_hidden_from_docs(self):
        self.assertEqual(asgi_post()[0], 401)
        self.assertEqual(asgi_post([("x-cron-secret", "wrong")])[0], 401)
        self.assertEqual(asgi_post([("x-cron-secret", "wrong-ünï")])[0], 401)
        self.assertEqual(self.snaps(self.bank), {})
        status, body = asgi_post([("X-Cron-Secret", SECRET)])
        self.assertEqual(status, 200)
        self.assertEqual(body["snapshot_date"], TODAY.isoformat())
        self.assertEqual(len(self.snaps(self.bank)), 8)
        with mock.patch.dict(os.environ, {"CRON_SECRET": ""}):
            self.assertEqual(asgi_post([("x-cron-secret", SECRET)])[0], 503)
        self.assertNotIn("/internal/daily-snapshot", app.app.openapi()["paths"])

    # ---- what it writes ---------------------------------------------------
    def test_writes_all_eight_types_with_explicit_zeros(self):
        self.run_job()
        self.assertEqual(self.snaps(self.bank),
                         {bt: {"O+": 3, "A-": 2, "B+": 1}.get(bt, 0) for bt in EIGHT},
                         "expired excluded, expiring-today included, archived-but-unexpired still counted "
                         "(usable = expires_date >= today, same as /inventory/summary), the rest explicit 0")

    def test_empty_shelf_records_eight_zeros_and_a_stockout_is_no_longer_carried_forward(self):
        yesterday = TODAY - timedelta(days=1)
        with engine.begin() as c:  # the facility had 50 O+ yesterday and has none today
            c.execute(text("INSERT INTO inventory_snapshots (snapshot_date, blood_type, units, facility_id) "
                           "VALUES (:d, 'O+', 50, :f)"), {"d": yesterday, "f": self.empty_bank})
        self.run_job()
        self.assertEqual(self.snaps(self.empty_bank), {bt: 0 for bt in EIGHT})
        rows = {r[0]: r[1] for r in q("SELECT snapshot_date, units FROM inventory_snapshots "
                                      "WHERE facility_id=:f AND blood_type='O+'", f=self.empty_bank)}
        series = app._resample_daily_series(rows, set(rows), TODAY)
        self.assertEqual(series[-1], (TODAY, 0), "today must read as a stockout, not as 50 carried forward")

    def test_hospitals_are_snapshotted_inactive_facilities_are_not(self):
        self.run_job()
        self.assertEqual(self.snaps(self.hospital), {bt: (1 if bt == "O+" else 0) for bt in EIGHT})
        self.assertEqual(self.snaps(self.inactive), {})

    def test_response_counts_and_idempotent_rerun(self):
        first = self.run_job()
        self.assertEqual(first["snapshot_date"], TODAY.isoformat())
        self.assertGreaterEqual(first["facilities"], 3)
        self.assertEqual(first["rows_inserted"] + first["rows_updated"], 8 * first["facilities"])
        self.assertGreaterEqual(first["rows_inserted"], 24)
        with engine.begin() as c:
            _add_unit(c, self.bank, TODAY + timedelta(days=10), "O+")
        second = self.run_job()
        self.assertEqual(second["rows_inserted"], 0)
        self.assertEqual(second["rows_updated"], 8 * second["facilities"])
        self.assertEqual(self.snaps(self.bank)["O+"], 4, "last write wins")
        self.assertEqual(q("SELECT count(*) FROM inventory_snapshots WHERE facility_id=:f", f=self.bank)[0][0], 8)

    def test_business_today_is_read_once_and_used_for_every_write(self):
        other_day = TODAY - timedelta(days=200)
        with mock.patch.object(app, "business_today", return_value=other_day) as bt:
            body = self.run_job()
        self.assertEqual(bt.call_count, 1)
        self.assertEqual(body["snapshot_date"], other_day.isoformat())
        dates = {r[0] for r in q("SELECT DISTINCT snapshot_date FROM inventory_snapshots WHERE facility_id = ANY(:f)", f=self.fids)}
        self.assertEqual(dates, {other_day})

    def test_does_no_forecast_work(self):
        boom = mock.Mock(side_effect=AssertionError("the snapshot job must not fit or read forecasts"))
        with mock.patch.object(app, "_fit_sarimax_facility_forecast", boom), \
                mock.patch.object(app, "_fit_and_cache_sarimax", boom), \
                mock.patch.object(app, "_collect_sarimax_results", boom), \
                mock.patch.object(app, "get_forecast", boom):
            self.run_job()
        boom.assert_not_called()
        self.assertEqual(q("SELECT count(*) FROM facility_forecast_cache WHERE facility_id = ANY(:f)", f=self.fids)[0][0], 0)
        # the job also archives expired units now (tests/test_nightly_archive.py covers that in full): the one expired unit
        # is archived, and the unexpired unit that was already archived is untouched
        self.assertEqual(q("SELECT count(*) FROM blood_units WHERE facility_id=:f AND expires_date < :t AND archived_at IS NULL",
                           f=self.bank, t=TODAY)[0][0], 0)
        self.assertEqual(q("SELECT count(*) FROM blood_units WHERE facility_id=:f AND archived_at IS NOT NULL", f=self.bank)[0][0], 2)

    # ---- the dashboard writer shares the helper ----------------------------
    def test_dashboard_overwrites_a_stale_job_count_with_a_real_zero(self):
        """The conflict a bare same-key upsert would NOT prevent: the job records O+ = 3 at 23:30, the
        shelf empties, the dashboard loads. Skipping zero rows would leave the 3 in place."""
        self.run_job()
        self.assertEqual(self.snaps(self.bank)["O+"], 3)
        with engine.begin() as c:
            c.execute(text("DELETE FROM blood_units WHERE facility_id = :f"), {"f": self.bank})
        app.get_forecast(facility_id=self.bank)
        self.assertEqual(self.snaps(self.bank), {bt: 0 for bt in EIGHT})

    def test_dashboard_then_job_and_job_then_dashboard_agree_with_one_row_per_type(self):
        app.get_forecast(facility_id=self.bank)
        after_dashboard = self.snaps(self.bank)
        self.run_job()
        self.assertEqual(self.snaps(self.bank), after_dashboard)
        app.get_forecast(facility_id=self.bank)
        self.assertEqual(self.snaps(self.bank), after_dashboard)
        self.assertEqual(q("SELECT count(*) FROM inventory_snapshots WHERE facility_id=:f", f=self.bank)[0][0], 8)

    def test_hospital_dashboard_still_gets_no_forecast_and_no_cache(self):
        self.run_job()
        view = app.get_forecast(facility_id=self.hospital)
        self.assertNotIn("forecast_source", view)
        self.assertEqual(q("SELECT count(*) FROM facility_forecast_cache WHERE facility_id=:f", f=self.hospital)[0][0], 0)
