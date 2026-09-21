CREATE TABLE IF NOT EXISTS upload_history (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    facility_id bigint NOT NULL REFERENCES facilities(id),
    upload_type text NOT NULL CHECK (upload_type IN ('inventory', 'donors', 'historical_stock')),
    uploaded_by bigint REFERENCES users(id),
    filename text,
    uploaded_at timestamptz NOT NULL DEFAULT now(),
    rows_processed int NOT NULL,
    rows_failed int NOT NULL,
    error_details jsonb NOT NULL DEFAULT '[]',
    raw_content text
);

CREATE INDEX IF NOT EXISTS idx_upload_history_facility_type
    ON upload_history (facility_id, upload_type, uploaded_at DESC);
