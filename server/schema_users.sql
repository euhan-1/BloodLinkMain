-- facility_id is nullable: admin accounts (role='admin') don't belong to any
-- facility — see seed_admin_user.py, the only way one gets created. Every
-- facility-scoped endpoint (get_acting_facility_id in main.py) rejects a
-- NULL facility_id rather than silently querying with it.
CREATE TABLE IF NOT EXISTS users (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    email text NOT NULL UNIQUE,
    password_hash text NOT NULL,
    facility_id bigint REFERENCES facilities(id),
    role text NOT NULL DEFAULT 'staff',
    must_change_password boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now()
);
