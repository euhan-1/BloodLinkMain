"""Priority scheduling: the reason on a request, and what the server does with it.

A request carries a reason (trauma, scheduled_surgery or restock). The server uses it for exactly one thing: the order
in which _apply_priority_sort lists a facility's pending requests (GET /requests and GET /requests/incoming). Trauma and
scheduled surgery go ahead of restock, oldest first within each group, and every non-pending row keeps the position its
creation time gives it (that is what makes the sort non-preemptive). The reason is also named in the supplier's
notification, and the API refuses a reason it does not know.

What these tests prove: the sort's behaviour as a function (including edge cases), the order the two list endpoints
actually return for rows read back from Postgres, that accepting a request moves it to its chronological slot, that the
notification names the reason, that the API rejects an unknown reason end to end (HTTP 422, nothing stored), and that the
frontend's three values are the server's three values.

What they do not prove: that anything happens in that order afterwards (accept, release and stock reservation never read
the reason), that the supplier sees the list in this order on screen, behaviour of the deployed system or of production
data (this runs against the scratch database only), or concurrency (a status changing between two reads).

Needs the scratch database from conftest.py (TEST_DATABASE_URL).
"""
import asyncio
import json
import pathlib
import re
import unittest
import uuid
from datetime import datetime, timedelta, timezone

from pydantic import ValidationError
from sqlalchemy import text

import main as app
from database import engine
from tests.test_expired_transfer import make_pair, q

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)  # long before any real "now", so rows created by the API sort after it


def row(name, status, emergency_type, hours):
    """A request row as _apply_priority_sort sees it (dicts from the list queries)."""
    return {"id": name, "status": status, "emergency_type": emergency_type, "created_at": T0 + timedelta(hours=hours)}


def ids(rows):
    return [r["id"] for r in rows]


class PrioritySortFunctionTests(unittest.TestCase):
    """_apply_priority_sort as a pure function."""

    def test_immediate_use_goes_ahead_of_restock_oldest_first_within_each_group(self):
        rows = [row("a", "pending", "restock", 1), row("b", "pending", "trauma", 2), row("c", "pending", "restock", 3),
                row("d", "pending", "scheduled_surgery", 4), row("e", "pending", "trauma", 5)]
        self.assertEqual(ids(app._apply_priority_sort(rows)), ["b", "d", "e", "a", "c"])

    def test_trauma_and_scheduled_surgery_are_one_group_ordered_by_age_not_by_name(self):
        rows = [row("late-trauma", "pending", "trauma", 5), row("early-surgery", "pending", "scheduled_surgery", 2)]
        self.assertEqual(ids(app._apply_priority_sort(rows)), ["early-surgery", "late-trauma"])

    def test_non_pending_rows_keep_their_exact_positions(self):
        rows = [row("done", "completed", "restock", 0), row("p1", "pending", "restock", 1),
                row("acc", "accepted", "trauma", 2), row("p2", "pending", "trauma", 3),
                row("p3", "pending", "restock", 4), row("no", "declined", "trauma", 5)]
        out = app._apply_priority_sort(rows)
        self.assertEqual(ids(out), ["done", "p2", "acc", "p1", "p3", "no"])
        for anchored in ("done", "acc", "no"):
            self.assertEqual(ids(out).index(anchored), ids(rows).index(anchored), f"{anchored} moved")

    def test_a_trauma_that_is_not_pending_does_not_outrank_a_pending_restock(self):
        rows = [row("r", "pending", "restock", 1), row("t", "accepted", "trauma", 2)]
        self.assertEqual(ids(app._apply_priority_sort(rows)), ["r", "t"])

    def test_pending_rows_read_in_priority_order_once_the_others_are_filtered_out(self):
        # What the Requests screen shows after it filters to pending: the sorted order, with no client-side sort.
        rows = [row("done", "completed", "restock", 0), row("p1", "pending", "restock", 1), row("acc", "accepted", "trauma", 2),
                row("p2", "pending", "trauma", 3), row("p3", "pending", "scheduled_surgery", 4)]
        pending = [r for r in app._apply_priority_sort(rows) if r["status"] == "pending"]
        self.assertEqual(ids(pending), ["p2", "p3", "p1"])

    def test_an_unrecognised_reason_ranks_as_restock(self):
        # Rows the API would now refuse can still exist (no database CHECK). They are not dropped; they rank as restock.
        rows = [row("typo", "pending", "Trauma", 1), row("real", "pending", "trauma", 2)]
        self.assertEqual(ids(app._apply_priority_sort(rows)), ["real", "typo"])

    def test_equal_age_in_the_same_group_keeps_input_order(self):
        rows = [row("x", "pending", "trauma", 1), row("y", "pending", "trauma", 1), row("z", "pending", "trauma", 1)]
        self.assertEqual(ids(app._apply_priority_sort(rows)), ["x", "y", "z"])

    def test_edges_and_the_input_is_left_alone(self):
        self.assertEqual(app._apply_priority_sort([]), [])
        only = [row("a", "pending", "restock", 1)]
        self.assertEqual(ids(app._apply_priority_sort(only)), ["a"])
        none_pending = [row("a", "accepted", "restock", 1), row("b", "completed", "trauma", 2)]
        self.assertEqual(ids(app._apply_priority_sort(none_pending)), ["a", "b"])
        mixed = [row("a", "pending", "restock", 1), row("b", "pending", "trauma", 2)]
        before = ids(mixed)
        app._apply_priority_sort(mixed)
        self.assertEqual(ids(mixed), before, "the input list was reordered in place")


class ReasonValuesTests(unittest.TestCase):
    def test_the_server_accepts_exactly_three_reasons(self):
        self.assertEqual(app.EMERGENCY_TYPES, ("trauma", "scheduled_surgery", "restock"))

    def test_immediate_use_is_a_subset_of_the_accepted_reasons(self):
        self.assertTrue(app.IMMEDIATE_USE_TYPES <= set(app.EMERGENCY_TYPES))
        self.assertEqual(app.IMMEDIATE_USE_TYPES, {"trauma", "scheduled_surgery"})

    def test_the_frontend_sends_the_servers_values(self):
        # The picker's buttons come from the keys of EMERGENCY_TYPE_LABELS; those keys are what is posted.
        src = (pathlib.Path(__file__).resolve().parents[2] / "src" / "app" / "lib" / "requestTypes.ts").read_text(encoding="utf-8")
        union = re.search(r"export type EmergencyType\s*=\s*([^;]+);", src)
        self.assertIsNotNone(union, "EmergencyType union not found in requestTypes.ts")
        self.assertEqual(re.findall(r'"([^"]+)"', union.group(1)), list(app.EMERGENCY_TYPES))
        labels = re.search(r"EMERGENCY_TYPE_LABELS[^{]*\{([^}]*)\}", src, re.S)
        self.assertEqual(re.findall(r"^\s*(\w+)\s*:", labels.group(1), re.M), list(app.EMERGENCY_TYPES))


class PrioritySortEndpointTests(unittest.TestCase):
    """The two list endpoints, on rows read back from Postgres."""

    def setUp(self):
        self.tag = uuid.uuid4().hex[:6].upper()
        self.bank, self.hospital = make_pair(self, self.tag)

    def seed(self, rows):
        """rows: (name, status, emergency_type, hours after T0). Inserted out of the order they are listed in, so the
        database's own order is not accidentally already the answer."""
        made = {}
        with engine.begin() as c:
            for name, status, etype, hours in rows:
                confirmed = T0 + timedelta(hours=hours + 1) if status in ("accepted", "completed") else None
                made[name] = c.execute(text(
                    "INSERT INTO requests (requesting_facility_id, supplying_facility_id, blood_type, quantity, emergency_type, "
                    "status, created_at, supplier_confirmed_at, requester_confirmed_at) "
                    "VALUES (:h, :b, 'O+', 2, :e, :s, :t, :sc, :rc) RETURNING id"),
                    {"h": self.hospital, "b": self.bank, "e": etype, "s": status, "t": T0 + timedelta(hours=hours),
                     "sc": confirmed, "rc": confirmed if status == "completed" else None}).scalar()
        return made

    def names(self, listed, made):
        back = {v: k for k, v in made.items()}
        return [back[r["id"]] for r in listed]

    def test_both_lists_return_the_documented_order_for_a_mixed_set(self):
        # Oldest to newest. Pending slots are r2, r4, r5, r6, r8, r9; everything else must stay where it is.
        made = self.seed([
            ("r1", "completed", "restock", 0), ("r2", "pending", "restock", 1), ("r3", "accepted", "trauma", 2),
            ("r4", "pending", "scheduled_surgery", 3), ("r5", "pending", "restock", 4), ("r6", "pending", "trauma", 5),
            ("r7", "declined", "trauma", 6), ("r8", "pending", "trauma", 7), ("r9", "pending", "restock", 8),
        ])
        # Immediate use by age: r4, r6, r8. Restock by age: r2, r5, r9. They fill the pending slots in that order.
        expected = ["r1", "r4", "r3", "r6", "r8", "r2", "r7", "r5", "r9"]

        chronological = [r[0] for r in q("SELECT id FROM requests WHERE supplying_facility_id = :b ORDER BY created_at, id", b=self.bank)]
        back = {v: k for k, v in made.items()}
        self.assertEqual([back[i] for i in chronological], ["r1", "r2", "r3", "r4", "r5", "r6", "r7", "r8", "r9"])  # the raw order is not the answer

        self.assertEqual(self.names(app.list_incoming_requests(facility_id=self.bank), made), expected)  # the supplier's inbox
        self.assertEqual(self.names(app.list_requests(facility_id=self.hospital), made), expected)       # the requester's own list

    def test_pending_rows_alone_read_in_priority_order(self):
        made = self.seed([("r1", "completed", "restock", 0), ("r2", "pending", "restock", 1), ("r3", "accepted", "trauma", 2),
                          ("r4", "pending", "scheduled_surgery", 3), ("r5", "pending", "restock", 4), ("r6", "pending", "trauma", 5)])
        pending = [r for r in app.list_incoming_requests(facility_id=self.bank) if r["status"] == "pending"]
        self.assertEqual(self.names(pending, made), ["r4", "r6", "r2", "r5"])

    def test_a_trauma_request_rises_above_waiting_restock_requests(self):
        made = self.seed([("old", "pending", "restock", 1), ("older", "pending", "restock", 0)])
        self.assertEqual(self.names(app.list_incoming_requests(facility_id=self.bank), made), ["older", "old"])
        # A trauma request arrives now, through the real create path.
        new = app.create_request(app.CreateRequestBody(supplying_facility_id=self.bank, blood_type="O+", quantity=1,
                                                       component="Packed RBC", emergency_type="trauma"), requesting_facility_id=self.hospital)
        made["new"] = new["id"]
        self.assertEqual(self.names(app.list_incoming_requests(facility_id=self.bank), made), ["new", "older", "old"])

    def test_accepting_a_request_moves_it_to_its_chronological_slot(self):
        # Current, intended behaviour (non-preemptive), and the reason the ordering is hard to see in one list: once the
        # trauma request is accepted it stops being movable and drops back to where its creation time puts it.
        made = self.seed([("restock", "pending", "restock", 1), ("trauma", "pending", "trauma", 2)])
        self.assertEqual(self.names(app.list_incoming_requests(facility_id=self.bank), made), ["trauma", "restock"])
        app.accept_request(made["trauma"], facility_id=self.bank)
        self.assertEqual(self.names(app.list_incoming_requests(facility_id=self.bank), made), ["restock", "trauma"])

    def test_priority_never_crosses_between_facilities(self):
        other_bank, other_hospital = make_pair(self, self.tag + "B")
        made = self.seed([("mine-restock", "pending", "restock", 1), ("mine-trauma", "pending", "trauma", 2)])
        with engine.begin() as c:
            c.execute(text("INSERT INTO requests (requesting_facility_id, supplying_facility_id, blood_type, quantity, emergency_type, "
                           "status, created_at) VALUES (:h, :b, 'A+', 1, 'trauma', 'pending', :t)"),
                      {"h": other_hospital, "b": other_bank, "t": T0})
        self.assertEqual(self.names(app.list_incoming_requests(facility_id=self.bank), made), ["mine-trauma", "mine-restock"])


class SupplierNotificationTests(unittest.TestCase):
    def setUp(self):
        self.tag = uuid.uuid4().hex[:6].upper()
        self.bank, self.hospital = make_pair(self, self.tag)
        self.hospital_name = q("SELECT name FROM facilities WHERE id = :i", i=self.hospital)[0][0]

    def notified(self):
        return [r[0] for r in q("SELECT message FROM notifications WHERE facility_id = :b AND type = 'incoming_request' ORDER BY id", b=self.bank)]

    def send(self, emergency_type, quantity=3, blood_type="A+", requester=None):
        return app.create_request(app.CreateRequestBody(supplying_facility_id=self.bank, blood_type=blood_type, quantity=quantity,
                                                        component="Packed RBC", emergency_type=emergency_type), requesting_facility_id=requester or self.hospital)

    def test_the_supplier_is_told_the_reason(self):
        for etype, words in (("trauma", "trauma"), ("scheduled_surgery", "scheduled surgery"), ("restock", "restock")):
            with self.subTest(etype):
                self.send(etype)
                self.assertEqual(self.notified()[-1], f"New 3-unit A+ Packed RBC {words} request from {self.hospital_name}")

    def test_quantity_and_type_are_still_in_the_text(self):
        self.send("trauma", quantity=7, blood_type="O-")
        self.assertEqual(self.notified()[-1], f"New 7-unit O- Packed RBC trauma request from {self.hospital_name}")

    def test_a_blood_banks_request_is_announced_as_restock_whatever_it_asked_for(self):
        # The server forces a bank's reason to restock; the notification must say what was stored, not what was sent.
        other_bank, = make_pair(self, self.tag + "X")[:1]
        other_name = q("SELECT name FROM facilities WHERE id = :i", i=other_bank)[0][0]
        self.send("trauma", requester=other_bank)
        self.assertEqual(self.notified()[-1], f"New 3-unit A+ Packed RBC restock request from {other_name}")
        self.assertEqual(q("SELECT emergency_type FROM requests WHERE requesting_facility_id = :f", f=other_bank)[0][0], "restock")


def asgi_post(path, payload):
    """One POST through the real ASGI app (routing, body validation, error responses), with no HTTP client library."""
    body = json.dumps(payload).encode()
    sent = []

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST", "scheme": "http", "path": path,
             "raw_path": path.encode(), "query_string": b"", "client": ("127.0.0.1", 50000), "server": ("testserver", 80),
             "headers": [(b"host", b"testserver"), (b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]}
    asyncio.run(app.app(scope, receive, send))
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    return status, json.loads(b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body"))


class ReasonValidationTests(unittest.TestCase):
    BAD = ["Trauma", "TRAUMA", " trauma", "trauma ", "scheduled-surgery", "scheduled surgery", "Scheduled Surgery", "surgery",
           "emergency", "restocks", "restock\n", "", "null", "0"]

    def setUp(self):
        self.tag = uuid.uuid4().hex[:6].upper()
        self.bank, self.hospital = make_pair(self, self.tag)
        app.app.dependency_overrides[app.get_acting_facility_id] = lambda: self.hospital  # skip login; the route is the real one
        self.addCleanup(app.app.dependency_overrides.pop, app.get_acting_facility_id, None)

    def body(self, emergency_type):
        return {"supplying_facility_id": self.bank, "blood_type": "A+", "component": "Packed RBC", "quantity": 2, "emergency_type": emergency_type}

    def rows(self):
        return q("SELECT emergency_type FROM requests WHERE requesting_facility_id = :h", h=self.hospital)

    def test_the_model_accepts_the_three_values_and_nothing_else(self):
        for good in app.EMERGENCY_TYPES:
            with self.subTest(good=good):
                self.assertEqual(app.CreateRequestBody(**self.body(good)).emergency_type, good)
        for bad in self.BAD:
            with self.subTest(bad=bad):
                with self.assertRaises(ValidationError):
                    app.CreateRequestBody(**self.body(bad))

    def test_the_api_answers_422_and_stores_nothing_for_an_unknown_reason(self):
        for bad in self.BAD:
            with self.subTest(bad=bad):
                status, payload = asgi_post("/requests", self.body(bad))
                self.assertEqual(status, 422, payload)
                self.assertIn("emergency_type must be one of", json.dumps(payload))
        self.assertEqual(self.rows(), [])
        self.assertEqual(q("SELECT count(*) FROM notifications WHERE facility_id = :b", b=self.bank)[0][0], 0)

    def test_a_missing_reason_is_also_refused(self):
        body = self.body("trauma")
        del body["emergency_type"]
        status, _ = asgi_post("/requests", body)
        self.assertEqual(status, 422)
        self.assertEqual(self.rows(), [])

    def test_the_api_stores_each_valid_reason_as_sent(self):
        for good in app.EMERGENCY_TYPES:
            with self.subTest(good=good):
                status, payload = asgi_post("/requests", self.body(good))
                self.assertEqual(status, 200, payload)
                self.assertEqual(payload["emergency_type"], good)
        self.assertEqual(sorted(r[0] for r in self.rows()), sorted(app.EMERGENCY_TYPES))


if __name__ == "__main__":
    unittest.main()
