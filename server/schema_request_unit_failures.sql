-- One row per reserved unit that could not be received because it expired
-- between confirm-release and confirm-receipt (see main.confirm_receipt).
-- The unit itself is kept (archived at the supplier, never deleted); din and
-- expires_date are recorded as they stood at the moment receipt was refused.
CREATE TABLE IF NOT EXISTS request_unit_failures (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    request_id bigint NOT NULL REFERENCES requests(id),
    blood_unit_id bigint NOT NULL REFERENCES blood_units(id),
    din text NOT NULL,
    expires_date date NOT NULL,
    reason text NOT NULL CHECK (reason = 'expired_before_receipt'),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (request_id, blood_unit_id)
);

CREATE INDEX IF NOT EXISTS idx_request_unit_failures_unit ON request_unit_failures (blood_unit_id);
