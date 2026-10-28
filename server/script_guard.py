"""Refusal shared by the standalone scripts that write to the database in DATABASE_URL.

That database is production (see the repo-root CLAUDE.md), so a script that inserts or
deletes rows must not run by accident. Call require_production_write_flag() first thing in
the script's main(), before anything connects: without FLAG on the command line it prints
what the script would write and exits non-zero.
"""
import sys

FLAG = "--i-know-this-writes-to-production"


def require_production_write_flag(script: str, writes: list[str]) -> None:
    if FLAG in sys.argv:
        sys.argv.remove(FLAG)
        return
    print(f"REFUSING TO RUN {script}: it writes to the database in DATABASE_URL, which is the production database.")
    print("It would:")
    for line in writes:
        print(f"  - {line}")
    print(f"Nothing was written. To run it anyway, pass {FLAG}.")
    sys.exit(2)
