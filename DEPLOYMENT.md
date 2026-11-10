# Deployment and operations reference

How BloodLink is deployed and what it needs from its environment. Environment variables appear **by name only**: values live in the hosting dashboards and local `.env` files, never in the repository.

## Topology

- **Frontend:** a Vite build on Vercel. `vercel.json` rewrites `/api/:path*` to the Render backend and every other path to `index.html`. A production build calls the relative path `/api` (`src/app/lib/api.ts`); `VITE_API_BASE_URL` is read only by the local dev server. Vercel's rewrite has a **120-second ceiling**, so no request may approach it.
- **Backend:** FastAPI on Render's free tier.
- **Database:** Supabase Postgres, reached through `DATABASE_URL` (it must start with `postgresql+psycopg2://`). Development and production share this one database, so a local script that writes to it writes to live data. Three standalone scripts that insert or delete rows (`seed_facility_inventory.py`, `generate_synthetic_forecast_data.py`, `fit_and_cache_synthetic_forecast.py`) refuse to run without `--i-know-this-writes-to-production`; others, such as `select_orders.py`, do not, so treat any local script as a production write.

## Render free-tier limits

- **512 MB of memory and a throttled CPU.** scikit-learn and joblib would exhaust the memory, so production installs `server/requirements.txt` only. `requirements-dev.txt` is for local work.
- **The instance sleeps after about 15 minutes idle**, and the next request pays a cold start. UptimeRobot pings the backend to limit this (`GET /health` is the lightweight endpoint for it).
- **One SARIMAX fit takes about 12–20 s deployed** (about 0.3 s locally). A forecast request therefore fits at most 2 blood types and reports the rest in `pending_types`, which the dashboard polls.
- **Two database pools.** The normal engine (up to 15 connections) and a small read engine (up to 8) for endpoints that only read. Both need the Supabase pooler in **session mode** (port 5432): the read engine marks each session `default_transaction_read_only`, which a transaction-mode pooler would not keep, and the forecast's advisory lock is session-scoped. Supabase reports `max_connections = 60`; the pooler's own per-user limit is not visible from here, so if you lower it, keep it above 23.
- **Outbound SMTP is blocked**, so email goes out through an HTTP provider (see below).
- The rest of the service configuration (Python version, build command, root directory) is set in the Render dashboard and is not recorded in this repository.

## Backend start command

From `server/`:

```
uvicorn main:app --host 0.0.0.0 --port $PORT
```

`0.0.0.0` so the process accepts connections from outside its container; `$PORT` because Render injects the port to listen on.

## Backend environment variables

| Variable | Set to | Notes |
|---|---|---|
| `DATABASE_URL` | the Supabase connection string | `postgresql+psycopg2://…` |
| `JWT_SECRET` | a long random value | `python -c "import secrets; print(secrets.token_hex(32))"`. Changing it signs everyone out. |
| `CORS_ALLOWED_ORIGINS` | the deployed frontend origin, comma-separated if more than one | **Required. Unset or empty means no origin is allowed: it fails closed and is never `*`.** Production browsers reach the backend through the Vercel rewrite, so they send same-origin requests; the setting still matters for local development and any direct call. The parsed list is no longer exposed by `GET /health`; check it with a preflight (see After a deploy). |
| `FRONTEND_BASE_URL` | the frontend's public URL | Used to build the link in password-reset emails. **If unset it defaults to `http://localhost:5173`, so reset links would point at localhost.** |
| `ADMIN_EMAIL` | the address that should hear about new registrations | The "a registration is waiting for review" notice goes here. If unset only that one email fails (logged); the admin queue itself is unaffected. |
| `CRON_SECRET` | a long random value | Shared secret for the nightly snapshot job (below). While unset, the job's endpoint returns 503. |
| `ALLOW_DEV_FACILITY_OVERRIDE` | `false` | **Must be false in production.** When true, any request carrying an `X-Dev-Facility-Id` header acts as that facility with no login at all: a full authentication bypass. |
| `ALLOW_DEV_TEST_TOOLS` | `false` | **Must be false in production** (it is the default). When true it enables two development endpoints for donor outreach: `POST /donors/blasts/{id}/simulate-reply`, which records a donor reply (the only way a reply is ever recorded, since no SMS provider is connected), and `POST /donors/blasts/{id}/dev-force-expire`, which forces a blast's deadline into the past. Both still require a signed-in blood-bank account and act on that facility's own blasts. When false, both return 403 "dev test tools are disabled on this server". |
| `AUTO_ORDER_SELECTION_AFTER_UPLOAD` | `false` (the default) | When `true`, every successful `POST /forecast/historical-upload` starts SARIMAX order selection for up to 2 of the facility's blood types in a background task inside the web process. **Leave it false in production.** Each run upserts `facility_sarimax_order` (overwriting an existing order and its soundness baseline), deletes that type's cached forecast, and runs 72 fits per type on a 512 MB throttled instance (estimated at tens of minutes; never timed deployed). Order selection is offline work: `server/select_orders.py`, run by hand after dumping the facility's rows. When false the upload still succeeds and logs `[order-selection] skipped after upload for facility N`. |
| `WARM_FORECASTS_AFTER_SNAPSHOT` | `false` (the default) | When `true`, `POST /internal/daily-snapshot` also schedules a background task, after it has answered the cron call, that loads the forecast of every active blood bank with 30 or more recorded days through the same `get_forecast` code a dashboard load uses (one bank after another, at most 2 fits per call, with a pause between calls) so that bank's first load of the day is warm. The cron response is unchanged. See "The nightly snapshot job": **it only helps if the job also runs after midnight Manila**, because the cache is valid for one business date. It runs no order selection. It can announce a forecast shortage notification at night that the bank's first load would otherwise have announced (once, same text). |
| `ENABLE_API_DOCS` | `false` (the default) | When `true`, serves `/docs`, `/redoc` and `/openapi.json`, which list every route including `/admin/*` and the dev-only tools. **Leave it false in production**; set it in a local `server/.env` for development. |
| `MAX_UPLOAD_BYTES` | `5242880` (5 MB, the default) | The largest CSV the inventory and historical uploads accept. A bigger request is refused with 413 before its body is read (by `Content-Length`) or as soon as the running total passes the limit. Not applied to the donor upload. |
| `NOMINATIM_USER_AGENT` | unset (the default names the project's public repository) | The `User-Agent` sent to OpenStreetMap's Nominatim for the registration form's address lookup, as its usage policy requires. Set it to add a contact address of your own, e.g. `BloodLink/1.0 (mailto:you@example.org)`. |
| `SERVER_TIMING` | `true` (the default) | Adds a `Server-Timing: app;dur=…, db;dur=…` header to every response, so DevTools' Network panel can tell server time from network time. Durations in milliseconds only: no query text, counts or paths. `db` is statement time only; connection checkout, `BEGIN` and `COMMIT` are in `app` but not `db`. Set `false` to remove it. |

### Email

`EMAIL_PROVIDER` picks the provider: `brevo` (the default, and what production uses), `resend` or `smtp`. Any other value, or none, means Brevo. Without a working provider every email endpoint still returns its normal response and logs a send failure instead of delivering.

| Provider | Variables |
|---|---|
| Brevo (live) | `BREVO_API_KEY`, `MAIL_FROM`, `MAIL_FROM_NAME` |
| Resend (alternative) | `RESEND_API_KEY`, `RESEND_FROM_EMAIL`. Its shared sandbox sender delivers only to the Resend account's own address until a domain is verified. |
| SMTP (alternative; local use) | `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `MAIL_FROM`, `MAIL_FROM_NAME`. Does not work from Render. |

## Frontend variable

`VITE_API_BASE_URL` is read only by the local dev server (`.env.local`, pointing at the local backend). Production builds ignore it and use `/api`.

## The nightly snapshot job

`POST /internal/daily-snapshot` records today's stock for **every active facility, hospitals included**, writing an explicit 0 for a blood type with none in stock. It fits no forecast. After the snapshot commits it also **archives every active facility's expired units** (`archived_at` is set, nothing is deleted; a unit still reserved for a request is skipped; a unit expiring today stays until tomorrow's run). It is safe to retry: a second run the same day archives nothing. The response is the cron history's record of what it did: `snapshot_date`, `facilities`, `rows_inserted`, `rows_updated`, `archived_expired` (`total` and `by_facility`, every active facility listed, 0 meaning none found) and `expired_still_reserved` (expired units left because a request holds them). If the archive step fails the call returns 500 after the snapshot has already been saved, so a retry finishes it. **The first run after this change archives each facility's whole existing expired backlog at once.**

- **Scheduler: [cron-job.org](https://cron-job.org).** Nothing else in the repository names it, and this job is what keeps the forecast series continuous, so record any change to it here. The job lives in the project's cron-job.org account (ask the project owner for access).
- **Schedule:** daily at **23:30, time zone Asia/Manila (15:30 UTC)**.
- **Request:** a `POST` to `/internal/daily-snapshot` on the backend, with the header **`X-Cron-Secret`**. The secret's value is `CRON_SECRET`, which lives in Render's environment; the job's header must carry the same value, set in the cron-job.org job and never recorded in the repository. The endpoint returns 401 for a wrong secret and 503 while `CRON_SECRET` is unset.
- **Timeout: 90 seconds.** Render's free tier sleeps when idle and a cold start takes about 50 seconds, so a shorter timeout would count a successful wake-up as a failure.
- **Warming the forecast cache (optional, `WARM_FORECASTS_AFTER_SNAPSHOT=true`).** The forecast cache is valid for one Manila date, so a warm-up at 23:30 prepares a day that is about to end and the first dashboard load after midnight is cold again (12-20 s per blood type deployed, two types per call). To warm the new day, add a **second cron-job.org entry** for the same request (same `POST`, same `X-Cron-Secret` header) at about **00:10 Manila (16:10 UTC the day before)**. The call is idempotent (it upserts the new date's snapshot from the live stock and archives anything that expired at midnight), so nothing is lost; the 23:30 run is still the one that records the end of the day. The warm-up runs in the background after the response and takes a few minutes on this instance (est. 8 types x 12-20 s per bank, one fit at a time); its log lines start with `[warm]`. Memory (measured locally on Windows, so only a guide for Render): the process went from 43 MB to about 180 MB once pandas, scipy and statsmodels were imported by the first fit, the same cost a user's first forecast load pays, and the eight fits added about 13 MB more (peak 192 MB). It works one bank and one fit at a time, so it does not grow with the number of banks.
- **History:** the first successful run was **2026-10-27 23:30:18 Manila**. Before that, a snapshot was written only when a blood bank loaded its forecast or uploaded history.
- **If it stops running,** the missing days are unobserved, and a forecast carries the last recorded value forward over them. That cannot be repaired afterwards: October 1–25, 2026 is such a gap for every facility, caused by the job not existing before 2026-10-27 combined with upload 605's undo on 2026-09-30.

## After a deploy

Verify it live, not just "it built":

- Load the deployed frontend and confirm the login screen renders.
- Sign in with a real account and confirm a real API call succeeds (this proves the rewrite and the backend are connected).
- Open `GET /health`: it should answer `{"status":"ok"}`; it is a bare 503 with no body if the database is down (the reason is in Render's log, not the response). It no longer lists the CORS origins: to check them, send a preflight from the frontend origin and from another one (`curl -i -X OPTIONS -H "Origin: <origin>" -H "Access-Control-Request-Method: GET" <backend>/facilities`): only the real origin gets `access-control-allow-origin`.
- Confirm `GET /docs` and `GET /openapi.json` return 404.
- Confirm the two dev flags are false: as a blood-bank account, `simulate-reply` and `dev-force-expire` should return 403.
- Check the browser console for CORS errors, the most common failure mode for a mismatched origin.
