-- Coordination-chat messages tied to a specific request/transfer. Only the
-- two facilities on either side of a request (requester, supplier) can read
-- or post to it — enforced in main.py, not here.
CREATE TABLE IF NOT EXISTS request_messages (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    request_id bigint NOT NULL REFERENCES requests(id),
    sender_facility_id bigint NOT NULL REFERENCES facilities(id),
    message text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_request_messages_request_id ON request_messages(request_id);
