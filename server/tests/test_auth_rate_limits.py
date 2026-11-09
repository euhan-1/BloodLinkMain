"""Sign-in and email-sending limits: every action is counted under the account AND the sender's address, blocked when either is full,
and the sender's address is never one the caller writes. Needs the scratch database (conftest.py)."""
import unittest
import uuid
from unittest import mock

from fastapi import HTTPException
from sqlalchemy import text
from starlette.requests import Request

import auth
import main as app
from database import engine

IP_A, IP_B, IP_C = "81.107.0.110", "8.8.8.8", "1.1.1.1"
VERCEL = "76.76.21.9"


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def request_from(visitor, xff=None):
    """A request as Render delivers one that came through Vercel: the visitor is the entry before the Vercel peer."""
    headers = [(b"true-client-ip", VERCEL.encode()),
               (b"x-forwarded-for", (xff or f"{visitor}, {VERCEL}, 172.71.1.1, 10.0.0.1").encode())]
    return Request({"type": "http", "method": "POST", "path": "/auth/login", "headers": headers, "client": ("10.0.0.1", 1)})


class LoginLimitTests(unittest.TestCase):
    def setUp(self):
        self.tag = uuid.uuid4().hex[:8]
        self.email = f"limit-{self.tag}@example.com"
        self.password = "correct horse 99"
        with engine.begin() as c:
            self.user_id = c.execute(text("INSERT INTO users (email, password_hash, role) VALUES (:e, :h, 'staff') RETURNING id"),
                                     {"e": self.email, "h": auth.hash_password(self.password)}).scalar()
        self.addCleanup(self.drop_user)
        self.clock = Clock()
        patcher = mock.patch.object(app, "_LOGIN_LIMITER", auth.AttemptLimiter(limit=5, window=900, clock=self.clock))
        patcher.start()
        self.addCleanup(patcher.stop)

    def drop_user(self):
        with engine.begin() as c:
            c.execute(text("DELETE FROM users WHERE id = :i"), {"i": self.user_id})

    def attempt(self, ip=IP_A, password="wrong", email=None, xff=None):
        return app.login(app.LoginBody(email=email or self.email, password=password), request_from(ip, xff))

    def fail(self, **kw):
        with self.assertRaises(HTTPException) as ctx:
            self.attempt(**kw)
        return ctx.exception

    def test_the_limit_is_five_failures_per_fifteen_minutes_and_the_error_says_how_long(self):
        for _ in range(5):
            self.assertEqual(self.fail().status_code, 401)
        blocked = self.fail()
        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(blocked.detail, "Too many failed sign-in attempts. Please try again in 15 minutes.")
        self.assertEqual(blocked.headers["Retry-After"], "900")
        self.clock.t = 600
        self.assertIn("5 minutes", self.fail().detail)   # the wait counts down
        self.clock.t = 900
        self.assertEqual(self.fail().status_code, 401)   # and the window is a real 15 minutes

    def test_changing_the_forwarded_header_does_not_give_a_fresh_budget(self):
        # the old key was "<leftmost X-Forwarded-For>|<email>": a different header meant a different key, forever
        for i in range(5):
            self.assertEqual(self.fail(xff=f"9.9.9.{i}, {IP_A}, {VERCEL}, 172.71.1.1, 10.0.0.1").status_code, 401)
        self.assertEqual(self.fail(xff=f"7.7.7.7, {IP_A}, {VERCEL}, 172.71.1.1, 10.0.0.1").status_code, 429)

    def test_the_account_counter_holds_whatever_the_sender_address_is(self):
        for ip in (IP_A, IP_B, IP_C, "9.9.9.9", "4.4.4.4"):   # five different senders, one target
            self.assertEqual(self.fail(ip=ip).status_code, 401)
        self.assertEqual(self.fail(ip="5.5.5.5").status_code, 429)

    def test_the_address_counter_holds_whichever_account_is_tried(self):
        for i in range(5):
            self.assertEqual(self.fail(email=f"nobody{i}-{self.tag}@example.com").status_code, 401)  # five accounts, one sender
        self.assertEqual(self.fail(email=f"someone-else-{self.tag}@example.com").status_code, 429)
        # and it does not touch another sender
        self.assertEqual(self.fail(ip=IP_B, email=f"fresh-{self.tag}@example.com").status_code, 401)

    def test_an_unrelated_sender_and_account_are_unaffected(self):
        for _ in range(5):
            self.fail()
        self.assertEqual(self.fail(ip=IP_B, email=f"other-{self.tag}@example.com").status_code, 401)

    def test_account_names_are_compared_case_and_space_insensitively(self):
        for _ in range(5):
            self.fail()
        self.assertEqual(self.fail(ip=IP_B, email=f"  {self.email.upper()} ").status_code, 429)

    def test_only_failures_count_and_a_success_resets_nothing(self):
        for _ in range(4):
            self.fail()
        ok = self.attempt(password=self.password)                      # a good sign-in in between
        self.assertFalse(ok["must_change_password"])
        self.assertEqual(self.fail().status_code, 401)                 # the fifth failure still counts: success did not wipe four
        self.assertEqual(self.fail().status_code, 429)

    def test_successful_sign_ins_never_use_up_the_budget(self):
        for _ in range(8):
            self.assertFalse(self.attempt(password=self.password)["must_change_password"])

    def test_a_deactivated_facilitys_login_is_not_counted_as_a_failed_guess(self):
        with engine.begin() as c:
            fid = c.execute(text("INSERT INTO facilities (name, facility_type, is_active, profile_completed) "
                                 "VALUES (:n, 'bloodbank', false, true) RETURNING id"), {"n": f"LimitFac {self.tag}"}).scalar()
            c.execute(text("UPDATE users SET facility_id = :f WHERE id = :u"), {"f": fid, "u": self.user_id})
        self.addCleanup(self.drop_facility, fid)
        for _ in range(8):
            self.assertEqual(self.fail(password=self.password).status_code, 403)  # right password, deactivated: 403, never 429

    def drop_facility(self, fid):
        with engine.begin() as c:
            c.execute(text("UPDATE users SET facility_id = NULL WHERE id = :u"), {"u": self.user_id})
            c.execute(text("DELETE FROM facilities WHERE id = :f"), {"f": fid})

    def test_a_caller_whose_address_cannot_be_told_is_limited_by_account_alone(self):
        bare = Request({"type": "http", "method": "POST", "path": "/", "headers": [], "client": ("127.0.0.1", 1)})
        self.assertEqual(app._attempt_keys(bare, "A@B.C"), ["acct:a@b.c"])  # no shared "unknown" bucket that would lock everyone out


class EmailLimitTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        patcher = mock.patch.object(app, "_FORGOT_LIMITER", auth.AttemptLimiter(limit=5, window=600, clock=self.clock))
        patcher.start()
        self.addCleanup(patcher.stop)

    def forgot(self, email, ip=IP_A):
        # an address with no account: the response is identical and nothing is sent, but it is counted
        return app.forgot_password(app.ForgotPasswordBody(email=email), request_from(ip))

    def test_the_forgot_password_limit_is_per_account_and_per_address(self):
        email = f"nobody-{uuid.uuid4().hex[:6]}@example.com"
        for ip in (IP_A, IP_B, IP_C, "9.9.9.9", "4.4.4.4"):   # five senders, one target: nobody can mail one person endlessly
            self.forgot(email, ip)
        with self.assertRaises(HTTPException) as ctx:
            self.forgot(email, "5.5.5.5")
        self.assertEqual(ctx.exception.status_code, 429)
        self.assertEqual(ctx.exception.detail, "Too many email requests. Please try again in 10 minutes.")

    def test_one_address_cannot_mail_many_people(self):
        for i in range(5):
            self.forgot(f"nobody{i}-{uuid.uuid4().hex[:6]}@example.com")
        with self.assertRaises(HTTPException) as ctx:
            self.forgot(f"another-{uuid.uuid4().hex[:6]}@example.com")
        self.assertEqual(ctx.exception.status_code, 429)


class WaitTextTests(unittest.TestCase):
    def test_wait_text(self):
        for seconds, words in ((1, "1 second"), (45, "45 seconds"), (60, "1 minute"), (61, "2 minutes"), (900, "15 minutes")):
            self.assertEqual(app._wait_text(seconds), words)
