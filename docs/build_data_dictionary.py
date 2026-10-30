"""Generates docs/DATA_DICTIONARY.md by introspecting the LIVE database
(information_schema + pg_constraint) and sampling real rows for examples.
Descriptions are hand-written below; the script asserts every deployed table and
column has one, so a schema change fails loudly instead of being silently omitted.
The database is only ever read, inside a READ ONLY transaction.
Run from repo root: server\\.venv\\Scripts\\python.exe docs\\build_data_dictionary.py
"""
import datetime
import re
import sys
from pathlib import Path

sys.path.insert(0, "server")
from database import engine
from sqlalchemy import text

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("docs/DATA_DICTIONARY.md")  # an argument writes elsewhere (dry runs)

# Every value server/main.py passes to _create_notification. Checked against the code in main(), so a new
# notification type fails the run until it is listed here.
NOTIFICATION_TYPES = [
    "blast_complete", "expiring_unit_alert", "forecast_shortage", "incoming_request", "request_accepted",
    "request_cancelled", "request_declined", "transfer_completed", "transfer_confirmation_needed",
    "transfer_units_failed", "unit_expiry",
]

TABLE_DOC = {
    "facilities": "Every organisation on the platform (hospitals and blood banks). It is the tenant root: nearly every other table carries a facility_id pointing here. Operational.",
    "users": "Login accounts. Each staff account belongs to one facility; the single admin account belongs to none. Operational.",
    "facility_registration_requests": "A facility's self-service application to join the platform: organisation details, a contact person and the password they chose (hashed), held until an administrator approves or rejects it. Status runs submitted, then email_verified once the contact confirms a one-time code (see otp_codes), then approved or rejected. Approval creates the facility, its default stock thresholds and its first staff account; either decision archives the request. Contains personal data. Operational (identity).",
    "otp_codes": "One-time codes emailed to a person: either to prove a registrant owns the email on a facility_registration_requests row, or to reset a password. Only a hash of each code is stored, with a count of wrong guesses and an expiry; each row is bound to exactly one registration request or one user. Operational (security).",
    "password_reset_requests": "One row per emailed password-reset link, storing only a hash of the token so a database leak cannot be used to reset accounts. Operational (security).",
    "blood_units": "The blood inventory: one row per physical unit (bag/component) identified by its DIN. The core operational table. Operational.",
    "blood_type_thresholds": "Per-facility minimum and maximum stock levels for each blood type; they drive low-stock and overstock status and restock recommendations. Operational configuration.",
    "requests": "Blood transfer requests between two facilities (one requests, one supplies) and their lifecycle from pending to completed. Operational.",
    "request_messages": "The chat thread attached to a transfer request, letting the two facilities coordinate. Operational.",
    "request_units": "The per-unit ledger of a transfer: one row per unit the supplier reserved at release, copied as it stood then (DIN, type, component, volume, dates, shelf), then settled as received or failed when the requester confirms receipt. A snapshot, so a later CSV re-upload cannot rewrite what was released. Operational (audit).",
    "request_unit_failures": "One row per reserved unit that could not be received because it expired between the supplier's release and the requester's receipt. Records which DIN failed and why, so a short delivery is never unexplained. Operational (audit).",
    "notifications": "In-app notifications shown to a facility (incoming requests, expiring units, forecast shortages, and so on). Operational.",
    "upload_history": "An audit record of each CSV upload (inventory, donors or historical stock), including the raw file and any per-row errors, so an upload can be undone. Operational (audit).",
    "donors": "The donor contact list each facility keeps for outreach. Contains personal data. Operational.",
    "blasts": "Donor outreach campaigns: one row per call for donors of a given blood type, with a target and a deadline. Operational (donor outreach, part 1 of 3).",
    "blast_messages": "One row per message a blast addressed to one donor. Messages are simulated: there is no real SMS provider integration. Operational (donor outreach, part 2 of 3).",
    "blast_replies": "A donor's reply (yes/no) to a blast; at most one reply per donor per blast. Operational (donor outreach, part 3 of 3).",
    "inventory_snapshots": "The daily count of usable units (not past their expiry date) per facility and blood type, one row per day per type. Written when a blood bank loads its forecast, nightly for every active facility including hospitals (POST /internal/daily-snapshot at 23:30 Manila, first run 2026-10-27), and by historical-stock CSV uploads. All eight blood types are written each time and a type with none in stock is an explicit 0, so a missing day means nobody recorded it, not that stock was zero. It is the time series the per-facility forecast is fitted on, and the forecast carries the last value forward over a day with no rows. Operational data that also serves as forecasting input.",
    "facility_forecast_cache": "Cached per-facility SARIMAX forecast checkpoints, refit at most once a day per facility and blood type, so the dashboard does not refit on every load. Derived cache; forecasting.",
    "inventory_snapshot_write_log": "Append-only log of every value a historical-stock upload wrote into inventory_snapshots, plus the original organic value the first time an upload overwrote one. It exists so undoing an upload can restore what was overwritten and handle overlapping uploads correctly. Operational (audit).",
    "facility_sarimax_order": "The SARIMAX order chosen offline for each facility and blood type by a grid search on training data only (the series minus its last 30 days), with diagnostics reported separately on a 30-day hold-out the search never saw. A facility and type with no row here is fitted with the AI-recommended order or the fixed default instead (see facility_forecast_cache.model_order). Derived; forecasting.",
    "forecast_alert_state": "Remembers whether a facility and blood type is currently in a forecast-shortage alert, so a notification is raised once when it starts rather than on every refresh. Derived state; forecasting.",
    "synthetic_inventory_snapshots": "A generated two-year daily stock series per blood type (no facility). It is demonstration data used to fit the shared stand-in model; it is not real blood bank records. Research.",
    "synthetic_forecast_cache": "The stand-in forecast produced from the synthetic series, served to facilities that have fewer than 3 days of their own history. Research.",
}
D = {
    "facilities": {
        "id": "Unique identifier of the facility.",
        "name": "The facility's display name shown to other facilities.",
        "facility_type": "Whether it is a 'hospital' (requests blood) or a 'bloodbank' (holds and supplies blood). Free text; no database CHECK enforces the two values.",
        "address": "Street address as entered or picked on the map.",
        "latitude": "Map latitude of the facility, used for distance between facilities.",
        "longitude": "Map longitude of the facility.",
        "department": "The department or unit the profile was completed for (onboarding profile field).",
        "doh_license_number": "The facility's Department of Health licence number (onboarding profile field); not validated.",
        "profile_completed": "Whether the facility has finished its onboarding profile. False forces the onboarding step.",
        "is_active": "Whether the facility is active on the platform; inactive facilities are hidden from listings.",
    },
    "users": {
        "id": "Unique identifier of the account.",
        "email": "Login email; unique across all accounts.",
        "password_hash": "bcrypt hash of the password. The password itself is never stored.",
        "facility_id": "The facility this account works for. NULL for the platform admin, who belongs to no facility.",
        "role": "Account role: 'staff' (facility user) or 'admin'. Free text; no database CHECK.",
        "created_at": "When the account was created.",
        "must_change_password": "True when the account was issued with a temporary password and must set its own on first login.",
    },
    "facility_registration_requests": {
        "id": "Unique identifier of the registration request.",
        "facility_name": "The applicant facility's name; becomes facilities.name on approval.",
        "facility_type": "'hospital' or 'bloodbank'. Enforced by a CHECK constraint.",
        "address": "Street address entered or picked on the map.",
        "latitude": "Map latitude of the facility; may be missing if no location was picked.",
        "longitude": "Map longitude of the facility.",
        "doh_license_number": "The Department of Health licence number given on the form; not validated.",
        "contact_person": "Name of the person applying. Personal data.",
        "email": "The applicant's email address, which the one-time code is sent to and which becomes the login on approval. Personal data. Unique among live (not archived) requests.",
        "phone": "The applicant's phone number. Personal data.",
        "password_hash": "bcrypt hash of the password chosen at registration, copied to users.password_hash on approval. The password itself is never stored.",
        "status": "submitted, email_verified, approved or rejected. Enforced by a CHECK constraint.",
        "rejection_reason": "The administrator's stated reason when the request was rejected; NULL otherwise. Free text.",
        "reviewed_by": "The administrator account that approved or rejected the request.",
        "reviewed_at": "When the decision was made; NULL while undecided.",
        "archived_at": "When the request was archived (set on approval or rejection, so a later application with the same email starts a fresh row); NULL while live.",
        "created_at": "When the request was first submitted.",
    },
    "otp_codes": {
        "id": "Unique identifier of the code.",
        "purpose": "What the code is for: 'registration_email_verify' or 'password_reset'. Enforced by a CHECK constraint.",
        "registration_request_id": "The registration request being verified. Set only for registration_email_verify codes; a CHECK ties it to the purpose.",
        "user_id": "The account whose password the code resets. Set only for password_reset codes; a CHECK ties it to the purpose.",
        "email": "The address the code was emailed to. Personal data.",
        "code_hash": "Hash of the code; the code itself is never stored.",
        "attempts": "How many wrong guesses have been made against this code; the code stops being accepted at the cap.",
        "expires_at": "When the code stops working.",
        "used_at": "When the code was accepted or superseded by a newly issued one; NULL while still usable.",
        "created_at": "When the code was issued.",
    },
    "password_reset_requests": {
        "id": "Unique identifier of the reset request.",
        "user_id": "The account the reset link was issued for.",
        "token_hash": "Hash of the one-time token that was emailed; the token itself is not stored. Unique.",
        "expires_at": "When the link stops working.",
        "used_at": "When the link was used to set a new password; NULL while unused.",
        "created_at": "When the reset was requested.",
    },
    "blood_units": {
        "id": "Unique identifier of the inventory row.",
        "din": "Donation Identification Number: the unit's label/barcode, unique across the whole platform.",
        "blood_type": "ABO/Rh group of the unit (A+, A-, B+, B-, AB+, AB-, O+, O-).",
        "component": "What the unit is: Packed RBC, Whole Blood, Platelets or Fresh Frozen Plasma.",
        "location": "Where in the facility the unit is stored (a fridge, bay or freezer).",
        "volume_ml": "Volume of the unit in millilitres.",
        "collected_date": "The date the blood was drawn.",
        "expires_date": "The last date the unit is usable; drives near-expiry and expired status.",
        "created_at": "When the row was added to the system.",
        "facility_id": "The facility this unit belongs to.",
        "reserved_for_request_id": "The transfer request this unit is held for; NULL when the unit is free.",
        "last_notified_expiry_status": "The last expiry warning level ('near-expiry' or 'critical') already notified, so the same warning is not sent twice; NULL when none sent.",
        "upload_history_id": "The CSV upload that created this unit, so the upload can be undone; NULL for units not added by upload.",
        "archived_at": "When the unit was archived out of active inventory; NULL while active.",
    },
    "blood_type_thresholds": {
        "blood_type": "The blood type the levels apply to (part of the composite primary key).",
        "minimum_units": "The stock level below which the facility counts as low or breached for this type.",
        "maximum_units": "The stock level above which the facility counts as overstocked for this type.",
        "facility_id": "The facility these levels belong to (part of the composite primary key).",
    },
    "requests": {
        "id": "Unique identifier of the request.",
        "requesting_facility_id": "The facility asking for blood.",
        "supplying_facility_id": "The facility being asked to supply it.",
        "blood_type": "The blood type requested.",
        "quantity": "Number of units requested.",
        "emergency_type": "Why it is needed: 'trauma' (urgent) or 'restock' (routine). Free text; no database CHECK.",
        "status": "Lifecycle state: pending, accepted, declined, cancelled, completed, or failed (every reserved unit expired before receipt; see request_unit_failures). Completed can still carry failed units. Free text; no database CHECK.",
        "created_at": "When the request was made.",
        "supplier_confirmed_at": "When the supplier confirmed the hand-over; NULL until then.",
        "requester_confirmed_at": "When the requester confirmed receipt; NULL until then. Both confirmations complete the transfer.",
        "component": "The blood component requested ('Packed RBC' or 'Platelets'); release reserves only units of exactly this component. NULL on requests made before components were recorded, meaning not recorded, and such a request cannot be released. Free text; no database CHECK.",
    },
    "request_messages": {
        "id": "Unique identifier of the message.",
        "request_id": "The request whose chat thread this message belongs to.",
        "sender_facility_id": "The facility that wrote the message.",
        "message": "The message text.",
        "created_at": "When it was sent.",
    },
    "request_units": {
        "id": "Unique identifier of the ledger row.",
        "request_id": "The transfer request the unit was reserved for.",
        "blood_unit_id": "The unit, while it still exists. NULL once the unit has been deleted from inventory (ON DELETE SET NULL); the DIN below keeps the record.",
        "din": "The unit's donation identification number at release.",
        "blood_type": "The unit's blood type at release (a later CSV re-upload may change the live unit).",
        "component": "The unit's component at release.",
        "volume_ml": "The unit's volume in millilitres at release.",
        "collected_date": "The unit's collection date at release.",
        "expires_date": "The unit's expiry date at release (a snapshot; never updated).",
        "origin_location": "The supplier's shelf or storage location for the unit at release.",
        "days_remaining_at_release": "Whole days between the release day (Manila business date) and expires_date.",
        "outcome": "'reserved' from release until receipt, then 'received' or 'failed' (database CHECK).",
        "reason": "Why the unit failed; set exactly when outcome is 'failed'. Only 'expired_before_receipt' exists (database CHECK).",
        "days_remaining_at_receipt": "Whole days between the receipt day and the live unit's expiry at receipt, the value the received/failed decision was made on; NULL until receipt. Zero or negative for a unit that expired in transit. It differs from expires_date minus the receipt day only if the unit's expiry was changed after release.",
        "created_at": "When the supplier confirmed release and the row was written.",
    },
    "request_unit_failures": {
        "id": "Unique identifier of the failure record.",
        "request_id": "The transfer request the unit was reserved for.",
        "blood_unit_id": "The unit that was not received. It stays at the supplier, archived.",
        "din": "The unit's donation identification number at the moment receipt was refused.",
        "expires_date": "The unit's expiry date at the moment receipt was refused; before that day's business date.",
        "reason": "Why the unit failed. Only 'expired_before_receipt' exists (database CHECK).",
        "created_at": "When receipt was confirmed and the failure recorded.",
    },
    "notifications": {
        "id": "Unique identifier of the notification.",
        "facility_id": "The facility the notification is shown to.",
        "type": f"Category, one of {len(NOTIFICATION_TYPES)}: {', '.join(NOTIFICATION_TYPES)}.",
        "message": "The text shown to the user.",
        "link": "The in-app page or record the notification opens (for example 'requests:14' or 'inventory').",
        "read_at": "When the user read it; NULL while unread (currently NULL in every row).",
        "created_at": "When the notification was raised.",
    },
    "upload_history": {
        "id": "Unique identifier of the upload.",
        "facility_id": "The facility that uploaded the file.",
        "upload_type": "Kind of upload: 'inventory', 'donors' or 'historical_stock'. Enforced by a CHECK constraint.",
        "uploaded_by": "The user who uploaded it.",
        "filename": "Original file name.",
        "uploaded_at": "When the upload happened.",
        "rows_processed": "How many CSV rows were accepted.",
        "rows_failed": "How many CSV rows were rejected.",
        "error_details": "JSON list of per-row rejection reasons; an empty list when every row was accepted.",
        "raw_content": "The uploaded CSV text, kept so an upload can be audited or undone. Shown here as its header line only.",
        "undone_at": "When the upload was reversed; NULL if it stands.",
    },
    "donors": {
        "id": "Unique identifier of the donor record.",
        "name": "The donor's name. Personal data.",
        "blood_type": "The donor's blood type, used to pick who to contact for a blast.",
        "phone": "The donor's contact number. Personal data; unique within a facility.",
        "facility_id": "The facility that keeps this donor on its list.",
        "created_at": "When the donor was added.",
        "upload_history_id": "The CSV upload that added this donor; NULL when added by hand.",
    },
    "blasts": {
        "id": "Unique identifier of the outreach blast.",
        "facility_id": "The facility that launched the blast.",
        "blood_type": "The blood type of donors being called.",
        "target_count": "How many donors the facility wants to respond.",
        "time_limit_hours": "How many hours donors have to respond.",
        "status": "'active' while open, 'completed' once closed.",
        "created_at": "When the blast was launched.",
        "deadline_at": "When the response window closes.",
    },
    "blast_messages": {
        "id": "Unique identifier of the message record.",
        "blast_id": "The blast this message was part of.",
        "donor_id": "The donor it was addressed to.",
        "message_text": "The alert text composed for the donor.",
        "simulated_sent_at": "When the message was 'sent'. Sending is simulated end to end; nothing leaves the system.",
    },
    "blast_replies": {
        "id": "Unique identifier of the reply.",
        "blast_id": "The blast being answered.",
        "donor_id": "The donor who replied.",
        "reply": "The donor's answer, 'yes' or 'no'.",
        "replied_at": "When the reply was recorded.",
    },
    "inventory_snapshots": {
        "id": "Unique identifier of the snapshot row.",
        "snapshot_date": "The day the count describes.",
        "blood_type": "Blood type counted.",
        "units": "Usable units of that type in stock that day (not past expiry). An explicit 0 means the shelf was empty; no row for the day means it was not observed.",
        "created_at": "When the row was first written.",
        "facility_id": "The facility counted. Hospitals are counted too, though they are never forecast from.",
        "upload_history_id": "The historical-stock upload that supplied this row; NULL for rows the app wrote itself (a forecast load or the nightly job).",
    },
    "inventory_snapshot_write_log": {
        "id": "Unique identifier of the log entry; higher means written later.",
        "facility_id": "The facility whose snapshot was written. Log entries are deleted with the facility.",
        "snapshot_date": "The day of the snapshot that was written.",
        "blood_type": "Blood type of the snapshot that was written.",
        "units": "The unit count that was written for that day and type.",
        "upload_history_id": "The upload that wrote this value; NULL marks the original organic value saved before an upload overwrote it. Log entries are deleted with the upload.",
        "created_at": "When the entry was logged.",
    },
    "facility_forecast_cache": {
        "id": "Unique identifier of the cached point.",
        "facility_id": "The facility the forecast is for.",
        "blood_type": "Blood type forecast.",
        "forecast_date": "The future date this forecast point is for (checkpoints at 0, 5, 10, 15, 20, 25 and 30 days ahead).",
        "forecast_units": "Predicted stock on that date.",
        "lower_units": "Lower end of the 95% prediction interval.",
        "upper_units": "Upper end of the 95% prediction interval.",
        "trained_through_date": "The last day of history the model was fitted on; the cache is reused only while this equals today.",
        "model_order": "Label of the model that produced it: the SARIMAX order plus the dengue regressor, then in brackets how the order was obtained: [grid-searched] (a facility_sarimax_order row), [ai-recommended] (the decision-tree selector) or [default-order] (the fixed default). The bracket names the mechanism only. Rows written before the brackets existed have no suffix (a grid-searched order) or ' [fallback]' (the default order); the API reports those in today's spelling.",
        "generated_at": "When it was computed.",
    },
    "facility_sarimax_order": {
        "facility_id": "The facility the order was chosen for (part of the composite primary key). Rows are deleted with the facility.",
        "blood_type": "Blood type the order was chosen for (part of the composite primary key).",
        "p": "Non-seasonal autoregressive order of the chosen model.",
        "d": "Non-seasonal differencing order of the chosen model.",
        "q": "Non-seasonal moving-average order of the chosen model.",
        "seasonal_p": "Seasonal autoregressive order.",
        "seasonal_d": "Seasonal differencing order; the grid fixes it at 0.",
        "seasonal_q": "Seasonal moving-average order.",
        "seasonal_s": "Seasonal period in days; 7 (weekly).",
        "aic": "AIC of the chosen model fitted on the training window.",
        "lb_p_14_in_sample": "Ljung-Box p-value at lag 14 on the chosen model's in-sample residuals, corrected for the model's ARMA parameters.",
        "lb_p_21_in_sample": "Same at lag 21.",
        "lb_p_28_in_sample": "Same at lag 28.",
        "criterion_satisfied": "True when the chosen model passed Ljung-Box (p above 0.05) at all three lags; False means no candidate did and the lowest-AIC candidate was kept.",
        "n_candidates_converged": "How many of the grid's candidate models converged and passed the sanity check on the training window.",
        "lb_p_14_holdout_onestep": "Ljung-Box p-value at lag 14 on one-step-ahead errors over the hold-out, with parameters not re-estimated; NULL if not computable.",
        "lb_p_21_holdout_onestep": "Same at lag 21.",
        "lb_p_28_holdout_onestep": "Same at lag 28.",
        "holdout_mape": "Mean absolute percentage error, in percent, of the chosen model's 30-day forecast against the hold-out actuals; NULL if undefined.",
        "holdout_rmse": "Root mean squared error, in units, of that forecast.",
        "holdout_baseline_mape": "The same percentage error for a naive baseline that repeats the last training value.",
        "holdout_baseline_rmse": "The same root mean squared error, in units, for that naive baseline.",
        "trained_through_date": "The last day of the training window: the selection day minus the 30-day hold-out.",
        "evaluated_through_date": "The last day of the hold-out; the day the selection ran.",
        "selected_at": "When the row was written (UTC).",
        "start_params": "The chosen model's fitted parameters on the training window, used to warm-start the fit on the full series; NULL on rows written before this column existed.",
        "selection_aic_per_obs": "The training-window AIC divided by the number of observations; the baseline the live fit's soundness check compares against. NULL on rows written before this column existed.",
    },
    "forecast_alert_state": {
        "facility_id": "The facility (part of the composite primary key).",
        "blood_type": "Blood type (part of the composite primary key).",
        "alerting": "True while this facility and type is in a forecast-shortage alert.",
        "updated_at": "When the flag last changed.",
    },
    "synthetic_inventory_snapshots": {
        "id": "Unique identifier of the generated row.",
        "snapshot_date": "The (generated) day.",
        "blood_type": "Blood type.",
        "units": "Generated stock level for that day.",
        "created_at": "When the row was generated.",
    },
    "synthetic_forecast_cache": {
        "id": "Unique identifier of the row.",
        "blood_type": "Blood type forecast.",
        "forecast_date": "The future date the value is for.",
        "forecast_units": "Predicted stock for that date, to two decimals; model output on generated data.",
        "model_order": "Label of the model that produced it.",
        "generated_at": "When it was generated.",
    },
}
# examples that must not be shown verbatim
MASK = {("users", "password_hash"): "$2b$12$...", ("password_reset_requests", "token_hash"): "a3f9c2e1-...",
        ("donors", "phone"): "+1 5*****01",
        ("facility_registration_requests", "password_hash"): "$2b$12$...",
        ("facility_registration_requests", "contact_person"): "(personal data; not shown)",
        ("facility_registration_requests", "email"): "(personal data; not shown)",
        ("facility_registration_requests", "phone"): "+1 5*****01",
        ("facility_registration_requests", "rejection_reason"): "(free text; not shown)",
        ("otp_codes", "code_hash"): "$2b$12$...", ("otp_codes", "email"): "(personal data; not shown)"}
# preferred example row: donors 7 is a literal placeholder record ("Donor A"); users 7 is a demo account
ROW_ID = {"donors": 7, "users": 7, "upload_history": 22}
# preferred example row where the newest reads best: the oldest cache rows carry the pre-bracket label spelling
ROW_ORDER = {"facility_forecast_cache": "id desc"}


def length(c):
    dt = c["data_type"]
    if c["cl"]:
        return str(c["cl"])
    if dt == "bigint":
        return "64-bit"
    if dt == "integer":
        return "32-bit"
    if dt == "double precision":
        return "53-bit"
    if dt == "numeric":
        return f"{c['np']},{c['ns']}" if c["np"] else "—"
    return "—"


def show(v):
    if v is None:
        return "NULL"
    s = str(v).replace("\n", "\\n").replace("|", "\\|")
    return (s[:60] + "…") if len(s) > 60 else s


def main():
    # the notification types the code can raise must be exactly the ones documented
    raised = re.findall(r'_create_notification\(\s*conn,\s*[^,]+,\s*"(\w+)"', Path("server/main.py").read_text(encoding="utf-8"))
    assert set(raised) == set(NOTIFICATION_TYPES), f"notification types in server/main.py differ from NOTIFICATION_TYPES: {sorted(set(raised) ^ set(NOTIFICATION_TYPES))}"
    secrets = set()  # real values of masked columns seen while sampling; none may reach the output
    with engine.connect() as c:
        c.execute(text("SET TRANSACTION READ ONLY"))  # first statement of the transaction: this run cannot write
        tabs =[r[0] for r in c.execute(text("select table_name from information_schema.tables where table_schema='public' and table_type='BASE TABLE' order by 1"))]
        views = [r[0] for r in c.execute(text("select table_name from information_schema.views where table_schema='public'"))]
        cons = c.execute(text("select conrelid::regclass::text t, contype, conname, pg_get_constraintdef(oid) d, conkey, confrelid::regclass::text ft, confkey, confdeltype from pg_constraint where connamespace='public'::regnamespace")).mappings().all()
        attn = {(r[0], r[1]): r[2] for r in c.execute(text("select attrelid::regclass::text, attnum, attname from pg_attribute where attnum>0 and not attisdropped and attrelid in (select oid from pg_class where relnamespace='public'::regnamespace)"))}
        missing = [t for t in tabs if t not in TABLE_DOC]
        assert not missing, f"undocumented tables: {missing}"
        pk, uk, fk, fkrows, cascade_fks, setnull_fks = {}, {}, {}, [], [], []
        for k in cons:
            cols = [attn[(k["t"], n)] for n in k["conkey"]]
            if k["contype"] == "p":
                for col in cols:
                    pk[(k["t"], col)] = True
            elif k["contype"] == "u":
                for col in cols:
                    uk.setdefault((k["t"], col), []).append(cols)
            elif k["contype"] == "f":
                pcol = attn[(k["ft"], k["confkey"][0])]
                fk[(k["t"], cols[0])] = (k["ft"], pcol)
                fkrows.append((k["t"], cols[0], k["ft"], pcol))
                if k["confdeltype"] == "c":
                    cascade_fks.append(f"{k['t']}.{cols[0]}")
                if k["confdeltype"] == "n":
                    setnull_fks.append(f"{k['t']}.{cols[0]}")
        md, totals, nullable = [], {}, {}
        for t in tabs:
            cols = [dict(r) for r in c.execute(text("select column_name,data_type,character_maximum_length cl,numeric_precision np,numeric_scale ns,is_nullable from information_schema.columns where table_schema='public' and table_name=:t order by ordinal_position"), {"t": t}).mappings()]
            n = c.execute(text(f'select count(*) from "{t}"')).scalar()
            totals[t] = (n, len(cols))
            extra = f" where id={ROW_ID[t]}" if t in ROW_ID else ""
            row = c.execute(text(f'select * from "{t}"{extra} order by {ROW_ORDER.get(t, "1")} limit 1')).mappings().first()
            if row is None and extra:  # the preferred example row no longer exists: use the first real row, never an all-NULL one
                row = c.execute(text(f'select * from "{t}" order by {ROW_ORDER.get(t, "1")} limit 1')).mappings().first()
            row = row or {}
            secrets |= {str(row[name]) for (mt, name) in MASK if mt == t and row.get(name) is not None and len(str(row[name])) >= 4}
            md.append(f"## {t}\n\n{TABLE_DOC[t]} ({n:,} rows, {len(cols)} columns in the live database.)\n")
            md.append("| Field_Name | Data_Type | Length | Key | Description | Example |\n|---|---|---|---|---|---|")
            for col in cols:
                name = col["column_name"]
                nullable[(t, name)] = col["is_nullable"] == "YES"
                assert name in D[t], f"undocumented column {t}.{name}"
                keys = []
                if (t, name) in pk:
                    keys.append("PK")
                if (t, name) in fk:
                    keys.append("FK")
                if (t, name) in uk:
                    keys.append("UK" + ("*" if any(len(g) > 1 for g in uk[(t, name)]) else ""))
                desc = D[t][name]
                if (t, name) in fk:
                    desc = desc.rstrip(".") + f" (FK to {fk[(t, name)][0]}.{fk[(t, name)][1]})."
                if col["is_nullable"] == "YES":
                    desc += " Nullable."
                if n == 0:
                    ex = "(table is empty)"
                elif (t, name) in MASK:
                    ex = MASK[(t, name)]
                elif t == "upload_history" and name == "raw_content":
                    ex = show((row.get(name) or "").split("\n")[0])
                else:
                    v = row.get(name)
                    if v is None and col["is_nullable"] == "YES":
                        v = c.execute(text(f'select "{name}" from "{t}" where "{name}" is not null order by 1 limit 1')).scalar()
                        ex = show(v) if v is not None else "NULL (no row has a value)"
                    else:
                        ex = show(v)
                md.append(f"| {name} | {col['data_type']} | {length(col)} | {' + '.join(keys)} | {desc} | {ex} |")
            notes = []
            pks = [col for (tt, col) in pk if tt == t]
            if len(pks) > 1:
                notes.append(f"Composite primary key ({', '.join(pks)}).")
            for g in sorted({tuple(g) for (tt, _), gs in uk.items() if tt == t for g in gs if len(g) > 1}):
                notes.append(f"Composite unique key ({', '.join(g)}); `UK*` marks a column that is part of one.")
            for k in cons:
                if k["t"] == t and k["contype"] == "c":
                    notes.append(f"CHECK: `{k['d']}`.")
            if notes:
                md.append("\n" + " ".join(notes))
            md.append("")
        uniq_single = {(t, g[0]) for (t, _), gs in uk.items() for g in gs if len(g) == 1}
        fkmd = ["| Child column | Parent column | Cardinality | Nullable |", "|---|---|---|---|"]
        for ct, cc, pt, pc in sorted(fkrows):
            card = "1:1" if (ct, cc) in uniq_single else ("many : 0..1" if nullable[(ct, cc)] else "many : 1")
            fkmd.append(f"| {ct}.{cc} | {pt}.{pc} | {card} | {'yes' if nullable[(ct, cc)] else 'no'} |")
        delrules = {k["confdeltype"] for k in cons if k["contype"] == "f"}
        assert delrules <= {"a", "c", "n"}, f"a delete rule other than NO ACTION / CASCADE / SET NULL exists: {delrules}; update the prose"
        check_tables = sorted({k["t"] for k in cons if k["contype"] == "c"})
        n_checks = sum(1 for k in cons if k["contype"] == "c")
        assert not {"users", "requests", "facilities"} & set(check_tables), "users/requests/facilities now have CHECKs; the free-text sentence below is wrong"
        # request_units.blood_unit_id is SET NULL on purpose: the ledger row outlives a unit deleted from inventory.
        setnull_names = ", ".join(f"`{x}`" for x in sorted(setnull_fks))
        setnull_note = f" {setnull_names} is `SET NULL`: the row outlives the parent, which is the point of a ledger." if setnull_fks else ""
        cascade_names = [f"`{x}`" for x in sorted(cascade_fks)]
        cascade_text = " and ".join([", ".join(cascade_names[:-1]), cascade_names[-1]]) if len(cascade_names) > 1 else cascade_names[0]
        # what inventory_snapshots holds, for the Research vs operational table
        snap_total = c.execute(text("select count(*) from inventory_snapshots")).scalar()
        snap_upload = c.execute(text("select count(*) from inventory_snapshots where upload_history_id is not null")).scalar()
    check_text = ", ".join(f"`{t}`" for t in check_tables)
    total = len(tabs)
    head = f"""# BloodLink Data Dictionary and Schema Reference

Generated {datetime.date.today()} by `docs/build_data_dictionary.py` from the **live PostgreSQL (Supabase) database** via
`information_schema` and `pg_constraint`, not from the `schema_*.sql` files. Data types, lengths, keys and constraints are what is
deployed; example values are real values from actual rows, with the exceptions described below.

## Summary

* **{total} tables** in the `public` schema, {sum(v[1] for v in totals.values())} columns in all, {len(fkrows)} foreign keys. Views: {', '.join(views) or 'none'}.
* **No unexpected tables.** Every table maps to a feature: identity (`facilities`, `users`, `password_reset_requests`, `facility_registration_requests`, `otp_codes`), inventory
  (`blood_units`, `blood_type_thresholds`, `upload_history`, `inventory_snapshot_write_log`), transfers (`requests`, `request_messages`, `request_units`, `request_unit_failures`), notification
  (`notifications`), donor outreach (`donors`, `blasts`, `blast_messages`, `blast_replies`) and forecasting (`inventory_snapshots`,
  `facility_forecast_cache`, `facility_sarimax_order`, `forecast_alert_state`, `synthetic_inventory_snapshots`, `synthetic_forecast_cache`). The ones easiest to
  overlook are `facility_sarimax_order` (the SARIMAX order chosen offline per facility and blood type), `forecast_alert_state` (alert de-duplication), `upload_history` (audit; holds the raw CSV text and drives undo), `inventory_snapshot_write_log` (lets undo restore overwritten snapshots) and the
  two `synthetic_*` research tables.
* **Donor outreach tables:** `blasts` (the campaign), `blast_messages` (one row per donor messaged) and `blast_replies` (donor yes/no
  answers). Full column lists below.
* **`facilities` carries more than name, type, address, coordinates and `is_active`.** The onboarding migration also added
  `department`, `doh_license_number` and `profile_completed`, so the table has 10 columns.
* **Referential integrity:** every foreign key is `NO ACTION` on delete (a parent row cannot be deleted while children exist), except {cascade_text}, which `CASCADE` because those rows have no meaning without their parent (a pure log and derived selection results).{setnull_note}
  {n_checks} CHECK constraints exist, on {check_text}; each is shown under its table. Enumerations such as `users.role`, `requests.status`
  and `facilities.facility_type` are free text validated in application code only.

## Conventions and privacy

* **Length:** character length or numeric precision where the type has one. `bigint` = 64-bit, `integer` = 32-bit,
  `double precision` = 53-bit mantissa. `text`, `date`, `boolean`, `timestamptz`, `jsonb` and unconstrained `numeric` have no defined
  length (—).
* **Key:** PK primary key, FK foreign key, UK unique. `UK*` = the column is part of a composite unique key (named under the table).
* **Masked examples:** `password_hash` is shown as `$2b$12$...`; `token_hash` as `a3f9c2e1-...`; donor phone numbers are masked
  (`+1 5*****01`). The stored numbers follow a `+1 555-...` pattern, which suggests placeholders rather than real people, but that was
  not verified, so they are masked regardless. The `donors` example row is a placeholder record ("Donor A"); other donor rows hold
  person-like names, which are not reproduced. One `users` row holds a personal email address and is not shown.
* Where the sampled row has NULL in a nullable column, the first non-NULL value from any other row is shown; if none exists the
  example says "NULL (no row has a value)". Examples within one table can therefore come from different rows.
* Timestamps are `timestamptz`, stored in UTC.
* The database contains test and demonstration rows (for example facility 43 "Testing" with placeholder profile fields). Examples are
  real values, not curated ones.

"""
    assert all(f"`{t}`" in head for t in tabs), f"tables missing from the Summary prose: {[t for t in tabs if f'`{t}`' not in head]}"
    non_operational = ["synthetic_inventory_snapshots", "synthetic_forecast_cache", "facility_forecast_cache", "facility_sarimax_order",
                       "forecast_alert_state", "inventory_snapshots"]
    operational_text = ", ".join(f"`{t}`" for t in tabs if t not in non_operational)
    tail = f"""
## Foreign key map

Every foreign key constraint in the deployed schema. `requests` joins `facilities` twice (requesting and supplying);
`blood_units.reserved_for_request_id` is nullable. All are `ON DELETE NO ACTION` except {cascade_text}, which are `ON DELETE CASCADE`.{setnull_note} Cardinality reads child : parent.

""" + "\n".join(fkmd) + f"""

## Research vs operational

Which tables exist to support the forecasting research rather than day-to-day blood bank operation.

| Table | Class | Why |
|---|---|---|
| synthetic_inventory_snapshots | **Research** | Generated two-year demonstration series (no facility). Not blood bank records. Used to fit the shared stand-in model. |
| synthetic_forecast_cache | **Research** | Output of the stand-in model. Served only to facilities with fewer than 3 days of history, so it can appear in the UI, but it is model output on generated data, not a measurement. |
| facility_forecast_cache | Derived (forecasting) | Cache of per-facility SARIMAX output. Reproducible from `inventory_snapshots`; safe to truncate. |
| facility_sarimax_order | Derived (forecasting) | The SARIMAX order chosen offline for each facility and blood type, with its selection diagnostics. Reproducible by re-running `server/select_orders.py` on `inventory_snapshots`; with no row the live fitter uses the AI-recommended or default order. |
| forecast_alert_state | Derived (forecasting) | De-duplication flag for forecast-shortage notifications. It has an operational effect (raises a notification) but exists only because of the forecast. |
| inventory_snapshots | Mixed | Operational counts, but also the series the forecast is fitted on. At the time of generation, {snap_upload:,} of its {snap_total:,} rows came from historical-stock CSV uploads and {snap_total - snap_upload:,} were written by the app itself (a forecast load, or the nightly job for every active facility). Uploaded files in this deployment have included generated demonstration data, so the table should not be described as a record of real supply. A day with no row is unobserved, not zero. |

Every other table ({operational_text}) is operational. In the
paper, `blood_units` is the system's record of physical inventory; the `synthetic_*` tables are a research and demonstration
apparatus and should not be presented as equivalent to it. The current database also holds demo and test operational rows
(demo facilities and accounts), so even the operational tables contain demonstration content in this deployment.
"""
    out_text = head + "\n".join(md) + tail
    leaked = [x for x in secrets if x in out_text]
    assert not leaked, f"{len(leaked)} masked value(s) reached the output; not written"
    for pattern, what in ((r"\$2[aby]\$\d\d\$[./A-Za-z0-9]{20,}", "a bcrypt hash"), (r"\+\d[\d \-]{8,}\d", "a phone number")):
        assert not re.search(pattern, out_text), f"the output contains {what}; not written"
    OUT.write_text(out_text, encoding="utf-8")
    print("wrote", OUT, total, "tables")


main()
