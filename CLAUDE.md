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
- **A request's cost is its round trips, not its rows.** API to Supabase is about 130 ms a round trip (measured 2026-11-09 from
  `/health` against a page with no database call, and reproduced through a delaying proxy: endpoints cost 130 ms times the
  statements they send). Count statements and connections, batch per-type or per-component reads into one statement, and don't open
  a second connection where one will do. Two engines (`database.py`):
  - **`read_engine`, for endpoints that never write**: autocommit (no `BEGIN`/`COMMIT`), no pre-ping, every connection set
    `default_transaction_read_only` so a stray write fails at the database instead of committing by itself, and the function wrapped
    in `@retry_read` (a dead pooled connection, e.g. after Render's sleep, discards the pool and runs the read once more). A read
    costs **one round trip** per statement. A new read-only endpoint goes here; `tests/test_read_engine.py` scans the listed
    functions for writes and for the normal engine, so add yours to `READ_ONLY_FUNCTIONS`. Needs the pooler in **session mode**
    (port 5432): the read-only setting is per session. Autocommit is exactly what READ COMMITTED (the production default) already
    gave each statement; if a read ever needs one snapshot across statements it does not belong on this engine.
  - **`engine`, for anything that writes or needs a transaction**: pre-ping (`SELECT 1`) on every checkout, plus `BEGIN` and
    `COMMIT`/`ROLLBACK`: **three round trips before your first query**. The pre-ping stays here because a write cannot be retried
    after a dead-connection error, and checking only after a connection has been idle would let a connection that died a few seconds
    ago fail a user's write.
  `GET /forecast` writes (today's snapshot, the alert state), so it is on `engine`, but its warm path is one connection and one
  transaction (snapshot write, history, cache and alert state together; a second connection only when an alert must be written),
  and a fit always happens after that transaction has committed. `GET /notifications/unread-count` is a pure read: it leaves the rows
  `_STALE_NOTIFICATION_SQL` calls resolved out of the count instead of updating them (the sweep that marks them read runs at the event
  and when the list is opened). A response's `Server-Timing` header (`app`, `db`, durations only; `SERVER_TIMING=false` turns it
  off) separates server time from network time in DevTools.
- **Indexes added 2026-11-09 by `server/migrate_add_facility_indexes.sql`** (run by hand in the Supabase SQL editor with `CREATE INDEX
  CONCURRENTLY`, one at a time; checked valid in production by a read-only catalog query on 2026-11-10; all five are in
  `server/tests/schema.sql`): `idx_blood_units_facility_type_expiry` (facility_id, blood_type, expires_date: every facility-scoped
  stock read), `idx_blood_units_reserved_for_request` (reserved_for_request_id, partial `WHERE ... IS NOT NULL`: `confirm_receipt`'s
  lookup and the foreign key), `idx_requests_requester_created` (requesting_facility_id, created_at), `idx_requests_supplier_created`
  (supplying_facility_id, created_at), `idx_inventory_snapshots_facility_date` (facility_id, snapshot_date: the forecast's history
  read; the UNIQUE (snapshot_date, blood_type, facility_id) stays, it enforces one row per day). They are plain additive indexes:
  dropping one changes nothing but speed. At today's table sizes the planner may still seq-scan `requests` (16 rows) and the
  whole-facility `blood_units` reads; whether each is chosen was an expectation in the migration file, **not measured after
  creation**.
- **Fonts are self-hosted** (`src/styles/fonts.css`, files in `src/assets/fonts/`): IBM Plex Sans 400/500/600/700, Archivo 700/800,
  JetBrains Mono 400/500/600 and IBM Plex Serif 700, Latin subset only (a non-Latin script needs its subset files added), woff2 from the
  Fontsource packages (5.3.0), so no request goes to Google Fonts. All four families are SIL OFL 1.1: the `OFL-*.txt` files beside the
  fonts must stay with them. `index.html` preloads four (Plex Sans 400 and 600, Archivo 700, and Plex Serif 700 only at
  `min-width: 1024px`); adding a family means a `@font-face`, the file, its licence text, and a preload only if it is used above the fold.
- **Clay look** (the last block of `src/styles/theme.css`; its visual rules are in DESIGN.md, "The Clay Look"; nothing in the repo asserts it, a throwaway
  headless-Chrome probe checked it). **`<html data-surface="clay">` is the switch**, and **it is currently ON in `index.html`** (Euhan's commit 34e299b,
  "Enable clay look by default"); remove the attribute and the app is its default look. With the attribute absent no rule in the block matches: the compiled CSS
  is identical to the build without the block (proved by stripping the block and diffing) and 34 screens (fifteen at 1280 and 390 px in light, four in dark)
  render byte-identically to HEAD with the attribute removed (a few dark and chart-heavy screens render a rare stray variant in HEAD as much as in the new build;
  the probe compares the most common render; cause unknown). To try it on a build where it is absent run `document.documentElement.dataset.surface = "clay"`
  in DevTools (again after each reload). **`data-clay-strength="strong"`** on `<html>` selects the strong setting; absent is medium.
  **Surfaces, controls and the stock gauges only**: status and alert colours, the gauges' arc and track colours, chart colours, layout, spacing and copy are
  untouched, and the hospital's Action required keeps its fill, text and outer drop (it gets the radius and a light inner edge). **Why selectors and not a
  token**: the clay rules are unlayered because Tailwind utilities beat `@layer base`, and the radii cannot be a token (`rounded-sm/md/lg` read `--radius`, which
  would reshape the blood-type badge and every chip; `rounded-xl` compiles to a literal `1.25rem`). **Select by exact class token (`.bg-primary-tint`),
  never `[class*="bg-primary-tint"]`**: the substring also matches `hover:bg-primary-tint` and gave text buttons a chip look once.
  **One recipe per kind, applied to every instance** (8 kinds, listed in a comment at the top of the block and in DESIGN.md): primary button
  (`button.bg-primary.text-white`: maroon bank, navy hospital, selected chips), secondary / outline / danger / chip button (`button` with `.border-border`,
  `.bg-card`, `.bg-secondary`, `.border-status-critical-border` or `.bg-primary-tint` outside `header`), pill (`span.rounded-full` with horizontal padding, no
  gradient so a status colour is exact), field, card and nested tile, floating surface (dialog, popover, tooltip), status-tinted panel, and the flat
  controls left as they are (icon buttons, navigation, text links). **No per-file class constants were replaced: the whole look is selectors in `theme.css`**
  (one file of code touched). **One light, from the upper left, and the strength and the sizes are one block**, section 1 of the clay block. A raised thing (card, big container, tile, button, chip, pill, modal, popover, tooltip) has a warm outer drop to the bottom right, next to nothing on its top and left, a white highlight inside its top-left edge and a warm shade inside its bottom-right edge; a sunken thing (field, `-pressed`, the gauge groove) is the reverse. **No spread anywhere, and no raised drop with a zero or negative offset.** `--clay-lx` and `--clay-ly` (+1) are the direction and multiply every offset. A size is a depth `--clay-d-{sm,md,lg,float,row,pill}` (2, 3, 3.8, 5, 2, 1 px); the drop is d across, d * `--clay-ky` (1.25) down, blurred d * `--clay-kb` (1.8); the inner light is d, d, blurred d * `--clay-kib` (2); the inner shade is d * `--clay-klo` (1.25) the other way. Opacities `--clay-hi-a`, `--clay-lo-a`, `--clay-drop-a`, `--clay-grad`, `--clay-acc-hi-a`, `--clay-acc-drop-a`, each written for light and dark, medium and strong (strong: depths x1.2, higher opacities, the same directions); warm colours `--clay-hi-rgb`, `--clay-shade-rgb`, `--clay-drop-rgb`. Tokens, all generated from those: `--shadow-clay-sm` (buttons, chips, tiles, tooltips, unselected blood-type badges), `-md` (cards), `-lg` (every raised surface, `shadow-[var(--shadow-raised)]`, and the 600 px panels), `-float` (dialogs, popovers: **a `[role="dialog"]` must be excluded from the card rule or it gets `md`, which happened**), `-field` (recessed), `-row` and `-pill` (inner shading only), `-pressed`, `-status` (Action required: the `lg` drop and light over its own fill) and the `-accent` set for solid primary buttons. The gauge **tiles are raised** (`sm`), not recessed: an earlier version had them as wells and they read as inverted. **Measured extents** (visible = a 3-level colour change, on the page background, light, medium; px below / right / above / left): `lg` 10 / 9 / 0.5 / 2 (the target is about 10 or less below and right and at most 2 above and left), `md` 8.5 / 7, `sm` 5.5 / 5, `float` 14.5 / 14 / 2 / 4; strong `lg` 13.5 / 11.5 / 2 / 3; the round-four `lg` (a 20 px blur with a -9 px spread) was 15 / 11 / 0 / 0. Each token is one warm outer drop plus an inner light and an inner shade, so **at most three layers on any element** (the probe asserts it, and a direction audit of every computed `box-shadow` on 14 screens found 0 flags). Things to keep true when editing it:
  (1) **A card, tile, dialog, popover or tooltip has no visible outline**: its `border-color` is transparent and the width stays, so nothing moves. (2) **A small
  control in a list or table row (`h-6`, `h-7` or `h-8` in an `li`, `td`, `th`) gets inner shading only and no outer drop**, and a field there gets radius and fill
  only; the same control outside a row has the full recipe. (3) **An unfilled control's edge is `--clay-boundary` (#857B70 light, #857868 dark), 3:1 or better
  against what is behind it** (measured on every field, outline button and danger button on twelve screens: lowest 3.40 light, 3.78 dark); a filled button needs
  none. A danger button's own border token is 1.6:1 on a dark card, so in dark clay its edge uses `--status-critical`, the base tone of the same family. Dividers
  inside a card use the soft `--border` (#D2C9BC light). (4) **A field's clay shadow and boundary colour are off while it has focus** (`:not(:focus)`), so Tailwind's
  `focus:ring-*` and `focus:border-primary` show. **Keyboard focus rings are the off look's** (a 2px accent outline with a 2px offset on buttons and chips; a field has no outline, its indicator is the accent border and a 30% box-shadow ring), **except a hospital's dark ring**, which is `#5B82B6` (4.3:1 on a dark card; the dark navy accent is 2.7:1 there, already so with the switch off). **Measure a ring only after its 150 ms `transition-colors` has finished**: read at once, it is the accent at 50% and looks like 2.6:1 (this misled the probe once). (5) **The light colours (`--card` #F8F3EA, `--background` #EEE8DD, `--clay-field` #E9E2D6, `--border`,
  `--clay-boundary`) live under `:root:not(.dark)[data-surface="clay"]`**: at equal specificity they would out-rank the plain `.dark` block and turn dark cards
  cream (this happened once). The dark card and page are left alone (a lighter card lowers dark contrast figures that are already low). The trend chart's grid
  and cursor read `--border`, so `.recharts-wrapper` gets the off-state value back and chart strokes do not move. (6) **The whole block sits in three nested media
  queries, `not (prefers-contrast: more)`, `not (forced-colors: active)` and `not print`**: those users, and a printed page, get exactly the off look (checked: every
  element's shadow, radius, fill, gradient and border equals off in each, on four screens); never merge them into `not (A or B)`: the minifier turns that into invalid
  CSS and the block silently never applies. (7) **Pressed** (shadow inward, no transform, no layout shift) is inside `(prefers-reduced-motion: no-preference)`; a
  disabled button is left out of the shadow and gradient rules with `:not(:disabled)`. (8) Placeholders in clay fields use `--muted-foreground`.
  **Contrast, light and dark (token values, cross-checked in Chrome)**: text on a card 15.8 / 14.5, muted text on a card 5.3 / 5.4, on the page 4.8 / 5.9, on a field
  4.5 / 5.8; no text element (750 scanned on twelve screens, both themes) falls from AA to below it. **The surface-versus-page contrast and the border contrast are lower
  than the off look's by design** (card against page 1.10:1 against 1.05 with the default white, but no outline; a control's boundary 3.4:1 instead of 7.4:1).
  **Colours that move**: `--card` is a token, so every `bg-card` element (the unselected blood-type badge, the header, the buttons) is #F8F3EA instead of white, the
  page is #EEE8DD, the gauge's tick halo (`stroke="var(--card)"`) follows the card; fields are #E9E2D6. In dark theme only field fills move.
  **The stock gauge** (`components/StockDial.tsx`, both Dashboards; unchanged in this round) is molded as a clay tube and groove with extra thin strokes, never a blur
  or filter: per gauge four hidden groups (`g.dial-clay.dial-clay-{groove,lip,hi,lo}`, `display="none"` as a presentation attribute that only the clay rule
  `.dial-clay { display: inline }` overrides, so off, print, `prefers-contrast: more` and `forced-colors` are exactly what they were; `check_accessibility.ts` asserts
  it), each a narrower copy of the stroke it decorates shifted `CLAY_OFFSET` toward or away from the light, stroked with `color-mix(in srgb, white|black N%,
  currentColor)` (`--clay-dial-*`), so a highlight or shade is always its own status colour and everything stays inside the original band. Arc body against the track
  3.64 / 3.01 / 3.56 (teal, amber, red-orange) in light and 4.96 / 5.66 / 3.66 in dark, unchanged; against the card 4.95 / 4.10 / 4.84 in light (5.47 / 4.53 / 5.35
  against white) and unchanged in dark; the decorative strokes themselves are under 3:1 against the track, the arc's outer edge is always the body colour; the
  track against the card is 1.36:1 (1.50 against white; 1.54 dark). **Measured** (headless Chrome, local, 4x CPU throttle, the bank Dashboard against a mock API; not a device, and raster work may run off the traced thread): paint+raster, medium, median of 11: 15.3 ms off against 16.5 on in light (+1.2 ms, 8%; ranges 14-18 and 16-18) and 15.5 against 17.0 in dark (+1.5 ms, 10%); strong, median of 7: +1.5 and +1.8 ms; the layer phase was 10-14% higher in every comparison and style recalculation 2-6% (+3 to +8 ms of about 135). A small real cost is likely, at the edge of the run-to-run spread (the same build drifts by about 30% between sessions; round four measured +2.5 and +1.8 ms on a slower session); a phone's GPU was not measured. **Looked at with it on** (screenshots at 1280 and 390 px, light and dark): the bank
  Dashboard, Inventory, Source supply, Requests, Admin (with one pending registration), Register, sign-in, the hospital Dashboard and Source supply, the Add unit and
  Change password dialogs and the account menu. **Not looked at**: the Donors screen (donor-blast code, untouched) picks up the look through the shared selectors; the
  Receipt page, Reset password and Complete profile were not re-photographed this round; the request thread (a request open in the chat) was not mocked.

## Conventions that bite

- **Calendar dates go through `business_today()` (Asia/Manila).** Never
  `date.today()`, `datetime.utcnow()`, or SQL `CURRENT_DATE`. Compute the date
  once per request in Python and pass it into SQL as a parameter so a long
  request can't straddle the boundary. Instants stay UTC (`now()`,
  `datetime.now(timezone.utc)`, all `*_at` columns). `tzdata` is pinned and the
  zone resolves at import — a missing zone fails the deploy on purpose.
- **Forecast requests are bounded.** Max 2 SARIMAX fits per request, per-type
  cache commits, non-blocking advisory lock per facility. A second concurrent
  request returns cached results plus `pending_types`; it must not refit. **The cache key is the Manila business date**: a row
  is a hit only when `trained_through_date == business_today()`, so every bank's first dashboard load after midnight Manila is cold
  (12-20 s per type deployed, two types per call, the dashboard asking again until nothing is pending), and so is the first load
  after an upload, an undo or an order selection clears it. A type whose fit is rejected (the soundness guard) is never cached;
  only the in-process `_SARIMAX_FIT_FAILED` set stops it being refitted, so a restart or deploy retries it once. With
  `WARM_FORECASTS_AFTER_SNAPSHOT=true` (default false) the nightly job schedules `_warm_all_forecasts` after its response: it calls
  `get_forecast` itself for each active bank with 30+ recorded days until nothing is pending (so the lock, the 2-fit cap, the snapshot
  write, the weekday-guard log lines and the shortage notifications are the endpoint's own: a shortage a user load would have
  announced is announced at the warm-up instead, once). **It only helps a load on the date it runs**, so the 23:30 Manila run
  warms a day that is about to end; call the job again after midnight (see DEPLOYMENT.md). It runs no order selection.
- **Order selection runs offline**, never in a request: `server/select_orders.py`
  is the primary path. Grid is 72 candidates (p,q ∈ {0,1,2}, d ∈ {0,1},
  seasonal P,Q ∈ {0,1}, D=0, s=7), selected on the training window only
  (series minus last 30 days), lowest AIC among candidates passing df-corrected
  Ljung-Box at lags 14/21/28 with K = p+q+P+Q. Stored per (facility, blood type)
  in `facility_sarimax_order`. **Nothing selects orders after an upload by default.** The historical upload used to schedule
  `_run_order_selection_for_facility` (2 types, 72 fits each) as a background task in the one web process; that is now gated by
  `AUTO_ORDER_SELECTION_AFTER_UPLOAD` (default false; when false the upload logs one `[order-selection] skipped` line). Turn it on
  only knowingly: it upserts over existing `facility_sarimax_order` rows (with a new soundness baseline), deletes the type's
  forecast cache, takes the instance's CPU for what is estimated at tens of minutes (one fit is 12-20 s deployed; the 72-fit run was
  never timed there), and has no guard for any facility (not 158, which has 168 days of history and no stored order today). The
  inventory and historical uploads are plain `def`s so their blocking work runs in the threadpool, not on the event loop; the donor
  upload is still `async def`.
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
  DEPLOYMENT.md); first successful run 2026-10-27 23:30:18 Manila; it fits nothing; since the archive change it also archives
  every active facility's expired units, `expires_date < today` and not reserved for a request, through `_archive_expired_units`,
  the helper behind the manual Clear expired action, in a second transaction after the snapshot has committed, and reports counts
  per facility: `archived_expired` and `expired_still_reserved`. It changes no stock figure). October 1-25, 2026 has no rows
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
  `generate_synthetic_forecast_data.py`, `fit_and_cache_synthetic_forecast.py` and the archived
  `archive/migrations/resolve_existing_notifications.py` need `--i-know-this-writes-to-production`
  (`server/script_guard.py`). Other scripts, `select_orders.py` and the `migrate_*.py` migrations among them,
  have no such guard.
- **Who is asking, and how often.** Never read `X-Forwarded-For` by position from the left: Render keeps what a caller sends and appends
  its own hops, and `True-Client-IP` is the one value the caller cannot choose (`client_ip.py` explains the sources and its limits;
  behind Vercel it finds the visitor as the entry before that peer, and an address that is not a plain public IP is "unknown", never a
  shared key). Sign-in and the email-sending endpoints (forgot-password, OTP, registration) count under **two keys, the account
  (`acct:<email>`, which cannot be spoofed) and the sender (`ip:<address>`)**, and block when either is full; login is 5 *failed* tries per
  15 minutes, and a success clears the **account's** counter only, never the address's (so someone who knows one valid login cannot reset
  their address counter by signing in between guesses); the error says how long to wait. **`POST /auth/update-password`** has its own
  limiter of the same design (`_PASSWORD_CHANGE_LIMITER`, key `acct:user:<id>` plus `ip:<address>`): 5 failed current-password checks per
  15 minutes, a success clears the user's counter only, and the 429 does not say which key was full; **a wrong current password is a 400
  ("current password is incorrect"), never a 401**, because the frontend's `apiPost` treats every 401 as an expired session and signs the user out; it is separate from the login
  counters, so a typo in the account menu cannot lock someone out of signing in. In process memory, so a restart clears it. **Every
  authenticated request checks that the user still exists and its facility is active** (`_assert_account_active`; a pass is remembered
  30 s, never more than 60; a rejection is never remembered; the admin deactivation clears it), so deactivating a facility ends its
  sessions. `GET /facilities` needs a login. `/health` is `{"status":"ok"}` or a bare 503 (the error goes to the log), and `/docs`,
  `/redoc`, `/openapi.json` are off unless `ENABLE_API_DOCS`. The inventory and historical uploads refuse a body over
  `MAX_UPLOAD_BYTES` (default 5 MB, 413, by `Content-Length` and while reading; `upload_limits.py`) and a file that is not plain text; the
  donor upload is outside that. `GET /facilities/geocode` is public (registration) but limited: 10 lookups a minute per address, at least
  5 characters, a 5-minute cache, one call a second to Nominatim for the whole process, and an identifying User-Agent.
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
- **One definition of a releasable unit** (`RELEASABLE_UNITS_SQL`, `_releasable_counts`): of the requested component, with
  enough time before expiry (`_release_expiry_cutoff`), and not reserved for another request. `confirm_release` selects from it,
  `/facilities/nearby` counts it, and any "can give" figure must use it, so no screen promises more than release delivers.
  Compatible alternatives are offered to hospitals only: a blood bank is restocking, and covering A- with O- is a patient-side
  substitution. A bank's tab is "Source supply" and its request is always restock.
- **One definition each** of what the stock screens count and what a bank can offer. `USABLE_COUNTS_SQL` is the count of usable units
  (`expires_date >= today`, archived or not) that `/inventory/summary`, the hospital's threshold view and the snapshot writer all join,
  so they cannot disagree. `OFFERABLE_UNITS_SQL` (not archived, not reserved for a request) is what `/inventory/expiring`, the grouped
  notify endpoint and the per-unit notify endpoint all select from, so a bank cannot offer blood already committed to a transfer. The
  7-day near-expiry window is `NOTIFY_EXPIRY_NEAR_DAYS` on the server and one frontend constant, `EXPIRY_NEAR_DAYS`
  (`check_bank_inventory.ts` fails if they differ).
- **The blood bank Dashboard** (`BankDashboard.tsx`, logic in `lib/bankDashboard.ts`): one sentence in the worst state's colour,
  one per-type table (stock against minimum, units, status, **can give**, outlook), "Waiting for you", expiring units grouped by
  type, component and day (`GET /inventory/expiring`, one `POST /inventory/notify-nearby-hospitals` per group), and everything
  that is not a daily decision behind "Forecast details" (trend chart, history upload and undo). `/inventory/summary` rows carry
  `can_give` per component from `_releasable_counts`, the same predicate as release and search; it is {} for a hospital.
  Thresholds are per blood type, not per component, so a component's figure subtracts the type's one minimum: a known
  limitation while platelets are rare (one unit in production). The outlook is an estimate in its own column, under one plain sentence
  saying what it is (a 30-day projection from how the stock has moved, blind to donation drives and emergencies). **The row's
  basis tag is plain words, two states**: "From its own history" (any SARIMAX fit) or "Simple trend line" (the linear trend),
  plus "Not from your records yet" for the stand-in; no model names, settings or "optimized"-style claims there. The technical
  layer (SARIMAX, grid-searched / AI-recommended / default order, the full order label) is `technicalBasisFor` in
  **Forecast details**, per blood type, worded as the API names it. A hospital has no forecast and no such tag. And "Order 90 by 4 Nov" opens Source supply with type, quantity and Packed RBC
  prefilled. `/inventory/expiring` writes no notification: expiring units are not one (see **Notifications**).
- **"At risk"** is a third Dashboard outlook, between On track and an order: the central projection holds at or above the minimum at
  every checkpoint, but the lower bound of the 95% interval is below it at one (`_compute_downside_risk`; `/forecast` returns
  `downside_risks`, and per-type `type_bounds` with lower/upper). Display only: it never makes a recommendation, never changes
  `recommended_units`, never feeds an alert; those stay on the central projection. It starts at checkpoint 1 (today's stock is
  known), dates the first checkpoint whose lower bound breaches (not interpolated), and needs a bound: a type with no interval
  can never be At risk. The synthetic stand-in has no interval and no `downside_risks` key at all, and is gated to "Not enough
  history" (below) rather than read On track. **Below `SARIMAX_MIN_DAYS_REQUIRED` (30) recorded days of the type's own history (today counted) a type never reads On track or At
  risk**: `/forecast` lists it in `insufficient_history` and the column says "Not enough history (6 of 30 days)", the stand-in
  included. Asserting safety without a basis is worse than asserting risk without one. Recommendations are **not** withheld, but
  each one from a type below 30 days carries `limited_history` (with the day count) and the row says "Limited history (6 of 30
  days)"; so below 30 days the outlook is a limited-history recommendation or "Not enough history", never On track. At 30 days or
  more neither marking appears. Measured 2026-10-31 (read-only, from the cache and stored snapshots): 0 of 8 types at Northside
  (430 days) read At risk; on the four seeded banks with 6-8 snapshot days it would have fired on every type not already ordering
  (22 of 22), because that interval is wider than the stock itself, which is why the gate exists. With `MIN_DAYS_REQUIRED = 3` the
  system still recommends an order on three days of history, and the soundness guard can flip a type's method overnight: both are
  recorded in the selector JSON's `limitations` (and in `train_sarimax_selector.py`, which generates it: edit both).
- **Notifications** exist because something needs the facility or something changed in a transfer it is part of; when that stops being
  true the row **resolves itself**: the system marks it `read_at`, never deletes it. Types written today: `incoming_request`
  (resolves when the request is accepted, declined or cancelled), `request_accepted`, `request_declined`, `request_cancelled`,
  `receipt_needed` (the requester's prompt after release; resolves when receipt is confirmed), `transfer_completed`,
  `transfer_units_failed`, `forecast_shortage`, and the two actions' own `expiring_unit_alert` (a bank offering near-expiry units
  to hospitals; it resolves once the units it names have expired, a date the sweep reads from the offer's own text and creation
  day: `tests/test_notifications.py` builds it through both notify endpoints so the two cannot drift) and `blast_complete`. A row
  linked to a request that no longer exists resolves too. The old shared `transfer_confirmation_needed` is split (the supplier's
  "confirm release" notice at accept is dropped, the receipt prompt is `receipt_needed`) and `unit_expiry` is retired: nothing
  writes either and nothing resolves them (the old rows are all read; the code that resolved them was removed). Resolution happens at
  the event (`_resolve_notifications`) and again from the request's state on every `GET /notifications` and `/notifications/unread-count`
  (`_resolve_stale_notifications`, every row, no date limit). The one-off that cleaned up the rows written under the old rules,
  `archive/migrations/resolve_existing_notifications.py`, has run (392 rows resolved, 14 events left unread; the undo record is the
  JSON it saved in the git-ignored `server/tmp/`) and is archived as the record of how. `blood_units.last_notified_expiry_status` is
  unused (the data dictionary says so); its old values remain. Not built yet: a release reminder for the supplier (`docs/BACKLOG.md`). `forecast_shortage` is written only for a type with at least `SARIMAX_MIN_DAYS_REQUIRED` recorded days
  and never from the synthetic stand-in, in the Dashboard's sentence ("O+: projected below its minimum around 4 Nov; ordering 90
  units brings it back to its maximum."). Every request notification names its quantity and component, dates are written out
  ("28 Oct 2026"), and the text says "request", "kilometers" and "near-expiry". The bell (`NotificationBell`, logic in
  `lib/notifications.ts`, asserted by `check_notifications.ts`) shows the **server's count of every unread row** (exact to 99, then
  "99+"; the list's header gives the exact number), takes that count (and the newest id, to tell an arrival from a count that merely
  changed) from the one shared poller's `GET /sync` (see **Live sync**), which polls **only while the browser tab is visible** and at
  once when it becomes visible; it has no timer of its own. It loads the list when opened, 50 a page, and pages older rows with
  `GET /notifications?before_id=`. `GET /notifications/unread-count` still exists and is tested, but nothing in the app calls it.
  Expiring units are on the Dashboard, not in the list.
- **The blood bank Inventory** (`BankInventory.tsx`, logic in `lib/bankInventory.ts`): the expired backlog is the headline (one
  button archives exactly the units the server will archive, never the expired ones still reserved for a request, after a
  confirmation), then a search across every type (DIN prefix or substring, case and spaces ignored), then a table of the eight
  types that each open to their units, 50 a page, in release order (live units soonest expiry first, expired last). `GET
  /inventory` still returns every non-archived unit with no paging or filters, and each row now carries `reserved_for_request_id`. A unit's Note says "Reserved for request N" or "Not
  released: inside the margin"; the margin is mirrored in `RELEASE_MARGIN_DAYS` and `check_bank_inventory.ts` fails if it drifts
  from `MIN_DAYS_TO_EXPIRY_AT_RELEASE`.
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
  re-fetches with its messages every 4 s while the browser tab is visible; the Live label is the last poll's result). Both sides' request lists reload silently when the shared poller's token for them moves (see **Live sync**; no timer of their own); the
  chat shows a day separator above each run of bubbles once the thread spans more than one day. The **Request and chats tab badge counts
  the rows where this facility has an action available** (`needsAction` in `lib/requestInbox.ts`): incoming requests awaiting its decision,
  accepted incoming requests awaiting its release, accepted outgoing requests awaiting its receipt. Requests waiting on the other
  facility are not counted. It is the same figure as the first one in the inbox's header line (`inboxCounts().action`; the second
  is how many more are in flight but waiting on the other side), so the two cannot disagree. It was the count of requests this
  facility had sent that were still pending, which for a hospital was everything it was waiting for and for a bank left out its
  whole incoming side. The supplier's incoming list is polled only while that tab is open, so the badge's incoming part is as fresh as
  the last visit to the tab (or the load at mount); its outgoing part is polled on every tab. Request and chats is for work in flight only (awaiting a decision, awaiting release, awaiting receipt): finished
  transfers are in Transfer history, whose rows open their thread and receipt. Events are the record, not action surfaces: confirm release and confirm
  receipt are buttons in the open request's header (`RequestHeader`, above the thread, always visible) and on its inbox row, both
  from `rowActions` in `lib/requestInbox.ts`, so they cannot disagree; an action that fails shows its error in that header
  (a failure opens the request's thread so it is seen, since a narrow screen shows the list or the thread, never both). Events
  stay full width, never aligned to a side. An
  event is listed whenever status and timestamps show it happened; a time or account never captured is None and reads
  "not recorded", never omitted. An event's account is the account's email (not an id), shown to both facilities. The
  receipt (`/receipt/{id}`, `screens/Receipt.tsx`, printable via `print:` classes) renders the same `timeline` and
  `units`, the same page for both facilities, states how many chat messages exist without reproducing them, and always
  says what BloodLink does not record (condition, temperature, transport, signatures). The document itself is
  `components/ReceiptRecord.tsx` (props `showPrintedLine`, `showChatLocation`, `headingLevel`; defaults suit the page), which the
  page renders (`screens/Receipt.tsx` is only the shell: loader, title, Print button) and which the thread's **Receipt** control shows inline in place of the
  conversation (from the thread's own `GET /requests/{id}` data, with the stamp and chat sentence off and `headingLevel={4}`; the control replaces the old link to
  the page, which stays reachable from inside the inline view for printing). The units table is `components/TransferUnits.tsx`,
  shared by the receipt (every column) and the thread, where the release and receipt events each carry a "Units" toggle (release:
  what was sent; receipt: how each unit arrived) when the ledger recorded them. Release and receipt events name the units from `request_units`, falling back to `request_unit_failures`
  for a request released before the ledger existed.
- **Live sync** (`GET /sync`, `lib/liveSync.ts`, `lib/liveSyncRuntime.ts`, `scripts/check_live_sync.ts`, `tests/test_sync.py`): one
  poller per signed-in tab asks `GET /sync` about every 15 s (±10 % jitter) **while the tab is visible**, instead of each screen polling
  its own endpoint, so a change another facility makes shows within about 15-17 s with no reload. No websocket and no SSE: they hold a
  connection open per user on a one-worker, 512 MB instance. The endpoint is authenticated and facility-scoped (401 without a token,
  403 for a deactivated facility or an admin; the facility is whichever `get_acting_facility_id` resolves, like every facility endpoint), on
  `read_engine` (in `READ_ONLY_FUNCTIONS`) and **one statement** (measured 2026-11-10 through the logging proxy: 1 round trip, plus 1 more
  at most every 30 s for the active-account check, which any request shares); it writes nothing and no rate limiter sees it. It returns
  `{notifications: {unread, latest_id}, requests: {incoming, outgoing}, inventory, expiring}` (about 150 bytes) with an `ETag`
  (a hash of the payload) and `Cache-Control: private, no-cache`; a matching `If-None-Match` gets a bodyless 304. Tokens are 12 hex
  characters hashed from a count and a version value over the caller's own rows, so they say "something changed", never what, and
  nothing about another facility. **`requests.*`**: count + max id + the newest of `created_at`/`accepted_at`/`declined_at`/
  `cancelled_at`/`supplier_confirmed_at`/`requester_confirmed_at` (every transition stamps one: a new transition column must be added
  to that `GREATEST`). **`inventory`**: a row-by-row hash (`sum(hashtextextended(...))`, order-independent, since `blood_units` has no
  `updated_at`) of every unit that still matters (not archived, or archived but not yet expired: the units `USABLE_COUNTS_SQL` can
  count) plus the facility's thresholds plus the Manila date (midnight expires units without touching a row). **`expiring`**: the same
  hash over `OFFERABLE_UNITS_SQL`'s units inside `NOTIFY_EXPIRY_NEAR_DAYS`, plus the date. **notifications** are two numbers, not a token;
  they replace the unread-count polling, and `_STALE_NOTIFICATION_SQL` is the one definition of "resolved" for the sweep, the count and
  `/sync`. The 12 hex characters are not secret, only unreadable. **Frontend**: `LiveSync` (pure, injected network, clock and visibility)
  has exactly one pending timer for the whole app (the check script asserts it). **Each subscription carries its own baseline**, the
  token its screen last saw for its section, so nothing is lost between a screen's first fetch and the poller's first answer:
  `subscribe()` records the poller's current token for the section (it fetches and polls nothing), and a screen subscribes just before
  its first fetch (or in the same synchronous turn: a poll answer cannot land in between). If the poller has no answer yet the baseline
  is "unknown" and the first answer counts as a change, so a screen that fetched before that answer refetches once; a screen that
  mounts after the first answer records that token and is told nothing until a later answer differs, so it is not fetched twice. After
  that only a moved token notifies, once per subscription. `stop()` forgets every subscription's baseline (a restart may be another
  session). The fix is in `LiveSync.subscribe`/`notify`, so every screen has it through the shared code. A screen reloads silently
  through `apiGet`, so the shared request layer still de-duplicates and the TTL cache still applies; hidden tab: the pending poll is
  dropped; visible or window focus: poll at once; errors double the interval up to 120 s; **401 and 403 stop it** until `start()` is
  called again (TopNav mounts it, so signing in does); sign-out and `SESSION_EXPIRED_EVENT` stop it. Forecasts are not polled (cached per
  Manila day; the Dashboard loads them and asks again only while types are pending). The open request's chat keeps its own 4 s poll (only
  while a thread is open). Cost and limits: the first-answer refetch above is the one added fetch, at page load only and only when `/sync`
  answers after the screens have mounted (measured in a headless browser against a mock API, 2026-11-10: a bank's Dashboard added 3
  reads, expiring, summary and incoming requests, about 4 statements, when `/sync` answered 1.5 s late; none when it answered first);
  a token the poller reported moments ago can be stale when a screen subscribes, which costs one redundant refetch at the next answer,
  never a lost update; a facility's own write moves its own token, so it costs one extra silent refetch; `latest_id` is `max(id)` over the
  facility's notifications, which scans more as history grows (an `(facility_id, id DESC)` index would make it a lookup: not added);
  the admin screens, the donor list and the hospital sourcing search are not covered.
- **Privacy notice and consent (RA 10173)** (`/privacy`, `screens/PrivacyNotice.tsx`, text as data in `lib/privacyNotice.ts`,
  `scripts/check_privacy.ts`, `tests/test_privacy_consent.py`, `server/migrate_add_privacy_consent.sql`). `/privacy` is handled in `App.tsx`
  before the signed-in branches (works signed out and signed in) and is linked from sign-in, Register and the account menu. **Every
  statement in the notice was checked against the code, the schema or the running system on 2026-11-10; when one stops being true
  (a retention job, an anonymise function, a new third party, a changed provider), change the text AND raise the version.** The check
  script forbids claims the code does not back (encryption at rest, "compliant", retention periods, guarantees) and requires the ones
  it must make. What the notice says is deliberately plain about the gaps: nothing is deleted automatically and there is no retention
  period; deactivating a facility only blocks sign-in (no delete or anonymise function exists, only the `is_active` flag and
  `archived_at`); erasure, access and portability requests are done by hand; BloodLink does not itself encrypt stored data and nobody
  has verified the hosts' encryption; the donor feature is simulated and records no donor consent. The notice names its third
  parties: Brevo (email; the code's default, `EMAIL_PROVIDER` in production not verified), OpenStreetMap Nominatim (the Register form
  geocodes through our server, the profile screens straight from the browser) and OSM map tiles (the browser's address and map area),
  Supabase (Tokyo per these notes, not independently verified), Render, Vercel. Fonts are self-hosted, so Google receives nothing.
  **The contact address is `VITE_PRIVACY_CONTACT_EMAIL`**, baked into the build. Unset or a placeholder (`@example.com`, `.test`, ...),
  the page shows a red banner and `check_privacy.ts` **fails by design**; no address is written in the source. **Consent**: the Register
  form has a required, unticked checkbox, and `POST /facilities/register` enforces it itself (`_require_privacy_consent`): a missing or
  false `privacy_consent` (a strict JSON boolean, so "yes" or 1 does not count) is a 400 that writes nothing and does not use the email
  budget, and so is a `privacy_notice_version` other than `PRIVACY_NOTICE_VERSION` in `main.py`, which must equal the constant in
  `privacyNotice.ts` (the check script compares them). The version and the database clock's time go on the registration row (re-recorded
  if the form is resubmitted before verification) and are copied to `users` at approval; **NULL means "not recorded", never "refused"**,
  and it is what every account has today and every admin-created account will have. The four columns come from the migration, **which
  is a file only and is run by hand**; until it has been, `_consent_columns_ready` makes registration and approval work without
  recording anything and logs `[consent] ... consent is NOT being recorded` (a positive answer is cached, a negative one re-asked at
  the next registration). Run the migration before deploying the code that records consent; its test runs it twice against the scratch
  database. `docs/build_data_dictionary.py` already describes the four columns. Not built (proposal in `docs/BACKLOG.md`): asking the
  accounts that existed before. Not touched, by rule: the donor roster upload and the SMS-blast code, so the donor screen carries no
  notice line yet; and profile editing has no contact-person field (that is only collected at registration), so the one line about
  another person's data is by the contact person's name and phone on the Register form.
- **Accessibility** (`scripts/check_accessibility.ts`; behaviour in a browser is checked by hand, see below). **One `<h1>` per screen
  state**, naming the page: sign-in, Register, reset-password and complete-profile put their visible title in it (one per state: the
  count in the script is the number of states, not headings on a page); the two Dashboards carry an `sr-only` "Dashboard" (their
  headline is a sentence, not a title); Inventory, Transfer history, Source supply / Find blood supply and the Requests list use their
  visible title; Admin's is the header's "BloodLink Admin"; `/privacy` has one. A dialog's title is an h2, its panels' sections h2/h3
  below the page h1, and the receipt inline in a chat is level 3. **`theme.css` gives h1, h2 and h3 different default sizes (2xl, xl,
  lg) and a line height of 1.5, so a tag change on a heading with no size class of its own resizes it**: every h1/h2 in a screen names
  its size (the script enforces it), and the ones that were h3 are `text-lg leading-normal`. The Donors screen has no h1 (donor-blast
  code, left alone by rule). **Dialogs**: every centred dialog is `Modal` and both header popovers (notifications, account menu) are
  the same `DialogPanel` (`components/DialogPanel.tsx`; the pure Tab-wrap rule is `lib/dialogFocus.ts`, tested under Node): role
  `dialog`, `aria-modal`, `aria-labelledby`; focus moves in on open (the first control that is not the close button, or the element
  marked `data-autofocus`, which the Change Password dialog uses because a "Forgot your current password?" button precedes its first
  field; `focusKey` re-focuses when a dialog opens on "Loading…" and its form arrives, as Edit facility profile does); Tab and
  Shift+Tab wrap inside; Escape closes the topmost one; focus returns to the opener, or to `returnFocusRef` when the opener is gone
  (the account menu's items are, by the time their dialog opens). **Never build an overlay by hand** (the script fails on a
  `fixed inset-0` outside `Modal`) and give every `<Modal>` a title. A field in a dialog needs its `<label htmlFor>`; the Change
  Password, Thresholds and Add unit dialogs have them. **A `title` attribute alone carries nothing** (mouse hover only: not keyboard
  focus, not touch): the script fails on any element with `title=` and no `aria-label`/`aria-labelledby`/`aria-describedby`, except a
  reviewed list of two (the notification `<time>`, whose date is also in hidden text, and the Leaflet marker, whose title is its
  accessible name). Explanations that matter use `components/Tip.tsx` (opens on hover, keyboard focus and tap, Escape closes, text
  always in the page via `aria-describedby`): the order button's sentence, "Limited history", "At risk", "Not enough history", the
  forecast basis tags, a unit's "Not released" note, the Expiring column, Upload CSV's columns and Clear expired. **Charts**: the gauge
  (`StockDial`) is `role="img"` with value, scale and status word; the trend chart is `role="img"` labelled by `describeTrendChart`
  (`lib/trendChart.ts`, checked in `check_trend_chart.ts`): latest recorded total and range, the projection by date, the shaded range,
  and in words whether it stays above the combined minimum or falls below it by a date. **Status is never colour alone**: every
  `StatusDot` has its word beside it (the script checks the three places). **The Register password hint is the server's rule** (10
  characters, a letter and a digit, no very common password; the other password forms say 8, as their endpoints do), and the script
  compares both to `server/auth.py` and `main.py`. Known gaps, not fixed: `<label>`s with no `htmlFor` remain in the Admin create-facility
  form, ResetPassword, the location picker's map label and the Donors screen; the printable Receipt page was not browser-checked; the
  sign-in tab order has "Forgot password?" between the email and password fields. The headless-Chrome check (headings in every
  state, dialog focus, tooltips, keyboard-only sign-in and Register, computed heading styles against the previous build) is a throwaway
  script against a mock API, not in the repo (it needs `puppeteer-core`); it ran 91 checks on 2026-11-10.
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
  local scratch Postgres: `python -m pytest server/tests -q` (769 tests plus
  160 subtests; use `server/.venv`, system Python lacks pytest). Never run
  tests via `python -m unittest` — that skips conftest and hits `.env`'s
  production `DATABASE_URL`, despite what older test docstrings say. `server/tests/conftest.py`
  refuses to run without it or against a non-local host, overrides
  `DATABASE_URL`, and rebuilds the public schema from `server/tests/schema.sql`
  (a `pg_dump --schema-only` of production; regenerate after a migration).
  Never run the suite against production.
  **Test files carry no `if __name__ == "__main__"` block, and a new one must not get one.** Each used to end in
  `unittest.main()`, so `python tests/test_x.py` ran the tests without conftest, against `.env`'s production `DATABASE_URL`,
  creating and deleting rows there. The docstrings only described that; removing the blocks closed it. Tests run through pytest
  alone, which loads conftest (the local-host refusal and the schema rebuild).
  Frontend checks: `node scripts/check_stock_bands.ts` (stock band rules) and
  `node scripts/check_forecast_source_tags.ts` (per-type forecast source tags) and
  `node scripts/check_trend_chart.ts` (trend chart history/forecast data) and
  `node scripts/check_incoming_groups.ts` (the blood bank's incoming-request sections) and
  `node scripts/check_components.ts` (request components match the server's) and
  `node scripts/check_request_timeline.ts` (status-event wording and the chat thread) and
  `node scripts/check_transfers.ts` (transfer-history filters, date presets and wording) and
  `node scripts/check_request_inbox.ts` (the Requests inbox's order, counts, status wording and unread rule) and
  `node scripts/check_bank_dashboard.ts` (the blood bank Dashboard's sentence, outlook, basis and order prefill) and
  `node scripts/check_bank_inventory.ts` (the blood bank Inventory's notes, filters, search, paging and backlog) and
  `node scripts/check_notifications.ts` (the bell's badge, times and paging) and
  `node scripts/check_live_sync.ts` (the shared poller: baseline, 304, one timer, hidden tab, backoff, 401/403 stop, restart) and
  `node scripts/check_accessibility.ts` (one h1 per screen state, headings keep their size, no title-only controls, dialog focus rules and wiring, chart text alternatives, status never colour alone, the password rule the forms state) and
  `node scripts/check_privacy.ts` (the Privacy Notice: contact address set, version equals the server's, required wording and forbidden claims, the `/privacy` route and its links; **it fails until `VITE_PRIVACY_CONTACT_EMAIL` is set**, e.g. `VITE_PRIVACY_CONTACT_EMAIL=you@your-domain node scripts/check_privacy.ts`) and
  `node scripts/check_request_cache.ts` (the shared request layer under `apiGet`: in-flight sharing, the short cache, invalidation), each printing `ok`, plus
  `npm run typecheck`. The frontend has no component test runner.
  A red suite is a finding, not a footnote — "known failure" is how a real
  bug hides.
- **Regenerating `server/tests/schema.sql`** is a `pg_dump --schema-only --no-owner --no-privileges` of production, run by Euhan
  (pg_dump 17 to match the dump's header). Where no `pg_dump` is installed, a read-only fallback was used on 2026-11-10: compare
  production's catalog (`pg_indexes` joined to `pg_index.indisvalid`, `information_schema.columns`, `pg_get_constraintdef` over
  `pg_constraint`, `pg_tables`) with the file through `database.read_engine` (a read-only session, so `.env` is never read directly),
  and add what differs by hand in `pg_dump`'s format. That compares table, column, constraint and index **names**; it does not compare
  column types, defaults or index definitions of indexes the file already has, so it is weaker than a real dump. Only the five indexes
  above differed. The pytest run (conftest rebuilds the scratch schema from this file) confirms the file loads, nothing more.
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
