"""Conversations: one per pair of facilities (see "Conversations" in main.py), stage 1 of the Messages redesign, server side only.

What these tests prove, on the scratch database:
  - the pair is stored once, smaller id first, and the database refuses a reversed or a duplicate pair;
  - rows are created lazily (a first message, or opening the thread), NEVER by creating a request or by any transition, and a pair with
    requests and no row still appears in the list and in the thread, so a missing row can never break anything;
  - the list: one row per other facility, its shape, its ordering (a pair that needs my action first, then latest activity), the unread
    count after my read position, the count of requests needing my action (and that the SQL and the Python rule agree), the open count;
  - the thread: messages and request events merged in time order, each request's card data (units released and received), finished
    requests as a page of five with "show earlier", the message window and its cap;
  - access: only the two parties; a third facility asks about its own pair with someone, which does not exist (404); a deactivated
    account and a missing token are refused like everywhere else;
  - messages: with and without a request, a request of another pair refused, blank and over-2000 refused, allowed whenever the pair has a
    request of any status, the rate limit (shared with the old endpoint);
  - the read position moves forward only, never beyond the newest message, and only my own row;
  - the old per-request endpoints keep working and stay consistent with the conversation, with the migration in and before it;
  - the reads are pure reads on the read engine, one statement for the list;
  - the migration: part 1 run twice, the backfill, the old INSERT after it; part 2 and its refusal.

Needs the scratch database from conftest.py (TEST_DATABASE_URL)."""
import asyncio
import inspect
import pathlib
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from unittest import mock

import psycopg2
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import event, text

import auth
import main as app
from database import engine, read_engine
from tests.test_expired_transfer import make_pair, q
from tests.test_request_component import add
from tests.test_request_transitions import TransitionBase

NOW = datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc)


def at(minutes):
    return NOW + timedelta(minutes=minutes)


class ConvBase(TransitionBase):
    """A bank and a hospital with a request between them, plus an unrelated pair (a third bank and hospital) that must never see them."""

    def setUp(self):
        app._active_account_until.clear()
        app._conversations_seen = False
        app._conversations_checked_at = None
        super().setUp()
        self.assertTrue(app._conversations_ready(), "the scratch schema has the conversation tables (tests/schema.sql)")
        self.third_bank, self.third_hospital = make_pair(self, self.tag + "T")
        self.addCleanup(app._active_account_until.clear)

    # -- helpers
    def send(self, sender, other, text_, request_id=None):
        return app.send_conversation_message(other, app.SendConversationMessageBody(message=text_, request_id=request_id), facility_id=sender)

    def thread(self, me, other, **kw):
        return app.get_conversation(other, facility_id=me, **kw)

    def listing(self, me):
        return app.list_conversations(facility_id=me)["conversations"]

    def row_for(self, me, other):
        return next((c for c in self.listing(me) if c["facility"]["id"] == other), None)

    def read(self, me, other, up_to=None):
        body = None if up_to is None else app.MarkConversationReadBody(up_to_message_id=up_to)
        return app.mark_conversation_read(other, body, facility_id=me)

    def set_message_time(self, message_id, when):
        with engine.begin() as c:
            c.execute(text("UPDATE request_messages SET created_at = :t WHERE id = :i"), {"t": when, "i": message_id})

    def conversation_rows(self):
        return q("SELECT facility_low_id, facility_high_id FROM conversations WHERE facility_low_id = ANY(:f) OR facility_high_id = ANY(:f)",
                 f=[self.bank, self.hospital, self.third_bank, self.third_hospital])

    def insert_old_style_message(self, request_id, sender, message="from an old instance"):
        """What the previous server code wrote: no conversation_id."""
        with engine.begin() as c:
            return c.execute(text("INSERT INTO request_messages (request_id, sender_facility_id, message) VALUES (:r, :s, :m) RETURNING id"),
                             {"r": request_id, "s": sender, "m": message}).scalar()

    def message_ids(self, me, other):
        return [i["id"] for i in self.thread(me, other)["thread"] if i["type"] == "message"]


# ──────────────────────────── the pair ────────────────────────────

class PairTests(ConvBase):
    def test_a_pair_is_stored_smaller_id_first(self):
        self.assertEqual(app._pair(9, 4), (4, 9))
        self.assertEqual(app._pair(4, 9), (4, 9))

    def test_the_database_refuses_a_reversed_pair_and_a_duplicate(self):
        low, high = sorted((self.bank, self.hospital))
        with engine.begin() as c:
            c.execute(text("INSERT INTO conversations (facility_low_id, facility_high_id) VALUES (:l, :h)"), {"l": low, "h": high})
        for pair in ((high, low), (low, high), (low, low)):
            with self.assertRaises(Exception) as ctx, engine.begin() as c:
                c.execute(text("INSERT INTO conversations (facility_low_id, facility_high_id) VALUES (:l, :h)"), {"l": pair[0], "h": pair[1]})
            self.assertIn("conversations_pair", str(ctx.exception), pair)

    def test_either_side_sending_first_makes_the_one_row(self):
        self.new_request()
        self.send(self.hospital, self.bank, "hello")
        self.send(self.bank, self.hospital, "hi")
        self.assertEqual(self.conversation_rows(), [tuple(sorted((self.bank, self.hospital)))])


# ──────────────────────────── lazy creation, and a missing row breaks nothing ────────────────────────────

class LazyCreationTests(ConvBase):
    def test_requests_and_every_transition_never_touch_a_conversation_row(self):
        rid = self.new_request()
        self.accept(rid)
        self.release(rid)
        app.confirm_receipt(rid, facility_id=self.hospital, user_id=self.hospital_user)
        second = self.new_request()
        app.cancel_request(second, facility_id=self.hospital, user_id=self.hospital_user)
        third = self.new_request()
        app.decline_request(third, facility_id=self.bank, user_id=self.bank_user)
        self.assertEqual(self.conversation_rows(), [])
        self.assertEqual(q("SELECT count(*) FROM conversation_reads WHERE facility_id = ANY(:f)", f=[self.bank, self.hospital])[0][0], 0)

    def test_none_of_the_request_functions_even_mentions_a_conversation(self):
        for fn in (app.create_request, app.accept_request, app.decline_request, app.cancel_request, app.confirm_release, app.confirm_receipt):
            self.assertNotIn("conversation", inspect.getsource(inspect.unwrap(fn)).lower(), fn.__name__)

    def test_a_pair_with_requests_and_no_row_is_listed_and_has_a_thread(self):
        rid = self.new_request()
        for me, other in ((self.bank, self.hospital), (self.hospital, self.bank)):
            row = self.row_for(me, other)
            self.assertIsNotNone(row)
            self.assertEqual((row["requests_total"], row["open_requests"], row["unread"], row["last_message"]), (1, 1, 0, None))
            thread = self.thread(me, other)
            self.assertIsNone(thread["conversation_id"])
            self.assertEqual([c["request"]["id"] for c in thread["requests"]], [rid])
        self.assertEqual(self.conversation_rows(), [])   # reading made nothing

    def test_the_first_message_makes_the_row(self):
        self.new_request()
        self.send(self.hospital, self.bank, "first")
        self.assertEqual(len(self.conversation_rows()), 1)
        self.assertIsNotNone(self.thread(self.bank, self.hospital)["conversation_id"])

    def test_opening_the_thread_makes_the_row_and_my_read_row_only(self):
        self.new_request()
        result = self.read(self.hospital, self.bank)
        self.assertEqual((result["last_read_message_id"], result["unread"]), (0, 0))
        self.assertEqual(len(self.conversation_rows()), 1)
        self.assertEqual(q("SELECT facility_id FROM conversation_reads WHERE conversation_id = :c", c=result["conversation_id"]), [(self.hospital,)])

    def test_a_pair_with_a_conversation_row_and_no_requests_is_still_listed(self):
        rid = self.new_request()
        self.send(self.hospital, self.bank, "kept")
        with engine.begin() as c:
            c.execute(text("UPDATE request_messages SET request_id = NULL WHERE request_id = :r"), {"r": rid})
            c.execute(text("DELETE FROM requests WHERE id = :r"), {"r": rid})
        row = self.row_for(self.bank, self.hospital)
        self.assertIsNotNone(row)
        self.assertEqual((row["requests_total"], row["open_requests"], row["needs_action"]), (0, 0, 0))
        self.assertEqual(row["last_message"]["text"], "kept")
        self.assertEqual(self.thread(self.bank, self.hospital)["requests"], [])


# ──────────────────────────── the list ────────────────────────────

class ListTests(ConvBase):
    def test_the_shape(self):
        self.new_request()
        mid = self.send(self.hospital, self.bank, "Please confirm.")["id"]
        self.set_message_time(mid, at(5))
        result = app.list_conversations(facility_id=self.bank)
        self.assertEqual(set(result), {"conversations", "needs_action"})
        row = result["conversations"][0]
        self.assertEqual(set(row), {"facility", "last_message", "unread", "needs_action", "open_requests", "requests_total", "last_activity_at"})
        self.assertEqual(set(row["facility"]), {"id", "name", "facility_type", "is_active"})
        self.assertEqual(row["facility"]["id"], self.hospital)
        self.assertEqual(row["facility"]["facility_type"], "hospital")
        self.assertEqual(set(row["last_message"]), {"id", "sender_facility_id", "text", "created_at"})
        self.assertEqual((row["last_message"]["text"], row["last_message"]["sender_facility_id"], row["last_message"]["created_at"]), ("Please confirm.", self.hospital, at(5)))
        self.assertEqual((row["unread"], row["needs_action"], row["open_requests"], row["requests_total"]), (1, 1, 1, 1))
        self.assertEqual(result["needs_action"], 1)

    def test_the_last_message_text_is_cut_for_the_preview(self):
        self.new_request()
        self.send(self.hospital, self.bank, "x" * 2000)
        self.assertEqual(len(self.row_for(self.bank, self.hospital)["last_message"]["text"]), app.CONVERSATION_PREVIEW_CHARS)

    def test_ordering_needs_my_action_first_then_latest_activity(self):
        # The bank is `me`; three hospitals. H1 has a pending request (a move for the bank) but the oldest activity; H2 has nothing for the
        # bank and the newest message; H3 has nothing for the bank and an older message.
        h1, h2, h3 = (make_pair(self, f"{self.tag}{n}")[1] for n in "ABC")
        with engine.begin() as c:
            for h, status in ((h1, "pending"), (h2, "declined"), (h3, "cancelled")):
                c.execute(
                    text("INSERT INTO requests (requesting_facility_id, supplying_facility_id, blood_type, quantity, emergency_type, status, component, created_at) "
                         "VALUES (:h, :b, 'O+', 1, 'restock', :s, 'Packed RBC', :t) RETURNING id"),
                    {"h": h, "b": self.bank, "s": status, "t": at(-600)})
        for h, minutes in ((h1, 1), (h2, 50), (h3, 20)):
            self.set_message_time(self.send(h, self.bank, f"msg {minutes}")["id"], at(minutes))
        order = [c["facility"]["id"] for c in self.listing(self.bank) if c["facility"]["id"] in (h1, h2, h3)]
        self.assertEqual(order, [h1, h2, h3])
        # the hospital side: H2's own list has only the bank, of course
        self.assertEqual([c["facility"]["id"] for c in self.listing(h2)], [self.bank])

    def test_opening_a_thread_never_reorders_the_list(self):
        h2 = make_pair(self, self.tag + "O")[1]
        with engine.begin() as c:
            for h, minutes in ((self.hospital, -100), (h2, -50)):
                c.execute(text("INSERT INTO requests (requesting_facility_id, supplying_facility_id, blood_type, quantity, emergency_type, status, component, created_at) "
                               "VALUES (:h, :b, 'O+', 1, 'restock', 'cancelled', 'Packed RBC', :t)"), {"h": h, "b": self.bank, "t": at(minutes)})
        order = lambda: [c["facility"]["id"] for c in self.listing(self.bank) if c["facility"]["id"] in (self.hospital, h2)]  # noqa: E731
        self.assertEqual(order(), [h2, self.hospital])
        self.read(self.bank, self.hospital)      # opening the older conversation makes its row now, which is not activity
        self.assertEqual(order(), [h2, self.hospital])

    def test_a_request_moving_counts_as_activity(self):
        h2 = make_pair(self, self.tag + "M")[1]
        old = self.new_request()
        self.set_message_time(self.send(self.hospital, self.bank, "old")["id"], at(-5000))
        with engine.begin() as c:
            c.execute(text("UPDATE requests SET created_at = :t WHERE id = :r"), {"t": at(-5000), "r": old})
            c.execute(text("INSERT INTO requests (requesting_facility_id, supplying_facility_id, blood_type, quantity, emergency_type, status, component, created_at, declined_at) "
                           "VALUES (:h, :b, 'O+', 1, 'restock', 'declined', 'Packed RBC', :t, :t)"), {"h": h2, "b": self.bank, "t": at(-10)})
        order = [c["facility"]["id"] for c in self.listing(self.bank) if c["facility"]["id"] in (self.hospital, h2)]
        self.assertEqual(order[0], self.hospital, "the pending request needs the bank's action, so it goes first whatever its age")
        with engine.begin() as c:
            c.execute(text("UPDATE requests SET status = 'cancelled', cancelled_at = :t WHERE id = :r"), {"t": at(-5000), "r": old})
        order = [c["facility"]["id"] for c in self.listing(self.bank) if c["facility"]["id"] in (self.hospital, h2)]
        self.assertEqual(order, [h2, self.hospital], "no action waiting anywhere: the newer activity first")

    def test_needs_action_open_and_total_in_every_state_and_the_sql_agrees_with_the_python_rule(self):
        pending = self.new_request()
        unreleased = self.new_request(); self.accept(unreleased)
        released = self.new_request(); self.accept(released); self.release(released)
        done = self.new_request(); self.accept(done); self.release(done)
        app.confirm_receipt(done, facility_id=self.hospital, user_id=self.hospital_user)
        declined = self.new_request(); app.decline_request(declined, facility_id=self.bank, user_id=self.bank_user)
        cancelled = self.new_request(); app.cancel_request(cancelled, facility_id=self.hospital, user_id=self.hospital_user)
        everything = [dict(r._mapping) for r in self._all_requests()]
        self.assertEqual(len(everything), 6)
        for me, other, expected in ((self.bank, self.hospital, {pending, unreleased}), (self.hospital, self.bank, {released})):
            row = self.row_for(me, other)
            python_rule = {r["id"] for r in everything if app._request_needs_my_action(r, me)}
            self.assertEqual(python_rule, expected, "the rule: accept/decline, confirm release, confirm receipt; never cancel")
            self.assertEqual(row["needs_action"], len(expected), "the list's SQL agrees with the Python rule")
            self.assertEqual(row["open_requests"], 3)          # pending, unreleased, released
            self.assertEqual(row["requests_total"], 6)
            self.assertEqual(self.thread(me, other)["counts"], {"open_requests": 3, "needs_action": len(expected), "requests_total": 6})
        self.assertEqual(app.list_conversations(facility_id=self.bank)["needs_action"], 2)

    def _all_requests(self):
        with engine.connect() as c:
            return c.execute(text("SELECT * FROM requests WHERE requesting_facility_id = :h AND supplying_facility_id = :b ORDER BY id"),
                             {"h": self.hospital, "b": self.bank}).all()

    def test_a_bank_to_bank_pair_is_directional(self):
        other_bank = make_pair(self, self.tag + "R")[0]
        with engine.begin() as c:
            rid = c.execute(text("INSERT INTO requests (requesting_facility_id, supplying_facility_id, blood_type, quantity, emergency_type, status, component) "
                                 "VALUES (:r, :s, 'O+', 1, 'restock', 'pending', 'Packed RBC') RETURNING id"), {"r": self.bank, "s": other_bank}).scalar()
        self.assertEqual(self.row_for(other_bank, self.bank)["needs_action"], 1)     # the supplier decides
        self.assertEqual(self.row_for(self.bank, other_bank)["needs_action"], 0)     # the requester can only cancel
        self.assertEqual(self.row_for(self.bank, other_bank)["open_requests"], 1)

    def test_the_list_is_one_statement_on_the_read_engine(self):
        self.new_request()
        self.send(self.hospital, self.bank, "x")
        normal, read = self._record(lambda: self.listing(self.bank))
        self.assertEqual(len(read), 1, read)
        self.assertEqual(normal, [])

    def _record(self, fn):
        normal, read = [], []
        on_normal = lambda c, cur, statement, *a: normal.append(" ".join(statement.split()))  # noqa: E731
        on_read = lambda c, cur, statement, *a: read.append(" ".join(statement.split()))  # noqa: E731
        event.listen(engine, "before_cursor_execute", on_normal)
        event.listen(read_engine, "before_cursor_execute", on_read)
        try:
            fn()
        finally:
            event.remove(engine, "before_cursor_execute", on_normal)
            event.remove(read_engine, "before_cursor_execute", on_read)
        return normal, read


# ──────────────────────────── unread and the read position ────────────────────────────

class UnreadTests(ConvBase):
    def setUp(self):
        super().setUp()
        self.rid = self.new_request()

    def test_unread_counts_only_the_other_facilitys_messages_after_my_read_position(self):
        a = self.send(self.hospital, self.bank, "one")["id"]
        b = self.send(self.hospital, self.bank, "two")["id"]
        self.send(self.bank, self.hospital, "my own reply")
        self.assertEqual(self.row_for(self.bank, self.hospital)["unread"], 2)
        self.assertEqual(self.row_for(self.hospital, self.bank)["unread"], 1)
        self.assertEqual(self.read(self.bank, self.hospital, up_to=a)["unread"], 1)
        self.assertEqual(self.row_for(self.bank, self.hospital)["unread"], 1)
        self.assertEqual(self.read(self.bank, self.hospital)["unread"], 0)
        c = self.send(self.hospital, self.bank, "three")["id"]
        self.assertEqual(self.row_for(self.bank, self.hospital)["unread"], 1)
        self.assertGreater(c, b)
        self.assertEqual(self.row_for(self.hospital, self.bank)["unread"], 1, "the bank reading changed nothing for the hospital")

    def test_the_thread_reports_my_unread_and_my_position(self):
        a = self.send(self.hospital, self.bank, "one")["id"]
        t = self.thread(self.bank, self.hospital)
        self.assertEqual((t["unread"], t["read_position"], t["last_message_id"]), (1, 0, a))
        self.read(self.bank, self.hospital)
        t = self.thread(self.bank, self.hospital)
        self.assertEqual((t["unread"], t["read_position"], t["last_message_id"]), (0, a, a))

    def test_the_read_position_only_moves_forward(self):
        ids = [self.send(self.hospital, self.bank, str(i))["id"] for i in range(4)]
        self.assertEqual(self.read(self.bank, self.hospital, up_to=ids[2])["last_read_message_id"], ids[2])
        self.assertEqual(self.read(self.bank, self.hospital, up_to=ids[0])["last_read_message_id"], ids[2], "a smaller value changes nothing")
        self.assertEqual(self.read(self.bank, self.hospital, up_to=0)["last_read_message_id"], ids[2])
        self.assertEqual(self.read(self.bank, self.hospital, up_to=ids[3])["last_read_message_id"], ids[3])

    def test_it_never_moves_beyond_the_newest_message_so_a_future_message_stays_unread(self):
        a = self.send(self.hospital, self.bank, "one")["id"]
        self.assertEqual(self.read(self.bank, self.hospital, up_to=10 ** 9)["last_read_message_id"], a)
        b = self.send(self.hospital, self.bank, "two")["id"]
        self.assertEqual(self.row_for(self.bank, self.hospital)["unread"], 1)
        self.assertGreater(b, a)

    def test_only_my_own_row_is_written(self):
        self.send(self.hospital, self.bank, "one")
        self.read(self.bank, self.hospital)
        rows = q("SELECT facility_id FROM conversation_reads WHERE facility_id = ANY(:f)", f=[self.bank, self.hospital])
        self.assertEqual(rows, [(self.bank,)], "the hospital's row was neither created nor touched")

    def test_the_body_cannot_name_another_facility(self):
        self.assertEqual(set(app.MarkConversationReadBody.model_fields), {"up_to_message_id"})
        with self.assertRaises(ValidationError):
            app.MarkConversationReadBody(up_to_message_id=-1)

    def test_a_legacy_message_with_no_conversation_id_counts_as_unread_and_is_read_by_its_request(self):
        mid = self.insert_old_style_message(self.rid, self.hospital)
        self.assertEqual(self.row_for(self.bank, self.hospital)["unread"], 1)
        self.assertEqual(self.read(self.bank, self.hospital)["last_read_message_id"], mid)
        self.assertEqual(self.row_for(self.bank, self.hospital)["unread"], 0)


# ──────────────────────────── the thread ────────────────────────────

class ThreadTests(ConvBase):
    def test_messages_and_events_are_merged_in_time_order(self):
        rid = self.new_request()
        self.accept(rid)
        self.release(rid)
        with engine.begin() as c:   # a fixed timeline: sent 0, accepted 10, released 30, received 50
            c.execute(text("UPDATE requests SET created_at = :a, accepted_at = :b, supplier_confirmed_at = :c WHERE id = :r"),
                      {"a": at(0), "b": at(10), "c": at(30), "r": rid})
        m1 = self.send(self.hospital, self.bank, "before accept", request_id=rid)["id"]
        m2 = self.send(self.bank, self.hospital, "between accept and release", request_id=rid)["id"]
        m3 = self.send(self.hospital, self.bank, "after release")["id"]
        for mid, minutes in ((m1, 5), (m2, 20), (m3, 40)):
            self.set_message_time(mid, at(minutes))
        order = [(i["type"], i.get("kind") or i["id"]) for i in self.thread(self.bank, self.hospital)["thread"]]
        self.assertEqual(order, [("event", "sent"), ("message", m1), ("event", "accepted"), ("message", m2), ("event", "released"), ("message", m3)])

    def test_a_message_at_the_same_instant_as_an_event_goes_after_it_and_the_sort_is_stable(self):
        rid = self.new_request()
        mid = self.send(self.hospital, self.bank, "same instant")["id"]
        with engine.begin() as c:
            created = c.execute(text("SELECT created_at FROM requests WHERE id = :r"), {"r": rid}).scalar()
        self.set_message_time(mid, created)
        kinds = [i["type"] for i in self.thread(self.bank, self.hospital)["thread"]]
        self.assertEqual(kinds, ["event", "message"])

    def test_an_event_with_no_recorded_time_sits_at_the_previous_ones_and_nothing_breaks(self):
        with engine.begin() as c:
            rid = c.execute(text("INSERT INTO requests (requesting_facility_id, supplying_facility_id, blood_type, quantity, emergency_type, status, component, created_at) "
                                 "VALUES (:h, :b, 'O+', 1, 'restock', 'accepted', 'Packed RBC', :t) RETURNING id"), {"h": self.hospital, "b": self.bank, "t": at(0)}).scalar()
        items = self.thread(self.bank, self.hospital)["thread"]
        self.assertEqual([i["kind"] for i in items], ["sent", "accepted"])
        self.assertIsNone(items[1]["at"])                       # "not recorded"
        self.assertEqual(items[1]["order_at"], at(0))           # but it still sorts
        self.assertEqual([c["request"]["id"] for c in self.thread(self.bank, self.hospital)["requests"]], [rid])

    def test_a_requests_card_carries_who_did_what_and_the_units(self):
        rid = self.new_request(quantity=2)
        self.accept(rid)
        for n in range(2):
            add(self.bank, f"{self.tag}-U{n}", 10)
        app.confirm_release(rid, facility_id=self.bank, user_id=self.bank_user)
        app.confirm_receipt(rid, facility_id=self.hospital, user_id=self.hospital_user)
        card = self.thread(self.bank, self.hospital)["requests"][0]
        self.assertEqual(set(card), {"request", "direction", "needs_my_action", "timeline", "units", "units_recorded", "summary"})
        self.assertEqual(card["request"]["id"], rid)
        self.assertEqual((card["request"]["status"], card["request"]["blood_type"], card["request"]["component"], card["request"]["quantity"]),
                         ("completed", "O+", "Packed RBC", 2))
        self.assertEqual((card["direction"], card["needs_my_action"]), ("incoming", False))
        self.assertEqual([e["kind"] for e in card["timeline"]], ["sent", "accepted", "released", "received"])
        released = next(e for e in card["timeline"] if e["kind"] == "released")
        self.assertEqual((released["facility_id"], released["units"]["count"]), (self.bank, 2))
        self.assertEqual(released["account_email"], q("SELECT email FROM users WHERE id = :u", u=self.bank_user)[0][0])
        self.assertTrue(card["units_recorded"])
        self.assertEqual({u["outcome"] for u in card["units"]}, {"received"})
        self.assertEqual(len(card["units"]), 2)
        self.assertEqual(card["summary"], {"asked": 2, "received": 2, "failed": 0})
        self.assertEqual(self.thread(self.hospital, self.bank)["requests"][0]["direction"], "outgoing")

    def test_the_request_events_in_the_thread_match_the_request_detail_endpoint(self):
        rid = self.new_request()
        self.accept(rid)
        detail = app.get_request_detail(rid, facility_id=self.hospital)
        card = self.thread(self.hospital, self.bank)["requests"][0]
        self.assertEqual(card["timeline"], detail["timeline"])
        self.assertEqual(card["request"], detail["request"])
        self.assertEqual(card["summary"], detail["summary"])

    def _finished(self, n):
        ids = []
        for _ in range(n):
            rid = self.new_request()
            app.cancel_request(rid, facility_id=self.hospital, user_id=self.hospital_user)
            ids.append(rid)
        return ids

    def test_open_requests_are_always_shown_and_finished_ones_are_a_page_of_five_with_show_earlier(self):
        finished = self._finished(7)
        open_one = self.new_request()
        t = self.thread(self.bank, self.hospital)
        self.assertEqual([c["request"]["id"] for c in t["requests"]], sorted(finished[-5:] + [open_one]))
        self.assertEqual(t["finished"], {"total": 7, "shown": 5, "has_more": True, "next_cursor": finished[2]})
        self.assertEqual(t["counts"]["requests_total"], 8)
        older = self.thread(self.bank, self.hospital, finished_before=t["finished"]["next_cursor"])
        self.assertEqual([c["request"]["id"] for c in older["requests"]], finished[:2])
        self.assertEqual(older["finished"], {"total": 7, "shown": 2, "has_more": False, "next_cursor": None})
        self.assertEqual(older["counts"], t["counts"], "the counts are the conversation's, not the page's")

    def test_a_page_size_can_be_asked_for_and_is_capped(self):
        self._finished(4)
        self.assertEqual(self.thread(self.bank, self.hospital, finished_limit=2)["finished"]["shown"], 2)
        self.assertEqual(self.thread(self.bank, self.hospital, finished_limit=0)["finished"]["shown"], 1)
        self.assertEqual(self.thread(self.bank, self.hospital, finished_limit=10 ** 6)["finished"]["shown"], 4)

    def test_the_message_window_follows_the_requests_shown(self):
        finished = self._finished(7)
        open_one = self.new_request()
        with engine.begin() as c:    # request i created at minute 100*i; message i at 100*i + 10
            for i, rid in enumerate(finished + [open_one]):
                c.execute(text("UPDATE requests SET created_at = :t WHERE id = :r"), {"t": at(100 * i), "r": rid})
        made = [self.send(self.hospital, self.bank, f"m{i}")["id"] for i in range(8)]
        for i, mid in enumerate(made):
            self.set_message_time(mid, at(100 * i + 10))
        first = self.thread(self.bank, self.hospital)
        shown_first = [i["id"] for i in first["thread"] if i["type"] == "message"]
        self.assertEqual(shown_first, made[2:], "from the oldest request shown (finished[2], minute 200) onward")
        older = self.thread(self.bank, self.hospital, finished_before=first["finished"]["next_cursor"])
        shown_older = [i["id"] for i in older["thread"] if i["type"] == "message"]
        self.assertEqual(shown_older, made[:2], "the page of older requests brings the messages of its own window, and no overlap")
        self.assertEqual(sorted(shown_first + shown_older), sorted(made))

    def test_the_newest_messages_are_kept_when_there_are_too_many(self):
        self.new_request()
        made = [self.send(self.hospital, self.bank, f"m{i}")["id"] for i in range(5)]
        with mock.patch.object(app, "CONVERSATION_MESSAGES_MAX", 3):
            t = self.thread(self.bank, self.hospital)
        self.assertTrue(t["messages_truncated"])
        self.assertEqual([i["id"] for i in t["thread"] if i["type"] == "message"], made[2:])
        self.assertFalse(self.thread(self.bank, self.hospital)["messages_truncated"])

    def test_a_cursor_that_is_not_a_request_of_this_pair_is_refused(self):
        self.new_request()
        stranger = None
        with engine.begin() as c:
            stranger = c.execute(text("INSERT INTO requests (requesting_facility_id, supplying_facility_id, blood_type, quantity, emergency_type, status, component) "
                                      "VALUES (:h, :b, 'O+', 1, 'restock', 'cancelled', 'Packed RBC') RETURNING id"), {"h": self.third_hospital, "b": self.third_bank}).scalar()
        with self.assertRaises(HTTPException) as ctx:
            self.thread(self.bank, self.hospital, finished_before=stranger)
        self.assertEqual(ctx.exception.status_code, 400)

    def test_a_thread_is_a_pure_read_with_a_handful_of_statements(self):
        rid = self.new_request()
        self.accept(rid)
        self.release(rid)
        self.send(self.hospital, self.bank, "x")
        normal, read = ListTests._record(self, lambda: self.thread(self.bank, self.hospital))
        self.assertEqual(normal, [], "nothing on the engine that writes")
        self.assertLessEqual(len(read), 6, read)
        self.assertFalse(any(s.upper().startswith(("INSERT", "UPDATE", "DELETE")) for s in read))
        rows_before = self.conversation_rows()
        self.thread(self.bank, self.hospital)
        self.assertEqual(self.conversation_rows(), rows_before)


# ──────────────────────────── who can see and do what ────────────────────────────

class AccessTests(ConvBase):
    def setUp(self):
        super().setUp()
        self.rid = self.new_request()
        self.mid = self.send(self.hospital, self.bank, "a private word")["id"]

    def assertStatus(self, code, fn, *a, **kw):
        with self.assertRaises(HTTPException) as ctx:
            fn(*a, **kw)
        self.assertEqual(ctx.exception.status_code, code)
        return ctx.exception

    def test_a_third_facility_sees_nothing_of_the_pair_in_any_endpoint(self):
        for third in (self.third_bank, self.third_hospital):
            for other in (self.bank, self.hospital):
                self.assertStatus(404, app.get_conversation, other, facility_id=third)
                self.assertStatus(404, app.send_conversation_message, other, app.SendConversationMessageBody(message="let me in"), facility_id=third)
                self.assertStatus(404, app.mark_conversation_read, other, None, facility_id=third)
            self.assertEqual(self.listing(third), [])
        self.assertEqual(self.message_ids(self.bank, self.hospital), [self.mid], "and nothing was written by the attempts")
        self.assertEqual(self.conversation_rows(), [tuple(sorted((self.bank, self.hospital)))])

    def test_an_unknown_facility_reads_the_same_as_a_facility_with_no_request_in_common(self):
        a = self.assertStatus(404, app.get_conversation, 10 ** 9, facility_id=self.bank)
        b = self.assertStatus(404, app.get_conversation, self.third_bank, facility_id=self.bank)
        self.assertEqual(a.detail, b.detail)
        self.assertEqual(self.assertStatus(404, app.send_conversation_message, 10 ** 9, app.SendConversationMessageBody(message="x"), facility_id=self.bank).detail,
                         self.assertStatus(404, app.send_conversation_message, self.third_bank, app.SendConversationMessageBody(message="x"), facility_id=self.bank).detail)

    def test_a_conversation_with_yourself_is_refused(self):
        for fn, args in ((app.get_conversation, ()), (app.mark_conversation_read, (None,))):
            self.assertStatus(400, fn, self.bank, *args, facility_id=self.bank)
        self.assertStatus(400, app.send_conversation_message, self.bank, app.SendConversationMessageBody(message="x"), facility_id=self.bank)

    def test_every_route_resolves_the_acting_facility_from_the_token(self):
        paths = {"/conversations", "/conversations/{other_facility_id}", "/conversations/{other_facility_id}/messages", "/conversations/{other_facility_id}/read"}
        seen = set()
        for route in app.app.routes:
            if getattr(route, "path", None) in paths:
                seen.add(route.path)
                names = [d.call.__name__ for d in route.dependant.dependencies]
                self.assertIn("get_acting_facility_id", names, route.path)
                self.assertNotIn("facility_id", {p.name for p in route.dependant.query_params + route.dependant.body_params}, "the caller is never taken from the request")
        self.assertEqual(seen, paths)

    def test_no_token_is_401_and_a_deactivated_facility_is_refused(self):
        from tests.test_sync import get
        self.assertEqual(get("/conversations")[0], 401)
        with engine.begin() as c:
            uid = c.execute(text("INSERT INTO users (email, password_hash, facility_id, role) VALUES (:e, 'x', :f, 'staff') RETURNING id"),
                            {"e": f"conv-{self.tag}@example.com", "f": self.bank}).scalar()
        headers = [("authorization", "Bearer " + auth.create_access_token(user_id=uid, email="x@example.com", facility_id=self.bank, role="staff"))]
        self.assertEqual(get("/conversations", headers)[0], 200)
        self.assertEqual(get(f"/conversations/{self.hospital}", headers)[0], 200)
        with engine.begin() as c:
            c.execute(text("UPDATE facilities SET is_active = false WHERE id = :f"), {"f": self.bank})
        app._active_account_until.clear()
        self.assertEqual(get("/conversations", headers)[0], 403)
        self.assertEqual(get(f"/conversations/{self.hospital}", headers)[0], 403)

    def test_an_admin_account_has_no_facility_so_it_is_refused(self):
        from tests.test_sync import get
        token = auth.create_access_token(user_id=1, email="a@example.com", facility_id=None, role="admin")
        self.assertEqual(get("/conversations", [("authorization", f"Bearer {token}")])[0], 403)


# ──────────────────────────── sending ────────────────────────────

class SendTests(ConvBase):
    def test_a_message_about_a_request_and_one_about_nothing(self):
        rid = self.new_request()
        with_request = self.send(self.hospital, self.bank, "about it", request_id=rid)
        without = self.send(self.bank, self.hospital, "just talking")
        self.assertEqual(with_request["request_id"], rid)
        self.assertIsNone(without["request_id"])
        self.assertEqual((with_request["sender_facility_id"], with_request["sender_facility_name"]), (self.hospital, q("SELECT name FROM facilities WHERE id = :f", f=self.hospital)[0][0]))
        self.assertEqual(with_request["conversation_id"], without["conversation_id"])
        self.assertEqual(set(with_request), {"id", "conversation_id", "request_id", "sender_facility_id", "message", "created_at", "sender_facility_name"})
        stored = q("SELECT request_id, conversation_id FROM request_messages WHERE id = ANY(:i) ORDER BY id", i=[with_request["id"], without["id"]])
        self.assertEqual(stored, [(rid, with_request["conversation_id"]), (None, with_request["conversation_id"])])
        merged = [(i["id"], i["request_id"]) for i in self.thread(self.bank, self.hospital)["thread"] if i["type"] == "message"]
        self.assertEqual(merged, [(with_request["id"], rid), (without["id"], None)])

    def test_a_request_of_another_pair_is_refused_and_nothing_is_stored(self):
        self.new_request()
        with engine.begin() as c:
            stranger = c.execute(text("INSERT INTO requests (requesting_facility_id, supplying_facility_id, blood_type, quantity, emergency_type, status, component) "
                                      "VALUES (:h, :b, 'O+', 1, 'restock', 'pending', 'Packed RBC') RETURNING id"), {"h": self.third_hospital, "b": self.bank}).scalar()
        for bad in (stranger, 10 ** 9):
            with self.assertRaises(HTTPException) as ctx:
                self.send(self.hospital, self.bank, "wrong request", request_id=bad)
            self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(q("SELECT count(*) FROM request_messages WHERE sender_facility_id = :f", f=self.hospital)[0][0], 0)

    def test_blank_and_over_2000_are_refused_by_the_body_and_the_limit_is_inclusive(self):
        for bad in ("", "   ", "\n\t"):
            with self.assertRaises(ValidationError):
                app.SendConversationMessageBody(message=bad)
        with self.assertRaises(ValidationError):
            app.SendConversationMessageBody(message="x" * 2001)
        self.assertEqual(len(app.SendConversationMessageBody(message="x" * 2000).message), 2000)
        self.assertEqual(app.SendConversationMessageBody(message="  hi  ").message, "hi")
        self.new_request()
        self.assertEqual(len(self.send(self.hospital, self.bank, "y" * 2000)["message"]), 2000)

    def test_allowed_whenever_the_pair_has_a_request_of_any_status_and_not_otherwise(self):
        pending = self.new_request()
        self.assertEqual(self.send(self.hospital, self.bank, "while pending")["request_id"], None)   # the old gate would have said no
        app.cancel_request(pending, facility_id=self.hospital, user_id=self.hospital_user)
        self.send(self.bank, self.hospital, "after it was cancelled")
        declined = self.new_request(); app.decline_request(declined, facility_id=self.bank, user_id=self.bank_user)
        self.send(self.hospital, self.bank, "after it was declined")
        with self.assertRaises(HTTPException) as ctx:
            self.send(self.hospital, self.third_bank, "no request with this one")
        self.assertEqual(ctx.exception.status_code, 404)

    def test_a_deactivated_counterpart_cannot_be_messaged_but_its_thread_can_still_be_read(self):
        self.new_request()
        self.send(self.hospital, self.bank, "before")
        with engine.begin() as c:
            c.execute(text("UPDATE facilities SET is_active = false WHERE id = :f"), {"f": self.bank})
        with self.assertRaises(HTTPException) as ctx:
            self.send(self.hospital, self.bank, "after")
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(len(self.message_ids(self.hospital, self.bank)), 1)


class RateLimitTests(ConvBase):
    def setUp(self):
        super().setUp()
        self.new_request()
        self.clock = [1000.0]
        limiter = auth.AttemptLimiter(limit=3, window=60, clock=lambda: self.clock[0])
        patcher = mock.patch.object(app, "_MESSAGE_LIMITER", limiter)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_default_is_thirty_a_minute(self):
        self.assertEqual((app.MESSAGE_SEND_LIMIT, app.MESSAGE_SEND_WINDOW_SECONDS), (30, 60))

    def test_the_fourth_message_in_the_window_is_429_with_retry_after_and_then_it_clears(self):
        for i in range(3):
            self.send(self.hospital, self.bank, f"m{i}")
        with self.assertRaises(HTTPException) as ctx:
            self.send(self.hospital, self.bank, "too many")
        self.assertEqual(ctx.exception.status_code, 429)
        self.assertIn("Retry-After", ctx.exception.headers)
        self.assertEqual(len(self.message_ids(self.hospital, self.bank)), 3)
        self.clock[0] += 61
        self.send(self.hospital, self.bank, "after the window")

    def test_it_is_per_facility_and_the_other_side_is_not_slowed(self):
        for i in range(3):
            self.send(self.hospital, self.bank, f"m{i}")
        self.send(self.bank, self.hospital, "the bank still can")

    def test_the_old_endpoint_draws_on_the_same_allowance(self):
        rid = self.new_request()
        self.accept(rid)
        for i in range(2):
            app.send_request_message(rid, app.SendRequestMessageBody(message=f"old {i}"), facility_id=self.hospital)
        self.send(self.hospital, self.bank, "new")
        with self.assertRaises(HTTPException) as ctx:
            app.send_request_message(rid, app.SendRequestMessageBody(message="old again"), facility_id=self.hospital)
        self.assertEqual(ctx.exception.status_code, 429)

    def test_a_refused_attempt_counts_too(self):
        for i in range(3):
            with self.assertRaises(HTTPException):
                self.send(self.hospital, self.third_bank, "no request")          # 404, but counted
        with self.assertRaises(HTTPException) as ctx:
            self.send(self.hospital, self.bank, "now limited")
        self.assertEqual(ctx.exception.status_code, 429)


# ──────────────────────────── the old per-request endpoints ────────────────────────────

class OldEndpointTests(ConvBase):
    def setUp(self):
        super().setUp()
        self.rid = self.new_request()
        self.accept(self.rid)

    def test_a_message_sent_the_old_way_is_in_the_conversation_with_its_conversation_id(self):
        sent = app.send_request_message(self.rid, app.SendRequestMessageBody(message="old way"), facility_id=self.hospital)
        self.assertEqual(set(sent), {"id", "request_id", "sender_facility_id", "message", "created_at", "sender_facility_name"})   # the old shape, unchanged
        cid = q("SELECT conversation_id FROM request_messages WHERE id = :i", i=sent["id"])[0][0]
        self.assertIsNotNone(cid)
        self.assertEqual(self.message_ids(self.bank, self.hospital), [sent["id"]])
        self.assertEqual(self.row_for(self.bank, self.hospital)["last_message"]["text"], "old way")
        self.assertEqual(self.row_for(self.bank, self.hospital)["unread"], 1)

    def test_a_message_sent_the_new_way_about_the_request_shows_in_the_old_list_and_one_about_nothing_does_not(self):
        about = self.send(self.bank, self.hospital, "about it", request_id=self.rid)["id"]
        self.send(self.bank, self.hospital, "about nothing")
        self.assertEqual([m["id"] for m in app.list_request_messages(self.rid, facility_id=self.hospital)], [about])
        self.assertEqual(app.get_request_detail(self.rid, facility_id=self.hospital)["message_count"], 1)

    def test_old_and_new_messages_interleave_in_one_thread_in_id_order(self):
        a = app.send_request_message(self.rid, app.SendRequestMessageBody(message="1"), facility_id=self.hospital)["id"]
        b = self.send(self.bank, self.hospital, "2")["id"]
        c = app.send_request_message(self.rid, app.SendRequestMessageBody(message="3"), facility_id=self.bank)["id"]
        self.assertEqual(self.message_ids(self.hospital, self.bank), [a, b, c])

    def test_a_row_written_by_an_old_instance_with_no_conversation_id_still_shows(self):
        mid = self.insert_old_style_message(self.rid, self.hospital)
        self.assertIsNone(q("SELECT conversation_id FROM request_messages WHERE id = :i", i=mid)[0][0])
        self.assertEqual(self.message_ids(self.bank, self.hospital), [mid])
        self.assertEqual(self.row_for(self.bank, self.hospital)["last_message"]["id"], mid)
        # and a conversation row made later does not hide it
        self.send(self.bank, self.hospital, "later")
        self.assertEqual(len(self.message_ids(self.bank, self.hospital)), 2)

    def test_the_old_gate_is_unchanged(self):
        pending = self.new_request()
        with self.assertRaises(HTTPException) as ctx:
            app.send_request_message(pending, app.SendRequestMessageBody(message="too early"), facility_id=self.hospital)
        self.assertEqual(ctx.exception.status_code, 400)
        with self.assertRaises(HTTPException) as ctx:
            app.send_request_message(self.rid, app.SendRequestMessageBody(message="not a party"), facility_id=self.third_hospital)
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(app.list_request_messages(self.rid, facility_id=self.bank), [])


class BeforeTheMigrationTests(ConvBase):
    """A server that comes up before server/migrate_add_conversations.sql has been run: everything it already did still works."""

    def setUp(self):
        super().setUp()
        self.rid = self.new_request()
        self.accept(self.rid)
        patcher = mock.patch.object(app, "_conversations_ready", return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_new_endpoints_answer_503_and_say_why(self):
        for fn, args in ((app.list_conversations, ()), (app.get_conversation, (self.hospital,)), (app.mark_conversation_read, (self.hospital, None)),
                         (app.send_conversation_message, (self.hospital, app.SendConversationMessageBody(message="x")))):
            with self.assertRaises(HTTPException) as ctx:
                fn(*args, facility_id=self.bank)
            self.assertEqual(ctx.exception.status_code, 503)
            self.assertIn("migration", ctx.exception.detail)

    def test_the_old_send_writes_the_old_way_and_the_old_list_reads_it(self):
        sent = app.send_request_message(self.rid, app.SendRequestMessageBody(message="before"), facility_id=self.hospital)
        self.assertIsNone(q("SELECT conversation_id FROM request_messages WHERE id = :i", i=sent["id"])[0][0])
        self.assertEqual([m["id"] for m in app.list_request_messages(self.rid, facility_id=self.bank)], [sent["id"]])
        self.assertEqual(q("SELECT count(*) FROM conversations WHERE facility_low_id = ANY(:f)", f=[self.bank, self.hospital])[0][0], 0)

    def test_requests_and_every_transition_work(self):
        second = self.new_request()
        self.accept(second)
        self.release(second)
        app.confirm_receipt(second, facility_id=self.hospital, user_id=self.hospital_user)

    def test_sync_still_answers_and_its_messages_token_follows_the_messages_on_my_requests(self):
        from tests.test_sync import payload
        before = payload(self.bank)["messages"]
        app.send_request_message(self.rid, app.SendRequestMessageBody(message="hello"), facility_id=self.hospital)
        self.assertNotEqual(payload(self.bank)["messages"], before)


class ReadinessTests(unittest.TestCase):
    def setUp(self):
        self._seen, self._at = app._conversations_seen, app._conversations_checked_at
        self.addCleanup(lambda: (setattr(app, "_conversations_seen", self._seen), setattr(app, "_conversations_checked_at", self._at)))

    def test_a_negative_answer_is_asked_again_only_after_the_interval(self):
        app._conversations_seen = False
        app._conversations_checked_at = None
        calls = []

        class Result:
            def __init__(self, v): self.v = v
            def scalar(self): return self.v

        class FakeConn:
            def __enter__(self_): return self_
            def __exit__(self_, *a): return False
            def execute(self_, *a, **k):
                calls.append(1)
                return Result(False)

        clock = [100.0]
        with mock.patch.object(app.read_engine, "connect", return_value=FakeConn()), mock.patch.object(app.time, "monotonic", side_effect=lambda: clock[0]):
            self.assertFalse(app._conversations_ready())
            self.assertFalse(app._conversations_ready())
            self.assertEqual(len(calls), 1, "asked once, then not again inside the interval")
            clock[0] += app.CONVERSATIONS_RECHECK_SECONDS + 1
            self.assertFalse(app._conversations_ready())
            self.assertEqual(len(calls), 2)


# ──────────────────────────── over HTTP ────────────────────────────

async def _http(method, path, token, body=None):
    import json
    payload = b"" if body is None else json.dumps(body).encode()
    sent = []

    async def receive():
        return {"type": "http.request", "body": payload, "more_body": False}

    async def send(message):
        sent.append(message)

    path, _, query = path.partition("?")
    headers = [(b"host", b"test"), (b"authorization", f"Bearer {token}".encode())]
    if body is not None:
        headers += [(b"content-type", b"application/json"), (b"content-length", str(len(payload)).encode())]
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method, "path": path, "raw_path": path.encode(),
             "query_string": query.encode(), "root_path": "", "scheme": "http", "server": ("test", 80), "client": ("test", 1), "headers": headers}
    await app.app(scope, receive, send)
    start = next(m for m in sent if m["type"] == "http.response.start")
    raw = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return start["status"], (json.loads(raw) if raw else None)


class HttpTests(ConvBase):
    """The same endpoints through the real app: routing, the optional body, JSON encoding of dates and timestamps, validation errors."""

    def setUp(self):
        super().setUp()
        with engine.begin() as c:
            self.uid = {f: c.execute(text("INSERT INTO users (email, password_hash, facility_id, role) VALUES (:e, 'x', :f, 'staff') RETURNING id"),
                                     {"e": f"http-{self.tag}-{f}@example.com", "f": f}).scalar() for f in (self.bank, self.hospital)}
        self.token = {f: auth.create_access_token(user_id=u, email="x@example.com", facility_id=f, role="staff") for f, u in self.uid.items()}
        self.rid = self.new_request()
        self.accept(self.rid)
        self.release(self.rid)

    def call(self, method, path, who, body=None):
        return asyncio.run(_http(method, path, self.token[who], body))

    def test_the_whole_flow_over_http(self):
        status, sent = self.call("POST", f"/conversations/{self.bank}/messages", self.hospital, {"message": "  Please confirm.  ", "request_id": self.rid})
        self.assertEqual(status, 200, sent)
        self.assertEqual((sent["message"], sent["request_id"]), ("Please confirm.", self.rid))
        status, listing = self.call("GET", "/conversations", self.bank)
        self.assertEqual(status, 200)
        self.assertEqual(listing["conversations"][0]["last_message"]["text"], "Please confirm.")
        self.assertEqual((listing["conversations"][0]["unread"], listing["needs_action"]), (1, 0))
        status, thread = self.call("GET", f"/conversations/{self.hospital}", self.bank)
        self.assertEqual(status, 200, thread)
        self.assertEqual([i["type"] for i in thread["thread"]], ["event", "event", "event", "message"])
        self.assertEqual(thread["requests"][0]["timeline"][2]["kind"], "released")
        status, result = self.call("POST", f"/conversations/{self.hospital}/read", self.bank)           # no body at all
        self.assertEqual((status, result["unread"]), (200, 0))
        status, result = self.call("POST", f"/conversations/{self.hospital}/read", self.bank, {"up_to_message_id": 0})   # a body, moving nothing
        self.assertEqual((status, result["last_read_message_id"]), (200, sent["id"]))
        status, thread = self.call("GET", f"/conversations/{self.bank}?finished_limit=1", self.hospital)
        self.assertEqual((status, thread["finished"]["shown"]), (200, 0))

    def test_validation_and_errors_are_the_usual_http_ones(self):
        for bad in ({"message": ""}, {"message": "   "}, {"message": "x" * 2001}, {}):
            self.assertEqual(self.call("POST", f"/conversations/{self.bank}/messages", self.hospital, bad)[0], 422, bad)
        self.assertEqual(self.call("POST", f"/conversations/{self.hospital}/read", self.bank, {"up_to_message_id": -3})[0], 422)
        self.assertEqual(self.call("GET", f"/conversations/{self.third_bank}", self.bank)[0], 404)
        self.assertEqual(self.call("GET", f"/conversations/{self.bank}", self.bank)[0], 400)


# ──────────────────────────── the migration ────────────────────────────

SERVER = pathlib.Path(__file__).resolve().parents[1]


def without_transaction_lines(sql):
    """The files carry their own BEGIN / COMMIT; a test wraps them in its own transaction and rolls back, so it removes theirs."""
    return "\n".join(l for l in sql.splitlines() if l.strip() not in ("BEGIN;", "COMMIT;"))


class MigrationTests(unittest.TestCase):
    """Run the real files against the scratch database inside a transaction that is rolled back, after putting the tables back to how
    production is now (no conversation tables, request_id NOT NULL)."""

    def setUp(self):
        self.part1 = without_transaction_lines((SERVER / "migrate_add_conversations.sql").read_text(encoding="utf-8"))
        self.part2 = without_transaction_lines((SERVER / "migrate_conversations_part2_not_null.sql").read_text(encoding="utf-8"))
        self.raw = engine.raw_connection()
        self.addCleanup(self.raw.close)
        self.addCleanup(self.raw.rollback)       # LIFO: the rollback runs before the close
        self.cur = self.raw.cursor()
        self.cur.execute("SET LOCAL lock_timeout = '10s'")
        # production as it is today
        self.cur.execute("ALTER TABLE request_messages DROP COLUMN IF EXISTS conversation_id")
        self.cur.execute("DROP TABLE IF EXISTS conversation_reads")
        self.cur.execute("DROP TABLE IF EXISTS conversations")
        self.cur.execute("ALTER TABLE request_messages ALTER COLUMN request_id SET NOT NULL")
        # four facilities, six requests (three pairs, one with three), four messages, one on a declined request
        f = {}
        for name, kind in (("B1", "bloodbank"), ("B2", "bloodbank"), ("H1", "hospital"), ("H2", "hospital")):
            self.cur.execute("INSERT INTO facilities (name, facility_type) VALUES (%s, %s) RETURNING id", (f"Mig {uuid.uuid4().hex[:6]} {name}", kind))
            f[name] = self.cur.fetchone()[0]
        self.f = f
        def req(requester, supplier, status):
            self.cur.execute("INSERT INTO requests (requesting_facility_id, supplying_facility_id, blood_type, quantity, emergency_type, status, component) "
                             "VALUES (%s, %s, 'O+', 1, 'restock', %s, 'Packed RBC') RETURNING id", (f[requester], f[supplier], status))
            return self.cur.fetchone()[0]
        r1, r2, r3 = req("H1", "B1", "completed"), req("H1", "B1", "accepted"), req("H1", "B1", "pending")
        r4, r5, r6 = req("H2", "B1", "declined"), req("B2", "B1", "completed"), req("H2", "B2", "pending")
        self.requests = [r1, r2, r3, r4, r5, r6]
        for rid, sender, message in ((r1, "H1", "hello"), (r1, "B1", "hi"), (r4, "B1", "sorry"), (r5, "B2", "thanks")):
            self.cur.execute("INSERT INTO request_messages (request_id, sender_facility_id, message) VALUES (%s, %s, %s)", (rid, f[sender], message))

    def one(self, sql, *params):
        self.cur.execute(sql, params)
        return self.cur.fetchone()[0]

    def test_part_one_backfills_and_a_second_run_changes_nothing(self):
        ids = list(self.f.values())
        scope = "facility_low_id = ANY(%s) AND facility_high_id = ANY(%s)"
        for run in (1, 2):
            self.cur.execute(self.part1)
            self.assertEqual(self.one(f"SELECT count(*) FROM conversations WHERE {scope}", ids, ids), 4, f"run {run}: the four pairs that ever had a request")
            self.assertEqual(self.one("SELECT count(*) FROM request_messages WHERE conversation_id IS NULL"), 0, f"run {run}")
            self.assertEqual(self.one("SELECT count(*) FROM conversation_reads WHERE facility_id = ANY(%s)", ids), 8, f"run {run}: both sides of each")
        # each message went to its request's pair, and kept its request
        self.cur.execute("SELECT m.request_id, c.facility_low_id, c.facility_high_id FROM request_messages m JOIN conversations c ON c.id = m.conversation_id "
                         "WHERE m.request_id = ANY(%s)", (self.requests,))
        for request_id, low, high in self.cur.fetchall():
            self.cur.execute("SELECT least(requesting_facility_id, supplying_facility_id), greatest(requesting_facility_id, supplying_facility_id) FROM requests WHERE id = %s", (request_id,))
            self.assertEqual((low, high), self.cur.fetchone())
        # everything that existed counts as read for both sides: the position is the newest message in that conversation
        self.cur.execute("SELECT cr.last_read_message_id, (SELECT COALESCE(max(m.id), 0) FROM request_messages m WHERE m.conversation_id = cr.conversation_id) "
                         "FROM conversation_reads cr WHERE cr.facility_id = ANY(%s)", (ids,))
        for position, newest in self.cur.fetchall():
            self.assertEqual(position, newest)

    def test_the_old_codes_insert_still_works_after_part_one_and_a_message_with_no_request_is_allowed(self):
        self.cur.execute(self.part1)
        self.cur.execute("INSERT INTO request_messages (request_id, sender_facility_id, message) VALUES (%s, %s, 'old insert') RETURNING conversation_id",
                         (self.requests[0], self.f["H1"]))
        self.assertIsNone(self.cur.fetchone()[0])
        self.cur.execute("SELECT id FROM conversations WHERE facility_low_id = ANY(%s) LIMIT 1", (list(self.f.values()),))
        cid = self.cur.fetchone()[0]
        self.cur.execute("INSERT INTO request_messages (request_id, sender_facility_id, message, conversation_id) VALUES (NULL, %s, 'no request', %s)", (self.f["H1"], cid))

    def test_part_one_deletes_and_rewrites_no_message(self):
        self.cur.execute("SELECT id, request_id, sender_facility_id, message, created_at FROM request_messages WHERE request_id = ANY(%s) ORDER BY id", (self.requests,))
        before = self.cur.fetchall()
        self.cur.execute(self.part1)
        self.cur.execute("SELECT id, request_id, sender_facility_id, message, created_at FROM request_messages WHERE request_id = ANY(%s) ORDER BY id", (self.requests,))
        self.assertEqual(self.cur.fetchall(), before)

    def test_part_two_needs_part_one_places_stragglers_and_then_forbids_a_null(self):
        self.cur.execute(self.part1)
        self.cur.execute("INSERT INTO request_messages (request_id, sender_facility_id, message) VALUES (%s, %s, 'written by an old instance after part 1')",
                         (self.requests[1], self.f["H1"]))
        self.assertEqual(self.one("SELECT count(*) FROM request_messages WHERE conversation_id IS NULL"), 1)
        for run in (1, 2):
            self.cur.execute(self.part2)
            self.assertEqual(self.one("SELECT count(*) FROM request_messages WHERE conversation_id IS NULL"), 0, f"run {run}")
        self.assertEqual(self.one("SELECT is_nullable FROM information_schema.columns WHERE table_name = 'request_messages' AND column_name = 'conversation_id'"), "NO")
        self.cur.execute("SAVEPOINT s")
        with self.assertRaises(psycopg2.errors.NotNullViolation):
            self.cur.execute("INSERT INTO request_messages (request_id, sender_facility_id, message) VALUES (%s, %s, 'the old code now')", (self.requests[0], self.f["H1"]))
        self.cur.execute("ROLLBACK TO SAVEPOINT s")

    def test_part_two_creates_the_missing_conversation_of_a_straggler(self):
        self.cur.execute(self.part1)
        # a pair that gets its first request and first old-style message only after part 1 ran, so it has no conversation row yet
        self.cur.execute("INSERT INTO facilities (name, facility_type) VALUES (%s, 'hospital') RETURNING id", (f"Mig late {uuid.uuid4().hex[:6]}",))
        late = self.cur.fetchone()[0]
        self.cur.execute("INSERT INTO requests (requesting_facility_id, supplying_facility_id, blood_type, quantity, emergency_type, status, component) "
                         "VALUES (%s, %s, 'O+', 1, 'restock', 'accepted', 'Packed RBC') RETURNING id", (late, self.f["B1"]))
        rid = self.cur.fetchone()[0]
        self.cur.execute("INSERT INTO request_messages (request_id, sender_facility_id, message) VALUES (%s, %s, 'late')", (rid, late))
        self.cur.execute(self.part2)
        self.assertEqual(self.one("SELECT count(*) FROM request_messages WHERE conversation_id IS NULL"), 0)

    def test_the_files_say_what_the_owner_needs_to_run_and_in_what_order(self):
        header = (SERVER / "migrate_add_conversations.sql").read_text(encoding="utf-8")
        self.assertIn("CREATE TABLE request_messages_backup_20261111 AS SELECT * FROM request_messages;", header.split("BEGIN;")[0])
        self.assertLess(header.index("request_messages_backup_20261111"), header.index("BEGIN;"), "the backup step comes first")
        part2 = (SERVER / "migrate_conversations_part2_not_null.sql").read_text(encoding="utf-8")
        self.assertIn("ONLY AFTER THE NEW SERVER CODE IS LIVE", part2.split("BEGIN;")[0])
        for sql in (header, part2):
            self.assertNotIn("DROP TABLE", sql.upper().replace("-- ", ""), "the migration drops nothing")
            self.assertNotRegex(sql.upper(), r"\bDELETE\s+FROM\b")

