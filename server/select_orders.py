"""Runs per-type SARIMAX order selection (main.py:_run_order_selection_for_facility)
against the shared database and prints the resulting table.

THE PRIMARY PATH. The BackgroundTasks trigger on POST /forecast/historical-upload
is a safety net for a facility nobody runs this for, bounded to 2 types per
upload so an unattended facility still converges over repeated uploads — it is
NOT how this is meant to be run day to day. Running it here, on your own
machine, means Render's CPU (and its 512 MB / free-tier idle-sleep budget) never
enters into it: the grid is 72 candidates per type, and that fit time locally
is the whole cost (per-type timing UNMEASURED since the grid changed), paid
once, deliberately, not inside a request or an unattended background task.

Usage (from server/):
    .venv\\Scripts\\python.exe select_orders.py --facility 5
    .venv\\Scripts\\python.exe select_orders.py --all
"""
import argparse
import sys

from sqlalchemy import text

import main as app
from database import engine

COLUMNS = (
    "blood_type", "p", "d", "q", "seasonal_p", "seasonal_d", "seasonal_q", "seasonal_s",
    "aic", "lb_p_14_in_sample", "lb_p_21_in_sample", "lb_p_28_in_sample", "criterion_satisfied",
    "n_candidates_converged", "lb_p_14_holdout_onestep", "lb_p_21_holdout_onestep", "lb_p_28_holdout_onestep",
    "holdout_mape", "holdout_rmse", "holdout_baseline_mape", "holdout_baseline_rmse",
    "trained_through_date", "evaluated_through_date",
)


def fmt(v):
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.4f}" if abs(v) < 1000 else f"{v:.1f}"
    return str(v)


def print_table(facility_id: int, facility_name: str):
    with engine.connect() as conn:
        rows = conn.execute(
            text(f"SELECT {', '.join(COLUMNS)} FROM facility_sarimax_order WHERE facility_id = :f ORDER BY blood_type"),
            {"f": facility_id},
        ).mappings().all()
    print(f"\n=== facility {facility_id} ({facility_name}): {len(rows)} type(s) selected ===")
    if not rows:
        print("  (no type has enough history — ORDER_SELECTION_MIN_DAYS = "
              f"{app.ORDER_SELECTION_MIN_DAYS} days — or nothing in the grid converged)")
        return
    print("  " + " | ".join(COLUMNS))
    for r in rows:
        print("  " + " | ".join(fmt(r[c]) for c in COLUMNS))
    n_pass = sum(1 for r in rows if r["criterion_satisfied"])
    print(f"  criterion satisfied (in-sample Ljung-Box, all of lags 14/21/28): {n_pass}/{len(rows)}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--facility", type=int, help="run selection for one facility id, unbounded (every eligible type)")
    group.add_argument("--all", action="store_true", help="run selection for every bloodbank facility, unbounded")
    args = ap.parse_args()

    with engine.connect() as conn:
        if args.all:
            facilities = conn.execute(
                text("SELECT id, name FROM facilities WHERE facility_type = 'bloodbank' ORDER BY id")
            ).all()
        else:
            row = conn.execute(text("SELECT id, name FROM facilities WHERE id = :f"), {"f": args.facility}).first()
            if row is None:
                print(f"no facility with id {args.facility}", file=sys.stderr)
                sys.exit(1)
            facilities = [row]

    for facility_id, facility_name in facilities:
        print(f"selecting for facility {facility_id} ({facility_name})...", flush=True)
        app._run_order_selection_for_facility(facility_id)  # max_types=None: every eligible type, this is the unbounded primary path
        print_table(facility_id, facility_name)


if __name__ == "__main__":
    main()
