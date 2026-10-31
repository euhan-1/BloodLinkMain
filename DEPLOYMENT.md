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
| `CORS_ALLOWED_ORIGINS` | the deployed frontend origin, comma-separated if more than one | **Required. Unset or empty means no origin is allowed: it fails closed and is never `*`.** Production browsers reach the backend through the Vercel rewrite, so they send same-origin requests; the setting still matters for local development and any direct call. `GET /health` reports the list the server parsed. |
| `FRONTEND_BASE_URL` | the frontend's public URL | Used to build the link in password-reset emails. **If unset it defaults to `http://localhost:5173`, so reset links would point at localhost.** |
| `ADMIN_EMAIL` | the address that should hear about new registrations | The "a registration is waiting for review" notice goes here. If unset only that one email fails (logged); the admin queue itself is unaffected. |
| `CRON_SECRET` | a long random value | Shared secret for the nightly snapshot job (below). While unset, the job's endpoint returns 503. |
| `ALLOW_DEV_FACILITY_OVERRIDE` | `false` | **Must be false in production.** When true, any request carrying an `X-Dev-Facility-Id` header acts as that facility with no login at all: a full authentication bypass. |
| `ALLOW_DEV_TEST_TOOLS` | `false` | **Must be false in production** (it is the default). When true it enables two development endpoints for donor outreach: `POST /donors/blasts/{id}/simulate-reply`, which records a donor reply (the only way a reply is ever recorded, since no SMS provider is connected), and `POST /donors/blasts/{id}/dev-force-expire`, which forces a blast's deadline into the past. Both still require a signed-in blood-bank account and act on that facility's own blasts. When false, both return 403 "dev test tools are disabled on this server". |

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
- **History:** the first successful run was **2026-10-27 23:30:18 Manila**. Before that, a snapshot was written only when a blood bank loaded its forecast or uploaded history.
- **If it stops running,** the missing days are unobserved, and a forecast carries the last recorded value forward over them. That cannot be repaired afterwards: October 1–25, 2026 is such a gap for every facility, caused by the job not existing before 2026-10-27 combined with upload 605's undo on 2026-09-30.

## After a deploy

Verify it live, not just "it built":

- Load the deployed frontend and confirm the login screen renders.
- Sign in with a real account and confirm a real API call succeeds (this proves the rewrite and the backend are connected).
- Open `GET /health`: it should report the database as connected and the CORS origins you intended.
- Confirm the two dev flags are false: as a blood-bank account, `simulate-reply` and `dev-force-expire` should return 403.
- Check the browser console for CORS errors, the most common failure mode for a mismatched origin.
