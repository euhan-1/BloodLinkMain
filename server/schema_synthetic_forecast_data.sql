-- SYNTHETIC TEST DATA ONLY. Not tied to any real facility (no facility_id),
-- and deliberately kept out of inventory_snapshots (the real, per-facility
-- production table) so fabricated and genuinely-collected data can never be
-- confused with each other. Exists purely to develop/validate the ARIMAX
-- forecasting model before real historical data is available.
CREATE TABLE IF NOT EXISTS synthetic_inventory_snapshots (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    snapshot_date date NOT NULL,
    blood_type text NOT NULL,
    units integer NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (snapshot_date, blood_type)
);

COMMENT ON TABLE synthetic_inventory_snapshots IS
    'SYNTHETIC TEST DATA — generated, not real. Used only for ARIMAX model development pending real historical data from a facility interview. Never join or blend with inventory_snapshots.';
