-- Pending self-registration. Deliberately NOT facilities with is_active=false
-- — see the investigation notes this migration follows from: GET /facilities,
-- GET /facilities/nearby and POST /requests would all need to additionally
-- exclude "pending" rows on top of is_active, and at least one of those
-- (POST /requests, pre-fix) didn't even check is_active at all. A separate
-- table means nothing unapproved can ever be visible to search, listing, or
-- request routing, structurally, not by remembering to filter it out.
--
-- latitude/longitude are nullable at the column level (a submission could in
-- principle be stored before the map step, though the API layer requires
-- them) — same nullability reasoning as facilities.latitude/longitude.
--
-- Only one live (non-archived) request per email — a resubmission with the
-- same email while the prior one is still submitted/email_verified updates
-- that row in place (see POST /facilities/register) rather than piling up
-- duplicates; a resubmission after rejection or approval (both archived) is
-- a fresh row.
CREATE TABLE IF NOT EXISTS facility_registration_requests (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    facility_name text NOT NULL,
    facility_type text NOT NULL,
    address text NOT NULL,
    latitude double precision,
    longitude double precision,
    doh_license_number text NOT NULL,
    contact_person text NOT NULL,
    email text NOT NULL,
    phone text NOT NULL,
    password_hash text NOT NULL,
    status text NOT NULL DEFAULT 'submitted',
    rejection_reason text,
    reviewed_by bigint REFERENCES users(id),
    reviewed_at timestamptz,
    archived_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT facility_registration_requests_type_valid CHECK (facility_type IN ('hospital', 'bloodbank')),
    CONSTRAINT facility_registration_requests_status_valid
        CHECK (status IN ('submitted', 'email_verified', 'approved', 'rejected'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_facility_registration_requests_email_live
    ON facility_registration_requests (email) WHERE archived_at IS NULL;

-- Backs the admin queue: WHERE status = 'email_verified' AND archived_at IS NULL.
CREATE INDEX IF NOT EXISTS idx_facility_registration_requests_queue
    ON facility_registration_requests (status) WHERE archived_at IS NULL;
