# BloodLink Data Dictionary and Schema Reference

Generated 2026-09-26 by `docs/build_data_dictionary.py` from the **live PostgreSQL (Supabase) database** via
`information_schema` and `pg_constraint`, not from the `schema_*.sql` files. Data types, lengths, keys and constraints are what is
deployed; example values are real values from actual rows, with the exceptions described below.

## Summary

* **18 tables** in the `public` schema, 131 columns in all, 24 foreign keys. Views: none.
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

## blast_messages

One row per message a blast addressed to one donor. Messages are simulated: there is no real SMS provider integration. Operational (donor outreach, part 2 of 3). (28 rows, 5 columns in the live database.)

| Field_Name | Data_Type | Length | Key | Description | Example |
|---|---|---|---|---|---|
| id | bigint | 64-bit | PK | Unique identifier of the message record. | 1 |
| blast_id | bigint | 64-bit | FK | The blast this message was part of (FK to blasts.id). | 1 |
| donor_id | bigint | 64-bit | FK | The donor it was addressed to (FK to donors.id). | 1 |
| message_text | text | — |  | The alert text composed for the donor. | [BloodLink Alert] O- donors urgently needed at Riverside Gen… |
| simulated_sent_at | timestamp with time zone | — |  | When the message was 'sent'. Sending is simulated end to end; nothing leaves the system. | 2026-07-13 21:27:46.422386+00:00 |

## blast_replies

A donor's reply (yes/no) to a blast; at most one reply per donor per blast. Operational (donor outreach, part 3 of 3). (9 rows, 5 columns in the live database.)

| Field_Name | Data_Type | Length | Key | Description | Example |
|---|---|---|---|---|---|
| id | bigint | 64-bit | PK | Unique identifier of the reply. | 1 |
| blast_id | bigint | 64-bit | FK + UK* | The blast being answered (FK to blasts.id). | 4 |
| donor_id | bigint | 64-bit | FK + UK* | The donor who replied (FK to donors.id). | 8 |
| reply | text | — |  | The donor's answer, 'yes' or 'no'. | yes |
| replied_at | timestamp with time zone | — |  | When the reply was recorded. | 2026-07-14 07:40:00+00:00 |

Composite unique key (blast_id, donor_id); `UK*` marks a column that is part of one.

## blasts

Donor outreach campaigns: one row per call for donors of a given blood type, with a target and a deadline. Operational (donor outreach, part 1 of 3). (9 rows, 8 columns in the live database.)

| Field_Name | Data_Type | Length | Key | Description | Example |
|---|---|---|---|---|---|
| id | bigint | 64-bit | PK | Unique identifier of the outreach blast. | 1 |
| facility_id | bigint | 64-bit | FK | The facility that launched the blast (FK to facilities.id). | 1 |
| blood_type | text | — |  | The blood type of donors being called. | O- |
| target_count | integer | 32-bit |  | How many donors the facility wants to respond. | 5 |
| time_limit_hours | integer | 32-bit |  | How many hours donors have to respond. | 2 |
| status | text | — |  | 'active' while open, 'completed' once closed. | active |
| created_at | timestamp with time zone | — |  | When the blast was launched. | 2026-07-13 21:27:46.422386+00:00 |
| deadline_at | timestamp with time zone | — |  | When the response window closes. | 2026-07-13 23:27:19.727403+00:00 |

## blood_type_thresholds

Per-facility minimum and maximum stock levels for each blood type; they drive low-stock and overstock status and restock recommendations. Operational configuration. (64 rows, 4 columns in the live database.)

| Field_Name | Data_Type | Length | Key | Description | Example |
|---|---|---|---|---|---|
| blood_type | text | — | PK | The blood type the levels apply to (part of the composite primary key). | A- |
| minimum_units | integer | 32-bit |  | The stock level below which the facility counts as low or breached for this type. | 50 |
| maximum_units | integer | 32-bit |  | The stock level above which the facility counts as overstocked for this type. | 100 |
| facility_id | bigint | 64-bit | PK + FK | The facility these levels belong to (part of the composite primary key) (FK to facilities.id). | 158 |

Composite primary key (facility_id, blood_type).

## blood_units

The blood inventory: one row per physical unit (bag/component) identified by its DIN. The core operational table. Operational. (3,166 rows, 14 columns in the live database.)

| Field_Name | Data_Type | Length | Key | Description | Example |
|---|---|---|---|---|---|
| id | bigint | 64-bit | PK | Unique identifier of the inventory row. | 363 |
| din | text | — | UK | Donation Identification Number: the unit's label/barcode, unique across the whole platform. | BB-5-O--15 |
| blood_type | text | — |  | ABO/Rh group of the unit (A+, A-, B+, B-, AB+, AB-, O+, O-). | O- |
| component | text | — |  | What the unit is: Packed RBC, Whole Blood, Platelets or Fresh Frozen Plasma. | Packed RBC |
| location | text | — |  | Where in the facility the unit is stored (a fridge, bay or freezer). | Bay 1 |
| volume_ml | integer | 32-bit |  | Volume of the unit in millilitres. | 280 |
| collected_date | date | — |  | The date the blood was drawn. | 2026-06-25 |
| expires_date | date | — |  | The last date the unit is usable; drives near-expiry and expired status. | 2026-09-23 |
| created_at | timestamp with time zone | — |  | When the row was added to the system. | 2026-07-10 06:10:00.631893+00:00 |
| facility_id | bigint | 64-bit | FK | The facility this unit belongs to (FK to facilities.id). | 4 |
| reserved_for_request_id | bigint | 64-bit | FK | The transfer request this unit is held for; NULL when the unit is free (FK to requests.id). Nullable. | NULL (no row has a value) |
| last_notified_expiry_status | text | — |  | The last expiry warning level ('near-expiry' or 'critical') already notified, so the same warning is not sent twice; NULL when none sent. Nullable. | critical |
| upload_history_id | bigint | 64-bit | FK | The CSV upload that created this unit, so the upload can be undone; NULL for units not added by upload (FK to upload_history.id). Nullable. | 22 |
| archived_at | timestamp with time zone | — |  | When the unit was archived out of active inventory; NULL while active. Nullable. | NULL (no row has a value) |

## donors

The donor contact list each facility keeps for outreach. Contains personal data. Operational. (20 rows, 7 columns in the live database.)

| Field_Name | Data_Type | Length | Key | Description | Example |
|---|---|---|---|---|---|
| id | bigint | 64-bit | PK | Unique identifier of the donor record. | 7 |
| name | text | — |  | The donor's name. Personal data. | Donor A |
| blood_type | text | — |  | The donor's blood type, used to pick who to contact for a blast. | O- |
| phone | text | — | UK* | The donor's contact number. Personal data; unique within a facility. | +1 5*****01 |
| facility_id | bigint | 64-bit | FK + UK* | The facility that keeps this donor on its list (FK to facilities.id). | 1 |
| created_at | timestamp with time zone | — |  | When the donor was added. | 2026-07-14 07:34:56.818586+00:00 |
| upload_history_id | bigint | 64-bit | FK | The CSV upload that added this donor; NULL when added by hand (FK to upload_history.id). Nullable. | NULL (no row has a value) |

Composite unique key (facility_id, phone); `UK*` marks a column that is part of one.

## facilities

Every organisation on the platform (hospitals and blood banks). It is the tenant root: nearly every other table carries a facility_id pointing here. Operational. (8 rows, 10 columns in the live database.)

| Field_Name | Data_Type | Length | Key | Description | Example |
|---|---|---|---|---|---|
| id | bigint | 64-bit | PK | Unique identifier of the facility. | 1 |
| name | text | — |  | The facility's display name shown to other facilities. | Riverside General Hospital |
| facility_type | text | — |  | Whether it is a 'hospital' (requests blood) or a 'bloodbank' (holds and supplies blood). Free text; no database CHECK enforces the two values. | hospital |
| address | text | — |  | Street address as entered or picked on the map. Nullable. | Manila City Hall Area, Manila |
| latitude | double precision | 53-bit |  | Map latitude of the facility, used for distance between facilities. Nullable. | 14.5958 |
| longitude | double precision | 53-bit |  | Map longitude of the facility. Nullable. | 120.9822 |
| department | text | — |  | The department or unit the profile was completed for (onboarding profile field). Nullable. | ss |
| doh_license_number | text | — |  | The facility's Department of Health licence number (onboarding profile field); not validated. Nullable. | ss |
| profile_completed | boolean | — |  | Whether the facility has finished its onboarding profile. False forces the onboarding step. | True |
| is_active | boolean | — |  | Whether the facility is active on the platform; inactive facilities are hidden from listings. | True |

## facility_forecast_cache

Cached per-facility SARIMAX forecast checkpoints, refit at most once a day per facility and blood type, so the dashboard does not refit on every load. Derived cache; forecasting. (56 rows, 10 columns in the live database.)

| Field_Name | Data_Type | Length | Key | Description | Example |
|---|---|---|---|---|---|
| id | bigint | 64-bit | PK | Unique identifier of the cached point. | 239 |
| facility_id | bigint | 64-bit | FK + UK* | The facility the forecast is for (FK to facilities.id). | 158 |
| blood_type | text | — | UK* | Blood type forecast. | A- |
| forecast_date | date | — | UK* | The future date this forecast point is for (checkpoints at 0, 5, 10, 15, 20, 25 and 30 days ahead). | 2026-09-24 |
| forecast_units | numeric | — |  | Predicted stock on that date. | 43 |
| lower_units | numeric | — |  | Lower end of the 95% prediction interval. | 43 |
| upper_units | numeric | — |  | Upper end of the 95% prediction interval. | 43 |
| trained_through_date | date | — |  | The last day of history the model was fitted on; the cache is reused only while this equals today. | 2026-09-24 |
| model_order | text | — |  | Label of the model that produced it (the fixed SARIMAX order plus the dengue regressor). | SARIMAX(0,1,4)x(1,0,1,7)+dengue |
| generated_at | timestamp with time zone | — |  | When it was computed. | 2026-09-23 17:25:40.823435+00:00 |

Composite unique key (facility_id, blood_type, forecast_date); `UK*` marks a column that is part of one.

## forecast_alert_state

Remembers whether a facility and blood type is currently in a forecast-shortage alert, so a notification is raised once when it starts rather than on every refresh. Derived state; forecasting. (3 rows, 4 columns in the live database.)

| Field_Name | Data_Type | Length | Key | Description | Example |
|---|---|---|---|---|---|
| facility_id | bigint | 64-bit | PK + FK | The facility (part of the composite primary key) (FK to facilities.id). | 5 |
| blood_type | text | — | PK | Blood type (part of the composite primary key). | A+ |
| alerting | boolean | — |  | True while this facility and type is in a forecast-shortage alert. | False |
| updated_at | timestamp with time zone | — |  | When the flag last changed. | 2026-09-22 06:16:01.229912+00:00 |

Composite primary key (facility_id, blood_type).

## inventory_snapshots

The daily count of usable units per facility and blood type. Written by the app's own daily snapshot and by historical-stock CSV uploads; it is the time series the per-facility forecast is fitted on. Operational data that also serves as forecasting input. (1,320 rows, 7 columns in the live database.)

| Field_Name | Data_Type | Length | Key | Description | Example |
|---|---|---|---|---|---|
| id | bigint | 64-bit | PK | Unique identifier of the snapshot row. | 185 |
| snapshot_date | date | — | UK* | The day the count describes. | 2026-07-14 |
| blood_type | text | — | UK* | Blood type counted. | B+ |
| units | integer | 32-bit |  | Usable units of that type in stock that day. | 90 |
| created_at | timestamp with time zone | — |  | When the row was written. | 2026-07-13 20:27:38.819426+00:00 |
| facility_id | bigint | 64-bit | FK + UK* | The facility counted (FK to facilities.id). | 6 |
| upload_history_id | bigint | 64-bit | FK | The historical-stock upload that supplied this row; NULL for the app's own daily snapshot (FK to upload_history.id). Nullable. | 21 |

Composite unique key (snapshot_date, blood_type, facility_id); `UK*` marks a column that is part of one.

## notifications

In-app notifications shown to a facility (incoming requests, expiring units, forecast shortages, and so on). Operational. (136 rows, 7 columns in the live database.)

| Field_Name | Data_Type | Length | Key | Description | Example |
|---|---|---|---|---|---|
| id | bigint | 64-bit | PK | Unique identifier of the notification. | 8 |
| facility_id | bigint | 64-bit | FK | The facility the notification is shown to (FK to facilities.id). | 3 |
| type | text | — |  | Category: unit_expiry, incoming_request, request_accepted, request_cancelled, transfer_confirmation_needed, transfer_completed or forecast_shortage. | incoming_request |
| message | text | — |  | The text shown to the user. | New 3-unit O+ request from UI Notif Hospital |
| link | text | — |  | The in-app page or record the notification opens (for example 'requests:14' or 'inventory'). Nullable. | requests:14 |
| read_at | timestamp with time zone | — |  | When the user read it; NULL while unread (currently NULL in every row). Nullable. | NULL (no row has a value) |
| created_at | timestamp with time zone | — |  | When the notification was raised. | 2026-08-21 05:52:48.287292+00:00 |

## password_reset_requests

One row per emailed password-reset link, storing only a hash of the token so a database leak cannot be used to reset accounts. Operational (security). (13 rows, 6 columns in the live database.)

| Field_Name | Data_Type | Length | Key | Description | Example |
|---|---|---|---|---|---|
| id | bigint | 64-bit | PK | Unique identifier of the reset request. | 1 |
| user_id | bigint | 64-bit | FK | The account the reset link was issued for (FK to users.id). | 6 |
| token_hash | text | — | UK | Hash of the one-time token that was emailed; the token itself is not stored. Unique. | a3f9c2e1-... |
| expires_at | timestamp with time zone | — |  | When the link stops working. | 2026-09-13 07:28:14.647367+00:00 |
| used_at | timestamp with time zone | — |  | When the link was used to set a new password; NULL while unused. Nullable. | 2026-09-13 06:41:03.414811+00:00 |
| created_at | timestamp with time zone | — |  | When the reset was requested. | 2026-09-13 06:28:15.698229+00:00 |

## request_messages

The chat thread attached to a transfer request, letting the two facilities coordinate. Operational. (19 rows, 5 columns in the live database.)

| Field_Name | Data_Type | Length | Key | Description | Example |
|---|---|---|---|---|---|
| id | bigint | 64-bit | PK | Unique identifier of the message. | 1 |
| request_id | bigint | 64-bit | FK | The request whose chat thread this message belongs to (FK to requests.id). | 3 |
| sender_facility_id | bigint | 64-bit | FK | The facility that wrote the message (FK to facilities.id). | 1 |
| message | text | — |  | The message text. | Hi, checking on the status of our O- request. |
| created_at | timestamp with time zone | — |  | When it was sent. | 2026-07-22 17:04:51.217005+00:00 |

## requests

Blood transfer requests between two facilities (one requests, one supplies) and their lifecycle from pending to completed. Operational. (24 rows, 10 columns in the live database.)

| Field_Name | Data_Type | Length | Key | Description | Example |
|---|---|---|---|---|---|
| id | bigint | 64-bit | PK | Unique identifier of the request. | 1 |
| requesting_facility_id | bigint | 64-bit | FK | The facility asking for blood (FK to facilities.id). | 1 |
| supplying_facility_id | bigint | 64-bit | FK | The facility being asked to supply it (FK to facilities.id). | 5 |
| blood_type | text | — |  | The blood type requested. | O- |
| quantity | integer | 32-bit |  | Number of units requested. | 3 |
| emergency_type | text | — |  | Why it is needed: 'trauma' (urgent) or 'restock' (routine). Free text; no database CHECK. | trauma |
| status | text | — |  | Lifecycle state: pending, accepted, declined, cancelled or completed. Free text; no database CHECK. Only pending, declined, cancelled and completed occur in the current data. | completed |
| created_at | timestamp with time zone | — |  | When the request was made. | 2026-07-10 09:42:39.346305+00:00 |
| supplier_confirmed_at | timestamp with time zone | — |  | When the supplier confirmed the hand-over; NULL until then. Nullable. | 2026-07-10 10:38:24.560726+00:00 |
| requester_confirmed_at | timestamp with time zone | — |  | When the requester confirmed receipt; NULL until then. Both confirmations complete the transfer. Nullable. | 2026-07-10 10:38:26.559298+00:00 |

## synthetic_forecast_cache

The stand-in forecast produced from the synthetic series, served to facilities that have fewer than 3 days of their own history. Research. (3,208 rows, 6 columns in the live database.)

| Field_Name | Data_Type | Length | Key | Description | Example |
|---|---|---|---|---|---|
| id | bigint | 64-bit | PK | Unique identifier of the row. | 3209 |
| blood_type | text | — | UK* | Blood type forecast. | O+ |
| forecast_date | date | — | UK* | The future date the value is for. | 2026-08-21 |
| forecast_units | numeric | — |  | Predicted stock for that date, to two decimals; model output on generated data. | 79.52 |
| model_order | text | — |  | Label of the model that produced it. | SARIMAX(0,1,4)x(1,0,1,7)+dengue |
| generated_at | timestamp with time zone | — |  | When it was generated. | 2026-08-21 12:10:53.697897+00:00 |

Composite unique key (blood_type, forecast_date); `UK*` marks a column that is part of one.

## synthetic_inventory_snapshots

A generated two-year daily stock series per blood type (no facility). It is demonstration data used to fit the shared stand-in model; it is not real blood bank records. Research. (5,840 rows, 5 columns in the live database.)

| Field_Name | Data_Type | Length | Key | Description | Example |
|---|---|---|---|---|---|
| id | bigint | 64-bit | PK | Unique identifier of the generated row. | 11681 |
| snapshot_date | date | — | UK* | The (generated) day. | 2024-08-21 |
| blood_type | text | — | UK* | Blood type. | A+ |
| units | integer | 32-bit |  | Generated stock level for that day. | 107 |
| created_at | timestamp with time zone | — |  | When the row was generated. | 2026-08-21 11:55:52.885309+00:00 |

Composite unique key (snapshot_date, blood_type); `UK*` marks a column that is part of one.

## upload_history

An audit record of each CSV upload (inventory, donors or historical stock), including the raw file and any per-row errors, so an upload can be undone. Operational (audit). (3 rows, 11 columns in the live database.)

| Field_Name | Data_Type | Length | Key | Description | Example |
|---|---|---|---|---|---|
| id | bigint | 64-bit | PK | Unique identifier of the upload. | 22 |
| facility_id | bigint | 64-bit | FK | The facility that uploaded the file (FK to facilities.id). | 1 |
| upload_type | text | — |  | Kind of upload: 'inventory', 'donors' or 'historical_stock'. Enforced by a CHECK constraint. | inventory |
| uploaded_by | bigint | 64-bit | FK | The user who uploaded it (FK to users.id). Nullable. | 6 |
| filename | text | — |  | Original file name. Nullable. | hospital_inventory.csv |
| uploaded_at | timestamp with time zone | — |  | When the upload happened. | 2026-09-26 10:31:20.123850+00:00 |
| rows_processed | integer | 32-bit |  | How many CSV rows were accepted. | 113 |
| rows_failed | integer | 32-bit |  | How many CSV rows were rejected. | 0 |
| error_details | jsonb | — |  | JSON list of per-row rejection reasons; an empty list when every row was accepted. | [] |
| raw_content | text | — |  | The uploaded CSV text, kept so an upload can be audited or undone. Shown here as its header line only. Nullable. | din,blood_type,component,location,volume_ml,collected_date,e… |
| undone_at | timestamp with time zone | — |  | When the upload was reversed; NULL if it stands. Nullable. | NULL (no row has a value) |

CHECK: `CHECK ((upload_type = ANY (ARRAY['inventory'::text, 'donors'::text, 'historical_stock'::text])))`.

## users

Login accounts. Each staff account belongs to one facility; the single admin account belongs to none. Operational. (9 rows, 7 columns in the live database.)

| Field_Name | Data_Type | Length | Key | Description | Example |
|---|---|---|---|---|---|
| id | bigint | 64-bit | PK | Unique identifier of the account. | 7 |
| email | text | — | UK | Login email; unique across all accounts. | demo.bloodbank@example.com |
| password_hash | text | — |  | bcrypt hash of the password. The password itself is never stored. | $2b$12$... |
| facility_id | bigint | 64-bit | FK | The facility this account works for. NULL for the platform admin, who belongs to no facility (FK to facilities.id). Nullable. | 5 |
| role | text | — |  | Account role: 'staff' (facility user) or 'admin'. Free text; no database CHECK. | staff |
| created_at | timestamp with time zone | — |  | When the account was created. | 2026-08-20 06:38:21.582281+00:00 |
| must_change_password | boolean | — |  | True when the account was issued with a temporary password and must set its own on first login. | False |

## Foreign key map

Every foreign key constraint in the deployed schema. `requests` joins `facilities` twice (requesting and supplying);
`blood_units.reserved_for_request_id` is nullable. All are `ON DELETE NO ACTION`. Cardinality reads child : parent.

| Child column | Parent column | Cardinality | Nullable |
|---|---|---|---|
| blast_messages.blast_id | blasts.id | many : 1 | no |
| blast_messages.donor_id | donors.id | many : 1 | no |
| blast_replies.blast_id | blasts.id | many : 1 | no |
| blast_replies.donor_id | donors.id | many : 1 | no |
| blasts.facility_id | facilities.id | many : 1 | no |
| blood_type_thresholds.facility_id | facilities.id | many : 1 | no |
| blood_units.facility_id | facilities.id | many : 1 | no |
| blood_units.reserved_for_request_id | requests.id | many : 0..1 | yes |
| blood_units.upload_history_id | upload_history.id | many : 0..1 | yes |
| donors.facility_id | facilities.id | many : 1 | no |
| donors.upload_history_id | upload_history.id | many : 0..1 | yes |
| facility_forecast_cache.facility_id | facilities.id | many : 1 | no |
| forecast_alert_state.facility_id | facilities.id | many : 1 | no |
| inventory_snapshots.facility_id | facilities.id | many : 1 | no |
| inventory_snapshots.upload_history_id | upload_history.id | many : 0..1 | yes |
| notifications.facility_id | facilities.id | many : 1 | no |
| password_reset_requests.user_id | users.id | many : 1 | no |
| request_messages.request_id | requests.id | many : 1 | no |
| request_messages.sender_facility_id | facilities.id | many : 1 | no |
| requests.requesting_facility_id | facilities.id | many : 1 | no |
| requests.supplying_facility_id | facilities.id | many : 1 | no |
| upload_history.facility_id | facilities.id | many : 1 | no |
| upload_history.uploaded_by | users.id | many : 0..1 | yes |
| users.facility_id | facilities.id | many : 0..1 | yes |

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
