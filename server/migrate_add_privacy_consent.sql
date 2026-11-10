-- migrate_add_privacy_consent.sql
--
-- NOT RUN. Written for you to review and run yourself (Supabase SQL editor). Nothing in the repository executes this file against
-- production; the test suite runs it against the local scratch database only, to prove it is idempotent.
--
-- Adds the record of consent to the Privacy Notice (RA 10173): which version of the notice a person agreed to, and when.
--   facility_registration_requests  what was agreed on the Register form
--   users                           copied from the registration at approval (admin_approve_registration)
-- Four nullable columns, no default, no backfill. NULL means "not recorded", never "refused": every account that exists today, and every
-- account an administrator creates without the Register form, stays NULL until it acknowledges the notice (not built yet, see
-- docs/BACKLOG.md).
--
-- Safe to run more than once (IF NOT EXISTS). Adding a nullable column with no default is a metadata-only change in Postgres 11+: it does
-- not rewrite the table and takes only a brief lock. Both tables are small (users 9 rows, facility_registration_requests 4 rows,
-- measured 2026-11-10).
--
-- ORDER: run this BEFORE deploying the code that records consent. The new code also works without it (it registers people and logs
-- "[consent] the privacy-consent columns are missing ... consent is NOT being recorded"), but any registration made in that gap has no
-- consent on file and would have to be asked again.
--
-- Timestamps are timestamptz, like every other *_at column in these tables.

ALTER TABLE public.facility_registration_requests
    ADD COLUMN IF NOT EXISTS privacy_consent_version text,
    ADD COLUMN IF NOT EXISTS privacy_consented_at timestamp with time zone;

ALTER TABLE public.users
    ADD COLUMN IF NOT EXISTS privacy_consent_version text,
    ADD COLUMN IF NOT EXISTS privacy_consented_at timestamp with time zone;

-- CHECK (read-only; expect four rows):
-- SELECT table_name, column_name, data_type FROM information_schema.columns
-- WHERE table_schema = 'public' AND column_name IN ('privacy_consent_version', 'privacy_consented_at') ORDER BY 1, 2;

-- ROLLBACK (drops the consent record; nothing else depends on the columns):
-- ALTER TABLE public.users DROP COLUMN IF EXISTS privacy_consent_version, DROP COLUMN IF EXISTS privacy_consented_at;
-- ALTER TABLE public.facility_registration_requests DROP COLUMN IF EXISTS privacy_consent_version, DROP COLUMN IF EXISTS privacy_consented_at;
