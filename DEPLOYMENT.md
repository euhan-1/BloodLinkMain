# Deploying for group testing (not production)

Reference for the actual steps once you're ready to deploy — written so nothing here has to be re-derived later. Covers the specific choices made for this round: `ALLOW_DEV_FACILITY_OVERRIDE=false`, `ALLOW_DEV_TEST_TOOLS=true`, `CORS_ALLOWED_ORIGINS` starting at `*` until the real frontend URL exists.

## The real blocker: backend and frontend URLs depend on each other

Neither URL exists yet — nothing is deployed. That creates a real ordering constraint, not just a preference:

1. **Deploy the backend first**, with `CORS_ALLOWED_ORIGINS` left unset (defaults to `*`) since the frontend's URL isn't known yet. This gives you a real backend URL.
2. **Set `VITE_API_BASE_URL` to that backend URL** and deploy the frontend. This gives you a real frontend URL.
3. **Go back and set `CORS_ALLOWED_ORIGINS` on the backend to that real frontend URL**, then restart the backend service (most platforms don't need a full redeploy for an env var change, just a restart).

Skipping step 3 isn't a functional blocker for a group test (`*` keeps working fine — see `server/main.py`'s `CORS_ALLOWED_ORIGINS` comment for why that's safe here specifically), but it's the difference between "temporary" and "just never tightened it."

## Backend — environment variables to set on the host

Set as real environment variables in the hosting platform's dashboard. Never as a committed file — `server/.env` stays local-only and gitignored.

| Variable | Value | Source |
|---|---|---|
| `DATABASE_URL` | same value as local `server/.env` | copy by hand from your local file — it's the same Supabase instance either way |
| `JWT_SECRET` | same value as local `server/.env`, or a freshly generated one | `python -c "import secrets; print(secrets.token_hex(32))"` if you'd rather rotate it for this deployment |
| `ALLOW_DEV_FACILITY_OVERRIDE` | `false` | must be false — this is a full auth bypass if left on anywhere reachable |
| `ALLOW_DEV_TEST_TOOLS` | `true` | deliberate choice for this round, so donor-blast reply simulation stays testable |
| `CORS_ALLOWED_ORIGINS` | unset at first, then the real frontend URL once deployed (step 3 above) | e.g. `https://your-app.vercel.app` |

## Backend — start command

Local dev has been using `uvicorn main:app --host 127.0.0.1 --port 8000` — that exact command will not work on a real host. Use:

```
uvicorn main:app --host 0.0.0.0 --port $PORT
```

`--host 0.0.0.0` so the process accepts connections from outside its own container (`127.0.0.1` only accepts connections from itself). `$PORT` because most platforms (Render, Railway) inject the actual port to listen on via that env var rather than letting you pick a fixed one — the shell expands `$PORT` when the platform runs this as your configured start command.

## Frontend — build-time variable

`VITE_API_BASE_URL` is read once, at build time (`src/app/lib/api.ts`) — not at runtime. Set it as a build-time environment variable in Vercel's/Netlify's dashboard, pointing at the real backend URL from step 1. Local `.env.local` stays pointed at `http://localhost:8000` for local dev and is untouched by this — the platform's own build-time env var takes precedence for the deployed build, it doesn't read your local file at all.

## After both are live

Live-verify the same way every round of work in this project has been verified — not just "it built":
- Load the deployed frontend URL, confirm the login screen renders (proves the build succeeded and isn't silently pointing at `undefined` or the old localhost value).
- Log in with a real account, confirm a real API call succeeds (proves CORS and `VITE_API_BASE_URL` are both actually correct, not just configured).
- Check the browser console for CORS errors specifically — the most common failure mode here is a mismatched origin that only shows up as a browser-blocked request, not a server-side error.
