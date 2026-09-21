-- status lifecycle: pending -> accepted | declined | cancelled; accepted ->
-- completed (via supplier_confirmed_at then requester_confirmed_at, in that
-- order). cancelled is requester-initiated, declined is supplier-initiated —
-- both terminal, both only reachable from pending.
CREATE TABLE IF NOT EXISTS requests (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    requesting_facility_id bigint NOT NULL REFERENCES facilities(id),
    supplying_facility_id bigint NOT NULL REFERENCES facilities(id),
    blood_type text NOT NULL,
    quantity integer NOT NULL,
    emergency_type text NOT NULL,
    status text NOT NULL DEFAULT 'pending',
    supplier_confirmed_at timestamptz,
    requester_confirmed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);
