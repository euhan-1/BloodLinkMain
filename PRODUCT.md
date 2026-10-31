# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Hospital staff, blood bank staff, and BloodLink administrators. Hospital and blood bank accounts are facility-level (one login represents a facility, e.g. "Riverside General Hospital"); admin accounts have no facility and manage the network itself. A facility account comes into existence in one of two ways: an admin provisions it (a temporary password is issued and the account is forced to set a real password on first login), or the facility registers itself — the contact person proves ownership of their email with a one-time code, then an admin reviews and approves the registration before any account exists. Admin accounts are created only by a seed script, never through the app. The data model (facility addresses, a DOH license field) points to a Philippine healthcare operating context.

## Product Purpose

BloodLink coordinates blood supply between hospitals and blood banks: each facility tracks its own inventory, and facilities request and transfer units directly through the app instead of coordinating by phone or email. It exists to reduce the friction and opacity of manually coordinating blood supply, especially during shortages.

## Positioning

The core differentiator is direct hospital↔blood-bank coordination — real-time inventory visibility into one's own stock, plus a built-in request → accept → confirm-release → transfer workflow between facilities, not just single-facility record-keeping. AI-assisted shortage forecasting is a supporting capability, not the primary pitch.

## Operating Context

Built and evaluated as a thesis/capstone project — success is measured by a technical adviser/panel defense. It is nonetheless built and maintained to a real production standard (live-deployed on Vercel + Render, tested, iteratively hardened) rather than as a throwaway prototype.

Core workflows: CSV upload of inventory and donor rosters; per-blood-type minimum/maximum stock thresholds, set per facility (a new facility starts from default values); emergency sourcing via nearest-facility search ranked by distance and blood-type compatibility; the request lifecycle (create → accept → confirm-release → transfer, using first-expiry-first-out unit selection); a donor roster with SMS "blast" outreach; an in-app coordination chat per request; and notifications for expiry warnings, forecast shortage alerts, request status changes, and blast completion.

## Capabilities and Constraints

- **Auth**: JWT-based. Accounts are either provisioned by an admin (temporary password, forced change on first login) or created through self-registration with email one-time-code verification followed by admin approval. Two self-service password-reset flows exist: an emailed link and a 6-digit emailed code. Transactional email goes through Brevo (`EMAIL_PROVIDER`); Resend and SMTP are implemented alternatives, not the live provider.
- **Forecasting**: a hospital account sees current-stock-vs-minimum with explicit action prompts (no forecast). A blood-bank account is forecast per blood type, 30 days ahead, with a 95% prediction interval. Once the facility has 30 days of recorded history, each type gets its own SARIMAX forecast with a dengue-season regressor. With 3 to 29 days, or for any type whose SARIMAX fit is rejected as unsound, the type gets a linear trend. Below 3 days the account sees a synthetic SARIMAX stand-in — clearly labeled as such — rescaled to its real current stock.
- **Order labels**: Forecast details lists each blood type with how its SARIMAX order was obtained (the rows on the dashboard itself say only, in plain words, *from its own history* or *simple trend line*) — *grid-searched* (a per-facility offline search), *AI-recommended* (a small decision tree, next point) or *default order* (a fixed order) — or *linear trend* when no SARIMAX forecast is served. A tag names a mechanism only; it makes no claim about accuracy.
- **Nightly snapshot job**: stock is recorded per facility and blood type every day by a job that calls `POST /internal/daily-snapshot` at 23:30 Manila for every active facility, hospitals included, writing an explicit zero for a type with none in stock. Before it existed (its first successful run was 2026-10-27 23:30 Manila), a snapshot was written only when a blood bank loaded its forecast or uploaded history. The same job then archives each active facility's expired units (soft-delete, skipping any still reserved for a request), so dead units no longer pile up on the inventory page; the "Clear expired" button remains for clearing them before tonight.
- **Donor SMS blast is fully simulated end to end** — every message and log entry is explicitly labeled "SIMULATED — not actually sent"; no real SMS provider is connected yet.
- Admins can create and deactivate/reactivate facilities; there is deliberately no hard-delete path for a facility.
- A light/dark theme toggle exists (Account Menu), persisted per browser.
- **AI order selector**: built and deployed. A decision tree chooses a SARIMAX order for a blood type that has no grid-searched order. Its measured result is negative: it did not beat the fixed default order on synthetic held-out series (the Stage 1 gate failed), and on real series its non-seasonal orders forecast worse than the default. A guard therefore refuses a non-seasonal recommendation when the series has a significant weekday pattern, and logs each refusal. On facility 158's series, the only live series without grid-searched orders, the guard refuses it for every blood type (measured on that data). The selector is claimed as automation only, never as better accuracy.
- **Carry-forward limitation**: a day with no recorded snapshot is carried forward from the last recorded value when a forecast is fitted, not treated as missing. October 1–25, 2026 has no snapshot for any facility and cannot be recovered; it was caused by the nightly job not existing before 2026-10-27 combined with upload 605's undo on 2026-09-30. The figures here are a snapshot, not a standing claim: as of 2026-10-29, 25 of Northside's 453 modeled days (5.5%) are carried forward rather than observed, and as of 2026-10-29, 32 of facility 158's 189 (17%, counted facility-wide). The one-day step this creates on October 26 is why, as of 2026-10-29, four Northside types (A-, A+, AB+, O+) are served the linear trend: the soundness check rejects their SARIMAX fits. This is documented in the selector's limitations and was left unchanged on purpose.

## Brand Commitments

Name is "BloodLink," with an existing blood-drop mark (see the `BloodDropLogo` component). No other binding visual or voice constraints are recorded — visual direction belongs in DESIGN.md, not here.

## Evidence on Hand

Demo accounts exist for all three roles (hospital, blood bank, admin) with populated demo data (facilities, inventory, requests) rather than empty states; the four donor tables are currently empty. Demo account passwords are not stored in the repo. No real customer testimonials, case studies, or press exist, and none should be fabricated.

## Product Principles

1. Coordination over silos — the app's value is the cross-facility workflow, not just per-facility record-keeping.
2. Say what's real — simulated or synthetic capabilities (the SMS blast, the stand-in forecast model) are always visibly labeled as such, never presented as equivalent to the real thing.
3. Admin-controlled trust — every account traces back to an admin decision; there is no open signup surface to secure or moderate.
4. Built to hold up, not just to demo — treated as production-quality code and UX even though its audience of record is a thesis panel.

## Accessibility & Inclusion

No accessibility standard has been established yet.
