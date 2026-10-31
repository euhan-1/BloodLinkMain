"""The notification list: transfer events only, rows that resolve themselves, an honest count, and wording that says which request.

THE RULE: a row exists because something needs the facility, or something changed in a transfer it is part of. When that
stops being true the system marks the row read (never deletes it). These tests prove, on real Postgres (scratch):
  - incoming_request resolves when the request is accepted, declined or cancelled; the receipt prompt (receipt_needed) when
    receipt is confirmed; a row whose request no longer exists resolves; nothing is ever deleted;
  - the old shared transfer_confirmation_needed type is no longer written, the supplier is not told to "confirm release" at
    accept, and the requester's prompt is receipt_needed;
  - the sweep that derives the same from a request's state covers every row, whatever its age;
  - forecast_shortage is not written below SARIMAX_MIN_DAYS_REQUIRED recorded days, nor from the synthetic stand-in, and above
    it reads as the Dashboard does;
  - GET /notifications/unread-count is every unread row, and GET /notifications pages past 50;
  - the texts name the quantity and component (two requests for one blood type no longer read alike), write dates out
    ("28 Oct 2026"), and say "request", "kilometers" and "near-expiry" throughout.
What they do not prove: anything about production rows (the decision on the existing unread rows is separate) or what the
bell draws (scripts/check_notifications.ts).

Needs the scratch database from conftest.py (TEST_DATABASE_URL).
"""
import re
import unittest
import uuid
from datetime import date, datetime, timedelta, timezone
from unittest import mock

from sqlalchemy import text

import main as app
from database import engine
from tests.test_bulk_upload import upload
from tests.test_expired_transfer import TODAY, make_pair, q
from tests.test_request_component import add
from tests.test_request_transitions import TransitionBase

WRITTEN = datetime(2026, 10, 15, 9, 0, tzinfo=timezone.utc)  # a fixed creation time for rows built by hand


def name_of(facility_id):
    return q("SELECT name FROM facilities WHERE id = :f", f=facility_id)[0][0]


class FlowBase(TransitionBase):
    def notes(self, facility):
        return [(t, m, r is not None) for t, m, r in q(
            "SELECT type, message, read_at FROM notifications WHERE facility_id = :f ORDER BY id", f=facility)]

    def types(self, facility):
        return [t for t, _, _ in self.notes(facility)]

    def of_type(self, facility, notif_type):
        return [(m, read) for t, m, read in self.notes(facility) if t == notif_type]


class TransferEventTests(FlowBase):
    def test_accept_tells_the_requester_which_request_and_tells_the_supplier_nothing_new(self):
        rid = self.new_request(quantity=3)
        self.accept(rid)
        self.assertEqual(self.of_type(self.hospital, "request_accepted"),
                         [(f"{name_of(self.bank)} accepted your 3-unit O+ Packed RBC request", False)])
        self.assertEqual(self.types(self.bank), ["incoming_request"], "no 'confirm release' notice at accept: they just clicked Accept")

    def test_the_old_shared_type_is_never_written_by_a_whole_transfer(self):
        rid = self.new_request()
        self.accept(rid)
        self.release(rid)
        app.confirm_receipt(rid, facility_id=self.hospital, user_id=self.hospital_user)
        every = set(self.types(self.bank)) | set(self.types(self.hospital))
        self.assertEqual(every, {"incoming_request", "request_accepted", "receipt_needed", "transfer_completed"})
        self.assertNotIn("transfer_confirmation_needed", every)

    # ---- resolving --------------------------------------------------------
    def test_incoming_request_resolves_when_accepted_declined_or_cancelled(self):
        for label, act in (
            ("accepted", lambda r: self.accept(r)),
            ("declined", lambda r: app.decline_request(r, facility_id=self.bank, user_id=self.bank_user)),
            ("cancelled", lambda r: app.cancel_request(r, facility_id=self.hospital, user_id=self.hospital_user)),
        ):
            with self.subTest(label):
                rid = self.new_request()
                link = f"requests:{rid}"
                self.assertEqual(q("SELECT read_at IS NULL FROM notifications WHERE type='incoming_request' AND link=:l", l=link), [(True,)])
                act(rid)
                self.assertEqual(q("SELECT read_at IS NOT NULL FROM notifications WHERE type='incoming_request' AND link=:l", l=link),
                                 [(True,)], "resolved by the system, and still there")

    def test_the_receipt_prompt_resolves_when_receipt_is_confirmed(self):
        rid = self.new_request(quantity=2)
        self.accept(rid)
        add(self.bank, f"{self.tag}-A", 10)
        add(self.bank, f"{self.tag}-B", 10)
        app.confirm_release(rid, facility_id=self.bank, user_id=self.bank_user)
        self.assertEqual(self.of_type(self.hospital, "receipt_needed"),
                         [(f"{name_of(self.bank)} released your 2-unit O+ Packed RBC request. Confirm receipt when it arrives.", False)])
        app.confirm_receipt(rid, facility_id=self.hospital, user_id=self.hospital_user)
        self.assertEqual([read for _, read in self.of_type(self.hospital, "receipt_needed")], [True])
        self.assertEqual(self.of_type(self.bank, "transfer_completed"),
                         [(f"{name_of(self.hospital)} confirmed receipt of your 2-unit O+ Packed RBC request. The transfer is complete.", False)])

    def test_resolving_never_deletes_a_row(self):
        rid = self.new_request()
        self.accept(rid)
        self.release(rid)
        before = q("SELECT count(*) FROM notifications WHERE facility_id IN (:b, :h)", b=self.bank, h=self.hospital)[0][0]
        app.confirm_receipt(rid, facility_id=self.hospital, user_id=self.hospital_user)
        after = q("SELECT count(*) FROM notifications WHERE facility_id IN (:b, :h)", b=self.bank, h=self.hospital)[0][0]
        self.assertEqual(after, before + 1, "receipt adds the supplier's completed notice and removes nothing")

    def test_an_already_read_row_keeps_its_own_read_time(self):
        rid = self.new_request()
        stamp = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)
        with engine.begin() as c:
            c.execute(text("UPDATE notifications SET read_at = :t WHERE type='incoming_request' AND link = :l"), {"t": stamp, "l": f"requests:{rid}"})
        self.accept(rid)
        self.assertEqual(q("SELECT read_at FROM notifications WHERE type='incoming_request' AND link=:l", l=f"requests:{rid}")[0][0], stamp)

    # ---- wording ----------------------------------------------------------
    def test_two_requests_for_the_same_blood_type_no_longer_read_alike(self):
        a = self.new_request(quantity=2)
        b = self.new_request(quantity=5, component="Platelets")
        self.accept(a)
        app.decline_request(b, facility_id=self.bank, user_id=self.bank_user)
        bank = name_of(self.bank)
        self.assertEqual(sorted(m for m, _ in self.of_type(self.hospital, "request_accepted")), [f"{bank} accepted your 2-unit O+ Packed RBC request"])
        self.assertEqual(self.of_type(self.hospital, "request_declined"), [(f"{bank} declined your 5-unit O+ Platelets request", False)])
        c = self.new_request(quantity=4)
        app.cancel_request(c, facility_id=self.hospital, user_id=self.hospital_user)
        self.assertEqual(self.of_type(self.bank, "request_cancelled"), [(f"{name_of(self.hospital)} cancelled their 4-unit O+ Packed RBC request", False)])

    def test_a_request_with_no_recorded_component_is_still_named(self):
        rid = self.new_request(quantity=3)
        with engine.begin() as c:
            c.execute(text("UPDATE requests SET component = NULL WHERE id = :r"), {"r": rid})
        self.accept(rid)
        self.assertEqual(self.of_type(self.hospital, "request_accepted")[0][0], f"{name_of(self.bank)} accepted your 3-unit O+ request")

    def test_failed_units_are_named_with_written_out_dates_and_the_list_is_capped(self):
        for i in range(7):
            add(self.bank, f"{self.tag}-F{i}", 3)
        rid = self.new_request(quantity=7)
        self.accept(rid)
        app.confirm_release(rid, facility_id=self.bank, user_id=self.bank_user)
        with mock.patch.object(app, "business_today", return_value=TODAY + timedelta(days=9)):
            app.confirm_receipt(rid, facility_id=self.hospital, user_id=self.hospital_user)
        expires = app._format_date(TODAY + timedelta(days=3))
        for facility, who in ((self.hospital, "requester"), (self.bank, "supplier")):
            (message, _), = self.of_type(facility, "transfer_units_failed")
            with self.subTest(who):
                self.assertIn(f"(expired {expires})", message)
                self.assertEqual(len(re.findall(r"\(expired ", message)), 5, "five named, the rest counted")
                self.assertIn("and 2 more", message)
                self.assertIn("0 of 7 O+ Packed RBC units", message)
                self.assertIsNone(re.search(r"\d{4}-\d{2}-\d{2}", message), "no ISO dates")
                self.assertNotIn("—", message)
        self.assertEqual([read for _, read in self.of_type(self.hospital, "receipt_needed")], [True])

    def test_dates_are_written_out_the_way_the_screens_write_them(self):
        self.assertEqual(app._format_date(date(2026, 10, 28)), "28 Oct 2026")
        self.assertEqual(app._format_day_month(date(2026, 11, 4)), "4 Nov")
        self.assertEqual(app._kilometers_text(4.1), "4.1 kilometers")
        self.assertEqual(app._kilometers_text(1.0), "1 kilometer")


class SweepTests(FlowBase):
    def row(self, facility, notif_type, link, created_at=None):
        with engine.begin() as c:
            return c.execute(text("INSERT INTO notifications (facility_id, type, message, link, created_at) "
                                  "VALUES (:f, :t, 'x', :l, COALESCE(:c, now())) RETURNING id"),
                             {"f": facility, "t": notif_type, "l": link, "c": created_at}).scalar()

    def read(self, nid):
        return q("SELECT read_at IS NOT NULL FROM notifications WHERE id = :i", i=nid)[0][0]

    def list_for(self, facility):
        return app.list_notifications(facility_id=facility)

    def test_a_row_whose_request_no_longer_exists_resolves(self):
        nid = self.row(self.bank, "incoming_request", "requests:2000000000", created_at=WRITTEN)
        self.list_for(self.bank)
        self.assertTrue(self.read(nid))

    def test_the_sweep_covers_every_row_whatever_its_age(self):
        # there is no date limit: a row from before the notification rebuild resolves like any other
        ancient = self.row(self.bank, "incoming_request", "requests:2000000000", created_at=datetime(2026, 8, 21, 9, 0, tzinfo=timezone.utc))
        recent = self.row(self.bank, "incoming_request", "requests:2000000001", created_at=WRITTEN)
        app.notifications_unread_count(facility_id=self.bank)
        self.assertEqual((self.read(ancient), self.read(recent)), (True, True))

    def test_the_sweep_derives_each_rule_from_the_requests_state(self):
        pending = self.new_request()
        accepted = self.new_request()
        self.accept(accepted)
        released = self.new_request()
        self.accept(released)
        self.release(released)
        done = self.new_request()
        self.accept(done)
        self.release(done)
        app.confirm_receipt(done, facility_id=self.hospital, user_id=self.hospital_user)
        new = WRITTEN
        # rows as if the events had not resolved them (a missed event, or a row restored from a dump)
        with engine.begin() as c:
            c.execute(text("UPDATE notifications SET read_at = NULL WHERE facility_id IN (:b, :h)"), {"b": self.bank, "h": self.hospital})
            c.execute(text("UPDATE notifications SET created_at = :n WHERE facility_id IN (:b, :h)"), {"n": new, "b": self.bank, "h": self.hospital})
        app.list_notifications(facility_id=self.bank)
        app.list_notifications(facility_id=self.hospital)

        def unread(facility, notif_type, rid):
            return q("SELECT read_at IS NULL FROM notifications WHERE facility_id=:f AND type=:t AND link=:l", f=facility, t=notif_type, l=f"requests:{rid}")[0][0]

        self.assertTrue(unread(self.bank, "incoming_request", pending), "still pending: still needs the supplier")
        self.assertFalse(unread(self.bank, "incoming_request", accepted))
        self.assertTrue(unread(self.hospital, "receipt_needed", released), "released, not received: still needs the requester")
        self.assertFalse(unread(self.hospital, "receipt_needed", done))
        self.assertTrue(unread(self.hospital, "request_accepted", done), "an event, not a prompt: it waits to be read")

    def test_other_links_and_types_are_never_touched(self):
        new = WRITTEN
        ids = [self.row(self.bank, "forecast_shortage", "dashboard", created_at=new),
               self.row(self.bank, "expiring_unit_alert", "sourcing:O+", created_at=new),
               self.row(self.bank, "blast_complete", "chat", created_at=new)]
        self.list_for(self.bank)
        self.assertEqual([self.read(i) for i in ids], [False, False, False])


class UnreadCountAndPagingTests(unittest.TestCase):
    def setUp(self):
        with engine.begin() as c:
            self.fid, self.other = (c.execute(text("INSERT INTO facilities (name, facility_type, is_active, profile_completed) "
                                                  "VALUES (:n, 'bloodbank', true, true) RETURNING id"), {"n": f"Bell {uuid.uuid4().hex[:6]} {i}"}).scalar()
                                    for i in range(2))
            for fid, total, unread in ((self.fid, 130, 120), (self.other, 3, 3)):
                c.execute(text("INSERT INTO notifications (facility_id, type, message, link, read_at) "
                               "SELECT :f, 'request_accepted', 'n' || g, 'dashboard', CASE WHEN g <= :read THEN now() END FROM generate_series(1, :n) g"),
                          {"f": fid, "n": total, "read": total - unread})
        self.addCleanup(self.clean)

    def clean(self):
        with engine.begin() as c:
            c.execute(text("DELETE FROM notifications WHERE facility_id IN (:a, :b)"), {"a": self.fid, "b": self.other})
            c.execute(text("DELETE FROM facilities WHERE id IN (:a, :b)"), {"a": self.fid, "b": self.other})

    def test_the_count_is_every_unread_row_not_the_newest_page(self):
        self.assertEqual(len(app.list_notifications(facility_id=self.fid)), 50)
        got = app.notifications_unread_count(facility_id=self.fid)
        self.assertEqual(got["unread"], 120)
        self.assertEqual(got["latest_id"], q("SELECT max(id) FROM notifications WHERE facility_id = :f", f=self.fid)[0][0])
        self.assertEqual(app.notifications_unread_count(facility_id=self.other)["unread"], 3, "each facility counts its own")

    def test_older_rows_are_reachable_a_page_at_a_time_with_none_missed_or_repeated(self):
        seen, before = [], None
        for _ in range(10):
            page = app.list_notifications(limit=50, before_id=before, facility_id=self.fid)
            if not page:
                break
            seen += [r["id"] for r in page]
            before = page[-1]["id"]
        self.assertEqual(len(seen), 130)
        self.assertEqual(len(set(seen)), 130)
        self.assertEqual(seen, sorted(seen, reverse=True), "newest first, throughout")

    def test_the_page_size_is_clamped(self):
        self.assertEqual(len(app.list_notifications(limit=1000, facility_id=self.fid)), app.NOTIFICATIONS_MAX_PAGE_SIZE)
        self.assertEqual(len(app.list_notifications(limit=0, facility_id=self.fid)), 1)

    def test_marking_all_read_zeroes_the_count_including_rows_past_the_first_page(self):
        app.mark_all_notifications_read(facility_id=self.fid)
        self.assertEqual(app.notifications_unread_count(facility_id=self.fid)["unread"], 0)


class ForecastShortageTests(unittest.TestCase):
    DAYS = 28  # recorded days before today; get_forecast adds today's snapshot

    def setUp(self):
        with engine.begin() as c:
            self.fid = c.execute(text("INSERT INTO facilities (name, facility_type, is_active, profile_completed) "
                                      "VALUES (:n, 'bloodbank', true, true) RETURNING id"), {"n": f"Short {uuid.uuid4().hex[:6]}"}).scalar()
        self.addCleanup(self.clean)

    def clean(self):
        with engine.begin() as c:
            for t in ("notifications", "forecast_alert_state", "facility_forecast_cache", "inventory_snapshot_write_log", "blood_units",
                      "inventory_snapshots", "upload_history", "blood_type_thresholds"):
                c.execute(text(f"DELETE FROM {t} WHERE facility_id = :f"), {"f": self.fid})
            c.execute(text("DELETE FROM facilities WHERE id = :f"), {"f": self.fid})
        app._SARIMAX_FIT_FAILED.clear()

    def declining_o_positive(self, days, minimum=60):
        """O+ falls ~3 a day to 100 today: above its minimum now, projected under it within a few weeks."""
        rows = ["snapshot_date,blood_type,units"] + [f"{TODAY - timedelta(days=d)},O+,{100 + 3 * d}" for d in range(1, days + 1)]
        body, _, _ = upload(app.upload_historical_inventory_snapshots, self.fid, "\n".join(rows))
        self.assertEqual(body["errors"], [])
        with engine.begin() as c:
            c.execute(text("INSERT INTO blood_units (din, blood_type, component, location, volume_ml, collected_date, expires_date, facility_id) "
                           "SELECT :p || g, 'O+', 'Packed RBC', 'Fridge A', 280, :d, :e, :f FROM generate_series(1, 100) g"),
                      {"p": f"SHORT{self.fid}", "d": TODAY, "e": TODAY + timedelta(days=40), "f": self.fid})
            c.execute(text("INSERT INTO blood_type_thresholds (facility_id, blood_type, minimum_units, maximum_units) VALUES (:f, 'O+', :mn, 160)"),
                      {"f": self.fid, "mn": minimum})

    def forecast(self):
        with mock.patch.object(app, "_collect_sarimax_results", return_value=({}, [])):
            return app.get_forecast(facility_id=self.fid)

    def shortages(self):
        return q("SELECT message, read_at IS NOT NULL FROM notifications WHERE facility_id = :f AND type = 'forecast_shortage' ORDER BY id", f=self.fid)

    def test_below_thirty_recorded_days_nothing_is_written_but_the_dashboard_still_alerts(self):
        self.declining_o_positive(28)  # + today = 29
        r = self.forecast()
        self.assertEqual([a["type"] for a in r["alerts"]], ["O+"], "the Dashboard's own data is unchanged")
        self.assertEqual(self.shortages(), [])
        self.assertEqual(q("SELECT count(*) FROM forecast_alert_state WHERE facility_id=:f AND alerting", f=self.fid)[0][0], 0,
                         "not recorded as alerting, so crossing 30 days later still notifies")

    def test_at_thirty_recorded_days_it_is_written_once_in_the_dashboards_words(self):
        self.declining_o_positive(29)  # + today = 30
        r = self.forecast()
        (rec,) = [x for x in r["restock_recommendations"] if x["blood_type"] == "O+"]
        expected = (f"O+: projected below its minimum around {app._format_day_month(date.fromisoformat(rec['breach_date']))}; "
                    f"ordering {rec['recommended_units']} units brings it back to its maximum.")
        self.assertEqual(self.shortages(), [(expected, False)])
        self.forecast()
        self.assertEqual(len(self.shortages()), 1, "a second load is not a second transition")
        self.assertNotIn("forecast now predicts", expected)
        self.assertNotIn("days", expected.split(";")[0], "a date, not 'within N days'")

    def test_it_resolves_when_the_projection_clears(self):
        self.declining_o_positive(29)
        self.forecast()
        with engine.begin() as c:
            c.execute(text("UPDATE blood_type_thresholds SET minimum_units = 5 WHERE facility_id = :f"), {"f": self.fid})
        self.forecast()
        self.assertEqual([read for _, read in self.shortages()], [True])

    def test_without_a_recommendation_it_still_says_when(self):
        with engine.begin() as c:
            app._check_forecast_shortage_notifications(c, self.fid, [{"type": "AB+", "days_until_threshold": 9}], today=date(2026, 11, 1))
        self.assertEqual(self.shortages(), [("AB+: projected below its minimum around 10 Nov.", False)])

    def test_the_synthetic_stand_in_never_notifies(self):
        stand_in = {"series": [], "alerts": [{"type": "O+", "severity": "warn", "reason": "r", "days_until_threshold": 5}],
                    "restock_recommendations": [], "restock_status": "on_track", "insufficient_history": []}
        with mock.patch.object(app, "_build_synthetic_stand_in_forecast", return_value=stand_in):
            r = app.get_forecast(facility_id=self.fid)
        self.assertEqual(r["forecast_source"], "synthetic_model_stand_in")
        self.assertEqual(self.shortages(), [])


class ExpiringAlertWordingTests(unittest.TestCase):
    def setUp(self):
        self.tag = uuid.uuid4().hex[:6].upper()
        self.bank, self.hospital = make_pair(self, self.tag)

    def test_both_endpoints_use_one_wording_with_kilometers_and_near_expiry(self):
        add(self.bank, f"{self.tag}-1", 2)
        add(self.bank, f"{self.tag}-2", 2)
        app.notify_nearby_hospitals_of_expiring_group(
            app.NotifyExpiringGroupBody(blood_type="O+", component="Packed RBC", expires_date=TODAY + timedelta(days=2)), acting_facility_id=self.bank)
        app.notify_nearby_hospitals_of_expiring_unit(f"{self.tag}-1", acting_facility_id=self.bank)
        sent = [m for (m,) in q("SELECT message FROM notifications WHERE facility_id = :h AND type = 'expiring_unit_alert' ORDER BY id", h=self.hospital)]
        bank = name_of(self.bank)
        self.assertEqual(len(sent), 2)
        self.assertRegex(sent[0], rf"^{re.escape(bank)} has 2 near-expiry O\+ Packed RBC units that expire in 2 days, [\d.]+ kilometers? away$")
        self.assertRegex(sent[1], rf"^{re.escape(bank)} has 1 near-expiry O\+ Packed RBC unit that expires in 2 days, [\d.]+ kilometers? away$")
        for m in sent:
            self.assertIsNone(re.search(r"\dkm|\dd left|near expiry", m), m)


class OfferResolutionTests(unittest.TestCase):
    """An expiring_unit_alert (a bank offering near-expiry units) resolves once the units it names have expired. It names them only
    in its text, so these build the offer through the real notify endpoints: rewording one without the sweep fails here."""

    def setUp(self):
        self.tag = uuid.uuid4().hex[:6].upper()
        self.bank, self.hospital = make_pair(self, self.tag)

    def offers(self):
        return q("SELECT id, message, (created_at AT TIME ZONE 'Asia/Manila')::date FROM notifications "
                 "WHERE facility_id = :h AND type = 'expiring_unit_alert' ORDER BY id", h=self.hospital)

    def resolved(self, nid):
        return q("SELECT read_at IS NOT NULL FROM notifications WHERE id = :i", i=nid)[0][0]

    def sweep(self, today):
        with engine.begin() as c:
            return app._resolve_stale_notifications(c, self.hospital, today=today)

    def test_a_group_offer_lives_through_its_last_usable_day_and_resolves_the_day_after(self):
        add(self.bank, f"{self.tag}-1", 2)
        app.notify_nearby_hospitals_of_expiring_group(
            app.NotifyExpiringGroupBody(blood_type="O+", component="Packed RBC", expires_date=TODAY + timedelta(days=2)), acting_facility_id=self.bank)
        (nid, message, written), = self.offers()
        self.assertIn("expires in 2 days", message)
        for offset in (0, 1, 2):  # the units are still usable through their expiry date
            self.sweep(written + timedelta(days=offset))
            self.assertFalse(self.resolved(nid), f"day {offset}")
        self.assertEqual(self.sweep(written + timedelta(days=3)), 1)
        self.assertTrue(self.resolved(nid))

    def test_a_same_day_offer_resolves_the_next_day(self):
        add(self.bank, f"{self.tag}-1", 0)
        app.notify_nearby_hospitals_of_expiring_group(
            app.NotifyExpiringGroupBody(blood_type="O+", component="Packed RBC", expires_date=TODAY), acting_facility_id=self.bank)
        (nid, message, written), = self.offers()
        self.assertIn("expires today", message)
        self.sweep(written)
        self.assertFalse(self.resolved(nid))
        self.sweep(written + timedelta(days=1))
        self.assertTrue(self.resolved(nid))

    def test_the_single_unit_offer_resolves_the_same_way(self):
        add(self.bank, f"{self.tag}-1", 3)
        app.notify_nearby_hospitals_of_expiring_unit(f"{self.tag}-1", acting_facility_id=self.bank)
        (nid, message, written), = self.offers()
        self.assertIn("expires in 3 days", message)
        self.sweep(written + timedelta(days=3))
        self.assertFalse(self.resolved(nid))
        self.sweep(written + timedelta(days=4))
        self.assertTrue(self.resolved(nid))

    def test_the_day_is_the_manila_day_not_the_utc_day(self):
        # written at 01:00 Manila on 31 Oct (17:00 UTC on the 30th): "expires today" means 31 Oct, usable through that day
        with engine.begin() as c:
            nid = c.execute(text("INSERT INTO notifications (facility_id, type, message, link, created_at) VALUES "
                                 "(:h, 'expiring_unit_alert', 'X has 1 near-expiry O+ Packed RBC unit that expires today, 2 kilometers away', 'sourcing:O+', "
                                 "'2026-10-30T17:00:00Z') RETURNING id"), {"h": self.hospital}).scalar()
        self.sweep(date(2026, 10, 31))
        self.assertFalse(self.resolved(nid))
        self.sweep(date(2026, 11, 1))
        self.assertTrue(self.resolved(nid))

    def test_through_the_list_it_resolves_without_anyone_asking(self):
        with engine.begin() as c:
            nid = c.execute(text("INSERT INTO notifications (facility_id, type, message, link, created_at) VALUES "
                                 "(:h, 'expiring_unit_alert', 'X has 2 near-expiry O+ Packed RBC units that expire in 1 day, 2 kilometers away', 'sourcing:O+', "
                                 ":at) RETURNING id"), {"h": self.hospital, "at": WRITTEN}).scalar()
        manila_day = q("SELECT (created_at AT TIME ZONE 'Asia/Manila')::date FROM notifications WHERE id = :i", i=nid)[0][0]
        with mock.patch.object(app, "business_today", return_value=manila_day + timedelta(days=1)):
            app.list_notifications(facility_id=self.hospital)
        self.assertFalse(self.resolved(nid))
        with mock.patch.object(app, "business_today", return_value=manila_day + timedelta(days=2)):
            self.assertEqual(app.notifications_unread_count(facility_id=self.hospital)["unread"], 0)
        self.assertTrue(self.resolved(nid))

    def test_an_offer_whose_text_it_cannot_read_is_left_alone(self):
        with engine.begin() as c:
            nid = c.execute(text("INSERT INTO notifications (facility_id, type, message, link, created_at) VALUES "
                                 "(:h, 'expiring_unit_alert', 'some other wording', 'sourcing:O+', '2020-01-01T00:00:00Z') RETURNING id"),
                            {"h": self.hospital}).scalar()
        self.sweep(date(2030, 1, 1))
        self.assertFalse(self.resolved(nid))
