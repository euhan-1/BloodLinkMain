# BloodLink — Status Report, 2026-09-26

Written for a reader who knows the project but hasn't seen the code — same standard as `STATUS_SEPT_24.md`. Every claim below was re-verified today: fresh `git log`, fresh test runs, fresh live database queries, fresh live HTTP checks against the deployed frontend and backend. Nothing is carried over from the Sept 24 report without re-checking. File paths and line numbers are given throughout. Where I couldn't verify something, it says `UNKNOWN` and what would close the gap.

**Headline finding, up front since it shapes everything below:** in the two days since Sept 24, **zero code has been committed.** `git log` confirms the last commit is still `1593c36` at 2026-09-24 03:16. None of the six items you asked me to check on have been fixed — one of them (the expired-unit backlog) has gotten measurably worse just from time passing.

---

## Part 1 — What Changed Since Sept 24

**`git log --since="2026-09-24"` — the full list, 4 commits, all from the morning of Sept 24 (before the last report was written), nothing since:**

| Commit | Time | What it did |
|---|---|---|
| `1593c36` | 03:16 | Default to light mode instead of following OS `prefers-color-scheme` |
| `786a1ad` | 02:55 | Proxy API calls through Vercel (`vercel.json` rewrite) to work around PLDT blocking direct Render access |
| `c5b4d25` | 02:26 | Code-split heavy vendor chunks (recharts, leaflet) and lazy-load post-login screens |
| `114aede` | 01:58 | Lazy-import forecasting dependencies (pandas/scipy/statsmodels) so `uvicorn` boots fast; warm the backend from the login page |

All four of these predate the Sept 24 report itself (which was written using their end state) — so from that report's perspective, **nothing has landed since.**

**The six planned items, checked directly against current code and the live database:**

1. **Login accounts for facilities 2, 3, 4, 6 — NOT DONE.** `SELECT * FROM users` returns the same 5 rows as Sept 24, verbatim: `demo.hospital@example.com` (facility 1), `demo.bloodbank@example.com` (facility 5), `demo.admin@example.com` (no facility), `akosidux2000@gmail.com` (facility 43), `demo.forecast@example.com` (facility 158). St. Mary's, General Hospital Blood Bank, University Health, and Metro Blood Alliance still have zero accounts.

2. **Supplier-side priority routing and notification text — HALF DONE, and I owe you a correction on the other half.** Re-reading `main.py` just now: `GET /requests/incoming` (`main.py:2653-2673`) **already calls `_apply_priority_sort`**, identically to `GET /requests` (the requester's own queue, `main.py:2629-2650`) — both go through the exact same function. Since there have been zero commits since Sept 24, this was *already true* when I wrote that report; I described it as only affecting "the requester's own pending queue" and didn't call out that it applies to the supplier's incoming queue too. That was an incomplete description in my last report, not a change since then — correcting it now. **What's still genuinely not done**: the notification text a supplier receives is identical regardless of `emergency_type`. `main.py:2586-2589`: `f"New {quantity}-unit {blood_type} request from {requester}"` — same string for a trauma request as a routine restock. No urgency differentiation on the receiving end.

3. **Rate limiting on `/auth/login` and `/auth/forgot-password` — NOT DONE.** Exhaustive grep across `main.py`, `auth.py`, `requirements.txt` for rate-limit/throttle/lockout logic: zero hits. Unchanged from Sept 24.

4. **`GET /facilities` auth gap, CORS default, `npm audit` — NOT DONE.** `main.py:2188-2195`'s `list_facilities()` still has no `Depends` of any kind — still fully unauthenticated. `CORS_ALLOWED_ORIGINS` (`main.py:111`) still defaults to `"*"` if unset. `npm audit` counts are identical to Sept 24 (see Part 2).

5. **Expired-unit backlog — NOT DONE, and worse.** 1,353 unarchived on Sept 24; **1,685 now** (see Part 4 for the per-facility breakdown). Zero units have ever been archived, anywhere, at any point I've checked. This number grows on its own as more units cross their expiry date each day that nobody clicks "Clear Expired Backlog."

6. **`test_expired_inventory.py`'s timezone-fragile date construction — NOT DONE.** `tests/test_expired_inventory.py:42` still reads `TODAY = date.today()` directly. It happens to pass right now (16:35 local, well outside the fragile midnight–8am window — see Part 2), but the fragility itself hasn't been touched.

**Net: 0 of 6 fully done, 1 partially (and that one turned out to be more done than I'd previously given it credit for), the rest unchanged or worse.**

---

## Part 2 — Health Check

**Backend test suite**, run fresh just now: **90 passed, 0 failed**, 21 subtests, 75.6s. All 90 pass right now because it's 16:35 local — the same 6 tests that failed in the Sept 24 report (all in `test_expired_inventory.py`, all for the local-clock-vs-UTC-database reason documented there) would fail again between local midnight and ~8am, since that fragility (item 6 above) hasn't been fixed. This is not a new regression; it's the same clock-dependent flake, currently not triggering because of what time it is.

`npx tsc --noEmit`: **clean.**

Production build: **succeeds**, identical chunk sizes to Sept 24 (`vendor-charts` 415.84 kB/112.10 kB gzip, `vendor-leaflet` 160.48 kB/50.19 kB gzip, `vendor-react` 143.06 kB/45.83 kB gzip, main `index` 55.97 kB/14.79 kB gzip) — expected, since no frontend code has changed.

`npm audit`: **6 vulnerabilities — 0 critical, 5 high, 1 moderate, 0 low.** Identical package list to Sept 24 (`browserslist`, `nanoid`, `postcss`, `tar`, `baseline-browser-mapping`, and `vite` itself) — all build-tooling, none shipped to the production bundle or backend, unchanged.

**Deployed frontend and backend, both reachable and healthy, checked live just now:**
- `https://bloodlink-azure.vercel.app/` → 200, 0.19s.
- `https://bloodlink-azure.vercel.app/api/health` (the Vercel proxy path) → 200, 0.56s, `{"status":"ok","database":"connected",...}`.
- `https://bloodlink-backend-t688.onrender.com/health` (direct) → **200, 0.61s, this time.** On Sept 24 this same check timed out completely (21+ seconds, no response) from this same machine. It's healthy right now. **This means direct Render reachability is intermittent, not a permanent block** — consistent with an ISP-level or routing issue (PLDT was the specific symptom reported) rather than Render being down. The Vercel proxy is still the right fix regardless, since it makes the app work for the affected users no matter which way this intermittent behavior swings on any given day.
- The deployed bundle's content-hashed filename (`index-CnTkvTBe.js`) matches a fresh local build byte-for-byte-equivalent output exactly — confirms the live deployment matches current `HEAD`, nothing has drifted.

---

## Part 3 — Repo Inventory and Cleanup

Every file outside `src/` (the frontend source tree), classified. "Would break if run today" was checked by reading each script, not assumed from its name.

### Documentation

| File | What it is | Last touched | Recommendation |
|---|---|---|---|
| `README.md` | Standard project readme (npm i / npm run dev) | 2026-09-21 | KEEP. Has one stray `<!-- deploy test -->` HTML comment at the end, harmless leftover from testing the deploy pipeline — not urgent, but a one-line edit whenever this file is next touched. |
| `PRODUCT.md` | Structured product description (users, purpose) | 2026-09-21 | KEEP. Read it in full — accurate, matches everything I've independently verified about the app (facility-level accounts, admin-provisioned, no self-registration). |
| `DESIGN.md` | Design-system tokens (colors, etc.) | 2026-09-21 | KEEP. Matches the actual `theme.css` values I've cross-referenced elsewhere this project. |
| `DEPLOYMENT.md` | Deployment runbook | 2026-09-21 | KEEP, but **stale and should be updated, not deleted.** It documents `VITE_API_BASE_URL` as the mechanism that points the frontend at the backend — that's no longer how production works as of `786a1ad` (the Vercel `/api` proxy). It also doesn't mention `vercel.json` at all. Someone following this doc today to redeploy would set an env var that's now dead in production. Worth a 15-minute rewrite, not a cleanup-task deletion. |
| `ATTRIBUTIONS.md` | shadcn/ui + Unsplash license attributions | 2026-09-21 | KEEP as-is — this is a license-compliance file, not a working doc; leave it alone regardless of staleness. |
| `guidelines/Guidelines.md` | Figma Make template scaffold, literally never filled in (still reads "Add your own guidelines here" + the template's own instructions-for-filling-it-out) | 2026-09-21 | **DELETE.** Confirmed referenced nowhere (no `CLAUDE.md`, no config, no other file points at it) — it's inert template boilerplate, not documentation. |
| `BLOODLINK_STATUS_REPORT.md` | Old status doc | 2026-09-21 | Already self-marked `(ARCHIVED)` in its own first line, pointing readers to the maintained Claude Artifact instead. Not misleading as-is. Your call: DELETE (redundant with the artifact) or MOVE to an archive folder. Not urgent either way. |
| `BLOODLINK_STATUS_REPORT.archived-2026-07-23.md` | Verbatim backup of the above, from before this repo had git | 2026-09-21 | Same as above — was originally kept because "this project has no git repository... a real backup file is what actually keeps that text recoverable." That reasoning no longer applies (git history now does that job). Same recommendation: DELETE or MOVE, your call. |
| `server/SYNTHETIC_SARIMAX_VALIDATION.md` | Full Box-Jenkins identify/estimate/diagnose/cross-validate writeup for the synthetic model | 2026-09-21 | **KEEP — thesis evidence, not touched, per your instruction.** |
| `server/DENGUE_INDEX_DERIVATION.md` | This week's dengue-index investigation writeup | 2026-09-23 | **KEEP — thesis evidence, not touched, per your instruction.** |
| `STATUS_SEPT_24.md` | The previous status report | 2026-09-24, currently **untracked** (never committed) | Not part of the cleanup ask, but flagging: this file and this one aren't in git yet. Worth deciding now whether dated status reports live at the repo root indefinitely or move to something like `docs/status/` as more of them accumulate — see cleanup proposal below for an optional command. |

### Data

| File | What it is | Recommendation |
|---|---|---|
| `demo/demo_historical_stock.csv` | The demo facility's 150-day historical seed data | **KEEP — thesis evidence, per your instruction.** |
| `server/batangas_dengue_2016_2021_real.csv` | Real DOH dengue case-count data | **KEEP — thesis evidence, per your instruction.** |

### Backend scripts — the Box-Jenkins pipeline, AI selector, dengue investigation

All present, all unchanged since Sept 23-24, all correctly **not imported by `main.py`** (confirmed: `main.py` only imports `auth`, `email_service`, `database` at module level). Per your instruction, none of these are cleanup candidates regardless of "wired in" status — noting their state for completeness only:

- **Box-Jenkins pipeline** (`generate_synthetic_forecast_data.py`, `identify_synthetic_forecast_data.py`, `estimate_synthetic_forecast_models.py`, `diagnose_synthetic_forecast_models.py`, `crossvalidate_synthetic_forecast_model.py`, `reidentify_seasonal_synthetic_forecast_data.py`, `bounded_respecification_synthetic_forecast_data.py`) — confirmed no other file imports any of these.
- **AI selector** (`sarimax_selector_common.py`, `train_sarimax_selector.py`) — `train_sarimax_selector.py` imports `sarimax_selector_common`; `tests/test_sarimax_selector.py` imports it too. These two are linked to each other, not to anything live.
- **Dengue investigation** (`derive_dengue_season_index.py`, `batangas_dengue_2016_2021_real.csv`, `DENGUE_INDEX_DERIVATION.md`) — `tests/test_dengue_index_derivation.py` imports `derive_dengue_season_index`.

**On organizing these into a `research/` or `docs/` folder, since you asked**: I'd separate this into two answers, because they're not equally easy:

- The **documentation and data files** (`SYNTHETIC_SARIMAX_VALIDATION.md`, `DENGUE_INDEX_DERIVATION.md`, `batangas_dengue_2016_2021_real.csv`) have no import dependencies at all — moving them is a zero-risk `git mv`.
- The **Python scripts** are a different story. I checked: **every one of the 7 Box-Jenkins scripts, plus `fit_and_cache_synthetic_forecast.py` and `verify_synthetic_forecast_data.py`, does `from database import engine`** — a flat import that assumes `database.py` is a sibling file in the same directory. Moving any of them into a subfolder (e.g. `server/research/`) without `database.py` alongside would break that import the moment the script is run. Fixing it properly (so `server/database.py` stays put and shared, but the research scripts move) means either adding a small path shim to each moved file or restructuring `server/` into a proper Python package — both are code changes, not pure file moves, and cross into "fixing things," which you asked me not to do this pass. **My recommendation: organize the docs/data now (safe, see commands below), leave the Python scripts where they are for now, and treat moving them as its own small follow-up task if you want it** — not something to copy-paste blindly from a cleanup list.

### `create_*.py` / `migrate_*.py` / `seed_*.py` (18 files)

Checked every one for idempotency by reading it (not assuming from the name):
- The 9 `create_*.py` and 3 simple `migrate_*.py` files (`migrate_add_password_reset.py`, `migrate_add_upload_history.py`, `migrate_add_upload_undo.py`) are thin wrappers that execute a `schema_*.sql` file. Verified all 15 `schema_*.sql` files use `CREATE TABLE IF NOT EXISTS`. **All of these are safe to re-run today — no-ops if already applied.**
- The more complex `migrate_*.py` files (`migrate_add_admin_role.py`, `migrate_add_blood_unit_archived_at.py`, `migrate_add_facility_forecast_cache.py`, `migrate_add_facility_id.py`, `migrate_add_notifications.py`, `migrate_add_onboarding_columns.py`, `migrate_add_threshold_maximum.py`, `migrate_add_transfer_columns.py`, `migrate_scope_inventory_snapshots.py`, `migrate_scope_thresholds_to_facility.py`) all have explicit column/table-existence guards. **Safe to re-run today.**
- The 4 `seed_*.py` scripts guard with a row-count check (e.g. `seed_facilities.py:29-31`: `if count > 0: print("...skipping seed"); return`) rather than an `IF NOT EXISTS` pattern, but the effect is the same — **safe to re-run, they refuse to double-seed rather than erroring or duplicating.**

**None of the 18 are obsolete or would break if run today.** `migrate_add_threshold_maximum.py` vs. `migrate_scope_thresholds_to_facility.py` specifically (flagged as a pair worth double-checking in an earlier audit this project) — re-confirmed neither is obsolete; `migrate_scope_thresholds_to_facility.py`'s own backfill query depends on `maximum_units` already existing, so both still need to run in order on a from-scratch database.

**`fit_and_cache_synthetic_forecast.py` reachability**: still needed. Confirmed `main.py:962-1034`'s `_build_synthetic_stand_in_forecast` still reads the `synthetic_forecast_cache` table this script populates, and confirmed live in the database (Part 4) that **2 real facilities (St. Mary's, University Health) are on the `synthetic_model_stand_in` path right now** — this isn't a dead path, it's actively serving real facilities today.

### Repo size

Ten biggest tracked files — nothing alarming, no accidentally-committed binaries or build output:

```
185K  server/main.py
103K  package-lock.json
 62K  src/app/screens/Dashboard.tsx
 57K  src/app/screens/Requests.tsx
 32K  src/app/screens/Inventory.tsx
 26K  src/app/screens/Donors.tsx
 25K  src/styles/theme.css
 24K  server/SYNTHETIC_SARIMAX_VALIDATION.md
 23K  src/app/components/AccountMenu.tsx
 22K  demo/demo_historical_stock.csv
```

### `.gitignore`

Checked what's actually ignored on disk (`git status --ignored`) against what should be: `.env`/`.env.local`, `.venv/`, `__pycache__/`, `.pytest_cache/`, `dist/`, `node_modules/`, `.claude/`, `.impeccable/` are all correctly ignored, at both the repo root and inside `server/`. **Nothing missing that I could find.** `server/tmp/` (added to `.gitignore` this week for a diagnostic script's scratch output) is currently empty — no leftover files there either.

### `.env.example` vs. what the code actually reads

Extracted every `os.environ[...]` / `os.environ.get(...)` call from `main.py`, `auth.py`, `database.py`, `email_service.py`: `DATABASE_URL`, `JWT_SECRET`, `ALLOW_DEV_FACILITY_OVERRIDE`, `ALLOW_DEV_TEST_TOOLS`, `CORS_ALLOWED_ORIGINS`, `RESEND_API_KEY`, `RESEND_FROM_EMAIL`, `FRONTEND_BASE_URL` — **all 8 are documented in `server/.env.example`, nothing extra, nothing missing.** Same check on the frontend: `VITE_API_BASE_URL` is the only custom var read anywhere in `src/` (besides Vite's own built-in `import.meta.env.DEV`), and it's the only line in the root `.env.example`. **Both files are accurate and in sync with the code.**

### Proposed cleanup — exact commands, not run

```bash
# --- Safe, low-risk, no import dependencies ---

# Remove the never-filled-in Figma Make template scaffold; referenced nowhere.
git rm guidelines/Guidelines.md

# --- Your call, either is fine, neither is urgent ---

# Option A: delete the already-self-marked-retired status docs (redundant
# with the maintained Claude Artifact they both point to).
git rm BLOODLINK_STATUS_REPORT.md BLOODLINK_STATUS_REPORT.archived-2026-07-23.md

# Option B: archive them instead of deleting, if you'd rather keep the paper trail.
mkdir -p docs/archive
git mv BLOODLINK_STATUS_REPORT.md docs/archive/
git mv BLOODLINK_STATUS_REPORT.archived-2026-07-23.md docs/archive/

# Give the two dated audit reports (this one and Sept 24's) a home, now,
# before a third one makes the repo root noisy.
mkdir -p docs/status
git mv STATUS_SEPT_24.md docs/status/
git mv STATUS_SEPT_26.md docs/status/   # run this one separately, after this file is committed

# --- Organizational, zero risk (docs/data only, no Python import dependencies) ---

# Group the thesis-evidence documentation together. Purely a file move — KEEP
# unaffected, this just makes "this is research writeup, not live app code"
# visible at a glance.
mkdir -p server/research
git mv server/SYNTHETIC_SARIMAX_VALIDATION.md server/research/
git mv server/DENGUE_INDEX_DERIVATION.md server/research/
git mv server/batangas_dengue_2016_2021_real.csv server/research/

# --- NOT proposed as a command: moving the Box-Jenkins/.py research scripts ---
# Every one of them does `from database import engine`, a flat same-directory
# import. Moving them into server/research/ without also fixing that import
# (a code change, not a file move) would break them the next time anyone runs
# one. Recommend treating this as its own small follow-up task, not a blind
# copy-paste from this list — see Part 3's explanation above.
```

---

## Part 4 — Live Database State

**Facilities and logins**: 8 facilities, unchanged from Sept 24. **Still exactly 5 user accounts, still exactly the same facilities 2, 3, 4, and 6 with zero logins.**

**The two requests stuck by the Sept 24 walkthrough — checked directly, both still stuck, in exactly the state they were left in:**
- Request `id=44`... actually the one from the live walkthrough is `id=43` (Riverside → Metro Blood Alliance, O−, 3 units, created 2026-09-23 20:02 UTC): **status still `pending`.** Cannot be accepted, because nobody can log in as Metro Blood Alliance.
- Request `id=10` (University Health → Northside, O−, 3 units, created back in July, `supplier_confirmed_at` set 2026-09-23 20:11 UTC by my Sept 24 walkthrough): **status still `accepted`, `requester_confirmed_at` still `NULL`.** Cannot be completed, because nobody can log in as University Health to confirm receipt.

**Neither can be completed as things stand.** They will sit exactly like this indefinitely unless facility 4 or facility 6 gets a login account (item 1), someone with database access manually intervenes, or the requests are cancelled.

**Expired units, unarchived, per facility (worse than Sept 24 — see Part 1):**

| Facility | Expired, unarchived | Ever archived | Usable |
|---|---|---|---|
| Riverside General Hospital (1) | 299 | 0 | 93 |
| St. Mary's (2) | 238 | 0 | 214 |
| General Hospital Blood Bank (3) | 239 | 0 | 224 |
| University Health (4) | 250 | 0 | 236 |
| Northside (5) | 244 | 0 | 222 |
| Metro Blood Alliance (6) | 407 | 0 | 338 |
| Demo Blood Bank (158) | **8** | 0 | 573 |
| **Total** | **1,685** | **0** | — |

Worth flagging specifically: on Sept 24, facility 158 (the one used for the forecasting demo) had **zero** expired units and was the one clean facility. It now has 8. This will keep happening — units in its seed data cross their expiry date over real time regardless of whether anyone touches the app.

**Days of usable-stock history and `forecast_source`, per blood-bank facility, checked today:**

| Facility | Days | Latest snapshot | `forecast_source` |
|---|---|---|---|
| Demo Blood Bank (158) | 153 | 2026-09-24 | `sarimax_facility_history` |
| Northside (5) | 20 (+1 since Sept 24) | 2026-09-24 | `linear_trend` |
| Metro Blood Alliance (6) | 4 | 2026-09-01 (25d stale) | `linear_trend` |
| General Hospital Blood Bank (3) | 3 | 2026-09-01 (25d stale) | `linear_trend` |
| St. Mary's (2) | 2 | 2026-09-01 (25d stale) | `synthetic_model_stand_in` |
| University Health (4) | 2 | 2026-09-01 (25d stale) | `synthetic_model_stand_in` |

**`facility_forecast_cache` is stale right now**: 56 rows, all facility 158, `trained_through_date = 2026-09-24`, but the database's own `CURRENT_DATE` is `2026-09-26` — a 2-day gap. This isn't a bug, it's expected self-healing behavior: `_get_or_fit_cached_sarimax` (`main.py`) only treats the cache as fresh when `trained_through_date == today`, so the next time anyone loads the Demo Blood Bank's dashboard, it will detect the staleness and refit automatically. Nobody has opened that account's dashboard since Sept 24, which is also why `inventory_snapshots` for facility 158 has no rows for Sept 25 or 26 either — the two facts are the same underlying cause.

**What would look wrong on screen during a demo, all confirmed live, all unchanged in kind from Sept 24 (just larger numbers in one case):**
- Any real facility's Inventory screen will show a large, red "N expired — needs clearing" banner (Riverside's specific count would now read 299, not 245).
- The stuck request at facility 6 (Metro Blood Alliance) would show as permanently "pending" in Riverside's Requests view if that account is used to demo the round-trip — same silent trap as before.
- The stuck "accepted, awaiting confirmation" request at facility 4 shows up in Northside's outgoing-request history if that account is opened.
- Facility 158's forecast will re-fit (a brief loading moment) the first time anyone opens its dashboard after this report, since the cache is now 2 days stale — not an error, just a beat of "Loading forecast…" that wasn't there on Sept 24 right after a fresh fit.

---

## Part 5 — What I Want to Know

**1. Since the deploy fix, has anything regressed? Any 500s, failed deploys, or errors in the logs?**
Partially answerable, partially not. What I *can* confirm: zero commits have landed since the fix, so nothing has had a chance to regress via a code change; both the direct Render URL and the Vercel proxy path are live and healthy right now; the deployed bundle matches current `HEAD` exactly. What I *can't* confirm: **UNKNOWN — could not verify actual Render/Vercel runtime logs for the two-day gap.** I have no dashboard access to either platform from here. If you want a real answer to "were there any 500s," that needs either Render's log viewer or an access token I don't have.

**2. Is the Vercel `/api` proxy still the only path the frontend uses, or has anything started calling Render directly again?**
Still the only path — confirmed by re-reading `src/app/lib/api.ts:16` (`import.meta.env.DEV ? import.meta.env.VITE_API_BASE_URL : "/api"`, unchanged) and by grepping the entire `src/` tree for the literal string `bloodlink-backend-t688` — the only hit is inside a comment explaining *why* the proxy exists, not a URL anything actually calls.

**3. What's the single most fragile thing in the codebase right now — most likely to break during a live demo?**
Not a code bug — it's the **facility/account pairing dependency**, and it's fragile precisely because it fails *silently*. Nothing errors, nothing looks broken on screen; a request just sits at "pending" forever if it's sent to one of the 4 facilities with no login, and the UI gives no indication that's what happened versus the supplier simply not having responded yet. I hit this by accident on my first try during the Sept 24 walkthrough, clicking the nearest-suggested facility without checking. The forecast/SARIMAX view has the same shape of fragility one level down: it only looks like "real SARIMAX" for one specific account (`demo.forecast@example.com`); every other blood-bank login shows an honestly-labeled but visually less impressive fallback, with no error, just a quieter result. Both failure modes are about **which account gets used**, not about anything actually being broken.

**4. Is there anything in the repo that would embarrass us if a panelist read it?**
Checked directly: zero `console.log`/`console.debug` in the frontend; the one `print()` in `main.py` (line 3178) is a deliberate error-log fallback for a caught email-send failure, not debug leftover; zero TODO/FIXME/XXX/HACK comments describing unfinished work (the one "TODO" hit anywhere in tracked source, `server/seed_facility_inventory.py:19`, is a comment *describing a past, already-resolved TODO*, not a current one); zero placeholder/lorem-ipsum/"coming soon" text anywhere in the frontend. This codebase is genuinely clean of debug cruft. **One thing worth your attention, though, not code-related**: the `Testing` facility (id 43, still `is_active = true`) has a real-looking personal email attached to its one user account — `akosidux2000@gmail.com`, not a `demo.*@example.com`-style placeholder like every other account. If a panelist opens the Admin screen's facility/account list, that's the one row that reads as "someone's personal account got left in a system that's supposed to look production-ready," not as an intentional demo account. Worth deciding whether to deactivate that facility or replace the email before anyone but you looks at the Admin screen.

**5. What did you ask for that turned out to be a bad idea, and what would I have done differently?**
Nothing you asked for was a bad idea in isolation — the audits, the dengue investigation, the performance work, the light-mode default, the deploy-routing fix were all reasonable, well-scoped requests and all were genuinely useful. But stepping back at the level of *sequencing*: this is the **third** full-repo audit in five days (Sept 22, Sept 24, now Sept 26), and in the gap between the second and third, **none of the specific, already-identified, mostly-small fixes got made** — three of the six items from Sept 24 (rate limiting, the `/facilities` auth gap + CORS default, the test file's clock dependency) are each genuinely small, low-risk, well-understood changes at this point, not open research questions. If I'd been steering, I would have suggested spending the two days between reports *fixing* those three specifically, then auditing again to confirm they landed cleanly — rather than a third from-scratch verification pass that mostly re-confirms the same gaps a second time with fresh numbers. None of that is a criticism of any single request; it's an observation about the pattern across requests, and it's the one thing I'd actually change if I were choosing what happens next.

---

## Open Questions for Euhan

- **Facilities 2, 3, 4, 6 still have no logins, and it's now blocking two real stuck requests, not just a hypothetical.** Is creating at least one more blood-bank account (ideally two, so a round-trip demo has a backup) something you want done now, given the defense timeline? This is the highest-leverage single fix available and doesn't require touching any code — it's one `POST /admin/facilities` call per account.
- **Same question I asked Sept 24, still open**: is any of the 24-row `requests` history (dominated by `cancelled`, 14 of 24) meant to be part of the demo narrative, or is it all incidental and safe to mentally discount? Two more rows were added by both walkthroughs now.
- **The expired-unit backlog (1,685, growing) — clear it before the defense, or keep it as a deliberate "look, real problem, real one-click fix" talking point?** Same question as last time; it's larger now, which makes the decision slightly more time-sensitive if the answer is "clear it."
- **Do you want `ALLOW_DEV_TEST_TOOLS`'s actual production value checked?** `DEPLOYMENT.md` documents the *intended* setting as `true` for this round of deployment, but that's a doc, not a live check — I still haven't verified it directly, because doing so safely would mean either getting Render dashboard access or accepting a live write to the production donor-blast tables to test it. Same as Sept 24: your call on whether that trade-off is worth it.
- **`akosidux2000@gmail.com` on the still-active `Testing` facility** — deactivate it, replace the email, or leave it? Small, but easy to fix in under a minute via the Admin screen once you decide.
- **The Box-Jenkins/.py research scripts' `database.py` import coupling** — worth a small dedicated task to move them into `server/research/` properly (with the import fixed), or fine to leave them flat in `server/` alongside the live app code indefinitely? Not urgent either way, just flagging it's a real (small) piece of work if you want the reorganization, not a free move.
- **Given Part 5, Q5 — do you want the next session to be a fix session instead of another audit?** I'd genuinely recommend it at this point: the gaps are well-understood and mostly small; another audit right now would likely just reconfirm the same list a third time.
