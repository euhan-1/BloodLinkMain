-- address/latitude/longitude are nullable: an admin-onboarded facility (see
-- POST /admin/facilities in main.py) starts with just a name, type, and
-- login — location, department, and DOH license get filled in by the
-- facility itself via the post-first-login "complete your profile" flow
-- (POST /facilities/profile), which also flips profile_completed to true.
-- Any code that ranks/uses facility location (e.g. Emergency Sourcing's
-- distance search) must filter out rows where these are still NULL.
CREATE TABLE IF NOT EXISTS facilities (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name text NOT NULL,
    facility_type text NOT NULL,
    address text,
    latitude double precision,
    longitude double precision,
    department text,
    doh_license_number text,
    profile_completed boolean NOT NULL DEFAULT false,
    -- Set false by an admin via PATCH /admin/facilities/{id}. A deactivated
    -- facility's accounts can't log in (checked in POST /auth/login) until
    -- reactivated — this is the only place account access can be revoked,
    -- since there's no per-user active flag.
    is_active boolean NOT NULL DEFAULT true
);
