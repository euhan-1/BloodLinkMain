-- Coordination-chat messages tied to a specific request/transfer. Only the
-- two facilities on either side of a request (requester, supplier) can read
-- or post to it — enforced in main.py, not here.
--
-- This is the ORIGINAL definition, kept as it was. server/migrate_add_conversations.sql (2026-11-11) then added the conversation_id
-- column, dropped NOT NULL on request_id (a message may be about no request) and added the conversations and conversation_reads
-- tables; server/migrate_conversations_part2_not_null.sql later makes conversation_id NOT NULL. tests/schema.sql is the current shape.
CREATE TABLE IF NOT EXISTS request_messages (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    request_id bigint NOT NULL REFERENCES requests(id),
    sender_facility_id bigint NOT NULL REFERENCES facilities(id),
    message text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_request_messages_request_id ON request_messages(request_id);
