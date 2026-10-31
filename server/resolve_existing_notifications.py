"""One-off: resolve the notifications left over from before the notification rebuild.

The automatic sweep (main._resolve_stale_notifications) was held back by NOTIFICATIONS_AUTORESOLVE_SINCE (a date, now None) so the
unread rows written under the old rules were left as they were until someone decided. This was that decision, run once (it has
been; kept as the record of how):

  - the retired `unit_expiry` type (nothing writes it now): every unread row;
  - everything the sweep's rules resolve, with NO date limit: a request that no longer exists or is no longer pending, a receipt
    prompt already answered, the old shared `transfer_confirmation_needed` prompts already answered, an expiring-unit offer
    whose units have expired.

"Resolve" means read_at is set, nothing is deleted. Events that happened (request_accepted, request_declined, request_cancelled,
transfer_completed, transfer_units_failed) and forecast_shortage are left unread: they are things that happened and nobody read, and
the badge should say so. The one exception is the rule you already have, that any row whose request no longer exists resolves, which
holds for an event too (nothing is left to open). If a run would resolve any other row of a type outside RESOLVABLE it rolls back
and stops.

Before it writes anything it saves the ids of every unread row to a JSON file (default server/tmp/, which is git-ignored). Every row
it resolves gets the same read_at (the transaction's now()), recorded in that file with the SQL that undoes the run exactly.

The database in DATABASE_URL is production, so this refuses to run without the flag (script_guard.py), --dry-run included, since
a dry run opens a write transaction (it is rolled back).

Run from server/:
    .venv\\Scripts\\python.exe resolve_existing_notifications.py --dry-run --i-know-this-writes-to-production
    .venv\\Scripts\\python.exe resolve_existing_notifications.py --i-know-this-writes-to-production
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from database import engine
from script_guard import require_production_write_flag

# The types this script may resolve. Anything else resolved is a bug: the run is rolled back.
RESOLVABLE = {"unit_expiry", "incoming_request", "transfer_confirmation_needed", "receipt_needed", "expiring_unit_alert"}
RETIRED_TYPES = ["unit_expiry"]
DEFAULT_DUMP_DIR = Path(__file__).resolve().parent / "tmp"


def run(dump_dir: Path = DEFAULT_DUMP_DIR, dry_run: bool = False) -> dict:
    """One transaction. Returns {"stamp", "dump", "by_type": [(type, unread_before, resolved, still_unread)], "resolved", "unread_after"}.
    Commits unless dry_run; rolls back (and raises) if it would resolve a type outside RESOLVABLE."""
    import main as app  # the sweep itself: one definition of "no longer true", shared with the live list

    with engine.connect() as conn:
        try:
            stamp = conn.execute(text("SELECT now()")).scalar()
            before = conn.execute(text(
                "SELECT id, facility_id, type, created_at FROM notifications WHERE read_at IS NULL ORDER BY id")).all()
            dump = {
                "script": "resolve_existing_notifications.py",
                "ran_at": stamp.isoformat(),
                "resolved_stamp": stamp.isoformat(),
                "undo_sql": f"UPDATE notifications SET read_at = NULL WHERE read_at = '{stamp.isoformat()}';",
                "unread_before": [{"id": r.id, "facility_id": r.facility_id, "type": r.type, "created_at": r.created_at.isoformat()} for r in before],
            }
            dump_path = None
            if not dry_run:  # the ids are on disk before the first row changes
                dump_dir.mkdir(parents=True, exist_ok=True)
                dump_path = dump_dir / f"notification_cleanup_{stamp.astimezone(timezone.utc):%Y%m%dT%H%M%S}Z.json"
                dump_path.write_text(json.dumps(dump, indent=1), encoding="utf-8")

            app._resolve_stale_notifications(conn, None, None)  # no facility filter, no date limit
            conn.execute(text("UPDATE notifications SET read_at = now() WHERE type = ANY(:t) AND read_at IS NULL"), {"t": RETIRED_TYPES})

            by_type = conn.execute(text(
                """
                SELECT type,
                       count(*) FILTER (WHERE id = ANY(:ids)) AS unread_before,
                       count(*) FILTER (WHERE read_at = :stamp) AS resolved,
                       count(*) FILTER (WHERE read_at IS NULL) AS still_unread
                FROM notifications GROUP BY type ORDER BY type
                """), {"stamp": stamp, "ids": [r.id for r in before]}).all()
            # an event may resolve only because its request is gone; any other event, or forecast_shortage, resolved is a bug
            unexpected = [t for (t,) in conn.execute(text(
                """
                SELECT DISTINCT n.type FROM notifications n
                WHERE n.read_at = :stamp AND n.id = ANY(:ids) AND n.type <> ALL(:resolvable)
                  AND NOT (n.link ~ '^requests:[0-9]+$'
                           AND NOT EXISTS (SELECT 1 FROM requests r WHERE r.id = (substring(n.link from '^requests:([0-9]+)$'))::bigint))
                """), {"stamp": stamp, "ids": [r.id for r in before], "resolvable": sorted(RESOLVABLE)})]
            if unexpected:
                raise RuntimeError(f"refusing to commit: this run would resolve {unexpected}, which are not meant to be touched")
            result = {
                "stamp": stamp.isoformat(), "dump": str(dump_path) if dump_path else None,
                "by_type": [(r.type, r.unread_before, r.resolved, r.still_unread) for r in by_type if r.unread_before or r.still_unread],
                "resolved": sum(r.resolved for r in by_type), "unread_after": sum(r.still_unread for r in by_type),
            }
            if dry_run:
                conn.rollback()
            else:
                conn.commit()
                dump["result"] = {k: result[k] for k in ("by_type", "resolved", "unread_after")}
                dump_path.write_text(json.dumps(dump, indent=1), encoding="utf-8")
            return result
        except BaseException:
            conn.rollback()
            raise


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    require_production_write_flag("resolve_existing_notifications.py", [
        "mark read (set read_at on) every unread notification of the retired type unit_expiry",
        "mark read the unread incoming_request / receipt_needed / transfer_confirmation_needed rows whose request is gone or no longer needs them",
        "mark read the unread expiring_unit_alert rows whose units have expired",
        "leave request_accepted, request_declined, request_cancelled, transfer_completed, transfer_units_failed and forecast_shortage unread (unless their request no longer exists)",
        "(--dry-run does the same inside a transaction it rolls back)",
    ])
    result = run(dry_run=dry_run)
    print(("DRY RUN, rolled back: nothing was written." if dry_run else "Committed.") + f" Resolved {result['resolved']} rows; {result['unread_after']} stay unread.")
    print(f"{'type':32} {'unread before':>13} {'resolved':>9} {'still unread':>13}")
    for t, before, resolved, left in result["by_type"]:
        print(f"{t:32} {before:>13} {resolved:>9} {left:>13}")
    if not dry_run:
        print(f"\nIds saved to {result['dump']} before anything changed.")
        print(f"To undo exactly this run: UPDATE notifications SET read_at = NULL WHERE read_at = '{result['stamp']}';")


if __name__ == "__main__":
    main()
