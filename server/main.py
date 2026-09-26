import csv
import hashlib
import io
import json
import math
import os
import secrets
import warnings
from contextlib import nullcontext
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import jwt
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr, field_validator
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
import email_service
from database import engine

load_dotenv()

# Placeholder calendar rule — should become regionally configurable rather than
# a hardcoded constant once there's a real settings mechanism.
DENGUE_SEASON_MONTHS = {6, 7, 8, 9, 10}

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

# A materially higher bar than MIN_DAYS_REQUIRED above: SARIMAX(0,1,4)x(1,0,1,7)
# needs enough runway to estimate a 4-lag MA term plus several full weekly
# seasonal cycles before its fit means anything. Below this, a facility that
# has cleared MIN_DAYS_REQUIRED still gets the plain linear trend
# (forecast_source: "linear_trend"), not SARIMAX.
SARIMAX_MIN_DAYS_REQUIRED = 30

# The fixed, already-validated order from SYNTHETIC_SARIMAX_VALIDATION.md —
# the same order fit_and_cache_synthetic_forecast.py uses for the synthetic
# reference model. Per-facility, AI-chosen order selection is a separate,
# later step; this endpoint always fits this one fixed order once a facility
# clears SARIMAX_MIN_DAYS_REQUIRED.
FACILITY_SARIMAX_ORDER = (0, 1, 4)
FACILITY_SARIMAX_SEASONAL_ORDER = (1, 0, 1, 7)
FACILITY_SARIMAX_LABEL = "SARIMAX(0,1,4)x(1,0,1,7)+dengue"
FACILITY_SARIMAX_MAXITER = 200

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
# Defaults to "*" (any origin) rather than failing closed, since auth here is
# a Bearer token in a header, not a cookie — CORSMiddleware only refuses "*"
# when allow_credentials=True, which this app never sets. "*" is a reasonable
# temporary default before the real frontend URL is known, not a permanent
# choice: set this env var once it is, so it stops being wide open.
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


CORS_ALLOWED_ORIGINS = [_normalize_origin(o) for o in os.environ.get("CORS_ALLOWED_ORIGINS", "*").split(",")]

# Base URL of the deployed frontend, used only to build the link inside a
# password-reset email (e.g. "{FRONTEND_BASE_URL}/reset-password?token=...").
# Defaults to local dev so this works out of the box before it's set in a
# real deployment's environment.
FRONTEND_BASE_URL = os.environ.get("FRONTEND_BASE_URL", "http://localhost:5173").rstrip("/")

# Self-service reset links are meant to be used within minutes of being
# requested — much shorter-lived than a normal session, and long enough that
# an email arriving a little late still works.
PASSWORD_RESET_LINK_EXPIRY = timedelta(hours=1)

VALID_FACILITY_TYPES = {"hospital", "bloodbank"}


def get_current_user(authorization: Optional[str] = Header(default=None)) -> dict:
    """Requires 'Authorization: Bearer <token>'. Real auth (Step 6B)."""
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="not authenticated")
    token = authorization[len("Bearer "):]
    try:
        return auth.decode_access_token(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="session expired, please log in again")
    except (jwt.PyJWTError, ValueError):
        raise HTTPException(status_code=401, detail="invalid authentication token")


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
    return user["facility_id"]


def get_acting_user_id(authorization: Optional[str] = Header(default=None)) -> Optional[int]:
    """Best-effort real user id, for attribution only (upload_history.uploaded_by)
    — never for authorization, that's get_acting_facility_id's job alone.
    Returns None under the DEV-ONLY X-Dev-Facility-Id override with no real
    token behind it, since there's no real user to attribute the upload to
    in that case."""
    user = _try_get_user_from_token(authorization)
    return int(user["sub"]) if user else None


# ─── Notifications (Step 13A) ───────────────────────────────────────────────
# Facility-scoped (see schema_notifications.sql). Two kinds of trigger:
#   - Action-triggered: inserted directly at the point of the state change
#     (request created/accepted/declined, transfer confirmation needed, blast
#     completes) — real-time, no polling needed to generate these.
#   - State-diff-triggered (forecast newly predicting a shortage, a unit
#     newly crossing into near-expiry/critical): there's no scheduler in this
#     app, so instead of a background job, the diff-and-notify check rides
#     along inside the existing endpoint that already computes the relevant
#     state fresh on every call (GET /forecast, GET /inventory) — the first
#     time a blood-bank facility loads its dashboard (or a facility loads its
#     inventory) after a real transition, that's when the notification fires.
#     See _check_forecast_shortage_notifications / _check_expiry_notifications.
NOTIFY_EXPIRY_CRITICAL_DAYS = 3
NOTIFY_EXPIRY_NEAR_DAYS = 7


def _create_notification(conn, facility_id: int, notif_type: str, message: str, link: Optional[str] = None) -> None:
    conn.execute(
        text(
            "INSERT INTO notifications (facility_id, type, message, link) "
            "VALUES (:facility_id, :type, :message, :link)"
        ),
        {"facility_id": facility_id, "type": notif_type, "message": message, "link": link},
    )


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


# ─── Upload undo ────────────────────────────────────────────────────────────
# One preview/apply pair per upload_type, sharing the same eligibility logic
# so what the confirmation screen shows is exactly what the undo itself acts
# on — recomputed fresh both times, never trusting a client-supplied row
# list, since eligibility (a unit's reservation, a donor's blast status) can
# change between when staff opens the confirmation and when they click through.

def _preview_historical_undo(conn, upload_id: int, facility_id: int) -> tuple[list[dict], list[dict]]:
    """Historical snapshot rows have no downstream state to conflict with —
    always a full undo."""
    rows = conn.execute(
        text(
            """
            SELECT snapshot_date, blood_type, units FROM inventory_snapshots
            WHERE upload_history_id = :upload_id AND facility_id = :facility_id
            ORDER BY snapshot_date, blood_type
            """
        ),
        {"upload_id": upload_id, "facility_id": facility_id},
    ).mappings().all()
    return [dict(r) for r in rows], []


def _apply_historical_undo(conn, upload_id: int, facility_id: int) -> tuple[int, list[dict]]:
    eligible, blocked = _preview_historical_undo(conn, upload_id, facility_id)
    result = conn.execute(
        text("DELETE FROM inventory_snapshots WHERE upload_history_id = :upload_id AND facility_id = :facility_id"),
        {"upload_id": upload_id, "facility_id": facility_id},
    )
    return result.rowcount, blocked


def _classify_inventory_undo_rows(rows: list, facility_id: int) -> tuple[list[dict], list[dict]]:
    """Split this upload's units into what's still safe to remove and what
    isn't. Deliberately NOT filtered by facility_id at the query level (see
    callers) — a fully transferred unit's facility_id has already moved to
    the receiving facility, so filtering by the uploading facility would
    silently drop it from both lists instead of correctly reporting it as
    blocked. reserved_for_request_id alone is enough to detect either case
    (it's set the moment a supplier confirms release, before the transfer
    even completes); whether the unit is still physically here or already
    moved just changes the wording."""
    eligible, blocked = [], []
    for r in rows:
        if r["reserved_for_request_id"] is None:
            eligible.append({"din": r["din"], "blood_type": r["blood_type"]})
        else:
            reason = "reserved for a pending transfer" if r["facility_id"] == facility_id else "already transferred to another facility"
            blocked.append({"din": r["din"], "blood_type": r["blood_type"], "reason": reason})
    return eligible, blocked


def _preview_inventory_undo(conn, upload_id: int, facility_id: int) -> tuple[list[dict], list[dict]]:
    rows = conn.execute(
        text(
            """
            SELECT id, din, blood_type, facility_id, reserved_for_request_id FROM blood_units
            WHERE upload_history_id = :upload_id
            ORDER BY din
            """
        ),
        {"upload_id": upload_id},
    ).mappings().all()
    return _classify_inventory_undo_rows(rows, facility_id)


def _apply_inventory_undo(conn, upload_id: int, facility_id: int) -> tuple[int, list[dict]]:
    rows = conn.execute(
        text(
            """
            SELECT id, din, blood_type, facility_id, reserved_for_request_id FROM blood_units
            WHERE upload_history_id = :upload_id
            """
        ),
        {"upload_id": upload_id},
    ).mappings().all()
    eligible_ids = [r["id"] for r in rows if r["reserved_for_request_id"] is None]
    _, blocked = _classify_inventory_undo_rows(rows, facility_id)
    if eligible_ids:
        conn.execute(text("DELETE FROM blood_units WHERE id = ANY(:ids)"), {"ids": eligible_ids})
    return len(eligible_ids), blocked


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


def _check_expiry_notifications(conn, facility_id: int, rows: list) -> None:
    """Runs as a side effect of GET /inventory, reusing that endpoint's own
    query result instead of re-scanning blood_units a second time (rows may
    include already-expired units, since the caller doesn't filter those out
    — skipped below instead). Only ever notifies about a unit the FIRST time
    it reaches a given tier — last_notified_expiry_status is what makes this
    idempotent across repeated loads instead of spamming a notification every
    time the screen is opened while a unit sits at the same status."""
    today = date.today()
    for row in rows:
        if row["expires_date"] < today:
            continue
        days_left = (row["expires_date"] - today).days
        if days_left <= NOTIFY_EXPIRY_CRITICAL_DAYS:
            status = "critical"
        elif days_left <= NOTIFY_EXPIRY_NEAR_DAYS:
            status = "near-expiry"
        else:
            status = "ok"

        if status in ("critical", "near-expiry") and status != row["last_notified_expiry_status"]:
            _create_notification(
                conn, facility_id, "unit_expiry",
                f"{row['din']} ({row['blood_type']}) is now {status.replace('-', ' ')} — {days_left}d left",
                "inventory",
            )
            conn.execute(
                text("UPDATE blood_units SET last_notified_expiry_status = :status WHERE id = :id"),
                {"status": status, "id": row["id"]},
            )


def _check_forecast_shortage_notifications(conn, facility_id: int, alerts: list[dict]) -> None:
    """Runs as a side effect of GET /forecast, blood-bank branches only —
    called with whatever `alerts` that call already computed, so this never
    re-derives the trend/synthetic-model logic itself. forecast_alert_state
    is what makes "newly flips to predicting a shortage" mean an actual
    transition (false -> true), not "still alerting from before".

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
    alerting_types = {a["type"] for a in alerts}

    for alert in alerts:
        blood_type = alert["type"]
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
                f"{blood_type} forecast now predicts a shortage within {alert['days_until_threshold']} days",
                "dashboard",
            )

    # Clearing alerting back to false never creates a notification either way,
    # so there's no race to protect against here — a plain conditional UPDATE
    # (only touching rows that are currently TRUE) is enough.
    currently_tracked = conn.execute(
        text("SELECT blood_type FROM forecast_alert_state WHERE facility_id = :facility_id AND alerting = TRUE"),
        {"facility_id": facility_id},
    ).scalars().all()
    for blood_type in set(currently_tracked) - alerting_types:
        conn.execute(
            text(
                """
                UPDATE forecast_alert_state SET alerting = FALSE, updated_at = now()
                WHERE facility_id = :facility_id AND blood_type = :blood_type AND alerting = TRUE
                """
            ),
            {"facility_id": facility_id, "blood_type": blood_type},
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


# NOTE ON THE AI MODEL-SELECTION FEATURE: deliberately NOT wired in here.
# train_sarimax_selector.py builds and evaluates a decision-tree selector
# that chooses among sarimax_selector_common.CANDIDATE_LIBRARY, but its own
# evaluation (AI-recommended config vs this fixed default vs a full
# Box-Jenkins search, on held-out synthetic series) did not clear the bar
# set in advance — see that script's STOP CONDITION CHECK output. Per that
# script's own explicit instruction, a selector that doesn't beat this fixed
# default is not integrated. This function still hard-codes
# FACILITY_SARIMAX_ORDER/FACILITY_SARIMAX_SEASONAL_ORDER exactly as it did
# before that work started.
def _fit_sarimax_facility_forecast(daily_series: list[tuple[date, int]], today: date) -> Optional[dict]:
    """Fits FACILITY_SARIMAX_ORDER/FACILITY_SARIMAX_SEASONAL_ORDER with a
    dengue-season exogenous regressor on one blood type's own resampled
    daily series (see _resample_daily_series), and forecasts
    max(FORECAST_CHECKPOINTS) days past `today`.

    Pure — no DB access — so it's directly unit-testable the same way
    _linear_trend and _prediction_interval_half_width are. The caller is
    responsible for resampling real history into `daily_series` first and
    for caching the result; this function always actually fits.

    `daily_series` must already extend through `today` (its last entry's
    date must equal `today`) — that's what makes checkpoint 0 well-defined.
    Checkpoint 0 uses today's real observed value directly rather than a
    model estimate: it's a known fact, not a forecast, so there's nothing to
    gain by substituting a fitted value the way the linear-trend path does.

    Returns None — never raises — whenever there isn't enough data to
    attempt a fit, the fit doesn't converge, or it converges to a
    non-finite/degenerate forecast. The caller falls back to a linear trend
    for that blood type whenever this returns None; a bad fit here must
    never 500 the /forecast endpoint.
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

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            fit = SARIMAX(
                endog, exog=exog,
                order=FACILITY_SARIMAX_ORDER, seasonal_order=FACILITY_SARIMAX_SEASONAL_ORDER,
                trend=None, enforce_stationarity=False, enforce_invertibility=False,
            ).fit(disp=False, maxiter=FACILITY_SARIMAX_MAXITER)

        if not fit.mle_retvals.get("converged", False):
            return None

        forecast_res = fit.get_forecast(steps=horizon, exog=future_exog)
        point = forecast_res.predicted_mean
        ci = forecast_res.conf_int(alpha=1 - FORECAST_INTERVAL_CONFIDENCE)
    except Exception:
        # Any statsmodels failure (non-convergence raised as an exception,
        # a singular/rank-deficient fit, a linear-algebra error on a
        # pathological series, ...) falls back to the linear trend for this
        # blood type rather than ever reaching the caller as a 500.
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

    return {"checkpoints": checkpoints, "exog_included": True, "model_order": FACILITY_SARIMAX_LABEL}


def _get_or_fit_cached_sarimax(
    conn, facility_id: int, blood_type: str, daily_series: list[tuple[date, int]], today: date
) -> Optional[dict]:
    """Reads facility_forecast_cache for this facility+type's checkpoint
    dates; reuses it only if every checkpoint is present AND was trained
    through `today` (trained_through_date == today). Otherwise fits fresh
    via _fit_sarimax_facility_forecast and replaces the cached rows.

    Because inventory_snapshots only gains a new date for this
    facility+type at most once per day, this bounds a real refit to at most
    once per day per facility+type — same request that writes today's
    snapshot is the same request that (at most) triggers one refit, not a
    refit on every dashboard poll.
    """
    checkpoint_dates = [today + timedelta(days=c) for c in FORECAST_CHECKPOINTS]
    cached_rows = conn.execute(
        text(
            """
            SELECT forecast_date, forecast_units, lower_units, upper_units, trained_through_date
            FROM facility_forecast_cache
            WHERE facility_id = :facility_id AND blood_type = :blood_type AND forecast_date = ANY(:dates)
            """
        ),
        {"facility_id": facility_id, "blood_type": blood_type, "dates": checkpoint_dates},
    ).mappings().all()

    by_date = {row["forecast_date"]: row for row in cached_rows}
    fresh = len(by_date) == len(checkpoint_dates) and all(
        row["trained_through_date"] == today for row in cached_rows
    )
    if fresh:
        return {
            "checkpoints": {
                c: {
                    "units": int(by_date[cd]["forecast_units"]),
                    "lower": int(by_date[cd]["lower_units"]),
                    "upper": int(by_date[cd]["upper_units"]),
                }
                for c, cd in zip(FORECAST_CHECKPOINTS, checkpoint_dates)
            },
            "exog_included": True,
            "model_order": FACILITY_SARIMAX_LABEL,
        }

    result = _fit_sarimax_facility_forecast(daily_series, today)
    if result is None:
        return None

    conn.execute(
        text("DELETE FROM facility_forecast_cache WHERE facility_id = :facility_id AND blood_type = :blood_type"),
        {"facility_id": facility_id, "blood_type": blood_type},
    )
    conn.execute(
        text(
            """
            INSERT INTO facility_forecast_cache
                (facility_id, blood_type, forecast_date, forecast_units, lower_units, upper_units, trained_through_date, model_order)
            VALUES
                (:facility_id, :blood_type, :forecast_date, :forecast_units, :lower_units, :upper_units, :trained_through_date, :model_order)
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
    }


def _restock_status(recommendations: list[dict]) -> str:
    return "action_needed" if recommendations else "on_track"


# SYNTHETIC — must match MODEL_LABEL in fit_and_cache_synthetic_forecast.py.
# Validated on generated data only; see SYNTHETIC_SARIMAX_VALIDATION.md for the
# full identification -> estimation -> diagnostic-checking -> cross-validation
# writeup, including the unresolved Ljung-Box failure this model was still
# shipped with (cross-validated forecast accuracy was checked directly instead —
# see that doc's Phase 4 section for why that's a defensible tradeoff here).
SYNTHETIC_MODEL_LABEL = "SARIMAX(0,1,4)x(1,0,1,7)+dengue"


def _build_synthetic_stand_in_forecast(
    conn, today: date, current_units_by_type: dict[str, int], thresholds: dict[str, int],
    maximum_thresholds: dict[str, int], is_dengue_season: bool,
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
        if recommendation is not None:
            restock_recommendations.append(recommendation)

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
        {"day": "Today" if c == 0 else f"Day {c}", "units": total_by_checkpoint[c]}
        for c in FORECAST_CHECKPOINTS
    ]
    restock_recommendations.sort(key=lambda r: r["days_until_breach"])
    return {
        "series": series,
        "alerts": alerts,
        "restock_recommendations": restock_recommendations,
        "restock_status": _restock_status(restock_recommendations),
    }


app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"database unavailable: {exc}")
    # cors_allowed_origins deliberately included: the list of allowed origins
    # isn't sensitive (any real cross-origin request already reveals whether
    # its own origin is on it), and this is the one live, no-dashboard-needed
    # way to confirm exactly what CORS_ALLOWED_ORIGINS actually parsed to on
    # whatever's currently running — the normal question when a CORS error
    # shows up is "is the env var wrong, or did the redeploy not pick it up
    # yet," and this answers both without needing host log/dashboard access.
    return {"status": "ok", "database": "connected", "cors_allowed_origins": CORS_ALLOWED_ORIGINS}


@app.get("/notifications")
def list_notifications(facility_id: int = Depends(get_acting_facility_id)):
    """Most recent first, capped at 50 — this is a notification feed, not an
    archive. The frontend polls this on load and on a short interval; there's
    no push/WebSocket involved."""
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT id, type, message, link, read_at, created_at
                FROM notifications WHERE facility_id = :facility_id
                ORDER BY created_at DESC LIMIT 50
                """
            ),
            {"facility_id": facility_id},
        ).mappings().all()
    return [dict(row) for row in rows]


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

    return {"removed_count": removed_count, "blocked": blocked, "undone_at": undone_at}


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
def get_inventory(facility_id: int = Depends(get_acting_facility_id)):
    """The logged-in user's own facility's stock only — blood_units holds every
    facility's inventory (for the availability search), so this must filter.

    Still includes expired units (unlike /inventory/summary and every
    availability/forecast query) — staff needs to see them here to know
    what's in the expired backlog before archiving it via
    POST /inventory/archive-expired. Only excludes units already archived,
    so the working list declutters once that backlog is cleared without
    losing the rows (archived_at IS NULL, never a DELETE)."""
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT id, din, blood_type, component, location, volume_ml,
                       collected_date, expires_date, last_notified_expiry_status
                FROM blood_units
                WHERE facility_id = :facility_id AND archived_at IS NULL
                ORDER BY expires_date ASC
                """
            ),
            {"facility_id": facility_id},
        ).mappings().all()
        # Side effect: notifies once per unit the first time it crosses into
        # near-expiry/critical — reuses the rows just fetched above instead
        # of re-querying blood_units a second time (see _check_expiry_notifications).
        _check_expiry_notifications(conn, facility_id, rows)
    return [
        {
            "din": r["din"], "blood_type": r["blood_type"], "component": r["component"],
            "location": r["location"], "volume_ml": r["volume_ml"],
            "collected_date": r["collected_date"], "expires_date": r["expires_date"],
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
    """
    with engine.begin() as conn:
        result = conn.execute(
            text(
                """
                UPDATE blood_units SET archived_at = now()
                WHERE facility_id = :facility_id AND expires_date < CURRENT_DATE AND archived_at IS NULL
                """
            ),
            {"facility_id": facility_id},
        )
    return {"archived_count": result.rowcount}


@app.get("/inventory/summary")
def get_inventory_summary(facility_id: int = Depends(get_acting_facility_id)):
    """Per-type USABLE unit counts (the logged-in user's own facility)
    against this facility's own configured minimum thresholds.

    A "unit" is one blood_units row (one bag/donation), not a volume
    measurement. Types with zero current stock still appear, with units=0,
    so shortages are visible. Thresholds are per-facility (see
    blood_type_thresholds's schema comment) — every facility gets its own 8
    rows seeded at creation time (admin_create_facility_account), so this
    join should never miss a row for a real facility.

    expires_date >= CURRENT_DATE only: an expired unit was never usable, so
    it can't count toward "how much stock does this facility have" any more
    than it can count as available to fulfill another facility's request
    (see GET /facilities/nearby, which has always filtered this way) — a
    unit that's excluded from availability elsewhere but still inflating
    this count would just be a second, inconsistent definition of "stock."
    """
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT t.blood_type, t.minimum_units, t.maximum_units, COALESCE(c.unit_count, 0) AS units
                FROM blood_type_thresholds t
                LEFT JOIN (
                    SELECT blood_type, count(*) AS unit_count
                    FROM blood_units
                    WHERE facility_id = :facility_id AND expires_date >= CURRENT_DATE
                    GROUP BY blood_type
                ) c ON c.blood_type = t.blood_type
                WHERE t.facility_id = :facility_id
                ORDER BY t.blood_type
                """
            ),
            {"facility_id": facility_id},
        ).mappings().all()
    return [dict(row) for row in rows]


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
async def upload_inventory(
    file: UploadFile = File(...),
    facility_id: int = Depends(get_acting_facility_id),
    uploaded_by: Optional[int] = Depends(get_acting_user_id),
):
    """Real CSV upload for the Inventory screen (Step 8A).

    Duplicate handling deliberately differs from Donors (Step 7A), which
    upserts by (facility_id, phone) — here `din` is globally UNIQUE across all
    facilities (schema.sql), not scoped per facility, because a DIN identifies
    one physical blood bag. Re-uploading a DIN that already belongs to *this*
    facility is treated as a data-entry correction and updates the row in
    place. A DIN that already belongs to a *different* facility is rejected
    per-row instead of silently reassigned — moving stock between facilities
    is only ever supposed to happen through the dual-confirmation transfer
    flow, not a CSV import.

    The INSERT ... ON CONFLICT ... WHERE facility_id = :facility_id below
    does the ownership check atomically in the same statement as the
    insert/update, so there's no separate SELECT-then-write race window.
    """
    if not (file.filename or "").lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="only .csv files are accepted")

    raw_bytes = await file.read()
    try:
        csv_text = raw_bytes.decode("utf-8-sig")  # utf-8-sig tolerates a BOM from Excel exports
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="file must be UTF-8 encoded")

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


def _build_hospital_threshold_view(conn, facility_id: int, is_dengue_season: bool) -> dict:
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
            """
            SELECT t.blood_type, t.minimum_units, COALESCE(c.unit_count, 0) AS units
            FROM blood_type_thresholds t
            LEFT JOIN (
                SELECT blood_type, count(*) AS unit_count
                FROM blood_units
                WHERE facility_id = :facility_id AND expires_date >= CURRENT_DATE
                GROUP BY blood_type
            ) c ON c.blood_type = t.blood_type
            WHERE t.facility_id = :facility_id
            ORDER BY t.blood_type
            """
        ),
        {"facility_id": facility_id},
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
async def upload_historical_inventory_snapshots(
    file: UploadFile = File(...),
    facility_id: int = Depends(get_acting_facility_id),
    uploaded_by: Optional[int] = Depends(get_acting_user_id),
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
    """
    with engine.connect() as conn:
        facility_type = conn.execute(
            text("SELECT facility_type FROM facilities WHERE id = :id"), {"id": facility_id}
        ).scalar()
    if facility_type != "bloodbank":
        raise HTTPException(status_code=403, detail="historical data upload is only available to blood bank facilities")

    if not (file.filename or "").lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="only .csv files are accepted")

    raw_bytes = await file.read()
    try:
        csv_text = raw_bytes.decode("utf-8-sig")  # utf-8-sig tolerates a BOM from Excel exports
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="file must be UTF-8 encoded")

    valid_rows, errors = _parse_historical_snapshot_csv(csv_text, date.today())

    rows_processed = len(valid_rows)
    with engine.begin() as conn:
        upload_id = _start_upload_record(conn, facility_id, "historical_stock", uploaded_by, file.filename, csv_text)

        # One statement for the whole file instead of one round trip per row.
        batch = _last_wins(valid_rows, lambda r: (r["snapshot_date"], r["blood_type"]))
        if batch:
            conn.execute(
                text(
                    """
                    INSERT INTO inventory_snapshots (snapshot_date, blood_type, units, facility_id, upload_history_id)
                    SELECT t.snapshot_date, t.blood_type, t.units, :facility_id, :upload_id
                    FROM unnest(CAST(:snapshot_date AS date[]), CAST(:blood_type AS text[]), CAST(:units AS int[]))
                        AS t(snapshot_date, blood_type, units)
                    ON CONFLICT (snapshot_date, blood_type, facility_id)
                    DO UPDATE SET units = EXCLUDED.units, upload_history_id = EXCLUDED.upload_history_id
                    """
                ),
                {**{c: [r[c] for r in batch] for c in ("snapshot_date", "blood_type", "units")},
                 "facility_id": facility_id, "upload_id": upload_id},
            )

        _finish_upload_record(conn, upload_id, rows_processed, errors)

    with engine.connect() as conn:
        days_of_history = conn.execute(
            text("SELECT count(DISTINCT snapshot_date) FROM inventory_snapshots WHERE facility_id = :facility_id"),
            {"facility_id": facility_id},
        ).scalar()

    return {"rows_processed": rows_processed, "errors": errors, "days_of_history": days_of_history, "min_days_required": MIN_DAYS_REQUIRED}


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
    (expires_date >= CURRENT_DATE only — an expired unit is never counted,
    here or anywhere else stock is treated as available) as a snapshot
    (idempotent per day, per facility). Three tiers, gated purely on
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
    today = date.today()
    is_dengue_season = today.month in DENGUE_SEASON_MONTHS

    with engine.begin() as conn:
        facility_type = conn.execute(
            text("SELECT facility_type FROM facilities WHERE id = :id"), {"id": facility_id}
        ).scalar()
        if facility_type is None:
            raise HTTPException(status_code=500, detail="acting facility not found")

        if facility_type == "hospital":
            return _build_hospital_threshold_view(conn, facility_id, is_dengue_season)

        conn.execute(
            text(
                """
                INSERT INTO inventory_snapshots (snapshot_date, blood_type, units, facility_id)
                SELECT :today, blood_type, count(*), :facility_id
                FROM blood_units
                WHERE facility_id = :facility_id AND expires_date >= CURRENT_DATE
                GROUP BY blood_type
                ON CONFLICT (snapshot_date, blood_type, facility_id)
                DO UPDATE SET units = EXCLUDED.units
                """
            ),
            {"today": today, "facility_id": facility_id},
        )

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

        threshold_rows = conn.execute(
            text(
                "SELECT blood_type, minimum_units, maximum_units FROM blood_type_thresholds "
                "WHERE facility_id = :facility_id"
            ),
            {"facility_id": facility_id},
        ).mappings().all()
        thresholds = {row["blood_type"]: row["minimum_units"] for row in threshold_rows}
        maximum_thresholds = {row["blood_type"]: row["maximum_units"] for row in threshold_rows}

        distinct_dates = sorted({row["snapshot_date"] for row in history_rows})
        days_of_history = len(distinct_dates)

        if days_of_history < MIN_DAYS_REQUIRED:
            current_units_by_type = {
                row["blood_type"]: row["units"] for row in history_rows if row["snapshot_date"] == today
            }
            stand_in = _build_synthetic_stand_in_forecast(
                conn, today, current_units_by_type, thresholds, maximum_thresholds, is_dengue_season
            )

        if days_of_history < MIN_DAYS_REQUIRED:
            if stand_in is not None:
                _check_forecast_shortage_notifications(conn, facility_id, stand_in["alerts"])
                return {
                    "view": "forecast",
                    "has_sufficient_history": False,
                    "days_of_history": days_of_history,
                    "min_days_required": MIN_DAYS_REQUIRED,
                    "is_dengue_season": is_dengue_season,
                    "forecast_source": "synthetic_model_stand_in",
                    "synthetic_model_label": SYNTHETIC_MODEL_LABEL,
                    "series": stand_in["series"],
                    "alerts": stand_in["alerts"],
                    "restock_recommendations": stand_in["restock_recommendations"],
                    "restock_status": stand_in["restock_status"],
                }
            return {
                "view": "forecast",
                "has_sufficient_history": False,
                "days_of_history": days_of_history,
                "min_days_required": MIN_DAYS_REQUIRED,
                "is_dengue_season": is_dengue_season,
                "forecast_source": "none",
                "series": [],
                "alerts": [],
                "restock_recommendations": [],
                "restock_status": None,
            }

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
    sarimax_fallback_types: list[str] = []
    alerts = []
    restock_recommendations = []
    total_by_checkpoint = {c: 0 for c in FORECAST_CHECKPOINTS}
    # Lower/upper are summed across types the same way the point estimate is —
    # each type's own prediction interval, added up. That's a conservative
    # (slightly wider than the tightest possible) band for the total, but it's
    # simple, honest, and consistent with how total_by_checkpoint itself is
    # already just a sum of independent per-type projections.
    lower_by_checkpoint = {c: 0 for c in FORECAST_CHECKPOINTS}
    upper_by_checkpoint = {c: 0 for c in FORECAST_CHECKPOINTS}
    band_available = False

    with (engine.begin() if use_sarimax else nullcontext()) as maybe_conn:
        for blood_type, points in by_type.items():
            minimum = thresholds.get(blood_type)
            method_used = "linear_trend"
            checkpoint_values: dict[int, tuple[float, Optional[float], Optional[float]]] = {}
            slope: Optional[float] = None

            sarimax_result = None
            if use_sarimax:
                daily_series = _resample_daily_series(raw_units_by_type[blood_type], observed_dates, today)
                sarimax_result = _get_or_fit_cached_sarimax(maybe_conn, facility_id, blood_type, daily_series, today)

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
            if recommendation is not None:
                restock_recommendations.append(recommendation)

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
                    f"{FACILITY_SARIMAX_LABEL} fit on this facility's own {len(daily_series)}-day history "
                    f"for {blood_type}; projected to fall below minimum threshold ({minimum} units) in "
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

    series = [
        {
            "day": "Today" if c == 0 else f"Day {c}",
            "units": total_by_checkpoint[c],
            "lower": lower_by_checkpoint[c] if band_available else None,
            "upper": upper_by_checkpoint[c] if band_available else None,
        }
        for c in FORECAST_CHECKPOINTS
    ]

    with engine.begin() as conn:
        _check_forecast_shortage_notifications(conn, facility_id, alerts)

    restock_recommendations.sort(key=lambda r: r["days_until_breach"])

    forecast_source = "sarimax_facility_history" if use_sarimax else "linear_trend"
    response = {
        "view": "forecast",
        "has_sufficient_history": True,
        "days_of_history": days_of_history,
        "min_days_required": MIN_DAYS_REQUIRED,
        "sarimax_min_days_required": SARIMAX_MIN_DAYS_REQUIRED,
        "is_dengue_season": is_dengue_season,
        "forecast_source": forecast_source,
        "interval_confidence": FORECAST_INTERVAL_CONFIDENCE if band_available else None,
        "series": series,
        "alerts": alerts,
        "restock_recommendations": restock_recommendations,
        "restock_status": _restock_status(restock_recommendations),
    }
    if forecast_source == "sarimax_facility_history":
        response["sarimax_model_label"] = FACILITY_SARIMAX_LABEL
        response["sarimax_fallback_types"] = sarimax_fallback_types
    return response


@app.get("/facilities")
def list_facilities():
    """Plain facility list — powers the dev-only facility switcher dropdown."""
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT id, name, facility_type FROM facilities ORDER BY facility_type, name")
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
                       department, doh_license_number, profile_completed
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
    doh_license_number: str

    @field_validator("address", "department", "doh_license_number")
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
    """The other half of admin onboarding (see POST /admin/facilities):
    fills in the location/department/DOH license an admin-created facility
    doesn't have yet, and flips profile_completed so the frontend stops
    gating this facility into the complete-profile screen. The map-confirmed
    latitude/longitude are what Emergency Sourcing's distance ranking
    actually uses once this facility becomes a searchable candidate.

    Also doubles as the Edit Facility Profile endpoint (Account menu) —
    there's nothing first-time-only about this UPDATE, so a facility that's
    already complete can call it again to correct its address/pin/department/
    DOH license later. GET /facilities/me is what the edit form prefills from."""
    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                UPDATE facilities SET
                    address = :address, latitude = :latitude, longitude = :longitude,
                    department = :department, doh_license_number = :doh_license_number,
                    profile_completed = true
                WHERE id = :facility_id
                RETURNING id, name, facility_type, address, latitude, longitude,
                          department, doh_license_number, profile_completed
                """
            ),
            {
                "address": body.address,
                "latitude": body.latitude,
                "longitude": body.longitude,
                "department": body.department,
                "doh_license_number": body.doh_license_number,
                "facility_id": facility_id,
            },
        ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="facility not found")
    return dict(row)


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
# facility (admin_create_facility_account below) — a facility can edit its
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


def _evaluate_facility_stock(stock_by_type: dict[str, int], thresholds: dict[str, int], blood_type: str, quantity: int) -> dict:
    """Pure: given one facility's non-expired stock counts and that SAME
    facility's own safety thresholds, compute exact-type availability plus
    any compatible-but-not-exact donor types that themselves have enough
    usable stock to cover the request.

    `available`/`usable_units` stay strictly about the exact type — compatible
    alternatives are surfaced separately, not blended into that signal.
    """
    matching_units = stock_by_type.get(blood_type, 0)
    usable_units = matching_units - thresholds.get(blood_type, 0)

    alternatives = []
    for alt_type in BLOOD_COMPATIBILITY.get(blood_type, []):
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
    blood_type: str, quantity: int = 1, acting_facility_id: int = Depends(get_acting_facility_id)
):
    """Blood banks ranked in three tiers — exact-type available, then
    unavailable-but-with-a-compatible-alternative in stock, then fully
    unavailable — with real haversine distance from the logged-in user's own
    facility as the tiebreaker within each tier (see _evaluate_facility_stock
    for the availability/alternatives check).

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
    with engine.connect() as conn:
        origin = conn.execute(
            text("SELECT latitude, longitude FROM facilities WHERE id = :id"),
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

        stock_rows = conn.execute(
            text(
                """
                SELECT facility_id, blood_type, count(*) AS unit_count
                FROM blood_units
                WHERE expires_date >= CURRENT_DATE
                GROUP BY facility_id, blood_type
                """
            )
        ).mappings().all()

        # profile_completed facilities only — an admin-onboarded blood bank
        # with no confirmed location yet can't be distance-ranked, so it
        # can't appear as a search candidate until it completes its profile.
        facility_rows = conn.execute(
            text(
                """
                SELECT id, name, facility_type, address, latitude, longitude
                FROM facilities
                WHERE facility_type = 'bloodbank' AND id != :acting_facility_id AND profile_completed
                """
            ),
            {"acting_facility_id": acting_facility_id},
        ).mappings().all()

    stock_by_facility: dict[int, dict[str, int]] = {}
    for row in stock_rows:
        stock_by_facility.setdefault(row["facility_id"], {})[row["blood_type"]] = row["unit_count"]

    results = []
    for facility in facility_rows:
        evaluation = _evaluate_facility_stock(
            stock_by_facility.get(facility["id"], {}), thresholds_by_facility.get(facility["id"], {}),
            blood_type, quantity,
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
                """
                SELECT din, blood_type, component, expires_date
                FROM blood_units WHERE din = :din AND facility_id = :facility_id
                """
            ),
            {"din": din, "facility_id": acting_facility_id},
        ).mappings().first()
        if unit is None:
            raise HTTPException(status_code=404, detail="unit not found at your facility")

        days_left = (unit["expires_date"] - date.today()).days
        if days_left < 0:
            raise HTTPException(status_code=400, detail="this unit has already expired")
        if days_left > 7:
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
                WHERE facility_type = 'hospital' AND id != :acting_facility_id AND profile_completed
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
                f"{origin['name']} has a near-expiry {unit['blood_type']} unit ({unit['component']}) — "
                f"{days_left}d left, {distance_km}km away",
                f"sourcing:{unit['blood_type']}",
            )
            notified_names.append(hospital["name"])

    return {"notified_count": len(notified_names), "notified_facilities": notified_names}


class CreateRequestBody(BaseModel):
    supplying_facility_id: int
    blood_type: str
    quantity: int
    emergency_type: str

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
        supplying_facility_type = conn.execute(
            text("SELECT facility_type FROM facilities WHERE id = :id"), {"id": body.supplying_facility_id}
        ).scalar()
        if supplying_facility_type != "bloodbank":
            raise HTTPException(status_code=400, detail="a facility can only request from a blood bank")

        row = conn.execute(
            text(
                """
                INSERT INTO requests
                    (requesting_facility_id, supplying_facility_id, blood_type, quantity, emergency_type, status)
                VALUES
                    (:requesting_facility_id, :supplying_facility_id, :blood_type, :quantity, :emergency_type, 'pending')
                RETURNING id, requesting_facility_id, supplying_facility_id, blood_type, quantity,
                          emergency_type, status, created_at
                """
            ),
            {
                "requesting_facility_id": requesting_facility_id,
                "supplying_facility_id": body.supplying_facility_id,
                "blood_type": body.blood_type,
                "quantity": body.quantity,
                "emergency_type": emergency_type,
            },
        ).mappings().first()

        requester = conn.execute(
            text("SELECT name FROM facilities WHERE id = :id"), {"id": requesting_facility_id}
        ).mappings().first()
        _create_notification(
            conn, body.supplying_facility_id, "incoming_request",
            f"New {body.quantity}-unit {body.blood_type} request from {requester['name']}",
            f"requests:{row['id']}",
        )
    return dict(row)


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


@app.get("/requests")
def list_requests(facility_id: int = Depends(get_acting_facility_id)):
    """The logged-in user's own facility's outgoing requests, with pending ones
    priority-sorted (see _apply_priority_sort) and all other statuses left in
    natural order."""
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT r.id, r.requesting_facility_id, r.supplying_facility_id,
                       r.blood_type, r.quantity, r.emergency_type, r.status, r.created_at,
                       r.supplier_confirmed_at, r.requester_confirmed_at,
                       sf.name AS supplying_facility_name
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
def list_incoming_requests(facility_id: int = Depends(get_acting_facility_id)):
    """Requests where the acting facility (dev switcher, or Riverside by
    default) is the supplier — the "what do I need to act on" inbox."""
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT r.id, r.requesting_facility_id, r.supplying_facility_id,
                       r.blood_type, r.quantity, r.emergency_type, r.status, r.created_at,
                       r.supplier_confirmed_at, r.requester_confirmed_at,
                       rf.name AS requesting_facility_name
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
def accept_request(request_id: int, facility_id: int = Depends(get_acting_facility_id)):
    with engine.begin() as conn:
        req = conn.execute(text("SELECT * FROM requests WHERE id = :id"), {"id": request_id}).mappings().first()
        if req is None:
            raise HTTPException(status_code=404, detail="request not found")
        if req["supplying_facility_id"] != facility_id:
            raise HTTPException(status_code=403, detail="only the supplying facility can accept this request")
        if req["status"] != "pending":
            raise HTTPException(status_code=400, detail=f"cannot accept a request with status {req['status']!r}")
        result = conn.execute(
            text("UPDATE requests SET status = 'accepted' WHERE id = :id AND status = 'pending'"),
            {"id": request_id},
        )
        if result.rowcount == 0:
            raise HTTPException(status_code=409, detail="request status changed concurrently; refresh and try again")

        requester = conn.execute(
            text("SELECT name FROM facilities WHERE id = :id"), {"id": req["requesting_facility_id"]}
        ).mappings().first()
        supplier = conn.execute(
            text("SELECT name FROM facilities WHERE id = :id"), {"id": req["supplying_facility_id"]}
        ).mappings().first()
        _create_notification(
            conn, req["requesting_facility_id"], "request_accepted",
            f"{supplier['name']} accepted your {req['blood_type']} request",
            f"requests:{request_id}",
        )
        _create_notification(
            conn, req["supplying_facility_id"], "transfer_confirmation_needed",
            f"Confirm release for {requester['name']}'s {req['blood_type']} request",
            f"requests:{request_id}",
        )
    return {"id": request_id, "status": "accepted"}


@app.post("/requests/{request_id}/decline")
def decline_request(request_id: int, facility_id: int = Depends(get_acting_facility_id)):
    with engine.begin() as conn:
        req = conn.execute(text("SELECT * FROM requests WHERE id = :id"), {"id": request_id}).mappings().first()
        if req is None:
            raise HTTPException(status_code=404, detail="request not found")
        if req["supplying_facility_id"] != facility_id:
            raise HTTPException(status_code=403, detail="only the supplying facility can decline this request")
        if req["status"] != "pending":
            raise HTTPException(status_code=400, detail=f"cannot decline a request with status {req['status']!r}")
        result = conn.execute(
            text("UPDATE requests SET status = 'declined' WHERE id = :id AND status = 'pending'"),
            {"id": request_id},
        )
        if result.rowcount == 0:
            raise HTTPException(status_code=409, detail="request status changed concurrently; refresh and try again")

        supplier = conn.execute(
            text("SELECT name FROM facilities WHERE id = :id"), {"id": req["supplying_facility_id"]}
        ).mappings().first()
        _create_notification(
            conn, req["requesting_facility_id"], "request_declined",
            f"{supplier['name']} declined your {req['blood_type']} request",
            f"requests:{request_id}",
        )
    return {"id": request_id, "status": "declined"}


@app.post("/requests/{request_id}/cancel")
def cancel_request(request_id: int, facility_id: int = Depends(get_acting_facility_id)):
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
            text("UPDATE requests SET status = 'cancelled' WHERE id = :id AND status = 'pending'"),
            {"id": request_id},
        )
        if result.rowcount == 0:
            raise HTTPException(status_code=409, detail="request status changed concurrently; refresh and try again")

        requester = conn.execute(
            text("SELECT name FROM facilities WHERE id = :id"), {"id": req["requesting_facility_id"]}
        ).mappings().first()
        _create_notification(
            conn, req["supplying_facility_id"], "request_cancelled",
            f"{requester['name']} cancelled their {req['blood_type']} request",
            f"requests:{request_id}",
        )
    return {"id": request_id, "status": "cancelled"}


def _select_fefo_units(candidates: list[dict], quantity: int) -> list[dict]:
    """FEFO (First-Expired-First-Out): pick the `quantity` candidates with the
    earliest expires_date. Raises if fewer than `quantity` are available —
    we don't silently fulfill a partial quantity and call it done."""
    sorted_candidates = sorted(candidates, key=lambda u: u["expires_date"])
    if len(sorted_candidates) < quantity:
        raise ValueError(
            f"only {len(sorted_candidates)} matching unreserved, non-expired units available; need {quantity}"
        )
    return sorted_candidates[:quantity]


@app.post("/requests/{request_id}/confirm-release")
def confirm_release(request_id: int, facility_id: int = Depends(get_acting_facility_id)):
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

        # FOR UPDATE locks the matching rows for the rest of this transaction:
        # a concurrent confirm-release (e.g. for a different request on the
        # same facility/blood_type) that tries to select overlapping rows
        # blocks until we commit, then re-evaluates reserved_for_request_id
        # IS NULL against our now-committed reservation — so it naturally
        # excludes whatever we just claimed instead of racing to overwrite it.
        candidates = conn.execute(
            text(
                """
                SELECT id, expires_date FROM blood_units
                WHERE facility_id = :facility_id AND blood_type = :blood_type
                  AND expires_date >= CURRENT_DATE AND reserved_for_request_id IS NULL
                FOR UPDATE
                """
            ),
            {"facility_id": facility_id, "blood_type": req["blood_type"]},
        ).mappings().all()

        try:
            selected = _select_fefo_units([dict(c) for c in candidates], req["quantity"])
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        conn.execute(
            text(
                "UPDATE blood_units SET reserved_for_request_id = :rid "
                "WHERE id = ANY(:uids) AND reserved_for_request_id IS NULL"
            ),
            {"rid": request_id, "uids": [unit["id"] for unit in selected]},
        )
        result = conn.execute(
            text(
                "UPDATE requests SET supplier_confirmed_at = now() "
                "WHERE id = :id AND status = 'accepted' AND supplier_confirmed_at IS NULL"
            ),
            {"id": request_id},
        )
        if result.rowcount == 0:
            raise HTTPException(status_code=409, detail="release already confirmed or request status changed concurrently")

        supplier = conn.execute(
            text("SELECT name FROM facilities WHERE id = :id"), {"id": req["supplying_facility_id"]}
        ).mappings().first()
        _create_notification(
            conn, req["requesting_facility_id"], "transfer_confirmation_needed",
            f"{supplier['name']} released your {req['blood_type']} order — confirm receipt",
            f"requests:{request_id}",
        )

    return {"id": request_id, "reserved_unit_ids": [u["id"] for u in selected]}


@app.post("/requests/{request_id}/confirm-receipt")
def confirm_receipt(request_id: int, facility_id: int = Depends(get_acting_facility_id)):
    """Requester confirms receipt: this is the actual transfer — reassigns
    facility_id on the units the supplier reserved, then marks completed.
    Requires supplier to have confirmed release first (enforced ordering)."""
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

        result = conn.execute(
            text(
                """
                UPDATE blood_units
                SET facility_id = :requesting_facility_id, reserved_for_request_id = NULL
                WHERE reserved_for_request_id = :request_id
                """
            ),
            {"requesting_facility_id": req["requesting_facility_id"], "request_id": request_id},
        )
        if result.rowcount != req["quantity"]:
            # Reserved units went missing between confirm-release and confirm-receipt
            # (shouldn't happen given the guards above, but never silently mark a
            # transfer "completed" if the unit count doesn't match — raising here
            # rolls back the facility_id reassignment too, since it's all one txn).
            raise HTTPException(
                status_code=409,
                detail=(
                    f"expected to transfer {req['quantity']} reserved units but found "
                    f"{result.rowcount}; transfer aborted, nothing changed"
                ),
            )
        completion = conn.execute(
            text(
                "UPDATE requests SET requester_confirmed_at = now(), status = 'completed' "
                "WHERE id = :id AND status = 'accepted' AND supplier_confirmed_at IS NOT NULL "
                "AND requester_confirmed_at IS NULL"
            ),
            {"id": request_id},
        )
        if completion.rowcount == 0:
            raise HTTPException(status_code=409, detail="receipt already confirmed or request status changed concurrently")

        requester = conn.execute(
            text("SELECT name FROM facilities WHERE id = :id"), {"id": req["requesting_facility_id"]}
        ).mappings().first()
        _create_notification(
            conn, req["supplying_facility_id"], "transfer_completed",
            f"{requester['name']} confirmed receipt — your {req['blood_type']} transfer is complete",
            f"requests:{request_id}",
        )

    return {"id": request_id, "status": "completed", "units_transferred": result.rowcount}


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
    with engine.begin() as conn:
        req = _require_request_participant(conn, request_id, facility_id)
        # Coordination chat is for coordinating an actual transfer — sending
        # (not reading: history stays visible either way) is gated to
        # accepted/completed so there's no window where one side can send a
        # message the other side has no way to see or respond to yet (the
        # requester's own view only surfaces a request's chat once it's
        # accepted).
        if req["status"] not in ("accepted", "completed"):
            raise HTTPException(
                status_code=400,
                detail=f"cannot send a message on a request with status {req['status']!r} — only accepted or completed requests support messaging",
            )
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


# Brute-force limits for exactly two endpoints — /auth/login and
# /auth/forgot-password — and nothing else (no global middleware, so /health and
# normal API traffic are never throttled). Keyed on client IP + email, not IP
# alone: behind Vercel/Render the IP can be a shared edge address, and an
# IP-only key would let one person's typos lock out everyone.
#   login: only FAILED attempts count, 5 per 60s; a success clears the count.
#   forgot-password: every request counts (the response is identical either
#   way), 5 per 10 min.
_LOGIN_LIMITER = auth.AttemptLimiter(limit=5, window=60)
_FORGOT_LIMITER = auth.AttemptLimiter(limit=5, window=600)


def _attempt_key(request: Request, email: str) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    ip = forwarded.split(",")[0].strip() or (request.client.host if request.client else "unknown")
    return f"{ip}|{email.strip().lower()}"


def _raise_if_limited(limiter: "auth.AttemptLimiter", key: str) -> None:
    wait = limiter.retry_after(key)
    if wait:
        raise HTTPException(
            status_code=429,
            detail="Too many sign-in attempts. Please wait a moment and try again.",
            headers={"Retry-After": str(wait)},
        )


@app.post("/auth/login")
def login(body: LoginBody, request: Request):
    key = _attempt_key(request, body.email)
    _raise_if_limited(_LOGIN_LIMITER, key)
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
        _LOGIN_LIMITER.record(key)
        raise HTTPException(status_code=401, detail="invalid email or password")
    _LOGIN_LIMITER.clear(key)

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
    that simply forgot its password — distinct from admin-provisioned
    onboarding's forced first-login reset (POST /admin/facilities), which
    still uses the separate JWT-based reset_token + POST /auth/change-password
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
    key = _attempt_key(request, body.email)
    _raise_if_limited(_FORGOT_LIMITER, key)
    _FORGOT_LIMITER.record(key)

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


@app.post("/auth/change-password")
def change_password(body: ChangePasswordBody, reset_claims: dict = Depends(get_user_from_reset_token)):
    """The only thing a password-reset token can be used for. Succeeding here
    clears must_change_password and issues a real access token — the normal
    login response shape, so the frontend can proceed exactly as it would
    after an ordinary login (into the complete-profile flow if the facility's
    profile isn't done yet, or straight to the dashboard if it already is).
    In practice this is only ever reached by facility accounts created via
    POST /admin/facilities — admin accounts are seeded with a real password
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
def update_password(body: UpdatePasswordBody, user: dict = Depends(get_current_user)):
    """Self-service password change (Account menu) — distinct from
    POST /auth/change-password, which only ever handles the forced-reset
    flow off a narrowly-scoped reset_token. This one requires a normal
    access token (meaning must_change_password is already false — that
    token literally can't exist otherwise) plus proof of the current
    password, since the account is already logged in and choosing to
    change it, not being forced to."""
    user_id = int(user["sub"])
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT password_hash FROM users WHERE id = :id"), {"id": user_id}
        ).mappings().first()
        if row is None:
            raise HTTPException(status_code=404, detail="account not found")
        if not auth.verify_password(body.current_password, row["password_hash"]):
            raise HTTPException(status_code=401, detail="current password is incorrect")

        conn.execute(
            text("UPDATE users SET password_hash = :password_hash WHERE id = :id"),
            {"password_hash": auth.hash_password(body.new_password), "id": user_id},
        )
    return {"detail": "password updated"}


@app.get("/auth/me")
def get_me(user: dict = Depends(get_current_user)):
    """Re-checks the logged-in user's facility fresh from the DB — used on
    app load so a stale localStorage session (e.g. profile_completed flipped
    true in a different tab, or by an admin) doesn't gate someone into a
    profile-completion screen they've already finished, or the reverse."""
    with engine.connect() as conn:
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

def _create_staff_account(conn, facility_id: int, email: str) -> dict:
    """Shared temp-password issuance path — used by POST /admin/facilities
    (brand new facility + its first account) and
    POST /admin/facilities/{facility_id}/accounts (an account for a facility
    that already exists but has none). Same shape either way: a random temp
    password, must_change_password=true, role is always 'staff' — there is no
    way to pass a role here, which is what keeps admin creation confined to
    seed_admin_user.py.

    Email uniqueness is checked globally (WHERE email = :email, not scoped to
    a facility) because that's what the users table actually enforces
    everywhere else in this app — one email, one account, regardless of which
    facility it's tied to.

    The temporary password is returned once, here, and nowhere else — same
    out-of-band-relay caveat as everywhere this pattern is used: there's no
    email/SMS provider wired up to deliver it automatically, so whoever calls
    this is responsible for relaying it.
    """
    existing = conn.execute(
        text("SELECT id FROM users WHERE email = :email"), {"email": email}
    ).mappings().first()
    if existing is not None:
        raise HTTPException(status_code=409, detail="an account with this email already exists")

    temp_password = auth.generate_temp_password()
    user = conn.execute(
        text(
            """
            INSERT INTO users (email, password_hash, facility_id, role, must_change_password)
            VALUES (:email, :password_hash, :facility_id, 'staff', true)
            RETURNING id, email, facility_id, role, created_at
            """
        ),
        {
            "email": email,
            "password_hash": auth.hash_password(temp_password),
            "facility_id": facility_id,
        },
    ).mappings().first()
    return {"user": dict(user), "temporary_password": temp_password}


class CreateFacilityAccountBody(BaseModel):
    name: str
    facility_type: str
    email: EmailStr

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("name must not be blank")
        return v.strip()

    @field_validator("facility_type")
    @classmethod
    def _facility_type_valid(cls, v: str) -> str:
        if v not in VALID_FACILITY_TYPES:
            raise ValueError(f"facility_type must be one of {sorted(VALID_FACILITY_TYPES)}")
        return v


class UpdateFacilityStatusBody(BaseModel):
    is_active: bool


@app.get("/admin/facilities", dependencies=[Depends(require_admin_role)])
def admin_list_facilities():
    """Every facility plus its status and linked login account(s) — almost
    always exactly one account per facility (POST /admin/facilities below
    only ever creates one), but this doesn't assume that; a facility with no
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


@app.post("/admin/facilities", dependencies=[Depends(require_admin_role)])
def admin_create_facility_account(body: CreateFacilityAccountBody):
    """Replaces public self-service registration (Step 9A). Creates a minimal
    facility row — name/type only, no location/department/DOH license yet —
    plus a single staff login for it, with a random temporary password and
    must_change_password=true. The facility fills in its own address, map
    location, department, and DOH license via POST /facilities/profile the
    first time it logs in with a real password (see /auth/change-password
    and the frontend's complete-profile flow).

    Always creates role='staff' — there is no way to pass role here, which is
    what keeps admin creation confined to seed_admin_user.py.

    The temporary password is returned once, here, and nowhere else — there's
    no email/SMS provider wired up in this app to deliver it automatically
    (same limitation as the simulated donor SMS blast), so whoever calls this
    endpoint is responsible for relaying it to the facility out of band.
    """
    with engine.begin() as conn:
        # Checked here too, before creating the facility row, purely so a
        # duplicate-email request fails fast rather than after allocating a
        # facility + thresholds it's about to roll back anyway — engine.begin()
        # rolls back the whole transaction on the HTTPException either helper
        # call would raise, so this pre-check is a nicety, not a correctness
        # requirement.
        existing = conn.execute(
            text("SELECT id FROM users WHERE email = :email"), {"email": body.email}
        ).mappings().first()
        if existing is not None:
            raise HTTPException(status_code=409, detail="an account with this email already exists")

        facility = conn.execute(
            text(
                """
                INSERT INTO facilities (name, facility_type)
                VALUES (:name, :facility_type)
                RETURNING id, name, facility_type, profile_completed, is_active
                """
            ),
            {"name": body.name, "facility_type": body.facility_type},
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

        account = _create_staff_account(conn, facility["id"], body.email)

    return {
        "facility": dict(facility),
        "user": account["user"],
        "temporary_password": account["temporary_password"],
    }


class CreateAccountForFacilityBody(BaseModel):
    email: EmailStr


@app.post("/admin/facilities/{facility_id}/accounts", dependencies=[Depends(require_admin_role)])
def admin_create_account_for_facility(facility_id: int, body: CreateAccountForFacilityBody):
    """Provisions a login for a facility that already exists but has none —
    the gap POST /admin/facilities above can't close, since that endpoint
    always creates a brand new facility alongside its account. A facility
    onboarded some other way (an early seed script, a migration, admin-side
    data entry before this endpoint existed) had no way to ever get a login
    until now.

    One account per facility, deliberately, not multiple-accounts-per-facility:
    every facility-scoped permission in this app (get_acting_facility_id)
    already treats "logged in as X" as synonymous with "acting as facility
    X", and there's no per-user audit trail beyond upload_history.uploaded_by
    — a second login for the same facility would just be a second person
    with identical access and no way to tell their actions apart after the
    fact. If that ever becomes a real need, it wants real per-user
    permissions, not a bigger version of this endpoint. Refuses with 409 if
    the facility already has one, rather than silently allowing a second.
    """
    with engine.begin() as conn:
        facility = conn.execute(
            text("SELECT id, name, facility_type FROM facilities WHERE id = :id"),
            {"id": facility_id},
        ).mappings().first()
        if facility is None:
            raise HTTPException(status_code=404, detail="facility not found")

        existing_account = conn.execute(
            text("SELECT id FROM users WHERE facility_id = :facility_id"),
            {"facility_id": facility_id},
        ).mappings().first()
        if existing_account is not None:
            raise HTTPException(status_code=409, detail="this facility already has an account")

        account = _create_staff_account(conn, facility_id, body.email)

    return {
        "facility": dict(facility),
        "user": account["user"],
        "temporary_password": account["temporary_password"],
    }


@app.post("/admin/accounts/{user_id}/reset-password", dependencies=[Depends(require_admin_role)])
def admin_reset_account_password(user_id: int):
    """Admin backup access when a facility forgets its password. Password
    hashes are one-way (bcrypt) — there is no existing password to reveal —
    so this issues a brand new temporary password and puts the account back
    into the forced-reset flow, the same shape as POST /admin/facilities.
    Shown once, here, same out-of-band-relay caveat as account creation."""
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
    return dict(row)


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
