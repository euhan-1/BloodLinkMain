# Backlog

Things noticed and deliberately not built yet. Each says why it is wanted and what a sensible version looks like, so it can be picked up cold.
Remove an entry when it ships.

## Consent for accounts that existed before the Privacy Notice

*Update 2026-11-11: the notice is now version 1.1 (conversations and the stored read position). The proposal below applies to 1.0 to 1.1 as much as to "never asked": an account that consented to 1.0 is recorded as 1.0 and nothing asks it again.*

**Where it comes from.** The Register form now records consent (version and time) on the new account. Every account that exists today (9 on
2026-11-10: 8 staff and 1 admin) has `users.privacy_consented_at`
NULL (the admin-side account creation that would also have produced NULL rows was removed on 2026-11-11), meaning "not recorded". The notice itself says so. Proposed here, not built.

**The version.** A one-time acknowledgement at the next load of the app, for any account whose consent is NULL or older than
`PRIVACY_NOTICE_VERSION`: a full-screen step like the complete-profile one (the notice, or a link to it, plus the same unticked box and
"Continue"), then `POST /auth/acknowledge-privacy {privacy_notice_version}` sets the version and `now()` on the caller's `users` row.
Raising the version constant later re-asks everyone through the same path, which is the other thing it is for.

**What it touches.**
- Server: `_user_response` (used by `/auth/login` and `/auth/me`) gains `privacy_consent_required`, computed from the two columns that
  `GET /auth/me` and login already read (no extra statement); one new write endpoint on `engine`, rate-limited like `update-password`;
  `test_privacy_consent.py` and the check script grow to cover it. No new schema beyond `migrate_add_privacy_consent.sql`.
- Frontend: `SessionUser` (`lib/session.ts`) gains the flag; `App.tsx` gets a branch after sign-in and before `CompleteProfile`; one new
  screen; `api.ts` one function. A session already open is caught at its next load, because `App` re-reads `/auth/me` on mount.
- Not touched: the SMS and donor-blast code, the live poller, any forecast or stock path.

**Decisions first.**
- **Soft or hard gate.** Soft (the frontend shows the step; the server only records) is small and enough to hold a consent record. Hard (the
  server refuses every other endpoint with 403 until acknowledged) would also stop the live poller and every polling screen for an
  unacknowledged account, and needs `get_current_user` to read the column on the cached active-account check. Recommended: soft first.
- **The admin account.** Its email is personal data too, but it is a team member and the author of the notice. Recommended: ask it as well,
  so "every account" is true.
- **Order with the forced password change.** An account on a temporary password (an admin's password reset) has no access token until it changes it, so the
  acknowledgement can only come after that step.
- **What this does not fix.** The contact persons named on registrations (their name and phone) never see a consent screen; the line by
  that field on the Register form is the only measure. The 4 existing registration rows (1 approved, 3 rejected) hold a contact person's
  name and phone with no consent on file, and rejected ones are kept indefinitely. Removing them is a delete or an anonymise, which the
  archive-don't-delete rule does not currently allow: a decision for the team, not something this can assume.

## A release reminder for the supplier

**Where it comes from.** When a bank accepts a request it used to be sent "Confirm release for X's request", a notice telling it to do the next
step of the thing it had just clicked. That notice was dropped (the notification rebuild), correctly: the accepted request is already in its
"In progress" list with a Confirm release button. What is missing is not a notice at accept but a reminder when the release is late.

**The useful version.** One notification to the supplier a set number of hours after it accepted, only if it has still not released:
"Northside accepted this 5 hours ago and the 3-unit O+ Packed RBC request for Riverside General Hospital has not been released." It resolves
itself when the release is confirmed (or the request leaves `accepted`), like the other prompts (`_resolve_notifications` /
`_resolve_stale_notifications`). Once per request, not repeating.

**Things to decide first.**
- How many hours. That is an operational and clinical figure for blood bank staff to give, not something to guess; it may differ for
  trauma and scheduled surgery (`emergency_type`), which the request already carries.
- What triggers it. Nothing runs between requests except the nightly 23:30 job, which is too coarse for "hours later". Options: a second
  scheduled job on cron-job.org every few minutes, or checking at read time (the supplier's request lists and `GET /notifications` already
  poll every 20 to 25 seconds while the tab is open). A reminder is only useful to someone who is looking, so the read-time check may be enough.
- A new type (for example `release_reminder`), not a revival of the old shared `transfer_confirmation_needed`: one type, one meaning.

## Tests that fit a real SARIMAX can fail at a month boundary

**What happened.** On 1 Nov 2026 `test_corrected_same_day_reupload_replaces_the_cached_forecast` started failing on an unmodified
checkout of HEAD. The tests in that class fit a real model on a 40-day series ending today, and the dengue-season regressor is a
calendar (`dengue_season_index`: 1.0 for June to October, 0.0 otherwise). From 1 Nov the 40-day window contained both values, one fit
stopped producing a usable result, and the test failed purely because of the date. By the same logic it should go green again once the window sits wholly inside
November (about 40 days on) and fail again as the window crosses 1 Jun; neither of those is verified.

**What was done.** `SnapshotUndoAndCacheTests.setUp` now pins `app.dengue_season_index` to 1.0, the value the whole window had when the
tests were written. They prove caching and undo, not the regressor.

**What is still open.** Any test that fits a real model (not a stub) has the same exposure, and only that one class is pinned. By
grep (not checked one by one, and they all pass today), the files that call a fit or `get_forecast` and do not stub every call are
`test_sarimax_facility_forecast.py`, `test_sarimax_order_selection.py`, `test_ai_order_selection.py`,
`test_business_day_boundary.py`, `test_forecast_history.py` and `test_forecast_incremental.py`.

**The fix, not to be done piecemeal.** Pin the regressor (or freeze `business_today`) wherever a test fits a real model, in one pass:
find every such test, decide per file whether it is testing the regressor (then it should choose its dates on purpose) or something
else (then pin it), and ideally put the pin in one shared fixture or base class so a new test gets it by default. A guard that fails
the suite if a test fits a real model without the pin would stop it coming back.
