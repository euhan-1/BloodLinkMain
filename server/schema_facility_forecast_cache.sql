-- Real, per-facility SARIMAX forecast cache. Distinct from
-- synthetic_forecast_cache (schema_synthetic_forecast_cache.sql), which is
-- facility-agnostic and synthetic-only — this table is keyed by a real
-- facility_id and only ever holds forecasts fit on that facility's own real
-- inventory_snapshots history. Never blended with the synthetic cache.
--
-- One row per (facility, blood_type, forecast_date) checkpoint. GET
-- /forecast refits whenever trained_through_date is behind this
-- facility+type's latest real snapshot date — trained_through_date is the
-- freshness key, not forecast_date, which just lets a cached checkpoint be
-- looked up directly by calendar date.
--
-- Depends on facilities (schema_facilities.sql) existing first.
CREATE TABLE IF NOT EXISTS facility_forecast_cache (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    facility_id bigint NOT NULL REFERENCES facilities(id),
    blood_type text NOT NULL,
    forecast_date date NOT NULL,
    forecast_units numeric NOT NULL,
    lower_units numeric NOT NULL,
    upper_units numeric NOT NULL,
    trained_through_date date NOT NULL,
    model_order text NOT NULL,
    generated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (facility_id, blood_type, forecast_date)
);

COMMENT ON TABLE facility_forecast_cache IS
    'Real per-facility SARIMAX forecast cache, fit on that facility''s own inventory_snapshots history. Never blended with synthetic_forecast_cache.';
