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

-- Stage 3: when each transition happened, and which account did it. All nullable, and NOT backfilled: a request that
-- changed state before these columns existed has NULL here, and NULL means "not recorded", never "did not happen". A
-- timestamp derived afterwards from a notification would put scripted test traffic on the record as a clinical event.
--
-- supplier_confirmed_at (release) and requester_confirmed_at (receipt) already exist and are not renamed; released_by
-- and received_by pair with them. Each timestamp is set with now() in the same transaction as its status change, so a
-- transition that fails writes neither the timestamp nor the actor.
--
-- The actor columns are the user id of the logged-in account (main.get_acting_user_id, as upload_history.uploaded_by).
-- BloodLink has one account per facility by design, so this records WHICH FACILITY'S ACCOUNT acted, not which person at
-- that facility. NULL also where no real user stands behind the call (the dev-only facility override).
ALTER TABLE requests
    ADD COLUMN IF NOT EXISTS accepted_at timestamptz,
    ADD COLUMN IF NOT EXISTS declined_at timestamptz,
    ADD COLUMN IF NOT EXISTS cancelled_at timestamptz,
    ADD COLUMN IF NOT EXISTS accepted_by bigint REFERENCES users(id),
    ADD COLUMN IF NOT EXISTS declined_by bigint REFERENCES users(id),
    ADD COLUMN IF NOT EXISTS cancelled_by bigint REFERENCES users(id),
    ADD COLUMN IF NOT EXISTS released_by bigint REFERENCES users(id),
    ADD COLUMN IF NOT EXISTS received_by bigint REFERENCES users(id);

COMMENT ON COLUMN requests.accepted_at IS 'When the supplier accepted. NULL = not recorded (requests before this column), or not accepted.';
COMMENT ON COLUMN requests.declined_at IS 'When the supplier declined. NULL = not recorded (requests before this column), or not declined.';
COMMENT ON COLUMN requests.cancelled_at IS 'When the requester cancelled. NULL = not recorded (requests before this column), or not cancelled.';
COMMENT ON COLUMN requests.accepted_by IS 'The supplier facility''s account that accepted. One account per facility: identifies the facility''s login, not a named person. NULL = not recorded.';
COMMENT ON COLUMN requests.declined_by IS 'The supplier facility''s account that declined. One account per facility: identifies the facility''s login, not a named person. NULL = not recorded.';
COMMENT ON COLUMN requests.cancelled_by IS 'The requester facility''s account that cancelled. One account per facility: identifies the facility''s login, not a named person. NULL = not recorded.';
COMMENT ON COLUMN requests.released_by IS 'The supplier facility''s account that confirmed release (pairs with supplier_confirmed_at). One account per facility: not a named person. NULL = not recorded.';
COMMENT ON COLUMN requests.received_by IS 'The requester facility''s account that confirmed receipt (pairs with requester_confirmed_at). One account per facility: not a named person. NULL = not recorded.';
