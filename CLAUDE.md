# BloodLink

Undergraduate CS thesis (University of Batangas). Blood supply management and
emergency response for hospitals and blood banks: inventory, inter-facility
requests, SARIMAX demand forecasting, donor broadcast.

## Hard rules

- **Never commit or push.** Euhan does that. Report and stop.
- **Never touch the SMS / donor blast feature** unless asked explicitly.
- **The dev database IS production** (Supabase). Any local script touching it
  touches live data. Disclose every mutation you make, including test rows and
  dropped tables.
- **Archive, don't delete.** Soft-delete via `archived_at` / `undone_at`.
- **Keep `server/requirements.txt` lean.** scikit-learn and joblib OOM the
  512 MB instance; they live in `requirements-dev.txt` only. Every direct dependency is
  pinned, numpy included (imported directly, but only transitive until 2026-10-29) and pytest in the dev
  file; transitive dependencies are not pinned.

## Topology

- API: FastAPI + SQLAlchemy (psycopg2, explicit `postgresql+psycopg2://`) on
  Render free tier — 512 MB, throttled CPU, sleeps after ~15 min idle.
- Web: React/Vite on Vercel. `/api/:path*` rewrites to Render; **120 s ceiling**
  on that rewrite. No request may approach it.
- DB: Supabase Postgres (Tokyo). UptimeRobot pings to limit cold starts.
- Render CPU is far slower than local: one SARIMAX fit ≈ 0.3 s local, 12–20 s
  deployed. Budget from the deployed number, never the local one.

## Conventions that bite

- **Calendar dates go through `business_today()` (Asia/Manila).** Never
  `date.today()`, `datetime.utcnow()`, or SQL `CURRENT_DATE`. Compute the date
  once per request in Python and pass it into SQL as a parameter so a long
  request can't straddle the boundary. Instants stay UTC (`now()`,
  `datetime.now(timezone.utc)`, all `*_at` columns). `tzdata` is pinned and the
  zone resolves at import — a missing zone fails the deploy on purpose.
- **Forecast requests are bounded.** Max 2 SARIMAX fits per request, per-type
  cache commits, non-blocking advisory lock per facility. A second concurrent
  request returns cached results plus `pending_types`; it must not refit.
- **Order selection runs offline**, never in a request: `server/select_orders.py`
  is the primary path. Grid is 72 candidates (p,q ∈ {0,1,2}, d ∈ {0,1},
  seasonal P,Q ∈ {0,1}, D=0, s=7), selected on the training window only
  (series minus last 30 days), lowest AIC among candidates passing df-corrected
  Ljung-Box at lags 14/21/28 with K = p+q+P+Q. Stored per (facility, blood type)
  in `facility_sarimax_order`.
- **Which order a fit uses** (`_resolve_order`), in precedence: (1) the grid-searched
  row; (2) an AI recommendation, a depth-4 decision tree over 13 features of the
  type's own daily series, walked from `server/models/sarimax_selector_rules.json`
  (no scikit-learn), computed at fit time and not stored, and refused when it has no
  seasonal term and a weekday ANOVA on the series' daily changes is significant at 0.05; (3) the fixed default
  `(0,1,4)x(1,0,1,7)` when the AI can't produce one (rules missing or invalid,
  non-finite feature, any error). The label names the mechanism, in
  `facility_forecast_cache.model_order` and `/forecast`'s `sarimax_orders`:
  ` [grid-searched]`, ` [ai-recommended]`, ` [default-order]` (legacy rows: no
  suffix = grid-searched, ` [fallback]` = default-order). Labels never claim
  quality: the tree was measured not to beat the default on synthetic data
  (`deploy_recommended=false` in the JSON), so no better/optimized/improved
  wording anywhere. `sarimax_fallback_types` is unrelated: SARIMAX failed and the
  linear trend was used. `sarimax_selector_common.py` must stay importable
  without joblib/scikit-learn (`tests/test_sarimax_selector_import.py`).
- **Snapshots, and the permanent October 1-25 hole.** `inventory_snapshots` is written when a blood bank loads
  its forecast, on historical uploads, and nightly by `POST /internal/daily-snapshot` (23:30 Manila, every active
  facility including hospitals, an explicit 0 for an empty type, guarded by `CRON_SECRET`; scheduled on cron-job.org (see
  DEPLOYMENT.md); first successful run 2026-10-27 23:30:18 Manila; it fits nothing). October 1-25, 2026 has no rows
  for any facility and cannot be recovered: the job did not exist before 2026-10-27 and upload 605's undo on
  2026-09-30 removed what Northside had. `_resample_daily_series` carries the last value
  forward over an unobserved day. NaN instead was measured and rejected (O+ fails worse under it, and non-finite
  features would disable the AI selector). Count observed days with `_count_observed_days`, never
  `len(daily_series)`; the API's `days_of_history` is observed days.
- **The soundness guard** (`_sarimax_fit_is_sane`) rejects a fit whose AIC per observation exceeds its stored
  selection baseline (`facility_sarimax_order.selection_aic_per_obs`) by more than 0.05; a type whose row has no
  baseline is unguarded. A rejected fit is retried once (Nelder-Mead, then L-BFGS), then the type is served the
  linear trend. **Consequence as of 2026-10-29: four Northside types (A-, A+, AB+, O+) are on the linear trend**,
  because the October 1-25 carry-forward turns 26 days of real change into a one-day step on the 26th and their
  full-series fits degrade (that step, relative to a type's usual 26-day variability, has rank correlation 0.93 with
  the degradation). A replay of 2,213 fits found no clean alternative gate, so the guard is unchanged and the
  limitation is recorded in the selector JSON: do not widen 0.05 or re-baseline without new evidence.
- **Re-running order selection is not free.** `select_orders.py` / `_run_order_selection_for_facility` overwrites each
  type's `facility_sarimax_order` row and gives every type a baseline. On 2026-10-29 it sent B- to the linear trend,
  skipped AB- and O+ (the hold-out warm refit reports not_converged), and was undone from a dump. There is no audit
  table: dump the facility's rows first.
- **Scripts that write to production refuse to run by default.** `seed_facility_inventory.py`,
  `generate_synthetic_forecast_data.py` and `fit_and_cache_synthetic_forecast.py` need
  `--i-know-this-writes-to-production` (`server/script_guard.py`). Other scripts, `select_orders.py` among them,
  have no such guard.
- **Uploads are batched** — `INSERT … SELECT … FROM unnest(...)`, never
  row-by-row. Three CSV parsers require exact column names, filter extras, and
  reject on a missing required column.
- **A request names its component** (`COMPONENTS` in `server/main.py`: Packed RBC, Platelets; required, never
  defaulted, no DB CHECK). `confirm_release` and `/facilities/nearby` match it exactly, so search and release agree.
  A unit expiring on the release day is never released (`RELEASE_EXPIRY_FLOOR_DAYS`); `MIN_DAYS_TO_EXPIRY_AT_RELEASE`
  is per-component clinical configuration, unconfirmed by clinical staff, applied before FEFO. `requests.component` is
  NULL on the 12 requests that predate it (meaning not recorded, not releasable). Compatible alternatives are
  red-cell compatibility, offered only for `RED_CELL_COMPONENTS`. Adding a component: one line in `COMPONENTS`, one in
  `src/app/lib/bloodComponents.ts`.
- **The transfer ledger** (`request_units`): `confirm_release` writes one row per reserved unit, copied from the unit at
  release (DIN, type, component, volume, dates, supplier shelf, days left), and `confirm_receipt` settles each as
  `received` or `failed` with days left at receipt (from the live unit's expiry, the value the decision used; the row's
  own `expires_date` stays the release snapshot, so a mid-transit re-upload shows as a difference), in the same transactions. A CSV re-upload rewrites the live unit and
  cannot rewrite the row. `blood_unit_id` is `ON DELETE SET NULL` because `DELETE /inventory/{din}` hard-deletes; the DIN
  stays. `request_unit_failures` is still written alongside, and a test asserts the two agree. Requests released before
  the table existed have no rows and none are invented. A test cleanup that deletes requests must delete `request_units`
  rows first.
- **Every request transition stamps a time and an actor** in its own transaction: `accepted_at/by`, `declined_at/by`,
  `cancelled_at/by`, and `supplier_confirmed_at` + `released_by`, `requester_confirmed_at` + `received_by` (those two
  timestamps keep their names). The actor is `get_acting_user_id`, one account per facility, so it names a login, not a
  person, and is NULL under the dev override. Nothing is backfilled: NULL means "not recorded", never "did not happen".
  The five transition functions take `user_id`, so tests call them with `user_id=None` (a bare call would pass the
  `Depends` object into SQL, as for `uploaded_by`).
- **A request's timeline is derived, not stored** (`_request_timeline`, served by `GET /requests/{id}`, which the chat
  re-fetches with its messages every 4 s while the browser tab is visible; the Live label is the last poll's result). Both sides' request lists poll every 25 s while the browser tab is visible (silent, and at once on becoming visible); the
  chat shows a day separator above each run of bubbles once the thread spans more than one day. An
  event is listed whenever status and timestamps show it happened; a time or account never captured is None and reads
  "not recorded", never omitted. An event's account is the account's email (not an id), shown to both facilities. The
  receipt (`/receipt/{id}`, `screens/Receipt.tsx`, printable via `print:` classes) renders the same `timeline` and
  `units`, the same page for both facilities, states how many chat messages exist without reproducing them, and always
  says what BloodLink does not record (condition, temperature, transport, signatures). Release and receipt events name the units from `request_units`, falling back to `request_unit_failures`
  for a request released before the ledger existed.
- **The transfer history** (`GET /transfers`, the third Requests tab): a transfer is an accepted request, so finished ones
  (completed, failed) by default and accepted ones with `include_active`; pending never; declined and cancelled only when
  chosen as the outcome (dated by when requested, no units, so received is null, not 0). Filtering
  and totals are server-side, the date range is Manila business dates applied as instants, and each row is the
  `request` block and `summary` of `GET /requests/{id}` (`_request_block`, `_received_counts`), so history and receipt
  cannot disagree. Dates are written by `src/app/lib/dateFormat.ts` ("30 Oct 2026, 20:27"), never by `toLocale*`, so
  the table, the receipt and the chat timeline and bubbles agree (other screens still use `toLocale*`). Totals cover every match; `transfers` is capped at 500 with `truncated`.
- **Undo** reads `inventory_snapshot_write_log` to restore displaced counts, and
  clears `facility_forecast_cache` for the facility.

## Working expectations

- Run the full suite before reporting done, with `TEST_DATABASE_URL` set to a
  local scratch Postgres: `python -m pytest server/tests -q` (452 tests plus
  134 subtests; use `server/.venv`, system Python lacks pytest). Never run
  tests via `python -m unittest` — that skips conftest and hits `.env`'s
  production `DATABASE_URL`, despite what older test docstrings say. `server/tests/conftest.py`
  refuses to run without it or against a non-local host, overrides
  `DATABASE_URL`, and rebuilds the public schema from `server/tests/schema.sql`
  (a `pg_dump --schema-only` of production; regenerate after a migration).
  Never run the suite against production.
  Frontend checks: `node scripts/check_stock_bands.ts` (stock band rules) and
  `node scripts/check_forecast_source_tags.ts` (per-type forecast source tags) and
  `node scripts/check_trend_chart.ts` (trend chart history/forecast data) and
  `node scripts/check_incoming_groups.ts` (the blood bank's incoming-request sections) and
  `node scripts/check_components.ts` (request components match the server's) and
  `node scripts/check_request_timeline.ts` (status-event wording and the chat thread) and
  `node scripts/check_transfers.ts` (transfer-history filters, date presets and wording), each printing `ok`, plus
  `npm run typecheck`. The frontend has no component test runner.
  A red suite is a finding, not a footnote — "known failure" is how a real
  bug hides.
- **Don't report a measurement you didn't take.** Say UNKNOWN. Check that the
  deployed code actually contains the feature before measuring it there.
- **Check what you fed a statistical test** before believing its result. Use
  `standardized_forecasts_error` with the burn-in, not raw `resid`. Ljung-Box
  needs one-step residuals and n ≫ lag.
- Flag a claim that goes beyond the data, including your own.
- Prefer one shared helper over copies in three call sites; the CSV parsers are
  the cautionary example.

## Methodology docs

`docs/methodology/PHASE_1..4_*.md` are generated by `run_methodology.py` from
`docs/methodology/northside_420d_history.csv` — **generated demonstration data,
not real records**, and never to be described as empirical evidence about blood
supply. SELECTION FIT (training window) and PRODUCTION FIT (full series) are
reported separately and must never be conflated. `docs/DATA_DICTIONARY.md` is
generated by `build_data_dictionary.py` and errors until a new table has a
description.
