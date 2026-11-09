-- migrate_add_facility_indexes.sql
--
-- NOT RUN. Written for you to review and paste into the Supabase SQL editor yourself. Nothing in the repository executes this file.
--
-- Adds five indexes the system audit (2026-11-09) found missing. Every one is a plain additive index: no table, column or row
-- changes, no lock that blocks reads or writes (CREATE INDEX CONCURRENTLY), and each can be dropped again with no other effect.
--
-- ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
-- HOW TO RUN IT
-- ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
-- CREATE INDEX CONCURRENTLY cannot run inside a transaction block, and the SQL editor sends a multi-statement script as one
-- implicit transaction (you would get "CREATE INDEX CONCURRENTLY cannot run inside a transaction block"). So run the statements
-- ONE AT A TIME: select a single CREATE INDEX statement (from the line that starts it to its semicolon) and press Run. Each takes
-- well under a second at today's sizes (blood_units 6,198 rows, inventory_snapshots 5,528, requests 16).
--
-- If a CONCURRENTLY build is interrupted or fails it leaves an INVALID index behind, and "IF NOT EXISTS" will then skip it
-- silently on a re-run. After running all five, run the CHECK query at the bottom: every row must say valid = true. For a row that
-- says false, run its DROP INDEX CONCURRENTLY from the ROLLBACK section, then run its CREATE again.
--
-- After they are all valid, regenerate server/tests/schema.sql (a pg_dump --schema-only of production, per CLAUDE.md) so the test
-- schema matches. I did not edit schema.sql: until this runs, it is still an accurate dump of production.
--
-- Run them outside a busy minute if you can; the build reads the table once and takes only a lightweight lock that does not block
-- the app's reads or writes. The cost afterwards is one extra index to maintain on each insert/update of the table (tiny here).
--
-- ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
-- WHAT THE AUDIT MEASURED (read-only SQL and plain EXPLAIN, 2026-11-09; full output in .audit/db_session_output.txt)
-- ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
-- Current indexes on these tables: blood_units has only its primary key, UNIQUE(din) and an index on upload_history_id;
-- requests has only its primary key; inventory_snapshots has the primary key and UNIQUE(snapshot_date, blood_type, facility_id).
--
-- #1 blood_units, the facility-scoped read (EXPLAIN, facility 6 = the largest, 1,464 of 6,198 rows):
--      HashAggregate (cost=221.27..221.35 rows=8)
--        -> Seq Scan on blood_units (cost=0.00..218.97 rows=459)
--             Filter: (archived_at IS NULL) AND (expires_date >= '2026-11-09') AND (facility_id = 6)
--    pg_stat_user_tables: blood_units seq_scan = 16,203. Every facility-scoped query reads the whole table: /inventory,
--    /inventory/summary, /inventory/expiring, the release candidates (FOR UPDATE), "can give" counts, archive-expired.
--
-- #2 blood_units, the reservation lookup (EXPLAIN): Seq Scan on blood_units (cost=0.00..203.48 rows=1)
--      Filter: (reserved_for_request_id = 1). A foreign key with no index: confirm_receipt looks units up by it (FOR UPDATE).
--
-- #3/#4 requests (EXPLAIN): Seq Scan (cost 1.16 and 1.20) for the requester list and the supplier list. pg_stat: requests
--    seq_scan = 13,223, idx_scan = 19. The request lists are polled every 25 s per open tab. Only 16 rows today, so this is
--    cheap now; it is here because the table grows with use and nothing else would ever add these.
--
-- #5 inventory_snapshots, the forecast's history read (EXPLAIN of the real query, main.py:3370-3377, facility 5):
--      Index Scan using inventory_snapshots_date_type_facility_key on inventory_snapshots (cost=0.28..199.86 rows=3578)
--        Index Cond: (facility_id = 5)          [facility 158: cost 0.28..174.42 rows=1361]
--    The only index with snapshot_date has it as the LEADING column and facility_id third, so this read walks the whole index
--    (5,528 entries) to pick out one facility's 3,578. It is correct and already ordered, so the gain is modest (a new
--    (facility_id, snapshot_date) index reads only that facility's entries). Optional; the weakest case of the five.
--
-- WHAT TO EXPECT (an expectation, not a measurement: I did not run any of this):
--  - #2 should be picked by the planner immediately (a lookup of 1 row in 6,198). #5 should be picked too, for a modest saving.
--  - #1: each facility holds 5% to 24% of blood_units (8 facilities), so at today's size the planner may still choose a sequential
--    scan for the whole-facility reads; the index matters most for the narrow reads (one blood type, an expiry window, FEFO
--    candidates) and as the table or the facility count grows.
--  - #3 and #4: with 16 rows the planner will keep the sequential scan (cheapest at that size) and will switch when the table grows.
--  To confirm after running, repeat the EXPLAINs (they are in the VERIFY section) and compare with the numbers above.
-- ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────


-- ═══ #1 blood_units by facility, blood type, expiry ═══  (RUN ALONE)
-- Serves: WHERE facility_id = ? [AND blood_type = ?] AND expires_date >= / <= ?  in USABLE_COUNTS_SQL, RELEASABLE_UNITS_SQL,
-- OFFERABLE_UNITS_SQL, /inventory (also ORDER BY expires_date), /inventory/expiring, confirm_release's candidate select, archive-expired.
-- NOT partial on archived_at: USABLE_COUNTS_SQL deliberately counts non-expired archived units too (main.py USABLE_COUNTS_SQL comment),
-- so a "WHERE archived_at IS NULL" index could not serve it.
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_blood_units_facility_type_expiry
    ON public.blood_units (facility_id, blood_type, expires_date);


-- ═══ #2 blood_units by the request it is reserved for ═══  (RUN ALONE)
-- Serves: WHERE reserved_for_request_id = ?  (confirm_receipt's FOR UPDATE select) and the foreign key's own referential checks.
-- Partial: most units are not reserved, so the index holds only the handful that are.
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_blood_units_reserved_for_request
    ON public.blood_units (reserved_for_request_id)
    WHERE reserved_for_request_id IS NOT NULL;


-- ═══ #3 requests by requesting facility ═══  (RUN ALONE)
-- Serves: GET /requests (WHERE requesting_facility_id = ? ORDER BY created_at, id) and the requesting half of GET /transfers
-- (WHERE requesting_facility_id = ? OR supplying_facility_id = ?, which the planner can answer by combining #3 and #4).
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_requests_requester_created
    ON public.requests (requesting_facility_id, created_at);


-- ═══ #4 requests by supplying facility ═══  (RUN ALONE)
-- Serves: GET /requests/incoming (WHERE supplying_facility_id = ? ORDER BY created_at, id) and the supplying half of GET /transfers.
-- Why created_at and not status as the second column (the audit first suggested status): no query filters by status first. Every
-- list is "this facility's requests, oldest first"; the status filter in /transfers is applied to a facility's few rows after the
-- facility match, and status has only four values, so as an index column it would add size without narrowing anything. If you
-- want a status-led variant anyway: CREATE INDEX CONCURRENTLY idx_requests_supplier_status ON public.requests (supplying_facility_id, status);
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_requests_supplier_created
    ON public.requests (supplying_facility_id, created_at);


-- ═══ #5 inventory_snapshots by facility, date ═══  (RUN ALONE; optional and lowest priority, run it last)
-- Serves: WHERE facility_id = ? ORDER BY snapshot_date (the forecast's history read, main.py:3370-3377, and the per-facility reads
-- at :1918 and :3213). The existing UNIQUE(snapshot_date, blood_type, facility_id) stays: it enforces one row per day.
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_inventory_snapshots_facility_date
    ON public.inventory_snapshots (facility_id, snapshot_date);


-- ═══ CHECK (run after all five; every row must show valid = true) ═══
-- SELECT c.relname AS index_name, i.indisvalid AS valid, pg_size_pretty(pg_relation_size(c.oid)) AS size
-- FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
-- WHERE c.relname IN ('idx_blood_units_facility_type_expiry', 'idx_blood_units_reserved_for_request',
--                     'idx_requests_requester_created', 'idx_requests_supplier_created', 'idx_inventory_snapshots_facility_date')
-- ORDER BY c.relname;


-- ═══ VERIFY (plain EXPLAIN, no ANALYZE; replace 6 with a facility id; compare with the "measured" numbers above) ═══
-- EXPLAIN SELECT blood_type, count(*) FROM blood_units WHERE facility_id = 6 AND archived_at IS NULL AND expires_date >= CURRENT_DATE GROUP BY blood_type;
-- EXPLAIN SELECT id FROM blood_units WHERE reserved_for_request_id = 1;
-- EXPLAIN SELECT * FROM requests WHERE requesting_facility_id = 6 ORDER BY created_at DESC;
-- EXPLAIN SELECT * FROM requests WHERE supplying_facility_id = 6 AND status IN ('pending', 'accepted');
-- EXPLAIN SELECT snapshot_date, blood_type, units FROM inventory_snapshots WHERE facility_id = 5 ORDER BY snapshot_date ASC;
-- (CURRENT_DATE in the SQL editor is UTC; fine for an EXPLAIN. The app itself never uses CURRENT_DATE, it passes the Manila date.)


-- ═══ ROLLBACK (each is independent and harmless; run one at a time, same transaction rule) ═══
-- DROP INDEX CONCURRENTLY IF EXISTS public.idx_blood_units_facility_type_expiry;
-- DROP INDEX CONCURRENTLY IF EXISTS public.idx_blood_units_reserved_for_request;
-- DROP INDEX CONCURRENTLY IF EXISTS public.idx_requests_requester_created;
-- DROP INDEX CONCURRENTLY IF EXISTS public.idx_requests_supplier_created;
-- DROP INDEX CONCURRENTLY IF EXISTS public.idx_inventory_snapshots_facility_date;
