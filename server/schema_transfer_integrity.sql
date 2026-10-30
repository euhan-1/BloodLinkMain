-- Transfer integrity. ONE migration, built in stages; each stage appends its section below and the whole file stays
-- idempotent (re-running it changes nothing). Not applied to any database yet.
--
-- Stage 1: the component a request asks for. Release reserves only units of exactly this component, and the nearby
-- search counts only it (see main.CreateRequestBody, confirm_release, get_nearby_facilities).
--
-- Nullable on purpose: the 12 requests that exist were made before components, and nothing can say which component
-- they meant. NULL reads as "not recorded", never as a component. confirm_release refuses a request whose component
-- is NULL; new requests always carry one (the API requires it). No CHECK constraint, as for emergency_type: the list
-- lives in main.COMPONENTS so that adding one is a single line, not a second production migration.
ALTER TABLE requests ADD COLUMN IF NOT EXISTS component text;

-- Stage 2: the per-unit ledger. One row per unit reserved at release (main.confirm_release), settled at receipt
-- (main.confirm_receipt) in the same transactions as the reservation and the move.
--
-- Everything but the keys and the outcome is a SNAPSHOT of the unit at release. A later CSV re-upload may rewrite the
-- unit's component, blood type, volume, dates or shelf (ON CONFLICT (din) DO UPDATE); it cannot rewrite this row.
--
-- blood_unit_id is nullable with ON DELETE SET NULL: DELETE /inventory/{din} hard-deletes a unit with no transfer check,
-- so the hospital removing a unit it received must neither fail on a foreign key nor erase the record that it arrived.
-- din stays on the row, so the record survives. Unique on (request, unit) as for request_unit_failures, and on
-- (request, din), which still holds after the unit id has been set to NULL.
--
-- outcome: 'reserved' from release until receipt, then 'received' or 'failed'. reason is set exactly when failed.
-- days_remaining_at_receipt is set exactly when the row is settled, counted from the LIVE unit's expiry at receipt (the
-- value the received/failed decision is made on), not from expires_date above; zero or negative for a unit that
-- expired in transit. If a re-upload changed the expiry after release the two differ, and that difference is visible. Requests released before this table existed have no rows, and none are
-- backfilled: a snapshot written later would not be a snapshot of release.
CREATE TABLE IF NOT EXISTS request_units (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    request_id bigint NOT NULL REFERENCES requests(id),
    blood_unit_id bigint REFERENCES blood_units(id) ON DELETE SET NULL,
    din text NOT NULL,
    blood_type text NOT NULL,
    component text NOT NULL,
    volume_ml integer NOT NULL,
    collected_date date NOT NULL,
    expires_date date NOT NULL,
    origin_location text NOT NULL,
    days_remaining_at_release integer NOT NULL,
    outcome text NOT NULL DEFAULT 'reserved',
    reason text,
    days_remaining_at_receipt integer,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (request_id, blood_unit_id),
    UNIQUE (request_id, din),
    CONSTRAINT request_units_outcome_valid CHECK (outcome IN ('reserved', 'received', 'failed')),
    CONSTRAINT request_units_reason_matches_outcome CHECK (
        (outcome = 'failed' AND reason IS NOT NULL AND reason = 'expired_before_receipt') OR (outcome <> 'failed' AND reason IS NULL)
    ),
    CONSTRAINT request_units_receipt_days_when_settled CHECK ((outcome = 'reserved') = (days_remaining_at_receipt IS NULL))
);

CREATE INDEX IF NOT EXISTS idx_request_units_unit ON request_units (blood_unit_id);
