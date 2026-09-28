-- Two purposes share this table rather than getting one each: they're the
-- same shape (a 6-digit code, hashed, expiring, single-use, attempt-capped),
-- just bound to a different kind of row while no users row exists yet
-- (registration email verification) versus after one does (password reset).
-- Only the code's SHA-256 hash is ever stored — never the raw code — same
-- reasoning as password_reset_requests.token_hash.
--
-- registration_request_id has no FK yet: facility_registration_requests is
-- added in a later migration (migrate_add_facility_registration_requests.py),
-- which also adds the FK constraint this column is missing until then.
CREATE TABLE IF NOT EXISTS otp_codes (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    purpose text NOT NULL,
    registration_request_id bigint,
    user_id bigint REFERENCES users(id),
    email text NOT NULL,
    code_hash text NOT NULL,
    attempts integer NOT NULL DEFAULT 0,
    expires_at timestamptz NOT NULL,
    used_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT otp_codes_purpose_valid CHECK (purpose IN ('registration_email_verify', 'password_reset')),
    -- Exactly one of registration_request_id/user_id is set, matching purpose
    -- — a registration-email-verify code has no user yet, a password-reset
    -- code has no pending registration.
    CONSTRAINT otp_codes_binding_matches_purpose CHECK (
        (purpose = 'registration_email_verify' AND registration_request_id IS NOT NULL AND user_id IS NULL)
        OR
        (purpose = 'password_reset' AND user_id IS NOT NULL AND registration_request_id IS NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_otp_codes_registration_request
    ON otp_codes (registration_request_id) WHERE registration_request_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_otp_codes_user
    ON otp_codes (user_id) WHERE user_id IS NOT NULL;
