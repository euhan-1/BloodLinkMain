# Backlog

Things noticed and deliberately not built yet. Each says why it is wanted and what a sensible version looks like, so it can be picked up cold.
Remove an entry when it ships.

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
