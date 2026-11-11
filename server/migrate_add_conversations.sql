-- Conversations: one per pair of facilities, whatever requests exist between them. PART 1 of 2. Run by hand in the Supabase SQL editor
-- (the way migrate_add_facility_indexes.sql and migrate_add_privacy_consent.sql were). NOT applied to production by anything in this repo.
--
-- ORDER (see also the end of this file):
--   0. BACKUP, a separate statement, run first and on its own (the table holds 4 rows in production, counted 2026-11-11):
--        CREATE TABLE request_messages_backup_20261111 AS SELECT * FROM request_messages;
--        SELECT count(*) FROM request_messages_backup_20261111;   -- must equal: SELECT count(*) FROM request_messages;
--      The backup is a plain copy with no keys, so nothing depends on it. Keep it until the Messages screen has been used for a while,
--      then drop it by hand when you decide to (nothing in the app reads it). A future pg_dump of production will include it.
--   1. THIS FILE (part 1), then the checks at the bottom.
--   2. Deploy the server (the new code), see that GET /conversations answers.
--   3. server/migrate_conversations_part2_not_null.sql, only after step 2 and only once the old server instance is gone.
--
-- What part 1 does, and why it is safe to run BEFORE the new code is deployed:
--   * It only ADDS: two tables, one nullable column on request_messages, one index. Nothing existing is renamed or removed.
--   * It drops NOT NULL on request_messages.request_id (a message may now be about no request). The old code always supplies it, so it
--     never notices.
--   * The old code's INSERT into request_messages (request_id, sender_facility_id, message) still works after it: conversation_id is
--     nullable with no default. The new code reads a message whose conversation_id is NULL by way of its request's pair of facilities,
--     so such a row (written by an old server instance during a deploy) still shows up in the conversation.
--   * It is idempotent: IF NOT EXISTS, ON CONFLICT DO NOTHING, WHERE ... IS NULL. Running it twice changes nothing the second time
--     (tests/test_conversations.py runs it twice against the scratch database).
--   * It runs in one transaction: all of it or none of it.
--
-- Nothing in it deletes or rewrites a message: the backfill only fills in the new conversation_id of existing rows and keeps request_id.

BEGIN;

-- A pair is stored once, smaller id first, so (A, B) and (B, A) cannot both exist. Rows are created lazily by the server (the first
-- message sent, or the first time a thread is opened), never by creating a request: a missing row never breaks anything.
CREATE TABLE IF NOT EXISTS public.conversations (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    facility_low_id  bigint NOT NULL REFERENCES public.facilities(id),
    facility_high_id bigint NOT NULL REFERENCES public.facilities(id),
    created_at timestamp with time zone NOT NULL DEFAULT now(),
    CONSTRAINT conversations_pair_ordered CHECK (facility_low_id < facility_high_id),
    CONSTRAINT conversations_pair_unique UNIQUE (facility_low_id, facility_high_id)
);

-- Server-side read state: "this facility has read up to this message". One row per facility per conversation, never shared.
CREATE TABLE IF NOT EXISTS public.conversation_reads (
    conversation_id bigint NOT NULL REFERENCES public.conversations(id),
    facility_id bigint NOT NULL REFERENCES public.facilities(id),
    last_read_message_id bigint NOT NULL DEFAULT 0,
    updated_at timestamp with time zone NOT NULL DEFAULT now(),
    PRIMARY KEY (conversation_id, facility_id)
);

-- A message belongs to a conversation; the request it is about is now optional.
ALTER TABLE public.request_messages ADD COLUMN IF NOT EXISTS conversation_id bigint REFERENCES public.conversations(id);
ALTER TABLE public.request_messages ALTER COLUMN request_id DROP NOT NULL;

-- Backfill 1: a conversation for every pair that has ever had a request.
INSERT INTO public.conversations (facility_low_id, facility_high_id)
SELECT DISTINCT least(requesting_facility_id, supplying_facility_id), greatest(requesting_facility_id, supplying_facility_id)
FROM public.requests
ON CONFLICT (facility_low_id, facility_high_id) DO NOTHING;

-- Backfill 2: every existing message goes to its request's pair. request_id is kept: each message still says which request it was sent on.
UPDATE public.request_messages m
SET conversation_id = c.id
FROM public.requests r
JOIN public.conversations c
  ON c.facility_low_id  = least(r.requesting_facility_id, r.supplying_facility_id)
 AND c.facility_high_id = greatest(r.requesting_facility_id, r.supplying_facility_id)
WHERE r.id = m.request_id AND m.conversation_id IS NULL;

-- Backfill 3: everything that exists today counts as read for both sides, so nobody opens Messages to a wall of "unread".
INSERT INTO public.conversation_reads (conversation_id, facility_id, last_read_message_id)
SELECT c.id, v.f, COALESCE((SELECT max(m.id) FROM public.request_messages m WHERE m.conversation_id = c.id), 0)
FROM public.conversations c
CROSS JOIN LATERAL (VALUES (c.facility_low_id), (c.facility_high_id)) AS v(f)
ON CONFLICT (conversation_id, facility_id) DO NOTHING;

-- A thread read and the "last message per conversation" read both walk one conversation's messages by id. Plain CREATE INDEX:
-- request_messages has 4 rows in production (counted 2026-11-11), so CONCURRENTLY is not needed (and cannot run in a transaction).
CREATE INDEX IF NOT EXISTS idx_request_messages_conversation ON public.request_messages (conversation_id, id);

COMMIT;

-- CHECKS after part 1, run each on its own. All three must return 0:
--   SELECT count(*) FROM public.request_messages WHERE conversation_id IS NULL;
--   SELECT count(*) FROM public.request_messages m JOIN public.requests r ON r.id = m.request_id
--     JOIN public.conversations c ON c.id = m.conversation_id
--     WHERE c.facility_low_id <> least(r.requesting_facility_id, r.supplying_facility_id)
--        OR c.facility_high_id <> greatest(r.requesting_facility_id, r.supplying_facility_id);
--   SELECT count(*) FROM public.conversations c WHERE NOT EXISTS (SELECT 1 FROM public.requests r
--     WHERE least(r.requesting_facility_id, r.supplying_facility_id) = c.facility_low_id
--       AND greatest(r.requesting_facility_id, r.supplying_facility_id) = c.facility_high_id);
-- And this must return 2 * (number of conversations):
--   SELECT count(*) FROM public.conversation_reads;
