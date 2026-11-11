import os

# Single-threaded BLAS, set before numpy is ever imported (it is imported lazily, see
# _fit_sarimax_facility_forecast). On a throttled CPU share, BLAS worker threads
# contend for a fraction of a core; these SARIMAX matrices are tiny, so threads buy
# nothing. setdefault so the host's own environment can still override it.
for _var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_var, "1")

import csv
import hashlib
import hmac
import io
import json
import math
import secrets
import time
import warnings
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from typing import Optional

import jwt
from dotenv import load_dotenv
from fastapi import BackgroundTasks, Depends, FastAPI, File, Header, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, Field, StrictBool, field_validator
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

# pandas, scipy, and statsmodels are NOT imported here, deliberately — they're
# a multi-second import cost (numpy/scipy/pandas/statsmodels's C extensions)
# paid at process startup if imported at module level, and every request
# other than forecasting (login, inventory, requests, ...) never touches
# them. Each is imported lazily, right where it's used, inside
# _prediction_interval_half_width and _fit_sarimax_facility_forecast below —
# see those functions for why. This keeps `uvicorn main:app` reaching
# "Application startup complete" in ~1-2s instead of ~35s, which matters a
# lot on Render's free tier where a cold start already costs real time.

import auth
import client_ip
import email_service
import geocode_service
import server_timing
import upload_limits
from database import engine, read_engine, retry_read

load_dotenv()

# Placeholder calendar rule — should become regionally configurable rather than
# a hardcoded constant once there's a real settings mechanism.
DENGUE_SEASON_MONTHS = {6, 7, 8, 9, 10}

# Every calendar-day decision (is this unit expired, which day does today's snapshot
# belong to, is the forecast cache fresh, ...) is made on the Manila calendar: that is
# the day the facility's staff and their expiry dates are in. Instants (now(),
# datetime.now(timezone.utc), *_at columns, JWT/reset expiry) stay UTC and are unaffected.
# Compute it ONCE per request and pass it down (as a SQL parameter too, never CURRENT_DATE):
# a forecast request can run ~40 s, so a second lookup mid-request could straddle midnight.
try:
    BUSINESS_TZ = ZoneInfo("Asia/Manila")  # resolved once, at import; tzdata is a pinned requirement
except ZoneInfoNotFoundError as exc:  # fail the deploy loudly, not every request later
    raise RuntimeError("Time zone data for Asia/Manila is missing: install the pinned 'tzdata' package "
                       "(server/requirements.txt) - every calendar-day decision depends on it") from exc


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def business_today() -> date:
    return _utc_now().astimezone(BUSINESS_TZ).date()


# The real mathematical floor for _linear_trend's prediction interval to be
# defined at all, not an arbitrary round number — see
# _prediction_interval_half_width and SYNTHETIC_SARIMAX_VALIDATION.md's
# "Real-data path" section for the full derivation. Short version: an OLS
# prediction interval needs at least one residual degree of freedom
# (df = n - 2 >= 1), and below n=3 a line fits its points exactly (zero
# residual), leaving no honest interval width to report. This replaced the
# previous MIN_DAYS_REQUIRED = 7, which gatekept "is the real trend
# trustworthy" with a round number; now that job is done continuously by the
# interval itself (wide at n=3, narrowing as real history accumulates), and
# this constant's only remaining job is "is a real trend computable at all."
MIN_DAYS_REQUIRED = 3
FORECAST_CHECKPOINTS = [0, 5, 10, 15, 20, 25, 30]
FORECAST_INTERVAL_CONFIDENCE = 0.95
# How many days of observed totals GET /forecast returns as `history`, ending today, for the trend chart.
HISTORY_DAYS = 90

# A materially higher bar than MIN_DAYS_REQUIRED above: SARIMAX(0,1,4)x(1,0,1,7)
# needs enough runway to estimate a 4-lag MA term plus several full weekly
# seasonal cycles before its fit means anything. Below this, a facility that
# has cleared MIN_DAYS_REQUIRED still gets the plain linear trend
# (forecast_source: "linear_trend"), not SARIMAX.
SARIMAX_MIN_DAYS_REQUIRED = 30

# The fixed, already-validated order from SYNTHETIC_SARIMAX_VALIDATION.md —
# the same order fit_and_cache_synthetic_forecast.py uses for the synthetic
# reference model. Per-facility order selection (see SARIMAX_ORDER_GRID_D
# below) is a separate, later step that searches d as well as p/q/P/Q; this
# is only the FALLBACK for a facility+type with no selection yet.
#
# The fallback stays at d=1 deliberately, even though selection itself now
# searches d=0 too: with no evidence yet for this specific facility+type, d=1
# is the conservative default. Over-differencing a series that's actually
# stationary costs one redundant parameter (a near-unit MA root that cancels
# the difference — see PHASE_1_IDENTIFICATION.md's limitations for how
# starkly this shows up on the demonstration data). Under-differencing a
# series that's actually integrated invalidates the inference outright
# (a nominally stationary model fit to a non-stationary series). The two
# failure modes are not symmetric, so the untested default leans toward the
# cheaper mistake.
FACILITY_SARIMAX_ORDER = (0, 1, 4)
FACILITY_SARIMAX_SEASONAL_ORDER = (1, 0, 1, 7)
FACILITY_SARIMAX_LABEL = "SARIMAX(0,1,4)x(1,0,1,7)+dengue"
# What the response's sarimax_model_label says: only what is true for EVERY type that got a
# SARIMAX forecast. The order differs per type and per source (grid-searched, AI-recommended,
# default-order), so it lives in the per-type sarimax_orders map, never in this string.
FACILITY_SARIMAX_FAMILY_LABEL = "SARIMAX + dengue-season exogenous regressor"
# How an order was obtained, appended to every stored/reported order label. Deliberately NOT
# "fallback": sarimax_fallback_types already means "SARIMAX failed, linear trend used".
# The suffix names a mechanism; it says nothing about forecast quality.
ORDER_SOURCE_SUFFIX = {"grid": " [grid-searched]", "ai": " [ai-recommended]", "fixed": " [default-order]"}
_LEGACY_FIXED_SUFFIX = " [fallback]"  # how cache rows written before the rename spell "fixed"
# Significance level of the weekday-pattern test that stops a non-seasonal AI order being applied
# to a series with a weekly rhythm (see _resolve_order). A conventional alpha, not a tuned cut-off.
WEEKDAY_PATTERN_ALPHA = 0.05
FACILITY_SARIMAX_MAXITER = 200
# One GET /forecast fits at most this many blood types, then returns what it has
# plus a "pending" list; the dashboard polls for the rest. A cold fit is ~12-20 s
# on the deployed free-tier instance, so a full 8-type read (~100+ s) used to run
# into Vercel's 120 s proxy limit. Bounding the work per request bounds the request.
MAX_SARIMAX_FITS_PER_REQUEST = 2
_FORECAST_LOCK_NAMESPACE = 7301  # first key of the (namespace, facility_id) advisory lock
# (facility_id, blood_type, date) of fits that returned no usable result today, so
# every poll doesn't re-spend its fit budget on the same non-converging type.
# ponytail: per-process memory (the app runs one worker); a restart just retries once.
_SARIMAX_FIT_FAILED: set[tuple[int, str, date]] = set()

# ─── Per-type SARIMAX order selection (offline) ────────────────────────────
# FACILITY_SARIMAX_ORDER above was chosen once, offline, on synthetic data
# (SYNTHETIC_SARIMAX_VALIDATION.md) and applied to every facility and blood type
# alike. This grid search instead picks an order PER (facility, blood_type),
# from a live facility's own history, offline (never inside a forecast request —
# see _run_order_selection_for_facility), triggered in the background after a
# historical upload (see upload_historical_inventory_snapshots).
#
# Grid: p,q in {0,1,2}; d in {0,1}; seasonal P,Q in {0,1}; D=0, s=7 fixed — 72
# candidates. The dengue-season regressor is kept in every candidate. d was
# fixed at 1 through an earlier version of this mechanism; that was wrong for
# this data (see PHASE_1_IDENTIFICATION.md's limitations) — the raw ADF test
# identification runs is biased toward non-rejection by a level shift like the
# dengue flag, so a series that is actually stationary around that shift reads
# as non-stationary, and every d=1 fit ends up with an MA root at or on the
# unit circle, cancelling the difference it was never right to take. Rather
# than have identification try to out-guess that bias, d is now a searched
# parameter like everything else in the grid, decided the same way: by AIC and
# the Ljung-Box criterion, not assumed.
#
# SELECTION RULE: fit every candidate on TRAINING data only — the series minus
# the last ORDER_SELECTION_HOLDOUT_DAYS days — never the full series. Among
# candidates that CONVERGE on that training window, rank by AIC ascending and
# take the lowest-AIC candidate whose Ljung-Box test on ITS OWN training
# residuals — df-corrected using that candidate's OWN K =
# p+q+seasonal_p+seasonal_q (d is NOT a free parameter Ljung-Box needs to
# correct for; only the ARMA terms are), at lags 14, 21 AND 28 — passes at 0.05 on ALL
# THREE of those lags (not any one). All three, not any one, because this is
# the same PASS rule Chapter I Phase 3 already uses for the fixed order
# ("Ljung-Box (corrected) p > 0.05 at lags 14, 21 and 28"): passing only the
# shortest lag says nothing about serial correlation at the longer horizons
# the 30-day forecast actually depends on, and using the SAME rule here keeps
# the fixed-order and per-type numbers comparable rather than selecting
# against a laxer standard. Lag 7 is excluded from the criterion for the same
# reason PHASE_3_DIAGNOSTICS.md excludes it: at df = 7-K, the chi-square
# approximation the correction relies on is unreliable for the more complex
# candidates in this grid.
# If NO candidate passes, the best-AIC candidate among those that converged is
# still selected (never nothing), with criterion_satisfied=False — the live
# fitter has no way to fall back to "no forecast" for a type that has enough
# history, so it gets the best available fit, honestly labeled as not having
# satisfied the independence criterion (facility_sarimax_order.criterion_satisfied).
#
# POST-SELECTION INFERENCE: choosing the order on the same data its own
# diagnostics are then reported against would mean the model was picked to
# make those diagnostics pass — the in-sample Ljung-Box p-values above are
# evidence the SELECTION worked, not evidence the model fits data it hasn't
# seen. So the winning order is refit on that same training window and
# evaluated separately: forecast ORDER_SELECTION_HOLDOUT_DAYS days ahead,
# compare against the actual held-out observations (never used for selection),
# and store THAT Ljung-Box (on the 30 forecast errors, uncorrected — no
# parameters were estimated on this data, so no df-correction applies), MAPE
# and RMSE, alongside the same metrics for a naive last-training-value
# baseline over the identical window. Both sets of numbers are stored, clearly
# labeled _in_sample vs _holdout (see schema_facility_sarimax_order.sql).
SARIMAX_ORDER_GRID_PQ = (0, 1, 2)
SARIMAX_ORDER_GRID_D = (0, 1)  # searched, not assumed — see the module comment above
SARIMAX_ORDER_GRID_SEASONAL_PQ = (0, 1)
SARIMAX_ORDER_GRID_LAGS = (14, 21, 28)  # the lags the criterion is checked at
ORDER_SELECTION_HOLDOUT_DAYS = max(FORECAST_CHECKPOINTS)  # 30 — same horizon the live forecast itself covers
# A type needs this many days of history before order selection will even
# attempt it: enough for a real training fit (SARIMAX_MIN_DAYS_REQUIRED) PLUS
# a full hold-out window neither the selection nor its AIC ranking ever saw.
ORDER_SELECTION_MIN_DAYS = SARIMAX_MIN_DAYS_REQUIRED + ORDER_SELECTION_HOLDOUT_DAYS
MAX_ORDER_SELECTIONS_PER_BACKGROUND_RUN = 2  # see _run_order_selection_for_facility's max_types
_ORDER_SELECTION_LOCK_NAMESPACE = 7302  # a SEPARATE advisory-lock namespace from forecast fitting (7301):
# order selection (about 21 s per type locally, 72 fits — see server/select_orders.py, the primary
# path) and per-request forecast fitting are different operations with
# different time budgets: sharing one lock would make a slow selection run
# block ordinary forecast reads from ever fitting a still-missing type, for no
# benefit — a forecast read only wants the CURRENT stored order, selected or
# fallback, whichever is available right now, so it never needs to wait on
# selection.


def _auto_order_selection_enabled() -> bool:
    """Whether a successful historical upload starts order selection in the background. OFF unless
    AUTO_ORDER_SELECTION_AFTER_UPLOAD is true/1/yes/on. Read on every call so a test (or an operator restarting with the variable
    changed) needs no import-time state.

    Off by default because the task is not harmless on the deployed instance: it runs up to MAX_ORDER_SELECTIONS_PER_BACKGROUND_RUN
    types x 72 SARIMAX fits in the one web process (a fit is 12-20 s deployed, so tens of minutes: ESTIMATED from that figure, never
    measured), it competes for the CPU with every request, it upserts over any existing facility_sarimax_order row (and gives the
    type a new soundness baseline), and it deletes that type's cached forecast so the next dashboard load must refit. Selection is
    offline work: server/select_orders.py, run by hand after dumping the facility's rows (CLAUDE.md)."""
    return os.environ.get("AUTO_ORDER_SELECTION_AFTER_UPLOAD", "false").strip().lower() in ("true", "1", "yes", "on")

# CONVERGENCE, beyond mle_retvals.converged (see _sarimax_fit_is_sane). Northside O+
# investigation (2026-09): the training-window (390-day) SELECTION fit for O+'s winning
# order (1,0,2)x(1,0,1,7) converged cleanly (corrected Ljung-Box 0.44-0.80, 0/28 residual
# ACF lags outside bound). Refit on the FULL 420-day series from statsmodels' default
# start, mle_retvals.converged still reported True, but the fit had landed on a different,
# non-invertible optimum (ar.L1 -> 1.000011, a seasonal MA coefficient at -2.87, |theta|>1)
# with corrected Ljung-Box down to 2e-8 and 9/28 lags outside bound. Refitting the SAME
# 420-day series from the selection fit's own converged params (or from nm/powell then
# lbfgs) landed back in the good basin (AIC ~2382 vs ~2474 from the bad optimum) with clean
# diagnostics. mle_retvals.converged only means scipy's optimizer stopped moving — not that
# it stopped somewhere sane — so a second, calibrated check is needed after it.
#
# An absolute AR/MA root-modulus floor was tried first and does NOT work: AB-'s own
# training-window fit (and its production fit, warm-started from that same fit) both
# legitimately carry a root at modulus ~0.11 — a harmless near AR/MA cancellation, no AIC
# or Ljung-Box problem either fit — which sits BELOW the degenerate O+ fit's own worst root
# (~0.86). No single floor can pass AB- and fail O+, so root moduli are computed and
# returned for logging/diagnosis only; they do not gate _sarimax_fit_is_sane's verdict.
#
# What DOES cleanly separate them: AIC per observation, compared against the SAME order's
# training-window (selection) fit, scaled for the extra observations (AIC/N is comparable
# across different N; raw AIC is not). Calibrated on Northside's 8 currently-selected
# orders: every good production fit sits within +0.03 nats/observation of its own
# selection fit (worst: O-, +0.0265); the O+ convergence failure that prompted this sat at
# +0.2364 — about 9x that gap. 0.05 sits with real margin on both sides.
SARIMAX_AIC_PER_OBS_DEGRADATION_THRESHOLD = 0.05

EARTH_RADIUS_KM = 6371.0

# DEV-ONLY escape hatch (Step 6C), off unless explicitly enabled in .env. Never
# set this to true in a real deployment — it lets any request impersonate any
# facility via a plain header, no login required.
ALLOW_DEV_FACILITY_OVERRIDE = os.environ.get("ALLOW_DEV_FACILITY_OVERRIDE", "false").strip().lower() == "true"

# DEV-ONLY escape hatch (Step 7C), separate concern from the one above — gates
# the donor-blast test tools (simulate a reply, force a deadline into the
# past) that only exist because there's no real SMS provider yet.
ALLOW_DEV_TEST_TOOLS = os.environ.get("ALLOW_DEV_TEST_TOOLS", "false").strip().lower() == "true"

# The deployed frontend's real origin(s), comma-separated if more than one.
# Fails CLOSED when unset: no origins allowed, rather than "*" (any origin).
# An unset env var is a deploy-config mistake, and a wide-open CORS default
# would hide that mistake behind working-looking traffic; failing closed
# instead breaks every cross-origin request immediately and visibly, which is
# the failure direction you want for a system holding real facility data.
#
# CORSMiddleware matches allow_origins entries against the browser's Origin
# header with an EXACT string comparison — no normalization of its own. An
# Origin header is always just scheme://host[:port], never a trailing slash,
# so a value pasted into a host's env var UI with one (a very easy mistake —
# most browser address bars show one) silently never matches anything, with
# no error, just a missing Access-Control-Allow-Origin header on every
# response. Same failure mode for accidentally-included quote characters
# (pasting from a source that quoted the value) or mismatched case in the
# scheme. Normalizing all three here, per origin, means a config typo of
# exactly this shape degrades to "still works" instead of "fails silently
# with no indication why."
def _normalize_origin(raw: str) -> str:
    origin = raw.strip().strip("'\"").rstrip("/")
    if origin != "*" and "://" in origin:
        scheme, rest = origin.split("://", 1)
        origin = f"{scheme.lower()}://{rest}"
    return origin


def _parse_cors_origins(raw: Optional[str]) -> list[str]:
    """Pure so this can be tested without importing this whole module. Empty
    or unset -> [] (fail closed), never "*"."""
    raw = (raw or "").strip()
    return [_normalize_origin(o) for o in raw.split(",")] if raw else []


CORS_ALLOWED_ORIGINS = _parse_cors_origins(os.environ.get("CORS_ALLOWED_ORIGINS"))

# Base URL of the deployed frontend, used only to build the link inside a
# password-reset email (e.g. "{FRONTEND_BASE_URL}/reset-password?token=...").
# Defaults to local dev so this works out of the box before it's set in a
# real deployment's environment.
FRONTEND_BASE_URL = os.environ.get("FRONTEND_BASE_URL", "http://localhost:5173").rstrip("/")

# Self-service reset links are meant to be used within minutes of being
# requested — much shorter-lived than a normal session, and long enough that
# an email arriving a little late still works.
PASSWORD_RESET_LINK_EXPIRY = timedelta(hours=1)

# Where the "a facility registration is waiting for review" notice goes (see
# verify_facility_registration_email). Deliberately not validated as
# non-empty here — an unset value just makes the notification email fail
# (Resend rejects an empty "to"), caught and logged by the same
# catch-log-and-continue pattern every other email send already uses, rather
# than a special case for "not configured" on top of "SMTP unreachable."
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "")

VALID_FACILITY_TYPES = {"hospital", "bloodbank"}


# A token is valid for 24 hours and cannot be revoked, so every authenticated request also checks that the account behind it
# still exists and that its facility is still active; otherwise deactivating a facility would not stop a signed-in session.
# That is one query (and about four round trips) per check, so a PASS is remembered per user for AUTH_ACTIVE_CACHE_SECONDS. This
# cache is the longest a deactivation can go unnoticed by another worker, and the cap is 60 seconds: do not raise it above that.
# Only a pass is remembered (a rejection is looked up again, so reactivation takes effect at once), and deactivating through
# admin_update_facility_status clears it, so on the one worker this app runs it takes effect immediately.
AUTH_ACTIVE_CACHE_SECONDS = 30
_active_account_until: dict[int, float] = {}  # user id -> time.monotonic() until which the last check still counts


@retry_read
def _lookup_account(user_id: int):
    with read_engine.connect() as conn:
        return conn.execute(
            text(
                "SELECT u.facility_id, f.is_active AS facility_is_active FROM users u "
                "LEFT JOIN facilities f ON f.id = u.facility_id WHERE u.id = :id"
            ),
            {"id": user_id},
        ).mappings().first()


def _assert_account_active(user_id: int) -> None:
    now = time.monotonic()
    if _active_account_until.get(user_id, 0.0) > now:
        return
    row = _lookup_account(user_id)
    if row is None:
        _active_account_until.pop(user_id, None)
        raise HTTPException(status_code=401, detail="this account no longer exists, please log in again")
    if row["facility_id"] is not None and not row["facility_is_active"]:
        _active_account_until.pop(user_id, None)
        raise HTTPException(status_code=403, detail="this facility's account has been deactivated — contact your administrator")
    _active_account_until[user_id] = now + AUTH_ACTIVE_CACHE_SECONDS


def get_current_user(authorization: Optional[str] = Header(default=None)) -> dict:
    """Requires 'Authorization: Bearer <token>'. Real auth (Step 6B), plus the account-still-active check above."""
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="not authenticated")
    token = authorization[len("Bearer "):]
    try:
        user = auth.decode_access_token(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="session expired, please log in again")
    except (jwt.PyJWTError, ValueError):
        raise HTTPException(status_code=401, detail="invalid authentication token")
    _assert_account_active(int(user["sub"]))
    return user


def require_admin_role(user: dict = Depends(get_current_user)) -> dict:
    """Gates every /admin/* endpoint. role='admin' can only ever come from a
    real access token (see get_current_user), and role='admin' users can
    only ever be created by seed_admin_user.py — never through any HTTP
    endpoint, registration or otherwise. Returns the decoded user so callers
    that also want the admin's own id/email don't need a second dependency."""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="admin access required")
    return user


def get_user_from_reset_token(authorization: Optional[str] = Header(default=None)) -> dict:
    """Like get_current_user, but only accepts a password-reset token (see
    auth.create_password_reset_token) — used solely by
    POST /auth/change-password. A normal access token is rejected here just
    as firmly as a reset token is rejected everywhere else, so the two token
    kinds stay strictly non-interchangeable."""
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="not authenticated")
    token = authorization[len("Bearer "):]
    try:
        return auth.decode_password_reset_token(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="this reset link has expired, please log in again")
    except (jwt.PyJWTError, ValueError):
        raise HTTPException(status_code=401, detail="invalid or expired reset token")


def _try_get_user_from_token(authorization: Optional[str]) -> Optional[dict]:
    """Like get_current_user, but returns None instead of raising — used only
    by get_acting_facility_id so a missing/bad token doesn't short-circuit
    before the dev-override check below even runs."""
    if authorization is None or not authorization.startswith("Bearer "):
        return None
    token = authorization[len("Bearer "):]
    try:
        return auth.decode_access_token(token)
    except (jwt.PyJWTError, ValueError):
        return None


def get_acting_facility_id(
    authorization: Optional[str] = Header(default=None),
    x_dev_facility_id: Optional[int] = Header(default=None),
) -> int:
    """The logged-in user's facility_id, from a cryptographically verified JWT.

    DEV-ONLY escape hatch: when ALLOW_DEV_FACILITY_OVERRIDE is enabled and the
    request sends X-Dev-Facility-Id, that value wins over any real session —
    this is the old dev switcher's mechanism, restored but now opt-in rather
    than always-on (see ALLOW_DEV_FACILITY_OVERRIDE above). With the flag off
    (the default), this behaves exactly like sub-step B: real login required,
    a user can only ever act as their own facility.
    """
    if ALLOW_DEV_FACILITY_OVERRIDE and x_dev_facility_id is not None:
        return x_dev_facility_id

    user = _try_get_user_from_token(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="not authenticated")
    if user["facility_id"] is None:
        # An admin token (see require_admin_role) — admins have no facility
        # to act as, so every facility-scoped endpoint (inventory, dashboard,
        # requests, donors, ...) is off-limits rather than silently querying
        # with facility_id=NULL, which Postgres would just match nothing on.
        raise HTTPException(status_code=403, detail="admin accounts have no facility context")
    _assert_account_active(int(user["sub"]))
    return user["facility_id"]


def get_acting_user_id(authorization: Optional[str] = Header(default=None)) -> Optional[int]:
    """Best-effort real user id, for attribution only (upload_history.uploaded_by)
    — never for authorization, that's get_acting_facility_id's job alone.
    Returns None under the DEV-ONLY X-Dev-Facility-Id override with no real
    token behind it, since there's no real user to attribute the upload to
    in that case."""
    user = _try_get_user_from_token(authorization)
    return int(user["sub"]) if user else None


# ─── Notifications ─────────────────────────────────────────────────────────
# Facility-scoped. THE RULE: a row exists because something needs the facility or something changed in a transfer it is part
# of. When that stops being true the row resolves itself, meaning the system marks it read (read_at), never deletes it.
#   - Transfer events, written at the state change (request created / accepted / declined / cancelled / released / received):
#     incoming_request, request_accepted, request_declined, request_cancelled, receipt_needed, transfer_completed,
#     transfer_units_failed. Two of them are prompts and resolve on their own: incoming_request when the request leaves
#     "pending" (accepted, declined or cancelled), receipt_needed when receipt is confirmed. Any row linked to a request that
#     no longer exists resolves too (_resolve_stale_notifications).
#   - forecast_shortage: written when GET /forecast newly projects a type below its minimum, only for a type with at least
#     SARIMAX_MIN_DAYS_REQUIRED recorded days, in the Dashboard's wording, and resolved when the projection clears.
#   - expiring_unit_alert (a bank offering near-expiry units to hospitals) is written by that action and resolves once the units
#     it names have expired; blast_complete is written by its own action.
# Expiring units are NOT a notification: the Dashboard lists them, grouped, with an action (GET /inventory/expiring).
# unit_expiry rows written before that change remain; nothing writes them now.
NOTIFY_EXPIRY_NEAR_DAYS = 7


def _create_notification(conn, facility_id: int, notif_type: str, message: str, link: Optional[str] = None) -> None:
    conn.execute(
        text(
            "INSERT INTO notifications (facility_id, type, message, link) "
            "VALUES (:facility_id, :type, :message, :link)"
        ),
        {"facility_id": facility_id, "type": notif_type, "message": message, "link": link},
    )


_MONTH_NAMES = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _format_date(d: date) -> str:
    """"28 Oct 2026": written out here, not by strftime (whose month names follow the server's locale), the same form
    src/app/lib/dateFormat.ts writes."""
    return f"{d.day} {_MONTH_NAMES[d.month - 1]} {d.year}"


def _format_day_month(d: date) -> str:
    return f"{d.day} {_MONTH_NAMES[d.month - 1]}"


def _request_phrase(req) -> str:
    """"3-unit O+ Packed RBC request": what a request is, in every notification about it, so two requests for the same
    blood type read differently. The component is NULL on requests that predate it (not recorded): then it is left out."""
    component = req["component"]
    return f"{req['quantity']}-unit {req['blood_type']}{' ' + component if component else ''} request"


def _failed_units_text(expired: list, shown: int = 5) -> str:
    """"W0412 26 100200 (expired 28 Oct 2026), ... and 3 more": a long list of DINs is not something a notification can carry."""
    parts = [f"{u['din']} (expired {_format_date(u['expires_date'])})" for u in expired[:shown]]
    more = len(expired) - shown
    return ", ".join(parts) + (f", and {more} more" if more > 0 else "")


def _kilometers_text(km: float) -> str:
    return "1 kilometer" if km == 1 else f"{km} kilometers"


def _expiry_when(days_left: int, units: int) -> str:
    """"expires today" / "expire in 2 days" (the verb agrees with the number of units)."""
    verb = "expires" if units == 1 else "expire"
    return f"{verb} today" if days_left == 0 else f"{verb} in {days_left} {'day' if days_left == 1 else 'days'}"


def _resolve_notifications(conn, facility_id: int, notif_type: str, link: str) -> int:
    """Marks read, by the system, the unread `notif_type` notifications at `facility_id` that point at `link` (a transfer
    event just made them untrue). Never deletes. Returns how many rows it resolved."""
    return conn.execute(
        text(
            "UPDATE notifications SET read_at = now() "
            "WHERE facility_id = :f AND type = :t AND link = :l AND read_at IS NULL"
        ),
        {"f": facility_id, "t": notif_type, "l": link},
    ).rowcount


# The ONE definition of "this unread notification is no longer true" (see _resolve_stale_notifications). The UPDATE that marks such rows
# read and the unread count that leaves them out both use it, so the badge can never disagree with what the sweep would do. Needs the
# notifications table aliased `n`, and :today (the Manila business date).
_STALE_NOTIFICATION_SQL = """
(
  -- an offer of expiring units, once the date it names has passed
  (
    n.type = 'expiring_unit_alert'
    AND n.message ~ 'expires? (today|in [0-9]+ days?)'
    AND (n.created_at AT TIME ZONE 'Asia/Manila')::date
        + COALESCE((substring(n.message from 'expires? in ([0-9]+) days?'))::int, 0) < :today
  )
  -- anything about a request that is gone, or that no longer needs the prompt
  OR (
    n.link ~ '^requests:[0-9]+$'
    AND (
      NOT EXISTS (SELECT 1 FROM requests r WHERE r.id = (substring(n.link from '^requests:([0-9]+)$'))::bigint)
      OR EXISTS (
          SELECT 1 FROM requests r
          WHERE r.id = (substring(n.link from '^requests:([0-9]+)$'))::bigint
            AND (
              (n.type = 'incoming_request' AND r.status <> 'pending')
              OR (n.type = 'receipt_needed'
                  AND (r.requester_confirmed_at IS NOT NULL OR r.status IN ('completed', 'failed')))
            )
      )
    )
  )
)
"""


def _resolve_stale_notifications(conn, facility_id: Optional[int], today: Optional[date] = None) -> int:
    """Resolves (marks read) the unread notifications whose truth has already gone: the request no longer exists, an
    incoming_request whose request is no longer pending, a receipt prompt whose receipt has been confirmed, and an
    expiring_unit_alert whose units have expired. The request rules are the ones the transfer functions apply at the moment of
    the change, derived here from the request's state, so a missed event or a concurrent change cannot leave a finished to-do
    behind. `facility_id` None = every facility; `today` is the Manila business date (default: now).

    An offer of near-expiry units (expiring_unit_alert) carries no unit reference except its own text, which says when the units
    expire ("expire in 2 days", "expires today") counted from the day it was written. So its expiry date is that day plus those
    days, and the offer resolves once that date has passed, which is when the units can no longer be released. This reads the
    text _expiry_when writes: tests/test_notifications.py builds the offer through both notify endpoints, so rewording one
    without the other fails there."""
    today = today or business_today()
    return conn.execute(
        text(
            f"""
            UPDATE notifications n SET read_at = now()
            WHERE n.read_at IS NULL
              AND (CAST(:facility_id AS bigint) IS NULL OR n.facility_id = :facility_id)
              AND {_STALE_NOTIFICATION_SQL}
            """
        ),
        {"facility_id": facility_id, "today": today},
    ).rowcount


def _start_upload_record(
    conn, facility_id: int, upload_type: str, uploaded_by: Optional[int],
    filename: Optional[str], raw_content: Optional[str],
) -> int:
    """First half of logging a CSV upload — inserted before the batch's own
    rows so every blood_units/donors/inventory_snapshots row this upload
    writes can be tagged with the resulting id (upload_history_id), which is
    what makes a later undo able to target exactly this upload's rows and
    nothing else. rows_processed/rows_failed/error_details start at
    placeholder values and get filled in by _finish_upload_record once the
    caller's own row-by-row loop finishes.

    raw_content is the original file text for inventory and historical
    uploads (so staff can re-download exactly what they uploaded), and
    always None for donor uploads — donor CSVs carry personal information,
    and the donors table is already the one copy of that we want on file."""
    return conn.execute(
        text(
            """
            INSERT INTO upload_history
                (facility_id, upload_type, uploaded_by, filename, rows_processed, rows_failed, error_details, raw_content)
            VALUES
                (:facility_id, :upload_type, :uploaded_by, :filename, 0, 0, '[]'::jsonb, :raw_content)
            RETURNING id
            """
        ),
        {
            "facility_id": facility_id,
            "upload_type": upload_type,
            "uploaded_by": uploaded_by,
            "filename": filename,
            "raw_content": raw_content,
        },
    ).scalar_one()


def _finish_upload_record(conn, upload_id: int, rows_processed: int, errors: list[dict]) -> None:
    conn.execute(
        text(
            """
            UPDATE upload_history
            SET rows_processed = :rows_processed, rows_failed = :rows_failed, error_details = CAST(:error_details AS jsonb)
            WHERE id = :id
            """
        ),
        {
            "id": upload_id,
            "rows_processed": rows_processed,
            "rows_failed": len(errors),
            "error_details": json.dumps(errors, default=str),
        },
    )


def _last_wins(rows: list[dict], key) -> list[dict]:
    """One row per key, the later CSV line winning. A single multi-row INSERT ...
    ON CONFLICT DO UPDATE errors if two rows hit the same key ("cannot affect
    row a second time"), whereas the old row-by-row loop simply let the later
    line overwrite the earlier one; this keeps that outcome."""
    return list({key(r): r for r in rows}.values())


def _invalidate_facility_forecast_cache(conn, facility_id: int, blood_types: Optional[list[str]] = None) -> None:
    """Drops cached SARIMAX forecasts (all types, or just `blood_types`) so the
    next GET /forecast refits on the data as it now is. Freshness in
    _read_cached_sarimax is only "trained_through_date == today", which
    can't notice that the snapshots behind a cache changed. Every path that
    writes or removes a facility's inventory_snapshots calls this on the SAME
    connection as the data change, so a rolled-back change can't leave the
    cache cleared. Clearing is enough because a missing cache just triggers a
    fresh fit, and the cache is only read at all while the facility still has
    >= SARIMAX_MIN_DAYS_REQUIRED days of history."""
    _SARIMAX_FIT_FAILED.difference_update({k for k in _SARIMAX_FIT_FAILED if k[0] == facility_id})
    if blood_types is not None and not blood_types:
        return
    if blood_types is None:
        conn.execute(text("DELETE FROM facility_forecast_cache WHERE facility_id = :facility_id"), {"facility_id": facility_id})
    else:
        conn.execute(
            text("DELETE FROM facility_forecast_cache WHERE facility_id = :facility_id AND blood_type = ANY(:blood_types)"),
            {"facility_id": facility_id, "blood_types": blood_types},
        )


# ─── Upload undo ────────────────────────────────────────────────────────────
# One preview/apply pair per upload_type, sharing the same eligibility logic
# so what the confirmation screen shows is exactly what the undo itself acts
# on — recomputed fresh both times, never trusting a client-supplied row
# list, since eligibility (a unit's reservation, a donor's blast status) can
# change between when staff opens the confirmation and when they click through.

def _preview_historical_undo(conn, upload_id: int, facility_id: int) -> tuple[list[dict], list[dict]]:
    """Every (date, blood type) this upload wrote. Undo reverts each one: the
    row is deleted if the upload inserted it, or put back to the newest
    surviving value (another upload's, else the original organic one) if the
    upload overwrote something. Uploads made before inventory_snapshot_write_log
    existed have no log rows; for those the rows still tagged with the upload
    are listed instead (and deleted, as before)."""
    rows = conn.execute(
        text(
            """
            SELECT snapshot_date, blood_type, units FROM inventory_snapshot_write_log
            WHERE upload_history_id = :upload_id AND facility_id = :facility_id
            UNION
            SELECT snapshot_date, blood_type, units FROM inventory_snapshots
            WHERE upload_history_id = :upload_id AND facility_id = :facility_id
            ORDER BY snapshot_date, blood_type
            """
        ),
        {"upload_id": upload_id, "facility_id": facility_id},
    ).mappings().all()
    return [dict(r) for r in rows], []


def _apply_historical_undo(conn, upload_id: int, facility_id: int) -> tuple[int, list[dict]]:
    """Reverts exactly what this upload did to inventory_snapshots, using
    inventory_snapshot_write_log (see schema_snapshot_write_log.sql). For each
    (date, type) the upload wrote, the winner is the newest write-log entry
    from an upload that has NOT been undone (excluding this one), falling back
    to the original organic value; with no winner the upload inserted the row
    and it is deleted. Returns rows reverted (deleted + restored)."""
    reverted = conn.execute(
        text(
            """
            WITH keys AS (
                SELECT snapshot_date, blood_type FROM inventory_snapshot_write_log
                WHERE upload_history_id = :upload_id AND facility_id = :facility_id
            ),
            winner AS (
                SELECT DISTINCT ON (h.snapshot_date, h.blood_type)
                       h.snapshot_date, h.blood_type, h.units, h.upload_history_id
                FROM inventory_snapshot_write_log h
                JOIN keys k ON k.snapshot_date = h.snapshot_date AND k.blood_type = h.blood_type
                LEFT JOIN upload_history u ON u.id = h.upload_history_id
                WHERE h.facility_id = :facility_id
                  AND h.upload_history_id IS DISTINCT FROM :upload_id
                  AND (h.upload_history_id IS NULL OR u.undone_at IS NULL)
                ORDER BY h.snapshot_date, h.blood_type, (h.upload_history_id IS NULL), h.id DESC
            ),
            restored AS (
                UPDATE inventory_snapshots s
                SET units = w.units, upload_history_id = w.upload_history_id
                FROM winner w
                WHERE s.facility_id = :facility_id AND s.snapshot_date = w.snapshot_date AND s.blood_type = w.blood_type
                RETURNING 1
            ),
            deleted AS (
                DELETE FROM inventory_snapshots s
                USING keys k
                WHERE s.facility_id = :facility_id AND s.snapshot_date = k.snapshot_date AND s.blood_type = k.blood_type
                  AND NOT EXISTS (SELECT 1 FROM winner w WHERE w.snapshot_date = k.snapshot_date AND w.blood_type = k.blood_type)
                RETURNING 1
            )
            SELECT (SELECT count(*) FROM restored) + (SELECT count(*) FROM deleted)
            """
        ),
        {"upload_id": upload_id, "facility_id": facility_id},
    ).scalar_one()
    # Legacy uploads (made before the write log existed) left no log rows:
    # whatever is still tagged with this upload is deleted, as undo always did.
    legacy = conn.execute(
        text("DELETE FROM inventory_snapshots WHERE upload_history_id = :upload_id AND facility_id = :facility_id"),
        {"upload_id": upload_id, "facility_id": facility_id},
    ).rowcount
    _invalidate_facility_forecast_cache(conn, facility_id)
    return reverted + legacy, []


def _inventory_undo_rows(conn, upload_id: int) -> list:
    """This upload's units, deliberately NOT filtered by facility_id: the tag
    survives confirm_receipt, so a unit that has since moved to another
    facility is still found here and reported as transferred rather than
    silently vanishing from both lists. created_by_upload: the INSERT runs in
    the same transaction as _start_upload_record, so both now() values are
    identical exactly when this upload created the unit; otherwise it
    overwrote a unit that already existed."""
    return conn.execute(
        text(
            """
            SELECT b.id, b.din, b.blood_type, b.facility_id, b.reserved_for_request_id,
                   b.created_at = u.uploaded_at AS created_by_upload,
                   EXISTS (SELECT 1 FROM request_unit_failures f WHERE f.blood_unit_id = b.id) AS failed_in_transfer
            FROM blood_units b JOIN upload_history u ON u.id = b.upload_history_id
            WHERE b.upload_history_id = :upload_id
            ORDER BY b.din
            """
        ),
        {"upload_id": upload_id},
    ).mappings().all()


def _classify_inventory_undo_rows(rows: list, facility_id: int) -> tuple[list[dict], list[dict]]:
    """Split this upload's units into what's still safe to remove and what
    isn't. Only a unit the uploading facility still owns, that isn't reserved,
    wasn't released for a transfer that failed on expiry (see confirm_receipt),
    and that this upload created is removable. A unit the upload merely
    updated is left alone: blood_units keeps no record of its values before
    the upload, so there is nothing to restore it to, and deleting it would
    destroy stock that predates the upload. blocked[].skip ("transferred" /
    "modified") feeds the counts in undo_upload's response."""
    eligible, blocked = [], []
    for r in rows:
        row = {"id": r["id"], "din": r["din"], "blood_type": r["blood_type"]}
        if r["facility_id"] != facility_id:
            blocked.append({**row, "skip": "transferred", "reason": "already transferred to another facility"})
        elif r["reserved_for_request_id"] is not None:
            blocked.append({**row, "skip": "transferred", "reason": "reserved for a pending transfer"})
        elif r["failed_in_transfer"]:
            blocked.append({**row, "skip": "transferred", "reason": "released for a transfer and expired before receipt"})
        elif not r["created_by_upload"]:
            blocked.append({**row, "skip": "modified", "reason": "existed before this upload; its earlier values weren't recorded, so it was left as is"})
        else:
            eligible.append(row)
    return eligible, blocked


def _preview_inventory_undo(conn, upload_id: int, facility_id: int) -> tuple[list[dict], list[dict]]:
    return _classify_inventory_undo_rows(_inventory_undo_rows(conn, upload_id), facility_id)


def _apply_inventory_undo(conn, upload_id: int, facility_id: int) -> tuple[int, list[dict]]:
    eligible, blocked = _classify_inventory_undo_rows(_inventory_undo_rows(conn, upload_id), facility_id)
    removed = 0
    if eligible:
        # facility_id and reservation re-checked in the DELETE itself: defence in
        # depth so a stale tag can never reach a unit another facility holds.
        removed = conn.execute(
            text(
                "DELETE FROM blood_units WHERE id = ANY(:ids) AND facility_id = :facility_id "
                "AND upload_history_id = :upload_id AND reserved_for_request_id IS NULL"
            ),
            {"ids": [r["id"] for r in eligible], "facility_id": facility_id, "upload_id": upload_id},
        ).rowcount
    return removed, blocked


def _donor_blast_block_reasons(conn, donor_ids: list[int]) -> dict[int, str]:
    """Batched: one pair of set-based queries for a whole list of donor ids,
    instead of the two queries per donor this used to run inside a Python
    loop (see _preview_donor_undo / _apply_donor_undo). A donor id absent
    from the returned dict has no blast involvement at all (safe to delete).
    Otherwise the value is a human-readable reason, checked in priority
    order so it matches what's actually stopping the deletion: a confirmed
    reply on a still-active drive is the case staff asked about by name;
    anything else with blast history (messaged, or replied on a now-completed
    drive) still blocks deletion for a structural reason — blast_messages/
    blast_replies both hold a NOT NULL reference to the donor, and deleting
    them alongside the donor would quietly rewrite a "SIMULATED — not
    actually sent" record that's supposed to be as trustworthy as the
    upload log itself."""
    if not donor_ids:
        return {}

    reasons: dict[int, str] = {}

    active_confirmed_ids = conn.execute(
        text(
            """
            SELECT DISTINCT br.donor_id FROM blast_replies br
            JOIN blasts b ON b.id = br.blast_id
            WHERE br.donor_id = ANY(:donor_ids) AND br.reply = 'yes' AND b.status = 'active'
            """
        ),
        {"donor_ids": donor_ids},
    ).scalars().all()
    for donor_id in active_confirmed_ids:
        reasons[donor_id] = "confirmed on an active donor drive"

    remaining_ids = [d for d in donor_ids if d not in reasons]
    if remaining_ids:
        has_history_ids = conn.execute(
            text(
                """
                SELECT donor_id FROM blast_messages WHERE donor_id = ANY(:donor_ids)
                UNION
                SELECT donor_id FROM blast_replies WHERE donor_id = ANY(:donor_ids)
                """
            ),
            {"donor_ids": remaining_ids},
        ).scalars().all()
        for donor_id in has_history_ids:
            reasons[donor_id] = "has message history from a donor drive"

    return reasons


def _preview_donor_undo(conn, upload_id: int, facility_id: int) -> tuple[list[dict], list[dict]]:
    rows = conn.execute(
        text(
            """
            SELECT id, name, phone, blood_type FROM donors
            WHERE upload_history_id = :upload_id AND facility_id = :facility_id
            ORDER BY name
            """
        ),
        {"upload_id": upload_id, "facility_id": facility_id},
    ).mappings().all()
    reasons = _donor_blast_block_reasons(conn, [r["id"] for r in rows])
    eligible, blocked = [], []
    for r in rows:
        reason = reasons.get(r["id"])
        if reason is None:
            eligible.append({"name": r["name"], "phone": r["phone"], "blood_type": r["blood_type"]})
        else:
            blocked.append({"name": r["name"], "phone": r["phone"], "blood_type": r["blood_type"], "reason": reason})
    return eligible, blocked


def _apply_donor_undo(conn, upload_id: int, facility_id: int) -> tuple[int, list[dict]]:
    rows = conn.execute(
        text(
            """
            SELECT id, name, phone, blood_type FROM donors
            WHERE upload_history_id = :upload_id AND facility_id = :facility_id
            """
        ),
        {"upload_id": upload_id, "facility_id": facility_id},
    ).mappings().all()
    reasons = _donor_blast_block_reasons(conn, [r["id"] for r in rows])
    eligible_ids, blocked = [], []
    for r in rows:
        reason = reasons.get(r["id"])
        if reason is None:
            eligible_ids.append(r["id"])
        else:
            blocked.append({"name": r["name"], "phone": r["phone"], "blood_type": r["blood_type"], "reason": reason})
    if eligible_ids:
        conn.execute(text("DELETE FROM donors WHERE id = ANY(:ids)"), {"ids": eligible_ids})
    return len(eligible_ids), blocked


UPLOAD_UNDO_HANDLERS = {
    "historical_stock": (_preview_historical_undo, _apply_historical_undo),
    "inventory": (_preview_inventory_undo, _apply_inventory_undo),
    "donors": (_preview_donor_undo, _apply_donor_undo),
}


_SHORTAGE_MSG_HEAD = "projected below its minimum"  # the Dashboard's wording: "O+: projected below its minimum around 4 Nov; ordering 90 units ..."


def _shortage_message(blood_type: str, alert: dict, recommendation: Optional[dict], today: date) -> str:
    """The Dashboard's sentence (outlookFor in src/app/lib/bankDashboard.ts) behind the blood type. With a recommendation it
    says the order that brings the type back to its maximum; without one (no maximum configured) it says only when."""
    if recommendation is not None:
        when = "today" if recommendation["days_until_breach"] <= 0 else f"around {_format_day_month(date.fromisoformat(recommendation['breach_date']))}"
        units = recommendation["recommended_units"]
        return f"{blood_type}: {_SHORTAGE_MSG_HEAD} {when}; ordering {units} {'unit' if units == 1 else 'units'} brings it back to its maximum."
    days = alert["days_until_threshold"]
    when = "today" if days <= 0 else f"around {_format_day_month(today + timedelta(days=days))}"
    return f"{blood_type}: {_SHORTAGE_MSG_HEAD} {when}."


def _read_tracked_alert_types(conn, facility_id: int) -> list[str]:
    """The blood types currently marked alerting for this facility (forecast_alert_state)."""
    return list(conn.execute(
        text("SELECT blood_type FROM forecast_alert_state WHERE facility_id = :facility_id AND alerting = TRUE"),
        {"facility_id": facility_id},
    ).scalars().all())


def _shortage_check_needs_writes(tracked: list[str], alerts: list[dict], evaluated_types: Optional[set[str]] = None) -> bool:
    """Whether _check_forecast_shortage_notifications would write anything, given the alert state read a moment ago: a type alerting
    that is not yet tracked (to flip on and notify), or a tracked type in `evaluated_types` that is no longer alerting (to clear).
    The mirror of that function's two loops; tests/test_forecast_one_connection.py runs both over the same cases."""
    alerting = {a["type"] for a in alerts}
    tracked_now = set(tracked)
    still_tracked = tracked_now if evaluated_types is None else tracked_now & evaluated_types
    return bool(alerting - tracked_now) or bool(still_tracked - alerting)


def _check_forecast_shortage_notifications(
    conn, facility_id: int, alerts: list[dict], evaluated_types: Optional[set[str]] = None,
    recommendations: Optional[dict[str, dict]] = None, today: Optional[date] = None,
) -> None:
    """Runs as a side effect of GET /forecast, blood-bank branches only —
    called with whatever `alerts` that call already computed, so this never
    re-derives the trend/synthetic-model logic itself. forecast_alert_state
    is what makes "newly flips to predicting a shortage" mean an actual
    transition (false -> true), not "still alerting from before".

    The caller passes only alerts it is willing to stand behind: a type with fewer than SARIMAX_MIN_DAYS_REQUIRED recorded
    days, and the synthetic stand-in, are left out (the Dashboard says "not enough history" for them, and a notification
    must not say more than the screen does). A type left out while it is in `evaluated_types` is read as "not alerting".
    `recommendations` ({blood_type: recommendation}) gives the text the Dashboard's order and date.

    The false->true check-and-set for each alerting type is done as ONE
    atomic UPSERT (WHERE alerting = FALSE on the DO UPDATE branch), not a
    separate SELECT followed by a separate write. Two GET /forecast calls
    landing close together — a real, observed case, not hypothetical — would
    otherwise both read the "not yet alerting" state before either commits
    its write, and both create a notification for what is genuinely one
    transition. Postgres serializes concurrent writers on the same conflict
    target row, so the second call's UPSERT can only run after the first has
    committed, and by then its own WHERE alerting = FALSE no longer matches —
    it updates nothing and RETURNING yields no row, so nothing double-fires."""
    today = today or business_today()
    recommendations = recommendations or {}
    alerting_types = {a["type"] for a in alerts}

    # One read first: a type already alerting needs no write at all (its upsert below could only match nothing), so a dashboard
    # load that finds the same shortages as the last one sends no per-type statements. The upsert stays atomic for the types that
    # are not yet alerting, so two concurrent calls still produce one notification.
    currently_tracked = conn.execute(
        text("SELECT blood_type FROM forecast_alert_state WHERE facility_id = :facility_id AND alerting = TRUE"),
        {"facility_id": facility_id},
    ).scalars().all()

    for alert in alerts:
        blood_type = alert["type"]
        if blood_type in currently_tracked:
            continue
        flipped = conn.execute(
            text(
                """
                INSERT INTO forecast_alert_state (facility_id, blood_type, alerting, updated_at)
                VALUES (:facility_id, :blood_type, TRUE, now())
                ON CONFLICT (facility_id, blood_type) DO UPDATE
                    SET alerting = TRUE, updated_at = now()
                    WHERE forecast_alert_state.alerting = FALSE
                RETURNING blood_type
                """
            ),
            {"facility_id": facility_id, "blood_type": blood_type},
        ).first()
        if flipped is not None:
            _create_notification(
                conn, facility_id, "forecast_shortage",
                _shortage_message(blood_type, alert, recommendations.get(blood_type), today),
                "dashboard",
            )

    # Clearing alerting back to false never creates a notification either way,
    # so there's no race to protect against here — a plain conditional UPDATE
    # (only touching rows that are currently TRUE) is enough.
    # evaluated_types: on a partial forecast (types still pending) a type that simply
    # wasn't evaluated yet must not be read as "no longer alerting".
    still_tracked = set(currently_tracked) if evaluated_types is None else set(currently_tracked) & evaluated_types
    for blood_type in still_tracked - alerting_types:
        conn.execute(
            text(
                """
                UPDATE forecast_alert_state SET alerting = FALSE, updated_at = now()
                WHERE facility_id = :facility_id AND blood_type = :blood_type AND alerting = TRUE
                """
            ),
            {"facility_id": facility_id, "blood_type": blood_type},
        )
        # Notifications have no archive/delete column; "read_at" is how they are
        # resolved everywhere else, so the now-obsolete shortage notice is marked
        # read (it stays in history, just stops counting as unread). The blood
        # type is only recoverable from the message text: the prefix _shortage_message writes.
        conn.execute(
            text(
                """
                UPDATE notifications SET read_at = now()
                WHERE facility_id = :facility_id AND type = 'forecast_shortage' AND read_at IS NULL
                  AND message LIKE :prefix
                """
            ),
            {"facility_id": facility_id, "prefix": f"{blood_type}: {_SHORTAGE_MSG_HEAD}%"},
        )


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def _linear_trend(points: list[tuple[int, int]]) -> tuple[float, float]:
    """Least-squares slope/intercept. Not ARIMAX — a deliberately simple trend
    line, used only once enough real daily history exists to fit one."""
    n = len(points)
    sum_x = sum(x for x, _ in points)
    sum_y = sum(y for _, y in points)
    sum_xy = sum(x * y for x, y in points)
    sum_x2 = sum(x * x for x, _ in points)
    denom = n * sum_x2 - sum_x * sum_x
    if denom == 0:
        return 0.0, sum_y / n
    slope = (n * sum_xy - sum_x * sum_y) / denom
    intercept = (sum_y - slope * sum_x) / n
    return slope, intercept


def _prediction_interval_half_width(
    points: list[tuple[int, int]], slope: float, intercept: float, x0: int,
    confidence: float = FORECAST_INTERVAL_CONFIDENCE,
) -> Optional[float]:
    """Half-width of a (confidence*100)% OLS prediction interval for a NEW
    observation at x0, from the same fit `_linear_trend` already produced
    over `points`. Standard formula:

        half_width = t(df, confidence) * s * sqrt(1 + 1/n + (x0 - x_bar)^2 / Sxx)

    where s = sqrt(RSS / df) is the residual standard error and
    df = n - 2. This one quantity is why a separate MIN_DAYS_REQUIRED
    threshold no longer has to gatekeep "is this trend trustworthy" — the
    (x0 - x_bar)^2 term grows with distance into the future (the chart's
    band widens the further out it projects) and the 1/n and s terms both
    shrink as real history accumulates, so trust is communicated
    continuously instead of via a hard cutover.

    Returns None below n=3: with only 2 points a line fits them exactly
    (zero residual), leaving df=0 and no residual variance to estimate an
    interval from at all — not a design choice, a property of least squares.
    """
    n = len(points)
    if n < 3:
        return None
    df = n - 2
    x_bar = sum(x for x, _ in points) / n
    ss_xx = sum((x - x_bar) ** 2 for x, _ in points)
    if ss_xx == 0:
        return None
    rss = sum((y - (intercept + slope * x)) ** 2 for x, y in points)
    mse = rss / df
    se_pred = math.sqrt(mse * (1 + 1 / n + (x0 - x_bar) ** 2 / ss_xx))
    from scipy import stats as scipy_stats  # lazy — see the import comment near the top of this file
    t_crit = scipy_stats.t.ppf(1 - (1 - confidence) / 2, df)
    return t_crit * se_pred


def dengue_season_index(d: date) -> float:
    """Binary Philippine dengue/rainy-season calendar indicator: 1.0 for
    June-October, 0.0 otherwise. Same months as DENGUE_SEASON_MONTHS, which
    stays the single source of truth for "what counts as dengue season"
    everywhere else in this file (alert text, the synthetic reference
    model's own exog) — this function is just that set-membership check
    wrapped so both a training-window exog column and a future exog column
    can be built from nothing but a date range.

    Deliberately kept binary rather than a smoother continuous weighting:
    FACILITY_SARIMAX_ORDER was only ever validated (see
    SYNTHETIC_SARIMAX_VALIDATION.md) against this exact binary exog shape,
    and changing that shape here — without re-running that validation — would
    be fitting an untested model, not the validated one. A continuous
    severity index is a reasonable future improvement, but belongs in the
    same step that revisits the order itself.

    TRIED AND REVERTED (2026-09-23): a graded 0-1 monthly climatology,
    derived from real Batangas DOH surveillance data (see
    batangas_dengue_2016_2021_real.csv / derive_dengue_season_index.py /
    DENGUE_INDEX_DERIVATION.md), was tested as a replacement — TWO candidate
    versions, actually, since the first spec's year-exclusion window
    conflicted with this repo's own prior precedent (see that doc). BOTH
    failed live re-verification on facility 158's real history (152 days,
    all 8 blood types): the dengue coefficient lost significance for every
    type either way (p 0.40-0.97, vs. p<0.05 on 7/8 types with this binary
    flag) and AIC got worse across the board. Likely cause: facility 158's
    history spans only one partial season (Apr-Sep) with a single on/off
    transition, which the binary flag can fit as a simple level-shift even
    if it isn't capturing genuine repeatable seasonality — a graded shape no
    longer aligns with that same confound. Revisit only against a facility
    with real multi-season history and a fresh significance check.

    Because it's a pure calendar lookup, future exog values for the 30-day
    forecast horizon come for free — no forecasting of the exogenous
    variable itself is needed.
    """
    return 1.0 if d.month in DENGUE_SEASON_MONTHS else 0.0


def _resample_daily_series(
    type_rows: dict[date, int], observed_dates: set[date], end: date
) -> list[tuple[date, int]]:
    """Turns one blood type's own (possibly gappy) snapshot rows into a
    continuous day-by-day series from that type's own first observed date
    through `end` inclusive. SARIMAX needs a regular daily frequency for its
    weekly seasonal term (s=7) to mean anything; inventory_snapshots only
    ever has rows for days someone actually loaded the dashboard (or
    backfilled), so gaps are the normal case, not an edge case.

    Two different kinds of gap, deliberately handled differently:
      - A day this facility WAS observed on (it's in `observed_dates` —
        i.e. at least one OTHER blood type got a snapshot row that day) but
        THIS type has no row for it is a real zero. That's exactly why the
        GROUP BY in GET /forecast's snapshot INSERT never produced a row for
        it: this type genuinely had zero units on hand that day.
      - A day this facility was NEVER observed at all (no type has a row for
        it — the dashboard just wasn't loaded that day) is genuinely
        missing, not a zero. A stock level doesn't reset to 0 because nobody
        checked it; the last known level is carried forward instead of
        fabricating a stockout that didn't happen.

    Starts at this type's own first observed date, not the facility's
    overall first date, so nothing is invented before this type had any
    recorded presence at all. Returns [] if `type_rows` is empty.
    """
    type_dates = sorted(type_rows)
    if not type_dates:
        return []

    result: list[tuple[date, int]] = []
    last_known = type_rows[type_dates[0]]
    d = type_dates[0]
    while d <= end:
        if d in type_rows:
            last_known = type_rows[d]
            result.append((d, last_known))
        elif d in observed_dates:
            last_known = 0
            result.append((d, 0))
        else:
            result.append((d, last_known))
        d += timedelta(days=1)
    return result


def _count_observed_days(daily_series: list[tuple[date, int]], observed_dates: set[date]) -> int:
    """How many days of `daily_series` were actually observed: len(daily_series) also counts the
    days _resample_daily_series filled in (carried forward) because nobody recorded that day.
    Equals the API's days_of_history whenever the type starts at the facility's first date."""
    return sum(1 for d, _ in daily_series if d in observed_dates)


def _history_totals(history_rows, today: date, days: int = HISTORY_DAYS) -> list[dict]:
    """The facility's total stock per day for the last `days` days ending `today`, for the trend chart:
    [{"date": ISO, "units": total across all blood types, "observed": bool}], oldest first.

    `units` is built from the same per-type series the SARIMAX fit is fed (_resample_daily_series), so a day
    nobody recorded carries the last recorded value forward exactly as the model saw it, and a blood type with
    no row on a day somebody did record counts as a real zero. `observed` is true when the facility has at
    least one snapshot row that date, which is the same test _resample_daily_series uses to tell the two apart.
    Independent of any fit: the rows are already loaded, so this works while forecasts are still pending, and
    for the linear-trend path, which never builds the per-type daily series itself."""
    observed_dates = {r["snapshot_date"] for r in history_rows}
    by_type: dict[str, dict[date, int]] = {}
    for r in history_rows:
        by_type.setdefault(r["blood_type"], {})[r["snapshot_date"]] = r["units"]
    totals: dict[date, int] = {}
    for type_rows in by_type.values():
        for d, units in _resample_daily_series(type_rows, observed_dates, today):
            totals[d] = totals.get(d, 0) + units
    start = today - timedelta(days=days - 1)
    return [{"date": d.isoformat(), "units": totals[d], "observed": d in observed_dates} for d in sorted(totals) if d >= start]


def _series_point_fields(checkpoint: int, today: date) -> dict:
    """The label and the real date of one forward-series point, so the chart can put history and forecast on one
    date axis. Both forward series (SARIMAX/linear trend and the synthetic stand-in) build their points from this."""
    return {"day": "Today" if checkpoint == 0 else f"Day {checkpoint}", "date": (today + timedelta(days=checkpoint)).isoformat()}


def _sarimax_fit_is_sane(fit, n_obs: int, baseline_aic_per_obs: Optional[float]) -> tuple[bool, str, dict]:
    """Whether a completed .fit() call is trustworthy enough to serve or store — not just
    "converged". See the module comment above SARIMAX_AIC_PER_OBS_DEGRADATION_THRESHOLD for
    why mle_retvals.converged (checked first, below) isn't enough on its own, why an AR/MA
    root-modulus floor doesn't work for this data, and why AIC-per-observation vs the same
    order's training-window fit is what actually catches it.

    Shared by all three places a SARIMAX model gets fit: the grid search
    (_fit_candidate_order, baseline_aic_per_obs=None — nothing to compare against yet), the
    winning order's hold-out refit (_evaluate_order_on_holdout), and the production/live fit
    (_fit_sarimax_facility_forecast). One rule, checked the same way everywhere, instead of
    three copies drifting apart.

    Returns (ok, reason, diagnostics). diagnostics is populated whenever there's a real fit to
    describe (empty only when convergence itself already failed), so a caller can log what was
    actually true about a rejected fit, not just that it was rejected."""
    import numpy as np  # lazy — see the import comment near the top of this file

    if not (hasattr(fit, "mle_retvals") and fit.mle_retvals.get("converged", False)):
        return False, "not_converged", {}

    aic, bic = getattr(fit, "aic", float("nan")), getattr(fit, "bic", float("nan"))
    params = getattr(fit, "params", None)
    param_values = np.asarray(params, dtype=float) if params is not None else np.array([])
    if not (math.isfinite(aic) and math.isfinite(bic) and param_values.size and np.all(np.isfinite(param_values))):
        return False, "non_finite_fit", {}

    sigma2 = float(params.get("sigma2", float("nan"))) if hasattr(params, "get") else float("nan")
    if not math.isfinite(sigma2) or sigma2 <= 0:
        return False, "non_positive_sigma2", {"sigma2": sigma2}

    def root_moduli(roots) -> "np.ndarray":
        return np.abs(np.asarray(roots)) if roots is not None and len(roots) else np.array([])

    ar_roots, ma_roots = root_moduli(getattr(fit, "arroots", None)), root_moduli(getattr(fit, "maroots", None))
    if (ar_roots.size and not np.all(np.isfinite(ar_roots))) or (ma_roots.size and not np.all(np.isfinite(ma_roots))):
        return False, "non_finite_roots", {}

    aic_per_obs = aic / n_obs
    diagnostics = {
        "aic_per_obs": aic_per_obs,
        "min_ar_root": float(ar_roots.min()) if ar_roots.size else None,
        "min_ma_root": float(ma_roots.min()) if ma_roots.size else None,
    }
    if baseline_aic_per_obs is not None:
        delta = aic_per_obs - baseline_aic_per_obs
        diagnostics["aic_per_obs_delta_vs_baseline"] = delta
        if delta > SARIMAX_AIC_PER_OBS_DEGRADATION_THRESHOLD:
            return False, "aic_degraded_vs_baseline", diagnostics
    return True, "ok", diagnostics


# NOTE ON THE AI-RECOMMENDED ORDER (see _resolve_order). A (facility, blood type) with no
# grid-searched row gets an order recommended by a small decision tree over 13 features of
# its own daily series, exported as server/models/sarimax_selector_rules.json and walked
# with no scikit-learn. What is claimed: automation. A series-specific configuration costs a
# feature extraction and a tree walk, no model fit: median 7.7 ms per type at 155 days and
# 11.9 ms at 421 days. select_orders.py fits a 72-candidate grid per type: 166.7 s for the 8
# types of a 420-day series (one run). Both are local measurements; deployed timing is
# UNKNOWN. What is NOT claimed: that the recommended order forecasts better than the
# default. It was measured, on synthetic series only, and the result travels with the
# artifact (deploy_recommended, gate_reasons, limitations and the paired evaluation_summary
# in the JSON): the Stage 1 gate failed and the tree does not beat the default order there.
# The rules load regardless of deploy_recommended, deliberately. Any failure to produce a
# recommendation means the default order, never an error.
def _fit_sarimax_facility_forecast(
    daily_series: list[tuple[date, int]], today: date,
    order: tuple[int, int, int] = FACILITY_SARIMAX_ORDER,
    seasonal_order: tuple[int, int, int, int] = FACILITY_SARIMAX_SEASONAL_ORDER,
    model_label: str = FACILITY_SARIMAX_LABEL + ORDER_SOURCE_SUFFIX["fixed"],
    start_params: Optional[list[float]] = None,
    baseline_aic_per_obs: Optional[float] = None,
    log_prefix: str = "",
    observed_days: Optional[int] = None,
) -> Optional[dict]:
    """Fits `order`/`seasonal_order` (the fixed FACILITY_SARIMAX_ORDER/
    FACILITY_SARIMAX_SEASONAL_ORDER by default, or the order _resolve_order picked —
    a grid-searched row, an AI recommendation, or the fixed constant; see
    _fit_and_cache_sarimax, which is the only real caller) with a dengue-season exogenous
    regressor on one blood type's own resampled daily series (see
    _resample_daily_series), and forecasts max(FORECAST_CHECKPOINTS) days past
    `today`.

    Pure — no DB access — so it's directly unit-testable the same way
    _linear_trend and _prediction_interval_half_width are. The caller is
    responsible for resampling real history into `daily_series` first and
    for caching the result; this function always actually fits.

    `daily_series` must already extend through `today` (its last entry's
    date must equal `today`) — that's what makes checkpoint 0 well-defined.
    Checkpoint 0 uses today's real observed value directly rather than a
    model estimate: it's a known fact, not a forecast, so there's nothing to
    gain by substituting a fitted value the way the linear-trend path does.

    `start_params` — this (facility, blood_type)'s SELECTION fit's own converged
    params (facility_sarimax_order.start_params), when one has been selected — warm-
    starts the fit from the same basin the grid search already found, rather than
    statsmodels' generic default start. This is the PREVENT half of the Northside O+
    convergence-failure fix (see the module comment above
    SARIMAX_AIC_PER_OBS_DEGRADATION_THRESHOLD): refitting the SAME series from the
    default start can converge (mle_retvals.converged=True) at a materially worse,
    non-invertible optimum. None when there's no stored selection (neither the AI-recommended
    nor the fixed default order ever has one) — the fit then starts from
    statsmodels' own default, same as before this fix.

    `baseline_aic_per_obs` — that same selection fit's AIC per training observation
    (facility_sarimax_order.selection_aic_per_obs), passed to _sarimax_fit_is_sane so
    a fit that lands somewhere much worse than its own selection basin is caught even
    though mle_retvals reports it as converged. None skips that comparison (nothing to
    compare against) but the convergence/finite/root checks in _sarimax_fit_is_sane
    still run.

    RECOVER: if the fit (warm-started or not) fails _sarimax_fit_is_sane, this retries
    ONCE from a different start — Nelder-Mead (derivative-free, so it isn't caught by
    the same bad gradient basin) refined by one L-BFGS-B polish — before giving up.
    Still failing after that retry falls back to the linear trend, same as any other
    unusable fit here, with a one-line log so this is visible after the fact (see
    _SARIMAX_FIT_FAILED and the /forecast endpoint's per-type fallback).

    Returns None — never raises — whenever there isn't enough data to
    attempt a fit, the fit doesn't converge, it converges to a
    non-finite/degenerate forecast, or (after the retry above) it never lands
    somewhere trustworthy. The caller falls back to a linear trend for that blood
    type whenever this returns None; a bad fit here must never 500 the /forecast
    endpoint.
    """
    if len(daily_series) < SARIMAX_MIN_DAYS_REQUIRED or daily_series[-1][0] != today:
        return None

    import pandas as pd  # lazy — see the import comment near the top of this file
    from statsmodels.tsa.statespace.sarimax import SARIMAX

    dates = [d for d, _ in daily_series]
    values = [v for _, v in daily_series]
    idx = pd.DatetimeIndex(dates)
    endog = pd.Series(values, index=idx, dtype=float)
    exog = pd.DataFrame({"dengue_season": [dengue_season_index(d) for d in dates]}, index=idx)

    horizon = max(FORECAST_CHECKPOINTS)
    future_dates = [today + timedelta(days=i) for i in range(1, horizon + 1)]
    future_exog = pd.DataFrame(
        {"dengue_season": [dengue_season_index(d) for d in future_dates]},
        index=pd.DatetimeIndex(future_dates),
    )

    n_obs = len(daily_series)
    # What the log lines below say: which facility/type (the caller's log_prefix, e.g. "f5 O+"),
    # which order, and how much of the series was observed rather than filled in.
    tag = f"{log_prefix} {model_label}".strip()
    days = f"{observed_days} observed / {n_obs} modeled days" if observed_days is not None else f"{n_obs} modeled days"

    def verdict(reason: str, diag: dict) -> str:
        delta = diag.get("aic_per_obs_delta_vs_baseline")
        return reason if delta is None else f"{reason}, aic/obs delta {delta:+.4f} vs limit {SARIMAX_AIC_PER_OBS_DEGRADATION_THRESHOLD}"

    def fit_checked(model, **fit_kwargs) -> tuple[Optional[object], str, dict]:
        """Fits with `fit_kwargs` and returns (fit, reason, diagnostics), fit only if it's sane
        per _sarimax_fit_is_sane; None (never raises) on any failure, exactly like the
        rest of this function's contract."""
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                candidate = model.fit(disp=False, maxiter=FACILITY_SARIMAX_MAXITER, **fit_kwargs)
        except Exception as exc:
            return None, f"fit_raised_{type(exc).__name__}", {}
        ok, reason, diag = _sarimax_fit_is_sane(candidate, n_obs, baseline_aic_per_obs)
        return (candidate if ok else None), reason, diag

    try:
        model = SARIMAX(
            endog, exog=exog,
            order=order, seasonal_order=seasonal_order,
            trend=None, enforce_stationarity=False, enforce_invertibility=False,
        )
    except Exception:
        return None

    fit, reason, diag = fit_checked(model, start_params=start_params) if start_params is not None else fit_checked(model)
    if fit is None:
        print(f"[sarimax] {tag} primary fit unsound ({verdict(reason, diag)}) on {days}; retrying nm->lbfgs")
        # nm is only relocating the start point for the lbfgs polish below, not itself
        # the fit that gets judged — Nelder-Mead frequently reports its OWN
        # converged=False at this maxiter while still landing somewhere lbfgs can
        # polish into a sound fit (see the module comment above
        # SARIMAX_AIC_PER_OBS_DEGRADATION_THRESHOLD): gating on nm's own convergence
        # here would throw away exactly the recovery this retry exists for. Only
        # guard against nm producing unusable (non-finite) params to start lbfgs from.
        fit_nm = None
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                candidate_nm = model.fit(disp=False, maxiter=FACILITY_SARIMAX_MAXITER, method="nm")
            if all(math.isfinite(v) for v in candidate_nm.params):
                fit_nm = candidate_nm
        except Exception:
            pass
        fit, reason, diag = (
            fit_checked(model, start_params=fit_nm.params.values, method="lbfgs") if fit_nm is not None
            else (None, "nm_params_non_finite", {})
        )
        if fit is None:
            print(f"[sarimax] {tag} retry also unsound ({verdict(reason, diag)}) on {days}; falling back to linear trend")
            return None
        # A rescued type is not a failed one: say so, or the two read the same in the logs.
        print(f"[sarimax] {tag} retry sound ({verdict(reason, diag)}) on {days}")

    try:
        forecast_res = fit.get_forecast(steps=horizon, exog=future_exog)
        point = forecast_res.predicted_mean
        ci = forecast_res.conf_int(alpha=1 - FORECAST_INTERVAL_CONFIDENCE)
    except Exception:
        # Any statsmodels failure (a singular/rank-deficient fit, a linear-algebra
        # error on a pathological series, ...) falls back to the linear trend for
        # this blood type rather than ever reaching the caller as a 500.
        return None

    lower_col, upper_col = ci.columns[0], ci.columns[1]
    checkpoints: dict[int, dict[str, int]] = {
        0: {"units": int(values[-1]), "lower": int(values[-1]), "upper": int(values[-1])}
    }
    for i, forecast_date in enumerate(future_dates):
        offset = i + 1
        if offset not in FORECAST_CHECKPOINTS:
            continue
        est, lo, hi = float(point.iloc[i]), float(ci[lower_col].iloc[i]), float(ci[upper_col].iloc[i])
        if not (math.isfinite(est) and math.isfinite(lo) and math.isfinite(hi)):
            return None
        checkpoints[offset] = {"units": max(0, round(est)), "lower": max(0, round(lo)), "upper": max(0, round(hi))}

    if set(checkpoints) != set(FORECAST_CHECKPOINTS):
        return None

    return {"checkpoints": checkpoints, "exog_included": True, "model_order": model_label}


def _order_label(p: int, d: int, q: int, sp: int, sd: int, sq: int, ss: int, *, source: str) -> str:
    """"SARIMAX(0,1,4)x(1,0,1,7)+dengue" plus ORDER_SOURCE_SUFFIX[source] ("grid", "ai" or
    "fixed"). Every label names the mechanism that produced the order, so a stored or
    reported forecast can always be traced to it; none of them says anything about how
    good the order is."""
    return f"SARIMAX({p},{d},{q})x({sp},{sd},{sq},{ss})+dengue{ORDER_SOURCE_SUFFIX[source]}"


def _normalize_stored_order_label(label: str) -> str:
    """Reads a facility_forecast_cache.model_order written by any version of the app
    into today's spelling. Rows from before the three-way labels have no suffix (that
    was a grid-searched order) or the legacy " [fallback]" (the fixed constant);
    everything else already carries one of ORDER_SOURCE_SUFFIX."""
    if label.endswith(_LEGACY_FIXED_SUFFIX):
        return label[: -len(_LEGACY_FIXED_SUFFIX)] + ORDER_SOURCE_SUFFIX["fixed"]
    if label.endswith(tuple(ORDER_SOURCE_SUFFIX.values())):
        return label
    return label + ORDER_SOURCE_SUFFIX["grid"]


def _get_selected_order(conn, facility_id: int, blood_type: str) -> Optional[dict]:
    """The stored per-type SARIMAX order from facility_sarimax_order, or None if
    none has been selected yet for this (facility, blood_type) — _resolve_order
    then tries the AI recommendation, then FACILITY_SARIMAX_ORDER.

    start_params/selection_aic_per_obs are both None on a row selected before
    those columns existed — the live fitter then falls back to statsmodels' own
    default start with no baseline to check the fit's AIC against (same as
    before this pair of columns was added), until the next re-selection fills
    them in."""
    row = conn.execute(
        text(
            """
            SELECT p, d, q, seasonal_p, seasonal_d, seasonal_q, seasonal_s, criterion_satisfied,
                   start_params, selection_aic_per_obs
            FROM facility_sarimax_order WHERE facility_id = :facility_id AND blood_type = :blood_type
            """
        ),
        {"facility_id": facility_id, "blood_type": blood_type},
    ).mappings().first()
    return dict(row) if row else None


def _cached_from_rows(cached_rows, daily_series: list[tuple[date, int]], today: date) -> Optional[dict]:
    """The cache-hit answer for one type from its facility_forecast_cache rows, or None unless every checkpoint is present and was
    trained today. The one place the freshness rule and the live day-0 value live, shared by the one-type and the batched read."""
    checkpoint_dates = [today + timedelta(days=c) for c in FORECAST_CHECKPOINTS]
    by_date = {row["forecast_date"]: row for row in cached_rows}
    if len(by_date) != len(checkpoint_dates) or any(row["trained_through_date"] != today for row in cached_rows):
        return None
    checkpoints = {
        c: {
            "units": int(by_date[cd]["forecast_units"]),
            "lower": int(by_date[cd]["lower_units"]),
            "upper": int(by_date[cd]["upper_units"]),
        }
        for c, cd in zip(FORECAST_CHECKPOINTS, checkpoint_dates)
    }
    live_today = int(daily_series[-1][1])
    checkpoints[0] = {"units": live_today, "lower": live_today, "upper": live_today}
    # The stored label (grid-searched, AI-recommended or default-order?), not a hardcoded
    # constant — a cache hit must report whichever order actually produced it.
    model_order = _normalize_stored_order_label(next(iter(by_date.values()))["model_order"])
    return {"checkpoints": checkpoints, "exog_included": True, "model_order": model_order}


_CACHE_ROWS_SQL = """
    SELECT blood_type, forecast_date, forecast_units, lower_units, upper_units, trained_through_date, model_order
    FROM facility_forecast_cache
    WHERE facility_id = :facility_id AND blood_type = ANY(:blood_types) AND forecast_date = ANY(:dates)
"""


def _read_cached_sarimax_many(
    conn, facility_id: int, series_by_type: dict[str, list[tuple[date, int]]], today: date
) -> dict[str, Optional[dict]]:
    """_read_cached_sarimax for several types in ONE statement (a warm GET /forecast used to send one per type, a round trip each).
    {blood_type: cached answer, or None}: the same answers, from the same rule (_cached_from_rows)."""
    if not series_by_type:
        return {}
    rows = conn.execute(
        text(_CACHE_ROWS_SQL),
        {"facility_id": facility_id, "blood_types": list(series_by_type),
         "dates": [today + timedelta(days=c) for c in FORECAST_CHECKPOINTS]},
    ).mappings().all()
    rows_by_type: dict[str, list] = {}
    for row in rows:
        rows_by_type.setdefault(row["blood_type"], []).append(row)
    return {bt: _cached_from_rows(rows_by_type.get(bt, []), series, today) for bt, series in series_by_type.items()}


def _read_cached_sarimax(
    conn, facility_id: int, blood_type: str, daily_series: list[tuple[date, int]], today: date
) -> Optional[dict]:
    """This facility+type's cached checkpoints if every one is present AND was
    trained through `today` (trained_through_date == today), else None. Freshness
    is only "trained today"; anything that changes the snapshots behind a cache
    (upload, undo) drops the rows via _invalidate_facility_forecast_cache.

    Day 0 is just today's stock (the same value a fresh fit stores there), so a
    hit reports it live from the series: stock that changed since the fit shows up
    immediately with no refit. Days 1-30 stay as fitted this morning."""
    return _read_cached_sarimax_many(conn, facility_id, {blood_type: daily_series}, today)[blood_type]


def _ai_recommended_order(values) -> Optional[tuple[tuple[int, int, int], tuple[int, int, int, int]]]:
    """The AI-recommended (order, seasonal_order) for this daily series, or None for ANY
    reason it can't produce one: rules file missing/invalid, a non-finite feature (e.g.
    a constant or all-zero series), a statsmodels error. Never raises — the caller uses
    the default order. Milliseconds, no fit, so it doesn't count against the fit budget."""
    try:
        # lazy, like pandas/statsmodels above: this module imports statsmodels, a startup cost
        import sarimax_selector_common as selector

        rules = selector.load_selector_rules()
        if rules is None:
            return None
        return selector.recommend_order_from_series(values, rules)
    except Exception as exc:
        print(f"[sarimax] AI order unavailable ({type(exc).__name__}: {exc}); using the default order")
        return None


def _weekday_pattern(daily_series: list[tuple[date, int]]) -> Optional[tuple[float, float]]:
    """(eta^2, p) of a one-way ANOVA of the series' day-to-day changes grouped by weekday: whether
    the weekly rhythm is real (p) and how much of the variance in daily changes it explains (eta^2).
    None whenever the test can't be run (fewer than two weekdays with data, no variance, non-finite)."""
    import numpy as np  # lazy — see the import comment near the top of this file
    from scipy.stats import f_oneway

    try:
        changes = np.diff(np.array([v for _, v in daily_series], dtype=float))
        weekdays = np.array([d.weekday() for d, _ in daily_series[1:]])
        groups = [changes[weekdays == k] for k in range(7) if (weekdays == k).sum() >= 2]
        if len(groups) < 2:
            return None
        used = np.concatenate(groups)
        ss_total = float(np.sum((used - used.mean()) ** 2))
        p = float(f_oneway(*groups).pvalue)
        if not (ss_total > 0 and math.isfinite(p)):
            return None
        return float(sum(len(g) * (g.mean() - used.mean()) ** 2 for g in groups) / ss_total), p
    except Exception:
        return None


def _resolve_order(facility_id: int, blood_type: str, daily_series: list[tuple[date, int]]) -> dict:
    """Which order this (facility, blood_type) is fitted with, by precedence: a
    grid-searched row in facility_sarimax_order, else the AI recommendation computed
    from this series right now, else the fixed FACILITY_SARIMAX_ORDER. Returns
    order, seasonal_order, label (carrying the source), start_params and
    baseline_aic_per_obs (only a grid row has those two).

    The AI recommendation is not stored: it can change as the series grows, and each
    forecast's label (facility_forecast_cache.model_order) records what produced it.
    One rule, stated in a sentence: an AI order with no seasonal term is refused (and logged)
    when a one-way ANOVA of the series' daily changes by weekday is significant at 0.05."""
    with engine.connect() as conn:
        selected = _get_selected_order(conn, facility_id, blood_type)
    if selected:
        order = (selected["p"], selected["d"], selected["q"])
        seasonal_order = (selected["seasonal_p"], selected["seasonal_d"], selected["seasonal_q"], selected["seasonal_s"])
        source, start_params, baseline = "grid", selected["start_params"], selected["selection_aic_per_obs"]
    else:
        start_params = baseline = None
        recommended = _ai_recommended_order([v for _, v in daily_series])
        # Guard (narrow on purpose): an AI order with no seasonal term is refused when a weekday
        # ANOVA on the series' daily changes is significant at WEEKDAY_PATTERN_ALPHA, because it
        # would discard a weekly rhythm the series has. Backtested on real series (facility 158,
        # 31 rolling origins): that order's RMSE was 1.18x the default's. A seasonal AI order was
        # not backtested and passes through.
        if recommended is not None and not any(recommended[1][:3]):
            pattern = _weekday_pattern(daily_series)
            if pattern is not None and pattern[1] < WEEKDAY_PATTERN_ALPHA:
                (p, d, q), (sp, sd, sq, ss) = recommended
                p_text = "p < 0.001" if pattern[1] < 0.001 else f"p = {pattern[1]:.3f}"
                print(f"[sarimax] f{facility_id} {blood_type} ai order ({p},{d},{q})x({sp},{sd},{sq},{ss}) declined: "
                      f"series has a weekday pattern (eta^2 {pattern[0]:.2f}, {p_text}) the order ignores; using default")
                recommended = None
        if recommended is not None:
            (order, seasonal_order), source = recommended, "ai"
        else:
            order, seasonal_order, source = FACILITY_SARIMAX_ORDER, FACILITY_SARIMAX_SEASONAL_ORDER, "fixed"
    return {
        "order": order, "seasonal_order": seasonal_order, "source": source,
        "label": _order_label(*order, *seasonal_order, source=source),
        "start_params": start_params, "baseline_aic_per_obs": baseline,
    }


def _fit_and_cache_sarimax(
    facility_id: int, blood_type: str, daily_series: list[tuple[date, int]], today: date,
    observed_dates: Optional[set[date]] = None,
) -> Optional[dict]:
    """Fits one type (~12-20 s on the deployed instance) and commits its
    checkpoint rows in their OWN short transaction, so a request that is killed
    partway keeps every type it already finished. The fit itself holds no DB
    connection. The write is an upsert, so a concurrent writer can never raise
    IntegrityError. Returns None if the fit is unusable (caller falls back to the
    linear trend for this type — there is no retry with another order, which would
    add a second fit to the per-request budget).

    The order comes from _resolve_order (grid-searched, else AI-recommended, else the
    fixed default); the label stored with the rows names which."""
    resolved = _resolve_order(facility_id, blood_type, daily_series)
    order, seasonal_order, label = resolved["order"], resolved["seasonal_order"], resolved["label"]
    start_params, baseline_aic_per_obs = resolved["start_params"], resolved["baseline_aic_per_obs"]
    result = _fit_sarimax_facility_forecast(
        daily_series, today, order=order, seasonal_order=seasonal_order, model_label=label,
        start_params=start_params, baseline_aic_per_obs=baseline_aic_per_obs,
        log_prefix=f"f{facility_id} {blood_type}",
        observed_days=None if observed_dates is None else _count_observed_days(daily_series, observed_dates),
    )
    if result is None:
        _SARIMAX_FIT_FAILED.add((facility_id, blood_type, today))
        return None

    dates = [today + timedelta(days=c) for c in FORECAST_CHECKPOINTS]
    with engine.begin() as conn:
        # yesterday's checkpoint dates are stale rows the upsert below wouldn't overwrite
        conn.execute(
            text(
                "DELETE FROM facility_forecast_cache "
                "WHERE facility_id = :facility_id AND blood_type = :blood_type AND forecast_date <> ALL(:dates)"
            ),
            {"facility_id": facility_id, "blood_type": blood_type, "dates": dates},
        )
        conn.execute(
            text(
                """
                INSERT INTO facility_forecast_cache
                    (facility_id, blood_type, forecast_date, forecast_units, lower_units, upper_units, trained_through_date, model_order)
                VALUES
                    (:facility_id, :blood_type, :forecast_date, :forecast_units, :lower_units, :upper_units, :trained_through_date, :model_order)
                ON CONFLICT (facility_id, blood_type, forecast_date) DO UPDATE SET
                    forecast_units = EXCLUDED.forecast_units,
                    lower_units = EXCLUDED.lower_units,
                    upper_units = EXCLUDED.upper_units,
                    trained_through_date = EXCLUDED.trained_through_date,
                    model_order = EXCLUDED.model_order,
                    generated_at = now()
                """
            ),
            [
                {
                    "facility_id": facility_id,
                    "blood_type": blood_type,
                    "forecast_date": today + timedelta(days=c),
                    "forecast_units": result["checkpoints"][c]["units"],
                    "lower_units": result["checkpoints"][c]["lower"],
                    "upper_units": result["checkpoints"][c]["upper"],
                    "trained_through_date": today,
                    "model_order": result["model_order"],
                }
                for c in FORECAST_CHECKPOINTS
            ],
        )
    return result


def _collect_sarimax_results(
    facility_id: int, daily_by_type: dict[str, list[tuple[date, int]]], today: date,
    observed_dates: Optional[set[date]] = None, prefetched_cache: Optional[dict[str, Optional[dict]]] = None,
) -> tuple[dict[str, Optional[dict]], list[str]]:
    """Returns ({type: result-or-None}, pending_types). A type with a result is
    ready; one mapped to None had an unusable fit (caller uses the linear trend);
    a pending type has neither yet.

    Cached types cost one read. Missing types are fitted only by the request that
    holds this facility's advisory lock (pg_try_advisory_lock: never waits), and at
    most MAX_SARIMAX_FITS_PER_REQUEST per request. A request that can't get the lock
    means another request is already fitting for this facility: it fits nothing and
    just reports what is cached and what is still pending."""
    results: dict[str, Optional[dict]] = {}
    missing: list[str] = []
    fittable: dict[str, list[tuple[date, int]]] = {}
    for blood_type, series in daily_by_type.items():
        if len(series) < SARIMAX_MIN_DAYS_REQUIRED:
            # Can't be fitted yet (the fitter returns None below this), so it must not spend one of
            # the MAX_SARIMAX_FITS_PER_REQUEST slots or sit in pending_types. Happens once every type
            # is snapshotted daily: a type with no stock history starts as a one-day series of 0.
            results[blood_type] = None
        else:
            fittable[blood_type] = series
    if prefetched_cache is not None:
        cached_by_type = prefetched_cache  # already read, in the caller's transaction (see get_forecast)
    else:
        with engine.connect() as conn:
            cached_by_type = _read_cached_sarimax_many(conn, facility_id, fittable, today)  # one read for every type
    for blood_type in fittable:
        cached = cached_by_type[blood_type]
        if cached is not None:
            results[blood_type] = cached
        elif (facility_id, blood_type, today) in _SARIMAX_FIT_FAILED:
            results[blood_type] = None
        else:
            missing.append(blood_type)
    if not missing:
        return results, []

    lock_conn = engine.connect()
    try:
        if not lock_conn.execute(
            text("SELECT pg_try_advisory_lock(:ns, :fid)"), {"ns": _FORECAST_LOCK_NAMESPACE, "fid": facility_id}
        ).scalar():
            return results, missing
        try:
            # another request may have finished some of these while we were reading
            with engine.connect() as conn:
                now_cached = _read_cached_sarimax_many(conn, facility_id, {bt: daily_by_type[bt] for bt in missing}, today)
            for blood_type, cached in now_cached.items():
                if cached is not None:
                    results[blood_type] = cached
                    missing.remove(blood_type)
            for blood_type in list(missing)[:MAX_SARIMAX_FITS_PER_REQUEST]:
                results[blood_type] = _fit_and_cache_sarimax(
                    facility_id, blood_type, daily_by_type[blood_type], today, observed_dates=observed_dates
                )
                missing.remove(blood_type)
        finally:
            lock_conn.execute(
                text("SELECT pg_advisory_unlock(:ns, :fid)"), {"ns": _FORECAST_LOCK_NAMESPACE, "fid": facility_id}
            )
    finally:
        lock_conn.close()
    return results, missing


def _fit_candidate_order(y: "pd.Series", exog: "pd.DataFrame", p: int, d: int, q: int, sp: int, sq: int) -> Optional[dict]:
    """One candidate in the order-selection grid: fits SARIMAX(p,d,q)x(sp,0,sq,7)
    with the dengue exog and the same fit settings as the live fitter, and
    returns its diagnostics, or None if it doesn't converge OR fails
    _sarimax_fit_is_sane's finite/root/sigma2 guard (baseline_aic_per_obs=None here —
    there's no prior fit of THIS series to compare against yet; that comparison is
    the production/live fit's job, against what this function returns). Real work —
    not pure — but isolated so _rank_candidates_and_select (the actual selection
    RULE) can be unit-tested on fabricated candidate dicts without fitting
    anything."""
    import numpy as np  # lazy — see the import comment near the top of this file
    from scipy.stats import chi2
    from statsmodels.stats.diagnostic import acorr_ljungbox
    from statsmodels.tsa.statespace.sarimax import SARIMAX

    order, seasonal_order = (p, d, q), (sp, 0, sq, 7)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            fit = SARIMAX(
                y, exog=exog, order=order, seasonal_order=seasonal_order,
                trend=None, enforce_stationarity=False, enforce_invertibility=False,
            ).fit(disp=False, maxiter=FACILITY_SARIMAX_MAXITER)
        ok, _reason, _diag = _sarimax_fit_is_sane(fit, len(y), None)
        if not ok:
            return None
    except Exception:
        return None

    burn = max(int(getattr(fit, "loglikelihood_burn", 0)), 1)
    resid = np.asarray(fit.standardized_forecasts_error[0])[burn:]
    resid = resid[np.isfinite(resid)]
    K = p + q + sp + sq  # this candidate's own ARMA parameter count, for the df-correction below
    lb_p: dict[int, Optional[float]] = {}
    for lag in SARIMAX_ORDER_GRID_LAGS:
        dfree = lag - K
        if dfree <= 0 or len(resid) <= lag:
            lb_p[lag] = None
            continue
        uncorrected_p = float(acorr_ljungbox(resid, lags=[lag], model_df=0)["lb_pvalue"].iloc[0])
        stat = chi2.isf(uncorrected_p, lag)
        lb_p[lag] = float(chi2.sf(stat, dfree))

    return {
        "p": p, "d": d, "q": q, "seasonal_p": sp, "seasonal_d": 0, "seasonal_q": sq, "seasonal_s": 7,
        "aic": float(fit.aic), "lb_p": lb_p,
        # This candidate's OWN converged params/AIC-per-obs — carried through by
        # _rank_candidates_and_select's {**winner, ...} spread untouched, so the
        # winning candidate's basin is available to warm-start the production/live
        # fit on the full series (see _fit_sarimax_facility_forecast's start_params).
        "start_params": fit.params.tolist(),
        "aic_per_obs": float(fit.aic) / len(y),
    }


def _rank_candidates_and_select(candidates: list[dict]) -> Optional[dict]:
    """The selection RULE, pure (no fitting): among `candidates` that CONVERGED
    (already filtered to non-None by the caller), pick the lowest-AIC one whose
    Ljung-Box p-value is > 0.05 at EVERY lag in SARIMAX_ORDER_GRID_LAGS (14, 21
    and 28) — see the module comment above SARIMAX_ORDER_GRID_PQ for why all
    three, not any one. If none satisfies that, falls back to the lowest-AIC
    candidate overall, with criterion_satisfied=False. Returns None only if
    `candidates` is empty (nothing converged)."""
    if not candidates:
        return None

    def satisfies(c: dict) -> bool:
        return all(v is not None and v > 0.05 for v in c["lb_p"].values())

    passers = [c for c in candidates if satisfies(c)]
    pool, criterion_satisfied = (passers, True) if passers else (candidates, False)
    winner = min(pool, key=lambda c: c["aic"])
    return {**winner, "criterion_satisfied": criterion_satisfied, "n_candidates_converged": len(candidates)}


def _select_sarimax_order_for_series(y: "pd.Series", exog: "pd.DataFrame") -> Optional[dict]:
    """Runs the full 72-candidate grid (SARIMAX_ORDER_GRID_PQ x
    SARIMAX_ORDER_GRID_PQ x SARIMAX_ORDER_GRID_D x SARIMAX_ORDER_GRID_SEASONAL_PQ
    x SARIMAX_ORDER_GRID_SEASONAL_PQ) against `y`/`exog` and applies the
    selection rule. ~18-21 s per type locally (72 fits; 166.7 s for the 8 types of a
    420-day series, one run); deployed timing UNKNOWN (server/select_orders.py is the intended way to run
    this against production data — see its module docstring for why). Whatever
    `y` is gets fit directly: the TRAINING/hold-out split is the caller's job
    (see _run_order_selection_for_facility, the only caller) — this function
    has no opinion on which slice of a series it's given."""
    candidates = [
        r for p in SARIMAX_ORDER_GRID_PQ for q in SARIMAX_ORDER_GRID_PQ for d in SARIMAX_ORDER_GRID_D
        for sp in SARIMAX_ORDER_GRID_SEASONAL_PQ for sq in SARIMAX_ORDER_GRID_SEASONAL_PQ
        if (r := _fit_candidate_order(y, exog, p, d, q, sp, sq)) is not None
    ]
    return _rank_candidates_and_select(candidates)


def _evaluate_order_on_holdout(
    y_train: "pd.Series", exog_train: "pd.DataFrame", y_holdout: "pd.Series", exog_holdout: "pd.DataFrame",
    order: tuple[int, int, int], seasonal_order: tuple[int, int, int, int],
    start_params: Optional[list[float]] = None, baseline_aic_per_obs: Optional[float] = None,
) -> Optional[dict]:
    """Refits the WINNING order on the training window alone (a single extra
    fit — the 72 candidates in the grid are diagnosed on their own training
    residuals and discarded, not kept around) and forecasts
    ORDER_SELECTION_HOLDOUT_DAYS days into `y_holdout`, which the selection
    never saw. Returns MAPE/RMSE for this order and for a naive
    last-training-value-repeated baseline over the identical window (the valid
    out-of-sample accuracy evidence), plus a Ljung-Box independence check on
    ONE-STEP-AHEAD hold-out errors (see below) — or None for that check alone
    if it doesn't come out finite; None for the whole return if the training
    refit doesn't converge OR fails _sarimax_fit_is_sane.

    `start_params`/`baseline_aic_per_obs` are this same order's winning candidate
    fit from the grid search (_fit_candidate_order, on this EXACT y_train/exog_train)
    — warm-starting here isn't just the general PREVENT fix, it's fitting the
    identical data twice and has no excuse to land anywhere else. baseline_aic_per_obs
    then makes that self-consistency an assertion, not an assumption: if this refit's
    AIC/observation drifts from the grid search's own winning fit, that's caught
    the same way a production/live-fit divergence would be.

    NOT tested here: Ljung-Box on the STATIC (fixed-origin, multi-step)
    forecast errors used for MAPE/RMSE. That would be invalid — h-step-ahead
    errors from one fixed origin share the same underlying innovations
    propagated forward through the ARMA structure and are autocorrelated BY
    CONSTRUCTION even for a correctly specified model, and n=30 could not
    support lags 14/21/28 regardless. Instead, `fit.append(y_holdout,
    exog=exog_holdout, refit=False)` extends the fitted state through the
    hold-out WITHOUT re-estimating parameters, giving 30 genuine one-step-
    ahead standardized residuals (each conditioned on all data up to the
    point before it) — exactly what Ljung-Box needs. Uncorrected (df=lag):
    nothing was estimated on this data, so no parameter-count correction
    applies."""
    import numpy as np  # lazy — see the import comment near the top of this file
    from statsmodels.stats.diagnostic import acorr_ljungbox
    from statsmodels.tsa.statespace.sarimax import SARIMAX

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            fit_kwargs = {"start_params": start_params} if start_params is not None else {}
            fit = SARIMAX(
                y_train, exog=exog_train, order=order, seasonal_order=seasonal_order,
                trend=None, enforce_stationarity=False, enforce_invertibility=False,
            ).fit(disp=False, maxiter=FACILITY_SARIMAX_MAXITER, **fit_kwargs)
        ok, _reason, _diag = _sarimax_fit_is_sane(fit, len(y_train), baseline_aic_per_obs)
        if not ok:
            return None
        point = fit.get_forecast(steps=len(y_holdout), exog=exog_holdout).predicted_mean
    except Exception:
        return None

    actual = y_holdout.to_numpy()
    forecast_errors = actual - point.to_numpy()
    baseline_forecast = np.full(len(actual), y_train.iloc[-1])
    baseline_errors = actual - baseline_forecast

    def mape(errors: "np.ndarray") -> Optional[float]:
        # Days with actual == 0 would divide by zero; excluded rather than
        # inflating or crashing the metric (same convention run_methodology.py
        # would use if it ever hit a zero-count day, which this data hasn't).
        nonzero = actual != 0
        if not nonzero.any():
            return None
        return float(np.mean(np.abs(errors[nonzero]) / actual[nonzero]) * 100)

    def rmse(errors: "np.ndarray") -> float:
        return float(np.sqrt(np.mean(errors ** 2)))

    lb_p_onestep: dict[int, Optional[float]] = {lag: None for lag in SARIMAX_ORDER_GRID_LAGS}
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            extended = fit.append(y_holdout, exog=exog_holdout, refit=False)
        onestep = np.asarray(extended.standardized_forecasts_error[0])[-len(y_holdout):]
        onestep = onestep[np.isfinite(onestep)]
        for lag in SARIMAX_ORDER_GRID_LAGS:
            if len(onestep) > lag:
                lb_p_onestep[lag] = float(acorr_ljungbox(onestep, lags=[lag], model_df=0)["lb_pvalue"].iloc[0])
    except Exception:
        pass  # the accuracy metrics below still stand on their own; this check is best-effort

    return {
        "lb_p_onestep": lb_p_onestep, "mape": mape(forecast_errors), "rmse": rmse(forecast_errors),
        "baseline_mape": mape(baseline_errors), "baseline_rmse": rmse(baseline_errors),
    }


def _run_order_selection_for_facility(facility_id: int, max_types: Optional[int] = None) -> None:
    """Offline order selection for one facility's blood types: trains on the
    series minus the last ORDER_SELECTION_HOLDOUT_DAYS days, selects by AIC +
    in-sample Ljung-Box on that training window ALONE, then evaluates the
    winner on the held-out days it never saw (see _evaluate_order_on_holdout).
    Never called from a request.

    Two callers, two `max_types` values:
      - server/select_orders.py, the PRIMARY path — run manually against the
        shared database from a real machine, so Render's CPU budget never
        enters into it at all. Passes max_types=None: every eligible type,
        every run.
      - upload_historical_inventory_snapshots's BackgroundTasks trigger, which is OFF unless AUTO_ORDER_SELECTION_AFTER_UPLOAD=true
        (see _auto_order_selection_enabled for why) —
        passes MAX_ORDER_SELECTIONS_PER_BACKGROUND_RUN (2), so an unattended
        facility (nobody ever running the script for it) still makes bounded
        progress after each upload instead of one very long background task.
        Types with NO existing selection are processed before types being
        re-selected, so first coverage of all 8 comes before any refresh of
        an already-selected type — an unattended facility converges to "every
        type has SOME selection" within ceil(8 / 2) = 4 uploads, then starts
        refreshing older selections 2 at a time on further uploads.

    Reuses the forecast path's pattern: a SEPARATE pg_try_advisory_lock
    (_ORDER_SELECTION_LOCK_NAMESPACE, never blocking — if another run is already
    in progress for this facility, e.g. two uploads in quick succession, this
    call simply does nothing and returns) and a commit PER TYPE, so a run that
    is interrupted (deploy restart, the instance sleeping) keeps every type it
    already finished; the remaining types stay on whatever they had before
    (a prior selection, or the fixed-order fallback) until the next trigger.
    There is no separate resumption path — this function is the only place
    selection ever runs, so "resumable" here means exactly that: partial
    progress survives, the next trigger continues.

    A type with fewer than ORDER_SELECTION_MIN_DAYS days of history, whose
    72-candidate training grid converges nowhere, or whose winning order fails
    to refit on the hold-out, is left as it was (no row written) rather than
    storing a meaningless or partial selection."""
    lock_conn = engine.connect()
    try:
        if not lock_conn.execute(
            text("SELECT pg_try_advisory_lock(:ns, :fid)"), {"ns": _ORDER_SELECTION_LOCK_NAMESPACE, "fid": facility_id}
        ).scalar():
            return
        try:
            import pandas as pd  # lazy — see the import comment near the top of this file

            today = business_today()
            with engine.connect() as conn:
                history_rows = conn.execute(
                    text("SELECT snapshot_date, blood_type, units FROM inventory_snapshots WHERE facility_id = :facility_id"),
                    {"facility_id": facility_id},
                ).mappings().all()
                already_selected = set(conn.execute(
                    text("SELECT blood_type FROM facility_sarimax_order WHERE facility_id = :facility_id"),
                    {"facility_id": facility_id},
                ).scalars())
            if not history_rows:
                return
            observed_dates = {row["snapshot_date"] for row in history_rows}
            raw_units_by_type: dict[str, dict[date, int]] = {}
            for row in history_rows:
                raw_units_by_type.setdefault(row["blood_type"], {})[row["snapshot_date"]] = row["units"]

            # Never-selected types first (see the docstring above), then a
            # deterministic order within each group.
            blood_types = sorted(raw_units_by_type, key=lambda bt: (bt in already_selected, bt))
            if max_types is not None:
                blood_types = blood_types[:max_types]

            for blood_type in blood_types:
                daily_series = _resample_daily_series(raw_units_by_type[blood_type], observed_dates, today)
                if len(daily_series) < ORDER_SELECTION_MIN_DAYS or daily_series[-1][0] != today:
                    continue
                train_series, holdout_series = daily_series[:-ORDER_SELECTION_HOLDOUT_DAYS], daily_series[-ORDER_SELECTION_HOLDOUT_DAYS:]

                def build(series: list[tuple[date, int]]) -> tuple["pd.Series", "pd.DataFrame"]:
                    d = [x for x, _ in series]
                    idx = pd.DatetimeIndex(d)
                    return (pd.Series([v for _, v in series], index=idx, dtype=float),
                            pd.DataFrame({"dengue_season": [dengue_season_index(x) for x in d]}, index=idx))

                y_train, exog_train = build(train_series)
                y_holdout, exog_holdout = build(holdout_series)

                selection = _select_sarimax_order_for_series(y_train, exog_train)
                if selection is None:
                    continue  # nothing in the grid converged on the training window; leave it on whatever it had
                order = (selection["p"], selection["d"], selection["q"])
                seasonal_order = (selection["seasonal_p"], selection["seasonal_d"], selection["seasonal_q"], selection["seasonal_s"])
                holdout = _evaluate_order_on_holdout(
                    y_train, exog_train, y_holdout, exog_holdout, order, seasonal_order,
                    start_params=selection["start_params"], baseline_aic_per_obs=selection["aic_per_obs"],
                )
                if holdout is None:
                    # the winning order fits during selection but not on the hold-out;
                    # extremely unlikely, but skip rather than guess
                    continue

                with engine.begin() as conn:
                    conn.execute(
                        text(
                            """
                            INSERT INTO facility_sarimax_order
                                (facility_id, blood_type, p, d, q, seasonal_p, seasonal_d, seasonal_q, seasonal_s,
                                 aic, lb_p_14_in_sample, lb_p_21_in_sample, lb_p_28_in_sample,
                                 criterion_satisfied, n_candidates_converged,
                                 lb_p_14_holdout_onestep, lb_p_21_holdout_onestep, lb_p_28_holdout_onestep,
                                 holdout_mape, holdout_rmse, holdout_baseline_mape, holdout_baseline_rmse,
                                 start_params, selection_aic_per_obs,
                                 trained_through_date, evaluated_through_date, selected_at)
                            VALUES
                                (:facility_id, :blood_type, :p, :d, :q, :seasonal_p, :seasonal_d, :seasonal_q, :seasonal_s,
                                 :aic, :lb_p_14_in_sample, :lb_p_21_in_sample, :lb_p_28_in_sample,
                                 :criterion_satisfied, :n_candidates_converged,
                                 :lb_p_14_holdout_onestep, :lb_p_21_holdout_onestep, :lb_p_28_holdout_onestep,
                                 :holdout_mape, :holdout_rmse, :holdout_baseline_mape, :holdout_baseline_rmse,
                                 CAST(:start_params AS jsonb), :selection_aic_per_obs,
                                 :trained_through_date, :evaluated_through_date, now())
                            ON CONFLICT (facility_id, blood_type) DO UPDATE SET
                                p = EXCLUDED.p, d = EXCLUDED.d, q = EXCLUDED.q,
                                seasonal_p = EXCLUDED.seasonal_p, seasonal_d = EXCLUDED.seasonal_d, seasonal_q = EXCLUDED.seasonal_q,
                                seasonal_s = EXCLUDED.seasonal_s, aic = EXCLUDED.aic,
                                lb_p_14_in_sample = EXCLUDED.lb_p_14_in_sample, lb_p_21_in_sample = EXCLUDED.lb_p_21_in_sample,
                                lb_p_28_in_sample = EXCLUDED.lb_p_28_in_sample,
                                criterion_satisfied = EXCLUDED.criterion_satisfied,
                                n_candidates_converged = EXCLUDED.n_candidates_converged,
                                lb_p_14_holdout_onestep = EXCLUDED.lb_p_14_holdout_onestep,
                                lb_p_21_holdout_onestep = EXCLUDED.lb_p_21_holdout_onestep,
                                lb_p_28_holdout_onestep = EXCLUDED.lb_p_28_holdout_onestep,
                                holdout_mape = EXCLUDED.holdout_mape, holdout_rmse = EXCLUDED.holdout_rmse,
                                holdout_baseline_mape = EXCLUDED.holdout_baseline_mape,
                                holdout_baseline_rmse = EXCLUDED.holdout_baseline_rmse,
                                start_params = EXCLUDED.start_params,
                                selection_aic_per_obs = EXCLUDED.selection_aic_per_obs,
                                trained_through_date = EXCLUDED.trained_through_date,
                                evaluated_through_date = EXCLUDED.evaluated_through_date, selected_at = now()
                            """
                        ),
                        {
                            "facility_id": facility_id, "blood_type": blood_type,
                            "p": selection["p"], "d": selection["d"], "q": selection["q"],
                            "seasonal_p": selection["seasonal_p"], "seasonal_d": selection["seasonal_d"],
                            "seasonal_q": selection["seasonal_q"], "seasonal_s": selection["seasonal_s"],
                            "aic": selection["aic"],
                            "lb_p_14_in_sample": selection["lb_p"][14], "lb_p_21_in_sample": selection["lb_p"][21],
                            "lb_p_28_in_sample": selection["lb_p"][28],
                            "criterion_satisfied": selection["criterion_satisfied"],
                            "n_candidates_converged": selection["n_candidates_converged"],
                            "lb_p_14_holdout_onestep": holdout["lb_p_onestep"][14],
                            "lb_p_21_holdout_onestep": holdout["lb_p_onestep"][21],
                            "lb_p_28_holdout_onestep": holdout["lb_p_onestep"][28],
                            "holdout_mape": holdout["mape"], "holdout_rmse": holdout["rmse"],
                            "holdout_baseline_mape": holdout["baseline_mape"], "holdout_baseline_rmse": holdout["baseline_rmse"],
                            # This winning candidate's OWN converged params (see
                            # _fit_candidate_order) — stored in the SAME row, SAME upsert as
                            # the order they belong to, so a re-selection replaces both
                            # together and a stale param vector can never attach to a
                            # different (p,d,q)x(P,D,Q,s) spec.
                            "start_params": json.dumps(selection["start_params"]),
                            "selection_aic_per_obs": selection["aic_per_obs"],
                            "trained_through_date": train_series[-1][0], "evaluated_through_date": today,
                        },
                    )
                    # The newly selected order should be used the NEXT time this type is
                    # forecast, not held back until the daily cache boundary — same
                    # invalidation _invalidate_facility_forecast_cache uses elsewhere.
                    _invalidate_facility_forecast_cache(conn, facility_id, [blood_type])
        finally:
            lock_conn.execute(
                text("SELECT pg_advisory_unlock(:ns, :fid)"), {"ns": _ORDER_SELECTION_LOCK_NAMESPACE, "fid": facility_id}
            )
    finally:
        lock_conn.close()


def _compute_restock_recommendation(
    blood_type: str,
    point_by_checkpoint: dict[int, float],
    minimum: Optional[int],
    maximum: Optional[int],
    today: date,
) -> Optional[dict]:
    """Pure translation of an already-computed point forecast into a restock
    action — never touches how that forecast was produced (SARIMAX/
    linear-trend/synthetic stand-in all feed it the same shape). Returns None
    if this type has no threshold configured, or if its point forecast never
    dips below `minimum` anywhere in the FORECAST_CHECKPOINTS window (0-30
    days out) — i.e. "on track".

    recommended_units targets bringing the worst point anywhere in that
    30-day window back up to `maximum` (the facility's own target ceiling for
    this type), not just the point at the moment it first breaches — a
    non-monotonic trajectory (e.g. SARIMAX's seasonal structure) can dip
    lower later before recovering, and a recommendation sized only to the
    breach point wouldn't actually cover that.
    """
    if minimum is None or maximum is None:
        return None

    breach_checkpoint = next(
        (c for c in FORECAST_CHECKPOINTS if point_by_checkpoint[c] < minimum), None
    )
    if breach_checkpoint is None:
        return None

    if breach_checkpoint == 0:
        # Already below minimum today — nothing to interpolate.
        days_until_breach = 0.0
    else:
        prev_checkpoint = FORECAST_CHECKPOINTS[FORECAST_CHECKPOINTS.index(breach_checkpoint) - 1]
        prev_units, breach_units = point_by_checkpoint[prev_checkpoint], point_by_checkpoint[breach_checkpoint]
        if breach_units == prev_units:
            days_until_breach = float(breach_checkpoint)
        else:
            frac = (prev_units - minimum) / (prev_units - breach_units)
            days_until_breach = prev_checkpoint + frac * (breach_checkpoint - prev_checkpoint)

    projected_low_point = min(point_by_checkpoint[c] for c in FORECAST_CHECKPOINTS)
    recommended_units = max(0, math.ceil(maximum - projected_low_point))

    return {
        "blood_type": blood_type,
        "breach_date": (today + timedelta(days=round(days_until_breach))).isoformat(),
        "days_until_breach": round(days_until_breach),
        "recommended_units": recommended_units,
        # The projection's lowest point in the window: an estimate, shown beside (never blended with) current stock.
        "projected_low_units": round(projected_low_point),
    }


def _restock_status(recommendations: list[dict]) -> str:
    return "action_needed" if recommendations else "on_track"


def _limited_history(observed_days: int) -> Optional[dict]:
    """Below SARIMAX_MIN_DAYS_REQUIRED recorded days a type's forecast, and its interval above all, says little. The
    outlook then never asserts "on track" or "at risk" for it (it is listed in `insufficient_history`), and a restock
    recommendation, which stays, carries this so the screen can say it rests on limited history. None at 30 days or more."""
    if observed_days >= SARIMAX_MIN_DAYS_REQUIRED:
        return None
    return {"observed_days": observed_days, "min_days_required": SARIMAX_MIN_DAYS_REQUIRED}


def _compute_downside_risk(
    blood_type: str,
    point_by_checkpoint: dict[int, float],
    lower_by_checkpoint: dict[int, Optional[float]],
    minimum: Optional[int],
    today: date,
) -> Optional[dict]:
    """The state between "on track" and a restock recommendation: the central projection holds at or above
    `minimum` at every checkpoint, but the lower bound of the prediction interval drops below it at one. Returns the
    earliest such checkpoint, or None.

    Display only. It produces no recommendation, changes no recommended_units and feeds no alert: those stay on the
    central projection (_compute_restock_recommendation). It only tells a reader the model sees downside it is not
    acting on. Never a claim about a type the central projection already breaches (that one has a recommendation, and
    "projected to hold" would be false), and never a claim without a bound: a checkpoint whose lower bound is None
    (a type too sparse for an interval, the synthetic stand-in) cannot make a type at risk.

    Checkpoint 0 is skipped: today's stock is known, so an interval around it is not a risk of anything. The date is
    the checkpoint itself, not interpolated between checkpoints: the bound is a per-checkpoint statement.
    """
    if minimum is None or any(point_by_checkpoint[c] < minimum for c in FORECAST_CHECKPOINTS):
        return None
    risk_checkpoint = next(
        (c for c in FORECAST_CHECKPOINTS
         if c > 0 and lower_by_checkpoint[c] is not None and lower_by_checkpoint[c] < minimum),
        None,
    )
    if risk_checkpoint is None:
        return None
    return {
        "blood_type": blood_type,
        "risk_date": (today + timedelta(days=risk_checkpoint)).isoformat(),
        "days_until_risk": risk_checkpoint,
        "lower_units": round(lower_by_checkpoint[risk_checkpoint]),
    }


# SYNTHETIC — must match MODEL_LABEL in fit_and_cache_synthetic_forecast.py.
# Validated on generated data only; see SYNTHETIC_SARIMAX_VALIDATION.md for the
# full identification -> estimation -> diagnostic-checking -> cross-validation
# writeup, including the unresolved Ljung-Box failure this model was still
# shipped with (cross-validated forecast accuracy was checked directly instead —
# see that doc's Phase 4 section for why that's a defensible tradeoff here).
SYNTHETIC_MODEL_LABEL = "SARIMAX(0,1,4)x(1,0,1,7)+dengue"


def _build_synthetic_stand_in_forecast(
    conn, today: date, current_units_by_type: dict[str, int], thresholds: dict[str, int],
    maximum_thresholds: dict[str, int], is_dengue_season: bool, days_of_history: int,
) -> Optional[dict]:
    """Stand-in for a real per-facility trend while real history is below
    MIN_DAYS_REQUIRED: reads the pre-fit synthetic SARIMAX forecast (see
    SYNTHETIC_SARIMAX_VALIDATION.md) and rescales its shape to today's real
    per-type stock, so the trajectory is anchored to this facility's actual
    numbers even though the dynamics come from the synthetic model.

    Returns None (never a fabricated guess) if the cache doesn't cover every
    checkpoint date needed — that means fit_and_cache_synthetic_forecast.py
    needs re-running; the caller falls back to the same honest empty state
    used before this existed.
    """
    checkpoint_dates = [today + timedelta(days=c) for c in FORECAST_CHECKPOINTS]
    cached_rows = conn.execute(
        text(
            "SELECT blood_type, forecast_date, forecast_units FROM synthetic_forecast_cache "
            "WHERE forecast_date = ANY(:dates)"
        ),
        {"dates": checkpoint_dates},
    ).mappings().all()

    cached_by_type: dict[str, dict[date, float]] = {}
    for row in cached_rows:
        cached_by_type.setdefault(row["blood_type"], {})[row["forecast_date"]] = float(row["forecast_units"])

    for blood_type in current_units_by_type:
        type_cache = cached_by_type.get(blood_type)
        if not type_cache or any(d not in type_cache for d in checkpoint_dates):
            return None

    alerts = []
    restock_recommendations = []
    insufficient_history = []
    total_by_checkpoint = {c: 0 for c in FORECAST_CHECKPOINTS}

    for blood_type, current_units in current_units_by_type.items():
        type_cache = cached_by_type[blood_type]
        day0_synthetic = type_cache[checkpoint_dates[0]]
        scale_factor = (current_units / day0_synthetic) if day0_synthetic > 0 else 0.0

        scaled_by_checkpoint = {}
        for checkpoint, checkpoint_date in zip(FORECAST_CHECKPOINTS, checkpoint_dates):
            projected = max(0, round(type_cache[checkpoint_date] * scale_factor))
            scaled_by_checkpoint[checkpoint] = projected
            total_by_checkpoint[checkpoint] += projected

        recommendation = _compute_restock_recommendation(
            blood_type, scaled_by_checkpoint, thresholds.get(blood_type), maximum_thresholds.get(blood_type), today
        )
        limited = _limited_history(days_of_history)  # always limited: the stand-in exists below MIN_DAYS_REQUIRED
        if recommendation is not None:
            recommendation["limited_history"] = limited
            restock_recommendations.append(recommendation)
        else:
            insufficient_history.append({"blood_type": blood_type, **limited})

        minimum = thresholds.get(blood_type)
        if minimum is None or current_units < minimum:
            continue

        breach_checkpoint = next(
            (c for c in FORECAST_CHECKPOINTS if scaled_by_checkpoint[c] < minimum), None
        )
        if breach_checkpoint is None or breach_checkpoint == 0:
            continue

        prev_checkpoint = FORECAST_CHECKPOINTS[FORECAST_CHECKPOINTS.index(breach_checkpoint) - 1]
        prev_units, breach_units = scaled_by_checkpoint[prev_checkpoint], scaled_by_checkpoint[breach_checkpoint]
        if breach_units == prev_units:
            days_until_threshold = breach_checkpoint
        else:
            frac = (prev_units - minimum) / (prev_units - breach_units)
            days_until_threshold = prev_checkpoint + frac * (breach_checkpoint - prev_checkpoint)

        severity = "critical" if days_until_threshold <= 14 else "warn"
        reason = (
            f"SYNTHETIC MODEL ({SYNTHETIC_MODEL_LABEL}) — not this facility's own history, which is still "
            f"below the {MIN_DAYS_REQUIRED}-day minimum. Reference trajectory projected to fall below "
            f"minimum threshold ({minimum} units) in {round(days_until_threshold)} days."
        )
        if is_dengue_season:
            reason += " Coincides with dengue season, a period of historically elevated demand in this region."
        alerts.append({
            "type": blood_type,
            "severity": severity,
            "reason": reason,
            "days_until_threshold": round(days_until_threshold),
        })

    severity_order = {"critical": 0, "warn": 1}
    alerts.sort(key=lambda a: severity_order[a["severity"]])

    series = [
        {**_series_point_fields(c, today), "units": total_by_checkpoint[c]}
        for c in FORECAST_CHECKPOINTS
    ]
    restock_recommendations.sort(key=lambda r: r["days_until_breach"])
    return {
        "series": series,
        "alerts": alerts,
        "restock_recommendations": restock_recommendations,
        "restock_status": _restock_status(restock_recommendations),
        "insufficient_history": insufficient_history,
    }


def api_docs_enabled() -> bool:
    """The interactive docs (/docs, /redoc) and the schema (/openapi.json) list every route, including /admin/* and the dev-only
    tools, so they are OFF unless ENABLE_API_DOCS is true/1/yes/on. For local development set it in server/.env."""
    return os.environ.get("ENABLE_API_DOCS", "false").strip().lower() in ("true", "1", "yes", "on")


def fastapi_docs_settings(enabled: bool) -> dict:
    return {} if enabled else {"docs_url": None, "redoc_url": None, "openapi_url": None}


app = FastAPI(**fastapi_docs_settings(api_docs_enabled()))

# Added before CORS so CORS wraps it (a 413 still carries the CORS headers the browser needs to show the message). The donor upload
# is deliberately not listed.
app.add_middleware(upload_limits.UploadSizeLimitMiddleware, paths=("/inventory/upload", "/forecast/historical-upload"))

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Added last, so it is the outermost layer and its `app` figure covers everything this process does. Durations only; see
# server_timing.py. SERVER_TIMING=false turns it off.
if server_timing.enabled():
    server_timing.install_db_timing(engine)
    server_timing.install_db_timing(read_engine)  # the read-only endpoints' statements, so `db` is not 0 for them
    app.add_middleware(server_timing.ServerTimingMiddleware)


@retry_read
def _check_database() -> None:
    with read_engine.connect() as conn:
        conn.execute(text("SELECT 1"))


@app.get("/health")
def health():
    """Liveness plus a database check, for UptimeRobot and the login page's warm-up ping. Deliberately says nothing else: a failure
    is a bare 503 (the database error can carry the host, port and user, so it goes to the server log, never the response), and a
    success is just "ok". The allowed CORS origins are no longer listed here; see DEPLOYMENT.md for how to check them."""
    try:
        _check_database()
    except Exception as exc:
        print(f"[health] database check failed: {type(exc).__name__}: {exc}")
        return Response(status_code=503)
    return {"status": "ok"}


NOTIFICATIONS_PAGE_SIZE = 50
NOTIFICATIONS_MAX_PAGE_SIZE = 100


@app.get("/notifications")
def list_notifications(limit: int = NOTIFICATIONS_PAGE_SIZE, before_id: Optional[int] = None, facility_id: int = Depends(get_acting_facility_id)):
    """Newest first, `limit` rows (default 50, at most 100). To reach older rows pass `before_id` = the id of the oldest row
    already held: this returns the rows older than it. The bell's count comes from the shared poller's GET /sync, not from this;
    there's no push/WebSocket involved. Rows whose truth has gone (see _resolve_stale_notifications)
    are marked read first, so the list never offers a finished to-do as new."""
    limit = max(1, min(limit, NOTIFICATIONS_MAX_PAGE_SIZE))
    with engine.begin() as conn:
        _resolve_stale_notifications(conn, facility_id)
        rows = conn.execute(
            text(
                """
                SELECT id, type, message, link, read_at, created_at
                FROM notifications
                WHERE facility_id = :facility_id AND (CAST(:before_id AS bigint) IS NULL OR id < :before_id)
                ORDER BY id DESC LIMIT :limit
                """
            ),
            {"facility_id": facility_id, "before_id": before_id, "limit": limit},
        ).mappings().all()
    return [dict(row) for row in rows]


# ─── Live sync ──────────────────────────────────────────────────────────────
# GET /sync is what every signed-in browser tab polls (one timer per tab, about every 15 s while the tab is visible) INSTEAD OF each
# screen polling its own endpoint. It answers "did anything change for my facility?" with small opaque tokens, one per section; the
# page refetches only the section whose token moved. No websocket and no SSE: those hold a connection open per user on a one-worker,
# 512 MB instance. Properties that matter, each tested in tests/test_sync.py:
#   - ONE statement on the read engine (three aggregates and a tiny fourth, and the two for messages, cross-joined: one round trip), so a
#     poll costs 1 statement, plus the 30-second active-account check every other authenticated request has, plus (only until the process
#     has seen the conversations migration) the readiness check. It writes nothing and touches no rate limiter.
#   - Scoped to the caller's facility by construction: every aggregate is filtered by the acting facility, and tokens are hashes, so
#     they say "something changed", never what, and never anything about another facility.
#   - A token is a hash of (a count, a version value) over that facility's own rows:
#       requests.incoming / requests.outgoing  count + max id + the newest stamp of any transition column (every transition stamps a time)
#       inventory   every unit that still matters (not archived, or archived but not yet expired) hashed row by row, plus the facility's
#                   thresholds, plus the Manila date (midnight expires units without touching a row)
#       expiring    the same hash over the units a bank can still offer within NOTIFY_EXPIRY_NEAR_DAYS, plus the date
#       messages    count + max id of the messages in the caller's own conversations (by conversation, or by request for a row with no
#                   conversation yet) plus a hash of the caller's own read positions: it moves when a message arrives or is sent in any of
#                   its conversations and when its read position moves (another tab), and says nothing about any other facility. Until the
#                   conversations migration has been run the statement leaves the conversation parts out (_conversations_ready) and the token
#                   covers the messages on the caller's requests alone.
#     notifications is not a token but the two numbers the bell draws (unread and latest_id), which replace /notifications/unread-count's polling.
#   - ETag / If-None-Match: the ETag is a hash of the whole payload; an unchanged answer is a bodyless 304.
#   - Forecasts are NOT part of it: they are cached per Manila day and refetched by the Dashboard's own rules.
SYNC_TOKEN_LENGTH = 12


def _token(*parts) -> str:
    """An opaque 12-hex-character token for a section: a hash of its parts, so it reveals nothing about the rows behind it."""
    return hashlib.sha1("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:SYNC_TOKEN_LENGTH]


_SYNC_SQL = f"""
    SELECT ns.unread, ns.latest_id,
           rs.in_count, rs.in_max_id, rs.in_stamp, rs.out_count, rs.out_max_id, rs.out_stamp,
           us.units, us.units_hash, us.expiring_units, us.expiring_hash,
           ts.thresholds_hash/*@COLS*/
    FROM (
        SELECT (SELECT count(*) FROM notifications n
                WHERE n.facility_id = :facility_id AND n.read_at IS NULL AND NOT COALESCE({_STALE_NOTIFICATION_SQL}, false)) AS unread,
               (SELECT COALESCE(max(id), 0) FROM notifications WHERE facility_id = :facility_id) AS latest_id
    ) ns,
    (
        SELECT count(*) FILTER (WHERE supplying_facility_id = :facility_id) AS in_count,
               COALESCE(max(id) FILTER (WHERE supplying_facility_id = :facility_id), 0) AS in_max_id,
               extract(epoch FROM max(GREATEST(created_at, accepted_at, declined_at, cancelled_at, supplier_confirmed_at, requester_confirmed_at))
                                  FILTER (WHERE supplying_facility_id = :facility_id)) AS in_stamp,
               count(*) FILTER (WHERE requesting_facility_id = :facility_id) AS out_count,
               COALESCE(max(id) FILTER (WHERE requesting_facility_id = :facility_id), 0) AS out_max_id,
               extract(epoch FROM max(GREATEST(created_at, accepted_at, declined_at, cancelled_at, supplier_confirmed_at, requester_confirmed_at))
                                  FILTER (WHERE requesting_facility_id = :facility_id)) AS out_stamp
        FROM requests WHERE requesting_facility_id = :facility_id OR supplying_facility_id = :facility_id
    ) rs,
    (
        SELECT count(*) AS units,
               COALESCE(sum(hashtextextended(concat_ws('|', id, location, volume_ml, collected_date, expires_date,
                                                       COALESCE(reserved_for_request_id::text, '~'), COALESCE(archived_at::text, '~')), 0)), 0) AS units_hash,
               count(*) FILTER (WHERE archived_at IS NULL AND reserved_for_request_id IS NULL
                                  AND expires_date >= :today AND expires_date <= :last_day) AS expiring_units,
               COALESCE(sum(hashtextextended(concat_ws('|', id, expires_date), 0)) FILTER (
                            WHERE archived_at IS NULL AND reserved_for_request_id IS NULL
                              AND expires_date >= :today AND expires_date <= :last_day), 0) AS expiring_hash
        FROM blood_units
        WHERE facility_id = :facility_id AND (archived_at IS NULL OR expires_date >= :today)
    ) us,
    (
        SELECT COALESCE(sum(hashtextextended(concat_ws('|', blood_type, minimum_units, maximum_units), 0)), 0) AS thresholds_hash
        FROM blood_type_thresholds WHERE facility_id = :facility_id
    ) ts/*@FROM*/
"""
_SYNC_SQL_TEMPLATE = _SYNC_SQL

# The messages section. With the conversations migration in: the messages of the caller's conversations, or, for a row with no conversation
# (written by an old server instance, or before the backfill), of its request's pair, plus the caller's own read positions.
_SYNC_SQL = _SYNC_SQL_TEMPLATE.replace("/*@COLS*/", ", ms.msg_count, ms.msg_max_id, cr.reads_count, cr.reads_hash").replace("/*@FROM*/", """,
    (
        SELECT count(*) AS msg_count, COALESCE(max(m.id), 0) AS msg_max_id
        FROM request_messages m
        LEFT JOIN conversations c ON c.id = m.conversation_id
        LEFT JOIN requests r ON r.id = m.request_id AND m.conversation_id IS NULL
        WHERE (c.id IS NOT NULL AND :facility_id IN (c.facility_low_id, c.facility_high_id))
           OR (r.id IS NOT NULL AND :facility_id IN (r.requesting_facility_id, r.supplying_facility_id))
    ) ms,
    (
        SELECT count(*) AS reads_count, COALESCE(sum(hashtextextended(concat_ws('|', conversation_id, last_read_message_id), 0)), 0) AS reads_hash
        FROM conversation_reads WHERE facility_id = :facility_id
    ) cr""")
# Before the migration: the messages on the caller's requests only, and no read positions.
_SYNC_SQL_NO_CONVERSATIONS = _SYNC_SQL_TEMPLATE.replace("/*@COLS*/", ", ms.msg_count, ms.msg_max_id, 0 AS reads_count, 0 AS reads_hash").replace("/*@FROM*/", """,
    (
        SELECT count(*) AS msg_count, COALESCE(max(m.id), 0) AS msg_max_id
        FROM request_messages m JOIN requests r ON r.id = m.request_id
        WHERE :facility_id IN (r.requesting_facility_id, r.supplying_facility_id)
    ) ms""")


@retry_read
def _read_sync_state(facility_id: int, today: date):
    sql = _SYNC_SQL if _conversations_ready() else _SYNC_SQL_NO_CONVERSATIONS
    with read_engine.connect() as conn:
        return conn.execute(
            text(sql),
            {"facility_id": facility_id, "today": today, "last_day": today + timedelta(days=NOTIFY_EXPIRY_NEAR_DAYS)},
        ).mappings().first()


def _sync_payload(facility_id: int, today: date) -> dict:
    r = _read_sync_state(facility_id, today)
    return {
        "notifications": {"unread": r["unread"], "latest_id": r["latest_id"]},
        "requests": {
            "incoming": _token("in", r["in_count"], r["in_max_id"], r["in_stamp"]),
            "outgoing": _token("out", r["out_count"], r["out_max_id"], r["out_stamp"]),
        },
        "inventory": _token("inv", today, r["units"], r["units_hash"], r["thresholds_hash"]),
        "expiring": _token("exp", today, r["expiring_units"], r["expiring_hash"]),
        "messages": _token("msg", r["msg_count"], r["msg_max_id"], r["reads_count"], r["reads_hash"]),
    }


def _etag_matches(header: Optional[str], etag: str) -> bool:
    """If-None-Match: a list of entity tags (weak ones too) or *; true when `etag` is one of them."""
    if not header:
        return False
    return any(part.strip() in ("*", etag, f"W/{etag}") for part in header.split(","))


@app.get("/sync")
def sync_tokens(request: Request, facility_id: int = Depends(get_acting_facility_id)):
    """The change tokens for the caller's own facility (see the Live sync notes above). 200 with a small JSON body and an ETag, or a
    bodyless 304 when the caller's If-None-Match already matches. Authenticated like every facility endpoint (401 without a valid
    token, 403 for a deactivated facility or an admin account), reads only, never limited."""
    payload = _sync_payload(facility_id, business_today())
    etag = '"' + hashlib.sha1(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:16] + '"'
    headers = {"ETag": etag, "Cache-Control": "private, no-cache"}
    if _etag_matches(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers=headers)
    return JSONResponse(payload, headers=headers)


@app.get("/notifications/unread-count")
@retry_read
def notifications_unread_count(facility_id: int = Depends(get_acting_facility_id)):
    """The bell's badge: how many notifications this facility has unread, all of them, not only the newest page.
    `latest_id` is the newest row's id (read or not), so the bell can tell a new arrival from a count that merely changed.

    A pure read, one statement. It used to run _resolve_stale_notifications (an UPDATE) first, on every poll, so that a notice whose
    request had moved on stopped counting. It now leaves those rows out of the count with the same condition
    (_STALE_NOTIFICATION_SQL) and writes nothing: the number is the one the sweep would have produced. The rows themselves are
    marked read where they always were besides this: at the event, and when the list is opened (GET /notifications)."""
    today = business_today()
    with read_engine.connect() as conn:
        row = conn.execute(
            text(
                f"""
                SELECT count(*) FILTER (WHERE n.read_at IS NULL AND NOT COALESCE({_STALE_NOTIFICATION_SQL}, false)) AS unread,
                       COALESCE(max(n.id), 0) AS latest_id
                FROM notifications n WHERE n.facility_id = :facility_id
                """
            ),
            {"facility_id": facility_id, "today": today},
        ).mappings().first()
    return {"unread": row["unread"], "latest_id": row["latest_id"]}


@app.post("/notifications/{notification_id}/read")
def mark_notification_read(notification_id: int, facility_id: int = Depends(get_acting_facility_id)):
    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                UPDATE notifications SET read_at = now()
                WHERE id = :id AND facility_id = :facility_id AND read_at IS NULL
                RETURNING id
                """
            ),
            {"id": notification_id, "facility_id": facility_id},
        ).mappings().first()
    if row is None:
        # Either it doesn't exist, belongs to another facility, or was already
        # read — all three are fine to treat as "nothing left to do" rather
        # than an error; marking read is idempotent from the caller's side.
        return {"id": notification_id, "already_read_or_not_found": True}
    return {"id": notification_id, "read": True}


@app.post("/notifications/read-all")
def mark_all_notifications_read(facility_id: int = Depends(get_acting_facility_id)):
    with engine.begin() as conn:
        result = conn.execute(
            text("UPDATE notifications SET read_at = now() WHERE facility_id = :facility_id AND read_at IS NULL"),
            {"facility_id": facility_id},
        )
    return {"marked_read": result.rowcount}


VALID_UPLOAD_TYPES = {"inventory", "donors", "historical_stock"}


@app.get("/upload-history")
def list_upload_history(upload_type: Optional[str] = None, facility_id: int = Depends(get_acting_facility_id)):
    """Facility-scoped log for all three CSV upload flows, filterable by type
    so each screen (Inventory, Donors, the forecast panel's historical
    backfill) only shows its own history. Never returns raw_content here —
    only whether it's there to fetch — since a full CSV body has no business
    riding along in a list response; GET /upload-history/{id}/download is
    the separate, explicit way to get it back.
    """
    if upload_type is not None and upload_type not in VALID_UPLOAD_TYPES:
        raise HTTPException(status_code=400, detail=f"upload_type must be one of {sorted(VALID_UPLOAD_TYPES)}")

    query = """
        SELECT h.id, h.upload_type, h.filename, h.uploaded_at, h.rows_processed, h.rows_failed,
               h.error_details, u.email AS uploaded_by_email, (h.raw_content IS NOT NULL) AS has_raw_content,
               h.undone_at
        FROM upload_history h
        LEFT JOIN users u ON u.id = h.uploaded_by
        WHERE h.facility_id = :facility_id
    """
    params = {"facility_id": facility_id}
    if upload_type is not None:
        query += " AND h.upload_type = :upload_type"
        params["upload_type"] = upload_type
    query += " ORDER BY h.uploaded_at DESC LIMIT 50"

    with engine.connect() as conn:
        rows = conn.execute(text(query), params).mappings().all()
    return [dict(row) for row in rows]


@app.get("/upload-history/{upload_id}/download")
def download_upload_history_file(upload_id: int, facility_id: int = Depends(get_acting_facility_id)):
    """Re-download exactly what was uploaded, for inventory and historical
    uploads only — donor uploads never had raw_content stored in the first
    place (see upload_donors), so this 404s the same way it would for a
    record that doesn't exist, rather than distinguishing "no file" from
    "not yours" to a caller who shouldn't be able to tell the difference."""
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT filename, raw_content, upload_type FROM upload_history "
                "WHERE id = :id AND facility_id = :facility_id"
            ),
            {"id": upload_id, "facility_id": facility_id},
        ).mappings().first()
    if row is None or row["raw_content"] is None:
        raise HTTPException(status_code=404, detail="no downloadable file for this upload")

    filename = row["filename"] or f"{row['upload_type']}-upload-{upload_id}.csv"
    return Response(
        content=row["raw_content"],
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _load_upload_for_undo(conn, upload_id: int, facility_id: int) -> dict:
    row = conn.execute(
        text(
            "SELECT id, upload_type, undone_at FROM upload_history "
            "WHERE id = :id AND facility_id = :facility_id"
        ),
        {"id": upload_id, "facility_id": facility_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="upload not found")
    return dict(row)


@app.get("/upload-history/{upload_id}/undo-preview")
def preview_upload_undo(upload_id: int, facility_id: int = Depends(get_acting_facility_id)):
    """What "Undo this upload" would actually do, computed fresh every call —
    this is what the confirmation step shows before anything is removed.
    Always live, even for an already-undone upload (already_undone just
    tells the frontend to show that state instead of offering the action
    again); nothing here has a side effect.
    """
    with engine.connect() as conn:
        upload = _load_upload_for_undo(conn, upload_id, facility_id)
        preview_fn, _ = UPLOAD_UNDO_HANDLERS[upload["upload_type"]]
        eligible, blocked = preview_fn(conn, upload_id, facility_id)
    return {
        "upload_type": upload["upload_type"],
        "already_undone": upload["undone_at"] is not None,
        "eligible": eligible,
        "blocked": blocked,
    }


@app.post("/upload-history/{upload_id}/undo")
def undo_upload(upload_id: int, facility_id: int = Depends(get_acting_facility_id)):
    """Removes exactly the rows this upload is currently responsible for
    (see upload_history_id) that are still safe to remove, per-type
    eligibility recomputed fresh here — never trusting whatever the
    confirmation screen showed a moment earlier, since a unit can get
    reserved or a donor can get confirmed on a blast in between.

    The upload_history row itself is never deleted — undone_at is set
    regardless of how many rows actually turned out eligible, since the
    undo action itself did run; the log stays a permanent record that this
    upload happened, now annotated with when it was undone.
    """
    with engine.begin() as conn:
        upload = _load_upload_for_undo(conn, upload_id, facility_id)
        if upload["undone_at"] is not None:
            raise HTTPException(status_code=400, detail="this upload has already been undone")

        _, apply_fn = UPLOAD_UNDO_HANDLERS[upload["upload_type"]]
        removed_count, blocked = apply_fn(conn, upload_id, facility_id)

        undone_at = conn.execute(
            text("UPDATE upload_history SET undone_at = now() WHERE id = :id RETURNING undone_at"),
            {"id": upload_id},
        ).scalar_one()

    result = {"removed_count": removed_count, "blocked": blocked, "undone_at": undone_at}
    if upload["upload_type"] == "inventory":
        # modified_left_unchanged, not "restored": prior unit values aren't recorded anywhere.
        result["counts"] = {
            "created_removed": removed_count,
            "modified_left_unchanged": sum(b["skip"] == "modified" for b in blocked),
            "skipped_transferred": sum(b["skip"] == "transferred" for b in blocked),
        }
    return result


class CreateInventoryUnitBody(BaseModel):
    din: str
    blood_type: str
    component: str
    location: str
    volume_ml: int
    collected_date: date
    expires_date: date


@app.post("/inventory")
def create_inventory_unit(body: CreateInventoryUnitBody, facility_id: int = Depends(get_acting_facility_id)):
    """Manual single-unit intake — the Inventory screen's "Add Unit" button,
    for a facility that doesn't want to prepare a CSV for one unit. Re-applies
    the same validation _parse_inventory_csv uses for the bulk path, since
    this skips that parser entirely.
    """
    din = body.din.strip()
    component = body.component.strip()
    location = body.location.strip()
    if not din:
        raise HTTPException(status_code=400, detail="din is required")
    if body.blood_type not in VALID_BLOOD_TYPES:
        raise HTTPException(status_code=400, detail=f"invalid blood_type {body.blood_type!r}")
    if not component:
        raise HTTPException(status_code=400, detail="component is required")
    if not location:
        raise HTTPException(status_code=400, detail="location is required")
    if body.volume_ml <= 0:
        raise HTTPException(status_code=400, detail="volume_ml must be a positive whole number")
    if body.expires_date <= body.collected_date:
        raise HTTPException(status_code=400, detail="expires_date must be after collected_date")

    with engine.begin() as conn:
        existing_facility = conn.execute(
            text("SELECT facility_id FROM blood_units WHERE din = :din"), {"din": din}
        ).scalar()
        if existing_facility is not None:
            detail = f"DIN {din!r} is already registered"
            if existing_facility != facility_id:
                detail += " to a different facility"
            raise HTTPException(status_code=400, detail=detail)

        row = conn.execute(
            text(
                """
                INSERT INTO blood_units
                    (din, blood_type, component, location, volume_ml, collected_date, expires_date, facility_id)
                VALUES
                    (:din, :blood_type, :component, :location, :volume_ml, :collected_date, :expires_date, :facility_id)
                RETURNING din, blood_type, component, location, volume_ml, collected_date, expires_date
                """
            ),
            {
                "din": din,
                "blood_type": body.blood_type,
                "component": component,
                "location": location,
                "volume_ml": body.volume_ml,
                "collected_date": body.collected_date,
                "expires_date": body.expires_date,
                "facility_id": facility_id,
            },
        ).mappings().first()
    return dict(row)


@app.get("/inventory")
@retry_read
def get_inventory(facility_id: int = Depends(get_acting_facility_id)):
    """The logged-in user's own facility's stock only — blood_units holds every
    facility's inventory (for the availability search), so this must filter.

    Still includes expired units (unlike /inventory/summary and every
    availability/forecast query) — staff needs to see them here to know
    what's in the expired backlog before archiving it via
    POST /inventory/archive-expired. Only excludes units already archived,
    so the working list declutters once that backlog is cleared without
    losing the rows (archived_at IS NULL, never a DELETE)."""
    with read_engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT id, din, blood_type, component, location, volume_ml,
                       collected_date, expires_date, reserved_for_request_id
                FROM blood_units
                WHERE facility_id = :facility_id AND archived_at IS NULL
                ORDER BY expires_date ASC
                """
            ),
            {"facility_id": facility_id},
        ).mappings().all()
    return [
        {
            "din": r["din"], "blood_type": r["blood_type"], "component": r["component"],
            "location": r["location"], "volume_ml": r["volume_ml"],
            "collected_date": r["collected_date"], "expires_date": r["expires_date"],
            # The request this unit is held for, so the Inventory can say why a unit is not available when it is expected to be.
            "reserved_for_request_id": r["reserved_for_request_id"],
        }
        for r in rows
    ]


@app.delete("/inventory/{din}")
def delete_inventory_unit(din: str, facility_id: int = Depends(get_acting_facility_id)):
    """Removes one unit from the logged-in facility's own inventory.

    Blocked if the unit is reserved for a pending/accepted transfer — the same
    reserved_for_request_id guard the CSV-upload undo flow uses, since a unit
    already promised to another facility can't just disappear out from under
    that request.
    """
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT id, facility_id, reserved_for_request_id FROM blood_units WHERE din = :din"),
            {"din": din},
        ).mappings().first()
        if row is None or row["facility_id"] != facility_id:
            raise HTTPException(status_code=404, detail=f"no unit {din!r} on file at this facility")
        if row["reserved_for_request_id"] is not None:
            raise HTTPException(
                status_code=400,
                detail="this unit is reserved for a pending transfer and can't be removed",
            )
        conn.execute(text("DELETE FROM blood_units WHERE id = :id"), {"id": row["id"]})
    return {"deleted": True, "din": din}


def _archive_expired_units(conn, today: date, facility_id: Optional[int] = None) -> dict[int, int]:
    """THE definition of "archive the expired units", shared by the manual POST /inventory/archive-expired (one facility) and
    the nightly POST /internal/daily-snapshot (every ACTIVE facility, when facility_id is None), so the two can never disagree.

    Sets archived_at on units already past expires_date (strictly before `today`, so a unit expiring today is still
    usable and stays) that are not already archived and not reserved for a request. Soft-delete only, never a DELETE.
    One set-based UPDATE; idempotent, because an archived unit no longer matches. Returns {facility_id: units archived}
    for the facilities that had any."""
    rows = conn.execute(
        text(
            """
            UPDATE blood_units b SET archived_at = now()
            WHERE b.expires_date < :today AND b.archived_at IS NULL AND b.reserved_for_request_id IS NULL
              AND (CAST(:facility_id AS bigint) IS NULL OR b.facility_id = :facility_id)
              AND (CAST(:facility_id AS bigint) IS NOT NULL
                   OR EXISTS (SELECT 1 FROM facilities f WHERE f.id = b.facility_id AND f.is_active))
            RETURNING b.facility_id
            """
        ),
        {"today": today, "facility_id": facility_id},
    ).all()
    archived: dict[int, int] = {}
    for r in rows:
        archived[r.facility_id] = archived.get(r.facility_id, 0) + 1
    return archived


@app.post("/inventory/archive-expired")
def archive_expired_inventory(facility_id: int = Depends(get_acting_facility_id)):
    """Clears the expired-units backlog for the logged-in facility: bulk-sets
    archived_at on every one of its own units that's already past
    expires_date and not already archived. Soft-delete only, matching the
    same archive-only philosophy as facilities.is_active — never a DELETE,
    so historical blood_units rows are preserved.

    Has no effect on any usable/available count: an archived unit is by
    definition already expired, so it was already excluded from those (see
    /inventory/summary, GET /forecast, /facilities/nearby) by expires_date
    alone. This only changes what GET /inventory shows by default.

    A unit still reserved for a transfer is skipped even if expired: it
    belongs to that request until confirm_receipt settles it (which records
    and archives it itself), and archiving it here would let it be moved to
    the requester as an archived, invisible unit.

    The nightly job (POST /internal/daily-snapshot) now does the same for every active facility, through the same helper;
    this stays for a facility that wants its backlog cleared now rather than tonight.
    """
    today = business_today()
    with engine.begin() as conn:
        archived = _archive_expired_units(conn, today, facility_id)
    return {"archived_count": archived.get(facility_id, 0)}


# THE count of usable units per (facility, blood type): expires_date >= today, whether archived or not (an archived unit that has not
# expired is still stock). Joined as a derived table by /inventory/summary, the hospital's threshold view and the snapshot writer,
# so the three can never disagree about what a day's stock is. Parameters: :today and :facility_id (NULL = every facility).
USABLE_COUNTS_SQL = """
    SELECT facility_id, blood_type, count(*) AS n
    FROM blood_units
    WHERE expires_date >= :today
      AND (CAST(:facility_id AS bigint) IS NULL OR facility_id = :facility_id)
    GROUP BY facility_id, blood_type
"""


@app.get("/inventory/summary")
@retry_read
def get_inventory_summary(facility_id: int = Depends(get_acting_facility_id)):
    """Per-type USABLE unit counts (the logged-in user's own facility)
    against this facility's own configured minimum thresholds.

    A "unit" is one blood_units row (one bag/donation), not a volume
    measurement. Types with zero current stock still appear, with units=0,
    so shortages are visible. Thresholds are per-facility (see
    blood_type_thresholds's schema comment) — every facility gets its own 8
    rows seeded at creation time (admin_approve_registration), so this
    join should never miss a row for a real facility.

    expires_date >= today (the Manila business date) only: an expired unit was never usable, so
    it can't count toward "how much stock does this facility have" any more
    than it can count as available to fulfill another facility's request
    (see GET /facilities/nearby, which has always filtered this way) — a
    unit that's excluded from availability elsewhere but still inflating
    this count would just be a second, inconsistent definition of "stock."

    A blood bank's rows also carry `can_give`, per component: the units above that type's minimum (its reserve) that are
    releasable, which is what another facility's search (GET /facilities/nearby) would see available from it. Both come from
    _releasable_counts, the same predicate release selects from, so this cannot promise more than release delivers. It is
    {} for a hospital, which supplies nobody. Thresholds are per blood type, not per component, so a component's
    figure subtracts the type's one minimum, exactly as the search does (a known limitation, while platelets are rare).
    """
    today = business_today()
    with read_engine.connect() as conn:
        # The facility's type rides along on the same read (it used to be a second statement).
        rows = conn.execute(
            text(
                f"""
                SELECT t.blood_type, t.minimum_units, t.maximum_units, COALESCE(c.n, 0) AS units, f.facility_type
                FROM blood_type_thresholds t
                JOIN facilities f ON f.id = t.facility_id
                LEFT JOIN ({USABLE_COUNTS_SQL}) c ON c.facility_id = t.facility_id AND c.blood_type = t.blood_type
                WHERE t.facility_id = :facility_id
                ORDER BY t.blood_type
                """
            ),
            {"facility_id": facility_id, "today": today},
        ).mappings().all()
        is_bank = bool(rows) and rows[0]["facility_type"] == "bloodbank"
        # Every component's releasable counts in one statement, not one per component.
        releasable = _releasable_counts_multi(conn, list(COMPONENTS), today, facility_id) if is_bank else {}
        releasable = {c: releasable[c].get(facility_id, {}) for c in releasable}
    out = []
    for row in rows:
        entry = dict(row)
        del entry["facility_type"]
        entry["can_give"] = (
            {c: max(0, releasable[c].get(entry["blood_type"], 0) - entry["minimum_units"]) for c in COMPONENTS} if is_bank else {}
        )
        out.append(entry)
    return out


class UpdateThresholdBody(BaseModel):
    minimum_units: int
    maximum_units: int


@app.put("/thresholds/{blood_type}")
def update_threshold(
    blood_type: str, body: UpdateThresholdBody, facility_id: int = Depends(get_acting_facility_id)
):
    """Edits the minimum/maximum safe-stock levels for one blood type, for
    the logged-in user's own facility only.

    facility_id comes strictly from the verified JWT (get_acting_facility_id)
    and scopes the UPDATE's WHERE clause — a facility can only ever edit its
    own thresholds, exactly like every other facility-scoped mutating
    endpoint (DELETE /inventory/{din}, POST /inventory/archive-expired, ...).
    Was global across every facility until
    migrate_scope_thresholds_to_facility.py — see that script for the
    correctness bug this fixed and its backfill.
    """
    if body.minimum_units < 0 or body.maximum_units < 0:
        raise HTTPException(status_code=400, detail="thresholds cannot be negative")
    if body.minimum_units > body.maximum_units:
        raise HTTPException(status_code=400, detail="minimum cannot be greater than maximum")
    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                UPDATE blood_type_thresholds
                SET minimum_units = :minimum_units, maximum_units = :maximum_units
                WHERE facility_id = :facility_id AND blood_type = :blood_type
                RETURNING blood_type, minimum_units, maximum_units
                """
            ),
            {
                "facility_id": facility_id,
                "blood_type": blood_type,
                "minimum_units": body.minimum_units,
                "maximum_units": body.maximum_units,
            },
        ).mappings().first()
        if row is None:
            raise HTTPException(status_code=404, detail=f"no threshold row for blood type {blood_type!r} at this facility")
    return dict(row)


# ─── Inventory CSV Upload (Step 8A) ────────────────────────────────────────
# Same real-upload pattern as Donors (Step 7A) — the Inventory screen's
# "Upload CSV" button was purely decorative until now.

def _parse_inventory_csv(csv_text: str) -> tuple[list[dict], list[dict]]:
    """Pure: parses inventory CSV text into (valid_rows, errors), no DB involved.

    Required headers (case-insensitive): din, blood_type, component, location,
    volume_ml, collected_date, expires_date. Blank lines are skipped silently;
    any other malformed row is reported with its line number and reason rather
    than silently dropped or failing the whole batch.

    Each valid row keeps its source line number under "row" so the caller can
    report a DB-side rejection (e.g. a DIN already owned by another facility)
    against the same line numbering used for parse errors.
    """
    reader = csv.DictReader(io.StringIO(csv_text))
    if not reader.fieldnames:
        return [], [{"row": 0, "reason": "empty file"}]

    normalized_fields = [f.strip().lower() for f in reader.fieldnames]
    required = {"din", "blood_type", "component", "location", "volume_ml", "collected_date", "expires_date"}
    missing = required - set(normalized_fields)
    if missing:
        return [], [{"row": 0, "reason": f"missing required column(s): {', '.join(sorted(missing))}"}]

    valid_rows: list[dict] = []
    errors: list[dict] = []
    for i, raw_row in enumerate(reader, start=2):  # row 1 is the header
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw_row.items()}
        din = row.get("din", "")
        blood_type = row.get("blood_type", "")
        component = row.get("component", "")
        location = row.get("location", "")
        volume_raw = row.get("volume_ml", "")
        collected_raw = row.get("collected_date", "")
        expires_raw = row.get("expires_date", "")

        if not any([din, blood_type, component, location, volume_raw, collected_raw, expires_raw]):
            continue  # blank line

        if not din:
            errors.append({"row": i, "reason": "missing din"})
            continue
        if blood_type not in VALID_BLOOD_TYPES:
            errors.append({"row": i, "reason": f"invalid blood_type {blood_type!r}"})
            continue
        if not component:
            errors.append({"row": i, "reason": "missing component"})
            continue
        if not location:
            errors.append({"row": i, "reason": "missing location"})
            continue

        try:
            volume_ml = int(volume_raw)
            if volume_ml <= 0:
                raise ValueError
        except ValueError:
            errors.append({"row": i, "reason": f"invalid volume_ml {volume_raw!r}, must be a positive whole number"})
            continue

        try:
            collected_date = date.fromisoformat(collected_raw)
        except ValueError:
            errors.append({"row": i, "reason": f"invalid collected_date {collected_raw!r}, expected YYYY-MM-DD"})
            continue

        try:
            expires_date = date.fromisoformat(expires_raw)
        except ValueError:
            errors.append({"row": i, "reason": f"invalid expires_date {expires_raw!r}, expected YYYY-MM-DD"})
            continue

        if expires_date <= collected_date:
            errors.append({"row": i, "reason": "expires_date must be after collected_date"})
            continue

        valid_rows.append({
            "row": i,
            "din": din,
            "blood_type": blood_type,
            "component": component,
            "location": location,
            "volume_ml": volume_ml,
            "collected_date": collected_date,
            "expires_date": expires_date,
        })

    return valid_rows, errors


@app.post("/inventory/upload")
def upload_inventory(
    file: UploadFile = File(...),
    facility_id: int = Depends(get_acting_facility_id),
    uploaded_by: Optional[int] = Depends(get_acting_user_id),
):
    """Real CSV upload for the Inventory screen (Step 8A).

    Duplicate handling deliberately differs from Donors (Step 7A), which
    upserts by (facility_id, phone) — here `din` is globally UNIQUE across all
    facilities (blood_units_din_key), not scoped per facility, because a DIN identifies
    one physical blood bag. Re-uploading a DIN that already belongs to *this*
    facility is treated as a data-entry correction and updates the row in
    place. A DIN that already belongs to a *different* facility is rejected
    per-row instead of silently reassigned — moving stock between facilities
    is only ever supposed to happen through the dual-confirmation transfer
    flow, not a CSV import.

    The INSERT ... ON CONFLICT ... WHERE facility_id = :facility_id below
    does the ownership check atomically in the same statement as the
    insert/update, so there's no separate SELECT-then-write race window.

    A plain `def`, not `async def`: the CSV parse and the database work block, so FastAPI runs this in its threadpool instead of
    on the event loop, where it would stop every other request on the instance until it finished. `file.file` is the
    upload's own synchronous file object.
    """
    csv_text = upload_limits.read_csv_upload(file)  # .csv, size (MAX_UPLOAD_BYTES), a look at the bytes, UTF-8

    valid_rows, errors = _parse_inventory_csv(csv_text)

    rows_processed = 0
    with engine.begin() as conn:
        upload_id = _start_upload_record(conn, facility_id, "inventory", uploaded_by, file.filename, csv_text)

        # One statement for the whole file (a round trip per row cost ~75 ms each
        # against the remote DB). RETURNING din tells us which rows the ownership
        # WHERE let through; the rest belong to another facility.
        applied: set[str] = set()
        batch = _last_wins(valid_rows, lambda r: r["din"])
        if batch:
            cols = ("din", "blood_type", "component", "location", "volume_ml", "collected_date", "expires_date")
            applied = {
                r[0] for r in conn.execute(
                    text(
                        """
                        INSERT INTO blood_units
                            (din, blood_type, component, location, volume_ml, collected_date, expires_date, facility_id, upload_history_id)
                        SELECT t.din, t.blood_type, t.component, t.location, t.volume_ml, t.collected_date, t.expires_date,
                               :facility_id, :upload_id
                        FROM unnest(
                            CAST(:din AS text[]), CAST(:blood_type AS text[]), CAST(:component AS text[]),
                            CAST(:location AS text[]), CAST(:volume_ml AS int[]),
                            CAST(:collected_date AS date[]), CAST(:expires_date AS date[])
                        ) AS t(din, blood_type, component, location, volume_ml, collected_date, expires_date)
                        ON CONFLICT (din) DO UPDATE SET
                            blood_type = EXCLUDED.blood_type,
                            component = EXCLUDED.component,
                            location = EXCLUDED.location,
                            volume_ml = EXCLUDED.volume_ml,
                            collected_date = EXCLUDED.collected_date,
                            expires_date = EXCLUDED.expires_date,
                            upload_history_id = EXCLUDED.upload_history_id
                        WHERE blood_units.facility_id = :facility_id
                        RETURNING din
                        """
                    ),
                    {**{c: [r[c] for r in batch] for c in cols}, "facility_id": facility_id, "upload_id": upload_id},
                )
            }

        for row in valid_rows:
            if row["din"] in applied:
                rows_processed += 1
            else:
                errors.append({"row": row["row"], "reason": f"DIN {row['din']!r} is already registered to a different facility"})

        _finish_upload_record(conn, upload_id, rows_processed, errors)

    return {"rows_processed": rows_processed, "errors": errors}


def _build_hospital_threshold_view(conn, facility_id: int, is_dengue_season: bool, today: date) -> dict:
    """Hospital-type facilities don't get a forecast at all — per the thesis's
    core design split, a hospital's job is to notice and act on a shortage,
    not to project its own draw-down trend (that's the blood bank side's
    concern, handled by the code below this function). Reads live
    current-stock-vs-minimum using the exact same "count every non-expired
    blood_units row for this facility" definition /inventory/summary already
    uses, so the numbers agree with the rest of the dashboard.

    The action prompt is informational only — "requires_confirmation: true"
    on every entry is the point. This endpoint never creates a request; that
    still requires a real, separate POST /requests call the user has to
    trigger themselves (already-built Emergency Sourcing flow).
    """
    rows = conn.execute(
        text(
            f"""
            SELECT t.blood_type, t.minimum_units, COALESCE(c.n, 0) AS units
            FROM blood_type_thresholds t
            LEFT JOIN ({USABLE_COUNTS_SQL}) c ON c.facility_id = t.facility_id AND c.blood_type = t.blood_type
            WHERE t.facility_id = :facility_id
            ORDER BY t.blood_type
            """
        ),
        {"facility_id": facility_id, "today": today},
    ).mappings().all()

    thresholds = []
    action_prompts = []
    for row in rows:
        units, minimum, blood_type = row["units"], row["minimum_units"], row["blood_type"]
        below = units < minimum
        deficit = max(0, minimum - units)
        thresholds.append({
            "blood_type": blood_type,
            "units": units,
            "minimum_units": minimum,
            "status": "below_minimum" if below else "ok",
            "deficit": deficit,
        })
        if below:
            message = (
                f"{blood_type} is below minimum threshold ({units} of {minimum} units). "
                f"Confirm sending a request to nearby blood banks?"
            )
            if is_dengue_season:
                message += " Coincides with dengue season, a period of historically elevated demand in this region."
            action_prompts.append({
                "blood_type": blood_type,
                "units": units,
                "minimum_units": minimum,
                "deficit": deficit,
                "message": message,
                "requires_confirmation": True,
            })

    action_prompts.sort(key=lambda p: p["deficit"], reverse=True)

    return {
        "view": "threshold_status",
        "is_dengue_season": is_dengue_season,
        "thresholds": thresholds,
        "action_prompts": action_prompts,
    }


# ─── Historical Inventory Snapshot Upload (Step 11A) ───────────────────────
# Blood-bank-only. Lets a newly onboarded blood bank with real historical
# stock records backfill inventory_snapshots directly, instead of waiting on
# MIN_DAYS_REQUIRED days of organic same-facility history to accumulate
# before GET /forecast below switches off the synthetic stand-in. Hospitals
# never see a forecast at all (_build_hospital_threshold_view above), so this
# endpoint refuses them outright rather than silently no-op'ing.

def _parse_historical_snapshot_csv(csv_text: str, today: date) -> tuple[list[dict], list[dict]]:
    """Pure: parses historical inventory-snapshot CSV text into (valid_rows,
    errors), no DB involved. Required headers (case-insensitive):
    snapshot_date, blood_type, units.

    A row's snapshot_date must be strictly before `today` — today's snapshot
    is exclusively written by GET /forecast from this facility's real live
    blood_units count on every call, so a backfill can never overwrite it
    with a stale uploaded number (or plant a fake future one).
    """
    reader = csv.DictReader(io.StringIO(csv_text))
    if not reader.fieldnames:
        return [], [{"row": 0, "reason": "empty file"}]

    normalized_fields = [f.strip().lower() for f in reader.fieldnames]
    required = {"snapshot_date", "blood_type", "units"}
    missing = required - set(normalized_fields)
    if missing:
        return [], [{"row": 0, "reason": f"missing required column(s): {', '.join(sorted(missing))}"}]

    valid_rows: list[dict] = []
    errors: list[dict] = []
    for i, raw_row in enumerate(reader, start=2):  # row 1 is the header
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw_row.items()}
        snapshot_date_raw = row.get("snapshot_date", "")
        blood_type = row.get("blood_type", "")
        units_raw = row.get("units", "")

        if not any([snapshot_date_raw, blood_type, units_raw]):
            continue  # blank line

        try:
            snapshot_date = date.fromisoformat(snapshot_date_raw)
        except ValueError:
            errors.append({"row": i, "reason": f"invalid snapshot_date {snapshot_date_raw!r}, expected YYYY-MM-DD"})
            continue

        if snapshot_date >= today:
            errors.append({
                "row": i,
                "reason": f"snapshot_date {snapshot_date_raw!r} must be before today ({today.isoformat()}) — today's stock is recorded automatically, not backfilled",
            })
            continue

        if blood_type not in VALID_BLOOD_TYPES:
            errors.append({"row": i, "reason": f"invalid blood_type {blood_type!r}"})
            continue

        try:
            units = int(units_raw)
            if units < 0:
                raise ValueError
        except ValueError:
            errors.append({"row": i, "reason": f"invalid units {units_raw!r}, must be a whole number 0 or greater"})
            continue

        valid_rows.append({"row": i, "snapshot_date": snapshot_date, "blood_type": blood_type, "units": units})

    return valid_rows, errors


@app.post("/forecast/historical-upload")
def upload_historical_inventory_snapshots(
    file: UploadFile = File(...),
    facility_id: int = Depends(get_acting_facility_id),
    uploaded_by: Optional[int] = Depends(get_acting_user_id),
    # Defaulted (FastAPI's usual BackgroundTasks param has none) so tests and other
    # direct Python callers can invoke this function without going through a real
    # ASGI request; a real HTTP request always gets a real one injected by FastAPI —
    # verified locally against a running uvicorn instance, not just read from docs,
    # since the type annotation must stay bare `BackgroundTasks` (not Optional[...])
    # for FastAPI's injection to recognize it; the `= None` is a runtime-only default.
    background_tasks: BackgroundTasks = None,
):
    """Backfills inventory_snapshots for dates before this facility started
    using BloodLink, from the facility's own past daily/periodic stock
    records. Feeds the exact same table (and thus the exact same
    has_sufficient_history / real-trend logic) GET /forecast already reads —
    there's no separate "uploaded forecast" code path, just more real rows
    for the same query to fit a trend over.

    facility_type is re-checked fresh from the DB, never trusted from the
    client: a hospital-type facility is rejected here even if it somehow
    reaches this endpoint, matching GET /forecast's own hospital/blood-bank
    split (hospitals get threshold_status, never forecast, regardless of
    what the frontend does or doesn't show them).

    Re-uploading a date/type already on file updates it in place (a
    corrected export is expected, not a duplicate) — same upsert-by-natural-
    key pattern GET /forecast itself already uses for today's snapshot.

    Order selection does NOT run after an upload by default: it is offline work (server/select_orders.py, run by hand from a real
    machine). Only when AUTO_ORDER_SELECTION_AFTER_UPLOAD=true does a successful upload schedule
    _run_order_selection_for_facility (bounded to MAX_ORDER_SELECTIONS_PER_BACKGROUND_RUN types) as a BackgroundTask, after the
    response is sent; otherwise one log line says it was skipped. That task overwrites facility_sarimax_order rows and clears the
    facility's forecast cache, and it burns the single instance's CPU for a long time (see _auto_order_selection_enabled). See
    _run_order_selection_for_facility for what happens if a run is interrupted partway (per-type commits) or collides with
    another run (a non-blocking advisory lock).

    A plain `def`, not `async def`, for the same reason as upload_inventory: it runs in the threadpool, not on the event loop.
    """
    with engine.connect() as conn:
        facility_type = conn.execute(
            text("SELECT facility_type FROM facilities WHERE id = :id"), {"id": facility_id}
        ).scalar()
    if facility_type != "bloodbank":
        raise HTTPException(status_code=403, detail="historical data upload is only available to blood bank facilities")

    csv_text = upload_limits.read_csv_upload(file)  # .csv, size (MAX_UPLOAD_BYTES), a look at the bytes, UTF-8

    valid_rows, errors = _parse_historical_snapshot_csv(csv_text, business_today())

    rows_processed = len(valid_rows)
    with engine.begin() as conn:
        upload_id = _start_upload_record(conn, facility_id, "historical_stock", uploaded_by, file.filename, csv_text)

        # A few statements for the whole file instead of one round trip per row.
        batch = _last_wins(valid_rows, lambda r: (r["snapshot_date"], r["blood_type"]))
        if batch:
            arrays = {c: [r[c] for r in batch] for c in ("snapshot_date", "blood_type", "units")}
            unnest = "unnest(CAST(:snapshot_date AS date[]), CAST(:blood_type AS text[]), CAST(:units AS int[])) AS t(snapshot_date, blood_type, units)"
            # 1. Save the organic value of any row this upload is about to overwrite
            #    (once: skipped if a base entry is already logged) so undo can restore it.
            conn.execute(
                text(
                    f"""
                    INSERT INTO inventory_snapshot_write_log (facility_id, snapshot_date, blood_type, units, upload_history_id)
                    SELECT s.facility_id, s.snapshot_date, s.blood_type, s.units, NULL
                    FROM inventory_snapshots s
                    WHERE s.facility_id = :facility_id AND s.upload_history_id IS NULL
                      AND (s.snapshot_date, s.blood_type) IN (SELECT t.snapshot_date, t.blood_type FROM {unnest})
                      AND NOT EXISTS (
                          SELECT 1 FROM inventory_snapshot_write_log l
                          WHERE l.facility_id = s.facility_id AND l.snapshot_date = s.snapshot_date
                            AND l.blood_type = s.blood_type AND l.upload_history_id IS NULL)
                    """
                ),
                {**arrays, "facility_id": facility_id},
            )
            # 2. Log what this upload writes.
            conn.execute(
                text(
                    f"""
                    INSERT INTO inventory_snapshot_write_log (facility_id, snapshot_date, blood_type, units, upload_history_id)
                    SELECT :facility_id, t.snapshot_date, t.blood_type, t.units, :upload_id FROM {unnest}
                    """
                ),
                {**arrays, "facility_id": facility_id, "upload_id": upload_id},
            )
            # 3. The upsert itself.
            conn.execute(
                text(
                    f"""
                    INSERT INTO inventory_snapshots (snapshot_date, blood_type, units, facility_id, upload_history_id)
                    SELECT t.snapshot_date, t.blood_type, t.units, :facility_id, :upload_id FROM {unnest}
                    ON CONFLICT (snapshot_date, blood_type, facility_id)
                    DO UPDATE SET units = EXCLUDED.units, upload_history_id = EXCLUDED.upload_history_id
                    """
                ),
                {**arrays, "facility_id": facility_id, "upload_id": upload_id},
            )
            _invalidate_facility_forecast_cache(conn, facility_id)

        _finish_upload_record(conn, upload_id, rows_processed, errors)

    with engine.connect() as conn:
        days_of_history = conn.execute(
            text("SELECT count(DISTINCT snapshot_date) FROM inventory_snapshots WHERE facility_id = :facility_id"),
            {"facility_id": facility_id},
        ).scalar()

    if rows_processed and background_tasks is not None:
        if _auto_order_selection_enabled():
            background_tasks.add_task(_run_order_selection_for_facility, facility_id, MAX_ORDER_SELECTIONS_PER_BACKGROUND_RUN)
        else:
            print(f"[order-selection] skipped after upload for facility {facility_id}: AUTO_ORDER_SELECTION_AFTER_UPLOAD is not true "
                  "(select orders offline with server/select_orders.py)")

    return {"rows_processed": rows_processed, "errors": errors, "days_of_history": days_of_history, "min_days_required": MIN_DAYS_REQUIRED}


# ─── Daily inventory snapshot ──────────────────────────────────────────────

def _write_inventory_snapshots(conn, today: date, facility_id: Optional[int] = None) -> list[tuple[int, bool]]:
    """THE writer of today's inventory_snapshots rows, shared by GET /forecast (one
    facility) and POST /internal/daily-snapshot (every active facility), so the two
    can never disagree about what a day's row means.

    Writes ALL eight blood types per facility, a type with no usable units as an
    explicit 0. Skipping zero rows made a stockout look like an unobserved day, and
    _resample_daily_series carries the last known level forward over an unobserved
    day — so a facility that ran out recorded its old stock as still on hand.
    "Usable" is expires_date >= today alone, the same predicate as /inventory/summary
    and every other stock count (archived_at only controls what GET /inventory shows;
    an archived unit is by definition already expired). Last write for the day wins.

    One set-based upsert on (snapshot_date, blood_type, facility_id), ordered so two
    concurrent callers lock rows in the same order. Returns [(facility_id, inserted)]
    per row written; inserted is False when it overwrote an existing row for today
    (xmax = 0 is how Postgres distinguishes the two inside ON CONFLICT ... RETURNING)."""
    rows = conn.execute(
        text(
            f"""
            INSERT INTO inventory_snapshots (snapshot_date, blood_type, units, facility_id)
            SELECT :today, bt.blood_type, COALESCE(c.n, 0), f.id
            FROM facilities f
            CROSS JOIN unnest(CAST(:types AS text[])) AS bt(blood_type)
            LEFT JOIN ({USABLE_COUNTS_SQL}) c ON c.facility_id = f.id AND c.blood_type = bt.blood_type
            WHERE f.is_active AND (CAST(:facility_id AS bigint) IS NULL OR f.id = :facility_id)
            ORDER BY f.id, bt.blood_type
            ON CONFLICT (snapshot_date, blood_type, facility_id)
            DO UPDATE SET units = EXCLUDED.units
            RETURNING facility_id, (xmax = 0) AS inserted
            """
        ),
        {"today": today, "types": sorted(VALID_BLOOD_TYPES), "facility_id": facility_id},
    ).all()
    return [(r.facility_id, r.inserted) for r in rows]


def _warm_forecasts_enabled() -> bool:
    """Whether the nightly job also warms every bank's forecast cache afterwards. OFF unless WARM_FORECASTS_AFTER_SNAPSHOT is
    true/1/yes/on. Read on every call, like the other switches."""
    return os.environ.get("WARM_FORECASTS_AFTER_SNAPSHOT", "false").strip().lower() in ("true", "1", "yes", "on")


# A pause between get_forecast calls leaves the single instance's CPU to real requests.
WARM_FORECAST_PAUSE_SECONDS = 1.0


def _warm_max_rounds() -> int:
    """One call of get_forecast fits at most MAX_SARIMAX_FITS_PER_REQUEST types, so a bank with every type missing needs
    ceil(types / 2) calls; one more round is allowed for a type a user's load was still fitting."""
    return math.ceil(len(VALID_BLOOD_TYPES) / MAX_SARIMAX_FITS_PER_REQUEST) + 1


@retry_read
def _facilities_to_warm() -> list[int]:
    """Active blood banks with enough recorded days for a SARIMAX fit (the only ones with anything to cache). Hospitals are never
    forecast, and a bank below SARIMAX_MIN_DAYS_REQUIRED days is served the linear trend or the stand-in, which are not cached."""
    with read_engine.connect() as conn:
        return list(conn.execute(
            text(
                """
                SELECT f.id FROM facilities f
                WHERE f.facility_type = 'bloodbank' AND f.is_active
                  AND (SELECT count(DISTINCT s.snapshot_date) FROM inventory_snapshots s WHERE s.facility_id = f.id) >= :min_days
                ORDER BY f.id
                """
            ),
            {"min_days": SARIMAX_MIN_DAYS_REQUIRED},
        ).scalars().all())


def _warm_one_facility(facility_id: int) -> str:
    """Loads this bank's forecast exactly as its dashboard does, by calling get_forecast itself, until nothing is pending. That one
    function is the whole path, so the snapshot write, the cache write, the advisory lock, the 2-fits-per-call cap, the order
    resolution (and its log lines, including the weekday-guard refusals) and the shortage notifications are all the endpoint's own.
    Stops early if a round made no progress, which means another request holds this facility's fit lock: that load will finish it."""
    previous: Optional[list[str]] = None
    max_rounds = _warm_max_rounds()
    for round_number in range(1, max_rounds + 1):
        pending = list(get_forecast(facility_id=facility_id).get("pending_types") or [])
        if not pending:
            return f"complete after {round_number} call{'s' if round_number != 1 else ''}"
        if pending == previous:
            return f"stopped with {len(pending)} type(s) still pending (another request is fitting this facility)"
        previous = pending
        time.sleep(WARM_FORECAST_PAUSE_SECONDS)
    return f"stopped after {max_rounds} calls with {len(previous or [])} type(s) still pending"


def _warm_all_forecasts() -> None:
    """The background task behind WARM_FORECASTS_AFTER_SNAPSHOT: one bank after another, one fit at a time, never raising (a failure
    is logged and the next bank goes on). Runs after the cron request has been answered. It only helps a load on the SAME business
    date it runs on: the cache is valid for trained_through_date == business_today(), so a run at 23:30 Manila warms a day that is
    about to end (see DEPLOYMENT.md for the second, after-midnight call)."""
    started, today = time.monotonic(), business_today()
    try:
        facility_ids = _facilities_to_warm()
    except Exception as exc:
        print(f"[warm] could not list the banks to warm: {type(exc).__name__}: {exc}")
        return
    print(f"[warm] warming {len(facility_ids)} bank(s) for {today.isoformat()}")
    for facility_id in facility_ids:
        try:
            print(f"[warm] facility {facility_id}: {_warm_one_facility(facility_id)}")
        except Exception as exc:
            print(f"[warm] facility {facility_id}: failed, {type(exc).__name__}: {exc}")
    print(f"[warm] done in {time.monotonic() - started:.1f} s")


@app.post("/internal/daily-snapshot", include_in_schema=False)
def daily_snapshot(
    x_cron_secret: Optional[str] = Header(default=None),
    # Defaulted for direct callers (tests); a real request always gets FastAPI's own, as in upload_historical_inventory_snapshots.
    background_tasks: BackgroundTasks = None,
):
    """Nightly job (scheduled externally, 23:30 Manila): records today's inventory
    snapshot for EVERY active facility, hospitals included, so a day nobody opened
    the dashboard still gets an observation (a real 0 when the shelf is empty).
    Hospitals are snapshotted but never forecast from.

    Fails closed: 503 if CRON_SECRET is unset or blank, 401 unless X-Cron-Secret
    matches it. Fits no forecast. Two short transactions, in this order: the snapshot upsert
    (committed first: it is the part that cannot be redone later), then the archive of every
    active facility's expired units (_archive_expired_units: archived_at, never a DELETE, units
    still reserved for a request skipped, exactly as the manual action). If the archive fails
    the snapshot is already safe and the call returns 500, so the cron history shows it.
    business_today() is read once and used for every write. Idempotent, so a retry (e.g. after
    a cold-start timeout) is safe: a second run archives nothing.

    With WARM_FORECASTS_AFTER_SNAPSHOT=true it also schedules _warm_all_forecasts as a background task (after the response); the
    response is the same either way."""
    expected = os.environ.get("CRON_SECRET", "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="daily snapshot job is not configured")
    # bytes, not str: compare_digest raises TypeError on a non-ASCII str, which would 500
    if x_cron_secret is None or not hmac.compare_digest(x_cron_secret.encode("utf-8"), expected.encode("utf-8")):
        raise HTTPException(status_code=401, detail="invalid cron secret")

    today = business_today()
    with engine.begin() as conn:
        written = _write_inventory_snapshots(conn, today)
    with engine.begin() as conn:
        archived = _archive_expired_units(conn, today)
        # expired units that were NOT archived because a request still holds them: they leave when receipt settles them
        still_reserved = conn.execute(
            text(
                """
                SELECT b.facility_id, count(*) AS n FROM blood_units b JOIN facilities f ON f.id = b.facility_id
                WHERE f.is_active AND b.expires_date < :today AND b.archived_at IS NULL AND b.reserved_for_request_id IS NOT NULL
                GROUP BY b.facility_id
                """
            ),
            {"today": today},
        ).all()
    facility_ids = sorted({fid for fid, _ in written})
    # After the response: the cron service's timeout is short and a warm-up takes minutes on this instance.
    if background_tasks is not None and _warm_forecasts_enabled():
        background_tasks.add_task(_warm_all_forecasts)
    return {
        "snapshot_date": today.isoformat(),
        "facilities": len(facility_ids),
        "rows_inserted": sum(1 for _, inserted in written if inserted),
        "rows_updated": sum(1 for _, inserted in written if not inserted),
        # every active facility is listed, a 0 meaning "looked, found none"; keys are facility ids
        "archived_expired": {
            "total": sum(archived.values()),
            "by_facility": {str(fid): archived.get(fid, 0) for fid in facility_ids},
        },
        "expired_still_reserved": {
            "total": sum(r.n for r in still_reserved),
            "by_facility": {str(r.facility_id): r.n for r in still_reserved},
        },
    }


@app.get("/forecast")
def get_forecast(facility_id: int = Depends(get_acting_facility_id)):
    """Dashboard's main data endpoint, scoped to the logged-in user's own
    facility. Behavior branches on the facility's real facility_type, looked
    up fresh from the DB every call (never trusted from the client) — this is
    real server-side gating, not a client choosing how to render shared data:
    a hospital-type facility can never receive forecast-shaped data from this
    endpoint, and a blood-bank-type facility can never receive the threshold
    view, regardless of what the frontend asks for.

    Blood banks: every call records today's real total-per-type USABLE stock
    (expires_date >= today (the Manila business date) only — an expired unit is never counted,
    here or anywhere else stock is treated as available) as a snapshot
    (idempotent per day, per facility; all eight types, a type with no usable
    units recorded as an explicit 0 — see _write_inventory_snapshots). Three tiers, gated purely on
    how many distinct days of this facility's own real history exist so far:
      - < MIN_DAYS_REQUIRED (3) days: a clearly-labeled SYNTHETIC stand-in —
        the pre-fit SARIMAX(0,1,4)x(1,0,1,7)+dengue model validated in
        SYNTHETIC_SARIMAX_VALIDATION.md on generated data, rescaled to
        today's real stock levels ("synthetic_model_stand_in").
      - MIN_DAYS_REQUIRED to SARIMAX_MIN_DAYS_REQUIRED (30) days: a real
        linear trend fit on this facility's own history — real data, simple
        method ("linear_trend").
      - >= SARIMAX_MIN_DAYS_REQUIRED (30) days: a real
        FACILITY_SARIMAX_ORDER/FACILITY_SARIMAX_SEASONAL_ORDER SARIMAX fit
        (with a calendar-based dengue exog) on this facility's own real,
        gap-resampled daily history per blood type, cached in
        facility_forecast_cache and only refit when new data has landed
        ("sarimax_facility_history"). Falls back to the linear trend, per
        blood type, if a fit doesn't converge or produces a degenerate
        result — see _fit_sarimax_facility_forecast.
    Every response says exactly which of the three produced it via
    "forecast_source" — no numbers are ever presented as more real, or more
    sophisticated, than they are.

    Hospitals: see _build_hospital_threshold_view.
    """
    today = business_today()
    is_dengue_season = today.month in DENGUE_SEASON_MONTHS

    with engine.begin() as conn:
        # The facility's type and its thresholds in one read (a bank needs both; a hospital's view reads its own).
        facility_rows = conn.execute(
            text(
                "SELECT f.facility_type, t.blood_type, t.minimum_units, t.maximum_units FROM facilities f "
                "LEFT JOIN blood_type_thresholds t ON t.facility_id = f.id WHERE f.id = :id"
            ),
            {"id": facility_id},
        ).mappings().all()
        if not facility_rows:
            raise HTTPException(status_code=500, detail="acting facility not found")
        facility_type = facility_rows[0]["facility_type"]

        if facility_type == "hospital":
            return _build_hospital_threshold_view(conn, facility_id, is_dengue_season, today)

        _write_inventory_snapshots(conn, today, facility_id)

        history_rows = conn.execute(
            text(
                """
                SELECT snapshot_date, blood_type, units
                FROM inventory_snapshots
                WHERE facility_id = :facility_id
                ORDER BY snapshot_date ASC
                """
            ),
            {"facility_id": facility_id},
        ).mappings().all()

        threshold_rows = [row for row in facility_rows if row["blood_type"] is not None]
        thresholds = {row["blood_type"]: row["minimum_units"] for row in threshold_rows}
        maximum_thresholds = {row["blood_type"]: row["maximum_units"] for row in threshold_rows}

        distinct_dates = sorted({row["snapshot_date"] for row in history_rows})
        days_of_history = len(distinct_dates)
        history = _history_totals(history_rows, today)

        if days_of_history < MIN_DAYS_REQUIRED:
            current_units_by_type = {
                row["blood_type"]: row["units"] for row in history_rows if row["snapshot_date"] == today
            }
            stand_in = _build_synthetic_stand_in_forecast(
                conn, today, current_units_by_type, thresholds, maximum_thresholds, is_dengue_season, days_of_history
            )

        if days_of_history < MIN_DAYS_REQUIRED:
            if stand_in is not None:
                # the reference model is not this facility's own history: nothing it projects is worth an interruption
                _check_forecast_shortage_notifications(conn, facility_id, [])
                return {
                    "view": "forecast",
                    "has_sufficient_history": False,
                    "days_of_history": days_of_history,
                    "min_days_required": MIN_DAYS_REQUIRED,
                    "is_dengue_season": is_dengue_season,
                    "forecast_source": "synthetic_model_stand_in",
                    "synthetic_model_label": SYNTHETIC_MODEL_LABEL,
                    "series": stand_in["series"],
                    "history": history,
                    "alerts": stand_in["alerts"],
                    "restock_recommendations": stand_in["restock_recommendations"],
                    "restock_status": stand_in["restock_status"],
                    "insufficient_history": stand_in["insufficient_history"],
                }
            # No forecast at all means no type can be alerting; without this, a
            # facility that fell back to "none" kept its old alerting=TRUE forever.
            _check_forecast_shortage_notifications(conn, facility_id, [])
            return {
                "view": "forecast",
                "has_sufficient_history": False,
                "days_of_history": days_of_history,
                "min_days_required": MIN_DAYS_REQUIRED,
                "is_dengue_season": is_dengue_season,
                "forecast_source": "none",
                "series": [],
                "history": history,
                "alerts": [],
                "restock_recommendations": [],
                "restock_status": None,
            }

        # Everything the warm path needs from the database, read in THIS transaction: the history is already here, so the series are
        # built here, and the cache and the alert state are read here too. Before, each came from its own connection (three
        # round trips of overhead apiece). The transaction still ends before anything slow: a fit (cold path) happens after the
        # commit, with the snapshot row locks released.
        first_date = distinct_dates[0]
        observed_dates = set(distinct_dates)
        by_type: dict[str, list[tuple[int, int]]] = {}
        raw_units_by_type: dict[str, dict[date, int]] = {}
        for row in history_rows:
            x = (row["snapshot_date"] - first_date).days
            by_type.setdefault(row["blood_type"], []).append((x, row["units"]))
            raw_units_by_type.setdefault(row["blood_type"], {})[row["snapshot_date"]] = row["units"]

        today_offset = (today - first_date).days
        use_sarimax = days_of_history >= SARIMAX_MIN_DAYS_REQUIRED
        daily_by_type: dict[str, list[tuple[date, int]]] = {}
        prefetched_cache: Optional[dict[str, Optional[dict]]] = None
        if use_sarimax:
            daily_by_type = {bt: _resample_daily_series(raw_units_by_type[bt], observed_dates, today) for bt in by_type}
            prefetched_cache = _read_cached_sarimax_many(
                conn, facility_id, {bt: sr for bt, sr in daily_by_type.items() if len(sr) >= SARIMAX_MIN_DAYS_REQUIRED}, today
            )
        tracked_alert_types = _read_tracked_alert_types(conn, facility_id)

    sarimax_fallback_types: list[str] = []
    alerts = []
    restock_recommendations = []
    limited_history_types: set[str] = set()  # types whose alert is not notified (too few recorded days)
    downside_risks = []
    insufficient_history = []
    type_bounds: dict[str, list[dict]] = {}
    total_by_checkpoint = {c: 0 for c in FORECAST_CHECKPOINTS}
    # Lower/upper are summed across types the same way the point estimate is —
    # each type's own prediction interval, added up. That's a conservative
    # (slightly wider than the tightest possible) band for the total, but it's
    # simple, honest, and consistent with how total_by_checkpoint itself is
    # already just a sum of independent per-type projections.
    lower_by_checkpoint = {c: 0 for c in FORECAST_CHECKPOINTS}
    upper_by_checkpoint = {c: 0 for c in FORECAST_CHECKPOINTS}
    band_available = False

    sarimax_by_type: dict[str, Optional[dict]] = {}
    pending_types: list[str] = []
    if use_sarimax:
        sarimax_by_type, pending_types = _collect_sarimax_results(
            facility_id, daily_by_type, today, observed_dates, prefetched_cache=prefetched_cache
        )

    for blood_type, points in by_type.items():
        if blood_type in pending_types:
            continue  # not fitted yet: contributes nothing rather than a guess
        minimum = thresholds.get(blood_type)
        method_used = "linear_trend"
        checkpoint_values: dict[int, tuple[float, Optional[float], Optional[float]]] = {}
        slope: Optional[float] = None

        sarimax_result = None
        if use_sarimax:
            daily_series = daily_by_type[blood_type]
            sarimax_result = sarimax_by_type.get(blood_type)

        if sarimax_result is not None:
            method_used = "sarimax"
            for c in FORECAST_CHECKPOINTS:
                cp = sarimax_result["checkpoints"][c]
                checkpoint_values[c] = (cp["units"], cp["lower"], cp["upper"])
            current_units = checkpoint_values[0][0]
        else:
            if use_sarimax:
                sarimax_fallback_types.append(blood_type)
            slope, intercept = _linear_trend(points)
            current_units = intercept + slope * today_offset
            for checkpoint in FORECAST_CHECKPOINTS:
                x0 = today_offset + checkpoint
                point_estimate = intercept + slope * x0
                projected = max(0, round(point_estimate))
                half_width = _prediction_interval_half_width(points, slope, intercept, x0)
                if half_width is not None:
                    lower, upper = max(0, round(point_estimate - half_width)), max(0, round(point_estimate + half_width))
                else:
                    # This type's own history is too sparse (n < 3) to have
                    # an interval yet, even though the facility as a whole
                    # cleared MIN_DAYS_REQUIRED.
                    lower = upper = None
                checkpoint_values[checkpoint] = (projected, lower, upper)

        for checkpoint in FORECAST_CHECKPOINTS:
            projected, lower, upper = checkpoint_values[checkpoint]
            total_by_checkpoint[checkpoint] += projected
            if lower is not None and upper is not None:
                band_available = True
                lower_by_checkpoint[checkpoint] += lower
                upper_by_checkpoint[checkpoint] += upper
            else:
                # Nothing principled to add — contribute the point
                # estimate with zero added width rather than widening it.
                lower_by_checkpoint[checkpoint] += projected
                upper_by_checkpoint[checkpoint] += projected

        recommendation = _compute_restock_recommendation(
            blood_type,
            {c: checkpoint_values[c][0] for c in FORECAST_CHECKPOINTS},
            minimum,
            maximum_thresholds.get(blood_type),
            today,
        )
        limited = _limited_history(len(points))
        if limited is not None:
            limited_history_types.add(blood_type)
        if recommendation is not None:
            # Recommendations are not gated, but one made on limited history says so, with the day count.
            if limited is not None:
                recommendation["limited_history"] = limited
            restock_recommendations.append(recommendation)
        elif limited is not None:
            # Never "on track" or "at risk" on this little history: asserting safety without a basis is worse than
            # asserting risk without one.
            insufficient_history.append({"blood_type": blood_type, **limited})
        else:
            downside_risk = _compute_downside_risk(
                blood_type,
                {c: checkpoint_values[c][0] for c in FORECAST_CHECKPOINTS},
                {c: checkpoint_values[c][1] for c in FORECAST_CHECKPOINTS},
                minimum,
                today,
            )
            if downside_risk is not None:
                downside_risks.append(downside_risk)
        type_bounds[blood_type] = [
            {**_series_point_fields(c, today), "units": checkpoint_values[c][0],
             "lower": checkpoint_values[c][1], "upper": checkpoint_values[c][2]}
            for c in FORECAST_CHECKPOINTS
        ]

        # Only flags types currently at/above minimum but trending toward
        # it — types already below minimum are already surfaced by
        # /inventory/summary. SARIMAX has no single slope, so the
        # checkpoint-30-vs-today delta stands in as the trend-direction
        # signal for that path.
        is_declining = (checkpoint_values[30][0] - checkpoint_values[0][0]) < 0 if method_used == "sarimax" else slope < 0
        if minimum is None or not is_declining or current_units < minimum:
            continue

        breach_checkpoint = next(
            (c for c in FORECAST_CHECKPOINTS if checkpoint_values[c][0] < minimum), None
        )
        if breach_checkpoint is None or breach_checkpoint == 0:
            continue

        prev_checkpoint = FORECAST_CHECKPOINTS[FORECAST_CHECKPOINTS.index(breach_checkpoint) - 1]
        prev_units, breach_units = checkpoint_values[prev_checkpoint][0], checkpoint_values[breach_checkpoint][0]
        if breach_units == prev_units:
            days_until_threshold = breach_checkpoint
        else:
            frac = (prev_units - minimum) / (prev_units - breach_units)
            days_until_threshold = prev_checkpoint + frac * (breach_checkpoint - prev_checkpoint)

        severity = "critical" if days_until_threshold <= 14 else "warn"
        if method_used == "sarimax":
            reason = (
                f"{sarimax_result['model_order']} fit on this facility's own {_count_observed_days(daily_series, observed_dates)} "
                f"observed days of history for {blood_type}; projected to fall below minimum threshold ({minimum} units) in "
                f"{round(days_until_threshold)} days"
            )
        else:
            reason = (
                f"{abs(slope):.1f} unit/day decline over a {len(points)}-day trend for {blood_type}; "
                f"projected to fall below minimum threshold ({minimum} units) in "
                f"{round(days_until_threshold)} days"
            )
        if is_dengue_season:
            reason += " Coincides with dengue season, a period of historically elevated demand in this region."
        alerts.append({
            "type": blood_type,
            "severity": severity,
            "reason": reason,
            "days_until_threshold": round(days_until_threshold),
        })

    severity_order = {"critical": 0, "warn": 1}
    alerts.sort(key=lambda a: severity_order[a["severity"]])

    # The facility-wide total is only meaningful once every type is in; a partial
    # sum would read as a real (and much lower) total.
    series = [
        {
            **_series_point_fields(c, today),
            "units": total_by_checkpoint[c],
            "lower": lower_by_checkpoint[c] if band_available else None,
            "upper": upper_by_checkpoint[c] if band_available else None,
        }
        for c in FORECAST_CHECKPOINTS
    ] if not pending_types else []

    # Same thresholds as the Dashboard: a type on limited history shows "not enough history" there, so it is not notified.
    shortage_alerts = [a for a in alerts if a["type"] not in limited_history_types]
    evaluated_types = set(by_type) - set(pending_types)
    # The usual load finds the same shortages as the last one: nothing to flip on or clear, so no connection is opened at all. When
    # there is something to write, the check re-reads the state itself, so two concurrent loads still produce one notification.
    if _shortage_check_needs_writes(tracked_alert_types, shortage_alerts, evaluated_types):
        with engine.begin() as conn:
            _check_forecast_shortage_notifications(
                conn, facility_id, shortage_alerts, evaluated_types=evaluated_types,
                recommendations={r["blood_type"]: r for r in restock_recommendations}, today=today,
            )

    restock_recommendations.sort(key=lambda r: r["days_until_breach"])
    downside_risks.sort(key=lambda r: r["days_until_risk"])

    forecast_source = "sarimax_facility_history" if use_sarimax else "linear_trend"
    response = {
        "view": "forecast",
        "has_sufficient_history": True,
        "days_of_history": days_of_history,
        "min_days_required": MIN_DAYS_REQUIRED,
        "sarimax_min_days_required": SARIMAX_MIN_DAYS_REQUIRED,
        "is_dengue_season": is_dengue_season,
        "forecast_source": forecast_source,
        "interval_confidence": FORECAST_INTERVAL_CONFIDENCE if band_available and not pending_types else None,
        "series": series,
        "history": history,
        "alerts": alerts,
        "restock_recommendations": restock_recommendations,
        # Display only, never a recommendation: types whose projection holds but whose interval's lower bound dips below
        # the minimum. Absent from the synthetic stand-in and "none" answers, which have no interval.
        "downside_risks": downside_risks,
        # Types with no recommendation and fewer than SARIMAX_MIN_DAYS_REQUIRED recorded days: not assessable, so neither
        # "on track" nor "at risk". A recommendation from such a type stays and carries `limited_history` instead.
        "insufficient_history": insufficient_history,
        # {blood_type: [{day, date, units, lower, upper}, ...]} for every type that has a forecast: the same checkpoints
        # and interval as `series`, per type instead of summed. lower/upper are null where a type has no interval.
        "type_bounds": type_bounds,
        # "pending" = nothing to act on yet AND some types not calculated: "on_track" would be a claim
        "restock_status": "pending" if pending_types and not restock_recommendations else _restock_status(restock_recommendations),
        "pending_types": pending_types,
        "types_total": len(by_type),
    }
    if forecast_source == "sarimax_facility_history":
        # Family + regressor only: the order differs per type, so it is NOT in this string.
        response["sarimax_model_label"] = FACILITY_SARIMAX_FAMILY_LABEL
        # {blood_type: "SARIMAX(p,d,q)x(P,D,Q,s)+dengue [grid-searched|ai-recommended|default-order]"}
        # for every type that has a SARIMAX forecast (fresh fit or cache hit); a linear-trend
        # type is absent here and listed in sarimax_fallback_types instead.
        response["sarimax_orders"] = {
            bt: r["model_order"] for bt, r in sarimax_by_type.items() if r is not None
        }
        response["sarimax_fallback_types"] = sarimax_fallback_types
    return response


@app.get("/facilities")
@retry_read
def list_facilities(user: dict = Depends(get_current_user)):
    """Plain facility list — resolves facility names on the Requests screen and powers the dev-only facility switcher dropdown.
    Requires a login (any account): it used to be public, and no screen the signed-out visitor sees (sign-in, registration)
    calls it. A deactivated facility still does not appear here — is_active is an access gate (see
    admin_update_facility_status)."""
    with read_engine.connect() as conn:
        rows = conn.execute(
            text("SELECT id, name, facility_type FROM facilities WHERE is_active ORDER BY facility_type, name")
        ).mappings().all()
    return [dict(row) for row in rows]


@app.get("/facilities/me")
def get_my_facility(facility_id: int = Depends(get_acting_facility_id)):
    """Full profile for the logged-in user's own facility — the account
    menu's Edit Facility Profile form prefills from this. Nothing here is
    new data; POST /facilities/profile already returns the same shape after
    a write, this just makes it readable without writing first."""
    with engine.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT id, name, facility_type, address, latitude, longitude,
                       department, profile_completed
                FROM facilities WHERE id = :id
                """
            ),
            {"id": facility_id},
        ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="facility not found")
    return dict(row)


class CompleteProfileBody(BaseModel):
    address: str
    latitude: float
    longitude: float
    department: str

    @field_validator("address", "department")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must not be blank")
        return v.strip()

    @field_validator("latitude")
    @classmethod
    def _latitude_in_range(cls, v: float) -> float:
        if not -90 <= v <= 90:
            raise ValueError("latitude must be between -90 and 90")
        return v

    @field_validator("longitude")
    @classmethod
    def _longitude_in_range(cls, v: float) -> float:
        if not -180 <= v <= 180:
            raise ValueError("longitude must be between -180 and 180")
        return v


@app.post("/facilities/profile")
def complete_facility_profile(body: CompleteProfileBody, facility_id: int = Depends(get_acting_facility_id)):
    """The other half of the old admin onboarding (POST /admin/facilities, removed
    2026-11-11): fills in the location/department a facility created that way
    doesn't have yet, and flips profile_completed so the frontend stops
    gating this facility into the complete-profile screen. The map-confirmed
    latitude/longitude are what Emergency Sourcing's distance ranking
    actually uses once this facility becomes a searchable candidate.

    Also doubles as the Edit Facility Profile endpoint (Account menu) —
    there's nothing first-time-only about this UPDATE, so a facility that's
    already complete can call it again to correct its address/pin/department
    later. GET /facilities/me is what the edit form prefills from."""
    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                UPDATE facilities SET
                    address = :address, latitude = :latitude, longitude = :longitude,
                    department = :department,
                    profile_completed = true
                WHERE id = :facility_id
                RETURNING id, name, facility_type, address, latitude, longitude,
                          department, profile_completed
                """
            ),
            {
                "address": body.address,
                "latitude": body.latitude,
                "longitude": body.longitude,
                "department": body.department,
                "facility_id": facility_id,
            },
        ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="facility not found")
    return dict(row)


# ─── Facility self-registration ─────────────────────────────────────────────
# Never creates a live facilities/users row directly (see
# admin_approve_registration) — only a facility_registration_requests row an
# administrator reviews after email ownership is proven. Unauthenticated by
# nature: nobody has an account yet.

# Public (registration has no login yet), so it is limited instead: per client address (client_ip.py), a minimum and maximum query
# length, and in geocode_service.py a short cache, a one-call-per-second throttle toward Nominatim, and an identifying User-Agent.
GEOCODE_MIN_QUERY_LENGTH = 5
GEOCODE_MAX_QUERY_LENGTH = 200
_GEOCODE_LIMITER = auth.AttemptLimiter(limit=10, window=60)


@app.get("/facilities/geocode")
def geocode_facility_address(address: str, request: Request):
    """Prefill-only lookup for the registration form's map picker (reuses
    FacilityLocationFields). A miss or a geocoder outage never fails the request —
    it just means the applicant places the pin manually, the same
    required-confirmation step CompleteProfile.tsx already has. Only a malformed
    query (too short or too long) or too many lookups from one address is refused."""
    query = address.strip()
    if len(query) < GEOCODE_MIN_QUERY_LENGTH:
        raise HTTPException(status_code=422, detail=f"Type at least {GEOCODE_MIN_QUERY_LENGTH} characters of the address to look it up.")
    if len(query) > GEOCODE_MAX_QUERY_LENGTH:
        raise HTTPException(status_code=422, detail=f"That address is too long to look up (at most {GEOCODE_MAX_QUERY_LENGTH} characters).")
    keys = [f"ip:{_request_ip(request) or 'unknown'}"]  # an address we cannot tell shares one budget rather than none
    _raise_if_limited(_GEOCODE_LIMITER, keys, "Too many address lookups.")
    _record_attempt(_GEOCODE_LIMITER, keys)
    result = geocode_service.geocode_address(query)
    if result is None:
        return {"latitude": None, "longitude": None}
    return {"latitude": result[0], "longitude": result[1]}


# The version of the Privacy Notice (the /privacy page) a registrant consents to, stored with the account. It must equal
# PRIVACY_NOTICE_VERSION in src/app/lib/privacyNotice.ts (scripts/check_privacy.ts fails if the two differ). Raise both when the notice
# changes in a way that needs fresh consent.
PRIVACY_NOTICE_VERSION = "1.1"

# The four columns of server/migrate_add_privacy_consent.sql. Until it has been applied the app still registers people (see
# _consent_columns_ready), it just cannot record the consent.
_CONSENT_COLUMNS = {("users", "privacy_consent_version"), ("users", "privacy_consented_at"),
                    ("facility_registration_requests", "privacy_consent_version"), ("facility_registration_requests", "privacy_consented_at")}
_consent_columns_seen = False


def _consent_columns_ready(conn) -> bool:
    """True once the consent migration has been applied. Positive answers are remembered for the life of the process; a negative one
    is asked again at the next registration or approval (rare events: one extra statement each) and logged, so a deploy that went out
    before the migration says so in the log instead of failing."""
    global _consent_columns_seen
    if _consent_columns_seen:
        return True
    found = conn.execute(
        text(
            "SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = 'public' "
            "AND table_name IN ('users', 'facility_registration_requests') AND column_name IN ('privacy_consent_version', 'privacy_consented_at')"
        )
    ).all()
    if {(r[0], r[1]) for r in found} >= _CONSENT_COLUMNS:
        _consent_columns_seen = True
        return True
    print("[consent] the privacy-consent columns are missing (run server/migrate_add_privacy_consent.sql): consent is NOT being recorded")
    return False


class RegisterFacilityBody(BaseModel):
    facility_name: str
    facility_type: str
    address: str
    latitude: float
    longitude: float
    contact_person: str
    email: EmailStr
    phone: str
    password: str
    # Strict on purpose: only a JSON true is consent ("yes" or 1 must not count). Both default to "not given", so a client that
    # does not send them (an old page, a script) is refused by _require_privacy_consent rather than by a validation error.
    privacy_consent: StrictBool = False
    privacy_notice_version: str = ""

    @field_validator("facility_name", "address", "contact_person", "phone")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must not be blank")
        return v.strip()

    @field_validator("facility_type")
    @classmethod
    def _facility_type_valid(cls, v: str) -> str:
        if v not in VALID_FACILITY_TYPES:
            raise ValueError(f"facility_type must be one of {sorted(VALID_FACILITY_TYPES)}")
        return v

    @field_validator("latitude")
    @classmethod
    def _latitude_in_range(cls, v: float) -> float:
        if not -90 <= v <= 90:
            raise ValueError("latitude must be between -90 and 90")
        return v

    @field_validator("longitude")
    @classmethod
    def _longitude_in_range(cls, v: float) -> float:
        if not -180 <= v <= 180:
            raise ValueError("longitude must be between -180 and 180")
        return v

    @field_validator("password")
    @classmethod
    def _password_policy(cls, v: str) -> str:
        error = auth.validate_registration_password(v)
        if error:
            raise ValueError(error)
        return v


# Identical for every outcome — email already registered, already
# mid-registration, or brand new — same enumeration-safety reasoning as
# _FORGOT_PASSWORD_RESPONSE.
_REGISTRATION_RESPONSE = {
    "message": "Check your email for a verification code. Once verified, an administrator will review your application."
}


def _send_registration_otp_email(to_email: str, facility_name: str, code: str) -> None:
    """A typed code, never a link — institutional mail scanners at hospital
    and government addresses follow links and consume single-use tokens
    before the recipient ever sees them, which breaks link-based
    verification for exactly the addresses this form targets."""
    html = f"""
    <div style="font-family: sans-serif; max-width: 480px; margin: 0 auto;">
      <h2 style="color: #8C1B3A;">Verify your BloodLink registration</h2>
      <p>Thanks for registering <strong>{facility_name}</strong> with BloodLink. Enter this code to verify your email address:</p>
      <p style="font-size: 28px; font-weight: bold; letter-spacing: 4px; text-align: center; background: #f5f5f5; padding: 12px; border-radius: 6px;">{code}</p>
      <p style="color: #666; font-size: 13px;">This code expires in 10 minutes. If you didn't request this, you can safely ignore this email.</p>
    </div>
    """
    email_service.send_email(to=to_email, subject="Verify your BloodLink registration", html=html)


def _send_registration_existing_account_email(to_email: str) -> None:
    """Sent instead of a verification code when the email already has a real
    users row — the on-screen response is identical either way (see
    _REGISTRATION_RESPONSE), so this email is the only place the distinction
    is ever visible, and only to whoever actually controls that inbox."""
    html = f"""
    <div style="font-family: sans-serif; max-width: 480px; margin: 0 auto;">
      <h2 style="color: #8C1B3A;">You already have a BloodLink account</h2>
      <p>Someone tried to register a new BloodLink facility with this email address, but an account already exists for it.</p>
      <p>If this was you and you've forgotten your password, you can reset it from the sign-in page.</p>
      <p style="color: #666; font-size: 13px;">If you didn't request this, you can safely ignore this email.</p>
    </div>
    """
    email_service.send_email(to=to_email, subject="BloodLink registration — account already exists", html=html)


def _require_privacy_consent(body) -> None:
    """400 unless the body carries an explicit consent to the notice version this server knows. A version the server does not know
    (a page loaded before the notice changed, or a frontend and a backend deployed out of step) is refused too: nobody consents to
    a text the server cannot say it showed them."""
    if body.privacy_consent is not True:
        raise HTTPException(status_code=400, detail="Please read the Privacy Notice and tick the consent box to register.")
    if body.privacy_notice_version != PRIVACY_NOTICE_VERSION:
        raise HTTPException(
            status_code=400,
            detail="The Privacy Notice has changed since this page was loaded. Reload the page, read it again and give your consent again.",
        )


@app.post("/facilities/register")
def register_facility(body: RegisterFacilityBody, request: Request):
    """Starts self-registration: stores a pending
    facility_registration_requests row (never facilities/users directly) and
    emails a 6-digit verification code. Resubmitting the same email before
    verification updates that same row in place — the form re-submit is
    also how a mistyped email or any other field gets corrected, and it
    naturally reissues the code too (see _issue_otp). A resubmission after
    rejection or approval (both archived — see admin_approve_registration
    and admin_reject_registration) starts a fresh row instead.

    Same identical-response-regardless-of-outcome shape as
    POST /auth/forgot-password, and the same rate limiter — one abuse
    concern (don't let this become a way to mail strangers), shared budget.

    Consent is enforced here, not only by the form's checkbox: a registration without a true `privacy_consent` for the CURRENT
    notice version is refused with a 400 and writes nothing. The version and the moment (the database clock) are stored on the
    registration row and copied to the account at approval, when the consent columns exist (_consent_columns_ready).
    """
    _require_privacy_consent(body)
    keys = _attempt_keys(request, body.email)
    _raise_if_limited(_FORGOT_LIMITER, keys, "Too many email requests.")
    _record_attempt(_FORGOT_LIMITER, keys)

    with engine.begin() as conn:
        record_consent = _consent_columns_ready(conn)
        existing_user = conn.execute(text("SELECT 1 FROM users WHERE email = :email"), {"email": body.email}).first()
        if existing_user is not None:
            try:
                _send_registration_existing_account_email(body.email)
            except email_service.EmailSendError as e:
                print(f"[registration] existing-account notice failed for {body.email}: {e}")
            return _REGISTRATION_RESPONSE

        params = {
            "facility_name": body.facility_name,
            "facility_type": body.facility_type,
            "address": body.address,
            "latitude": body.latitude,
            "longitude": body.longitude,
            "contact_person": body.contact_person,
            "email": body.email,
            "phone": body.phone,
            "password_hash": auth.hash_password(body.password),
        }

        existing_request = conn.execute(
            text("SELECT id FROM facility_registration_requests WHERE email = :email AND archived_at IS NULL"),
            {"email": body.email},
        ).mappings().first()

        params["consent_version"] = PRIVACY_NOTICE_VERSION  # unused by the statements below until the migration has been applied
        if existing_request is not None:
            registration_id = conn.execute(
                text(
                    f"""
                    UPDATE facility_registration_requests SET
                        facility_name = :facility_name, facility_type = :facility_type, address = :address,
                        latitude = :latitude, longitude = :longitude,
                        contact_person = :contact_person, phone = :phone, password_hash = :password_hash,
                        status = 'submitted'{", privacy_consent_version = :consent_version, privacy_consented_at = now()" if record_consent else ""}
                    WHERE id = :id
                    RETURNING id
                    """
                ),
                {**params, "id": existing_request["id"]},
            ).scalar_one()
        else:
            registration_id = conn.execute(
                text(
                    f"""
                    INSERT INTO facility_registration_requests
                        (facility_name, facility_type, address, latitude, longitude,
                         contact_person, email, phone, password_hash{", privacy_consent_version, privacy_consented_at" if record_consent else ""})
                    VALUES
                        (:facility_name, :facility_type, :address, :latitude, :longitude,
                         :contact_person, :email, :phone, :password_hash{", :consent_version, now()" if record_consent else ""})
                    RETURNING id
                    """
                ),
                params,
            ).scalar_one()

        code = _issue_otp(conn, "registration_email_verify", body.email, registration_request_id=registration_id)

    try:
        _send_registration_otp_email(body.email, body.facility_name, code)
    except email_service.EmailSendError as e:
        print(f"[registration] verification email failed for registration_id={registration_id}: {e}")

    return _REGISTRATION_RESPONSE


class VerifyRegistrationEmailBody(BaseModel):
    email: EmailStr
    code: str


# Generic on every outcome — no pending registration for this email, an
# expired/wrong/already-used code, or an attempt-capped one — same
# enumeration-safety reasoning as everywhere else in this file. The only way
# to tell any of these apart is by controlling the inbox the code was
# actually sent to.
_VERIFICATION_FAILED_DETAIL = "this verification code is invalid or has expired"
_VERIFICATION_SUCCESS_RESPONSE = {
    "message": "Email verified. Your application is now pending administrator review."
}


@app.post("/facilities/register/verify")
def verify_facility_registration_email(body: VerifyRegistrationEmailBody):
    """Gates submitted -> email_verified — the queue admin_list_registrations
    reads from only ever includes rows that have passed this. Idempotent for
    an already-verified row (a double-submit or a browser back/forward
    shouldn't show an error for a code that already did its job).

    Deliberately never raises INSIDE the `with engine.begin()` block: an
    exception there rolls back the whole transaction, which would silently
    undo the attempts += 1 _verify_otp just wrote on a wrong guess — the
    attempt cap would never actually accumulate. `verified` is resolved
    first, the transaction always commits normally, and the HTTPException
    (if any) is raised only after.

    The admin notification email fires exactly once — on the actual
    submitted -> email_verified transition, tracked by
    `newly_verified_facility_name` staying None on every other outcome
    (already verified, wrong code, no such registration) — not on every
    idempotent re-verify of an already-verified row."""
    newly_verified_facility_name = None
    with engine.begin() as conn:
        registration = conn.execute(
            text(
                "SELECT id, status FROM facility_registration_requests "
                "WHERE email = :email AND archived_at IS NULL"
            ),
            {"email": body.email},
        ).mappings().first()

        if registration is None:
            verified = False
        elif registration["status"] == "email_verified":
            verified = True
        else:
            verified = _verify_otp(conn, "registration_email_verify", body.code, registration_request_id=registration["id"])
            if verified:
                row = conn.execute(
                    text(
                        "UPDATE facility_registration_requests SET status = 'email_verified' "
                        "WHERE id = :id RETURNING facility_name"
                    ),
                    {"id": registration["id"]},
                ).mappings().first()
                newly_verified_facility_name = row["facility_name"]

    if not verified:
        raise HTTPException(status_code=400, detail=_VERIFICATION_FAILED_DETAIL)

    if newly_verified_facility_name is not None:
        try:
            _send_admin_registration_notice(newly_verified_facility_name)
        except email_service.EmailSendError as e:
            print(f"[registration] admin notice failed for registration_id={registration['id']}: {e}")

    return _VERIFICATION_SUCCESS_RESPONSE


def _send_admin_registration_notice(facility_name: str) -> None:
    """Best-effort — see ADMIN_EMAIL's own comment for why an unset value is
    just a failed send, not a special case. A closed browser tab reaches no
    one, so the queue itself (GET /admin/registrations) is never the only
    way an administrator finds out about this."""
    html = f"""
    <div style="font-family: sans-serif; max-width: 480px; margin: 0 auto;">
      <h2 style="color: #8C1B3A;">New facility registration pending review</h2>
      <p><strong>{facility_name}</strong> has verified its email and is waiting for review in the admin registration queue.</p>
    </div>
    """
    email_service.send_email(to=ADMIN_EMAIL, subject="BloodLink — facility registration pending review", html=html)


# Standard donor-recipient compatibility: recipient type -> donor types they
# can receive from. Same mapping already shown in the frontend's own
# compatibility reference panel — kept independently here since it serves a
# different purpose (facility stock fallback vs. a search-type suggestion),
# but if this table ever needs to change, both copies need updating together.
BLOOD_COMPATIBILITY: dict[str, list[str]] = {
    "A+": ["A+", "A-", "O+", "O-"],
    "A-": ["A-", "O-"],
    "B+": ["B+", "B-", "O+", "O-"],
    "B-": ["B-", "O-"],
    "O+": ["O+", "O-"],
    "O-": ["O-"],
    "AB+": ["A+", "A-", "B+", "B-", "O+", "O-", "AB+", "AB-"],
    "AB-": ["A-", "B-", "O-", "AB-"],
}

# Starting min/max safe-stock thresholds seeded for every newly-created
# facility (admin_approve_registration) — a facility can edit its
# own from here via PUT /thresholds/{blood_type}. Must match seed_thresholds.py
# and migrate_scope_thresholds_to_facility.py's backfill values, so a
# freshly-seeded facility and a migrated pre-existing one start identically.
DEFAULT_THRESHOLDS: list[tuple[str, int, int]] = [
    ("A+", 80, 160),
    ("A-", 50, 100),
    ("B+", 60, 120),
    ("B-", 40, 80),
    ("O+", 100, 200),
    ("O-", 80, 160),
    ("AB+", 30, 60),
    ("AB-", 25, 50),
]


# What a transfer request can ask for. These are the two values blood_units.component holds in production; a request
# names one, release only reserves units of exactly that component, and the nearby search counts only that component.
# Adding one is this line and COMPONENTS in src/app/lib/bloodComponents.ts (scripts/check_components.ts compares them).
# Do not add a component the system has never handled. There is deliberately no database CHECK, as for emergency_type.
COMPONENTS = ("Packed RBC", "Platelets")

# BLOOD_COMPATIBILITY above is red-cell compatibility. Platelets follow other rules, so a search for them offers no
# compatible alternatives until clinical staff supply the table for them.
RED_CELL_COMPONENTS = {"Packed RBC"}

# CLINICAL CONFIGURATION, not code logic. The fewest days a unit must still have before it expires, counted from the day
# the supplier releases it, for the release to reserve it (and for the nearby search to count it). A unit whose
# expires_date is on the release day itself is never released: RELEASE_EXPIRY_FLOOR_DAYS is the hard floor and no entry
# below it takes effect. Receipt still only requires expires_date >= its own day (see confirm_receipt).
#
# Defaults, to be confirmed by the blood banks' own clinical staff before they are relied on:
#   Packed RBC 3: the longest release-to-receipt gap on the three completed transfers in production was 2 days 12 hours
#     (request 10); 3 days covers that gap and leaves the unit valid on the day it arrives. Three transfers, one of them
#     slow, is a thin basis.
#   Platelets 1: the floor. Platelets keep only about five days (general knowledge, not measured here), so a larger
#     margin would exclude most of their life. They are also moved in hours, not days.
# The setting applies to the requested component only, and a component missing here gets the floor.
RELEASE_EXPIRY_FLOOR_DAYS = 1
MIN_DAYS_TO_EXPIRY_AT_RELEASE = {"Packed RBC": 3, "Platelets": 1}


def _release_expiry_cutoff(component: str, today: date) -> date:
    """The earliest expires_date a unit of `component` may have to be released on `today`."""
    return today + timedelta(days=max(RELEASE_EXPIRY_FLOOR_DAYS, MIN_DAYS_TO_EXPIRY_AT_RELEASE.get(component, 0)))


# THE definition of a unit another facility could be given: of the requested component, with enough time left before it
# expires (see _release_expiry_cutoff), and not already reserved for another request. confirm_release selects from exactly
# this, the nearby search counts exactly this, and so does anything that says how much a facility "can give", so no screen can
# promise more than release delivers. Needs :component and :cutoff (see _releasable_params).
RELEASABLE_UNITS_SQL = "component = :component AND expires_date >= :cutoff AND reserved_for_request_id IS NULL"


def _releasable_params(component: str, today: date) -> dict:
    return {"component": component, "cutoff": _release_expiry_cutoff(component, today)}


def _releasable_counts(conn, component: str, today: date, facility_id: Optional[int] = None) -> dict[int, dict[str, int]]:
    """{facility_id: {blood_type: releasable units}} under RELEASABLE_UNITS_SQL; one facility's when `facility_id` is given.
    A facility's "can give" for a type is this minus that type's minimum (the reserve), as the nearby search computes it."""
    rows = conn.execute(
        text(
            f"""
            SELECT facility_id, blood_type, count(*) AS unit_count
            FROM blood_units
            WHERE {RELEASABLE_UNITS_SQL} AND (CAST(:facility_id AS bigint) IS NULL OR facility_id = :facility_id)
            GROUP BY facility_id, blood_type
            """
        ),
        {**_releasable_params(component, today), "facility_id": facility_id},
    ).mappings().all()
    counts: dict[int, dict[str, int]] = {}
    for r in rows:
        counts.setdefault(r["facility_id"], {})[r["blood_type"]] = r["unit_count"]
    return counts


def _releasable_counts_multi(conn, components, today: date, facility_id: Optional[int] = None) -> dict[str, dict[int, dict[str, int]]]:
    """{component: _releasable_counts(component)} for several components in ONE statement: the same predicate
    (RELEASABLE_UNITS_SQL, once per component, with that component's own expiry cutoff), so the answers are identical to calling
    _releasable_counts per component; it only saves the round trips (the dashboard's summary asked once per component)."""
    parts, params = [], {"facility_id": facility_id}
    for i, component in enumerate(components):
        where = RELEASABLE_UNITS_SQL.replace(":component", f":component_{i}").replace(":cutoff", f":cutoff_{i}")
        parts.append(
            f"SELECT CAST(:component_{i} AS text) AS component, facility_id, blood_type, count(*) AS unit_count "
            f"FROM blood_units WHERE {where} AND (CAST(:facility_id AS bigint) IS NULL OR facility_id = :facility_id) "
            f"GROUP BY facility_id, blood_type"
        )
        params[f"component_{i}"] = component
        params[f"cutoff_{i}"] = _release_expiry_cutoff(component, today)
    out: dict[str, dict[int, dict[str, int]]] = {c: {} for c in components}
    if not parts:
        return out
    for r in conn.execute(text(" UNION ALL ".join(parts)), params).mappings().all():
        out[r["component"]].setdefault(r["facility_id"], {})[r["blood_type"]] = r["unit_count"]
    return out


def _component_error(component: str) -> str | None:
    return None if component in COMPONENTS else f"component must be one of {list(COMPONENTS)}"


def _evaluate_facility_stock(
    stock_by_type: dict[str, int], thresholds: dict[str, int], blood_type: str, quantity: int,
    compatibility: dict[str, list[str]] = BLOOD_COMPATIBILITY,
) -> dict:
    """Pure: given one facility's releasable stock counts and that SAME
    facility's own safety thresholds, compute exact-type availability plus
    any compatible-but-not-exact donor types that themselves have enough
    usable stock to cover the request.

    `available`/`usable_units` stay strictly about the exact type — compatible
    alternatives are surfaced separately, not blended into that signal.
    """
    matching_units = stock_by_type.get(blood_type, 0)
    usable_units = matching_units - thresholds.get(blood_type, 0)

    alternatives = []
    for alt_type in compatibility.get(blood_type, []):
        if alt_type == blood_type:
            continue
        alt_usable = stock_by_type.get(alt_type, 0) - thresholds.get(alt_type, 0)
        if alt_usable >= quantity:
            alternatives.append({"blood_type": alt_type, "usable_units": alt_usable, "label": "compatible alternative"})
    alternatives.sort(key=lambda a: a["usable_units"], reverse=True)

    return {
        "matching_units": matching_units,
        "usable_units": usable_units,
        "available": usable_units >= quantity,
        "compatible_alternatives": alternatives,
    }


@app.get("/facilities/nearby")
def get_nearby_facilities(
    blood_type: str, component: str, quantity: int = 1, acting_facility_id: int = Depends(get_acting_facility_id)
):
    """Blood banks ranked in three tiers — exact-type available, then
    unavailable-but-with-a-compatible-alternative in stock, then fully
    unavailable — with real haversine distance from the logged-in user's own
    facility as the tiebreaker within each tier (see _evaluate_facility_stock
    for the availability/alternatives check).

    `component` is required and counts only units of that component, so a bank
    holding only platelets is not "Available" for packed red cells and release
    does not then fail. Only units confirm_release would reserve are counted:
    those with enough time left before expiry (see _release_expiry_cutoff).
    Compatible alternatives are red-cell compatibility, so only a red-cell
    component gets them, and only a hospital is offered them: a blood bank is
    restocking, and covering an A- restock with O- is a patient-side
    substitution, not a restock.

    Expired units are excluded on purpose — expired stock isn't usable, so it
    can't count as available. Safety reserve is each CANDIDATE facility's own
    per-type threshold, not the searching facility's — a candidate's
    "available" stock is what it has above its own safety reserve, since
    that's the policy it would actually be giving away against. The acting
    facility is excluded from its own results — matters now that any
    facility (including blood banks) can be the origin, not just Riverside
    (which was never in the bloodbank-only candidate list, so this never
    came up before).
    """
    if (component_error := _component_error(component)) is not None:
        raise HTTPException(status_code=422, detail=component_error)
    today = business_today()
    with engine.connect() as conn:
        origin = conn.execute(
            text("SELECT latitude, longitude, facility_type FROM facilities WHERE id = :id"),
            {"id": acting_facility_id},
        ).mappings().first()
        if origin is None:
            raise HTTPException(status_code=500, detail="acting facility not found")
        if origin["latitude"] is None or origin["longitude"] is None:
            raise HTTPException(status_code=400, detail="complete your facility profile before searching the network")

        threshold_rows = conn.execute(
            text("SELECT facility_id, blood_type, minimum_units FROM blood_type_thresholds")
        ).mappings().all()
        thresholds_by_facility: dict[int, dict[str, int]] = {}
        for row in threshold_rows:
            thresholds_by_facility.setdefault(row["facility_id"], {})[row["blood_type"]] = row["minimum_units"]

        stock_by_facility = _releasable_counts(conn, component, today)

        # profile_completed facilities only — an admin-onboarded blood bank
        # with no confirmed location yet can't be distance-ranked, so it
        # can't appear as a search candidate until it completes its profile.
        # is_active too: a deactivated blood bank must not be a search result
        # any more than it can be a request target (see create_request below).
        # Coordinates checked explicitly too: nothing in the schema ties them
        # to profile_completed, and one null row would crash the haversine
        # for every searcher, not just hide that facility.
        facility_rows = conn.execute(
            text(
                """
                SELECT id, name, facility_type, address, latitude, longitude
                FROM facilities
                WHERE facility_type = 'bloodbank' AND id != :acting_facility_id
                  AND profile_completed AND is_active
                  AND latitude IS NOT NULL AND longitude IS NOT NULL
                """
            ),
            {"acting_facility_id": acting_facility_id},
        ).mappings().all()

    results = []
    for facility in facility_rows:
        evaluation = _evaluate_facility_stock(
            stock_by_facility.get(facility["id"], {}), thresholds_by_facility.get(facility["id"], {}),
            blood_type, quantity,
            BLOOD_COMPATIBILITY if component in RED_CELL_COMPONENTS and origin["facility_type"] == "hospital" else {},
        )
        results.append({
            **dict(facility),
            "distance_km": round(
                _haversine_km(origin["latitude"], origin["longitude"], facility["latitude"], facility["longitude"]), 1
            ),
            **evaluation,
        })
    def rank(r: dict) -> tuple[int, float]:
        if r["available"]:
            tier = 0
        elif r["compatible_alternatives"]:
            tier = 1
        else:
            tier = 2
        return (tier, r["distance_km"])

    results.sort(key=rank)
    return results


# A unit a facility can still OFFER another: on its shelf (not archived) and not already promised to a transfer. The one predicate behind
# the Dashboard's expiring groups, the grouped notify endpoint and the per-unit notify endpoint, so none of them can offer blood that
# is committed elsewhere.
OFFERABLE_UNITS_SQL = "archived_at IS NULL AND reserved_for_request_id IS NULL"


@app.get("/inventory/expiring")
@retry_read
def get_expiring_inventory(facility_id: int = Depends(get_acting_facility_id)):
    """The facility's units that expire within NOTIFY_EXPIRY_NEAR_DAYS (today included), grouped by blood type, component and
    expiry date, soonest first, for the Dashboard: a bank holding hundreds of units wants "3 units of O+ expire on 2 Nov",
    not one row per DIN. Expired units are the Inventory screen's business, and units reserved for a request are leaving
    anyway, so neither is listed.

    This is where expiring units live; it writes no notification (the old once-per-tier unit_expiry notification is gone)."""
    today = business_today()
    with read_engine.connect() as conn:
        rows = conn.execute(
            text(
                f"""
                SELECT id, din, blood_type, component, expires_date
                FROM blood_units
                WHERE facility_id = :facility_id AND {OFFERABLE_UNITS_SQL}
                  AND expires_date >= :today AND expires_date <= :last_day
                ORDER BY expires_date ASC
                """
            ),
            {"facility_id": facility_id, "today": today, "last_day": today + timedelta(days=NOTIFY_EXPIRY_NEAR_DAYS)},
        ).mappings().all()
    groups: dict[tuple, int] = {}
    for r in rows:
        key = (r["expires_date"], r["blood_type"], r["component"])
        groups[key] = groups.get(key, 0) + 1
    listed = [
        {"blood_type": bt, "component": comp, "expires_date": d, "days_left": (d - today).days, "units": n}
        for (d, bt, comp), n in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2]))
    ]
    return {"window_days": NOTIFY_EXPIRY_NEAR_DAYS, "total_units": sum(g["units"] for g in listed), "groups": listed}


class NotifyExpiringGroupBody(BaseModel):
    blood_type: str
    component: str
    expires_date: date


@app.post("/inventory/notify-nearby-hospitals")
def notify_nearby_hospitals_of_expiring_group(body: NotifyExpiringGroupBody, acting_facility_id: int = Depends(get_acting_facility_id)):
    """One in-system notification per nearby hospital for a whole group of near-expiry units (one blood type, component and
    expiry date), instead of one per DIN. Same recipients and the same Emergency Sourcing link as the per-unit endpoint, with
    the number of units in the message. The group must exist at the acting facility (units not reserved for a request) and
    still be inside the near-expiry window."""
    today = business_today()
    days_left = (body.expires_date - today).days
    if days_left < 0:
        raise HTTPException(status_code=400, detail="these units have already expired")
    if days_left > NOTIFY_EXPIRY_NEAR_DAYS:
        raise HTTPException(status_code=400, detail="these units aren't near-expiry yet")
    with engine.begin() as conn:
        units = conn.execute(
            text(
                f"""
                SELECT count(*) FROM blood_units
                WHERE facility_id = :facility_id AND {OFFERABLE_UNITS_SQL}
                  AND blood_type = :blood_type AND component = :component AND expires_date = :expires_date
                """
            ),
            {"facility_id": acting_facility_id, "blood_type": body.blood_type, "component": body.component, "expires_date": body.expires_date},
        ).scalar()
        if not units:
            raise HTTPException(status_code=404, detail="no such units at your facility")
        origin = conn.execute(
            text("SELECT name, latitude, longitude FROM facilities WHERE id = :id"), {"id": acting_facility_id}
        ).mappings().first()
        if origin["latitude"] is None or origin["longitude"] is None:
            raise HTTPException(status_code=400, detail="complete your facility profile before notifying nearby hospitals")
        hospitals = conn.execute(
            text(
                """
                SELECT id, name, latitude, longitude FROM facilities
                WHERE facility_type = 'hospital' AND id != :acting_facility_id AND profile_completed AND is_active
                  AND latitude IS NOT NULL AND longitude IS NOT NULL
                """
            ),
            {"acting_facility_id": acting_facility_id},
        ).mappings().all()
        when = _expiry_when(days_left, units)
        notified_names = []
        for hospital in hospitals:
            distance_km = round(_haversine_km(origin["latitude"], origin["longitude"], hospital["latitude"], hospital["longitude"]), 1)
            _create_notification(
                conn, hospital["id"], "expiring_unit_alert",
                f"{origin['name']} has {units} near-expiry {body.blood_type} {body.component} {'unit' if units == 1 else 'units'} that {when}, "
                f"{_kilometers_text(distance_km)} away",
                f"sourcing:{body.blood_type}",
            )
            notified_names.append(hospital["name"])
    return {"notified_count": len(notified_names), "notified_facilities": notified_names, "units": units}


@app.post("/inventory/{din}/notify-nearby-hospitals")
def notify_nearby_hospitals_of_expiring_unit(din: str, acting_facility_id: int = Depends(get_acting_facility_id)):
    """Real backend for the Dashboard's "Notify Nearby Hospitals" button
    (previously decorative — flipped a local checkmark and called nothing).
    Replaces the old /facilities/nearby-expiring-stock, which was never wired
    to any frontend, used a hardcoded origin facility, and had no auth at
    all — dead since the Step 6D facility-scoping pass.

    Creates a real in-system notification for every nearby, profile-completed
    hospital, pointing at Emergency Sourcing prefilled to this unit's blood
    type. The unit must belong to the acting facility — can't notify about
    someone else's stock — and must actually be near-expiry, the same 7-day
    window the Dashboard's own Expiry Warnings panel uses to decide whether
    to show this button at all.
    """
    with engine.begin() as conn:
        unit = conn.execute(
            text(
                f"""
                SELECT din, blood_type, component, expires_date
                FROM blood_units WHERE din = :din AND facility_id = :facility_id AND {OFFERABLE_UNITS_SQL}
                """
            ),
            {"din": din, "facility_id": acting_facility_id},
        ).mappings().first()
        if unit is None:
            # Say why: a unit that is not here, one already archived, and one promised to a transfer are different answers.
            state = conn.execute(
                text("SELECT archived_at IS NOT NULL AS archived FROM blood_units WHERE din = :din AND facility_id = :facility_id"),
                {"din": din, "facility_id": acting_facility_id},
            ).mappings().first()
            if state is None:
                raise HTTPException(status_code=404, detail="unit not found at your facility")
            if state["archived"]:
                raise HTTPException(status_code=400, detail="this unit has been archived, so it can't be offered")
            raise HTTPException(status_code=400, detail="this unit is reserved for a request, so it can't be offered")

        days_left = (unit["expires_date"] - business_today()).days
        if days_left < 0:
            raise HTTPException(status_code=400, detail="this unit has already expired")
        if days_left > NOTIFY_EXPIRY_NEAR_DAYS:
            raise HTTPException(status_code=400, detail="this unit isn't near-expiry yet")

        origin = conn.execute(
            text("SELECT name, latitude, longitude FROM facilities WHERE id = :id"),
            {"id": acting_facility_id},
        ).mappings().first()
        if origin["latitude"] is None or origin["longitude"] is None:
            raise HTTPException(status_code=400, detail="complete your facility profile before notifying nearby hospitals")

        hospitals = conn.execute(
            text(
                """
                SELECT id, name, latitude, longitude
                FROM facilities
                WHERE facility_type = 'hospital' AND id != :acting_facility_id
                  AND profile_completed AND is_active
                  AND latitude IS NOT NULL AND longitude IS NOT NULL
                """
            ),
            {"acting_facility_id": acting_facility_id},
        ).mappings().all()

        notified_names = []
        for hospital in hospitals:
            distance_km = round(
                _haversine_km(origin["latitude"], origin["longitude"], hospital["latitude"], hospital["longitude"]), 1
            )
            _create_notification(
                conn, hospital["id"], "expiring_unit_alert",
                f"{origin['name']} has 1 near-expiry {unit['blood_type']} {unit['component']} unit that {_expiry_when(days_left, 1)}, "
                f"{_kilometers_text(distance_km)} away",
                f"sourcing:{unit['blood_type']}",
            )
            notified_names.append(hospital["name"])

    return {"notified_count": len(notified_names), "notified_facilities": notified_names}


# The reasons a request can carry. The reason decides one thing: where the request is listed among the supplier's
# pending requests (see IMMEDIATE_USE_TYPES and _apply_priority_sort below), and a value that is not recognised ranks
# as restock without a word. So it is constrained here, at the API. There is deliberately no database CHECK: that is a
# production migration and a separate decision, so a row written some other way can still hold another value and rank
# as restock.
EMERGENCY_TYPES = ("trauma", "scheduled_surgery", "restock")


class CreateRequestBody(BaseModel):
    supplying_facility_id: int
    blood_type: str
    # Required, never defaulted: release reserves units of exactly this component, so the requester always chooses.
    component: str
    quantity: int
    emergency_type: str

    @field_validator("component")
    @classmethod
    def _component_valid(cls, v: str) -> str:
        if (error := _component_error(v)) is not None:
            raise ValueError(error)
        return v

    @field_validator("emergency_type")
    @classmethod
    def _emergency_type_valid(cls, v: str) -> str:
        if v not in EMERGENCY_TYPES:
            raise ValueError(f"emergency_type must be one of {list(EMERGENCY_TYPES)}")
        return v

    @field_validator("quantity")
    @classmethod
    def _quantity_positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError("quantity must be at least 1")
        return v

    @field_validator("blood_type")
    @classmethod
    def _blood_type_valid(cls, v: str) -> str:
        if v not in VALID_BLOOD_TYPES:
            raise ValueError(f"blood_type must be one of {sorted(VALID_BLOOD_TYPES)}")
        return v


@app.post("/requests")
def create_request(body: CreateRequestBody, requesting_facility_id: int = Depends(get_acting_facility_id)):
    """requesting_facility_id always comes from the logged-in user's own
    facility — never taken from the request body, same pattern as every other
    "who is 'us'" lookup in this app."""
    with engine.begin() as conn:
        # Blood banks have no patients, so trauma/scheduled_surgery (which
        # grant non-preemptive priority ahead of restock — see
        # IMMEDIATE_USE_TYPES/_priority_rank below) can never legitimately
        # apply to a blood-bank-originated request. Re-checked fresh from the
        # DB, never trusted from the client, same pattern as
        # _require_bloodbank_facility.
        requesting_facility_type = conn.execute(
            text("SELECT facility_type FROM facilities WHERE id = :id"), {"id": requesting_facility_id}
        ).scalar()
        emergency_type = "restock" if requesting_facility_type == "bloodbank" else body.emergency_type

        # Emergency Sourcing's own search only ever surfaces bloodbank-type
        # candidates, so a well-behaved client never sends anything else here
        # — but that's a frontend filter, not a guarantee. No facility (hospital
        # or blood bank) has a legitimate reason to target a hospital (or a
        # nonexistent id) as its supplier — only blood banks hold distributable
        # stock — so this is re-checked fresh from the DB and rejected outright
        # rather than trusted from the client, same pattern as the
        # emergency_type restriction just above.
        supplying_facility = conn.execute(
            text("SELECT facility_type, is_active FROM facilities WHERE id = :id"), {"id": body.supplying_facility_id}
        ).mappings().first()
        if supplying_facility is None or supplying_facility["facility_type"] != "bloodbank":
            raise HTTPException(status_code=400, detail="a facility can only request from a blood bank")
        if not supplying_facility["is_active"]:
            raise HTTPException(status_code=400, detail="this facility is not currently accepting requests")

        row = conn.execute(
            text(
                """
                INSERT INTO requests
                    (requesting_facility_id, supplying_facility_id, blood_type, component, quantity, emergency_type, status)
                VALUES
                    (:requesting_facility_id, :supplying_facility_id, :blood_type, :component, :quantity, :emergency_type, 'pending')
                RETURNING id, requesting_facility_id, supplying_facility_id, blood_type, component, quantity,
                          emergency_type, status, created_at
                """
            ),
            {
                "requesting_facility_id": requesting_facility_id,
                "supplying_facility_id": body.supplying_facility_id,
                "blood_type": body.blood_type,
                "component": body.component,
                "quantity": body.quantity,
                "emergency_type": emergency_type,
            },
        ).mappings().first()

        requester = conn.execute(
            text("SELECT name FROM facilities WHERE id = :id"), {"id": requesting_facility_id}
        ).mappings().first()
        _create_notification(
            conn, body.supplying_facility_id, "incoming_request",
            # The supplier decides what to handle first, so the reason goes in the text: the stored value (a blood
            # bank's is always restock), "scheduled_surgery" read as "scheduled surgery". The component is named because
            # the supplier will be asked for exactly that and nothing else.
            f"New {body.quantity}-unit {body.blood_type} {body.component} {emergency_type.replace('_', ' ')} request from {requester['name']}",
            f"requests:{row['id']}",
        )
    return dict(row)


# Every transition stamps when it happened and which account did it (accepted_at/by, declined_at/by, cancelled_at/by,
# supplier_confirmed_at + released_by, requester_confirmed_at + received_by), in the same transaction as the status change.
# The actor is the facility's one account (get_acting_user_id), never a named person, and is NULL under the dev override.
# Status lifecycle: pending -> accepted | declined | cancelled ; accepted ->
# completed (completed requires supplier_confirmed_at then
# requester_confirmed_at, in that order — see confirm_release/confirm_receipt
# below). cancelled is requester-initiated (see cancel_request), the mirror
# of declined being supplier-initiated — both are only reachable from
# pending, and both are terminal.
IMMEDIATE_USE_TYPES = {"trauma", "scheduled_surgery"}


def _priority_rank(emergency_type: str) -> int:
    return 0 if emergency_type in IMMEDIATE_USE_TYPES else 1


def _apply_priority_sort(rows: list[dict]) -> list[dict]:
    """Reorders only 'pending' rows: immediate-use (trauma/scheduled_surgery)
    ahead of restock, then by created_at ascending within a tier.

    Non-pending rows are treated as fixed anchors at their exact index in the
    input order (expected to already be created_at ascending) — this is what
    makes it non-preemptive: an accepted/completed request's position never
    changes, even when a later, higher-priority pending request arrives and
    ends up ahead of it by claiming an earlier open (pending) slot.
    """
    movable_indices = [i for i, r in enumerate(rows) if r["status"] == "pending"]
    movable_rows = sorted(
        (rows[i] for i in movable_indices),
        key=lambda r: (_priority_rank(r["emergency_type"]), r["created_at"]),
    )

    result = list(rows)
    for slot_index, sorted_row in zip(movable_indices, movable_rows):
        result[slot_index] = sorted_row
    return result


# Units of a request that expired before receipt (see confirm_receipt), for both request lists.
_FAILED_UNITS_SQL = """
    COALESCE((SELECT json_agg(json_build_object('din', f.din, 'expires_date', f.expires_date) ORDER BY f.din)
              FROM request_unit_failures f WHERE f.request_id = r.id), '[]'::json) AS failed_units
"""


@app.get("/requests")
@retry_read
def list_requests(facility_id: int = Depends(get_acting_facility_id)):
    """The logged-in user's own facility's outgoing requests, with pending ones
    priority-sorted (see _apply_priority_sort) and all other statuses left in
    natural order."""
    with read_engine.connect() as conn:
        rows = conn.execute(
            text(
                f"""
                SELECT r.id, r.requesting_facility_id, r.supplying_facility_id,
                       r.blood_type, r.component, r.quantity, r.emergency_type, r.status, r.created_at,
                       r.supplier_confirmed_at, r.requester_confirmed_at,
                       sf.name AS supplying_facility_name, {_FAILED_UNITS_SQL}
                FROM requests r
                JOIN facilities sf ON sf.id = r.supplying_facility_id
                WHERE r.requesting_facility_id = :facility_id
                ORDER BY r.created_at ASC, r.id ASC
                """
            ),
            {"facility_id": facility_id},
        ).mappings().all()
    return _apply_priority_sort([dict(row) for row in rows])


@app.get("/requests/incoming")
@retry_read
def list_incoming_requests(facility_id: int = Depends(get_acting_facility_id)):
    """Requests where the acting facility (dev switcher, or Riverside by
    default) is the supplier — the "what do I need to act on" inbox."""
    with read_engine.connect() as conn:
        rows = conn.execute(
            text(
                f"""
                SELECT r.id, r.requesting_facility_id, r.supplying_facility_id,
                       r.blood_type, r.component, r.quantity, r.emergency_type, r.status, r.created_at,
                       r.supplier_confirmed_at, r.requester_confirmed_at,
                       rf.name AS requesting_facility_name, {_FAILED_UNITS_SQL}
                FROM requests r
                JOIN facilities rf ON rf.id = r.requesting_facility_id
                WHERE r.supplying_facility_id = :facility_id
                ORDER BY r.created_at ASC, r.id ASC
                """
            ),
            {"facility_id": facility_id},
        ).mappings().all()
    return _apply_priority_sort([dict(row) for row in rows])


@app.post("/requests/{request_id}/accept")
def accept_request(
    request_id: int, facility_id: int = Depends(get_acting_facility_id),
    user_id: Optional[int] = Depends(get_acting_user_id),
):
    with engine.begin() as conn:
        req = conn.execute(text("SELECT * FROM requests WHERE id = :id"), {"id": request_id}).mappings().first()
        if req is None:
            raise HTTPException(status_code=404, detail="request not found")
        if req["supplying_facility_id"] != facility_id:
            raise HTTPException(status_code=403, detail="only the supplying facility can accept this request")
        if req["status"] != "pending":
            raise HTTPException(status_code=400, detail=f"cannot accept a request with status {req['status']!r}")
        result = conn.execute(
            text(
                "UPDATE requests SET status = 'accepted', accepted_at = now(), accepted_by = :user_id "
                "WHERE id = :id AND status = 'pending'"
            ),
            {"id": request_id, "user_id": user_id},
        )
        if result.rowcount == 0:
            raise HTTPException(status_code=409, detail="request status changed concurrently; refresh and try again")

        supplier = conn.execute(
            text("SELECT name FROM facilities WHERE id = :id"), {"id": req["supplying_facility_id"]}
        ).mappings().first()
        _create_notification(
            conn, req["requesting_facility_id"], "request_accepted",
            f"{supplier['name']} accepted your {_request_phrase(req)}",
            f"requests:{request_id}",
        )
        # The supplier's own next step (confirm release) is not announced to the supplier: they just clicked Accept, and the
        # request is in their "in progress" list with the button. What it does do is settle the "new request" prompt.
        _resolve_notifications(conn, req["supplying_facility_id"], "incoming_request", f"requests:{request_id}")
    return {"id": request_id, "status": "accepted"}


@app.post("/requests/{request_id}/decline")
def decline_request(
    request_id: int, facility_id: int = Depends(get_acting_facility_id),
    user_id: Optional[int] = Depends(get_acting_user_id),
):
    with engine.begin() as conn:
        req = conn.execute(text("SELECT * FROM requests WHERE id = :id"), {"id": request_id}).mappings().first()
        if req is None:
            raise HTTPException(status_code=404, detail="request not found")
        if req["supplying_facility_id"] != facility_id:
            raise HTTPException(status_code=403, detail="only the supplying facility can decline this request")
        if req["status"] != "pending":
            raise HTTPException(status_code=400, detail=f"cannot decline a request with status {req['status']!r}")
        result = conn.execute(
            text(
                "UPDATE requests SET status = 'declined', declined_at = now(), declined_by = :user_id "
                "WHERE id = :id AND status = 'pending'"
            ),
            {"id": request_id, "user_id": user_id},
        )
        if result.rowcount == 0:
            raise HTTPException(status_code=409, detail="request status changed concurrently; refresh and try again")

        supplier = conn.execute(
            text("SELECT name FROM facilities WHERE id = :id"), {"id": req["supplying_facility_id"]}
        ).mappings().first()
        _create_notification(
            conn, req["requesting_facility_id"], "request_declined",
            f"{supplier['name']} declined your {_request_phrase(req)}",
            f"requests:{request_id}",
        )
        _resolve_notifications(conn, req["supplying_facility_id"], "incoming_request", f"requests:{request_id}")
    return {"id": request_id, "status": "declined"}


@app.post("/requests/{request_id}/cancel")
def cancel_request(
    request_id: int, facility_id: int = Depends(get_acting_facility_id),
    user_id: Optional[int] = Depends(get_acting_user_id),
):
    """Requester-initiated withdrawal of their own request — the mirror of
    decline_request (supplier-initiated) above. Only reachable from pending:
    once a supplier has accepted, the requester can no longer unilaterally
    pull out (they'd need to coordinate via chat, same as any other real
    logistics change after commitment)."""
    with engine.begin() as conn:
        req = conn.execute(text("SELECT * FROM requests WHERE id = :id"), {"id": request_id}).mappings().first()
        if req is None:
            raise HTTPException(status_code=404, detail="request not found")
        if req["requesting_facility_id"] != facility_id:
            raise HTTPException(status_code=403, detail="only the requesting facility can cancel this request")
        if req["status"] != "pending":
            raise HTTPException(status_code=400, detail=f"cannot cancel a request with status {req['status']!r}")
        result = conn.execute(
            text(
                "UPDATE requests SET status = 'cancelled', cancelled_at = now(), cancelled_by = :user_id "
                "WHERE id = :id AND status = 'pending'"
            ),
            {"id": request_id, "user_id": user_id},
        )
        if result.rowcount == 0:
            raise HTTPException(status_code=409, detail="request status changed concurrently; refresh and try again")

        requester = conn.execute(
            text("SELECT name FROM facilities WHERE id = :id"), {"id": req["requesting_facility_id"]}
        ).mappings().first()
        _create_notification(
            conn, req["supplying_facility_id"], "request_cancelled",
            f"{requester['name']} cancelled their {_request_phrase(req)}",
            f"requests:{request_id}",
        )
        # the "new request" prompt for a request nobody can accept any more
        _resolve_notifications(conn, req["supplying_facility_id"], "incoming_request", f"requests:{request_id}")
    return {"id": request_id, "status": "cancelled"}


def _select_fefo_units(candidates: list[dict], quantity: int) -> list[dict]:
    """FEFO (First-Expired-First-Out): pick the `quantity` candidates with the
    earliest expires_date. Raises if fewer than `quantity` are available —
    we don't silently fulfill a partial quantity and call it done."""
    sorted_candidates = sorted(candidates, key=lambda u: u["expires_date"])
    if len(sorted_candidates) < quantity:
        raise ValueError(
            f"only {len(sorted_candidates)} matching unreserved units with enough time before expiry available; need {quantity}"
        )
    return sorted_candidates[:quantity]


@app.post("/requests/{request_id}/confirm-release")
def confirm_release(
    request_id: int, facility_id: int = Depends(get_acting_facility_id),
    user_id: Optional[int] = Depends(get_acting_user_id),
):
    """Supplier confirms release: FEFO-selects specific blood_units and marks
    them reserved_for_request_id. Does NOT move them yet — the actual
    facility_id reassignment only happens once the requester also confirms
    (confirm_receipt), matching "reassign only when both sides confirm"."""
    with engine.begin() as conn:
        req = conn.execute(text("SELECT * FROM requests WHERE id = :id"), {"id": request_id}).mappings().first()
        if req is None:
            raise HTTPException(status_code=404, detail="request not found")
        if req["supplying_facility_id"] != facility_id:
            raise HTTPException(status_code=403, detail="only the supplying facility can confirm release")
        if req["status"] != "accepted":
            raise HTTPException(status_code=400, detail=f"cannot confirm release for status {req['status']!r}")
        if req["supplier_confirmed_at"] is not None:
            raise HTTPException(status_code=400, detail="release already confirmed")
        # A request made before components existed names none. Matching on a NULL would reserve nothing and report a
        # misleading "0 units available", so say what is wrong; the requester cancels it and asks again.
        if req["component"] is None:
            raise HTTPException(
                status_code=400,
                detail="this request does not say which component it needs, so it cannot be released; decline it and ask the requester to send it again",
            )

        # FOR UPDATE locks the matching rows for the rest of this transaction:
        # a concurrent confirm-release (e.g. for a different request on the
        # same facility/blood_type) that tries to select overlapping rows
        # blocks until we commit, then re-evaluates reserved_for_request_id
        # IS NULL against our now-committed reservation — so it naturally
        # excludes whatever we just claimed instead of racing to overwrite it.
        # The component is matched exactly, and the expiry filter comes before FEFO picks, so the units nearest their
        # expiry (the ones FEFO would otherwise prefer) are the ones a margin removes (see _release_expiry_cutoff).
        today = business_today()
        cutoff = _release_expiry_cutoff(req["component"], today)
        candidates = conn.execute(
            text(
                f"""
                SELECT id, expires_date FROM blood_units
                WHERE facility_id = :facility_id AND blood_type = :blood_type AND {RELEASABLE_UNITS_SQL}
                FOR UPDATE
                """
            ),
            {"facility_id": facility_id, "blood_type": req["blood_type"], **_releasable_params(req["component"], today)},
        ).mappings().all()

        try:
            selected = _select_fefo_units([dict(c) for c in candidates], req["quantity"])
        except ValueError as exc:
            # Name the units the margin held back, or a supplier whose inventory screen shows enough stock is left guessing.
            held_back = conn.execute(
                text(
                    """
                    SELECT count(*) FROM blood_units
                    WHERE facility_id = :facility_id AND blood_type = :blood_type AND component = :component
                      AND expires_date >= :today AND expires_date < :cutoff AND reserved_for_request_id IS NULL
                    """
                ),
                {"facility_id": facility_id, "blood_type": req["blood_type"], "component": req["component"],
                 "today": today, "cutoff": cutoff},
            ).scalar()
            detail = str(exc)
            if held_back:
                days = (cutoff - today).days
                detail += f" ({held_back} more expire within {days} {'day' if days == 1 else 'days'} and are not released)"
            raise HTTPException(status_code=400, detail=detail)

        conn.execute(
            text(
                "UPDATE blood_units SET reserved_for_request_id = :rid "
                "WHERE id = ANY(:uids) AND reserved_for_request_id IS NULL"
            ),
            {"rid": request_id, "uids": [unit["id"] for unit in selected]},
        )
        # The ledger: one row per reserved unit, copied from the unit as it stands now. It is a snapshot, so a later CSV
        # re-upload that rewrites the unit's component, blood type or expiry cannot change what was released. Same
        # transaction as the reservation: no reserved unit without a row, no row without a reservation.
        conn.execute(
            text(
                """
                INSERT INTO request_units
                    (request_id, blood_unit_id, din, blood_type, component, volume_ml, collected_date, expires_date,
                     origin_location, days_remaining_at_release, outcome)
                SELECT :rid, id, din, blood_type, component, volume_ml, collected_date, expires_date,
                       location, expires_date - CAST(:today AS date), 'reserved'
                FROM blood_units WHERE id = ANY(:uids)
                """
            ),
            {"rid": request_id, "uids": [unit["id"] for unit in selected], "today": today},
        )
        result = conn.execute(
            text(
                "UPDATE requests SET supplier_confirmed_at = now(), released_by = :user_id "
                "WHERE id = :id AND status = 'accepted' AND supplier_confirmed_at IS NULL"
            ),
            {"id": request_id, "user_id": user_id},
        )
        if result.rowcount == 0:
            raise HTTPException(status_code=409, detail="release already confirmed or request status changed concurrently")

        supplier = conn.execute(
            text("SELECT name FROM facilities WHERE id = :id"), {"id": req["supplying_facility_id"]}
        ).mappings().first()
        # One meaning per type: this prompt is the requester's, to confirm receipt (the supplier's release prompt is no more).
        _create_notification(
            conn, req["requesting_facility_id"], "receipt_needed",
            f"{supplier['name']} released your {_request_phrase(req)}. Confirm receipt when it arrives.",
            f"requests:{request_id}",
        )

    return {"id": request_id, "reserved_unit_ids": [u["id"] for u in selected]}


@app.post("/requests/{request_id}/confirm-receipt")
def confirm_receipt(
    request_id: int, facility_id: int = Depends(get_acting_facility_id),
    user_id: Optional[int] = Depends(get_acting_user_id),
):
    """Requester confirms receipt: this is the actual transfer — reassigns
    facility_id on the units the supplier reserved, then marks completed.
    Requires supplier to have confirmed release first (enforced ordering).
    upload_history_id is kept: undo only removes units the uploading facility
    still owns, and uses the surviving tag to report these as transferred.

    A reserved unit that expired between release and receipt (expires_date
    before today's Manila business date) cannot be received: it is not
    moved, its reservation is cleared, it is archived where it is (at the
    supplier), and a request_unit_failures row records its DIN, expiry and
    why. The rest still transfer and the request is 'completed'; if every
    reserved unit expired, the request is 'failed'. Either way both
    facilities are notified with the DINs that failed, never a silent
    short count."""
    today = business_today()
    with engine.begin() as conn:
        req = conn.execute(text("SELECT * FROM requests WHERE id = :id"), {"id": request_id}).mappings().first()
        if req is None:
            raise HTTPException(status_code=404, detail="request not found")
        if req["requesting_facility_id"] != facility_id:
            raise HTTPException(status_code=403, detail="only the requesting facility can confirm receipt")
        if req["status"] != "accepted":
            raise HTTPException(status_code=400, detail=f"cannot confirm receipt for status {req['status']!r}")
        if req["supplier_confirmed_at"] is None:
            raise HTTPException(status_code=400, detail="supplier has not confirmed release yet")
        if req["requester_confirmed_at"] is not None:
            raise HTTPException(status_code=400, detail="receipt already confirmed")

        reserved = conn.execute(
            text(
                "SELECT id, din, expires_date FROM blood_units "
                "WHERE reserved_for_request_id = :request_id ORDER BY din FOR UPDATE"
            ),
            {"request_id": request_id},
        ).mappings().all()
        if len(reserved) != req["quantity"]:
            # Reserved units went missing between confirm-release and confirm-receipt
            # (shouldn't happen given the guards above, but never silently mark a
            # transfer "completed" if the unit count doesn't match).
            raise HTTPException(
                status_code=409,
                detail=(
                    f"expected to transfer {req['quantity']} reserved units but found "
                    f"{len(reserved)}; transfer aborted, nothing changed"
                ),
            )
        received_ids = [u["id"] for u in reserved if u["expires_date"] >= today]
        expired = [u for u in reserved if u["expires_date"] < today]

        if received_ids:
            conn.execute(
                text(
                    "UPDATE blood_units SET facility_id = :requesting_facility_id, reserved_for_request_id = NULL "
                    "WHERE id = ANY(:ids)"
                ),
                {"requesting_facility_id": req["requesting_facility_id"], "ids": received_ids},
            )
        # Settle the ledger rows written at release (see confirm_release), in this same transaction as the move. Days
        # remaining at receipt is counted from the LIVE unit's expiry, the value the received/failed decision above was
        # made on, so the ledger cannot disagree with the decision it records. The row's own expires_date stays the
        # release-time snapshot: if a CSV re-upload changed the expiry in transit, the two differ and that is visible.
        # The figure is zero or negative for a unit that expired in transit. A request released before the ledger existed
        # has no rows, and none are invented here: a snapshot made at receipt would not be a snapshot of release.
        for outcome, reason, units in (("received", None, [u for u in reserved if u["expires_date"] >= today]),
                                       ("failed", "expired_before_receipt", expired)):
            if units:
                conn.execute(
                    text(
                        "UPDATE request_units ru SET outcome = :outcome, reason = :reason, "
                        "days_remaining_at_receipt = b.expires_date - CAST(:today AS date) "
                        "FROM blood_units b WHERE ru.blood_unit_id = b.id "
                        "AND ru.request_id = :request_id AND ru.blood_unit_id = ANY(:ids)"
                    ),
                    {"outcome": outcome, "reason": reason, "today": today, "request_id": request_id, "ids": [u["id"] for u in units]},
                )
        if expired:
            expired_ids = [u["id"] for u in expired]
            conn.execute(
                text(
                    """
                    INSERT INTO request_unit_failures (request_id, blood_unit_id, din, expires_date, reason)
                    SELECT :request_id, id, din, expires_date, 'expired_before_receipt'
                    FROM blood_units WHERE id = ANY(:ids)
                    """
                ),
                {"request_id": request_id, "ids": expired_ids},
            )
            conn.execute(
                text("UPDATE blood_units SET reserved_for_request_id = NULL, archived_at = now() WHERE id = ANY(:ids)"),
                {"ids": expired_ids},
            )

        status = "completed" if received_ids else "failed"
        completion = conn.execute(
            text(
                "UPDATE requests SET requester_confirmed_at = now(), received_by = :user_id, status = :status "
                "WHERE id = :id AND status = 'accepted' AND supplier_confirmed_at IS NOT NULL "
                "AND requester_confirmed_at IS NULL"
            ),
            {"id": request_id, "status": status, "user_id": user_id},
        )
        if completion.rowcount == 0:
            raise HTTPException(status_code=409, detail="receipt already confirmed or request status changed concurrently")

        names = dict(conn.execute(
            text("SELECT id, name FROM facilities WHERE id IN (:r, :s)"),
            {"r": req["requesting_facility_id"], "s": req["supplying_facility_id"]},
        ).all())
        requester_name, supplier_name = names[req["requesting_facility_id"]], names[req["supplying_facility_id"]]
        link = f"requests:{request_id}"
        # The prompt to confirm receipt has been answered.
        _resolve_notifications(conn, req["requesting_facility_id"], "receipt_needed", link)
        if not expired:
            _create_notification(
                conn, req["supplying_facility_id"], "transfer_completed",
                f"{requester_name} confirmed receipt of your {_request_phrase(req)}. The transfer is complete.",
                link,
            )
        else:
            failed_text = _failed_units_text(expired)
            component = f" {req['component']}" if req["component"] else ""
            got = f"{len(received_ids)} of {req['quantity']} {req['blood_type']}{component} units"
            _create_notification(
                conn, req["requesting_facility_id"], "transfer_units_failed",
                f"Received {got} from {supplier_name}. Not received, expired before receipt: {failed_text}",
                link,
            )
            _create_notification(
                conn, req["supplying_facility_id"], "transfer_units_failed",
                f"{requester_name} received {got}. Not transferred, expired before receipt and archived at your "
                f"facility: {failed_text}",
                link,
            )

    return {
        "id": request_id, "status": status, "units_transferred": len(received_ids),
        "failed_units": [
            {"din": u["din"], "expires_date": u["expires_date"], "reason": "expired_before_receipt"} for u in expired
        ],
    }


class SendRequestMessageBody(BaseModel):
    message: str

    @field_validator("message")
    @classmethod
    def message_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("message cannot be blank")
        if len(v) > 2000:
            raise ValueError("message too long (max 2000 characters)")
        return v


def _require_request_participant(conn, request_id: int, facility_id: int) -> dict:
    """Coordination-chat messages are only ever visible to the two facilities
    actually on either side of the request — same "who is 'us'" scoping used
    everywhere else in this app, just applied to a request instead of a
    facility's own inventory."""
    req = conn.execute(text("SELECT * FROM requests WHERE id = :id"), {"id": request_id}).mappings().first()
    if req is None:
        raise HTTPException(status_code=404, detail="request not found")
    if facility_id not in (req["requesting_facility_id"], req["supplying_facility_id"]):
        raise HTTPException(status_code=403, detail="not a participant in this request")
    return dict(req)


def _received_counts(quantity: int, ledger: Optional[tuple[int, int]], failure_rows: int) -> tuple[int, int]:
    """(arrived, failed) for a request whose receipt was confirmed. The ledger's own (received, failed) counts when it has
    rows for the request; otherwise request_unit_failures is all a request released before the ledger existed has, and
    every unit it does not list as failed arrived, because confirm_receipt only settles a request whose reserved units
    match its quantity. One rule for the timeline, the detail's summary and the transfer history."""
    if ledger is not None:
        return ledger
    return quantity - failure_rows, failure_rows


def _request_block(req: dict, facilities: dict[int, dict]) -> dict:
    """The `request` object of GET /requests/{id}, and of every row of GET /transfers: one shape, built in one place."""
    return {
        "id": req["id"], "status": req["status"], "blood_type": req["blood_type"], "component": req["component"],
        "quantity": req["quantity"], "emergency_type": req["emergency_type"], "created_at": req["created_at"],
        "supplier_confirmed_at": req["supplier_confirmed_at"], "requester_confirmed_at": req["requester_confirmed_at"],
        "requesting_facility": facilities[req["requesting_facility_id"]],
        "supplying_facility": facilities[req["supplying_facility_id"]],
    }


# The order a request moves through, which is also the order its timeline reads in.
_TIMELINE_KINDS = ("sent", "accepted", "released", "received", "declined", "cancelled")


def _request_timeline(
    req: dict, names: dict[int, str], emails: dict[int, str], units: list[dict], failures: list[dict],
) -> list[dict]:
    """The request's events, DERIVED from its status and timestamps rather than stored in an events table, so they cannot
    drift out of agreement with the row. The lifecycle is small and fixed: sent, then accepted, released and received, or
    ended by declined or cancelled.

    An event is listed whenever the request's state shows it happened, even if its time or actor was never captured (a
    request from before those columns existed): `at` or `user_id` is then None and reads as "not recorded". Nothing is
    hidden because it is missing. `order_at` is where the event sits among the chat messages: its own time, or the
    previous event's when it has none.

    `account_email` is the email of the facility's one account (see get_acting_user_id), a login and not a named person;
    the facility is already named on the event. Nothing records who sent a request, so "sent" never has one. None also
    where the user id is recorded but no longer resolves to an email.

    `units` (on released) and `outcome` (on received) come from the per-unit ledger when it has rows for this request;
    otherwise units is None ("not recorded") and the outcome falls back to request_unit_failures, the only record a request
    released before the ledger existed has.
    """
    status = req["status"]
    requester, supplier = req["requesting_facility_id"], req["supplying_facility_id"]
    released = req["supplier_confirmed_at"] is not None
    present = {
        "sent": True,
        "accepted": req["accepted_at"] is not None or released or status in ("accepted", "completed", "failed"),
        "released": released,
        "received": req["requester_confirmed_at"] is not None,
        "declined": req["declined_at"] is not None or status == "declined",
        "cancelled": req["cancelled_at"] is not None or status == "cancelled",
    }
    at = {"sent": req["created_at"], "accepted": req["accepted_at"], "released": req["supplier_confirmed_at"],
          "received": req["requester_confirmed_at"], "declined": req["declined_at"], "cancelled": req["cancelled_at"]}
    actor = {"sent": None, "accepted": req["accepted_by"], "released": req["released_by"], "received": req["received_by"],
             "declined": req["declined_by"], "cancelled": req["cancelled_by"]}
    side = {"sent": requester, "accepted": supplier, "released": supplier, "received": requester, "declined": supplier,
            "cancelled": requester}

    events, previous = [], req["created_at"]
    for kind in _TIMELINE_KINDS:
        if not present[kind]:
            continue
        event = {"kind": kind, "at": at[kind], "order_at": at[kind] or previous, "facility_id": side[kind],
                 "facility_name": names.get(side[kind]),
                 "account_email": emails.get(actor[kind]) if actor[kind] is not None else None}
        if kind == "released":
            event["units"] = (
                {"count": len(units), "dins": [u["din"] for u in units],
                 "components": sorted({u["component"] for u in units})} if units else None
            )
        if kind == "received":
            if units:
                failed = [{"din": u["din"], "reason": u["reason"]} for u in units if u["outcome"] == "failed"]
                ledger = (sum(1 for u in units if u["outcome"] == "received"), len(failed))
            else:
                failed = [{"din": f["din"], "reason": f["reason"]} for f in failures]
                ledger = None
            arrived, _ = _received_counts(req["quantity"], ledger, len(failures))
            event["outcome"] = {"arrived": arrived, "failed": failed, "from_ledger": bool(units)}
        events.append(event)
        previous = event["order_at"]
    return events


# The outcome of a transfer, as the history filters and labels it. `expired_in_transit` is a filter only: partly or none.
# `declined` and `cancelled` are requests that never became transfers: they are listed only when one of them is chosen.
TRANSFER_OUTCOMES = ("received_in_full", "partly_received", "none_received", "expired_in_transit", "in_progress", "declined", "cancelled")
TRANSFERS_MAX_ROWS = 500


def _transfer_outcome(status: str, failed: Optional[int]) -> str:
    if status in ("accepted", "declined", "cancelled"):
        return "in_progress" if status == "accepted" else status
    if status == "failed":
        return "none_received"
    return "partly_received" if failed else "received_in_full"


@app.get("/transfers")
def list_transfers(
    include_active: bool = False,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    other_facility_id: Optional[int] = None,
    blood_type: Optional[str] = None,
    component: Optional[str] = None,
    outcome: Optional[str] = None,
    limit: int = TRANSFERS_MAX_ROWS,
    facility_id: int = Depends(get_acting_facility_id),
):
    """Every transfer the acting facility has been part of, in both directions, newest first: the history behind "what did
    we send Riverside in October", "how many units have we received this quarter", "which transfers had units expire in
    transit". A transfer is a request that was accepted, so the default is finished ones (completed, failed) and
    `include_active` adds accepted ones still in progress. Pending, declined and cancelled requests never became transfers,
    so they are excluded by default; choosing outcome `declined` or `cancelled` lists those requests instead (and only those),
    which is how a facility sees what it asked for and was refused. Such a row has no units: `summary.received` and
    `summary.failed` are null, and it is dated by when it was requested, not by a decision time that old rows never captured.

    `direction` is the flow of blood relative to the caller: "sent" when it was the supplier, "received" when it was the
    requester. Each row is the `request` block of GET /requests/{id} plus its `summary` (asked, received, failed), so the
    history and the receipt cannot disagree; the unit-level detail stays behind the receipt.

    The date is when the transfer happened: receipt confirmed for a finished one, release for one in progress that has been
    released, otherwise when it was requested. The range is in Manila business dates (date_from and date_to inclusive) and
    is applied as instants, so no SQL date function and no viewer time zone is involved. The filters other than the
    status scope apply to `transfers` and `totals`; `other_facilities` (for the facility filter) lists every facility this one
    has dealt with in any request, so no filter ever empties the list of choices. `total` counts everything matching; `transfers` holds at most `limit`
    (up to TRANSFERS_MAX_ROWS) and `truncated` says when rows were left out. `totals` cover everything matching, not just
    the rows returned.
    """
    if blood_type is not None and blood_type not in VALID_BLOOD_TYPES:
        raise HTTPException(status_code=422, detail=f"blood_type must be one of {sorted(VALID_BLOOD_TYPES)}")
    if component is not None and (error := _component_error(component)) is not None:
        raise HTTPException(status_code=422, detail=error)
    if outcome is not None and outcome not in TRANSFER_OUTCOMES:
        raise HTTPException(status_code=422, detail=f"outcome must be one of {list(TRANSFER_OUTCOMES)}")
    if date_from is not None and date_to is not None and date_from > date_to:
        raise HTTPException(status_code=422, detail="date_from must not be after date_to")
    limit = max(1, min(limit, TRANSFERS_MAX_ROWS))
    statuses = [outcome] if outcome in ("declined", "cancelled") else ["completed", "failed"] + (["accepted"] if include_active else [])

    with engine.connect() as conn:
        rows = [dict(r) for r in conn.execute(
            text(
                """
                SELECT * FROM requests
                WHERE (requesting_facility_id = :f OR supplying_facility_id = :f) AND status = ANY(:statuses)
                """
            ),
            {"f": facility_id, "statuses": statuses},
        ).mappings().all()]
        ids = [r["id"] for r in rows]
        ledger = {r["request_id"]: (r["received"], r["failed"]) for r in conn.execute(
            text(
                "SELECT request_id, count(*) FILTER (WHERE outcome = 'received') AS received, "
                "count(*) FILTER (WHERE outcome = 'failed') AS failed FROM request_units "
                "WHERE request_id = ANY(:ids) GROUP BY request_id"
            ),
            {"ids": ids},
        ).mappings().all()} if ids else {}
        failure_rows = dict(conn.execute(
            text("SELECT request_id, count(*) FROM request_unit_failures WHERE request_id = ANY(:ids) GROUP BY request_id"),
            {"ids": ids},
        ).all()) if ids else {}
        # Every facility this one has dealt with, in any request, so the facility filter's choices never depend on which
        # status scope or outcome is chosen (and a chosen facility is always still in the list).
        dealt_with = [r[0] for r in conn.execute(
            text(
                "SELECT DISTINCT CASE WHEN supplying_facility_id = :f THEN requesting_facility_id ELSE supplying_facility_id END "
                "FROM requests WHERE requesting_facility_id = :f OR supplying_facility_id = :f"
            ),
            {"f": facility_id},
        ).all()]
        facility_ids = sorted({r[k] for r in rows for k in ("requesting_facility_id", "supplying_facility_id")} | set(dealt_with))
        facilities = {
            f["id"]: dict(f) for f in conn.execute(
                text("SELECT id, name, facility_type, address FROM facilities WHERE id = ANY(:ids)"),
                {"ids": facility_ids},
            ).mappings().all()
        } if facility_ids else {}

    def row_for(req: dict) -> dict:
        sent = req["supplying_facility_id"] == facility_id
        other = req["requesting_facility_id"] if sent else req["supplying_facility_id"]
        if req["status"] in ("declined", "cancelled"):
            received = failed = None  # nothing was ever released, so there is nothing to count: not 0
            happened = req["created_at"]
        elif req["status"] == "accepted":
            received = failed = None
            happened = req["supplier_confirmed_at"] or req["created_at"]
        else:
            received, failed = _received_counts(req["quantity"], ledger.get(req["id"]), failure_rows.get(req["id"], 0))
            happened = req["requester_confirmed_at"] or req["supplier_confirmed_at"] or req["created_at"]
        return {
            "request": _request_block(req, facilities), "direction": "sent" if sent else "received", "other_facility_id": other,
            "transfer_at": happened, "outcome": _transfer_outcome(req["status"], failed),
            "summary": {"asked": req["quantity"], "received": received, "failed": failed},
        }

    everything = [row_for(r) for r in rows]
    other_facilities = sorted(
        ({"id": fid, "name": facilities[fid]["name"]} for fid in dealt_with), key=lambda f: f["name"].lower()
    )

    start = datetime.combine(date_from, datetime.min.time(), tzinfo=BUSINESS_TZ) if date_from else None
    end = datetime.combine(date_to + timedelta(days=1), datetime.min.time(), tzinfo=BUSINESS_TZ) if date_to else None

    def keep(t: dict) -> bool:
        r = t["request"]
        return (
            (other_facility_id is None or t["other_facility_id"] == other_facility_id)
            and (blood_type is None or r["blood_type"] == blood_type)
            and (component is None or r["component"] == component)
            and (start is None or t["transfer_at"] >= start)
            and (end is None or t["transfer_at"] < end)
            and (outcome is None or t["outcome"] == outcome
                 or (outcome == "expired_in_transit" and t["outcome"] in ("partly_received", "none_received")))
        )

    matching = sorted((t for t in everything if keep(t)), key=lambda t: (t["transfer_at"], t["request"]["id"]), reverse=True)
    settled = [t for t in matching if t["summary"]["received"] is not None]
    return {
        "transfers": matching[:limit],
        "total": len(matching),
        "truncated": len(matching) > limit,
        "other_facilities": other_facilities,
        "totals": {
            "transfers": len(matching),
            "units_sent": sum(t["summary"]["received"] for t in settled if t["direction"] == "sent"),
            "units_received": sum(t["summary"]["received"] for t in settled if t["direction"] == "received"),
            "units_failed": sum(t["summary"]["failed"] for t in settled),
            "transfers_with_expired_units": sum(1 for t in settled if t["summary"]["failed"]),
        },
    }


@app.get("/requests/{request_id}")
def get_request_detail(request_id: int, facility_id: int = Depends(get_acting_facility_id)):
    """One request in full, for its chat timeline and its receipt: the request, both facilities, the derived events
    (see _request_timeline), and the per-unit ledger. Participants only, like the chat. The chat re-fetches this alongside
    its messages, so the timeline is as fresh as the thread.

    The response shape is decided once here: the timeline and the receipt render the same `timeline` and `units`, and both
    facilities get exactly the same content (nothing is shaped by who is asking, beyond who may ask).
    `message_count` is how many chat messages the request holds; the receipt states the count without reproducing them.
    `units_recorded` is False for a request released before the ledger existed, in which case `units` is empty and the
    receipt says so instead of hiding the section."""
    with engine.connect() as conn:
        req = _require_request_participant(conn, request_id, facility_id)
        facilities = {
            r["id"]: dict(r) for r in conn.execute(
                text("SELECT id, name, facility_type, address FROM facilities WHERE id IN (:a, :b)"),
                {"a": req["requesting_facility_id"], "b": req["supplying_facility_id"]},
            ).mappings().all()
        }
        units = [dict(r) for r in conn.execute(
            text(
                """
                SELECT din, blood_type, component, volume_ml, collected_date, expires_date, origin_location,
                       days_remaining_at_release, outcome, reason, days_remaining_at_receipt
                FROM request_units WHERE request_id = :r ORDER BY din
                """
            ),
            {"r": request_id},
        ).mappings().all()]
        failures = [dict(r) for r in conn.execute(
            text("SELECT din, reason FROM request_unit_failures WHERE request_id = :r ORDER BY din"), {"r": request_id}
        ).mappings().all()]
        actor_ids = [req[k] for k in ("accepted_by", "declined_by", "cancelled_by", "released_by", "received_by") if req[k] is not None]
        emails = dict(conn.execute(
            text("SELECT id, email FROM users WHERE id = ANY(:ids)"), {"ids": actor_ids}
        ).all()) if actor_ids else {}
        message_count = conn.execute(
            text("SELECT count(*) FROM request_messages WHERE request_id = :r"), {"r": request_id}
        ).scalar()
    timeline = _request_timeline(req, {fid: f["name"] for fid, f in facilities.items()}, emails, units, failures)
    received = next((e["outcome"] for e in timeline if e["kind"] == "received"), None)
    return {
        "request": _request_block(req, facilities),
        "timeline": timeline,
        "units": units,
        "units_recorded": bool(units),
        "message_count": message_count,
        "summary": {
            "asked": req["quantity"],
            "received": received["arrived"] if received else None,
            "failed": len(received["failed"]) if received else None,
        },
    }


@app.get("/requests/{request_id}/messages")
def list_request_messages(request_id: int, facility_id: int = Depends(get_acting_facility_id)):
    """Real coordination-chat history for one request/transfer — replaces
    the frontend's old hardcoded chatMessages mock. Polled by the frontend
    rather than pushed; there's no websocket layer in this app."""
    with engine.connect() as conn:
        _require_request_participant(conn, request_id, facility_id)
        rows = conn.execute(
            text(
                """
                SELECT m.id, m.request_id, m.sender_facility_id, f.name AS sender_facility_name,
                       m.message, m.created_at
                FROM request_messages m
                JOIN facilities f ON f.id = m.sender_facility_id
                WHERE m.request_id = :request_id
                ORDER BY m.created_at ASC, m.id ASC
                """
            ),
            {"request_id": request_id},
        ).mappings().all()
    return [dict(row) for row in rows]


@app.post("/requests/{request_id}/messages")
def send_request_message(
    request_id: int, body: SendRequestMessageBody, facility_id: int = Depends(get_acting_facility_id)
):
    # Limited together with POST /conversations/{id}/messages (one allowance per facility, or the old endpoint would be the way round it).
    keys = [f"fac:{facility_id}"]
    _raise_if_limited(_MESSAGE_LIMITER, keys, "You are sending messages too quickly.")
    _record_attempt(_MESSAGE_LIMITER, keys)
    # The same table as the conversations: once the migration is in, the message also names its conversation (made if need be), so it
    # shows in the pair's thread at once; before it, the old insert, unchanged (and the new code reads such a row by its request).
    conversations = _conversations_ready()
    with engine.begin() as conn:
        req = _require_request_participant(conn, request_id, facility_id)
        # Coordination chat is for coordinating an actual transfer — sending
        # (not reading: history stays visible either way) is gated to
        # accepted/completed/failed so there's no window where one side can send a
        # message the other side has no way to see or respond to yet (the
        # requester's own view only surfaces a request's chat once it's
        # accepted).
        if req["status"] not in ("accepted", "completed", "failed"):
            raise HTTPException(
                status_code=400,
                detail=f"cannot send a message on a request with status {req['status']!r} — only accepted, completed or failed requests support messaging",
            )
        if conversations:
            other = req["supplying_facility_id"] if req["requesting_facility_id"] == facility_id else req["requesting_facility_id"]
            row = conn.execute(
                text(
                    """
                    INSERT INTO request_messages (conversation_id, request_id, sender_facility_id, message)
                    VALUES (:cid, :request_id, :facility_id, :message)
                    RETURNING id, request_id, sender_facility_id, message, created_at
                    """
                ),
                {"cid": _ensure_conversation(conn, facility_id, other), "request_id": request_id, "facility_id": facility_id, "message": body.message},
            ).mappings().first()
        else:
            row = conn.execute(
                text(
                    """
                    INSERT INTO request_messages (request_id, sender_facility_id, message)
                    VALUES (:request_id, :facility_id, :message)
                    RETURNING id, request_id, sender_facility_id, message, created_at
                    """
                ),
                {"request_id": request_id, "facility_id": facility_id, "message": body.message},
            ).mappings().first()
        sender_name = conn.execute(
            text("SELECT name FROM facilities WHERE id = :id"), {"id": facility_id}
        ).scalar()
    return {**dict(row), "sender_facility_name": sender_name}


# ─── Conversations (one per pair of facilities) ─────────────────────────────
# The Messages redesign, stage 1 (server only). A conversation is the whole exchange between two facilities, whatever requests exist between
# them: the messages and, merged in time order, the status events of every request between the pair. Until now a chat was one per request
# (GET/POST /requests/{id}/messages, kept below unchanged for the current frontend); both write the same table, request_messages.
#
#   - Lazy rows. A `conversations` row (one per pair, smaller facility id first) is created by the first message sent or the first time a
#     thread is opened (POST /conversations/{id}/read), NEVER by create_request or a transition: those do not touch the table, so a
#     missing row can never break one. Every read builds the pair list from the union of requests and conversations, so a pair with
#     requests and no row still appears, and a message whose conversation_id is NULL (an old server instance, or a request's chat before the
#     backfill) is read by way of its request's pair.
#   - Who can see what. Every endpoint is the CALLER's view of the pair (caller, other_facility_id): a third facility asking about a pair it
#     is not in is simply asking about its own pair with someone, which does not exist (404). Nothing here takes both facility ids.
#   - Messaging is allowed whenever the pair has at least one request (any status). The old per-request gate (accepted, completed or failed)
#     existed so one side could not write something the other could not see; in a conversation both sides see it.
#   - Unread is a per-conversation count of the OTHER facility's messages after my read position (conversation_reads, one row per facility per
#     conversation, moved forward only, only my own row). "Needs action" mirrors the frontend's rowActions / needsAction: the nav badge's
#     rule, computed once here in SQL (_ACTION_SQL) and once in Python (_request_needs_my_action), and a test asserts they agree.
#   - Rolling out. Run server/migrate_add_conversations.sql first. A server that comes up before it keeps working: _conversations_ready()
#     says whether the tables exist (asked at most every CONVERSATIONS_RECHECK_SECONDS until it is true, then remembered), GET /sync omits the
#     conversation parts of its statement until then, the old message endpoint writes the old way, and the new endpoints answer 503.
CONVERSATION_FINISHED_PAGE = 5          # finished requests (completed, failed, declined, cancelled) shown before "show earlier"
CONVERSATION_FINISHED_MAX_PAGE = 50
CONVERSATION_MESSAGES_MAX = 500         # the newest this many messages of the window; `messages_truncated` says when some were left out
CONVERSATION_PREVIEW_CHARS = 200        # the list's last-message text is cut to this
MESSAGE_SEND_LIMIT = 30                 # messages per facility per window, across the new and the old send endpoint
MESSAGE_SEND_WINDOW_SECONDS = 60
CONVERSATIONS_RECHECK_SECONDS = 30
OPEN_REQUEST_STATUSES = ("pending", "accepted")

_MESSAGE_LIMITER = auth.AttemptLimiter(limit=MESSAGE_SEND_LIMIT, window=MESSAGE_SEND_WINDOW_SECONDS)

_conversations_seen = False
_conversations_checked_at: Optional[float] = None


@retry_read
def _conversations_ready() -> bool:
    """True once server/migrate_add_conversations.sql has been applied (both tables and the column exist). A positive answer is remembered
    for the life of the process; a negative one is asked again after CONVERSATIONS_RECHECK_SECONDS, so a deploy that went out before the
    migration costs one extra statement every half minute, says so in the log, and starts using the tables by itself once they exist."""
    global _conversations_seen, _conversations_checked_at
    if _conversations_seen:
        return True
    now = time.monotonic()
    if _conversations_checked_at is not None and now - _conversations_checked_at < CONVERSATIONS_RECHECK_SECONDS:
        return False
    with read_engine.connect() as conn:
        ready = bool(conn.execute(
            text(
                "SELECT to_regclass('public.conversations') IS NOT NULL AND to_regclass('public.conversation_reads') IS NOT NULL "
                "AND EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' "
                "AND table_name = 'request_messages' AND column_name = 'conversation_id')"
            )
        ).scalar())
    _conversations_checked_at = now
    if ready:
        _conversations_seen = True
    else:
        print("[conversations] the conversation tables are missing (run server/migrate_add_conversations.sql): the Messages endpoints answer 503")
    return ready


def _require_conversations() -> None:
    if not _conversations_ready():
        raise HTTPException(status_code=503, detail="messages are not available yet: the conversations migration has not been run on this database")


def _pair(a: int, b: int) -> tuple[int, int]:
    """The two facility ids as a conversation stores them: smaller first."""
    return (a, b) if a < b else (b, a)


# This facility has a move to make on the request: accept or decline an incoming one, confirm release of an accepted incoming one, confirm
# receipt of an accepted outgoing one that was released. (Cancelling one's own pending request is not a move waiting on this facility.)
# The same rule as needsAction / rowActions in src/app/lib/requestInbox.ts; _request_needs_my_action is the Python form of it.
_ACTION_SQL = """(
    (supplying_facility_id = :me AND (status = 'pending' OR (status = 'accepted' AND supplier_confirmed_at IS NULL)))
    OR (requesting_facility_id = :me AND status = 'accepted' AND supplier_confirmed_at IS NOT NULL AND requester_confirmed_at IS NULL)
)"""


def _request_needs_my_action(req: dict, me: int) -> bool:
    if req["supplying_facility_id"] == me:
        return req["status"] == "pending" or (req["status"] == "accepted" and req["supplier_confirmed_at"] is None)
    if req["requesting_facility_id"] == me:
        return req["status"] == "accepted" and req["supplier_confirmed_at"] is not None and req["requester_confirmed_at"] is None
    return False


# Everything the list needs, in ONE statement: the pairs (requests and conversation rows united), each pair's request counts, last message,
# my unread count and last activity. A message belongs to the conversation named by its conversation_id, or, when that is NULL, to its
# request's pair.
_CONVERSATION_LIST_SQL = f"""
    WITH my_requests AS (
        SELECT r.*, CASE WHEN r.requesting_facility_id = :me THEN r.supplying_facility_id ELSE r.requesting_facility_id END AS other_id
        FROM requests r WHERE r.requesting_facility_id = :me OR r.supplying_facility_id = :me
    ), my_conversations AS (
        SELECT c.id AS conversation_id, c.created_at AS conversation_created_at,
               CASE WHEN c.facility_low_id = :me THEN c.facility_high_id ELSE c.facility_low_id END AS other_id
        FROM conversations c WHERE c.facility_low_id = :me OR c.facility_high_id = :me
    ), my_messages AS (
        SELECT m.id, m.sender_facility_id, m.message, m.created_at, COALESCE(mc.other_id, mr.other_id) AS other_id
        FROM request_messages m
        LEFT JOIN my_conversations mc ON mc.conversation_id = m.conversation_id
        LEFT JOIN my_requests mr ON mr.id = m.request_id AND m.conversation_id IS NULL
        WHERE mc.other_id IS NOT NULL OR mr.other_id IS NOT NULL
    ), pairs AS (
        SELECT other_id FROM my_requests UNION SELECT other_id FROM my_conversations
    ), request_totals AS (
        SELECT other_id, count(*) AS request_count,
               count(*) FILTER (WHERE status IN ('pending', 'accepted')) AS open_count,
               count(*) FILTER (WHERE {_ACTION_SQL}) AS action_count,
               max(GREATEST(created_at, accepted_at, declined_at, cancelled_at, supplier_confirmed_at, requester_confirmed_at)) AS last_request_at
        FROM my_requests GROUP BY other_id
    ), last_message AS (
        SELECT DISTINCT ON (other_id) other_id, id, sender_facility_id, message, created_at FROM my_messages ORDER BY other_id, id DESC
    ), my_read AS (
        SELECT mc.other_id, cr.last_read_message_id
        FROM my_conversations mc JOIN conversation_reads cr ON cr.conversation_id = mc.conversation_id AND cr.facility_id = :me
    ), unread AS (
        SELECT mm.other_id, count(*) AS unread
        FROM my_messages mm LEFT JOIN my_read r ON r.other_id = mm.other_id
        WHERE mm.sender_facility_id <> :me AND mm.id > COALESCE(r.last_read_message_id, 0)
        GROUP BY mm.other_id
    )
    SELECT p.other_id AS facility_id, f.name, f.facility_type, f.is_active,
           lm.id AS last_message_id, lm.sender_facility_id AS last_message_sender_id,
           left(lm.message, {CONVERSATION_PREVIEW_CHARS}) AS last_message_text, lm.created_at AS last_message_at,
           COALESCE(u.unread, 0) AS unread, COALESCE(rt.action_count, 0) AS action_count,
           COALESCE(rt.open_count, 0) AS open_count, COALESCE(rt.request_count, 0) AS request_count,
           COALESCE(GREATEST(lm.created_at, rt.last_request_at), mc.conversation_created_at) AS last_activity_at
    FROM pairs p
    JOIN facilities f ON f.id = p.other_id
    LEFT JOIN request_totals rt ON rt.other_id = p.other_id
    LEFT JOIN last_message lm ON lm.other_id = p.other_id
    LEFT JOIN unread u ON u.other_id = p.other_id
    LEFT JOIN my_conversations mc ON mc.other_id = p.other_id
    ORDER BY (COALESCE(rt.action_count, 0) > 0) DESC, last_activity_at DESC NULLS LAST, p.other_id
"""


@app.get("/conversations")
@retry_read
def list_conversations(facility_id: int = Depends(get_acting_facility_id)):
    """One row per other facility this one has a request or a conversation with: the facility, the last message (cut to
    CONVERSATION_PREVIEW_CHARS characters), how many of the other facility's messages are unread after my read position, how many
    requests need MY action (the nav badge's rule), how many are open (pending or accepted), and the last activity (the newer of the last
    message and a request moving; the conversation row's creation only for a pair with neither, so opening a thread never reorders the list). Ordered with a pair that needs my action first, then by latest activity, newest first.
    `needs_action` at the top is the sum, the same figure the Requests tab badge shows. A pure read, one statement, on the read engine."""
    _require_conversations()
    with read_engine.connect() as conn:
        rows = conn.execute(text(_CONVERSATION_LIST_SQL), {"me": facility_id}).mappings().all()
    conversations = [
        {
            "facility": {"id": r["facility_id"], "name": r["name"], "facility_type": r["facility_type"], "is_active": r["is_active"]},
            "last_message": None if r["last_message_id"] is None else {
                "id": r["last_message_id"], "sender_facility_id": r["last_message_sender_id"], "text": r["last_message_text"],
                "created_at": r["last_message_at"],
            },
            "unread": r["unread"],
            "needs_action": r["action_count"],
            "open_requests": r["open_count"],
            "requests_total": r["request_count"],
            "last_activity_at": r["last_activity_at"],
        }
        for r in rows
    ]
    return {"conversations": conversations, "needs_action": sum(c["needs_action"] for c in conversations)}


# The pair's own facts in one statement: both facilities, the conversation row (if any), my read position, my unread count and the newest
# message id. `conv` is empty for a pair that has requests and no row yet; the messages of such a pair are found by their request.
_CONVERSATION_HEAD_SQL = """
    WITH conv AS (SELECT id FROM conversations WHERE facility_low_id = :low AND facility_high_id = :high),
    pair_requests AS (
        SELECT id FROM requests
        WHERE (requesting_facility_id = :low AND supplying_facility_id = :high) OR (requesting_facility_id = :high AND supplying_facility_id = :low)
    ), pair_messages AS (
        SELECT m.id, m.sender_facility_id FROM request_messages m
        WHERE m.conversation_id IN (SELECT id FROM conv) OR (m.conversation_id IS NULL AND m.request_id IN (SELECT id FROM pair_requests))
    ), pos AS (
        SELECT COALESCE(max(cr.last_read_message_id), 0) AS p FROM conversation_reads cr
        WHERE cr.facility_id = :me AND cr.conversation_id IN (SELECT id FROM conv)
    )
    SELECT f.id, f.name, f.facility_type, f.address, f.is_active,
           (SELECT id FROM conv) AS conversation_id,
           (SELECT p FROM pos) AS read_position,
           (SELECT count(*) FROM pair_messages pm WHERE pm.sender_facility_id <> :me AND pm.id > (SELECT p FROM pos)) AS unread,
           (SELECT COALESCE(max(id), 0) FROM pair_messages) AS last_message_id
    FROM facilities f WHERE f.id IN (:me, :other)
"""


@app.get("/conversations/{other_facility_id}")
@retry_read
def get_conversation(
    other_facility_id: int,
    finished_limit: int = CONVERSATION_FINISHED_PAGE,
    finished_before: Optional[int] = None,
    facility_id: int = Depends(get_acting_facility_id),
):
    """The conversation between the caller's facility and `other_facility_id`, merged in time order: the messages and the status events of
    every request between them (`thread`), plus each request's card data (`requests`: the request block, who did what and when, the units
    released and received, whether it needs my action). 404 when the pair has no request and no conversation (which is all a third
    facility can ever see of someone else's pair: it is asking about its own pair with the other).

    Open requests (pending, accepted) are always in the first response. Finished ones (completed, failed, declined, cancelled) come as the
    newest `finished_limit` (default 5, at most 50); `finished.has_more` says there are older ones, and `finished_before` = the oldest finished
    request id already held returns the next page of older ones (only finished requests, with the messages and events of that time window).
    Messages are limited to the window of the requests shown: from the oldest shown request's creation, or all of them when no finished
    request is held back, at most CONVERSATION_MESSAGES_MAX (`messages_truncated`).

    A pure read on the read engine, six statements, and it never creates the conversation row: opening the thread is POST
    /conversations/{id}/read, which does."""
    _require_conversations()
    if other_facility_id == facility_id:
        raise HTTPException(status_code=400, detail="a conversation is between two different facilities")
    finished_limit = max(1, min(finished_limit, CONVERSATION_FINISHED_MAX_PAGE))
    low, high = _pair(facility_id, other_facility_id)
    with read_engine.connect() as conn:
        heads = conn.execute(text(_CONVERSATION_HEAD_SQL), {"me": facility_id, "other": other_facility_id, "low": low, "high": high}).mappings().all()
        facilities = {h["id"]: {"id": h["id"], "name": h["name"], "facility_type": h["facility_type"], "address": h["address"]} for h in heads}
        if other_facility_id not in facilities:
            raise HTTPException(status_code=404, detail="no conversation with this facility")
        head = next(h for h in heads if h["id"] == other_facility_id)
        reqs = [dict(r) for r in conn.execute(
            text(
                "SELECT * FROM requests WHERE (requesting_facility_id = :a AND supplying_facility_id = :b) "
                "OR (requesting_facility_id = :b AND supplying_facility_id = :a) ORDER BY id"
            ),
            {"a": facility_id, "b": other_facility_id},
        ).mappings().all()]
        if not reqs and head["conversation_id"] is None:
            raise HTTPException(status_code=404, detail="no conversation with this facility")

        open_reqs = [r for r in reqs if r["status"] in OPEN_REQUEST_STATUSES]
        finished = sorted((r for r in reqs if r["status"] not in OPEN_REQUEST_STATUSES), key=lambda r: r["id"], reverse=True)
        until = None
        if finished_before is None:
            page = finished[:finished_limit]
            shown = open_reqs + page
            more = len(finished) > len(page)
        else:
            cursor = next((r for r in reqs if r["id"] == finished_before), None)
            if cursor is None:
                raise HTTPException(status_code=400, detail="finished_before is not a request of this conversation")
            older = [r for r in finished if r["id"] < finished_before]
            page = older[:finished_limit]
            shown = page
            more = len(older) > len(page)
            until = cursor["created_at"]
        since = min((r["created_at"] for r in shown), default=None) if more else None

        shown_ids = [r["id"] for r in shown]
        units, failures, emails = [], [], {}
        if shown_ids:
            units = [dict(r) for r in conn.execute(
                text(
                    "SELECT request_id, din, blood_type, component, volume_ml, collected_date, expires_date, origin_location, "
                    "days_remaining_at_release, outcome, reason, days_remaining_at_receipt FROM request_units "
                    "WHERE request_id = ANY(:ids) ORDER BY request_id, din"
                ),
                {"ids": shown_ids},
            ).mappings().all()]
            failures = [dict(r) for r in conn.execute(
                text("SELECT request_id, din, reason FROM request_unit_failures WHERE request_id = ANY(:ids) ORDER BY request_id, din"),
                {"ids": shown_ids},
            ).mappings().all()]
            actor_ids = sorted({r[k] for r in shown for k in ("accepted_by", "declined_by", "cancelled_by", "released_by", "received_by") if r[k] is not None})
            if actor_ids:
                emails = dict(conn.execute(text("SELECT id, email FROM users WHERE id = ANY(:ids)"), {"ids": actor_ids}).all())
        messages = [dict(m) for m in conn.execute(
            text(
                """
                SELECT m.id, m.request_id, m.sender_facility_id, f.name AS sender_facility_name, m.message, m.created_at
                FROM request_messages m JOIN facilities f ON f.id = m.sender_facility_id
                WHERE (m.conversation_id = :cid OR (m.conversation_id IS NULL AND m.request_id IN (
                          SELECT id FROM requests
                          WHERE (requesting_facility_id = :low AND supplying_facility_id = :high) OR (requesting_facility_id = :high AND supplying_facility_id = :low))))
                  AND (CAST(:since AS timestamptz) IS NULL OR m.created_at >= :since)
                  AND (CAST(:until AS timestamptz) IS NULL OR m.created_at < :until)
                ORDER BY m.created_at DESC, m.id DESC LIMIT :cap
                """
            ),
            {"cid": head["conversation_id"], "low": low, "high": high, "since": since, "until": until, "cap": CONVERSATION_MESSAGES_MAX + 1},
        ).mappings().all()]
    truncated = len(messages) > CONVERSATION_MESSAGES_MAX
    messages = list(reversed(messages[:CONVERSATION_MESSAGES_MAX]))

    names = {fid: f["name"] for fid, f in facilities.items()}
    by_request_units: dict[int, list[dict]] = {}
    for u in units:
        by_request_units.setdefault(u["request_id"], []).append(u)
    by_request_failures: dict[int, list[dict]] = {}
    for f in failures:
        by_request_failures.setdefault(f["request_id"], []).append(f)

    cards, items = [], []
    for req in sorted(shown, key=lambda r: r["id"]):
        ledger = by_request_units.get(req["id"], [])
        timeline = _request_timeline(req, names, emails, ledger, by_request_failures.get(req["id"], []))
        received = next((e["outcome"] for e in timeline if e["kind"] == "received"), None)
        cards.append({
            "request": _request_block(req, facilities),
            "direction": "outgoing" if req["requesting_facility_id"] == facility_id else "incoming",
            "needs_my_action": _request_needs_my_action(req, facility_id),
            "timeline": timeline,
            "units": [{k: v for k, v in u.items() if k != "request_id"} for u in ledger],
            "units_recorded": bool(ledger),
            "summary": {
                "asked": req["quantity"],
                "received": received["arrived"] if received else None,
                "failed": len(received["failed"]) if received else None,
            },
        })
        for event in timeline:
            items.append({"type": "event", "request_id": req["id"], **event})
    for m in messages:
        items.append({"type": "message", **m})
    # Time order: an event at its own time (`order_at`, the previous event's when it has none), a message at its creation. At the same
    # instant an event comes before a message, then by request id or message id; a request's events keep their lifecycle order (stable sort).
    items.sort(key=lambda i: (i["order_at"], 0, i["request_id"]) if i["type"] == "event" else (i["created_at"], 1, i["id"]))

    oldest_finished_shown = page[-1]["id"] if page else None
    return {
        "facility": facilities[other_facility_id],
        "conversation_id": head["conversation_id"],
        "read_position": head["read_position"],
        "unread": head["unread"],
        "last_message_id": head["last_message_id"],
        "counts": {
            "open_requests": len(open_reqs),
            "needs_action": sum(1 for r in reqs if _request_needs_my_action(r, facility_id)),
            "requests_total": len(reqs),
        },
        "requests": cards,
        "thread": items,
        "finished": {
            "total": len(finished), "shown": len(page), "has_more": more,
            "next_cursor": oldest_finished_shown if more else None,
        },
        "messages_truncated": truncated,
    }


def _pair_has_request(conn, a: int, b: int) -> bool:
    return conn.execute(
        text(
            "SELECT 1 FROM requests WHERE (requesting_facility_id = :a AND supplying_facility_id = :b) "
            "OR (requesting_facility_id = :b AND supplying_facility_id = :a) LIMIT 1"
        ),
        {"a": a, "b": b},
    ).first() is not None


def _ensure_conversation(conn, a: int, b: int) -> int:
    """The conversation row of the pair, made if it does not exist yet. Safe under a race: the unique pair constraint decides, and the
    loser reads the winner's row."""
    low, high = _pair(a, b)
    row = conn.execute(
        text(
            "INSERT INTO conversations (facility_low_id, facility_high_id) VALUES (:low, :high) "
            "ON CONFLICT (facility_low_id, facility_high_id) DO NOTHING RETURNING id"
        ),
        {"low": low, "high": high},
    ).first()
    if row is not None:
        return row[0]
    return conn.execute(
        text("SELECT id FROM conversations WHERE facility_low_id = :low AND facility_high_id = :high"), {"low": low, "high": high}
    ).scalar()


class SendConversationMessageBody(SendRequestMessageBody):
    """`message` is trimmed, non-blank, at most 2000 characters (SendRequestMessageBody). `request_id`, when given, says which request the
    message is about; it must be a request between the caller and the other facility."""
    request_id: Optional[int] = None


@app.post("/conversations/{other_facility_id}/messages")
def send_conversation_message(
    other_facility_id: int, body: SendConversationMessageBody, facility_id: int = Depends(get_acting_facility_id),
):
    """Send a message to another facility, optionally about one of the requests between them. Allowed whenever the two have at least one
    request, whatever its status. Creates the conversation row if this is the first message. At most MESSAGE_SEND_LIMIT messages a minute
    per facility (429 with Retry-After), counted whatever happens to the message, across this endpoint and POST /requests/{id}/messages."""
    _require_conversations()
    if other_facility_id == facility_id:
        raise HTTPException(status_code=400, detail="a conversation is between two different facilities")
    keys = [f"fac:{facility_id}"]
    _raise_if_limited(_MESSAGE_LIMITER, keys, "You are sending messages too quickly.")
    _record_attempt(_MESSAGE_LIMITER, keys)
    with engine.begin() as conn:
        other = conn.execute(
            text("SELECT id, name, is_active FROM facilities WHERE id = :id"), {"id": other_facility_id}
        ).mappings().first()
        # An unknown facility and a facility with no request in common read the same, so this cannot be used to look facilities up.
        if other is None or not _pair_has_request(conn, facility_id, other_facility_id):
            raise HTTPException(status_code=404, detail="no conversation with this facility")
        if body.request_id is not None:
            about = conn.execute(
                text(
                    "SELECT 1 FROM requests WHERE id = :r AND ((requesting_facility_id = :a AND supplying_facility_id = :b) "
                    "OR (requesting_facility_id = :b AND supplying_facility_id = :a))"
                ),
                {"r": body.request_id, "a": facility_id, "b": other_facility_id},
            ).first()
            if about is None:
                raise HTTPException(status_code=400, detail="request_id is not a request between you and this facility")
        if not other["is_active"]:
            raise HTTPException(status_code=400, detail="this facility is not currently accepting messages")
        cid = _ensure_conversation(conn, facility_id, other_facility_id)
        row = conn.execute(
            text(
                """
                INSERT INTO request_messages (conversation_id, request_id, sender_facility_id, message)
                VALUES (:cid, :request_id, :facility_id, :message)
                RETURNING id, conversation_id, request_id, sender_facility_id, message, created_at
                """
            ),
            {"cid": cid, "request_id": body.request_id, "facility_id": facility_id, "message": body.message},
        ).mappings().first()
        sender_name = conn.execute(text("SELECT name FROM facilities WHERE id = :id"), {"id": facility_id}).scalar()
    return {**dict(row), "sender_facility_name": sender_name}


class MarkConversationReadBody(BaseModel):
    up_to_message_id: Optional[int] = Field(default=None, ge=0)


@app.post("/conversations/{other_facility_id}/read")
def mark_conversation_read(
    other_facility_id: int, body: Optional[MarkConversationReadBody] = None, facility_id: int = Depends(get_acting_facility_id),
):
    """Move MY read position in this conversation forward: to `up_to_message_id` (what the screen actually showed; never beyond the newest
    message there is) or, with no body, to the newest message. Forward only (a smaller value changes nothing) and only my own row
    (facility_id comes from the token, never the body). Creates the conversation row if this is the first time the thread is opened, which
    is when the conversation row is made when no message has been sent yet."""
    _require_conversations()
    if other_facility_id == facility_id:
        raise HTTPException(status_code=400, detail="a conversation is between two different facilities")
    with engine.begin() as conn:
        low, high = _pair(facility_id, other_facility_id)
        exists = conn.execute(text("SELECT 1 FROM facilities WHERE id = :id"), {"id": other_facility_id}).first() is not None
        has_row = conn.execute(
            text("SELECT 1 FROM conversations WHERE facility_low_id = :low AND facility_high_id = :high"), {"low": low, "high": high}
        ).first() is not None
        if not exists or not (has_row or _pair_has_request(conn, facility_id, other_facility_id)):
            raise HTTPException(status_code=404, detail="no conversation with this facility")
        cid = _ensure_conversation(conn, facility_id, other_facility_id)
        latest = conn.execute(
            text(
                """
                SELECT COALESCE(max(m.id), 0) FROM request_messages m
                WHERE m.conversation_id = :cid OR (m.conversation_id IS NULL AND m.request_id IN (
                      SELECT id FROM requests
                      WHERE (requesting_facility_id = :low AND supplying_facility_id = :high) OR (requesting_facility_id = :high AND supplying_facility_id = :low)))
                """
            ),
            {"cid": cid, "low": low, "high": high},
        ).scalar()
        target = latest if body is None or body.up_to_message_id is None else min(body.up_to_message_id, latest)
        position = conn.execute(
            text(
                """
                INSERT INTO conversation_reads (conversation_id, facility_id, last_read_message_id)
                VALUES (:cid, :me, :target)
                ON CONFLICT (conversation_id, facility_id) DO UPDATE
                SET last_read_message_id = GREATEST(conversation_reads.last_read_message_id, EXCLUDED.last_read_message_id),
                    updated_at = CASE WHEN EXCLUDED.last_read_message_id > conversation_reads.last_read_message_id
                                      THEN now() ELSE conversation_reads.updated_at END
                RETURNING last_read_message_id
                """
            ),
            {"cid": cid, "me": facility_id, "target": target},
        ).scalar()
        unread = conn.execute(
            text(
                """
                SELECT count(*) FROM request_messages m
                WHERE m.sender_facility_id <> :me AND m.id > :pos
                  AND (m.conversation_id = :cid OR (m.conversation_id IS NULL AND m.request_id IN (
                      SELECT id FROM requests
                      WHERE (requesting_facility_id = :low AND supplying_facility_id = :high) OR (requesting_facility_id = :high AND supplying_facility_id = :low))))
                """
            ),
            {"cid": cid, "me": facility_id, "pos": position, "low": low, "high": high},
        ).scalar()
    return {"conversation_id": cid, "last_read_message_id": position, "unread": unread}




# ─── Auth (Step 6A) ─────────────────────────────────────────────────────────

class LoginBody(BaseModel):
    email: EmailStr
    password: str


class ChangePasswordBody(BaseModel):
    new_password: str

    @field_validator("new_password")
    @classmethod
    def _password_min_length(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("password must be at least 8 characters")
        return v


def _user_response(user: dict, facility: Optional[dict]) -> dict:
    """facility is None only for admin accounts (facility_id IS NULL) —
    profile_completed is reported as True for them since there's no profile
    to complete, so the frontend's complete-profile gate naturally skips
    admins without needing to special-case a null there too.

    facility_type rides along here (not just GET /facilities/me) so the
    frontend knows hospital vs. bloodbank immediately from the login/me
    response, in time to pick the right --role-accent before first paint —
    no extra round trip, no color flash on load."""
    return {
        "id": user["id"],
        "email": user["email"],
        "facility_id": user["facility_id"],
        "facility_name": facility["name"] if facility else None,
        "facility_type": facility["facility_type"] if facility else None,
        "role": user["role"],
        "profile_completed": facility["profile_completed"] if facility else True,
    }


# Brute-force limits — no global middleware, so /health and normal API traffic are never throttled. Every limited action is
# counted under TWO keys and blocked when either is full:
#   acct:<email>   the account being attacked. Not spoofable: it is the target, not a claim about the sender.
#   ip:<address>   the sender, from client_ip.py (never the leftmost X-Forwarded-For entry, which the caller writes). A caller who
#                  goes straight to the Render URL can still choose that address, so this key is the second line, not the first.
# Before this, the one key was "<leftmost X-Forwarded-For>|<email>": changing the header gave a fresh budget on every request, so
# nothing stopped a guessing run against any account, and no key ever added up per account or per address.
#   login: only FAILED attempts count, 5 per 15 minutes per key. A success clears the ACCOUNT's counter only, never the address's:
#   someone who knows one valid login could otherwise reset their address counter by signing in between guesses. The error says
#   how long to wait.
#   change of password (POST /auth/update-password): its own limiter, the same two-key design with the signed-in user as the account
#   key, 5 failed current-password checks per 15 minutes; a success clears the user's counter only, and the 429 does not say which
#   key tripped.
#   forgot-password / OTP issuance / registration (resend included): every request counts (the response must look identical
#   either way), 5 per 10 min per key. OTP issuance is deliberately the SAME limiter as forgot-password's link flow rather
#   than a third instance — both exist to stop the same abuse (mailing strangers from our sender identity), and email
#   verification / password reset are two different `purpose`s on the same otp_codes table, so the same rate budget being
#   purpose-agnostic is the simpler choice, not a separate knob per purpose that could silently drift apart.
# In process memory: a restart clears them (one worker, see auth.AttemptLimiter).
LOGIN_FAILURE_LIMIT = 5
LOGIN_FAILURE_WINDOW_SECONDS = 15 * 60
_LOGIN_LIMITER = auth.AttemptLimiter(limit=LOGIN_FAILURE_LIMIT, window=LOGIN_FAILURE_WINDOW_SECONDS)
_FORGOT_LIMITER = auth.AttemptLimiter(limit=5, window=600)
# Kept apart from the login counters so a typo in the account menu cannot lock the same person out of signing in, or the reverse.
_PASSWORD_CHANGE_LIMITER = auth.AttemptLimiter(limit=LOGIN_FAILURE_LIMIT, window=LOGIN_FAILURE_WINDOW_SECONDS)


def _request_ip(request: Request) -> Optional[str]:
    return client_ip.client_ip(request.headers, request.client.host if request.client else None)


def _attempt_keys(request: Request, email: str) -> list[str]:
    keys = [f"acct:{email.strip().lower()}"]
    ip = _request_ip(request)
    if ip is not None:
        keys.append(f"ip:{ip}")
    return keys


def _user_attempt_keys(request: Request, user_id: int) -> list[str]:
    """Like _attempt_keys, for a request that is already signed in: the account key is the signed-in user (an id, not something the
    caller sends)."""
    keys = [f"acct:user:{user_id}"]
    ip = _request_ip(request)
    if ip is not None:
        keys.append(f"ip:{ip}")
    return keys


def _clear_account_attempts(limiter: "auth.AttemptLimiter", keys: list[str]) -> None:
    """Forget the account's failures (after a success) and nothing else: the address's stay, whoever signs in from it."""
    for k in keys:
        if k.startswith("acct:"):
            limiter.clear(k)


def _wait_text(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds} second{'s' if seconds != 1 else ''}"
    minutes = math.ceil(seconds / 60)
    return f"{minutes} minute{'s' if minutes != 1 else ''}"


def _raise_if_limited(limiter: "auth.AttemptLimiter", keys: list[str], subject: str = "Too many failed sign-in attempts.") -> None:
    wait = max(limiter.retry_after(k) for k in keys)
    if wait:
        raise HTTPException(
            status_code=429,
            detail=f"{subject} Please try again in {_wait_text(wait)}.",
            headers={"Retry-After": str(wait)},
        )


def _record_attempt(limiter: "auth.AttemptLimiter", keys: list[str]) -> None:
    for k in keys:
        limiter.record(k)


# ─── OTP (Step 11) ──────────────────────────────────────────────────────────
# Shared by registration email verification and password reset (see
# schema_otp_codes.sql for why one table, not two). _issue_otp and
# _verify_otp are the only two places that touch otp_codes directly — every
# call site (registration submission/resend, forgot-password-via-code,
# registration email-verify) goes through these, never writes the table
# itself, so "invalidate the prior code on reissue" and "cap attempts before
# comparing the hash" can't be gotten right in one call site and wrong in
# another.

def _issue_otp(
    conn, purpose: str, email: str, *, user_id: Optional[int] = None, registration_request_id: Optional[int] = None
) -> str:
    """Invalidates any still-live code for this exact binding, then issues a
    fresh one. Returns the raw 6-digit code — the caller emails it and never
    stores it; only its hash lands in the DB (see auth.hash_otp_code)."""
    binding_column = "user_id" if purpose == "password_reset" else "registration_request_id"
    binding_value = user_id if purpose == "password_reset" else registration_request_id

    conn.execute(
        text(
            f"""
            UPDATE otp_codes SET used_at = now()
            WHERE purpose = :purpose AND {binding_column} = :binding_value AND used_at IS NULL
            """
        ),
        {"purpose": purpose, "binding_value": binding_value},
    )

    code = auth.generate_otp_code()
    conn.execute(
        text(
            """
            INSERT INTO otp_codes (purpose, registration_request_id, user_id, email, code_hash, expires_at)
            VALUES (:purpose, :registration_request_id, :user_id, :email, :code_hash, :expires_at)
            """
        ),
        {
            "purpose": purpose,
            "registration_request_id": registration_request_id,
            "user_id": user_id,
            "email": email,
            "code_hash": auth.hash_otp_code(code),
            "expires_at": datetime.now(timezone.utc) + auth.OTP_CODE_EXPIRY,
        },
    )
    return code


def _verify_otp(
    conn, purpose: str, code: str, *, user_id: Optional[int] = None, registration_request_id: Optional[int] = None
) -> bool:
    """True and marks the code used_at if `code` matches the latest live code
    for this binding; False otherwise (wrong code, expired, already used, or
    already attempt-capped). The attempt cap is checked BEFORE the hash
    comparison and on every call, including a correct guess that arrives
    after the cap was already hit by prior wrong ones — a capped code stops
    accepting guesses at all, it doesn't just stop counting them."""
    binding_column = "user_id" if purpose == "password_reset" else "registration_request_id"
    binding_value = user_id if purpose == "password_reset" else registration_request_id

    row = conn.execute(
        text(
            f"""
            SELECT id, code_hash, attempts, expires_at FROM otp_codes
            WHERE purpose = :purpose AND {binding_column} = :binding_value AND used_at IS NULL
            ORDER BY created_at DESC LIMIT 1
            """
        ),
        {"purpose": purpose, "binding_value": binding_value},
    ).mappings().first()

    if row is None or row["expires_at"] < datetime.now(timezone.utc) or row["attempts"] >= auth.OTP_MAX_ATTEMPTS:
        return False

    if row["code_hash"] != auth.hash_otp_code(code):
        conn.execute(text("UPDATE otp_codes SET attempts = attempts + 1 WHERE id = :id"), {"id": row["id"]})
        return False

    conn.execute(text("UPDATE otp_codes SET used_at = now() WHERE id = :id"), {"id": row["id"]})
    return True


@app.post("/auth/login")
def login(body: LoginBody, request: Request):
    keys = _attempt_keys(request, body.email)
    _raise_if_limited(_LOGIN_LIMITER, keys)
    with engine.connect() as conn:
        user = conn.execute(
            text(
                """
                SELECT u.*, f.name AS facility_name, f.facility_type, f.profile_completed, f.is_active AS facility_is_active
                FROM users u
                LEFT JOIN facilities f ON f.id = u.facility_id
                WHERE u.email = :email
                """
            ),
            {"email": body.email},
        ).mappings().first()

    # Same error for "no such user" and "wrong password" — don't leak which
    # emails are registered.
    if user is None or not auth.verify_password(body.password, user["password_hash"]):
        _record_attempt(_LOGIN_LIMITER, keys)
        raise HTTPException(status_code=401, detail="invalid email or password")
    _clear_account_attempts(_LOGIN_LIMITER, keys)  # the account's failures only; the address's stay

    # Only ever false for a facility-linked account (facility_is_active is
    # NULL, not False, for admins — who have no facility row to deactivate).
    if user["facility_is_active"] is False:
        raise HTTPException(status_code=403, detail="this facility's account has been deactivated — contact your administrator")

    if user["must_change_password"]:
        # No access_token here at all — a forced-reset account has no way to
        # reach any other endpoint until POST /auth/change-password succeeds
        # and issues a real one. See auth.create_password_reset_token.
        reset_token = auth.create_password_reset_token(user_id=user["id"], email=user["email"])
        return {
            "must_change_password": True,
            "reset_token": reset_token,
            "email": user["email"],
            "facility_name": user["facility_name"],
        }

    token = auth.create_access_token(
        user_id=user["id"], email=user["email"], facility_id=user["facility_id"], role=user["role"]
    )
    facility = (
        {"name": user["facility_name"], "facility_type": user["facility_type"], "profile_completed": user["profile_completed"]}
        if user["facility_id"] else None
    )
    return {
        "must_change_password": False,
        "access_token": token,
        "token_type": "bearer",
        "user": _user_response(user, facility),
    }


class ForgotPasswordBody(BaseModel):
    email: EmailStr


# Fixed, identical response for every outcome of POST /auth/forgot-password —
# whether the email matched a real account, an inactive facility, an admin
# account, or nothing at all. Returning it as one constant (rather than
# building the string at each call site) makes it structurally impossible for
# a future edit to accidentally introduce a wording difference that leaks
# which case occurred.
_FORGOT_PASSWORD_RESPONSE = {
    "message": "If an account with that email exists, a password reset link has been sent."
}


def _send_password_reset_email(to_email: str, facility_name: Optional[str], reset_url: str) -> None:
    """Isolated so the raw reset_url — the one place the raw token exists
    outside this process's memory — only ever flows into this one function
    call, never into a log line, a print(), or an HTTP response."""
    greeting = f"the {facility_name} account" if facility_name else "your BloodLink account"
    html = f"""
    <div style="font-family: sans-serif; max-width: 480px; margin: 0 auto;">
      <h2 style="color: #8C1B3A;">Reset your BloodLink password</h2>
      <p>We received a request to reset the password for {greeting}.</p>
      <p>
        <a href="{reset_url}" style="display: inline-block; padding: 10px 20px; background: #8C1B3A; color: #fff; text-decoration: none; border-radius: 6px;">
          Set a new password
        </a>
      </p>
      <p style="color: #666; font-size: 13px;">This link expires in 1 hour and can only be used once. If you didn't request this, you can safely ignore this email.</p>
    </div>
    """
    email_service.send_email(to=to_email, subject="Reset your BloodLink password", html=html)


@app.post("/auth/forgot-password")
def forgot_password(body: ForgotPasswordBody, request: Request):
    """Self-service reset for an existing, already-verified facility account
    that simply forgot its password — distinct from the forced first-login
    reset after an admin's temporary password (POST /admin/accounts/{id}/reset-password),
    which still uses the separate JWT-based reset_token + POST /auth/change-password
    machinery untouched by this endpoint.

    Deliberately reveals nothing about whether the email matched anything —
    same response, same status code, every time (see _FORGOT_PASSWORD_RESPONSE)
    — so this can't be used to enumerate registered emails. Only ever emails a
    link for an email already tied to a real, active facility account; never
    creates one (no self-registration exists anywhere in this app) and never
    for an admin account (same seed-script-only rule as ever for those).

    The raw token is generated here, emailed once via Resend, and then never
    seen again by this process — only its SHA-256 hash is stored, so a
    database read alone can never be used to complete a reset.
    """
    keys = _attempt_keys(request, body.email)
    _raise_if_limited(_FORGOT_LIMITER, keys, "Too many email requests.")
    _record_attempt(_FORGOT_LIMITER, keys)

    with engine.connect() as conn:
        user = conn.execute(
            text(
                """
                SELECT u.id, u.email, u.facility_id, f.name AS facility_name, f.is_active AS facility_is_active
                FROM users u
                LEFT JOIN facilities f ON f.id = u.facility_id
                WHERE u.email = :email
                """
            ),
            {"email": body.email},
        ).mappings().first()

    eligible = user is not None and user["facility_id"] is not None and user["facility_is_active"] is not False
    if eligible:
        raw_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        expires_at = datetime.now(timezone.utc) + PASSWORD_RESET_LINK_EXPIRY
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO password_reset_requests (user_id, token_hash, expires_at) "
                    "VALUES (:user_id, :token_hash, :expires_at)"
                ),
                {"user_id": user["id"], "token_hash": token_hash, "expires_at": expires_at},
            )
        reset_url = f"{FRONTEND_BASE_URL}/reset-password?token={raw_token}"
        try:
            _send_password_reset_email(user["email"], user["facility_name"], reset_url)
        except email_service.EmailSendError as e:
            # Logged without the token/url (see _send_password_reset_email) —
            # this is the one place a delivery failure is visible at all,
            # since the response to the client must stay generic regardless.
            print(f"[auth] password reset email failed for user_id={user['id']}: {e}")

    return _FORGOT_PASSWORD_RESPONSE


class ResetPasswordBody(BaseModel):
    token: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def _password_min_length(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("password must be at least 8 characters")
        return v


@app.post("/auth/reset-password")
def reset_password(body: ResetPasswordBody):
    """Completes a self-service reset started by POST /auth/forgot-password.

    The token is single-use (used_at is set the moment it's consumed here,
    inside the same transaction as the password update) and time-limited
    (expires_at, checked against the DB's own clock) — a link that's already
    been used or has expired fails with the same generic error either way, so
    neither case tells an attacker anything more specific than "try again."
    """
    token_hash = hashlib.sha256(body.token.encode("utf-8")).hexdigest()
    with engine.begin() as conn:
        reset_request = conn.execute(
            text(
                """
                SELECT id, user_id, expires_at, used_at FROM password_reset_requests
                WHERE token_hash = :token_hash
                """
            ),
            {"token_hash": token_hash},
        ).mappings().first()

        if (
            reset_request is None
            or reset_request["used_at"] is not None
            or reset_request["expires_at"] < datetime.now(timezone.utc)
        ):
            raise HTTPException(status_code=400, detail="this password reset link is invalid or has expired")

        conn.execute(
            text("UPDATE users SET password_hash = :password_hash WHERE id = :id"),
            {"password_hash": auth.hash_password(body.new_password), "id": reset_request["user_id"]},
        )
        conn.execute(
            text("UPDATE password_reset_requests SET used_at = now() WHERE id = :id"),
            {"id": reset_request["id"]},
        )

    return {"message": "Password updated. You can now log in with your new password."}


# ─── Password reset via OTP (Step 7) ───────────────────────────────────────
# A typed-code twin of POST /auth/forgot-password + POST /auth/reset-password
# above, built to run ALONGSIDE the link flow, not replace it yet — the link
# flow is left completely untouched here. The reason for building this at
# all: institutional mail scanners at hospital/government addresses (this
# app's actual users) follow links and consume single-use tokens before the
# recipient ever sees them, which likely already breaks the link flow for
# exactly the accounts that most need it to work. Retiring the link flow is
# a deliberate separate change once this is confirmed working end to end,
# not bundled into introducing it.

class ForgotPasswordOtpBody(BaseModel):
    email: EmailStr


_FORGOT_PASSWORD_OTP_RESPONSE = {
    "message": "If an account with that email exists, a password reset code has been sent."
}


def _send_password_reset_otp_email(to_email: str, facility_name: Optional[str], code: str) -> None:
    greeting = f"the {facility_name} account" if facility_name else "your BloodLink account"
    html = f"""
    <div style="font-family: sans-serif; max-width: 480px; margin: 0 auto;">
      <h2 style="color: #8C1B3A;">Reset your BloodLink password</h2>
      <p>We received a request to reset the password for {greeting}. Enter this code to continue:</p>
      <p style="font-size: 28px; font-weight: bold; letter-spacing: 4px; text-align: center; background: #f5f5f5; padding: 12px; border-radius: 6px;">{code}</p>
      <p style="color: #666; font-size: 13px;">This code expires in 10 minutes and can only be used once. If you didn't request this, you can safely ignore this email.</p>
    </div>
    """
    email_service.send_email(to=to_email, subject="Your BloodLink password reset code", html=html)


@app.post("/auth/forgot-password/otp")
def forgot_password_otp(body: ForgotPasswordOtpBody, request: Request):
    """Same eligibility rule and enumeration-safety shape as
    POST /auth/forgot-password (identical response regardless of outcome) —
    a 6-digit code instead of a link. Shares _FORGOT_LIMITER with that
    endpoint: same abuse concern, one budget."""
    keys = _attempt_keys(request, body.email)
    _raise_if_limited(_FORGOT_LIMITER, keys, "Too many email requests.")
    _record_attempt(_FORGOT_LIMITER, keys)

    with engine.begin() as conn:
        user = conn.execute(
            text(
                """
                SELECT u.id, u.email, u.facility_id, f.name AS facility_name, f.is_active AS facility_is_active
                FROM users u
                LEFT JOIN facilities f ON f.id = u.facility_id
                WHERE u.email = :email
                """
            ),
            {"email": body.email},
        ).mappings().first()

        eligible = user is not None and user["facility_id"] is not None and user["facility_is_active"] is not False
        code = _issue_otp(conn, "password_reset", user["email"], user_id=user["id"]) if eligible else None

    if code is not None:
        try:
            _send_password_reset_otp_email(user["email"], user["facility_name"], code)
        except email_service.EmailSendError as e:
            print(f"[auth] password reset OTP email failed for user_id={user['id']}: {e}")

    return _FORGOT_PASSWORD_OTP_RESPONSE


class ResetPasswordOtpBody(BaseModel):
    email: EmailStr
    code: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def _password_min_length(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("password must be at least 8 characters")
        return v


@app.post("/auth/reset-password/otp")
def reset_password_otp(body: ResetPasswordOtpBody):
    """Completes a reset started by POST /auth/forgot-password/otp. Same
    8-character minimum as POST /auth/reset-password (the link flow's
    equivalent) — this is a new channel to an existing endpoint's behavior,
    not a reason to change the password policy on existing accounts.

    Never raises INSIDE the `with engine.begin()` block, for the same reason
    verify_facility_registration_email doesn't: an exception there would
    roll back the attempts += 1 _verify_otp just wrote on a wrong guess, and
    the attempt cap would never actually accumulate."""
    with engine.begin() as conn:
        user = conn.execute(text("SELECT id FROM users WHERE email = :email"), {"email": body.email}).mappings().first()
        if user is None:
            verified = False
        else:
            verified = _verify_otp(conn, "password_reset", body.code, user_id=user["id"])
            if verified:
                conn.execute(
                    text("UPDATE users SET password_hash = :password_hash WHERE id = :id"),
                    {"password_hash": auth.hash_password(body.new_password), "id": user["id"]},
                )

    if not verified:
        raise HTTPException(status_code=400, detail="this reset code is invalid or has expired")

    return {"message": "Password updated. You can now log in with your new password."}


@app.post("/auth/change-password")
def change_password(body: ChangePasswordBody, reset_claims: dict = Depends(get_user_from_reset_token)):
    """The only thing a password-reset token can be used for. Succeeding here
    clears must_change_password and issues a real access token — the normal
    login response shape, so the frontend can proceed exactly as it would
    after an ordinary login (into the complete-profile flow if the facility's
    profile isn't done yet, or straight to the dashboard if it already is).
    In practice this is only ever reached by facility accounts on a temporary
    password (an admin's reset, or one issued before admin account creation was
    removed on 2026-11-11) — admin accounts are seeded with a real password
    and must_change_password=false, so they never get a reset_token."""
    user_id = int(reset_claims["sub"])
    with engine.begin() as conn:
        # The JWT itself has no server-side revocation, so this WHERE clause is
        # what makes it single-use in practice: must_change_password flips to
        # false the first time this succeeds, so a replayed/leaked copy of the
        # same still-unexpired token can't silently overwrite the password a
        # second time (e.g. to undo what the legitimate user just set).
        row = conn.execute(
            text(
                """
                UPDATE users SET password_hash = :password_hash, must_change_password = false
                WHERE id = :id AND must_change_password = true
                RETURNING id, email, facility_id, role
                """
            ),
            {"password_hash": auth.hash_password(body.new_password), "id": user_id},
        ).mappings().first()
        if row is None:
            exists = conn.execute(text("SELECT 1 FROM users WHERE id = :id"), {"id": user_id}).scalar()
            if not exists:
                raise HTTPException(status_code=404, detail="account not found")
            raise HTTPException(status_code=400, detail="this password reset link has already been used")

        facility = None
        if row["facility_id"] is not None:
            facility = conn.execute(
                text("SELECT name, facility_type, profile_completed FROM facilities WHERE id = :id"),
                {"id": row["facility_id"]},
            ).mappings().first()

    token = auth.create_access_token(
        user_id=row["id"], email=row["email"], facility_id=row["facility_id"], role=row["role"]
    )
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": _user_response(row, facility),
    }


class UpdatePasswordBody(BaseModel):
    current_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def _password_min_length(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("password must be at least 8 characters")
        return v


@app.post("/auth/update-password")
def update_password(body: UpdatePasswordBody, request: Request, user: dict = Depends(get_current_user)):
    """Self-service password change (Account menu) — distinct from
    POST /auth/change-password, which only ever handles the forced-reset
    flow off a narrowly-scoped reset_token. This one requires a normal
    access token (meaning must_change_password is already false — that
    token literally can't exist otherwise) plus proof of the current
    password, since the account is already logged in and choosing to
    change it, not being forced to.

    A stolen 24-hour token must not be able to guess the current password, so failed current-password checks are limited: 5 per 15
    minutes under the signed-in user and under the sender's address (_PASSWORD_CHANGE_LIMITER). Only a failed check counts; a
    success clears the user's counter, not the address's. The 429 says how long to wait and not which key was full."""
    user_id = int(user["sub"])
    keys = _user_attempt_keys(request, user_id)
    _raise_if_limited(_PASSWORD_CHANGE_LIMITER, keys, "Too many failed attempts to confirm your password.")
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT password_hash FROM users WHERE id = :id"), {"id": user_id}
        ).mappings().first()
        if row is None:
            raise HTTPException(status_code=404, detail="account not found")
        if not auth.verify_password(body.current_password, row["password_hash"]):
            _record_attempt(_PASSWORD_CHANGE_LIMITER, keys)
            # 400, not 401: the session is fine, a field is wrong. The frontend treats every 401 as an expired session and signs the
            # user out, so a mistyped current password must not be one. 401 stays for an invalid or expired token (get_current_user).
            raise HTTPException(status_code=400, detail="current password is incorrect")
        _clear_account_attempts(_PASSWORD_CHANGE_LIMITER, keys)

        conn.execute(
            text("UPDATE users SET password_hash = :password_hash WHERE id = :id"),
            {"password_hash": auth.hash_password(body.new_password), "id": user_id},
        )
    return {"detail": "password updated"}


@app.get("/auth/me")
@retry_read
def get_me(user: dict = Depends(get_current_user)):
    """Re-checks the logged-in user's facility fresh from the DB — used on
    app load so a stale localStorage session (e.g. profile_completed flipped
    true in a different tab, or by an admin) doesn't gate someone into a
    profile-completion screen they've already finished, or the reverse."""
    with read_engine.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT u.id, u.email, u.facility_id, u.role, f.name AS facility_name, f.facility_type, f.profile_completed
                FROM users u LEFT JOIN facilities f ON f.id = u.facility_id
                WHERE u.id = :id
                """
            ),
            {"id": int(user["sub"])},
        ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="account not found")
    facility = (
        {"name": row["facility_name"], "facility_type": row["facility_type"], "profile_completed": row["profile_completed"]}
        if row["facility_id"] else None
    )
    return _user_response(row, facility)


# ─── Admin (Step 10A) ───────────────────────────────────────────────────────
# Every endpoint below requires role='admin' on a real access token (see
# require_admin_role) — no shared secret, no header-based bypass. role='admin'
# can only ever be set by seed_admin_user.py, run by hand once; there is no
# endpoint anywhere that lets a request create or promote itself to admin.
#
# There is no endpoint here that creates a facility or a login. A facility comes into existence one way: it registers itself
# (POST /facilities/register), proves its email, and an admin approves it (admin_approve_registration below). The admin-side
# "create facility account" and "add account to a facility" endpoints, and the temporary password they issued, were removed on
# 2026-11-11; the forced first-login reset still exists for the accounts that were issued one and for an admin's password reset.

class UpdateFacilityStatusBody(BaseModel):
    is_active: bool


@app.get("/admin/facilities", dependencies=[Depends(require_admin_role)])
def admin_list_facilities():
    """Every facility plus its status and linked login account(s) — almost
    always exactly one account per facility (registration approval
    creates exactly one), but this doesn't assume that; a facility with no
    account yet (shouldn't normally happen) just gets an empty accounts list."""
    with engine.connect() as conn:
        facilities = conn.execute(
            text(
                """
                SELECT id, name, facility_type, profile_completed, is_active
                FROM facilities ORDER BY name
                """
            )
        ).mappings().all()
        user_rows = conn.execute(
            text(
                """
                SELECT id, email, facility_id, must_change_password
                FROM users WHERE facility_id IS NOT NULL
                """
            )
        ).mappings().all()

    accounts_by_facility: dict[int, list[dict]] = {}
    for row in user_rows:
        accounts_by_facility.setdefault(row["facility_id"], []).append(
            {"id": row["id"], "email": row["email"], "must_change_password": row["must_change_password"]}
        )

    return [
        {**dict(f), "accounts": accounts_by_facility.get(f["id"], [])}
        for f in facilities
    ]


@app.post("/admin/accounts/{user_id}/reset-password", dependencies=[Depends(require_admin_role)])
def admin_reset_account_password(user_id: int):
    """Admin backup access when a facility forgets its password. Password
    hashes are one-way (bcrypt) — there is no existing password to reveal —
    so this issues a brand new temporary password and puts the account back
    into the forced-reset flow (must_change_password). It is returned once,
    here, in the response: there is no email delivery of it, so whoever calls
    this relays it out of band."""
    temp_password = auth.generate_temp_password()
    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                UPDATE users SET password_hash = :password_hash, must_change_password = true
                WHERE id = :id AND facility_id IS NOT NULL
                RETURNING id, email
                """
            ),
            {"password_hash": auth.hash_password(temp_password), "id": user_id},
        ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="account not found")
    return {"id": row["id"], "email": row["email"], "temporary_password": temp_password}


@app.patch("/admin/facilities/{facility_id}", dependencies=[Depends(require_admin_role)])
def admin_update_facility_status(facility_id: int, body: UpdateFacilityStatusBody):
    """Deactivate/reactivate. A deactivated facility's account(s) can't log
    in — enforced in POST /auth/login, not just hidden in the UI — until this
    is flipped back. Nothing about the facility's data is touched; this is
    purely an access gate."""
    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                UPDATE facilities SET is_active = :is_active
                WHERE id = :id
                RETURNING id, name, facility_type, profile_completed, is_active
                """
            ),
            {"is_active": body.is_active, "id": facility_id},
        ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="facility not found")
    _active_account_until.clear()  # a deactivation must not wait out the pass cache (see _assert_account_active)
    return dict(row)


# ─── Facility registration review ──────────────────────────────────────────
# The admin-facing half of self-registration (see register_facility /
# verify_facility_registration_email). Never touches facilities/users except
# on approval — a rejected or still-pending request is only ever a
# facility_registration_requests row.

@app.get("/admin/registrations", dependencies=[Depends(require_admin_role)])
def admin_list_registrations():
    """Only email-verified, non-archived requests — a merely-submitted
    (unverified) row must never reach an administrator, per the whole point
    of gating on email verification before this queue."""
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT id, facility_name, facility_type, address, latitude, longitude,
                       contact_person, email, phone, created_at
                FROM facility_registration_requests
                WHERE status = 'email_verified' AND archived_at IS NULL
                ORDER BY created_at
                """
            )
        ).mappings().all()
    return [dict(row) for row in rows]


def _send_registration_approved_email(to_email: str, facility_name: str) -> None:
    html = f"""
    <div style="font-family: sans-serif; max-width: 480px; margin: 0 auto;">
      <h2 style="color: #8C1B3A;">Your BloodLink registration is approved</h2>
      <p><strong>{facility_name}</strong> has been approved. You can now sign in with the email and password you registered with.</p>
    </div>
    """
    email_service.send_email(to=to_email, subject="Your BloodLink registration is approved", html=html)


def _send_registration_rejected_email(to_email: str, facility_name: str, reason: str) -> None:
    html = f"""
    <div style="font-family: sans-serif; max-width: 480px; margin: 0 auto;">
      <h2 style="color: #8C1B3A;">Your BloodLink registration was not approved</h2>
      <p>Your registration for <strong>{facility_name}</strong> was not approved for the following reason:</p>
      <p style="background: #f5f5f5; padding: 12px; border-radius: 6px;">{reason}</p>
      <p style="color: #666; font-size: 13px;">You're welcome to submit a new registration once this is addressed.</p>
    </div>
    """
    email_service.send_email(to=to_email, subject="Your BloodLink registration was not approved", html=html)


@app.post("/admin/registrations/{registration_id}/approve", dependencies=[Depends(require_admin_role)])
def admin_approve_registration(registration_id: int, admin: dict = Depends(require_admin_role)):
    """Creates the real facilities + users rows from the request and
    archives it — the only place either of those ever gets created from a
    registration. must_change_password is false here, unlike an account on a
    temporary password issued by an admin: the applicant chose
    this password themselves, proved they own the email it's tied to, and
    an administrator has now reviewed the application — there's no
    temporary-password handoff to complete.

    profile_completed is set true immediately: address/coordinates
    were already collected (and just reviewed) at registration, so
    routing this facility through the post-login CompleteProfile screen
    would only ask it to re-type what it already gave. department is left
    null — the one field registration doesn't collect — editable any time
    via the Account menu's Edit Facility Profile, same field, no forced gate.
    """
    with engine.begin() as conn:
        registration = conn.execute(
            text("SELECT * FROM facility_registration_requests WHERE id = :id AND archived_at IS NULL"),
            {"id": registration_id},
        ).mappings().first()
        if registration is None:
            raise HTTPException(status_code=404, detail="registration not found")
        if registration["status"] != "email_verified":
            raise HTTPException(status_code=400, detail="this registration hasn't been email-verified yet")

        existing_user = conn.execute(
            text("SELECT 1 FROM users WHERE email = :email"), {"email": registration["email"]}
        ).first()
        if existing_user is not None:
            raise HTTPException(status_code=409, detail="an account with this email already exists")

        facility = conn.execute(
            text(
                """
                INSERT INTO facilities (name, facility_type, address, latitude, longitude, profile_completed)
                VALUES (:name, :facility_type, :address, :latitude, :longitude, true)
                RETURNING id, name, facility_type, profile_completed, is_active
                """
            ),
            {
                "name": registration["facility_name"],
                "facility_type": registration["facility_type"],
                "address": registration["address"],
                "latitude": registration["latitude"],
                "longitude": registration["longitude"],
            },
        ).mappings().first()

        conn.execute(
            text(
                """
                INSERT INTO blood_type_thresholds (facility_id, blood_type, minimum_units, maximum_units)
                VALUES (:facility_id, :blood_type, :minimum_units, :maximum_units)
                """
            ),
            [
                {"facility_id": facility["id"], "blood_type": bt, "minimum_units": mn, "maximum_units": mx}
                for bt, mn, mx in DEFAULT_THRESHOLDS
            ],
        )

        # The consent given on the registration form becomes the account's own record. NULL (a registration made before the consent
        # existed, or while the migration was not applied) means "not recorded", never "refused".
        copy_consent = _consent_columns_ready(conn)
        user = conn.execute(
            text(
                f"""
                INSERT INTO users (email, password_hash, facility_id, role, must_change_password{", privacy_consent_version, privacy_consented_at" if copy_consent else ""})
                VALUES (:email, :password_hash, :facility_id, 'staff', false{", :consent_version, :consented_at" if copy_consent else ""})
                RETURNING id, email, facility_id, role, created_at
                """
            ),
            {
                "email": registration["email"], "password_hash": registration["password_hash"], "facility_id": facility["id"],
                "consent_version": registration.get("privacy_consent_version"), "consented_at": registration.get("privacy_consented_at"),
            },
        ).mappings().first()

        conn.execute(
            text(
                """
                UPDATE facility_registration_requests
                SET status = 'approved', reviewed_by = :admin_id, reviewed_at = now(), archived_at = now()
                WHERE id = :id
                """
            ),
            {"admin_id": int(admin["sub"]), "id": registration_id},
        )

    try:
        _send_registration_approved_email(registration["email"], facility["name"])
    except email_service.EmailSendError as e:
        print(f"[registration] approval email failed for registration_id={registration_id}: {e}")

    return {"facility": dict(facility), "user": dict(user)}


class RejectRegistrationBody(BaseModel):
    reason: str

    @field_validator("reason")
    @classmethod
    def _reason_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("a rejection reason is required")
        return v.strip()


@app.post("/admin/registrations/{registration_id}/reject", dependencies=[Depends(require_admin_role)])
def admin_reject_registration(registration_id: int, body: RejectRegistrationBody, admin: dict = Depends(require_admin_role)):
    """Archived, never deleted (see facility_registration_requests'
    archived_at) — a rejected application stays on file with its reason,
    the same archive-only philosophy as facilities.is_active."""
    with engine.begin() as conn:
        registration = conn.execute(
            text(
                "SELECT email, facility_name FROM facility_registration_requests "
                "WHERE id = :id AND archived_at IS NULL"
            ),
            {"id": registration_id},
        ).mappings().first()
        if registration is None:
            raise HTTPException(status_code=404, detail="registration not found")

        conn.execute(
            text(
                """
                UPDATE facility_registration_requests
                SET status = 'rejected', rejection_reason = :reason, reviewed_by = :admin_id,
                    reviewed_at = now(), archived_at = now()
                WHERE id = :id
                """
            ),
            {"reason": body.reason, "admin_id": int(admin["sub"]), "id": registration_id},
        )

    try:
        _send_registration_rejected_email(registration["email"], registration["facility_name"], body.reason)
    except email_service.EmailSendError as e:
        print(f"[registration] rejection email failed for registration_id={registration_id}: {e}")

    return {"message": "registration rejected"}


# ─── Donors (Step 7A) ───────────────────────────────────────────────────────

VALID_BLOOD_TYPES = set(BLOOD_COMPATIBILITY.keys())


def _parse_donor_csv(csv_text: str) -> tuple[list[dict], list[dict]]:
    """Pure: parses donor CSV text into (valid_rows, errors), no DB involved.

    Required headers (case-insensitive): name, blood_type, phone. Blank lines
    are skipped silently; any other malformed row is reported with its line
    number and reason rather than silently dropped or failing the whole batch.
    """
    reader = csv.DictReader(io.StringIO(csv_text))
    if not reader.fieldnames:
        return [], [{"row": 0, "reason": "empty file"}]

    normalized_fields = [f.strip().lower() for f in reader.fieldnames]
    required = {"name", "blood_type", "phone"}
    missing = required - set(normalized_fields)
    if missing:
        return [], [{"row": 0, "reason": f"missing required column(s): {', '.join(sorted(missing))}"}]

    valid_rows: list[dict] = []
    errors: list[dict] = []
    for i, raw_row in enumerate(reader, start=2):  # row 1 is the header
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw_row.items()}
        name = row.get("name", "")
        blood_type = row.get("blood_type", "")
        phone = row.get("phone", "")

        if not name and not blood_type and not phone:
            continue  # blank line

        if not name:
            errors.append({"row": i, "reason": "missing name"})
        elif blood_type not in VALID_BLOOD_TYPES:
            errors.append({"row": i, "reason": f"invalid blood_type {blood_type!r}"})
        elif not phone:
            errors.append({"row": i, "reason": "missing phone"})
        else:
            valid_rows.append({"name": name, "blood_type": blood_type, "phone": phone})

    return valid_rows, errors


def _require_bloodbank_facility(conn, facility_id: int) -> None:
    """Donor outreach (roster upload, blasts, replies) is blood-bank-only —
    same split as GET /forecast and historical-upload, just applied to this
    domain: hospitals get threshold_status, never forecast; hospitals see no
    Donors tab either, and this is what makes that a real restriction rather
    than a UI choice. facility_type is re-checked fresh from the DB on every
    call, never trusted from the client, matching that same pattern exactly."""
    facility_type = conn.execute(
        text("SELECT facility_type FROM facilities WHERE id = :id"), {"id": facility_id}
    ).scalar()
    if facility_type != "bloodbank":
        raise HTTPException(status_code=403, detail="donor outreach is only available to blood bank facilities")


@app.post("/donors/upload")
async def upload_donors(
    file: UploadFile = File(...),
    facility_id: int = Depends(get_acting_facility_id),
    uploaded_by: Optional[int] = Depends(get_acting_user_id),
):
    """Real CSV upload, not the decorative Inventory button's pattern (that one
    doesn't actually exist anywhere in this codebase — see Step 7A notes).
    Re-uploading updates existing donors by (facility_id, phone) rather than
    duplicating them, since a roster CSV is expected to be re-exported and
    re-uploaded periodically as it changes.

    Logged to upload_history like the other two upload flows, but with
    raw_content left NULL — donor CSVs carry personal information, and the
    donors table is already the one copy of that we want sitting around.
    """
    with engine.connect() as conn:
        _require_bloodbank_facility(conn, facility_id)

    if not (file.filename or "").lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="only .csv files are accepted")

    raw_bytes = await file.read()
    try:
        csv_text = raw_bytes.decode("utf-8-sig")  # utf-8-sig tolerates a BOM from Excel exports
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="file must be UTF-8 encoded")

    valid_rows, errors = _parse_donor_csv(csv_text)

    with engine.begin() as conn:
        upload_id = _start_upload_record(conn, facility_id, "donors", uploaded_by, file.filename, None)

        batch = _last_wins(valid_rows, lambda r: r["phone"])
        if batch:
            conn.execute(
                text(
                    """
                    INSERT INTO donors (name, blood_type, phone, facility_id, upload_history_id)
                    SELECT t.name, t.blood_type, t.phone, :facility_id, :upload_id
                    FROM unnest(CAST(:name AS text[]), CAST(:blood_type AS text[]), CAST(:phone AS text[]))
                        AS t(name, blood_type, phone)
                    ON CONFLICT (facility_id, phone)
                    DO UPDATE SET name = EXCLUDED.name, blood_type = EXCLUDED.blood_type, upload_history_id = EXCLUDED.upload_history_id
                    """
                ),
                {**{c: [r[c] for r in batch] for c in ("name", "blood_type", "phone")},
                 "facility_id": facility_id, "upload_id": upload_id},
            )

        _finish_upload_record(conn, upload_id, len(valid_rows), errors)

    return {"rows_processed": len(valid_rows), "errors": errors}


@app.get("/donors")
def list_donors(facility_id: int = Depends(get_acting_facility_id)):
    with engine.connect() as conn:
        _require_bloodbank_facility(conn, facility_id)
        rows = conn.execute(
            text(
                """
                SELECT id, name, blood_type, phone, created_at
                FROM donors WHERE facility_id = :facility_id
                ORDER BY name ASC
                """
            ),
            {"facility_id": facility_id},
        ).mappings().all()
    return [dict(row) for row in rows]


# ─── Donor Blasts (Step 7B) ─────────────────────────────────────────────────
# SMS sending is simulated end to end — there is no real provider integration.
# Every response and log entry is explicitly labeled as simulated so this
# can never be confused with a real send once a provider is wired in later.

class CreateBlastBody(BaseModel):
    blood_type: str
    target_count: int = 5
    time_limit_hours: int = 2

    @field_validator("target_count")
    @classmethod
    def _target_count_positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError("target_count must be at least 1")
        return v

    @field_validator("time_limit_hours")
    @classmethod
    def _time_limit_positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError("time_limit_hours must be at least 1")
        return v


def _build_blast_message(blood_type: str, facility_name: str, target_count: int, time_limit_hours: int) -> str:
    """Pure: the actual SMS content a real provider would send — deliberately
    NOT tagged as simulated internally (a real donor receiving a text that
    says "SIMULATED" would be nonsensical). The simulated/not-sent label is
    metadata on the log entry and API response, not part of the message text.

    Covers both required consent elements: replying shares the donor's name
    and phone number with the requesting facility, and the reply itself is
    what constitutes their consent.
    """
    hours_word = "hour" if time_limit_hours == 1 else "hours"
    return (
        f"[BloodLink Alert] {blood_type} donors urgently needed at {facility_name}. "
        f"We're aiming for {target_count} donors within {time_limit_hours} {hours_word}. "
        f"Reply YES if you're able to donate — by replying, you consent to your name and "
        f"phone number being shared with {facility_name}, and your reply itself serves as "
        f"that consent. Reply STOP to opt out."
    )


@app.post("/donors/blast")
def create_blast(body: CreateBlastBody, facility_id: int = Depends(get_acting_facility_id)):
    """Creates a blast, messages every currently-matching donor on file (not
    capped to target_count — you message everyone eligible and let replies
    fill the target, same as the real donor-recruitment pattern this
    simulates), and logs each simulated message. Zero matching donors is a
    valid, honestly-reported outcome, not an error."""
    with engine.begin() as conn:
        _require_bloodbank_facility(conn, facility_id)

        facility = conn.execute(
            text("SELECT name FROM facilities WHERE id = :id"), {"id": facility_id}
        ).mappings().first()
        if facility is None:
            raise HTTPException(status_code=500, detail="acting facility not found")

        matching_donors = conn.execute(
            text(
                """
                SELECT id, name, phone FROM donors
                WHERE facility_id = :facility_id AND blood_type = :blood_type
                ORDER BY name ASC
                """
            ),
            {"facility_id": facility_id, "blood_type": body.blood_type},
        ).mappings().all()

        deadline_at = datetime.now(timezone.utc) + timedelta(hours=body.time_limit_hours)

        blast_row = conn.execute(
            text(
                """
                INSERT INTO blasts (facility_id, blood_type, target_count, time_limit_hours, deadline_at)
                VALUES (:facility_id, :blood_type, :target_count, :time_limit_hours, :deadline_at)
                RETURNING id, facility_id, blood_type, target_count, time_limit_hours, status, created_at, deadline_at
                """
            ),
            {
                "facility_id": facility_id,
                "blood_type": body.blood_type,
                "target_count": body.target_count,
                "time_limit_hours": body.time_limit_hours,
                "deadline_at": deadline_at,
            },
        ).mappings().first()

        message_text = _build_blast_message(body.blood_type, facility["name"], body.target_count, body.time_limit_hours)

        messages = [
            {
                "donor_id": donor["id"],
                "donor_name": donor["name"],
                "phone": donor["phone"],
                "message_text": message_text,
            }
            for donor in matching_donors
        ]
        if messages:
            conn.execute(
                text(
                    """
                    INSERT INTO blast_messages (blast_id, donor_id, message_text)
                    VALUES (:blast_id, :donor_id, :message_text)
                    """
                ),
                [{"blast_id": blast_row["id"], "donor_id": m["donor_id"], "message_text": message_text} for m in messages],
            )

    return {
        "blast": dict(blast_row),
        "simulated": True,
        "label": "SIMULATED — not actually sent",
        "recipient_count": len(messages),
        "messages": messages,
    }


@app.get("/donors/blasts")
def list_blasts(facility_id: int = Depends(get_acting_facility_id)):
    with engine.connect() as conn:
        _require_bloodbank_facility(conn, facility_id)
        rows = conn.execute(
            text(
                """
                SELECT id, blood_type, target_count, time_limit_hours, status, created_at, deadline_at
                FROM blasts WHERE facility_id = :facility_id
                ORDER BY created_at DESC
                """
            ),
            {"facility_id": facility_id},
        ).mappings().all()
    return [dict(row) for row in rows]


@app.get("/donors/blasts/{blast_id}/messages")
def list_blast_messages(blast_id: int, facility_id: int = Depends(get_acting_facility_id)):
    with engine.begin() as conn:
        _require_bloodbank_facility(conn, facility_id)

        blast = conn.execute(
            text("SELECT id FROM blasts WHERE id = :id AND facility_id = :facility_id"),
            {"id": blast_id, "facility_id": facility_id},
        ).mappings().first()
        if blast is None:
            raise HTTPException(status_code=404, detail="blast not found")

        # Lazy completion check — catches the deadline silently passing even
        # if no reply ever triggered it, so "drive complete" messages show up
        # here as soon as anyone looks, not only right after a reply/dev-tool call.
        _complete_blast_if_needed(conn, blast_id)

        rows = conn.execute(
            text(
                """
                SELECT bm.id, bm.donor_id, d.name AS donor_name, d.phone, bm.message_text, bm.simulated_sent_at
                FROM blast_messages bm
                JOIN donors d ON d.id = bm.donor_id
                WHERE bm.blast_id = :blast_id
                ORDER BY d.name ASC
                """
            ),
            {"blast_id": blast_id},
        ).mappings().all()
    return {
        "simulated": True,
        "label": "SIMULATED — not actually sent",
        "messages": [dict(row) for row in rows],
    }


# ─── Donor Blast Replies + Auto-Stop (Step 7C) ─────────────────────────────

def _build_drive_complete_message(blood_type: str, facility_name: str) -> str:
    """Pure, same rule as _build_blast_message: real content a provider would
    actually send, never internally tagged as simulated."""
    return (
        f"[BloodLink Update] Thank you for your response — the {blood_type} donor drive "
        f"at {facility_name} has now reached its goal or time limit and is complete. "
        f"We appreciate your willingness to help and may reach out for a future drive."
    )


def _evaluate_blast_completion(
    yes_reply_count: int, target_count: int, now: datetime, deadline_at: datetime
) -> tuple[bool, Optional[str]]:
    """Pure: the actual fill/auto-stop decision, isolated from all DB I/O so it
    can be unit tested directly. Order of checks matters as the deterministic
    tie-break: if both conditions are true simultaneously, target_reached wins.
    >= (not >) on both comparisons — reaching the target exactly, or reaching
    the deadline exactly, both count as complete.
    """
    if yes_reply_count >= target_count:
        return True, "target_reached"
    if now >= deadline_at:
        return True, "deadline_passed"
    return False, None


def _complete_blast_if_needed(conn, blast_id: int) -> bool:
    """Idempotent — a no-op if already completed. Checks real current time
    against the blast's actual state; if this call is what completes it, logs
    a drive-complete message for every donor who was messaged but never
    replied (yes or no) — donors who already said no are correctly left out,
    same as anyone already counted toward the target.

    This runs lazily on every blast read (list_blast_messages, simulate_reply,
    the dev force-expire tool, get_confirmed_donors) — including the ~4s chat
    poll — so two calls landing close together is a real case, not
    hypothetical, same as GET /forecast's forecast_shortage notification. The
    status flip is one atomic UPDATE ... WHERE status != 'completed' RETURNING
    id, not a separate SELECT-then-decide followed by a separate write: only
    the call whose UPDATE actually matches a row (RETURNING yields one) is
    the one that flipped it and proceeds to fire the notification; a second,
    near-simultaneous call finds status already 'completed' by the time its
    own UPDATE runs, matches nothing, and returns False instead of firing a
    duplicate — same closed-race shape as
    _check_forecast_shortage_notifications's UPSERT.
    """
    blast = conn.execute(text("SELECT * FROM blasts WHERE id = :id"), {"id": blast_id}).mappings().first()
    if blast is None or blast["status"] == "completed":
        return False

    now = datetime.now(timezone.utc)
    yes_count = conn.execute(
        text("SELECT count(*) FROM blast_replies WHERE blast_id = :id AND reply = 'yes'"),
        {"id": blast_id},
    ).scalar()

    should_complete, _reason = _evaluate_blast_completion(yes_count, blast["target_count"], now, blast["deadline_at"])
    if not should_complete:
        return False

    flipped = conn.execute(
        text("UPDATE blasts SET status = 'completed' WHERE id = :id AND status != 'completed' RETURNING id"),
        {"id": blast_id},
    ).first()
    if flipped is None:
        return False

    facility = conn.execute(
        text("SELECT name FROM facilities WHERE id = :id"), {"id": blast["facility_id"]}
    ).mappings().first()
    message_text = _build_drive_complete_message(blast["blood_type"], facility["name"])

    _create_notification(
        conn, blast["facility_id"], "blast_complete",
        f"{blast['blood_type']} donor drive complete — {yes_count} of {blast['target_count']} confirmed",
        "chat",
    )

    unresponded = conn.execute(
        text(
            """
            SELECT DISTINCT bm.donor_id
            FROM blast_messages bm
            LEFT JOIN blast_replies br ON br.blast_id = bm.blast_id AND br.donor_id = bm.donor_id
            WHERE bm.blast_id = :id AND br.id IS NULL
            """
        ),
        {"id": blast_id},
    ).mappings().all()

    if unresponded:
        conn.execute(
            text(
                "INSERT INTO blast_messages (blast_id, donor_id, message_text) "
                "VALUES (:blast_id, :donor_id, :message_text)"
            ),
            [{"blast_id": blast_id, "donor_id": row["donor_id"], "message_text": message_text} for row in unresponded],
        )

    return True


class SimulateReplyBody(BaseModel):
    donor_id: int
    reply: str
    replied_at: Optional[datetime] = None

    @field_validator("reply")
    @classmethod
    def _valid_reply(cls, v: str) -> str:
        if v not in ("yes", "no"):
            raise ValueError('reply must be "yes" or "no"')
        return v


@app.post("/donors/blasts/{blast_id}/simulate-reply")
def simulate_reply(
    blast_id: int, body: SimulateReplyBody, facility_id: int = Depends(get_acting_facility_id)
):
    """DEV-ONLY (see ALLOW_DEV_TEST_TOOLS) — the only way a reply is ever
    recorded, since there's no real provider/webhook. replied_at defaults to
    now but can be backdated for testing fill order.

    Rejects any new reply once the blast is already completed (by real
    current time), regardless of what replied_at the request claims — a
    backdated reply can't retroactively reopen an already-completed blast.
    This is what makes "auto-stops accepting new replies" an absolute gate.
    """
    if not ALLOW_DEV_TEST_TOOLS:
        raise HTTPException(status_code=403, detail="dev test tools are disabled on this server")

    with engine.begin() as conn:
        _require_bloodbank_facility(conn, facility_id)

        blast = conn.execute(
            text("SELECT * FROM blasts WHERE id = :id AND facility_id = :facility_id"),
            {"id": blast_id, "facility_id": facility_id},
        ).mappings().first()
        if blast is None:
            raise HTTPException(status_code=404, detail="blast not found")

        if blast["status"] == "completed":
            raise HTTPException(status_code=400, detail="blast already completed, no longer accepting replies")
        if datetime.now(timezone.utc) >= blast["deadline_at"]:
            _complete_blast_if_needed(conn, blast_id)
            raise HTTPException(status_code=400, detail="blast deadline has passed, no longer accepting replies")

        messaged = conn.execute(
            text("SELECT 1 FROM blast_messages WHERE blast_id = :blast_id AND donor_id = :donor_id"),
            {"blast_id": blast_id, "donor_id": body.donor_id},
        ).first()
        if messaged is None:
            raise HTTPException(status_code=400, detail="this donor was not messaged as part of this blast")

        replied_at = body.replied_at or datetime.now(timezone.utc)
        try:
            conn.execute(
                text(
                    """
                    INSERT INTO blast_replies (blast_id, donor_id, reply, replied_at)
                    VALUES (:blast_id, :donor_id, :reply, :replied_at)
                    """
                ),
                {"blast_id": blast_id, "donor_id": body.donor_id, "reply": body.reply, "replied_at": replied_at},
            )
        except IntegrityError:
            raise HTTPException(status_code=409, detail="this donor has already replied to this blast")

        just_completed = _complete_blast_if_needed(conn, blast_id)
        updated_blast = conn.execute(text("SELECT * FROM blasts WHERE id = :id"), {"id": blast_id}).mappings().first()

    return {"recorded": True, "blast_status": updated_blast["status"], "just_completed": just_completed}


@app.post("/donors/blasts/{blast_id}/dev-force-expire")
def dev_force_expire_blast(blast_id: int, facility_id: int = Depends(get_acting_facility_id)):
    """DEV-ONLY (see ALLOW_DEV_TEST_TOOLS) — forces the deadline into the past
    and immediately runs the completion check, so the deadline-passed path
    can be tested without waiting real hours."""
    if not ALLOW_DEV_TEST_TOOLS:
        raise HTTPException(status_code=403, detail="dev test tools are disabled on this server")

    with engine.begin() as conn:
        _require_bloodbank_facility(conn, facility_id)

        blast = conn.execute(
            text("SELECT * FROM blasts WHERE id = :id AND facility_id = :facility_id"),
            {"id": blast_id, "facility_id": facility_id},
        ).mappings().first()
        if blast is None:
            raise HTTPException(status_code=404, detail="blast not found")

        past = datetime.now(timezone.utc) - timedelta(minutes=1)
        conn.execute(text("UPDATE blasts SET deadline_at = :past WHERE id = :id"), {"past": past, "id": blast_id})

        just_completed = _complete_blast_if_needed(conn, blast_id)
        updated_blast = conn.execute(text("SELECT * FROM blasts WHERE id = :id"), {"id": blast_id}).mappings().first()

    return {"deadline_forced_to_past": True, "blast_status": updated_blast["status"], "just_completed": just_completed}


@app.get("/donors/blasts/{blast_id}/confirmed")
def get_confirmed_donors(blast_id: int, facility_id: int = Depends(get_acting_facility_id)):
    """The real point of a blast: who actually confirmed. Only the first
    target_count YES replies in timestamp order count as confirmed — matches
    _evaluate_blast_completion's own logic, so this can never show more
    confirmed donors than the blast was ever allowed to accept.
    """
    with engine.begin() as conn:
        _require_bloodbank_facility(conn, facility_id)

        blast = conn.execute(
            text("SELECT * FROM blasts WHERE id = :id AND facility_id = :facility_id"),
            {"id": blast_id, "facility_id": facility_id},
        ).mappings().first()
        if blast is None:
            raise HTTPException(status_code=404, detail="blast not found")

        _complete_blast_if_needed(conn, blast_id)  # lazy completion check on read

        rows = conn.execute(
            text(
                """
                SELECT d.name, d.phone, br.replied_at
                FROM blast_replies br
                JOIN donors d ON d.id = br.donor_id
                WHERE br.blast_id = :blast_id AND br.reply = 'yes'
                ORDER BY br.replied_at ASC
                LIMIT :target_count
                """
            ),
            {"blast_id": blast_id, "target_count": blast["target_count"]},
        ).mappings().all()

        updated_blast = conn.execute(text("SELECT * FROM blasts WHERE id = :id"), {"id": blast_id}).mappings().first()

    return {
        "blast_status": updated_blast["status"],
        "target_count": updated_blast["target_count"],
        "confirmed_count": len(rows),
        "confirmed_donors": [dict(row) for row in rows],
    }
