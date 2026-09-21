-- Depends on facilities (schema_facilities.sql) existing first — run that before this on a fresh install.
-- reserved_for_request_id references requests (schema_requests.sql) — also run that first.
CREATE TABLE IF NOT EXISTS blood_units (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    din text NOT NULL UNIQUE,
    blood_type text NOT NULL,
    component text NOT NULL,
    location text NOT NULL,
    volume_ml integer NOT NULL,
    collected_date date NOT NULL,
    expires_date date NOT NULL,
    facility_id bigint NOT NULL REFERENCES facilities(id),
    reserved_for_request_id bigint REFERENCES requests(id),
    created_at timestamptz NOT NULL DEFAULT now()
);
