-- Append-only log of every value a historical-stock upload wrote into
-- inventory_snapshots, plus the ORIGINAL organic value (upload_history_id NULL)
-- the first time an upload overwrote one. inventory_snapshots keeps only the
-- current value; this is what lets an undo put back what an upload overwrote,
-- and what lets undoing upload A after upload B overlapped it behave correctly.
-- Undo of upload U, per (date, type) U wrote: the newest surviving upload's
-- value wins, else the organic value, else the row is deleted (U inserted it).
-- Pure log: cascades away with its facility or its upload.
CREATE TABLE IF NOT EXISTS inventory_snapshot_write_log (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    facility_id bigint NOT NULL REFERENCES facilities(id) ON DELETE CASCADE,
    snapshot_date date NOT NULL,
    blood_type text NOT NULL,
    units integer NOT NULL,
    upload_history_id bigint REFERENCES upload_history(id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_snapshot_write_log_key ON inventory_snapshot_write_log (facility_id, snapshot_date, blood_type);
CREATE INDEX IF NOT EXISTS idx_snapshot_write_log_upload ON inventory_snapshot_write_log (upload_history_id);
