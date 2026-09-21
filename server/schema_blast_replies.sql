CREATE TABLE IF NOT EXISTS blast_replies (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    blast_id bigint NOT NULL REFERENCES blasts(id),
    donor_id bigint NOT NULL REFERENCES donors(id),
    reply text NOT NULL,
    replied_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (blast_id, donor_id)
);
