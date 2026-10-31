# BloodLink Data Dictionary and Schema Reference

Generated 2026-11-01 by `docs/build_data_dictionary.py` from the **live PostgreSQL (Supabase) database** via
`information_schema` and `pg_constraint`, not from the `schema_*.sql` files. Data types, lengths, keys and constraints are what is
deployed; example values are real values from actual rows, with the exceptions described below.

## Summary

* **24 tables** in the `public` schema, 223 columns in all, 39 foreign keys. Views: none.
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
* **Referential integrity:** every foreign key is `NO ACTION` on delete (a parent row cannot be deleted while children exist), except `facility_sarimax_order.facility_id`, `inventory_snapshot_write_log.facility_id` and `inventory_snapshot_write_log.upload_history_id`, which `CASCADE` because those rows have no meaning without their parent (a pure log and derived selection results). `request_units.blood_unit_id` is `SET NULL`: the row outlives the parent, which is the point of a ledger.
  9 CHECK constraints exist, on `facility_registration_requests`, `otp_codes`, `request_unit_failures`, `request_units`, `upload_history`; each is shown under its table. Enumerations such as `users.role`, `requests.status`
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

One row per message a blast addressed to one donor. Messages are simulated: there is no real SMS provider integration. Operational (donor outreach, part 2 of 3). (0 rows, 5 columns in the live database.)

| Field_Name | Data_Type | Length | Key | Description | Example |
|---|---|---|---|---|---|
| id | bigint | 64-bit | PK | Unique identifier of the message record. | (table is empty) |
| blast_id | bigint | 64-bit | FK | The blast this message was part of (FK to blasts.id). | (table is empty) |
| donor_id | bigint | 64-bit | FK | The donor it was addressed to (FK to donors.id). | (table is empty) |
| message_text | text | — |  | The alert text composed for the donor. | (table is empty) |
| simulated_sent_at | timestamp with time zone | — |  | When the message was 'sent'. Sending is simulated end to end; nothing leaves the system. | (table is empty) |

## blast_replies

A donor's reply (yes/no) to a blast; at most one reply per donor per blast. Operational (donor outreach, part 3 of 3). (0 rows, 5 columns in the live database.)

| Field_Name | Data_Type | Length | Key | Description | Example |
|---|---|---|---|---|---|
| id | bigint | 64-bit | PK | Unique identifier of the reply. | (table is empty) |
| blast_id | bigint | 64-bit | FK + UK* | The blast being answered (FK to blasts.id). | (table is empty) |
| donor_id | bigint | 64-bit | FK + UK* | The donor who replied (FK to donors.id). | (table is empty) |
| reply | text | — |  | The donor's answer, 'yes' or 'no'. | (table is empty) |
| replied_at | timestamp with time zone | — |  | When the reply was recorded. | (table is empty) |

Composite unique key (blast_id, donor_id); `UK*` marks a column that is part of one.

## blasts

Donor outreach campaigns: one row per call for donors of a given blood type, with a target and a deadline. Operational (donor outreach, part 1 of 3). (0 rows, 8 columns in the live database.)

| Field_Name | Data_Type | Length | Key | Description | Example |
|---|---|---|---|---|---|
| id | bigint | 64-bit | PK | Unique identifier of the outreach blast. | (table is empty) |
| facility_id | bigint | 64-bit | FK | The facility that launched the blast (FK to facilities.id). | (table is empty) |
| blood_type | text | — |  | The blood type of donors being called. | (table is empty) |
| target_count | integer | 32-bit |  | How many donors the facility wants to respond. | (table is empty) |
| time_limit_hours | integer | 32-bit |  | How many hours donors have to respond. | (table is empty) |
| status | text | — |  | 'active' while open, 'completed' once closed. | (table is empty) |
| created_at | timestamp with time zone | — |  | When the blast was launched. | (table is empty) |
| deadline_at | timestamp with time zone | — |  | When the response window closes. | (table is empty) |

## blood_type_thresholds

Per-facility minimum and maximum stock levels for each blood type; they drive low-stock and overstock status and restock recommendations. Operational configuration. (64 rows, 4 columns in the live database.)

| Field_Name | Data_Type | Length | Key | Description | Example |
|---|---|---|---|---|---|
| blood_type | text | — | PK | The blood type the levels apply to (part of the composite primary key). | A- |
| minimum_units | integer | 32-bit |  | The stock level below which the facility counts as low or breached for this type. | 50 |
| maximum_units | integer | 32-bit |  | The stock level above which the facility counts as overstocked for this type. | 100 |
| facility_id | bigint | 64-bit | PK + FK | The facility these levels belong to (part of the composite primary key) (FK to facilities.id). | 2039 |

Composite primary key (facility_id, blood_type).

## blood_units

The blood inventory: one row per physical unit (bag/component) identified by its DIN. The core operational table. Operational. (6,198 rows, 14 columns in the live database.)

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
| last_notified_expiry_status | text | — |  | NO LONGER USED. The expiry warning level ('near-expiry' or 'critical') last notified for the unit, written while expiring units produced a once-per-tier notification. Nothing reads or writes it now (expiring units are shown on the dashboard instead of notified); the values that remain are history and carry no meaning. Nullable. | critical |
| upload_history_id | bigint | 64-bit | FK | The CSV upload that created this unit, so the upload can be undone; NULL for units not added by upload (FK to upload_history.id). Nullable. | NULL (no row has a value) |
| archived_at | timestamp with time zone | — |  | When the unit was archived out of active inventory; NULL while active. Nullable. | 2026-10-31 15:30:15.998692+00:00 |

## donors

The donor contact list each facility keeps for outreach. Contains personal data. Operational. (0 rows, 7 columns in the live database.)

| Field_Name | Data_Type | Length | Key | Description | Example |
|---|---|---|---|---|---|
| id | bigint | 64-bit | PK | Unique identifier of the donor record. | (table is empty) |
| name | text | — |  | The donor's name. Personal data. | (table is empty) |
| blood_type | text | — |  | The donor's blood type, used to pick who to contact for a blast. | (table is empty) |
| phone | text | — | UK* | The donor's contact number. Personal data; unique within a facility. | (table is empty) |
| facility_id | bigint | 64-bit | FK + UK* | The facility that keeps this donor on its list (FK to facilities.id). | (table is empty) |
| created_at | timestamp with time zone | — |  | When the donor was added. | (table is empty) |
| upload_history_id | bigint | 64-bit | FK | The CSV upload that added this donor; NULL when added by hand (FK to upload_history.id). Nullable. | (table is empty) |

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

Cached per-facility SARIMAX forecast checkpoints, refit at most once a day per facility and blood type, so the dashboard does not refit on every load. Derived cache; forecasting. (91 rows, 10 columns in the live database.)

| Field_Name | Data_Type | Length | Key | Description | Example |
|---|---|---|---|---|---|
| id | bigint | 64-bit | PK | Unique identifier of the cached point. | 5553 |
| facility_id | bigint | 64-bit | FK + UK* | The facility the forecast is for (FK to facilities.id). | 5 |
| blood_type | text | — | UK* | Blood type forecast. | O+ |
| forecast_date | date | — | UK* | The future date this forecast point is for (checkpoints at 0, 5, 10, 15, 20, 25 and 30 days ahead). | 2026-11-30 |
| forecast_units | numeric | — |  | Predicted stock on that date. | 101 |
| lower_units | numeric | — |  | Lower end of the 95% prediction interval. | 90 |
| upper_units | numeric | — |  | Upper end of the 95% prediction interval. | 112 |
| trained_through_date | date | — |  | The last day of history the model was fitted on; the cache is reused only while this equals today. | 2026-10-31 |
| model_order | text | — |  | Label of the model that produced it: the SARIMAX order plus the dengue regressor, then in brackets how the order was obtained: [grid-searched] (a facility_sarimax_order row), [ai-recommended] (the decision-tree selector) or [default-order] (the fixed default). The bracket names the mechanism only. Rows written before the brackets existed have no suffix (a grid-searched order) or ' [fallback]' (the default order); the API reports those in today's spelling. | SARIMAX(1,0,2)x(1,0,1,7)+dengue [grid-searched] |
| generated_at | timestamp with time zone | — |  | When it was computed. | 2026-10-31 09:10:37.877702+00:00 |

Composite unique key (facility_id, blood_type, forecast_date); `UK*` marks a column that is part of one.

## facility_registration_requests

A facility's self-service application to join the platform: organisation details, a contact person and the password they chose (hashed), held until an administrator approves or rejects it. Status runs submitted, then email_verified once the contact confirms a one-time code (see otp_codes), then approved or rejected. Approval creates the facility, its default stock thresholds and its first staff account; either decision archives the request. Contains personal data. Operational (identity). (3 rows, 17 columns in the live database.)

| Field_Name | Data_Type | Length | Key | Description | Example |
|---|---|---|---|---|---|
| id | bigint | 64-bit | PK | Unique identifier of the registration request. | 46 |
| facility_name | text | — |  | The applicant facility's name; becomes facilities.name on approval. | E2E Reject Flow Hospital |
| facility_type | text | — |  | 'hospital' or 'bloodbank'. Enforced by a CHECK constraint. | hospital |
| address | text | — |  | Street address entered or picked on the map. | Malvar |
| latitude | double precision | 53-bit |  | Map latitude of the facility; may be missing if no location was picked. Nullable. | 14.05 |
| longitude | double precision | 53-bit |  | Map longitude of the facility. Nullable. | 121.15 |
| doh_license_number | text | — |  | The Department of Health licence number given on the form; not validated. | DOH-E2E-004 |
| contact_person | text | — |  | Name of the person applying. Personal data. | (personal data; not shown) |
| email | text | — |  | The applicant's email address, which the one-time code is sent to and which becomes the login on approval. Personal data. Unique among live (not archived) requests. | (personal data; not shown) |
| phone | text | — |  | The applicant's phone number. Personal data. | +1 5*****01 |
| password_hash | text | — |  | bcrypt hash of the password chosen at registration, copied to users.password_hash on approval. The password itself is never stored. | $2b$12$... |
| status | text | — |  | submitted, email_verified, approved or rejected. Enforced by a CHECK constraint. | rejected |
| rejection_reason | text | — |  | The administrator's stated reason when the request was rejected; NULL otherwise. Free text. Nullable. | (free text; not shown) |
| reviewed_by | bigint | 64-bit | FK | The administrator account that approved or rejected the request (FK to users.id). Nullable. | 13 |
| reviewed_at | timestamp with time zone | — |  | When the decision was made; NULL while undecided. Nullable. | 2026-09-28 10:44:05.673415+00:00 |
| archived_at | timestamp with time zone | — |  | When the request was archived (set on approval or rejection, so a later application with the same email starts a fresh row); NULL while live. Nullable. | 2026-09-28 10:44:05.673415+00:00 |
| created_at | timestamp with time zone | — |  | When the request was first submitted. | 2026-09-28 10:44:00.148423+00:00 |

CHECK: `CHECK ((facility_type = ANY (ARRAY['hospital'::text, 'bloodbank'::text])))`. CHECK: `CHECK ((status = ANY (ARRAY['submitted'::text, 'email_verified'::text, 'approved'::text, 'rejected'::text])))`.

## facility_sarimax_order

The SARIMAX order chosen offline for each facility and blood type by a grid search on training data only (the series minus its last 30 days), with diagnostics reported separately on a 30-day hold-out the search never saw. A facility and type with no row here is fitted with the AI-recommended order or the fixed default instead (see facility_forecast_cache.model_order). Derived; forecasting. (8 rows, 27 columns in the live database.)

| Field_Name | Data_Type | Length | Key | Description | Example |
|---|---|---|---|---|---|
| facility_id | bigint | 64-bit | PK + FK | The facility the order was chosen for (part of the composite primary key). Rows are deleted with the facility (FK to facilities.id). | 5 |
| blood_type | text | — | PK | Blood type the order was chosen for (part of the composite primary key). | A- |
| p | smallint | — |  | Non-seasonal autoregressive order of the chosen model. | 1 |
| d | smallint | — |  | Non-seasonal differencing order of the chosen model. | 0 |
| q | smallint | — |  | Non-seasonal moving-average order of the chosen model. | 2 |
| seasonal_p | smallint | — |  | Seasonal autoregressive order. | 1 |
| seasonal_d | smallint | — |  | Seasonal differencing order; the grid fixes it at 0. | 0 |
| seasonal_q | smallint | — |  | Seasonal moving-average order. | 1 |
| seasonal_s | smallint | — |  | Seasonal period in days; 7 (weekly). | 7 |
| aic | double precision | 53-bit |  | AIC of the chosen model fitted on the training window. | 962.774597001934 |
| lb_p_14_in_sample | double precision | 53-bit |  | Ljung-Box p-value at lag 14 on the chosen model's in-sample residuals, corrected for the model's ARMA parameters. | 0.0510309727281807 |
| lb_p_21_in_sample | double precision | 53-bit |  | Same at lag 21. | 0.193014054997755 |
| lb_p_28_in_sample | double precision | 53-bit |  | Same at lag 28. | 0.0767657102783659 |
| criterion_satisfied | boolean | — |  | True when the chosen model passed Ljung-Box (p above 0.05) at all three lags; False means no candidate did and the lowest-AIC candidate was kept. | True |
| n_candidates_converged | smallint | — |  | How many of the grid's candidate models converged and passed the sanity check on the training window. | 67 |
| lb_p_14_holdout_onestep | double precision | 53-bit |  | Ljung-Box p-value at lag 14 on one-step-ahead errors over the hold-out, with parameters not re-estimated; NULL if not computable. Nullable. | 0.661242638615603 |
| lb_p_21_holdout_onestep | double precision | 53-bit |  | Same at lag 21. Nullable. | 0.741370245653675 |
| lb_p_28_holdout_onestep | double precision | 53-bit |  | Same at lag 28. Nullable. | 0.186386890595778 |
| holdout_mape | double precision | 53-bit |  | Mean absolute percentage error, in percent, of the chosen model's 30-day forecast against the hold-out actuals; NULL if undefined. Nullable. | 5.06872563476744 |
| holdout_rmse | double precision | 53-bit |  | Root mean squared error, in units, of that forecast. Nullable. | 0.954648565046465 |
| holdout_baseline_mape | double precision | 53-bit |  | The same percentage error for a naive baseline that repeats the last training value. Nullable. | 7.54085266585267 |
| holdout_baseline_rmse | double precision | 53-bit |  | The same root mean squared error, in units, for that naive baseline. Nullable. | 1.50554530541816 |
| trained_through_date | date | — |  | The last day of the training window: the selection day minus the 30-day hold-out. | 2026-08-29 |
| evaluated_through_date | date | — |  | The last day of the hold-out; the day the selection ran. | 2026-09-28 |
| selected_at | timestamp with time zone | — |  | When the row was written (UTC). | 2026-09-27 19:36:15.446485+00:00 |
| start_params | jsonb | — |  | The chosen model's fitted parameters on the training window, used to warm-start the fit on the full series; NULL on rows written before this column existed. Nullable. | [-2.3073161410847987, 0.3467861248855131, 0.0680065838502903… |
| selection_aic_per_obs | double precision | 53-bit |  | The training-window AIC divided by the number of observations; the baseline the live fit's soundness check compares against. NULL on rows written before this column existed. Nullable. | 2.4560576454131 |

Composite primary key (facility_id, blood_type).

## forecast_alert_state

Remembers whether a facility and blood type is currently in a forecast-shortage alert, so a notification is raised once when it starts rather than on every refresh. Derived state; forecasting. (8 rows, 4 columns in the live database.)

| Field_Name | Data_Type | Length | Key | Description | Example |
|---|---|---|---|---|---|
| facility_id | bigint | 64-bit | PK + FK | The facility (part of the composite primary key) (FK to facilities.id). | 5 |
| blood_type | text | — | PK | Blood type (part of the composite primary key). | A- |
| alerting | boolean | — |  | True while this facility and type is in a forecast-shortage alert. | False |
| updated_at | timestamp with time zone | — |  | When the flag last changed. | 2026-10-26 05:52:20.029688+00:00 |

Composite primary key (facility_id, blood_type).

## inventory_snapshot_write_log

Append-only log of every value a historical-stock upload wrote into inventory_snapshots, plus the original organic value the first time an upload overwrote one. It exists so undoing an upload can restore what was overwritten and handle overlapping uploads correctly. Operational (audit). (24,480 rows, 7 columns in the live database.)

| Field_Name | Data_Type | Length | Key | Description | Example |
|---|---|---|---|---|---|
| id | bigint | 64-bit | PK | Unique identifier of the log entry; higher means written later. | 9620 |
| facility_id | bigint | 64-bit | FK | The facility whose snapshot was written. Log entries are deleted with the facility (FK to facilities.id). | 5 |
| snapshot_date | date | — |  | The day of the snapshot that was written. | 2026-03-30 |
| blood_type | text | — |  | Blood type of the snapshot that was written. | O+ |
| units | integer | 32-bit |  | The unit count that was written for that day and type. | 149 |
| upload_history_id | bigint | 64-bit | FK | The upload that wrote this value; NULL marks the original organic value saved before an upload overwrote it. Log entries are deleted with the upload (FK to upload_history.id). Nullable. | 104 |
| created_at | timestamp with time zone | — |  | When the entry was logged. | 2026-09-26 15:52:11.599627+00:00 |

## inventory_snapshots

The daily count of usable units (not past their expiry date) per facility and blood type, one row per day per type. Written when a blood bank loads its forecast, nightly for every active facility including hospitals (POST /internal/daily-snapshot at 23:30 Manila, first run 2026-10-27), and by historical-stock CSV uploads. All eight blood types are written each time and a type with none in stock is an explicit 0, so a missing day means nobody recorded it, not that stock was zero. It is the time series the per-facility forecast is fitted on, and the forecast carries the last value forward over a day with no rows. Operational data that also serves as forecasting input. (5,008 rows, 7 columns in the live database.)

| Field_Name | Data_Type | Length | Key | Description | Example |
|---|---|---|---|---|---|
| id | bigint | 64-bit | PK | Unique identifier of the snapshot row. | 185 |
| snapshot_date | date | — | UK* | The day the count describes. | 2026-07-14 |
| blood_type | text | — | UK* | Blood type counted. | B+ |
| units | integer | 32-bit |  | Usable units of that type in stock that day (not past expiry). An explicit 0 means the shelf was empty; no row for the day means it was not observed. | 90 |
| created_at | timestamp with time zone | — |  | When the row was first written. | 2026-07-13 20:27:38.819426+00:00 |
| facility_id | bigint | 64-bit | FK + UK* | The facility counted. Hospitals are counted too, though they are never forecast from (FK to facilities.id). | 6 |
| upload_history_id | bigint | 64-bit | FK | The historical-stock upload that supplied this row; NULL for rows the app wrote itself (a forecast load or the nightly job) (FK to upload_history.id). Nullable. | 21 |

Composite unique key (snapshot_date, blood_type, facility_id); `UK*` marks a column that is part of one.

## notifications

In-app notifications shown to a facility (incoming requests, expiring units, forecast shortages, and so on). Operational. (422 rows, 7 columns in the live database.)

| Field_Name | Data_Type | Length | Key | Description | Example |
|---|---|---|---|---|---|
| id | bigint | 64-bit | PK | Unique identifier of the notification. | 8 |
| facility_id | bigint | 64-bit | FK | The facility the notification is shown to (FK to facilities.id). | 3 |
| type | text | — |  | Category, one of 10 written today: blast_complete, expiring_unit_alert, forecast_shortage, incoming_request, receipt_needed, request_accepted, request_cancelled, request_declined, transfer_completed, transfer_units_failed. Rows written earlier may also carry transfer_confirmation_needed, unit_expiry, which nothing writes now. | incoming_request |
| message | text | — |  | The text shown to the user. | New 3-unit O+ request from UI Notif Hospital |
| link | text | — |  | The in-app page or record the notification opens (for example 'requests:14' or 'inventory'). Nullable. | requests:14 |
| read_at | timestamp with time zone | — |  | When it was read, by the user or by the system resolving it (the request moved on, the receipt was confirmed, the forecast cleared, the request no longer exists); NULL while unread. A resolved row is marked read, never deleted. Nullable. | 2026-10-31 11:09:17.264251+00:00 |
| created_at | timestamp with time zone | — |  | When the notification was raised. | 2026-08-21 05:52:48.287292+00:00 |

## otp_codes

One-time codes emailed to a person: either to prove a registrant owns the email on a facility_registration_requests row, or to reset a password. Only a hash of each code is stored, with a count of wrong guesses and an expiry; each row is bound to exactly one registration request or one user. Operational (security). (6 rows, 10 columns in the live database.)

| Field_Name | Data_Type | Length | Key | Description | Example |
|---|---|---|---|---|---|
| id | bigint | 64-bit | PK | Unique identifier of the code. | 70 |
| purpose | text | — |  | What the code is for: 'registration_email_verify' or 'password_reset'. Enforced by a CHECK constraint. | registration_email_verify |
| registration_request_id | bigint | 64-bit | FK | The registration request being verified. Set only for registration_email_verify codes; a CHECK ties it to the purpose (FK to facility_registration_requests.id). Nullable. | 46 |
| user_id | bigint | 64-bit | FK | The account whose password the code resets. Set only for password_reset codes; a CHECK ties it to the purpose (FK to users.id). Nullable. | 234 |
| email | text | — |  | The address the code was emailed to. Personal data. | (personal data; not shown) |
| code_hash | text | — |  | Hash of the code; the code itself is never stored. | $2b$12$... |
| attempts | integer | 32-bit |  | How many wrong guesses have been made against this code; the code stops being accepted at the cap. | 0 |
| expires_at | timestamp with time zone | — |  | When the code stops working. | 2026-09-28 10:54:00.640618+00:00 |
| used_at | timestamp with time zone | — |  | When the code was accepted or superseded by a newly issued one; NULL while still usable. Nullable. | 2026-09-28 10:44:03.138049+00:00 |
| created_at | timestamp with time zone | — |  | When the code was issued. | 2026-09-28 10:44:00.148423+00:00 |

CHECK: `CHECK ((purpose = ANY (ARRAY['registration_email_verify'::text, 'password_reset'::text])))`. CHECK: `CHECK ((((purpose = 'registration_email_verify'::text) AND (registration_request_id IS NOT NULL) AND (user_id IS NULL)) OR ((purpose = 'password_reset'::text) AND (user_id IS NOT NULL) AND (registration_request_id IS NULL))))`.

## password_reset_requests

One row per emailed password-reset link, storing only a hash of the token so a database leak cannot be used to reset accounts. Operational (security). (1 rows, 6 columns in the live database.)

| Field_Name | Data_Type | Length | Key | Description | Example |
|---|---|---|---|---|---|
| id | bigint | 64-bit | PK | Unique identifier of the reset request. | 15 |
| user_id | bigint | 64-bit | FK | The account the reset link was issued for (FK to users.id). | 6 |
| token_hash | text | — | UK | Hash of the one-time token that was emailed; the token itself is not stored. Unique. | a3f9c2e1-... |
| expires_at | timestamp with time zone | — |  | When the link stops working. | 2026-09-30 06:21:47.131058+00:00 |
| used_at | timestamp with time zone | — |  | When the link was used to set a new password; NULL while unused. Nullable. | NULL (no row has a value) |
| created_at | timestamp with time zone | — |  | When the reset was requested. | 2026-09-30 05:21:47.237577+00:00 |

## request_messages

The chat thread attached to a transfer request, letting the two facilities coordinate. Operational. (4 rows, 5 columns in the live database.)

| Field_Name | Data_Type | Length | Key | Description | Example |
|---|---|---|---|---|---|
| id | bigint | 64-bit | PK | Unique identifier of the message. | 6 |
| request_id | bigint | 64-bit | FK | The request whose chat thread this message belongs to (FK to requests.id). | 4 |
| sender_facility_id | bigint | 64-bit | FK | The facility that wrote the message (FK to facilities.id). | 4 |
| message | text | — |  | The message text. | hello |
| created_at | timestamp with time zone | — |  | When it was sent. | 2026-07-22 21:08:23.594478+00:00 |

## request_unit_failures

One row per reserved unit that could not be received because it expired between the supplier's release and the requester's receipt. Records which DIN failed and why, so a short delivery is never unexplained. Operational (audit). (0 rows, 7 columns in the live database.)

| Field_Name | Data_Type | Length | Key | Description | Example |
|---|---|---|---|---|---|
| id | bigint | 64-bit | PK | Unique identifier of the failure record. | (table is empty) |
| request_id | bigint | 64-bit | FK + UK* | The transfer request the unit was reserved for (FK to requests.id). | (table is empty) |
| blood_unit_id | bigint | 64-bit | FK + UK* | The unit that was not received. It stays at the supplier, archived (FK to blood_units.id). | (table is empty) |
| din | text | — |  | The unit's donation identification number at the moment receipt was refused. | (table is empty) |
| expires_date | date | — |  | The unit's expiry date at the moment receipt was refused; before that day's business date. | (table is empty) |
| reason | text | — |  | Why the unit failed. Only 'expired_before_receipt' exists (database CHECK). | (table is empty) |
| created_at | timestamp with time zone | — |  | When receipt was confirmed and the failure recorded. | (table is empty) |

Composite unique key (request_id, blood_unit_id); `UK*` marks a column that is part of one. CHECK: `CHECK ((reason = 'expired_before_receipt'::text))`.

## request_units

The per-unit ledger of a transfer: one row per unit the supplier reserved at release, copied as it stood then (DIN, type, component, volume, dates, shelf), then settled as received or failed when the requester confirms receipt. A snapshot, so a later CSV re-upload cannot rewrite what was released. Operational (audit). (7 rows, 15 columns in the live database.)

| Field_Name | Data_Type | Length | Key | Description | Example |
|---|---|---|---|---|---|
| id | bigint | 64-bit | PK | Unique identifier of the ledger row. | 1 |
| request_id | bigint | 64-bit | FK + UK* | The transfer request the unit was reserved for (FK to requests.id). | 60 |
| blood_unit_id | bigint | 64-bit | FK + UK* | The unit, while it still exists. NULL once the unit has been deleted from inventory (ON DELETE SET NULL); the DIN below keeps the record (FK to blood_units.id). Nullable. | 54140 |
| din | text | — | UK* | The unit's donation identification number at release. | BB-5-A--12 |
| blood_type | text | — |  | The unit's blood type at release (a later CSV re-upload may change the live unit). | A- |
| component | text | — |  | The unit's component at release. | Packed RBC |
| volume_ml | integer | 32-bit |  | The unit's volume in millilitres at release. | 280 |
| collected_date | date | — |  | The unit's collection date at release. | 2026-10-14 |
| expires_date | date | — |  | The unit's expiry date at release (a snapshot; never updated). | 2027-01-12 |
| origin_location | text | — |  | The supplier's shelf or storage location for the unit at release. | Bay 1 |
| days_remaining_at_release | integer | 32-bit |  | Whole days between the release day (Manila business date) and expires_date. | 73 |
| outcome | text | — |  | 'reserved' from release until receipt, then 'received' or 'failed' (database CHECK). | received |
| reason | text | — |  | Why the unit failed; set exactly when outcome is 'failed'. Only 'expired_before_receipt' exists (database CHECK). Nullable. | NULL (no row has a value) |
| days_remaining_at_receipt | integer | 32-bit |  | Whole days between the receipt day and the live unit's expiry at receipt, the value the received/failed decision was made on; NULL until receipt. Zero or negative for a unit that expired in transit. It differs from expires_date minus the receipt day only if the unit's expiry was changed after release. Nullable. | 73 |
| created_at | timestamp with time zone | — |  | When the supplier confirmed release and the row was written. | 2026-10-30 21:40:01.937390+00:00 |

Composite unique key (request_id, blood_unit_id); `UK*` marks a column that is part of one. Composite unique key (request_id, din); `UK*` marks a column that is part of one. CHECK: `CHECK (((outcome = 'reserved'::text) = (days_remaining_at_receipt IS NULL)))`. CHECK: `CHECK ((outcome = ANY (ARRAY['reserved'::text, 'received'::text, 'failed'::text])))`. CHECK: `CHECK ((((outcome = 'failed'::text) AND (reason IS NOT NULL) AND (reason = 'expired_before_receipt'::text)) OR ((outcome <> 'failed'::text) AND (reason IS NULL))))`.

## requests

Blood transfer requests between two facilities (one requests, one supplies) and their lifecycle from pending to completed. Operational. (14 rows, 19 columns in the live database.)

| Field_Name | Data_Type | Length | Key | Description | Example |
|---|---|---|---|---|---|
| id | bigint | 64-bit | PK | Unique identifier of the request. | 2 |
| requesting_facility_id | bigint | 64-bit | FK | The facility asking for blood (FK to facilities.id). | 1 |
| supplying_facility_id | bigint | 64-bit | FK | The facility being asked to supply it (FK to facilities.id). | 6 |
| blood_type | text | — |  | The blood type requested. | A+ |
| quantity | integer | 32-bit |  | Number of units requested. | 5 |
| emergency_type | text | — |  | Why it is needed: 'trauma' (urgent) or 'restock' (routine). Free text; no database CHECK. | restock |
| status | text | — |  | Lifecycle state: pending, accepted, declined, cancelled, completed, or failed (every reserved unit expired before receipt; see request_unit_failures). Completed can still carry failed units. Free text; no database CHECK. | cancelled |
| created_at | timestamp with time zone | — |  | When the request was made. | 2026-07-10 09:42:39.655554+00:00 |
| supplier_confirmed_at | timestamp with time zone | — |  | When the supplier confirmed the hand-over; NULL until then. Nullable. | 2026-09-23 20:11:54.700112+00:00 |
| requester_confirmed_at | timestamp with time zone | — |  | When the requester confirmed receipt; NULL until then. Both confirmations complete the transfer. Nullable. | 2026-09-26 09:04:31.616919+00:00 |
| component | text | — |  | The blood component requested ('Packed RBC' or 'Platelets'); release reserves only units of exactly this component. NULL on requests made before components were recorded, meaning not recorded, and such a request cannot be released. Free text; no database CHECK. Nullable. | Packed RBC |
| accepted_at | timestamp with time zone | — |  | When the supplier accepted; NULL until then, and NULL on requests that changed state before this column existed (not recorded). Nullable. | 2026-10-30 21:29:04.979341+00:00 |
| declined_at | timestamp with time zone | — |  | When the supplier declined; NULL unless declined, and NULL on requests declined before this column existed (not recorded). Nullable. | NULL (no row has a value) |
| cancelled_at | timestamp with time zone | — |  | When the requester cancelled; NULL unless cancelled, and NULL on requests cancelled before this column existed (not recorded). Nullable. | NULL (no row has a value) |
| accepted_by | bigint | 64-bit | FK | The user account that accepted (FK to users.id). BloodLink has one account per facility, so this identifies the supplier facility's login, not a named person. NULL = not recorded (FK to users.id). Nullable. | 7 |
| declined_by | bigint | 64-bit | FK | The user account that declined (FK to users.id). One account per facility: the supplier facility's login, not a named person. NULL = not recorded (FK to users.id). Nullable. | NULL (no row has a value) |
| cancelled_by | bigint | 64-bit | FK | The user account that cancelled (FK to users.id). One account per facility: the requester facility's login, not a named person. NULL = not recorded (FK to users.id). Nullable. | NULL (no row has a value) |
| released_by | bigint | 64-bit | FK | The user account that confirmed release, pairing with supplier_confirmed_at (FK to users.id). One account per facility: not a named person. NULL = not recorded (FK to users.id). Nullable. | 7 |
| received_by | bigint | 64-bit | FK | The user account that confirmed receipt, pairing with requester_confirmed_at (FK to users.id). One account per facility: not a named person. NULL = not recorded (FK to users.id). Nullable. | 6 |

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

An audit record of each CSV upload (inventory, donors or historical stock), including the raw file and any per-row errors, so an upload can be undone. Operational (audit). (14 rows, 11 columns in the live database.)

| Field_Name | Data_Type | Length | Key | Description | Example |
|---|---|---|---|---|---|
| id | bigint | 64-bit | PK | Unique identifier of the upload. | 21 |
| facility_id | bigint | 64-bit | FK | The facility that uploaded the file (FK to facilities.id). | 158 |
| upload_type | text | — |  | Kind of upload: 'inventory', 'donors' or 'historical_stock'. Enforced by a CHECK constraint. | historical_stock |
| uploaded_by | bigint | 64-bit | FK | The user who uploaded it (FK to users.id). Nullable. | 47 |
| filename | text | — |  | Original file name. Nullable. | demo_historical_stock.csv |
| uploaded_at | timestamp with time zone | — |  | When the upload happened. | 2026-09-22 07:52:50.576315+00:00 |
| rows_processed | integer | 32-bit |  | How many CSV rows were accepted. | 1200 |
| rows_failed | integer | 32-bit |  | How many CSV rows were rejected. | 0 |
| error_details | jsonb | — |  | JSON list of per-row rejection reasons; an empty list when every row was accepted. | [] |
| raw_content | text | — |  | The uploaded CSV text, kept so an upload can be audited or undone. Shown here as its header line only. Nullable. | snapshot_date,blood_type,units |
| undone_at | timestamp with time zone | — |  | When the upload was reversed; NULL if it stands. Nullable. | 2026-09-26 15:54:53.873236+00:00 |

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
`blood_units.reserved_for_request_id` is nullable. All are `ON DELETE NO ACTION` except `facility_sarimax_order.facility_id`, `inventory_snapshot_write_log.facility_id` and `inventory_snapshot_write_log.upload_history_id`, which are `ON DELETE CASCADE`. `request_units.blood_unit_id` is `SET NULL`: the row outlives the parent, which is the point of a ledger. Cardinality reads child : parent.

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
| facility_registration_requests.reviewed_by | users.id | many : 0..1 | yes |
| facility_sarimax_order.facility_id | facilities.id | many : 1 | no |
| forecast_alert_state.facility_id | facilities.id | many : 1 | no |
| inventory_snapshot_write_log.facility_id | facilities.id | many : 1 | no |
| inventory_snapshot_write_log.upload_history_id | upload_history.id | many : 0..1 | yes |
| inventory_snapshots.facility_id | facilities.id | many : 1 | no |
| inventory_snapshots.upload_history_id | upload_history.id | many : 0..1 | yes |
| notifications.facility_id | facilities.id | many : 1 | no |
| otp_codes.registration_request_id | facility_registration_requests.id | many : 0..1 | yes |
| otp_codes.user_id | users.id | many : 0..1 | yes |
| password_reset_requests.user_id | users.id | many : 1 | no |
| request_messages.request_id | requests.id | many : 1 | no |
| request_messages.sender_facility_id | facilities.id | many : 1 | no |
| request_unit_failures.blood_unit_id | blood_units.id | many : 1 | no |
| request_unit_failures.request_id | requests.id | many : 1 | no |
| request_units.blood_unit_id | blood_units.id | many : 0..1 | yes |
| request_units.request_id | requests.id | many : 1 | no |
| requests.accepted_by | users.id | many : 0..1 | yes |
| requests.cancelled_by | users.id | many : 0..1 | yes |
| requests.declined_by | users.id | many : 0..1 | yes |
| requests.received_by | users.id | many : 0..1 | yes |
| requests.released_by | users.id | many : 0..1 | yes |
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
| facility_sarimax_order | Derived (forecasting) | The SARIMAX order chosen offline for each facility and blood type, with its selection diagnostics. Reproducible by re-running `server/select_orders.py` on `inventory_snapshots`; with no row the live fitter uses the AI-recommended or default order. |
| forecast_alert_state | Derived (forecasting) | De-duplication flag for forecast-shortage notifications. It has an operational effect (raises a notification) but exists only because of the forecast. |
| inventory_snapshots | Mixed | Operational counts, but also the series the forecast is fitted on. At the time of generation, 4,560 of its 5,008 rows came from historical-stock CSV uploads and 448 were written by the app itself (a forecast load, or the nightly job for every active facility). Uploaded files in this deployment have included generated demonstration data, so the table should not be described as a record of real supply. A day with no row is unobserved, not zero. |

Every other table (`blast_messages`, `blast_replies`, `blasts`, `blood_type_thresholds`, `blood_units`, `donors`, `facilities`, `facility_registration_requests`, `inventory_snapshot_write_log`, `notifications`, `otp_codes`, `password_reset_requests`, `request_messages`, `request_unit_failures`, `request_units`, `requests`, `upload_history`, `users`) is operational. In the
paper, `blood_units` is the system's record of physical inventory; the `synthetic_*` tables are a research and demonstration
apparatus and should not be presented as equivalent to it. The current database also holds demo and test operational rows
(demo facilities and accounts), so even the operational tables contain demonstration content in this deployment.
