-- Depends on users (schema_users.sql) existing first.
--
-- Only the token's SHA-256 hash is ever stored — never the raw token — so a
-- database read (backup, dump, compromised replica) can't be used to reset
-- anyone's password. The raw token exists only in memory for the duration of
-- one request and in the one email it's sent in.
CREATE TABLE IF NOT EXISTS password_reset_requests (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id bigint NOT NULL REFERENCES users(id),
    token_hash text NOT NULL UNIQUE,
    expires_at timestamptz NOT NULL,
    used_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_password_reset_requests_token_hash ON password_reset_requests(token_hash);
