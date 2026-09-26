"""Generates docs/DATA_DICTIONARY.md by introspecting the LIVE database
(information_schema + pg_constraint) and sampling real rows for examples.
Descriptions are hand-written below; the script asserts every deployed table and
column has one, so a schema change fails loudly instead of being silently omitted.
Run from repo root: server\\.venv\\Scripts\\python.exe docs\\build_data_dictionary.py
"""
import datetime
import sys
from pathlib import Path

sys.path.insert(0, "server")
from database import engine
from sqlalchemy import text

OUT = Path("docs/DATA_DICTIONARY.md")

TABLE_DOC = {
    "facilities": "Every organisation on the platform (hospitals and blood banks). It is the tenant root: nearly every other table carries a facility_id pointing here. Operational.",
    "users": "Login accounts. Each staff account belongs to one facility; the single admin account belongs to none. Operational.",
    "password_reset_requests": "One row per emailed password-reset link, storing only a hash of the token so a database leak cannot be used to reset accounts. Operational (security).",
    "blood_units": "The blood inventory: one row per physical unit (bag/component) identified by its DIN. The core operational table. Operational.",
    "blood_type_thresholds": "Per-facility minimum and maximum stock levels for each blood type; they drive low-stock and overstock status and restock recommendations. Operational configuration.",
    "requests": "Blood transfer requests between two facilities (one requests, one supplies) and their lifecycle from pending to completed. Operational.",
    "request_messages": "The chat thread attached to a transfer request, letting the two facilities coordinate. Operational.",
    "notifications": "In-app notifications shown to a facility (incoming requests, expiring units, forecast shortages, and so on). Operational.",
    "upload_history": "An audit record of each CSV upload (inventory, donors or historical stock), including the raw file and any per-row errors, so an upload can be undone. Operational (audit).",
    "donors": "The donor contact list each facility keeps for outreach. Contains personal data. Operational.",
    "blasts": "Donor outreach campaigns: one row per call for donors of a given blood type, with a target and a deadline. Operational (donor outreach, part 1 of 3).",
    "blast_messages": "One row per message a blast addressed to one donor. Messages are simulated: there is no real SMS provider integration. Operational (donor outreach, part 2 of 3).",
    "blast_replies": "A donor's reply (yes/no) to a blast; at most one reply per donor per blast. Operational (donor outreach, part 3 of 3).",
    "inventory_snapshots": "The daily count of usable units per facility and blood type. Written by the app's own daily snapshot and by historical-stock CSV uploads; it is the time series the per-facility forecast is fitted on. Operational data that also serves as forecasting input.",
    "facility_forecast_cache": "Cached per-facility SARIMAX forecast checkpoints, refit at most once a day per facility and blood type, so the dashboard does not refit on every load. Derived cache; forecasting.",
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
        "status": "Lifecycle state: pending, accepted, declined, cancelled or completed. Free text; no database CHECK. Only pending, declined, cancelled and completed occur in the current data.",
        "created_at": "When the request was made.",
        "supplier_confirmed_at": "When the supplier confirmed the hand-over; NULL until then.",
        "requester_confirmed_at": "When the requester confirmed receipt; NULL until then. Both confirmations complete the transfer.",
    },
    "request_messages": {
        "id": "Unique identifier of the message.",
        "request_id": "The request whose chat thread this message belongs to.",
        "sender_facility_id": "The facility that wrote the message.",
        "message": "The message text.",
        "created_at": "When it was sent.",
    },
    "notifications": {
        "id": "Unique identifier of the notification.",
        "facility_id": "The facility the notification is shown to.",
        "type": "Category: unit_expiry, incoming_request, request_accepted, request_cancelled, transfer_confirmation_needed, transfer_completed or forecast_shortage.",
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
        "units": "Usable units of that type in stock that day.",
        "created_at": "When the row was written.",
        "facility_id": "The facility counted.",
        "upload_history_id": "The historical-stock upload that supplied this row; NULL for the app's own daily snapshot.",
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
        "model_order": "Label of the model that produced it (the fixed SARIMAX order plus the dengue regressor).",
        "generated_at": "When it was computed.",
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
        ("donors", "phone"): "+1 5*****01"}
# preferred example row: donors 7 is a literal placeholder record ("Donor A"); users 7 is a demo account
ROW_ID = {"donors": 7, "users": 7, "upload_history": 22}


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
    with engine.connect() as c:
        tabs = [r[0] for r in c.execute(text("select table_name from information_schema.tables where table_schema='public' and table_type='BASE TABLE' order by 1"))]
        views = [r[0] for r in c.execute(text("select table_name from information_schema.views where table_schema='public'"))]
        cons = c.execute(text("select conrelid::regclass::text t, contype, conname, pg_get_constraintdef(oid) d, conkey, confrelid::regclass::text ft, confkey, confdeltype from pg_constraint where connamespace='public'::regnamespace")).mappings().all()
        attn = {(r[0], r[1]): r[2] for r in c.execute(text("select attrelid::regclass::text, attnum, attname from pg_attribute where attnum>0 and not attisdropped and attrelid in (select oid from pg_class where relnamespace='public'::regnamespace)"))}
        missing = [t for t in tabs if t not in TABLE_DOC]
        assert not missing, f"undocumented tables: {missing}"
        pk, uk, fk, fkrows = {}, {}, {}, []
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
        md, totals, nullable = [], {}, {}
        for t in tabs:
            cols = [dict(r) for r in c.execute(text("select column_name,data_type,character_maximum_length cl,numeric_precision np,numeric_scale ns,is_nullable from information_schema.columns where table_schema='public' and table_name=:t order by ordinal_position"), {"t": t}).mappings()]
            n = c.execute(text(f'select count(*) from "{t}"')).scalar()
            totals[t] = (n, len(cols))
            extra = f" where id={ROW_ID[t]}" if t in ROW_ID else ""
            row = c.execute(text(f'select * from "{t}"{extra} order by 1 limit 1')).mappings().first() or {}
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
                if (t, name) in MASK:
                    ex = MASK[(t, name)]
                elif t == "upload_history" and name == "raw_content":
                    ex = show((row[name] or "").split("\n")[0])
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
        assert delrules == {"a"}, f"delete rules changed: {delrules}; update the prose"
    total = len(tabs)
    head = f"""# BloodLink Data Dictionary and Schema Reference

Generated {datetime.date.today()} by `docs/build_data_dictionary.py` from the **live PostgreSQL (Supabase) database** via
`information_schema` and `pg_constraint`, not from the `schema_*.sql` files. Data types, lengths, keys and constraints are what is
deployed; example values are real values from actual rows, with the exceptions described below.

## Summary

* **{total} tables** in the `public` schema, {sum(v[1] for v in totals.values())} columns in all, {len(fkrows)} foreign keys. Views: {', '.join(views) or 'none'}.
* **No unexpected tables.** Every table maps to a feature: identity (`facilities`, `users`, `password_reset_requests`), inventory
  (`blood_units`, `blood_type_thresholds`, `upload_history`), transfers (`requests`, `request_messages`), notification
  (`notifications`), donor outreach (`donors`, `blasts`, `blast_messages`, `blast_replies`) and forecasting (`inventory_snapshots`,
  `facility_forecast_cache`, `forecast_alert_state`, `synthetic_inventory_snapshots`, `synthetic_forecast_cache`). The ones easiest to
  overlook are `forecast_alert_state` (alert de-duplication), `upload_history` (audit; holds the raw CSV text and drives undo) and the
  two `synthetic_*` research tables.
* **Donor outreach tables:** `blasts` (the campaign), `blast_messages` (one row per donor messaged) and `blast_replies` (donor yes/no
  answers). Full column lists below.
* **`facilities` carries more than name, type, address, coordinates and `is_active`.** The onboarding migration also added
  `department`, `doh_license_number` and `profile_completed`, so the table has 10 columns.
* **Referential integrity:** every foreign key is `NO ACTION` on delete (a parent row cannot be deleted while children exist).
  The only CHECK constraint in the schema is `upload_history.upload_type`; enumerations such as `users.role`, `requests.status`
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
    tail = """
## Foreign key map

Every foreign key constraint in the deployed schema. `requests` joins `facilities` twice (requesting and supplying);
`blood_units.reserved_for_request_id` is nullable. All are `ON DELETE NO ACTION`. Cardinality reads child : parent.

""" + "\n".join(fkmd) + """

## Research vs operational

Which tables exist to support the forecasting research rather than day-to-day blood bank operation.

| Table | Class | Why |
|---|---|---|
| synthetic_inventory_snapshots | **Research** | Generated two-year demonstration series (no facility). Not blood bank records. Used to fit the shared stand-in model. |
| synthetic_forecast_cache | **Research** | Output of the stand-in model. Served only to facilities with fewer than 3 days of history, so it can appear in the UI, but it is model output on generated data, not a measurement. |
| facility_forecast_cache | Derived (forecasting) | Cache of per-facility SARIMAX output. Reproducible from `inventory_snapshots`; safe to truncate. |
| forecast_alert_state | Derived (forecasting) | De-duplication flag for forecast-shortage notifications. It has an operational effect (raises a notification) but exists only because of the forecast. |
| inventory_snapshots | Mixed | Operational counts, but also the series the forecast is fitted on. At the time of generation, 1,200 of its 1,320 rows came from one historical-stock CSV upload of a generated demonstration file; the rest are the app's own daily snapshots. It should not be described as a record of real supply. |

Every other table (`facilities`, `users`, `password_reset_requests`, `blood_units`, `blood_type_thresholds`, `requests`,
`request_messages`, `notifications`, `upload_history`, `donors`, `blasts`, `blast_messages`, `blast_replies`) is operational. In the
paper, `blood_units` is the system's record of physical inventory; the `synthetic_*` tables are a research and demonstration
apparatus and should not be presented as equivalent to it. The current database also holds demo and test operational rows
(demo facilities and accounts), so even the operational tables contain demonstration content in this deployment.
"""
    OUT.write_text(head + "\n".join(md) + tail, encoding="utf-8")
    print("wrote", OUT, total, "tables")


main()
