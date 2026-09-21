# BloodLink — Status Report

**Prepared:** 2026-07-23
**Purpose:** A complete, accurate account of what BloodLink currently does, for discussion with the thesis technical adviser. Everything described as "built" or "verified" below has been checked against the running code and live database — nothing here is aspirational.

**ARCHIVED NOTE (added later): this is the original, unedited 2026-07-23 text, kept verbatim as a historical snapshot only. It is stale and in places contradicted by the current system — see `BLOODLINK_STATUS_REPORT.md` in this same directory for why, and for a link to the maintained, current status record. Do not treat anything below as current.**

---

## Contents

1. [System Overview](#1-system-overview)
2. [What's Fully Built and Verified](#2-whats-fully-built-and-verified)
3. [Forecasting / ARIMAX Validation](#3-forecasting--arimax-validation)
4. [Known Limitations and Deliberate Scope Decisions](#4-known-limitations-and-deliberate-scope-decisions)
5. [What's Still Pending](#5-whats-still-pending-prioritized)
6. [Tech Stack Summary](#6-tech-stack-summary)

---

## 1. System Overview

BloodLink is a blood bank management system with two kinds of accounts that see genuinely different tools, because they solve different problems:

- **Blood banks** are the reserve — they hold larger stock and need to know *where things are heading*. Their dashboard shows a shortage **forecast**: a projected trend over the next 30 days, built from real daily history once enough of it exists.
- **Hospitals** are the point of use — they draw down a smaller working stock and need to know *what to do right now*. Their dashboard shows a **threshold view** instead: current stock vs. a safety minimum for each blood type, with an explicit "confirm this request" action the moment something dips below minimum.

This is not a cosmetic difference. The backend itself decides which of the two a given account gets, based on what kind of facility it belongs to — a hospital account can never receive forecast data, and a blood bank account can never receive the threshold-and-action view, regardless of what the frontend asks for. That split is described in full in [Section 2.7](#27-hospital-vs-blood-bank-dashboard-split).

Everything in the system — inventory, requests, transfers, donor outreach — is scoped to "your own facility." A logged-in user only ever sees and acts on their own facility's data, with one dev-only exception used for testing (explained in [Section 4](#4-known-limitations-and-deliberate-scope-decisions)).

---

## 2. What's Fully Built and Verified

### 2.1 Registration & Authentication

- Real login: email + password, checked against a bcrypt-hashed password, returns a signed session token (JWT) valid for 24 hours.
- Every subsequent action in the app is tied to that token — a user can only ever act as their own facility.
- Registration (creating a new facility account) works correctly on the backend but isn't yet reachable from the sign-in screen — see [Section 4](#4-known-limitations-and-deliberate-scope-decisions).

### 2.2 Inventory

- Full unit-by-unit tracking (one row per physical blood bag/donation — a "unit," not a volume measurement), each with its own ID, blood type, component (whole blood, platelets, plasma, etc.), collection date, expiration date, and status.
- Real expiry-status coloring: OK, near-expiry, critical, or expired, computed live from the expiration date.
- Scoped per facility — every facility only sees its own physical stock.

### 2.3 Dashboard

- Real-time summary: total units on hand, how many blood types are below minimum ("critical shortage"), how many units expire within a week, and a chart comparing current stock to minimum threshold for all 8 blood types.
- An expiry-warnings list surfaces anything expiring soon.
- (The forecast/threshold panel is described separately in [2.7](#27-hospital-vs-blood-bank-dashboard-split), since it's the one part of the dashboard that differs by account type.)

### 2.4 Emergency Sourcing

When a facility is short on a blood type, this screen finds who to ask:

- **Real distance ranking** — every candidate blood bank is ranked by actual straight-line distance from the requesting facility, calculated properly (accounting for the Earth's curvature, not just a flat map approximation).
- **Real stock check** — each candidate is checked against its own live inventory, minus a safety reserve, so "Available" actually means available.
- **Blood type compatibility fallback** — if the exact blood type requested isn't available at a facility, the system automatically suggests compatible alternative types using standard donor-recipient compatibility rules (for example, a facility that's out of A+ but has O- in stock will see O- offered as a valid substitute, since O- can be given to anyone).
- Submitting a request is real and creates an actual record other facilities can see and act on.

### 2.5 Priority-Scheduled Requests

- Incoming and outgoing requests are tracked with a real status lifecycle: pending → accepted or declined → (once accepted) completed.
- Requests are automatically priority-sorted: trauma and scheduled-surgery requests are queued ahead of routine restock requests. This re-sorting only ever affects requests still waiting for a decision — once a request has been accepted, its position is locked in, so a new urgent request arriving later can't bump something that's already moving.

### 2.6 Dual-Confirmation Transfers

Nothing is transferred based on one side's word alone:

1. The supplying facility confirms it's releasing stock — the system automatically picks which physical units to send using **first-expired-first-out** logic (the units closest to expiring go out first, so nothing valuable expires sitting on a shelf).
2. Only after the requesting facility separately confirms it actually received the stock does the system reassign those units to the new facility and mark the transfer complete.
3. A real, persisted coordination chat exists per transfer, so both sides can talk through logistics — visible only to the two facilities actually involved in that transfer.

### 2.7 Hospital vs. Blood Bank Dashboard Split

This is the core of the thesis's two-tier design, and it's real, not just a different look:

- The backend looks up what *kind* of facility is asking (hospital or blood bank) fresh from the database on every request — it never trusts anything the frontend claims.
- **Blood banks** get the forecast dashboard (described in Section 3).
- **Hospitals** get an "Action Required" panel instead: every blood type currently below its safety minimum is listed, worst-shortage-first, each with a plain-language explanation and a button that jumps straight to Emergency Sourcing with that blood type pre-selected. **Sending the request itself still requires a separate, explicit confirmation** — nothing is ever sent automatically.
- For hospitals, the "Active Requests" summary number on the dashboard is now real too, pulled directly from their actual outbound requests.
- Verified with an intentionally tricky edge case: a facility literally named *"General Hospital Blood Bank"* — the system correctly gave it the blood-bank (forecast) dashboard, proving the split is based on the facility's actual registered type, not a guess based on its name.

### 2.8 Donor SMS Blast

- Real donor roster management (upload a list of donors by blood type and phone number).
- Real "blast" creation and tracking: target a blood type, a number of donors needed, and a time limit; the system tracks confirmations in real time as they come in and automatically marks the drive complete once the target is hit or the deadline passes.
- **Sending is simulated, deliberately** — see [Section 4](#4-known-limitations-and-deliberate-scope-decisions). Every message is logged exactly as if it were sent, and the screen carries a permanent, impossible-to-miss "SIMULATED — not actually sent" banner so this is never mistaken for the real thing.

---

## 3. Forecasting / ARIMAX Validation

The thesis's forecasting model is a SARIMAX time-series model (a standard statistical forecasting technique that accounts for trend, recent history, weekly patterns, and an external factor — here, dengue season). Before trusting this model with any real numbers, it needed to be properly validated using the standard methodology for this kind of model: **identify** a sensible structure, **estimate** it, **check the diagnostics**, and only then use it to **forecast** — going back a step whenever a check fails, rather than pushing forward regardless.

**The problem:** there's no real historical inventory data yet — that's still pending a facility interview that hasn't been approved. So the entire validation was run on a generated, synthetic-but-realistic dataset first: two years of daily stock levels per blood type, deliberately built to include a seasonal dip during dengue season, so the validation process itself could be proven out before ever touching real numbers.

**What was tested, and what happened:**

- **Identification:** a statistical stationarity test confirmed the synthetic data behaves consistently over time (no need to "difference" it first) — expected, and correct.
- **Estimation:** the model was fit with a dengue-season indicator as an input. The dengue effect came out in the correct direction (stock drops during dengue season) for all 8 blood types, and was statistically confirmed for 2 of them — the other 6 showed the same real effect, just not strongly enough, relative to their own day-to-day noise, to call statistically certain. This is an honest, expected result, not a weak one: it tracked almost perfectly with how noisy each blood type's daily numbers were.
- **Diagnostic checking — this is where it gets interesting:** the standard test for "did the model capture everything predictable" (the Ljung-Box test) **failed for every blood type**. Digging into why showed the leftover pattern lined up exactly with the *synthetic data generator's* own restock schedule (which randomly restocks every 5 to 9 days) — a moving-target cycle like that can't be fully captured by this type of model, no matter how it's tuned. Several genuine attempts were made to fix this (adding a weekly seasonal term, trying higher-order variations) — each one measurably improved the model's overall fit, but none fully cleared the diagnostic test.
- **The honest conclusion drawn from this:** the diagnostic failure is a property of the *synthetic test data's* artificial restock pattern, not a flaw in the modeling approach itself — and this was written down explicitly rather than glossed over. Critically, a failed diagnostic doesn't automatically mean a useless forecast, so that was checked directly rather than assumed: running the model against held-out data it hadn't seen, it beat a "just guess last week's numbers" baseline for all 8 blood types, and beat a "just guess no change" baseline for 6 of 8. So despite the diagnostic not being clean, the forecasts themselves are still modestly, measurably useful — which is the more important practical question.

**Why this is a good outcome, not a red flag:** the entire point of validating on synthetic data first was to catch exactly this kind of issue safely, on data that doesn't matter, before it could happen on real facility data. It worked as intended — the process surfaced a real limitation, that limitation was traced to its actual root cause instead of being hand-waved, and it's now on record that **real historical data (once available) will need this same validation cycle run fresh** rather than assuming the synthetic result carries over. Along the way, this process also caught and fixed three separate real bugs in the modeling code itself (a data-generation bug, a missed-intercept statistics bug, and a model-fitting convergence bug) — each one found by cross-checking results against independently known facts rather than trusting numbers at face value. The full technical write-up, including every number, lives in `server/SYNTHETIC_ARIMAX_VALIDATION.md`.

Until real historical data is available, blood-bank dashboards use this synthetic-trained model as a clearly labeled stand-in (see [Section 4](#4-known-limitations-and-deliberate-scope-decisions)) — never presented as if it were based on real facility history.

---

## 4. Known Limitations and Deliberate Scope Decisions

Each of these was a conscious decision, not something overlooked. Reasons are kept to one line on purpose — happy to go deeper on any of them in discussion.

| Limitation | Why |
|---|---|
| **Donor SMS is simulated, not actually sent** | No real SMS provider is connected — this was scoped as a proof-of-concept for the outreach *workflow*, not a production messaging integration. |
| **Inventory CSV upload / "Add Unit" buttons don't do anything** | No backend support for creating or importing units exists yet — all current inventory came from developer-run setup scripts, not real usage. |
| **Safety thresholds (minimum units per blood type) are shared across all facilities** | There's no per-facility policy table yet — every facility currently uses the same minimums, which is a simplification, not a modeled reality. |
| **Registration has no verification/approval step** | Self-service registration grants access immediately by design, for this version — a real deployment would need a manual or automated verification gate before a new facility account is trusted. |
| **Forecasts run on a synthetic stand-in model until real historical data exists** | Real historical inventory data is pending an unapproved facility interview — see [Section 3](#3-forecasting--arimax-validation) for the full reasoning. |

**A few more, surfaced during a full system audit, worth having on record:**

- **Two developer-only backdoors are currently switched on** in the local environment: one lets any request impersonate any facility, the other enables donor-reply simulation tools. Both are off by default and must stay off in any real deployment — they exist purely to make local testing possible without needing dozens of real accounts.
- **No password-reset flow** exists yet (no button, no backend support).
- The **"Notify Nearby Hospitals" button** on the dashboard's expiry-warning list doesn't actually notify anyone yet — the real lookup it should trigger exists and works on the backend but isn't wired to that button.
- The **Emergency Sourcing map** is a static illustrative graphic, clearly labeled as such — not a real map or routing system.
- The dashboard's "Active Requests" count is real for hospital accounts now, but **still a hardcoded placeholder for blood-bank accounts**, a smaller follow-up task.

---

## 5. What's Still Pending, Prioritized

**Near-term / quick wins:**
1. Wire the "Register your facility" button on the login screen to the (already working) registration backend.
2. Wire "Notify Nearby Hospitals" to the real backend lookup that already exists for it.
3. Make the "Active Requests" dashboard number real for blood-bank accounts (already done for hospitals).
4. Clean up a leftover test request in the live database (a manually created 9,999-unit request, harmless but should be removed).

**Medium-term / real functionality gaps:**
5. Build a real inventory intake path — actual CSV upload and manual unit-entry, backed by a new endpoint (currently, inventory can only be populated by a developer running a script).
6. Add a password-reset flow.
7. Move safety thresholds from a single shared table to real per-facility policy.

**Before any real deployment:**
8. Turn off both developer-only backdoors (facility impersonation, reply simulation) — currently on for local testing only.
9. Add a real verification/approval step to registration.
10. Integrate an actual SMS provider to replace the simulated donor-blast sending.

**Longer-term / thesis-critical:**
11. Once real historical inventory data becomes available (pending the facility interview), run the full identification → estimation → diagnostic-checking validation cycle again on real data from scratch — the synthetic-data results validate the *method*, not the *numbers*, and shouldn't be assumed to carry over.

---

## 6. Tech Stack Summary

**Frontend**
- React 18 + TypeScript, built with Vite
- Tailwind CSS for styling
- Recharts for the dashboard charts

**Backend**
- Python, FastAPI framework
- SQLAlchemy (used for direct, hand-written SQL queries rather than an ORM — a deliberate choice for full visibility into exactly what's being queried)
- JWT-based authentication (PyJWT) with bcrypt password hashing

**Database**
- PostgreSQL, hosted on Supabase

**Forecasting**
- statsmodels (the SARIMAX model itself), pandas and numpy for data handling

**Verification tooling used throughout development** (not part of the shipped app)
- Playwright, for automated browser checks of every feature against the live system before considering it "done"
