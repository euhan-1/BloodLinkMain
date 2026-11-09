"""GET /facilities/geocode is public (registration has no login), so it is limited: minimum length, a per-address rate limit,
a short cache, a one-call-per-second throttle toward Nominatim, and an identifying User-Agent. No network: urlopen is patched.

Needs the scratch database only because main.py imports it (conftest.py)."""
import io
import json
import unittest
import urllib.error
from unittest import mock

from fastapi import HTTPException
from starlette.requests import Request

import auth
import geocode_service as gs
import main as app

VISITOR = "81.107.0.110"
OTHER = "8.8.8.8"


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def request_from(ip):
    return Request({"type": "http", "method": "GET", "path": "/facilities/geocode", "query_string": b"",
                    "headers": [(b"true-client-ip", ip.encode())], "client": ("10.0.0.1", 1)})


def nominatim_says(results):
    body = json.dumps(results).encode()
    return mock.MagicMock(return_value=io.BytesIO(body))


class ThrottleTests(unittest.TestCase):
    def test_callers_get_one_turn_per_second_and_a_long_queue_is_refused(self):
        clock, slept = Clock(), []
        t = gs._OutgoingThrottle(1.0, 2.5, clock=clock, sleep=slept.append)
        self.assertTrue(t.wait_for_turn())   # now
        self.assertTrue(t.wait_for_turn())   # in 1 s
        self.assertTrue(t.wait_for_turn())   # in 2 s
        self.assertFalse(t.wait_for_turn())  # would be 3 s away, past the 2.5 s limit: refused, not queued
        self.assertEqual(slept, [1.0, 2.0])
        clock.t = 10.0
        self.assertTrue(t.wait_for_turn())   # the line has cleared: no wait
        self.assertEqual(slept, [1.0, 2.0])


class CacheTests(unittest.TestCase):
    def test_entries_expire_and_the_oldest_leave_first(self):
        clock = Clock()
        c = gs._Cache(ttl=10, max_entries=2, clock=clock)
        c.put("a", (1.0, 2.0))
        self.assertEqual(c.get("a"), (True, (1.0, 2.0)))
        clock.t = 10
        self.assertEqual(c.get("a"), (False, None))
        clock.t = 11
        c.put("a", None)
        self.assertEqual(c.get("a"), (True, None))   # a miss from Nominatim is remembered too
        c.put("b", (3.0, 4.0))
        c.put("c", (5.0, 6.0))
        self.assertEqual(c.get("a"), (False, None))  # bounded: the oldest went
        self.assertTrue(c.get("c")[0])


class ServiceTests(unittest.TestCase):
    def setUp(self):
        gs._cache.clear()
        gs._throttle._next_slot = 0.0
        patcher = mock.patch.object(gs._throttle, "sleep", lambda s: None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def lookup(self, text, results):
        with mock.patch("urllib.request.urlopen", nominatim_says(results)) as urlopen:
            out = gs.geocode_address(text)
        return out, urlopen

    def test_an_identical_query_is_answered_from_memory(self):
        first, urlopen = self.lookup("12 Rizal Avenue, Batangas City", [{"lat": "13.75", "lon": "121.05"}])
        self.assertEqual(first, (13.75, 121.05))
        self.assertEqual(urlopen.call_count, 1)
        with mock.patch("urllib.request.urlopen") as again:
            second = gs.geocode_address("  12  RIZAL avenue,   batangas city ")  # same query, different case and spacing
        self.assertEqual(second, (13.75, 121.05))
        again.assert_not_called()

    def test_no_match_is_cached_but_a_failure_is_not(self):
        none, _ = self.lookup("nowhere at all 99999", [])
        self.assertIsNone(none)
        with mock.patch("urllib.request.urlopen") as again:
            self.assertIsNone(gs.geocode_address("nowhere at all 99999"))
        again.assert_not_called()
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("down")):
            self.assertIsNone(gs.geocode_address("a flaky lookup 123"))
        out, urlopen = self.lookup("a flaky lookup 123", [{"lat": "1.5", "lon": "2.5"}])
        self.assertEqual((out, urlopen.call_count), ((1.5, 2.5), 1))  # asked again: the failure was not remembered

    def test_a_busy_process_skips_the_lookup_instead_of_queueing_it(self):
        with mock.patch.object(gs._throttle, "wait_for_turn", return_value=False), mock.patch("urllib.request.urlopen") as urlopen:
            self.assertIsNone(gs.geocode_address("somewhere busy street"))
        urlopen.assert_not_called()

    def test_it_sends_an_identifying_user_agent_never_the_library_default(self):
        _, urlopen = self.lookup("identify yourself street", [])
        agent = urlopen.call_args[0][0].get_header("User-agent")
        self.assertEqual(agent, gs.DEFAULT_USER_AGENT)
        self.assertIn("BloodLink", agent)
        self.assertIn("https://", agent)           # a way to reach the project, as the policy asks
        self.assertNotIn("Python-urllib", agent)
        self.assertNotIn("@", agent)               # nobody's email address is sent
        gs._cache.clear()
        with mock.patch.dict("os.environ", {"NOMINATIM_USER_AGENT": "BloodLink-Test/9 (mailto:ops@example.test)"}):
            _, urlopen = self.lookup("identify yourself street", [])
        self.assertEqual(urlopen.call_args[0][0].get_header("User-agent"), "BloodLink-Test/9 (mailto:ops@example.test)")

    def test_it_still_asks_for_the_philippines_and_one_result(self):
        _, urlopen = self.lookup("scoped query street", [])
        url = urlopen.call_args[0][0].full_url
        self.assertIn("countrycodes=ph", url)
        self.assertIn("limit=1", url)


class EndpointTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        patcher = mock.patch.object(app, "_GEOCODE_LIMITER", auth.AttemptLimiter(limit=10, window=60, clock=self.clock))
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(gs, "geocode_address", return_value=(13.75, 121.05))
        self.geocoder = patcher.start()
        self.addCleanup(patcher.stop)

    def ask(self, text="12 Rizal Avenue", ip=VISITOR):
        return app.geocode_facility_address(address=text, request=request_from(ip))

    def test_a_normal_lookup_still_works_and_still_never_fails_on_a_miss(self):
        self.assertEqual(self.ask(), {"latitude": 13.75, "longitude": 121.05})
        self.geocoder.return_value = None
        self.assertEqual(self.ask("somewhere unknown"), {"latitude": None, "longitude": None})

    def test_a_query_that_is_too_short_or_too_long_is_refused_with_a_plain_message(self):
        for bad, expected in (("", "at least 5"), ("  abc ", "at least 5"), ("x" * 201, "too long")):
            with self.assertRaises(HTTPException) as ctx:
                self.ask(bad)
            self.assertEqual(ctx.exception.status_code, 422)
            self.assertIn(expected, ctx.exception.detail)
        self.geocoder.assert_not_called()  # refused before anything went out

    def test_the_eleventh_lookup_in_a_minute_from_one_address_is_refused_with_the_wait(self):
        for _ in range(10):
            self.ask()
        with self.assertRaises(HTTPException) as ctx:
            self.ask()
        self.assertEqual(ctx.exception.status_code, 429)
        self.assertIn("Too many address lookups", ctx.exception.detail)
        self.assertIn("1 minute", ctx.exception.detail)
        self.assertEqual(ctx.exception.headers["Retry-After"], "60")
        self.assertEqual(self.ask(ip=OTHER), {"latitude": 13.75, "longitude": 121.05})  # another address has its own budget
        self.clock.t = 61
        self.ask()  # and the first one is allowed again after the minute

    def test_the_address_comes_from_the_proxy_header_not_one_the_caller_writes(self):
        spoof = Request({"type": "http", "method": "GET", "path": "/", "query_string": b"", "client": ("10.0.0.1", 1),
                         "headers": [(b"true-client-ip", VISITOR.encode()), (b"x-forwarded-for", b"1.1.1.1, 2.2.2.2")]})
        for _ in range(10):
            app.geocode_facility_address(address="12 Rizal Avenue", request=spoof)
        # a different X-Forwarded-For does not give a fresh budget: the key is the proxy-supplied address
        spoof2 = Request({"type": "http", "method": "GET", "path": "/", "query_string": b"", "client": ("10.0.0.1", 1),
                          "headers": [(b"true-client-ip", VISITOR.encode()), (b"x-forwarded-for", b"3.3.3.3")]})
        with self.assertRaises(HTTPException):
            app.geocode_facility_address(address="12 Rizal Avenue", request=spoof2)
