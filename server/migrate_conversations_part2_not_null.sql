-- Conversations, PART 2 of 2: make request_messages.conversation_id NOT NULL.
--
-- RUN THIS ONLY AFTER THE NEW SERVER CODE IS LIVE (the code that writes conversation_id on every message, through both the new
-- endpoints and the old per-request one) AND the previous server instance is gone. Until then an old instance's INSERT, which does not
-- name conversation_id, would be refused by the NOT NULL and a user's message would fail with a 500.
--
-- It is the only step of the conversations work that can make an old writer fail, which is why it is separate. If anything about the
-- deploy is in doubt, do not run it: the rest of the system works with the column nullable (a NULL row is read by way of its request).
--
-- Order, again:
--   0. the backup (see the header of migrate_add_conversations.sql)
--   1. migrate_add_conversations.sql
--   2. deploy the server
--   3. THIS FILE
--
-- It first fills in any row an old instance wrote between step 1 and step 2 (same statement as backfill 2 of part 1), then refuses
-- to continue if one is still NULL (a message whose request does not exist cannot be placed; none can exist: request_id is a foreign
-- key), then sets the constraint. One transaction; idempotent (a second run changes nothing).

BEGIN;

UPDATE public.request_messages m
SET conversation_id = c.id
FROM public.requests r
JOIN public.conversations c
  ON c.facility_low_id  = least(r.requesting_facility_id, r.supplying_facility_id)
 AND c.facility_high_id = greatest(r.requesting_facility_id, r.supplying_facility_id)
WHERE r.id = m.request_id AND m.conversation_id IS NULL;

-- A pair that had a request but no conversation row yet (created lazily, so possible) has none to join above: make the rows, then place.
INSERT INTO public.conversations (facility_low_id, facility_high_id)
SELECT DISTINCT least(r.requesting_facility_id, r.supplying_facility_id), greatest(r.requesting_facility_id, r.supplying_facility_id)
FROM public.request_messages m JOIN public.requests r ON r.id = m.request_id
WHERE m.conversation_id IS NULL
ON CONFLICT (facility_low_id, facility_high_id) DO NOTHING;

UPDATE public.request_messages m
SET conversation_id = c.id
FROM public.requests r
JOIN public.conversations c
  ON c.facility_low_id  = least(r.requesting_facility_id, r.supplying_facility_id)
 AND c.facility_high_id = greatest(r.requesting_facility_id, r.supplying_facility_id)
WHERE r.id = m.request_id AND m.conversation_id IS NULL;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM public.request_messages WHERE conversation_id IS NULL) THEN
        RAISE EXCEPTION 'request_messages still has rows without a conversation_id: nothing was changed';
    END IF;
END $$;

ALTER TABLE public.request_messages ALTER COLUMN conversation_id SET NOT NULL;

COMMIT;

-- CHECK afterwards (must return 0, and the column must now say NOT NULL):
--   SELECT count(*) FROM public.request_messages WHERE conversation_id IS NULL;
--   SELECT is_nullable FROM information_schema.columns WHERE table_name = 'request_messages' AND column_name = 'conversation_id';
