-- blood_type_thresholds: per-facility minimum/maximum safe-stock policy.
-- Composite key (facility_id, blood_type) — was a single-column blood_type
-- PRIMARY KEY (global across every facility) until
-- migrate_scope_thresholds_to_facility.py. A facility's rows are seeded at
-- creation time (see admin_create_facility_account in main.py), so every
-- facility always has its own 8 rows — no facility-scoped read here should
-- ever need to fall back to a hardcoded default.
--
-- Depends on facilities (schema_facilities.sql) existing first.
CREATE TABLE IF NOT EXISTS blood_type_thresholds (
    facility_id bigint NOT NULL REFERENCES facilities(id),
    blood_type text NOT NULL,
    minimum_units integer NOT NULL,
    maximum_units integer NOT NULL,
    PRIMARY KEY (facility_id, blood_type)
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
