"""The nightly job (POST /internal/daily-snapshot) also archives expired units, for every active facility.

Archive, never delete: archived_at is set, the row stays. A unit still reserved for a request is skipped, exactly as the
manual "Clear expired units" action skips it, because both go through one helper (_archive_expired_units). A unit expiring
today is still usable and stays. The response reports counts per facility so the cron history shows what ran, and a second
run on the same day does nothing. No stock figure moves: every count already filters on expires_date >= today.

What these tests prove: the behaviour above on real Postgres (scratch), through the functions the route calls and once
through the real ASGI app. What they do not prove: anything about production data, the cron schedule, or timing on Render.

Needs the scratch database from conftest.py (TEST_DATABASE_URL).
"""
import os
import unittest
import uuid
from unittest import mock

from fastapi import HTTPException
from sqlalchemy import text

import main as app
from database import engine
from tests.test_daily_snapshot import SECRET, asgi_post
from tests.test_expired_transfer import TODAY, delete_facilities, make_pair, q
from tests.test_request_component import accepted_request, add


def archived_at(din):
    return q("SELECT archived_at FROM blood_units WHERE din = :d", d=din)[0][0]


class NightlyArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tag = uuid.uuid4().hex[:6].upper()
        self.bank, self.hospital = make_pair(self, self.tag)
        with engine.begin() as c:
            self.inactive = c.execute(text("INSERT INTO facilities (name, facility_type, is_active, profile_completed) "
                                           "VALUES (:n, 'bloodbank', false, true) RETURNING id"), {"n": f"NightArch {self.tag} off"}).scalar()
        self.addCleanup(delete_facilities, [self.inactive])
        env = mock.patch.dict(os.environ, {"CRON_SECRET": SECRET})
        env.start()
        self.addCleanup(env.stop)

    def din(self, n):
        return f"{self.tag}-{n}"

    def run_job(self):
        return app.daily_snapshot(x_cron_secret=SECRET)

    def archived_dins(self, facility):
        return {r[0] for r in q("SELECT din FROM blood_units WHERE facility_id = :f AND archived_at IS NOT NULL", f=facility)}

    # ---- what it archives -------------------------------------------------
    def test_it_archives_each_active_facilitys_expired_units_hospitals_included(self):
        add(self.bank, self.din("B1"), -1)
        add(self.bank, self.din("B2"), -30)
        add(self.hospital, self.din("H1"), -2)

        body = self.run_job()

        self.assertEqual(self.archived_dins(self.bank), {self.din("B1"), self.din("B2")})
        self.assertEqual(self.archived_dins(self.hospital), {self.din("H1")})
        self.assertEqual(body["archived_expired"]["by_facility"][str(self.bank)], 2)
        self.assertEqual(body["archived_expired"]["by_facility"][str(self.hospital)], 1)
        self.assertGreaterEqual(body["archived_expired"]["total"], 3)  # the scratch DB may hold others' units: never fewer

    def test_a_unit_expiring_today_and_a_live_unit_stay(self):
        add(self.bank, self.din("TODAY"), 0)
        add(self.bank, self.din("LIVE"), 10)
        add(self.bank, self.din("GONE"), -1)

        self.run_job()

        self.assertEqual(self.archived_dins(self.bank), {self.din("GONE")}, "expires_date < today, strictly: a unit expiring today is still usable")

    def test_a_facility_with_nothing_expired_is_listed_with_zero(self):
        body = self.run_job()
        self.assertEqual(body["archived_expired"]["by_facility"][str(self.bank)], 0)
        self.assertEqual(body["archived_expired"]["by_facility"][str(self.hospital)], 0)

    def test_an_inactive_facility_is_left_alone_and_not_listed(self):
        add(self.inactive, self.din("OFF"), -5)

        body = self.run_job()

        self.assertEqual(self.archived_dins(self.inactive), set())
        self.assertNotIn(str(self.inactive), body["archived_expired"]["by_facility"])

    # ---- reserved units ---------------------------------------------------
    def reserved_then_expired(self, n="RES"):
        """A unit released for a request, which then passes its expiry while still held (a real in-transit case)."""
        add(self.bank, self.din(n), 20)
        rid = accepted_request(self.bank, self.hospital, 1)
        app.confirm_release(rid, facility_id=self.bank, user_id=None)
        with engine.begin() as c:
            c.execute(text("UPDATE blood_units SET expires_date = :d WHERE din = :n"), {"d": TODAY - app.timedelta(days=2), "n": self.din(n)})
        return rid

    def test_an_expired_unit_still_reserved_for_a_request_is_left_alone_and_reported(self):
        rid = self.reserved_then_expired()
        add(self.bank, self.din("PLAIN"), -3)

        body = self.run_job()

        self.assertIsNone(archived_at(self.din("RES")), "it belongs to the request until receipt settles it")
        self.assertEqual(q("SELECT reserved_for_request_id FROM blood_units WHERE din = :d", d=self.din("RES"))[0][0], rid)
        self.assertIsNotNone(archived_at(self.din("PLAIN")), "the unreserved expired unit beside it is archived")
        self.assertEqual(body["archived_expired"]["by_facility"][str(self.bank)], 1)
        self.assertEqual(body["expired_still_reserved"]["by_facility"][str(self.bank)], 1)

    def test_the_job_and_the_manual_action_agree_on_a_reserved_unit(self):
        self.reserved_then_expired()
        add(self.bank, self.din("PLAIN"), -3)
        self.assertEqual(app.archive_expired_inventory(facility_id=self.bank), {"archived_count": 1})
        body = self.run_job()
        self.assertEqual(body["archived_expired"]["by_facility"][str(self.bank)], 0, "the manual action already cleared the one it may")
        self.assertIsNone(archived_at(self.din("RES")))

    # ---- safe to run twice ------------------------------------------------
    def test_a_second_run_the_same_day_does_nothing(self):
        add(self.bank, self.din("B1"), -1)
        add(self.hospital, self.din("H1"), -1)
        first = self.run_job()
        stamp = (archived_at(self.din("B1")), archived_at(self.din("H1")))

        second = self.run_job()

        self.assertEqual(first["archived_expired"]["by_facility"][str(self.bank)], 1)
        self.assertEqual(second["archived_expired"]["by_facility"][str(self.bank)], 0)
        self.assertEqual(second["archived_expired"]["by_facility"][str(self.hospital)], 0)
        self.assertEqual(second["archived_expired"]["total"], 0)
        self.assertEqual((archived_at(self.din("B1")), archived_at(self.din("H1"))), stamp, "an archived unit is not re-stamped")
        self.assertEqual(second["rows_inserted"], 0, "the snapshot half was already idempotent")

    def test_a_unit_that_expires_overnight_is_archived_by_the_next_run_only(self):
        add(self.bank, self.din("TOMORROW"), 0)  # expires today: stays tonight
        self.run_job()
        self.assertIsNone(archived_at(self.din("TOMORROW")))
        with engine.begin() as c:  # a day passes
            c.execute(text("UPDATE blood_units SET expires_date = :d WHERE din = :n"), {"d": TODAY - app.timedelta(days=1), "n": self.din("TOMORROW")})
        self.run_job()
        self.assertIsNotNone(archived_at(self.din("TOMORROW")))

    # ---- archive, never delete; no stock figure moves ---------------------
    def test_nothing_is_deleted(self):
        for i, days in enumerate((-1, -9, 5)):
            add(self.bank, self.din(f"N{i}"), days)
        before = q("SELECT count(*) FROM blood_units WHERE facility_id = :f", f=self.bank)[0][0]
        self.run_job()
        self.assertEqual(q("SELECT count(*) FROM blood_units WHERE facility_id = :f", f=self.bank)[0][0], before)

    def test_no_stock_figure_changes(self):
        for i, days in enumerate((-1, -9, 5, 12, 0)):
            add(self.bank, self.din(f"S{i}"), days)

        def usable():  # the predicate every stock count uses (expires_date >= today), archived or not
            return {r[0]: r[1] for r in q("SELECT blood_type, count(*) FROM blood_units WHERE facility_id = :f AND expires_date >= :d GROUP BY 1",
                                          f=self.bank, d=TODAY)}

        before = usable()
        self.run_job()
        self.assertEqual(usable(), before)
        counted = {r[0]: r[1] for r in q("SELECT blood_type, units FROM inventory_snapshots WHERE facility_id = :f AND snapshot_date = :d",
                                         f=self.bank, d=TODAY)}
        self.assertEqual(counted["O+"], 3, "+5, +12 and expiring today; the two expired never counted, archived or not")
        self.assertEqual(before, {"O+": 3})
        self.assertEqual(q("SELECT count(*) FROM blood_units WHERE facility_id = :f AND archived_at IS NOT NULL", f=self.bank)[0][0], 2)

    def test_the_inventory_list_stops_showing_what_was_archived(self):
        add(self.bank, self.din("OLD"), -4)
        add(self.bank, self.din("NEW"), 6)
        self.assertEqual({u["din"] for u in app.get_inventory(facility_id=self.bank)}, {self.din("OLD"), self.din("NEW")})
        self.run_job()
        self.assertEqual({u["din"] for u in app.get_inventory(facility_id=self.bank)}, {self.din("NEW")})

    # ---- the report, and failure ------------------------------------------
    def test_the_response_is_json_with_string_facility_keys_through_the_real_app(self):
        add(self.bank, self.din("B1"), -1)
        status, body = asgi_post([("X-Cron-Secret", SECRET)])
        self.assertEqual(status, 200)
        self.assertEqual(body["archived_expired"]["by_facility"][str(self.bank)], 1)
        self.assertEqual(set(body), {"snapshot_date", "facilities", "rows_inserted", "rows_updated", "archived_expired", "expired_still_reserved"})
        self.assertEqual(set(body["archived_expired"]), {"total", "by_facility"})

    def test_a_wrong_secret_archives_nothing(self):
        add(self.bank, self.din("B1"), -1)
        with self.assertRaises(HTTPException) as cm:
            app.daily_snapshot(x_cron_secret="nope")
        self.assertEqual(cm.exception.status_code, 401)
        self.assertEqual(self.archived_dins(self.bank), set())

    def test_if_the_archive_fails_the_snapshot_is_already_saved_and_the_call_fails_loudly(self):
        add(self.bank, self.din("O1"), 5)
        add(self.bank, self.din("B1"), -1)
        with mock.patch.object(app, "_archive_expired_units", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                self.run_job()
        rows = q("SELECT blood_type, units FROM inventory_snapshots WHERE facility_id = :f AND snapshot_date = :d", f=self.bank, d=TODAY)
        self.assertEqual(len(rows), 8, "the snapshot, the part that cannot be redone, committed first")
        self.assertEqual(self.archived_dins(self.bank), set())
        self.run_job()  # a retry finishes the job
        self.assertEqual(self.archived_dins(self.bank), {self.din("B1")})
