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

    def test_a_success_clears_the_account_counter_and_not_the_address_counter(self):
        for _ in range(4):
            self.fail()                                               # acct: 4, ip IP_A: 4
        ok = self.attempt(password=self.password)                     # a good sign-in from the same address
        self.assertFalse(ok["must_change_password"])
        # the address counter was not reset by it: one more failure from IP_A is the fifth, and IP_A is now blocked...
        self.assertEqual(self.fail(email=f"guess-{self.tag}@example.com").status_code, 401)
        blocked = self.fail(email=f"another-{self.tag}@example.com")
        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(blocked.detail, "Too many failed sign-in attempts. Please try again in 15 minutes.")
        # ...while the account counter WAS cleared: from another address, four more failures on that account are still allowed
        for _ in range(4):
            self.assertEqual(self.fail(ip=IP_B).status_code, 401)

    def test_someone_who_knows_one_valid_login_cannot_reset_their_address_counter_by_signing_in(self):
        mine = f"mine-{self.tag}@example.com"
        with engine.begin() as c:
            c.execute(text("INSERT INTO users (email, password_hash, role) VALUES (:e, :h, 'staff')"), {"e": mine, "h": auth.hash_password("my own pass 12")})
        self.addCleanup(self.drop_extra, mine)
        for i in range(4):
            self.fail(email=f"victim{i}-{self.tag}@example.com")      # four guesses at four different accounts from IP_A
        self.attempt(email=mine, password="my own pass 12")           # a valid sign-in to their own account in between
        self.assertEqual(self.fail(email=f"victim4-{self.tag}@example.com").status_code, 401)   # the fifth guess
        self.assertEqual(self.fail(email=f"victim5-{self.tag}@example.com").status_code, 429)   # the address is blocked: no reset bought

    def drop_extra(self, email):
        with engine.begin() as c:
            c.execute(text("DELETE FROM users WHERE email = :e"), {"e": email})

    def test_a_success_only_clears_that_accounts_counter(self):
        other = f"other-{self.tag}@example.com"
        with engine.begin() as c:
            c.execute(text("INSERT INTO users (email, password_hash, role) VALUES (:e, :h, 'staff')"), {"e": other, "h": auth.hash_password("other pass 1234")})
        self.addCleanup(self.drop_extra, other)
        for ip in ("2.2.2.2", "3.3.3.3", "7.7.7.7"):
            self.fail(ip=ip)                                          # three failures on the target, from three addresses
        for ip in ("2.2.2.2", "3.3.3.3", "7.7.7.7"):
            self.fail(email=other, ip=ip, password="wrong")           # and three on the other account
        self.attempt(ip="9.9.9.9", password=self.password)            # the target's owner signs in: THAT account's counter clears
        for ip in ("4.4.4.4", "5.5.5.5", "6.6.6.6", "8.8.4.4", "1.0.0.1"):
            self.assertEqual(self.fail(ip=ip).status_code, 401)       # five fresh failures on the target are allowed again
        self.assertEqual(self.fail(ip="9.9.9.8").status_code, 429)    # the sixth is not
        # the other account was not cleared: it has used 3, so two more are allowed and then it is limited
        for ip in ("4.4.4.5", "5.5.5.6"):
            self.assertEqual(self.fail(email=other, ip=ip, password="wrong").status_code, 401)
        self.assertEqual(self.fail(email=other, ip="6.6.6.7", password="wrong").status_code, 429)

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


class UpdatePasswordLimitTests(unittest.TestCase):
    """POST /auth/update-password: 5 failed current-password checks per 15 minutes, under the signed-in user and the address."""

    def setUp(self):
        self.tag = uuid.uuid4().hex[:8]
        self.password = "correct horse 99"
        self.users = {}
        with engine.begin() as c:
            for name in ("a", "b"):
                self.users[name] = c.execute(text("INSERT INTO users (email, password_hash, role) VALUES (:e, :h, 'staff') RETURNING id"),
                                             {"e": f"pw-{name}-{self.tag}@example.com", "h": auth.hash_password(self.password)}).scalar()
        self.addCleanup(self.drop)
        self.clock = Clock()
        patcher = mock.patch.object(app, "_PASSWORD_CHANGE_LIMITER", auth.AttemptLimiter(limit=5, window=900, clock=self.clock))
        patcher.start()
        self.addCleanup(patcher.stop)

    def drop(self):
        with engine.begin() as c:
            c.execute(text("DELETE FROM users WHERE id = ANY(:ids)"), {"ids": list(self.users.values())})

    def change(self, who="a", current="wrong password 1", new="a brand new pass 1", ip=IP_A):
        user = {"sub": str(self.users[who])}
        return app.update_password(app.UpdatePasswordBody(current_password=current, new_password=new), request_from(ip), user=user)

    def fail(self, who="a", ip=IP_A):
        with self.assertRaises(HTTPException) as ctx:
            self.change(who=who, ip=ip)
        return ctx.exception

    def test_the_sixth_wrong_current_password_gets_a_429_with_the_wait(self):
        for _ in range(5):
            self.assertEqual(self.fail().status_code, 400)
        blocked = self.fail()
        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(blocked.detail, "Too many failed attempts to confirm your password. Please try again in 15 minutes.")
        self.assertEqual(blocked.headers["Retry-After"], "900")
        self.clock.t = 900
        self.assertEqual(self.fail().status_code, 400)                 # a real 15-minute window

    def test_a_wrong_current_password_is_a_400_with_the_same_words_never_a_401(self):
        # a 401 means "your session is invalid or expired" and makes the frontend sign the user out; this is a wrong field, not that
        err = self.fail()
        self.assertEqual((err.status_code, err.detail), (400, "current password is incorrect"))

    def test_a_missing_or_invalid_session_is_still_a_401(self):
        for header in (None, "Bearer not-a-token"):
            with self.assertRaises(HTTPException) as ctx:
                app.get_current_user(authorization=header)   # the dependency every authenticated route, this one included, runs first
            self.assertEqual(ctx.exception.status_code, 401)

    def test_through_the_real_app_the_statuses_are_400_then_429_and_a_good_change_is_200(self):
        import asyncio
        import json
        from tests.test_upload_endpoints import asgi
        token = auth.create_access_token(user_id=self.users["a"], email="x@example.com", facility_id=None, role="staff")
        headers = [("authorization", f"Bearer {token}"), ("content-type", "application/json")]

        def post(current):
            body = json.dumps({"current_password": current, "new_password": "a brand new pass 1"}).encode()
            return asyncio.run(asgi("POST", "/auth/update-password", headers, body))

        statuses = [post("wrong password 1")[0] for _ in range(5)]
        self.assertEqual(statuses, [400] * 5)
        status, body = post("wrong password 1")
        self.assertEqual(status, 429)                                   # the sixth
        self.assertEqual(json.loads(body)["detail"], "Too many failed attempts to confirm your password. Please try again in 15 minutes.")
        self.clock.t = 901                                              # the window passes
        self.assertEqual(post(self.password)[0], 200)                   # a correct change succeeds

    def test_a_correct_password_before_the_limit_succeeds(self):
        for _ in range(4):
            self.fail()
        self.assertEqual(self.change(current=self.password, new="a brand new pass 1"), {"detail": "password updated"})
        with engine.connect() as c:
            stored = c.execute(text("SELECT password_hash FROM users WHERE id = :i"), {"i": self.users["a"]}).scalar()
        self.assertTrue(auth.verify_password("a brand new pass 1", stored))

    def test_a_correct_one_after_the_limit_is_still_refused(self):
        for _ in range(5):
            self.fail()
        with self.assertRaises(HTTPException) as ctx:
            self.change(current=self.password)                         # right password, but the guesses ran out first
        self.assertEqual(ctx.exception.status_code, 429)

    def test_a_success_clears_the_user_counter_and_not_the_address_counter(self):
        for _ in range(4):
            self.fail()                                                # user a: 4, IP_A: 4
        self.change(current=self.password, new="a brand new pass 1")
        # the address counter stands: one more wrong from IP_A is its fifth, then IP_A is blocked even for another user
        self.assertEqual(self.fail(who="b").status_code, 400)
        self.assertEqual(self.fail(who="b").status_code, 429)
        # the user counter was cleared: user a from other addresses has all five again
        for ip in (IP_B, IP_C, "9.9.9.9", "4.4.4.4", "5.5.5.5"):
            with self.assertRaises(HTTPException) as ctx:
                self.change(current="wrong password 1", new="x" * 12, ip=ip)
            self.assertEqual(ctx.exception.status_code, 400)

    def test_counters_do_not_leak_between_users_or_into_login(self):
        for _ in range(5):
            self.fail(who="a", ip=IP_A)                                # user a is spent
        self.assertEqual(self.fail(who="a", ip=IP_A).status_code, 429)
        self.assertEqual(self.fail(who="b", ip=IP_B).status_code, 400)  # user b, another address: untouched
        self.assertEqual(self.change(who="b", current=self.password, ip=IP_B), {"detail": "password updated"})
        # the login limiter is separate: nothing counted there, so a's own sign-in still works from this address
        with engine.connect() as c:
            email = c.execute(text("SELECT email FROM users WHERE id = :i"), {"i": self.users["a"]}).scalar()
        result = app.login(app.LoginBody(email=email, password=self.password), request_from(IP_A))
        self.assertFalse(result["must_change_password"])

    def test_the_message_does_not_say_which_counter_tripped(self):
        for _ in range(5):
            self.fail(who="a", ip=IP_A)
        by_user = self.fail(who="a", ip=IP_B)                          # the user counter is full, this address is new
        by_address = self.fail(who="b", ip=IP_A)                       # the address counter is full, this user is new
        self.assertEqual((by_user.status_code, by_address.status_code), (429, 429))
        self.assertEqual(by_user.detail, by_address.detail)
        self.assertEqual(by_user.headers, by_address.headers)

    def test_a_caller_whose_address_cannot_be_told_is_limited_by_the_user_alone(self):
        bare = Request({"type": "http", "method": "POST", "path": "/", "headers": [], "client": ("127.0.0.1", 1)})
        self.assertEqual(app._user_attempt_keys(bare, 7), ["acct:user:7"])

    def test_only_failed_checks_count(self):
        for _ in range(8):                                             # eight successful changes in a row never use the budget
            self.change(current=self.password, new=self.password)
