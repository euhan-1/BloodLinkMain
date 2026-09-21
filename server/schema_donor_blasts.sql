-- status lifecycle (sub-step C implements the actual transitions): active -> completed
CREATE TABLE IF NOT EXISTS blasts (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    facility_id bigint NOT NULL REFERENCES facilities(id),
    blood_type text NOT NULL,
    target_count integer NOT NULL,
    time_limit_hours integer NOT NULL,
    status text NOT NULL DEFAULT 'active',
    created_at timestamptz NOT NULL DEFAULT now(),
    deadline_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS blast_messages (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    blast_id bigint NOT NULL REFERENCES blasts(id),
    donor_id bigint NOT NULL REFERENCES donors(id),
    message_text text NOT NULL,
    simulated_sent_at timestamptz NOT NULL DEFAULT now()
);
