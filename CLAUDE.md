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
  (one file of code touched). **One light, from the upper left, and the strength and the sizes are one block**, section 1 of the clay block. A raised thing (card, big container, tile, button, chip, pill, modal, popover, tooltip, and by default a text field) has a warm outer drop to the bottom right, next to nothing on its top and left, a white highlight inside its top-left edge and a faint warm shade well inside its bottom-right edge; a sunken thing (the gauge groove, `-pressed`, and a field if switched) is the reverse. **No spread anywhere, and no raised drop with a zero or negative offset.** `--clay-lx` and `--clay-ly` (+1) are the direction and multiply every offset. A size is a depth `--clay-d-{sm,md,lg,float,row,pill}` (1.6, 2, 2.4, 4, 1.6, 1 px); the drop is d across, d * `--clay-ky` (1.25) down, blurred d * `--clay-kb` (4.2); the inner light is d, d, blurred d * `--clay-kib` (2); the inner shade is d * `--clay-klo` (0.6) the other way, blurred d * `--clay-ksb` (4). Opacities `--clay-hi-a`, `--clay-lo-a`, `--clay-drop-a` (medium, light: 0.95, 0.07, 0.12), `--clay-grad`, `--clay-acc-hi-a`, `--clay-acc-drop-a`, each written for light and dark, medium and strong (strong: depths x1.25, higher opacities, the same directions); warm colours `--clay-hi-rgb`, `--clay-shade-rgb`, `--clay-drop-rgb`. **`--clay-field-depth`** is the field switch: `var(--clay-raised)` (default) or `var(--clay-sunken)`; the shadow token, the fill (`color-mix` of `--clay-field-raised-fill` and `--clay-field` by `--clay-field-up` = `max(depth, 0)`) and the inner offsets all follow it (the probe flips it and checks it flips back). Tokens, all generated from those: `--shadow-clay-sm` (buttons, chips, tiles, tooltips, unselected blood-type badges), `-md` (cards), `-lg` (every raised surface, `shadow-[var(--shadow-raised)]`, and the 600 px panels), `-float` (dialogs, popovers: **a `[role="dialog"]` must be excluded from the card rule or it gets `md`, which happened**), `-field` (raised or sunken), `-row` and `-pill` (inner shading only), `-pressed`, `-status` (Action required: the `lg` drop and light over its own fill) and the `-accent` set for solid primary buttons. The gauge **tiles are raised** (`sm`), not recessed (an earlier version had them as wells and they read as inverted). **Why the edge is soft, and how to keep it so**: an outline is a dark band on both sides of one edge, an outer drop and an inner shade stacked on the bottom-right edge. An outer drop is clipped at the box edge, so its darkness there is about opacity x Phi(offset / sigma); a small offset, a blur of about 4 times the depth and a low opacity keep that step small, and the inner shade is faint and far inside. **Raising `--clay-lo-a` or `--clay-drop-a`, or lowering `--clay-kb`, brings the outline back: re-measure the luminance profile across the edge (not by eye).** Measured on each surface's real computed style replayed on an isolated box over its real backdrop, light, largest per-pixel step in L* across the bottom / right edge, shadow alone, before to after: gauge tile 10.78 / 10.78 to 3.71 / 3.71, big card 6.57 / 5.61 to 3.11 / 2.74, secondary button 11.41 / 8.07 to 3.71 / 2.41, Admin card 11.99 / 10.8 to 3.74 / 3.44 (every one under half; the dark band inside a tile's edge 6.1 to 1.3, the shadow outside 10.8 to 3.7); a card's whole step, fill included, has a floor of about 4.4 from the card against the page (big card 9.09 to 6.31). **Measured extents** (visible = a 3-level colour change, on the page background, light, medium; px below / right / above / left): `lg` 10 / 9.5 / 4 / 4.5 (the target is about 10 or less below and right; the larger blur leaks a faint tail on the light side, 0 at the 8-level threshold), `md` 8.5 / 8, `sm` 5.5 / 5, `float` 18 / 17; strong `lg` 14 / 12 / 6.5 / 6. Each token is one warm outer drop plus an inner light and an inner shade, so **at most three layers on any element** (the probe asserts it, and a direction audit of every computed `box-shadow` on 14 screens found 0 flags, fields included). Things to keep true when editing it:
  (1) **A card, tile, dialog, popover or tooltip has no visible outline**: its `border-color` is transparent and the width stays, so nothing moves. (2) **A small
  control in a list or table row (`h-6`, `h-7` or `h-8` in an `li`, `td`, `th`) gets inner shading only and no outer drop**, and a field there gets radius and fill
  only (a **raised field** elsewhere is flat-filled, lighter than a card, no gradient so it never reads as a button, with a text cursor, the 3:1 boundary and a placeholder at 5.5:1 light / 4.9:1 dark); the same control outside a row has the full recipe. (3) **An unfilled control's edge is `--clay-boundary` (#857B70 light, #857868 dark), 3:1 or better
  against what is behind it** (measured on every field, outline button and danger button on twelve screens: lowest 3.40 light, 3.78 dark); a filled button needs
  none. A danger button's own border token is 1.6:1 on a dark card, so in dark clay its edge uses `--status-critical`, the base tone of the same family. Dividers
  and rims inside a surface (section rules, header band edges, row separators, `divide-y`, and every `border-gray-200` / `border-gray-300`, all decorative) use the soft `--border` (#DDD3C4 light, #3A3029 dark; 1.34:1 / 1.33:1 on a card), measured 1.21 to 1.34 light and 1.33 to 1.44 dark on 27 lines on four screens. (4) **A field's clay shadow and boundary colour are off while it has focus** (`:not(:focus)`), so Tailwind's
  `focus:ring-*` and `focus:border-primary` show. **Keyboard focus rings are the off look's** (a 2px accent outline with a 2px offset on buttons and chips; a field has no outline, its indicator is the accent border and a 30% box-shadow ring), **except a hospital's dark ring**, which is `#5B82B6` (4.3:1 on a dark card; the dark navy accent is 2.7:1 there, already so with the switch off). **Measure a ring only after its 150 ms `transition-colors` has finished**: read at once, it is the accent at 50% and looks like 2.6:1 (this misled the probe once). (5) **The light colours (`--card` #F8F3EA, `--background` #EEE8DD, `--clay-field` #E9E2D6 (a sunken field's fill), `--clay-field-raised-fill` #FBF8F2, `--border`,
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
  track against the card is 1.36:1 (1.50 against white; 1.54 dark). **Measured** (headless Chrome, local, 4x CPU throttle, the bank Dashboard against a mock API; not a device, and raster work may run off the traced thread): paint+raster, medium, median of 11: 15.5 ms off against 16.6 on in light (+1.1 ms, 7%; ranges 15-17 and 14-18) and 15.3 against 16.7 in dark (+1.4 ms, 9%; 14-16 and 14-18); strong, median of 7: +0.3 and +0.6 ms; the layer phase was 8-12% higher in every comparison and style recalculation 4-5% (+5 to +6 ms of about 135). A small cost is likely, inside the run-to-run spread (the same build drifts by about 30% between sessions); earlier rounds measured +1.2 to +2.5 ms; a phone's GPU was not measured. **Looked at with it on** (screenshots at 1280 and 390 px, light and dark): the bank
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
  **Forecast details**, per blood type, worded as the API names it. A hospital has no forecast and no such tag. **Forecast details is
  plain language first** (2026-11-11; `PlainGroups`, `TechnicalDetails` and `forecastGroups` / `PATTERN_GROUP` / `TREND_GROUP` /
  `LIMITED_NOTE` in `lib/bankDashboard.ts`; a bank-only panel, since a hospital has none): the opening line ("Built from N days of this
  facility's recorded stock. These are estimates, not guarantees."), then two groups of blood-type chips, each drawn only when it has a
  type: **"Follows your usual pattern"** (any type with a fitted model) and **"Continues the recent direction"** (the linear trend, and a
  type absent from `sarimax_orders`); a type still being calculated is in neither, and the synthetic stand-in and "none" keep their own
  paragraphs, no groups. A type whose own history is below the model's minimum (`insufficient_history`, or a recommendation carrying
  `limited_history`) keeps its group and gets one extra line saying it is under N recorded days and rough too, because the pattern group's
  own sentence would otherwise promise a pattern that six days of records cannot show. **Rule for forecast text a user reads: no model
  name, setting, statistical term or percentage, and no word of accuracy, in the main view or in a tooltip or `aria-label`;** the check
  script bans SARIMAX, ARIMA, grid, exogenous, order, seasonal, MAPE, RMSE, AIC, linear, lag, fit, model, %, accurate, reliab, best, precise, optimi and better from
  the two group texts and the limited-history note, and a second check bans "prediction interval", "technically", "more reliable" and "95%" from the Dashboard's source. Everything technical (the per-type method and settings, the grid-searched / AI-recommended /
  default-order flag, the "SARIMAX for 3 blood types, linear trend for 5" line, the linear-trend explanation) is **only** inside "Technical
  details (for administrators)", the last thing in the panel: a real `button` in an `h3` with `aria-expanded` and `aria-controls`,
  **closed by default** (its body is `hidden`, so none of it is in the page's text or tab order until opened), with exactly the words the
  panel showed before. **The pattern group's sentence makes no comparison** ("These pick up weekly ups and downs and dengue season in your recorded stock."):
  an earlier version said "They are more reliable", a claim the data does not back (the order selector's decision tree was measured not to
  beat the default order on synthetic data, and the AI-order backtest on the one real facility was worse; see the selector JSON). The stand-in
  paragraph still says "Synthetic, not real data ... trained on generated data" and its first line "comes from a reference model" (a disclosure
  kept as it was; the Dashboard's own stand-in line and the chart's text alternative say "an example pattern, not your own records").
  **Other Dashboard wording, plain too**: the At risk sentence says "the low end of the range it could realistically fall in" (no 95%), and
  the shaded-band help says "The band is the range the total could realistically fall in. It is wider when there is less history or when
  looking further ahead." (no "prediction interval"). **The badge and the Upload history "Refresh" link were fixed for dark contrast
  (2026-11-11)**: the unselected `BloodTypeBadge` text and Refresh are `text-primary dark:text-foreground` (bank 3.78 and 4.1, hospital 2.67
  and 2.89 before; 14.53 and 15.77 after, light unchanged at 7.38 to 11.5). **Still under 4.5:1 in the dark theme, measured and not changed**:
  every other `text-primary` text (bank maroon 3.59 to 4.1, hospital navy 2.51 to 2.67): the active nav label, the nav's unread count, "Order 90
  by 21 Nov", "Open the incoming list", "Review in inventory", "View all requests", "Released, confirm receipt", and the thread card's
  status lines "Accepted, confirm release" / "Awaiting your decision". The "Link" half of the wordmark is a logotype. The fix would be one dark
  token for accent text (a lighter navy and maroon), which is a new token and so the owner's call; `BloodTypeBadge`'s `selected` state
  (white on the accent) is not used by any screen. `check_bank_dashboard.ts` and `check_accessibility.ts` assert all
  of this. And "Order 90 by 4 Nov" opens Source supply with type, quantity and Packed RBC
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
- **The hospital Inventory** (`HospitalInventory.tsx`, `HospitalInventoryView`) is the same page as the bank's, not a narrower copy: the
  same content width (the `max-w-5xl` wrappers are gone; header block, table and Upload history all run the full `max-w-screen-2xl`
  column), the same header block (title, units on hand, Upload CSV, Add unit; the hospital adds its expired-units note and Clear expired
  button), the same search box above the table (`components/UnitSearch.tsx`, shared with `BankInventory.tsx` so the box, its help
  line, the `/` shortcut and the results frame cannot drift; `searchUnits` from `lib/bankInventory.ts`, so a DIN prefix or substring,
  case and spaces ignored, then location or component), and the same row grid at the same `lg` breakpoint, `gap-x-4`, 48 px rows,
  Thresholds right-aligned to the content edge (verified equal to the pixel at 1280 and 1920 against the bank's in a headless browser
  on a mock API). The columns are the hospital's own: Type, Units held, Status, Minimum, Maximum, Thresholds. An opened type lists
  DIN, Component, Location, Volume, Collected, Expires, Time left, Remove (the raw `collected` / `expires` strings as before, no bank
  Note column: release-margin and reservation notes are a supplier's). Search results add the Type column. The old per-type search
  (shown above 8 units) stays. The Thresholds button keeps its visible label below `lg` (the bank's is icon-only there). Rows start
  collapsed, as before. No data logic, API or accent changed. **The per-unit filter** (Component, Expires, Status, Clear filters) is
  one component, `components/UnitFilterBar.tsx`, drawn by both an opened bank type and an opened hospital type (neither screen has its
  own `<select>`; `check_bank_inventory.ts` fails if one grows back, if the bar loses a label, or if it gains an accent colour: the
  maroon and the navy only appear on the focus ring, which is the role's own accent). What differs is data: the Status options
  (`BANK_STATE_FILTER_OPTIONS`: any, available, reserved, not released: inside the margin, expired; `HOSPITAL_STATE_FILTER_OPTIONS`:
  any, **usable (counted in units held)**, expired). A hospital has no reserved or inside-the-margin state because only blood banks
  release units (`create_request` refuses a hospital as supplier), so `filterUnits` got a `"usable"` state (`daysLeft >= 0`, the figure
  "units held" counts) and the hospital filter does not offer the release states. In the hospital panel it combines with the existing
  per-type text search; the bank's paging is not on the hospital panel (it lists every unit of the type, as before). Verified in a
  headless browser on a mock inventory: filters narrow and combine, Clear filters restores, Tab reaches the three selects in order each
  with its label, focus stays on a select after it changes, the navy 2px focus ring, no overflow at 390 px, and the bank's expanded
  page renders byte-identically before and after.
- **Accounts: one way in.** A facility account is made by registration approval only (`POST /facilities/register`, email proof,
  `admin_approve_registration`). The admin-side "Create Facility Account" form and the table's "Add account" button, with their
  endpoints (`POST /admin/facilities`, `POST /admin/facilities/{id}/accounts`), the request models, `_create_staff_account`, the
  frontend functions and `server/tests/test_admin_create_account_for_facility.py` (4 tests), were removed on 2026-11-11. What stays: the admin's
  **Reset** (`POST /admin/accounts/{id}/reset-password`, which **still returns `temporary_password` in its response**, shown once in the
  Admin screen), `must_change_password`, the forced first-login change and the "(pending first login)" label (4 accounts were on a temporary
  password when last counted, a read-only count on 2026-11-11; 0 facilities had no account), all existing columns (no migration), the
  registrations queue, deactivate / activate. A facility with no account now shows "No account" in the table, with no action to fix it
  (none exists in production). The privacy notice no longer mentions accounts an administrator created; its version was not raised, because
  `check_privacy.ts` does not tie the version to that sentence and the notice's other statements are unchanged (the sentence only got
  shorter), which is a judgment, not something the script enforces.
- **The header** (`TopNav` in `App.tsx`, the Admin screen's header, `NotificationBell` / `AccountMenu` in `components/AccountMenu.tsx`):
  **4rem tall, every control at least 44 × 44 px**, one row at 320 to 1920 px, for all three roles. One class, `HEADER_ROW_CLASS`,
  is the inner row of both the facility nav and the Admin header (`max-w-screen-2xl`, `px-4 sm:px-6`, the page gutter), so the logo and
  the account button line up with the content on every screen (the Admin bar used to be full-bleed with its own `px-6`). Nav labels
  still appear at `md`, the wordmark at `sm`, the account name at `lg`. The bell badge is anchored to the icon, not the button.
  **Nothing else was positioned from the old 56 px height**: no `top-14`, `scroll-mt` or `calc(100vh - ...)` exists; the two header
  popovers hang from their buttons with `top-full`, and `Modal` is portaled. Below `sm` the notification panel anchors to the header
  and spans the viewport (it ran off the left edge of a 390 px phone before this change, by 28 px, and by 44 px with the larger
  controls). The clay rules need nothing from it (the header's controls are flat controls). **Re-baselined 2026-11-11 against HEAD with
  the switch off** (headless Chrome, mock API, crops from under each header, 2 renders per side): with the new header held to its old
  56 px by an injected style, 60 of 76 state / width / theme crops are pixel-identical, and the 16 that differ are exactly the Admin
  page (form removed, content gutter), the hospital Inventory (new layout) and the account popover (it hangs 8 px under a taller
  button, so the page pixels behind it differ). Without that injection 27 of 76 differ, by a few dozen glyph pixels each (shown for
  the hospital Dashboard at 1280: 42 pixels in one blood-type badge, the same in three renders of each side, none once the 8 px offset
  is removed); that is raster noise from the 8 px shift, not a style change, but it is an inference from the injected run, not a
  measurement of the cause. With the switch on, under `prefers-contrast: more`, `forced-colors: active` and print, every element's
  computed shadow, radius, fill, gradient, border colour, text colour and box equals the switch-off render in all 190 state / width /
  theme combinations (computed styles, not pixels, because single screenshots are noisy). Differs from HEAD with the switch off:
  the header on every screen, the Admin page, the hospital Inventory, the account popover's position; nothing else was found.
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
- **A request's timeline is derived, not stored** (`_request_timeline`, served by `GET /requests/{id}` and, per request card, by
  `GET /conversations/{id}`). An event is listed whenever status and timestamps show it happened; a time or account never captured is
  None and reads "not recorded", never omitted. An event's account is the account's email (not an id). The receipt (`/receipt/{id}`,
  `screens/Receipt.tsx`, printable via `print:` classes) renders the same `timeline` and `units`, the same page for both facilities,
  states how many messages were sent **about that request** (`request_messages.request_id`) without reproducing them, and always says
  what BloodLink does not record (condition, temperature, transport, signatures). The document itself is `components/ReceiptRecord.tsx`
  (props `showPrintedLine`, `showChatLocation`, `headingLevel`; defaults suit the page), which the page renders (`screens/Receipt.tsx` is
  only the shell: loader, title, Print button). The units table is `components/TransferUnits.tsx`, shared by the receipt (every column) and
  the thread's expandable release and receipt events. Release and receipt events name the units from `request_units`, falling back to
  `request_unit_failures` for a request released before the ledger existed.
- **Supply and Messages** (2026-11-11, stage 2 of the Messages redesign; `screens/Supply.tsx`, `screens/Messages.tsx`,
  `components/ConversationList.tsx`, `components/ThreadParts.tsx`, `lib/conversations.ts`, `scripts/check_conversations.ts`; the server is
  the **Conversations** bullet below). **Names**: the top nav tab "Requests" is **Supply** (screen key `supply`; a notification row's link
  text `requests:{id}` is still what the server writes and still opens the request: it names a request, not a screen); its sub-tabs are the
  role's sourcing name (Source supply for a bank, Emergency sourcing for a hospital), **Messages** (the old "Request and chats") and
  Transfer history, unchanged. The Messages badge is `GET /conversations`' `needs_action`: requests needing this facility's action (the
  nav badge's rule, `needsAction` in `lib/requestInbox.ts`). **The old per-request chat is gone from the frontend** (`Requests.tsx`,
  `RequestThread`, `RequestHeader`, `RequestInboxRow`, the inbox order and the "New / Updated" localStorage flags; the Receipt button in
  the chat header and its inline receipt view); the old per-request server endpoints stay. **Layout**: a conversation list (one row per other facility: initials,
  name, time, last message or "You: ...", an unread badge, "N requests need your action" or "N open requests, waiting on them"; the list's
  title is the screen's one h1) and one thread (title h2, a request's title h3). On a phone the list is the whole screen until a
  conversation opens, then the thread with a back link (focus returns to the row). **Alignment by actor**: every message, status event and
  request is aligned by who did it, mine on the right and theirs on the left, never centred or full width; each carries a visible label,
  "You" or the facility's name, plus the time, so alignment is never the only cue. A message is a bubble (mine in the role's accent with
  white text: maroon, navy; theirs neutral). **The live-card rule** (`threadRows` in `lib/conversations.ts`): every OPEN request has exactly
  one live card, rendered at the position of the request's most recent status event (its creation when it has none), on the side of the
  facility that did that event, mine on the right and theirs on the left; so after I accept or release, the card is the "You accepted request
  #65" / "You released 4 units for request #38" card on my right, at the bottom, with the next button in it, and when the other side acts
  it moves to their left with whatever I now need to do. It shows blood type, quantity, component, emergency type, the status pill, the event
  sentence ("You accepted request #38", "Riverside General Hospital confirmed receipt for request #31: 5 of 5 arrived"), the `rowActions`
  buttons, "Message about this" and the Receipt link where it applies. "Units released (N)" and "Units and outcome (N)" stay expandable inside
  it, and **the account email is only inside that expansion ("Recorded by")**, never on a card or line. **Earlier events of the same
  request collapse into one-line muted trails** ("Request #65 · 4 units of Packed RBC · Accepted", no buttons, aligned like the actor of that
  event, "Details" expands the sentence and the units): only the live card has actions, so nothing can disagree. **A message never moves a
  live card; only a new status event does** (rows are in time order and a message is just another item): a message sent after the latest
  event sits below the card. Keys: the live card is `live-<request>-<latest event kind>` (so a new event is a new node at its new place, and
  while nothing happens its node never changes), a trail is `e-<request>-<kind>`, a message `m-<id>`; the card keeps its DOM id
  `req-card-<id>` and its status `req-status-<id>` (one live card per request, so no duplicates). After an action the thread scrolls the new card
  into view (the whole card, `block: "nearest"`) and focuses its status text. **Requests in the thread**: finished ones (completed, failed, declined,
  cancelled) are a one-line summary ("Request #31 · 5 units of Packed RBC · Completed, 5 of 5 received" and "Show history") that expands
  in place, the newest 5 first and "Show earlier requests (N more)" through `finished_before`; **a request this facility just acted on
  stays expanded (its live card at its final event, with the result) until the conversation is reopened or "Hide history"**, so the result does not fold away under the
  reader. The header says how many requests are open and how many need my action, with "Go to the first one" (scrolls to that card and
  focuses its status). **Actions are on the request card only**, from `rowActions` (the one list the badge counts), through
  `RequestActionButtons`: Accept and Decline (the only pair), Confirm release, Confirm receipt, Cancel request (keeps its confirmation
  dialog); no action in the header or a strip. An error shows inside its card; after an action focus moves to the card's status text
  (`req-status-<id>`). A released or finished card has a small **Receipt** link to `/receipt/{id}`, the page Transfer history links.
  **Composer**: the message goes to the conversation with no request by default; "Message about this" on a card sets the chip
  "About: request #N ×" (one line, also at 390 px); the field is disabled only when the pair has no request, and an error or hint is
  announced next to it (`role="alert"`, `aria-describedby`); a 429 says "You are sending messages too quickly. Wait a few seconds, then
  send again." and keeps the text; **focus stays in the field after sending** (the field is never disabled while sending). **Live**: the
  conversation list (kept in `Supply.tsx`, so the badge is right on every sub-tab) refetches when the shared poller's `messages` token or
  either requests token moves; the open thread polls `GET /conversations/{id}` every 4 s only while it is open and the browser tab is
  visible (the "Live" label is the last poll's result); opening it, and each time a newer message arrives while it is open and visible,
  calls `POST .../read`, and the "N new messages" divider is drawn from the read position the thread had when it was OPENED. Rows have
  stable keys (`m-<id>`, `e-<request>-<kind>`), so a poll that changes nothing changes no DOM node and a screen reader is not told old
  messages again (measured: 0 nodes added over two polls). A deep link (a notification, Transfer history's "Chat") finds the other facility from
  `GET /requests/{id}`, opens that conversation, expands and focuses the card, and loads older pages until it is found. **Accessibility**:
  the thread is `role="log" aria-live="polite" aria-relevant="additions" tabindex="0"` with a name; own-sender labels are the foreground
  colour (the old accent-coloured label failed contrast on a dark card); a bubble and a chip have a **border** (so forced colors still
  shows them; the clay rules make it transparent). **The selected conversation row** (`ConversationList.tsx`): `aria-current="true"`, a stronger accent tint (`bg-primary/15`), a **4 px
  accent bar on its left edge that is a real border** (so forced colors and `prefers-contrast: more` still draw it: measured 4px solid in
  both), a bold name, and foreground-coloured preview, time and action line (the amber action text was 4.06 to 4.32:1 on the selected tint
  and failed; foreground is 12.2 to 13.0:1). A row with something waiting on me that is not selected has its own light amber tint
  (`bg-status-watch-tint/40`), no bar and a semibold name, so the two are told apart when both apply: the selected row keeps its amber
  "needs your action" line and dot. On a phone the list and the thread alternate, so a selected row is only visible from lg up.
  **Contrast measured in the browser** (computed colours against the composited
  background; bank maroon and hospital navy, light and dark, clay on and off; every text in the screen): all at or above 4.5:1, the lowest
  being my bubble in the bank's dark theme at **4.5036:1** (white on #C94A68: it passes by 0.004, so a change to that token needs this
  re-measured), then list previews 4.9 to 5.1. Under forced colors, `prefers-contrast: more` and print the bubbles and chips keep a real 1px border. Clay: `msg-bubble`, `msg-bubble-own`,
  `msg-bubble-other` and `thread-chip` are the marker classes (theme.css section 8): small raised look, accent shading on mine, no
  outline; a request card is an ordinary card. Checked with a headless Chrome flow against a stateful mock API (a throwaway
  script, not in the repo): open a conversation, send, accept, confirm release, confirm receipt, cancel, the unread divider, 429, paging,
  the phone layout, keyboard-only, focus. **Cost, measured** (headless Chrome, local, 4x CPU throttle, a mock API; not a device; one scripted task:
  open Supply > Messages, open the Riverside conversation, expand a finished request, send a message, let two polls land, scroll the log; median
  of 7, clay on against off): paint + raster 124.1 ms off, 135.5 ms on in light (+11.4 ms, +9%) and 129.0 against 137.8 in dark (+8.8 ms, +7%); layout and
  style +5%, the ranges of off and on overlap in places and the same build drifts between sessions. **Layout shift**: none from sending, from polls
  or from a message arriving; two shifts after the Dashboard loaded, both on a click the script made with `.click()` (so the browser does not
  call it input): opening a conversation (0.013 at 1280, 0.031 at 390: the log's "Loading" line becomes its rows) and expanding a finished request
  (0.011, 0.020: the rows below it move down, which is the point). **Privacy notice 1.1** (see its bullet).
- **Live sync** (`GET /sync`, `lib/liveSync.ts`, `lib/liveSyncRuntime.ts`, `scripts/check_live_sync.ts`, `tests/test_sync.py`): one
  poller per signed-in tab asks `GET /sync` about every 15 s (±10 % jitter) **while the tab is visible**, instead of each screen polling
  its own endpoint, so a change another facility makes shows within about 15-17 s with no reload. No websocket and no SSE: they hold a
  connection open per user on a one-worker, 512 MB instance. The endpoint is authenticated and facility-scoped (401 without a token,
  403 for a deactivated facility or an admin; the facility is whichever `get_acting_facility_id` resolves, like every facility endpoint), on
  `read_engine` (in `READ_ONLY_FUNCTIONS`) and **one statement** (measured 2026-11-10 through the logging proxy: 1 round trip, plus 1 more
  at most every 30 s for the active-account check, which any request shares); it writes nothing and no rate limiter sees it. It returns
  `{notifications: {unread, latest_id}, requests: {incoming, outgoing}, inventory, expiring, messages}` (about 175 bytes) with an `ETag`
  (a hash of the payload) and `Cache-Control: private, no-cache`; a matching `If-None-Match` gets a bodyless 304. Tokens are 12 hex
  characters hashed from a count and a version value over the caller's own rows, so they say "something changed", never what, and
  nothing about another facility. **`requests.*`**: count + max id + the newest of `created_at`/`accepted_at`/`declined_at`/
  `cancelled_at`/`supplier_confirmed_at`/`requester_confirmed_at` (every transition stamps one: a new transition column must be added
  to that `GREATEST`). **`inventory`**: a row-by-row hash (`sum(hashtextextended(...))`, order-independent, since `blood_units` has no
  `updated_at`) of every unit that still matters (not archived, or archived but not yet expired: the units `USABLE_COUNTS_SQL` can
  count) plus the facility's thresholds plus the Manila date (midnight expires units without touching a row). **`expiring`**: the same
  hash over `OFFERABLE_UNITS_SQL`'s units inside `NOTIFY_EXPIRY_NEAR_DAYS`, plus the date. **`messages`** (added with the conversations, 2026-11-11): count + max id of the messages in the caller's own conversations (a message with
  no `conversation_id` is counted by its request's pair) plus a hash of the caller's own `conversation_reads` rows, so it moves when a
  message arrives or is sent in any of its conversations and when its own read position moves (another tab), and says nothing about any other
  facility; still one statement. **Until the conversations migration has been run** the statement leaves the conversation parts out (the token
  then covers the messages on the caller's requests alone), chosen by `_conversations_ready()`, which costs one extra statement the first
  time per process (and one every 30 s while the migration is missing), then none. The Messages list (`Supply.tsx`) subscribes to it, with both requests tokens. **notifications** are two numbers, not a token;
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
- **Conversations** (Messages redesign, **stage 1 of 2, the server; stage 2, the frontend, is the Supply and Messages bullet above**; `main.py`, "Conversations";
  `server/migrate_add_conversations.sql` and `server/migrate_conversations_part2_not_null.sql`, **both files only, never run by anything in
  the repo and not yet applied to production**; `tests/test_conversations.py`, `tests/test_sync.py`, `docs/build_data_dictionary.py`). One
  conversation per **pair of facilities**, whatever requests exist between them (`conversations`: the pair stored once, smaller id first, a
  CHECK and a UNIQUE keep it so; `conversation_reads`: one row per facility per conversation, the id of the last message it has read;
  `request_messages` gained a nullable `conversation_id` and `request_id` is nullable: a message may be about no request).
  **Rows are lazy**: a `conversations` row is made by the first message sent or the first time a thread is opened (`POST
  /conversations/{id}/read`), **never by `create_request` or a transition**, and a test fails if any of those six functions' source mentions
  a conversation. Every read builds the pair list from the union of requests and conversation rows, and a message whose `conversation_id` is
  NULL (an old server instance, or before the backfill) is read by way of its request's pair, so a missing row breaks nothing and nothing is
  hidden. Endpoints (the caller's own view of the pair; the other facility is the path, named `other_facility_id`, and **nothing takes both ids,
  so a third facility is only ever asking about its own pair with someone, which is 404**; an unknown facility and a facility with no request in
  common read the same):
  `GET /conversations` (**one statement, `read_engine`**: per other facility the facility, last message cut to 200 characters, unread count after
  my read position, requests needing MY action, open requests, total, last activity; ordered **action-waiting first, then latest activity**, the
  conversation row's creation counting only for a pair with neither a message nor a request, so opening a thread never reorders the list);
  `GET /conversations/{id}` (**six statements at most, `read_engine`, creates nothing**: `thread` = the messages and every request's status events
  merged in time order, an event at its own time or the previous event's when it has none, an event before a message at the same instant;
  `requests` = each card's data: the `_request_block`, direction, `needs_my_action`, the timeline with who did what and when, the units
  released and received, the summary, identical to `GET /requests/{id}`; `counts`; `unread`, `read_position`; **open requests always, finished
  ones the newest 5 (`finished_limit`, at most 50) with `finished.has_more` and `finished_before` = the oldest finished id held for "show
  earlier"**, the messages limited to the shown requests' window and to 500, `messages_truncated`);
  `POST /conversations/{id}/messages` (body trimmed, non-blank, 2000 characters in Pydantic, optional `request_id` that must be a request
  between the two, else 400; **allowed whenever the pair has at least one request of any status**, which replaces the old accepted/completed/
  failed gate because in a conversation both sides see the message; a deactivated counterpart cannot be messaged; **30 a minute per facility**,
  `_MESSAGE_LIMITER`, the existing `AttemptLimiter` design, every attempt counted, 429 with `Retry-After`, **shared with the old endpoint** so it
  is not a way round); `POST /conversations/{id}/read` (moves **my** read position forward only, never beyond the newest message, only my own
  row: the facility is the token's, the body can only carry `up_to_message_id`). **"Needs action" is the nav badge's rule** (accept or decline an
  incoming pending request, confirm release of an accepted incoming one, confirm receipt of an accepted outgoing one that was released; never
  cancel), written once in SQL (`_ACTION_SQL`) and once in Python (`_request_needs_my_action`, the form of `rowActions` / `needsAction` in
  `lib/requestInbox.ts`), and a test asserts they agree on every status. **The old per-request endpoints are unchanged to a caller**
  (`GET`/`POST /requests/{id}/messages`, the same gate and shapes), and write the same table: once the migration is in the old POST also sets
  `conversation_id` (making the row if need be) so its message is in the conversation at once, and it draws on the same send allowance. A message sent
  the new way with a `request_id` shows in the old per-request list; one about no request does not. **Deploy-order safety**:
  `_conversations_ready()` (positive answers remembered for the process, negative ones re-asked every 30 s and logged) lets a server that comes
  up **before** the migration keep working: the new endpoints answer 503 ("the conversations migration has not been run"), `/sync` leaves the
  conversation parts out, the old POST writes the old way. **Order**: backup, part 1, deploy the server, then part 2 (NOT NULL), which is the
  only step that can make an old writer fail and has its own file; see the header of each file, which is the instruction. The unread count is
  per facility and server-side (`conversation_reads`), after the owner's decision for option 2 of the proposal; the nav badge stays
  "needs action". The privacy notice's chat statements were reworded in stage 2 (**version 1.1, 11 November 2026**: one conversation per other
  facility, a message can be about no request, the stored read position, and that the other facility sees the whole conversation but is
  not shown how far you have read; `check_privacy.ts` requires that wording). **Why the version was raised**: no earlier statement became
  false, but the notice itself says "when this notice changes, the version number changes", a new item is now stored (a read position
  per facility per conversation) and a message may now exist with no request; a reworded notice under the old number would break its own
  promise. There is no re-consent flow (`docs/BACKLOG.md` proposes asking the accounts that existed before; that now covers 1.0 to
  1.1 as well): registrations from now on record 1.1, earlier ones keep their recorded 1.0 and NULL stays "not recorded". **Deploy
  order for the version**: the server accepts only the version it names, so a Vercel build with 1.1 and a Render instance still on 1.0
  (or the reverse) refuses registrations (400) until both are live; deploy the server first. `docs/DATA_DICTIONARY.md` was **not** regenerated (it introspects production,
  which does not have the tables yet): `docs/build_data_dictionary.py` has the descriptions and was dry-run against the scratch database.
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
  and it is what every account has today (an account is made one way now, registration approval, which records it; see **Accounts**). The four columns come from the migration, **which
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
  compares both to `server/auth.py` and `main.py`. Known gaps, not fixed: `<label>`s with no `htmlFor` remain in ResetPassword, the location picker's map label and the Donors screen; the printable Receipt page was not browser-checked; the
  sign-in tab order has "Forgot password?" between the email and password fields. The headless-Chrome check (headings in every
  state, dialog focus, tooltips, keyboard-only sign-in and Register, computed heading styles against the previous build) is a throwaway
  script against a mock API, not in the repo (it needs `puppeteer-core`); it ran 91 checks on 2026-11-10.
- **The transfer history** (`GET /transfers`, the third Supply tab): a transfer is an accepted request, so finished ones
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
  local scratch Postgres: `python -m pytest server/tests -q` (844 tests plus
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
  `node scripts/check_request_timeline.ts` (the receipt's wording of status events) and
  `node scripts/check_transfers.ts` (transfer-history filters, date presets and wording) and
  `node scripts/check_request_inbox.ts` (a request card's actions, the badge's rule, status wording) and
  `node scripts/check_conversations.ts` (the Messages screen: list order, unread and action counts, alignment by actor, finished-request collapse and paging, day separators, the new-messages divider, wiring) and
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
  **2026-11-11 the file was regenerated from the scratch Postgres, not from production**: a real `pg_dump` 17 (`C:\Program Files\PostgreSQL\17\bin`,
  the scratch server is 17.11) of the scratch database after `migrate_add_conversations.sql` (part 1 only) was applied to it. Against the previous
  file the only differences are the two new tables, the `conversation_id` column, `request_id` losing NOT NULL, one index, the constraints and
  keys of the new tables, and the header's server version (17.6 to 17.11). It reflects production **after part 1**, not after part 2. Regenerate
  it from production once the migration has been run, as above, and the two should match.
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
