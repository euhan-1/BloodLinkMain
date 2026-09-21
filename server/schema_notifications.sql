-- Facility-scoped, not user-scoped: a facility can have more than one staff
-- account (see POST /admin/facilities), and "a new request arrived" is
-- something any of them should see, not just whoever happened to trigger it.
CREATE TABLE IF NOT EXISTS notifications (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    facility_id bigint NOT NULL REFERENCES facilities(id),
    type text NOT NULL,
    message text NOT NULL,
    link text,
    read_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_notifications_facility_created
    ON notifications (facility_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_notifications_facility_unread
    ON notifications (facility_id) WHERE read_at IS NULL;
