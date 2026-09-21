# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Hospital staff, blood bank staff, and BloodLink administrators. Hospital and blood bank accounts are facility-level (one login represents a facility, e.g. "Riverside General Hospital"); admin accounts have no facility and manage the network itself. All accounts are provisioned by an admin — a temporary password is issued and the account is forced to set a real password on first login. There is no self-service registration. The data model (facility addresses, a DOH license field) points to a Philippine healthcare operating context.

## Product Purpose

BloodLink coordinates blood supply between hospitals and blood banks: each facility tracks its own inventory, and facilities request and transfer units directly through the app instead of coordinating by phone or email. It exists to reduce the friction and opacity of manually coordinating blood supply, especially during shortages.

## Positioning

The core differentiator is direct hospital↔blood-bank coordination — real-time inventory visibility into one's own stock, plus a built-in request → accept → confirm-release → transfer workflow between facilities, not just single-facility record-keeping. AI-assisted shortage forecasting is a supporting capability, not the primary pitch.

## Operating Context

Built and evaluated as a thesis/capstone project — success is measured by a technical adviser/panel defense. It is nonetheless built and maintained to a real production standard (live-deployed on Vercel + Render, tested, iteratively hardened) rather than as a throwaway prototype.

Core workflows: CSV upload of inventory and donor rosters; per-blood-type minimum/maximum stock thresholds (currently one shared table across all facilities, not per-facility — a known simplification); emergency sourcing via nearest-facility search ranked by distance and blood-type compatibility; the request lifecycle (create → accept → confirm-release → transfer, using first-expiry-first-out unit selection); a donor roster with SMS "blast" outreach; an in-app coordination chat per request; and notifications for expiry warnings, forecast shortage alerts, request status changes, and blast completion.

## Capabilities and Constraints

- **Auth**: JWT-based; accounts are admin-provisioned only, with a forced password change on first login and a genuine self-service "forgot password" flow (emailed reset link via Resend).
- **Forecasting**: a hospital account sees current-stock-vs-minimum with explicit action prompts (no forecast). A blood-bank account sees a real OLS-trend forecast with a prediction interval once it has enough of its own history, or a synthetic SARIMAX stand-in — clearly labeled as such — rescaled to its real current stock when it doesn't.
- **Donor SMS blast is fully simulated end to end** — every message and log entry is explicitly labeled "SIMULATED — not actually sent"; no real SMS provider is connected yet.
- Admins can create and archive/restore facilities; there is deliberately no hard-delete path for a facility.
- A light/dark theme toggle exists (Account Menu), persisted per browser.
- Not yet built: the AI-assisted model-selection classifier described in the thesis write-up (feature extraction, candidate models, a trained selector). The current forecast uses one hand-fit model, not a learned selector — this is the largest gap between the thesis description and the shipped app.

## Brand Commitments

Name is "BloodLink," with an existing blood-drop mark (see the `BloodDropLogo` component). No other binding visual or voice constraints are recorded — visual direction belongs in DESIGN.md, not here.

## Evidence on Hand

Seeded demo accounts exist for all three roles (hospital, blood bank, admin) with real, populated demo data (facilities, inventory, requests, donors) rather than empty states. No real customer testimonials, case studies, or press exist, and none should be fabricated.

## Product Principles

1. Coordination over silos — the app's value is the cross-facility workflow, not just per-facility record-keeping.
2. Say what's real — simulated or synthetic capabilities (the SMS blast, the stand-in forecast model) are always visibly labeled as such, never presented as equivalent to the real thing.
3. Admin-controlled trust — every account traces back to an admin decision; there is no open signup surface to secure or moderate.
4. Built to hold up, not just to demo — treated as production-quality code and UX even though its audience of record is a thesis panel.

## Accessibility & Inclusion

No accessibility standard has been established yet.
