"""GET /sync: per-section change tokens for the caller's own facility (see "Live sync" in main.py). Proves each token moves when it should
and only then, that one facility's changes never move another's, the ETag/304 contract, authentication, that a poll is one statement
and writes nothing, and that polling cannot trip a rate limiter. Needs the scratch database from conftest.py (TEST_DATABASE_URL)."""
import asyncio
import json
import re
import unittest
import uuid
from datetime import timedelta
from unittest import mock

from sqlalchemy import event, text

import auth
import main as app
from database import engine, read_engine
from tests.test_expired_transfer import TODAY, make_pair, q
from tests.test_request_component import add
from tests.test_request_transitions import TransitionBase

TOKEN = re.compile(r"^[0-9a-f]{12}$")


def payload(facility, today=TODAY):
    return app._sync_payload(facility, today)


def flat(p):
    return {"unread": p["notifications"]["unread"], "latest_id": p["notifications"]["latest_id"],
            "incoming": p["requests"]["incoming"], "outgoing": p["requests"]["outgoing"],
            "inventory": p["inventory"], "expiring": p["expiring"], "messages": p["messages"]}


def moved(before, after):
    """The sections whose value changed."""
    return sorted(k for k in before if before[k] != after[k])


async def call(path, headers=()):
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    path, _, query = path.partition("?")
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET", "path": path, "raw_path": path.encode(),
             "query_string": query.encode(), "root_path": "", "scheme": "http", "server": ("test", 80), "client": ("test", 1),
             "headers": [(b"host", b"test"), *[(k.lower().encode(), v.encode()) for k, v in headers]]}
    await app.app(scope, receive, send)
    start = next(m for m in sent if m["type"] == "http.response.start")
    return start["status"], {k.decode().lower(): v.decode() for k, v in start["headers"]}, b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")


def get(path, headers=()):
    return asyncio.run(call(path, headers))


class SyncBase(TransitionBase):
    """A bank and a hospital (the pair under test) plus a second, unrelated pair whose changes must never be visible to the first."""

    def setUp(self):
        app._active_account_until.clear()
        self.assertTrue(app._conversations_ready())   # remembered from here on: a poll is then one statement (the first call per process asks)
        super().setUp()
        self.other_bank, self.other_hospital = make_pair(self, self.tag + "X")
        with engine.begin() as c:
            for f in (self.bank, self.hospital, self.other_bank, self.other_hospital):
                for bt in sorted(app.VALID_BLOOD_TYPES):
                    c.execute(text("INSERT INTO blood_type_thresholds (facility_id, blood_type, minimum_units, maximum_units) VALUES (:f, :b, 5, 50)"),
                              {"f": f, "b": bt})
        self.addCleanup(app._active_account_until.clear)

    def snapshot(self):
        return {f: flat(payload(f)) for f in (self.bank, self.hospital, self.other_bank, self.other_hospital)}

    def assertOnlyMoved(self, before, expected):
        """`expected` = {facility: [sections that moved]}; every other facility and section must be exactly as it was."""
        after = self.snapshot()
        for facility in before:
            self.assertEqual(moved(before[facility], after[facility]), sorted(expected.get(facility, [])), f"facility {facility}")
        return after


class ShapeTests(SyncBase):
    def test_the_payload_is_small_and_opaque(self):
        p = payload(self.bank)
        self.assertEqual(set(p), {"notifications", "requests", "inventory", "expiring", "messages"})
        self.assertEqual(set(p["notifications"]), {"unread", "latest_id"})
        self.assertEqual(set(p["requests"]), {"incoming", "outgoing"})
        for token in (p["requests"]["incoming"], p["requests"]["outgoing"], p["inventory"], p["expiring"], p["messages"]):
            self.assertRegex(token, TOKEN)
        self.assertLess(len(json.dumps(p)), 280)

    def test_the_same_state_gives_the_same_answer(self):
        self.assertEqual(payload(self.bank), payload(self.bank))


class RequestTokenTests(SyncBase):
    def test_a_new_request_moves_the_suppliers_incoming_and_the_requesters_outgoing_and_nothing_else(self):
        before = self.snapshot()
        self.new_request()
        self.assertOnlyMoved(before, {self.bank: ["incoming", "unread", "latest_id"], self.hospital: ["outgoing"]})

    def test_every_status_change_moves_both_sides_tokens(self):
        rid = self.new_request()
        for label, act in (("accepted", lambda: self.accept(rid)), ("released", lambda: self.release(rid)),
                           ("received", lambda: app.confirm_receipt(rid, facility_id=self.hospital, user_id=self.hospital_user))):
            before = self.snapshot()
            act()
            after = self.snapshot()
            self.assertIn("incoming", moved(before[self.bank], after[self.bank]), label)
            self.assertIn("outgoing", moved(before[self.hospital], after[self.hospital]), label)
            for unrelated in (self.other_bank, self.other_hospital):
                self.assertEqual(before[unrelated], after[unrelated], label)

    def test_a_decline_and_a_cancel_move_them_too(self):
        declined, cancelled = self.new_request(), self.new_request()
        before = self.snapshot()
        app.decline_request(declined, facility_id=self.bank, user_id=self.bank_user)
        mid = self.snapshot()
        self.assertEqual(moved(before[self.bank], mid[self.bank]).count("incoming"), 1)
        self.assertIn("outgoing", moved(before[self.hospital], mid[self.hospital]))
        app.cancel_request(cancelled, facility_id=self.hospital, user_id=self.hospital_user)
        after = self.snapshot()
        self.assertIn("incoming", moved(mid[self.bank], after[self.bank]))
        self.assertIn("outgoing", moved(mid[self.hospital], after[self.hospital]))

    def test_a_banks_own_outgoing_and_incoming_tokens_are_independent(self):
        rid = self.new_request()                        # hospital -> bank: for the bank this is incoming
        before = self.snapshot()
        self.assertEqual(before[self.bank]["outgoing"], payload(self.bank)["requests"]["outgoing"])
        self.accept(rid)
        after = self.snapshot()
        self.assertNotIn("outgoing", moved(before[self.bank], after[self.bank]))


class InventoryTokenTests(SyncBase):
    def test_adding_a_unit_moves_inventory_and_not_the_other_facilities(self):
        before = self.snapshot()
        add(self.bank, f"{self.tag}-A", 30)
        self.assertOnlyMoved(before, {self.bank: ["inventory"]})   # 30 days out: not in the 7-day expiring window

    def test_a_unit_expiring_soon_moves_expiring_as_well(self):
        before = self.snapshot()
        add(self.bank, f"{self.tag}-S", 3)
        self.assertOnlyMoved(before, {self.bank: ["inventory", "expiring"]})

    def test_a_reservation_moves_both_because_the_unit_is_no_longer_on_offer(self):
        add(self.bank, f"{self.tag}-S", 4)
        rid = self.new_request()
        self.accept(rid)
        before = self.snapshot()
        app.confirm_release(rid, facility_id=self.bank, user_id=self.bank_user)       # reserves the unit
        after = self.snapshot()
        self.assertIn("inventory", moved(before[self.bank], after[self.bank]))
        self.assertIn("expiring", moved(before[self.bank], after[self.bank]))

    def test_archiving_a_unit_moves_inventory_and_expiring(self):
        add(self.bank, f"{self.tag}-S", 2)
        before = self.snapshot()
        with engine.begin() as c:
            c.execute(text("UPDATE blood_units SET archived_at = now() WHERE facility_id = :f"), {"f": self.bank})
        self.assertOnlyMoved(before, {self.bank: ["inventory", "expiring"]})

    def test_an_edit_in_place_moves_inventory_and_leaves_expiring_when_the_expiry_is_unchanged(self):
        add(self.bank, f"{self.tag}-L", 30)
        before = self.snapshot()
        with engine.begin() as c:                      # a CSV re-upload changing a unit's shelf
            c.execute(text("UPDATE blood_units SET location = 'Shelf Z' WHERE facility_id = :f"), {"f": self.bank})
        self.assertOnlyMoved(before, {self.bank: ["inventory"]})

    def test_a_threshold_change_moves_inventory(self):
        before = self.snapshot()
        app.update_threshold("O+", app.UpdateThresholdBody(minimum_units=9, maximum_units=60), facility_id=self.bank)
        self.assertOnlyMoved(before, {self.bank: ["inventory"]})

    def test_a_deleted_unit_moves_inventory(self):
        add(self.bank, f"{self.tag}-D", 30)
        before = self.snapshot()
        with engine.begin() as c:
            c.execute(text("DELETE FROM blood_units WHERE din = :d"), {"d": f"{self.tag}-D"})
        self.assertOnlyMoved(before, {self.bank: ["inventory"]})

    def test_midnight_moves_inventory_and_expiring_without_any_row_changing(self):
        add(self.bank, f"{self.tag}-S", 3)
        today, tomorrow = payload(self.bank, TODAY), payload(self.bank, TODAY + timedelta(days=1))
        self.assertEqual(moved(flat(today), flat(tomorrow)), ["expiring", "inventory"])   # units expire at the date change

    def test_an_archived_expired_unit_no_longer_matters(self):
        with engine.begin() as c:
            c.execute(text("INSERT INTO blood_units (din, blood_type, component, location, volume_ml, collected_date, expires_date, facility_id, archived_at) "
                           "VALUES (:d, 'O+', 'Packed RBC', 'A', 450, :c, :e, :f, now())"),
                      {"d": f"{self.tag}-OLD", "c": TODAY - timedelta(days=60), "e": TODAY - timedelta(days=20), "f": self.bank})
        before = self.snapshot()
        with engine.begin() as c:
            c.execute(text("UPDATE blood_units SET location = 'Z' WHERE din = :d"), {"d": f"{self.tag}-OLD"})
        self.assertOnlyMoved(before, {})             # an archived, expired unit is on no screen: not worth a refetch


class NotificationTests(SyncBase):
    def test_a_notification_arriving_moves_the_two_numbers_the_bell_draws(self):
        before = payload(self.bank)["notifications"]
        with engine.begin() as c:
            nid = c.execute(text("INSERT INTO notifications (facility_id, type, message, link) VALUES (:f, 'request_accepted', 'x', 'dashboard') RETURNING id"),
                            {"f": self.bank}).scalar()
        after = payload(self.bank)["notifications"]
        self.assertEqual((after["unread"], after["latest_id"]), (before["unread"] + 1, nid))

    def test_reading_one_lowers_unread_and_keeps_latest_id(self):
        with engine.begin() as c:
            nid = c.execute(text("INSERT INTO notifications (facility_id, type, message, link) VALUES (:f, 'request_accepted', 'x', 'dashboard') RETURNING id"),
                            {"f": self.bank}).scalar()
        before = payload(self.bank)["notifications"]
        app.mark_notification_read(nid, facility_id=self.bank)
        after = payload(self.bank)["notifications"]
        self.assertEqual((after["unread"], after["latest_id"]), (before["unread"] - 1, before["latest_id"]))

    def test_the_numbers_are_exactly_what_unread_count_returns_including_stale_rows_left_out(self):
        rid = self.new_request()                       # the supplier's incoming_request notification
        self.accept(rid)                               # resolves it
        with engine.begin() as c:
            c.execute(text("UPDATE notifications SET read_at = NULL WHERE facility_id = :f"), {"f": self.bank})   # as if the event had missed it
        self.assertEqual(payload(self.bank)["notifications"], app.notifications_unread_count(facility_id=self.bank))

    def test_another_facilitys_notification_is_invisible(self):
        before = self.snapshot()
        with engine.begin() as c:
            c.execute(text("INSERT INTO notifications (facility_id, type, message, link) VALUES (:f, 'request_accepted', 'x', 'dashboard')"), {"f": self.other_bank})
        self.assertOnlyMoved(before, {self.other_bank: ["unread", "latest_id"]})


class IsolationTests(SyncBase):
    def test_everything_another_pair_does_leaves_this_pair_untouched(self):
        before = self.snapshot()
        add(self.other_bank, f"{self.tag}-X1", 3)
        add(self.other_bank, f"{self.tag}-X2", 30)
        with engine.begin() as c:
            rid = c.execute(text("INSERT INTO requests (requesting_facility_id, supplying_facility_id, blood_type, quantity, emergency_type, component) "
                                 "VALUES (:h, :b, 'O+', 1, 'trauma', 'Packed RBC') RETURNING id"), {"h": self.other_hospital, "b": self.other_bank}).scalar()
            c.execute(text("UPDATE requests SET status = 'accepted', accepted_at = now() WHERE id = :r"), {"r": rid})
        after = self.snapshot()
        for facility in (self.bank, self.hospital):
            self.assertEqual(before[facility], after[facility])
        self.assertNotEqual(before[self.other_bank], after[self.other_bank])

    def test_a_request_is_visible_to_its_two_parties_and_nobody_else(self):
        nothing = app._token("in", 0, 0, None)                               # what a facility with no requests reads
        self.assertEqual(payload(self.other_bank)["requests"]["incoming"], nothing)
        self.new_request()
        self.assertNotEqual(payload(self.bank)["requests"]["incoming"], nothing)
        self.assertEqual(payload(self.other_bank)["requests"]["incoming"], nothing)       # still nothing for the unrelated bank
        self.assertEqual(payload(self.other_hospital)["requests"]["outgoing"], app._token("out", 0, 0, None))


class MessageTokenTests(SyncBase):
    """The messages token moves when a message arrives or is sent in any of the caller's conversations and when its own read position
    moves, and for no one else (see "Live sync" in main.py)."""

    def setUp(self):
        super().setUp()
        self.rid = self.new_request()

    def send(self, sender, other, text_="hi", request_id=None):
        return app.send_conversation_message(other, app.SendConversationMessageBody(message=text_, request_id=request_id), facility_id=sender)

    def test_a_new_message_moves_both_partys_token_and_nothing_else_for_anyone(self):
        before = self.snapshot()
        self.send(self.hospital, self.bank)
        self.assertOnlyMoved(before, {self.bank: ["messages"], self.hospital: ["messages"]})

    def test_a_message_sent_the_old_way_moves_it_too(self):
        self.accept(self.rid)
        before = self.snapshot()
        app.send_request_message(self.rid, app.SendRequestMessageBody(message="old"), facility_id=self.bank)
        self.assertOnlyMoved(before, {self.bank: ["messages"], self.hospital: ["messages"]})

    def test_a_row_with_no_conversation_id_moves_it_by_way_of_its_request(self):
        before = self.snapshot()
        with engine.begin() as c:
            c.execute(text("INSERT INTO request_messages (request_id, sender_facility_id, message) VALUES (:r, :s, 'from an old instance')"),
                      {"r": self.rid, "s": self.hospital})
        self.assertOnlyMoved(before, {self.bank: ["messages"], self.hospital: ["messages"]})

    def test_reading_moves_only_the_readers_token_and_a_read_that_changes_nothing_moves_nothing(self):
        self.send(self.hospital, self.bank)
        before = self.snapshot()
        app.mark_conversation_read(self.hospital, None, facility_id=self.bank)
        after = self.assertOnlyMoved(before, {self.bank: ["messages"]})
        app.mark_conversation_read(self.hospital, None, facility_id=self.bank)       # forward only: nothing to move
        self.assertEqual(self.snapshot(), after)

    def test_opening_a_thread_that_has_no_messages_yet_moves_only_the_openers_token(self):
        before = self.snapshot()
        app.mark_conversation_read(self.hospital, None, facility_id=self.bank)
        self.assertOnlyMoved(before, {self.bank: ["messages"]})

    def test_another_pairs_conversation_is_invisible(self):
        with engine.begin() as c:
            rid = c.execute(text("INSERT INTO requests (requesting_facility_id, supplying_facility_id, blood_type, quantity, emergency_type, status, component) "
                                 "VALUES (:h, :b, 'O+', 1, 'restock', 'pending', 'Packed RBC') RETURNING id"), {"h": self.other_hospital, "b": self.other_bank}).scalar()
        before = self.snapshot()
        self.send(self.other_hospital, self.other_bank, "elsewhere", request_id=rid)
        app.mark_conversation_read(self.other_hospital, None, facility_id=self.other_bank)
        self.assertOnlyMoved(before, {self.other_bank: ["messages"], self.other_hospital: ["messages"]})

    def test_the_token_says_nothing_but_that_something_changed(self):
        a = payload(self.bank)["messages"]
        self.send(self.hospital, self.bank, "secret text")
        b = payload(self.bank)["messages"]
        self.assertNotEqual(a, b)
        self.assertRegex(b, TOKEN)
        self.assertNotIn("secret", json.dumps(payload(self.bank)))

    def test_the_etag_and_the_304_follow_it(self):
        with engine.begin() as c:
            uid = c.execute(text("INSERT INTO users (email, password_hash, facility_id, role) VALUES (:e, 'x', :f, 'staff') RETURNING id"),
                            {"e": f"msg-{self.tag}@example.com", "f": self.bank}).scalar()
        token = auth.create_access_token(user_id=uid, email="x@example.com", facility_id=self.bank, role="staff")
        headers = [("authorization", f"Bearer {token}")]
        status, h, _ = get("/sync", headers)
        self.assertEqual(status, 200)
        self.assertEqual(get("/sync", headers + [("if-none-match", h["etag"])])[0], 304)
        self.send(self.hospital, self.bank)
        status, h2, _ = get("/sync", headers + [("if-none-match", h["etag"])])
        self.assertEqual(status, 200)
        self.assertNotEqual(h2["etag"], h["etag"])

    def test_the_first_poll_of_a_process_also_asks_if_the_migration_is_in_and_never_again(self):
        def count():
            seen = []
            record = lambda c, cur, statement, *a: seen.append(statement)  # noqa: E731
            event.listen(read_engine, "before_cursor_execute", record)
            try:
                payload(self.bank)
            finally:
                event.remove(read_engine, "before_cursor_execute", record)
            return seen
        app._conversations_seen, app._conversations_checked_at = False, None
        self.assertEqual(len(count()), 2)     # the readiness question, then the one sync statement
        self.assertEqual(len(count()), 1)     # a positive answer is remembered for the life of the process

    def test_a_poll_is_still_one_statement_with_the_messages_token(self):
        seen = []
        record = lambda c, cur, statement, *a: seen.append(statement)  # noqa: E731
        event.listen(read_engine, "before_cursor_execute", record)
        try:
            payload(self.bank)
        finally:
            event.remove(read_engine, "before_cursor_execute", record)
        self.assertEqual(len(seen), 1, seen)


class EndpointTests(SyncBase):
    def setUp(self):
        super().setUp()
        with engine.begin() as c:
            self.uid = c.execute(text("INSERT INTO users (email, password_hash, facility_id, role) VALUES (:e, 'x', :f, 'staff') RETURNING id"),
                                 {"e": f"sync-{self.tag}@example.com", "f": self.bank}).scalar()
        token = auth.create_access_token(user_id=self.uid, email="x@example.com", facility_id=self.bank, role="staff")
        self.auth = [("authorization", f"Bearer {token}")]

    def test_a_signed_in_call_is_200_json_with_an_etag(self):
        status, headers, body = get("/sync", self.auth)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), payload(self.bank, app.business_today()))
        self.assertRegex(headers["etag"], r'^"[0-9a-f]{16}"$')
        self.assertEqual(headers["cache-control"], "private, no-cache")

    def test_nothing_changed_is_a_bodyless_304_and_a_change_is_a_new_200(self):
        _, headers, _ = get("/sync", self.auth)
        etag = headers["etag"]
        status, h304, body = get("/sync", [*self.auth, ("if-none-match", etag)])
        self.assertEqual((status, body), (304, b""))
        self.assertEqual(h304["etag"], etag)
        add(self.bank, f"{self.tag}-N", 30)
        status, headers2, body = get("/sync", [*self.auth, ("if-none-match", etag)])
        self.assertEqual(status, 200)
        self.assertNotEqual(headers2["etag"], etag)
        self.assertEqual(get("/sync", [*self.auth, ("if-none-match", headers2["etag"])])[0], 304)

    def test_weak_lists_and_star_forms_of_if_none_match_are_understood(self):
        etag = get("/sync", self.auth)[1]["etag"]
        for header in (f"W/{etag}", f'"nope", {etag}', f'"nope", W/{etag}', "*"):
            self.assertEqual(get("/sync", [*self.auth, ("if-none-match", header)])[0], 304, header)
        for header in ('"something-else"', "", "garbage"):
            self.assertEqual(get("/sync", [*self.auth, ("if-none-match", header)])[0], 200, header)

    def test_the_etag_is_per_facility_state_not_shared(self):
        mine = get("/sync", self.auth)[1]["etag"]
        add(self.other_bank, f"{self.tag}-Y", 30)
        self.assertEqual(get("/sync", [*self.auth, ("if-none-match", mine)])[0], 304)   # someone else's stock: still nothing for me

    def test_unauthenticated_is_401(self):
        for headers in ([], [("authorization", "Bearer not-a-token")], [("authorization", "Basic abc")]):
            status, _, _ = get("/sync", headers)
            self.assertEqual(status, 401, headers)

    def drop_user(self, user_id):
        with engine.begin() as c:
            c.execute(text("DELETE FROM users WHERE id = :i"), {"i": user_id})

    def test_an_admin_account_has_no_facility_so_it_is_refused(self):
        with engine.begin() as c:
            admin = c.execute(text("INSERT INTO users (email, password_hash, role) VALUES (:e, 'x', 'admin') RETURNING id"),
                              {"e": f"sync-admin-{self.tag}@example.com"}).scalar()
        self.addCleanup(self.drop_user, admin)
        token = auth.create_access_token(user_id=admin, email="a@example.com", facility_id=None, role="admin")
        self.assertEqual(get("/sync", [("authorization", f"Bearer {token}")])[0], 403)

    def test_a_deactivated_account_is_refused_even_with_a_valid_token(self):
        self.assertEqual(get("/sync", self.auth)[0], 200)
        app.admin_update_facility_status(self.bank, app.UpdateFacilityStatusBody(is_active=False))
        status, _, body = get("/sync", self.auth)
        self.assertEqual(status, 403)
        self.assertIn("deactivated", json.loads(body)["detail"])

    def test_a_deleted_user_is_refused(self):
        with engine.begin() as c:
            c.execute(text("DELETE FROM users WHERE id = :u"), {"u": self.uid})
        app._active_account_until.clear()
        self.assertEqual(get("/sync", self.auth)[0], 401)

    def test_a_request_for_someone_elses_facility_cannot_be_made(self):
        # the facility comes from the verified token, never from the request: a header or query naming another changes nothing
        _, h1, _ = get("/sync", self.auth)
        _, h2, _ = get("/sync?facility_id=%d" % self.other_bank, self.auth)
        self.assertEqual(h1["etag"], h2["etag"])


class CostTests(SyncBase):
    def counted(self, fn):
        seen = []
        record = lambda c, cur, statement, *a: seen.append(" ".join(statement.split()))  # noqa: E731
        for e in (engine, read_engine):
            event.listen(e, "before_cursor_execute", record)
        try:
            fn()
        finally:
            for e in (engine, read_engine):
                event.remove(e, "before_cursor_execute", record)
        return seen

    def test_a_poll_is_one_statement_on_the_read_engine_and_writes_nothing(self):
        on_read = []
        record = lambda c, cur, statement, *a: on_read.append(statement)  # noqa: E731
        event.listen(read_engine, "before_cursor_execute", record)
        try:
            seen = self.counted(lambda: payload(self.bank))
        finally:
            event.remove(read_engine, "before_cursor_execute", record)
        self.assertEqual(len(seen), 1, seen)
        self.assertEqual(len(on_read), 1)    # that one statement ran on the read engine, which is read-only at the database
        self.assertFalse(re.match(r"\s*(INSERT|UPDATE|DELETE)", seen[0]))

    def test_the_endpoint_adds_only_the_active_account_check_every_other_endpoint_pays_too(self):
        with engine.begin() as c:
            uid = c.execute(text("INSERT INTO users (email, password_hash, facility_id, role) VALUES (:e, 'x', :f, 'staff') RETURNING id"),
                            {"e": f"cost-{self.tag}@example.com", "f": self.bank}).scalar()
        token = auth.create_access_token(user_id=uid, email="x@example.com", facility_id=self.bank, role="staff")
        headers = [("authorization", f"Bearer {token}")]
        first = self.counted(lambda: get("/sync", headers))
        again = self.counted(lambda: get("/sync", headers))
        self.assertEqual(len(first), 2, first)            # the account check, then the sync statement
        self.assertEqual(len(again), 1, again)            # the check is remembered for 30 s: one statement per poll

    def test_polling_never_trips_or_touches_a_rate_limiter(self):
        limiters = (app._LOGIN_LIMITER, app._FORGOT_LIMITER, app._PASSWORD_CHANGE_LIMITER, app._GEOCODE_LIMITER)
        before = [dict(lim._events) for lim in limiters]
        with engine.begin() as c:
            uid = c.execute(text("INSERT INTO users (email, password_hash, facility_id, role) VALUES (:e, 'x', :f, 'staff') RETURNING id"),
                            {"e": f"poll-{self.tag}@example.com", "f": self.bank}).scalar()
        token = auth.create_access_token(user_id=uid, email="x@example.com", facility_id=self.bank, role="staff")
        for _ in range(60):                                # four minutes of polling at 4 s, far more than the real 15 s
            status, _, _ = get("/sync", [("authorization", f"Bearer {token}"), ("true-client-ip", "81.107.0.110")])
            self.assertIn(status, (200, 304))
        self.assertEqual([dict(lim._events) for lim in limiters], before)

    def test_the_sync_functions_are_on_the_read_engine_only(self):
        import inspect
        source = inspect.getsource(app._read_sync_state)
        self.assertIn("read_engine.connect()", source)
        self.assertNotIn("engine.begin", source)
        self.assertTrue(hasattr(app._read_sync_state, "__wrapped__"))      # retry_read, like every read-engine function
