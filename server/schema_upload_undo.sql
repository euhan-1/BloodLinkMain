-- Traces exactly which rows a given upload is currently responsible for, so
-- an undo can target precisely those rows. On a re-upload that corrects an
-- existing row (DIN/phone/date+type upsert), this column moves to the
-- upload that most recently wrote the row — undo always acts on a row's
-- CURRENT state, never a stale one, so that's the correct owner.
ALTER TABLE upload_history ADD COLUMN IF NOT EXISTS undone_at timestamptz;

ALTER TABLE blood_units ADD COLUMN IF NOT EXISTS upload_history_id bigint REFERENCES upload_history(id);
ALTER TABLE donors ADD COLUMN IF NOT EXISTS upload_history_id bigint REFERENCES upload_history(id);
ALTER TABLE inventory_snapshots ADD COLUMN IF NOT EXISTS upload_history_id bigint REFERENCES upload_history(id);

CREATE INDEX IF NOT EXISTS idx_blood_units_upload_history ON blood_units (upload_history_id);
CREATE INDEX IF NOT EXISTS idx_donors_upload_history ON donors (upload_history_id);
CREATE INDEX IF NOT EXISTS idx_inventory_snapshots_upload_history ON inventory_snapshots (upload_history_id);
