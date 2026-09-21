CREATE TABLE IF NOT EXISTS donors (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name text NOT NULL,
    blood_type text NOT NULL,
    phone text NOT NULL,
    facility_id bigint NOT NULL REFERENCES facilities(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (facility_id, phone)
);
