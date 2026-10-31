"""Every calendar-day decision is made on the Asia/Manila date, computed once per request
(main.business_today), independent of the machine's timezone and of the database's
CURRENT_DATE (UTC). The clock is frozen at the two instants where the old behaviour
went wrong: just after 00:00 Manila and just before 08:00 Manila. At both, the UTC date
is still the PREVIOUS day, so a UTC (or UTC-server) implementation gets every assertion
below wrong. Real DB, disposable facilities; the SARIMAX fit is stubbed.

Run from server/: .venv\\Scripts\\python.exe -m pytest tests/test_business_day_boundary.py -q  (TEST_DATABASE_URL must point at a local scratch Postgres)
"""
import unittest
import uuid
from datetime import date, datetime, timedelta, timezone
from unittest import mock

from sqlalchemy import text

import main as app
from database import engine
from tests.test_bulk_upload import q, upload
from tests.test_expired_inventory import _add_unit, _cleanup_facility, _make_facility
from tests.test_forecast_incremental import stub_result

MANILA_TODAY = date(2026, 9, 27)
# Manila 00:00:05 and 07:59:55 on 27 Sep; the UTC date is 26 Sep at both.
JUST_AFTER_MIDNIGHT = datetime(2026, 9, 26, 16, 0, 5, tzinfo=timezone.utc)
JUST_BEFORE_8AM = datetime(2026, 9, 26, 23, 59, 55, tzinfo=timezone.utc)
# One second before Manila midnight on the 26th.
JUST_BEFORE_MIDNIGHT = datetime(2026, 9, 26, 15, 59, 55, tzinfo=timezone.utc)
BOTH = (("00:00:05 Manila", JUST_AFTER_MIDNIGHT), ("07:59:55 Manila", JUST_BEFORE_8AM))


def freeze(instant):
    return mock.patch.object(app, "_utc_now", return_value=instant)


class BusinessTodayTests(unittest.TestCase):
    def test_maps_instants_to_the_manila_calendar_day(self):
        for label, instant, expected in (
            ("15:59:59 UTC = 23:59:59 Manila", datetime(2026, 9, 26, 15, 59, 59, tzinfo=timezone.utc), date(2026, 9, 26)),
            ("16:00:00 UTC = 00:00:00 Manila", datetime(2026, 9, 26, 16, 0, 0, tzinfo=timezone.utc), MANILA_TODAY),
            ("23:59:59 UTC = 07:59:59 Manila", datetime(2026, 9, 26, 23, 59, 59, tzinfo=timezone.utc), MANILA_TODAY),
            ("00:00:00 UTC = 08:00:00 Manila", datetime(2026, 9, 27, 0, 0, 0, tzinfo=timezone.utc), MANILA_TODAY),
        ):
            with self.subTest(label), freeze(instant):
                self.assertEqual(app.business_today(), expected)


class BusinessDayBoundaryTests(unittest.TestCase):
    def setUp(self):
        with engine.begin() as c:
            self.fid = _make_facility(c, f"__test_bday_{uuid.uuid4().hex[:6]}__")
            # expired the previous Manila day / expires today (Manila) / tomorrow
            _add_unit(c, self.fid, MANILA_TODAY - timedelta(days=1), "O+")
            _add_unit(c, self.fid, MANILA_TODAY, "O+")
            _add_unit(c, self.fid, MANILA_TODAY + timedelta(days=1), "O+")

    def tearDown(self):
        with engine.begin() as c:
            for t in ("inventory_snapshot_write_log", "facility_forecast_cache", "inventory_snapshots", "upload_history"):
                c.execute(text(f"DELETE FROM {t} WHERE facility_id = :f"), {"f": self.fid})
            _cleanup_facility(c, self.fid)
        app._SARIMAX_FIT_FAILED.clear()

    def test_unit_expiring_today_is_usable_and_yesterdays_is_not(self):
        for label, instant in BOTH:
            with self.subTest(label), freeze(instant):
                summary = app.get_inventory_summary(facility_id=self.fid)
                self.assertEqual(next(r for r in summary if r["blood_type"] == "O+")["units"], 2)

    def test_yesterdays_unit_is_archivable_and_still_counted_nowhere(self):
        with freeze(JUST_AFTER_MIDNIGHT):
            self.assertEqual(app.archive_expired_inventory(facility_id=self.fid)["archived_count"], 1)

    def test_expiring_days_left_are_counted_from_the_manila_day(self):
        with freeze(JUST_AFTER_MIDNIGHT):
            groups = app.get_expiring_inventory(facility_id=self.fid)["groups"]
        # today's unit has 0 days left, tomorrow's 1; yesterday's is expired and not listed
        self.assertEqual(sorted(g["days_left"] for g in groups), [0, 1])
        self.assertEqual(q("SELECT count(*) FROM notifications WHERE facility_id=:f", f=self.fid)[0][0], 0, "and it notifies nothing")

    def test_snapshot_lands_on_the_manila_date_not_the_utc_date(self):
        for label, instant in BOTH:
            with self.subTest(label), freeze(instant):
                app.get_forecast(facility_id=self.fid)
                self.assertEqual(q("SELECT snapshot_date, units FROM inventory_snapshots WHERE facility_id=:f AND blood_type='O+'",
                                   f=self.fid), [(MANILA_TODAY, 2)])
                self.assertEqual(q("SELECT DISTINCT snapshot_date FROM inventory_snapshots WHERE facility_id=:f", f=self.fid),
                                 [(MANILA_TODAY,)])
                with engine.begin() as c:
                    c.execute(text("DELETE FROM inventory_snapshots WHERE facility_id = :f"), {"f": self.fid})

    def test_historical_upload_accepts_manila_yesterday_and_rejects_manila_today(self):
        yesterday, today = MANILA_TODAY - timedelta(days=1), MANILA_TODAY
        for label, instant in BOTH:
            with self.subTest(label), freeze(instant):
                body, _, _ = upload(app.upload_historical_inventory_snapshots, self.fid,
                                    f"snapshot_date,blood_type,units\n{yesterday},O+,10\n{today},O+,11")
                self.assertEqual(body["rows_processed"], 1)
                self.assertEqual([e["row"] for e in body["errors"]], [3])
                self.assertIn("must be before today", body["errors"][0]["reason"])
                with engine.begin() as c:
                    c.execute(text("DELETE FROM inventory_snapshots WHERE facility_id = :f"), {"f": self.fid})

    def test_cache_freshness_flips_at_manila_midnight_not_utc_midnight(self):
        # 40 days of history ending the Manila day before "just before midnight"
        end = date(2026, 9, 25)
        rows = "snapshot_date,blood_type,units\n" + "\n".join(f"{end - timedelta(days=d)},O+,{100 + d % 5}" for d in range(40))
        with engine.begin() as c:  # the fixture units would add snapshot rows; this test wants history only
            c.execute(text("DELETE FROM blood_units WHERE facility_id = :f"), {"f": self.fid})
        # get_forecast now writes all eight types (the other seven as 0 for today only), so the
        # fitter is also called for those one-day series; the real one returns None for them.
        # This test is about the 40-day O+ series, so count only fits of a series long enough to fit.
        fit = mock.Mock(side_effect=lambda series, *a, **kw: (
            stub_result(series, *a, **kw) if len(series) >= app.SARIMAX_MIN_DAYS_REQUIRED else None))

        def real_fits():
            return sum(1 for c in fit.call_args_list if len(c.args[0]) >= app.SARIMAX_MIN_DAYS_REQUIRED)

        with mock.patch.object(app, "_fit_sarimax_facility_forecast", fit):
            with freeze(JUST_BEFORE_MIDNIGHT):
                upload(app.upload_historical_inventory_snapshots, self.fid, rows)
                app.get_forecast(facility_id=self.fid)
                self.assertEqual(real_fits(), 1)
                app.get_forecast(facility_id=self.fid)
                self.assertEqual(real_fits(), 1, "23:59:55 Manila: still the same business day, cache fresh")
            with freeze(JUST_AFTER_MIDNIGHT):
                app.get_forecast(facility_id=self.fid)
                self.assertEqual(real_fits(), 2, "00:00:05 Manila: new business day, cache stale (UTC date has not changed yet)")
            with freeze(JUST_BEFORE_8AM):
                app.get_forecast(facility_id=self.fid)
                self.assertEqual(real_fits(), 2, "07:59:55 Manila: still fresh; it must NOT wait for UTC midnight to refit")
