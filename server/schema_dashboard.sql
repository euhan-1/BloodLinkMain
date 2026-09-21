CREATE TABLE IF NOT EXISTS blood_type_thresholds (
    blood_type text PRIMARY KEY,
    minimum_units integer NOT NULL,
    maximum_units integer NOT NULL
);

-- Depends on facilities (schema_facilities.sql) existing first.
CREATE TABLE IF NOT EXISTS inventory_snapshots (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    snapshot_date date NOT NULL,
    blood_type text NOT NULL,
    units integer NOT NULL,
    facility_id bigint NOT NULL REFERENCES facilities(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (snapshot_date, blood_type, facility_id)
);
