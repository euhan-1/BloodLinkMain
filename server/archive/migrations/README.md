# Archived schema scripts

Historical records of schema changes that have **already been applied** to the
database (which is production — see the repo-root CLAUDE.md). They are kept so
the history of the schema is readable. **Do not re-run them.**

The current schema is `server/tests/schema.sql` (a `pg_dump --schema-only` of
production) and the `server/schema_*.sql` files that stayed in `server/`.

## Why they no longer run from here

They were moved here unmodified from `server/`, and two things in them assume
they still live there:

- `from database import engine` — `database.py` is in `server/`.
- Most of the `create_*` / `migrate_*` wrappers read their SQL with
  `Path(__file__).parent / "schema_*.sql"`, i.e. the file next to the script.
  Those `.sql` files are still in `server/`, not here.

So `python server/archive/migrations/<script>.py` fails with `ModuleNotFoundError`
(or `FileNotFoundError` on the `.sql`).

## If one ever had to be run

1. Read it first and check it against the live schema. Several are one-shot
   backfills, and `schema_facility_sarimax_order.sql` does a `DROP TABLE`.
2. Copy it (do not move it) next to `database.py`, run it from `server/` with
   the venv, then delete the copy:

   ```
   cd server
   copy archive\migrations\<script>.py .
   .venv\Scripts\python.exe <script>.py
   del <script>.py
   ```

   `DATABASE_URL` in `server/.env` is production. Take a backup first.
