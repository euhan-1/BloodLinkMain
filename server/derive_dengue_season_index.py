"""Derives dengue_season_index()'s graded monthly climatology (main.py) from
real DOH Epidemiology Bureau weekly dengue case counts for Batangas province,
2016-2021 (batangas_dengue_2016_2021_real.csv, committed alongside this
script for reproducibility).

Method:
  1. Each weekly row is assigned to the calendar month of its (week-ending)
     date.
  2. For a chosen set of "typical" years (excluding declared-epidemic and
     pandemic-disrupted years — see below), compute the mean weekly case
     count per calendar month, pooling all years together.
  3. Normalize by dividing every month's mean by the single largest month's
     mean, so the peak month is exactly 1.0 and every other month is its
     relative share of the peak — matching the 0-1 scale
     DENGUE_SEASON_INDEX_BY_MONTH / dengue_season_index() use.

Year exclusions, and why (see printed diagnostics below for the actual
numbers that justify each one):
  - 2019: DOH declared a national dengue epidemic this year. Peak weekly
    count hits 907 vs. 230-240 in the next-highest typical year — roughly a
    4x outlier that would distort the shape, not represent it.
  - 2020-2021: COVID-19 lockdowns collapsed case counts to near zero
    (single digits most weeks) and reporting never recovered within this
    dataset. 2021 also only has 2 weeks of data (Jan 3, Jan 10) — nowhere
    near a full year.
  - 2016-2018 are kept as the "typical year" baseline.

See DENGUE_INDEX_DERIVATION.md for why this ended up NOT excluding 2019 on
the first pass (an earlier verbal spec pooled 2016-2019), what that produced,
and why neither version was actually adopted into main.py's live exog.

Run from the server/ directory:

    .venv\\Scripts\\python.exe derive_dengue_season_index.py
"""
import csv
from collections import defaultdict
from pathlib import Path

CSV_PATH = Path(__file__).parent / "batangas_dengue_2016_2021_real.csv"

TYPICAL_YEARS = {2016, 2017, 2018}


def load_rows():
    rows = []
    with CSV_PATH.open(newline="") as f:
        for row in csv.DictReader(f):
            year, month, day = (int(p) for p in row["date"].split("-"))
            rows.append({"year": year, "month": month, "cases": int(row["cases"])})
    return rows


def yearly_peak_and_total(rows):
    by_year = defaultdict(list)
    for r in rows:
        by_year[r["year"]].append(r["cases"])
    return {y: (max(v), sum(v), len(v)) for y, v in sorted(by_year.items())}


def monthly_climatology(rows, years: set[int]) -> dict[int, float]:
    by_month = defaultdict(list)
    for r in rows:
        if r["year"] in years:
            by_month[r["month"]].append(r["cases"])
    means = {m: sum(v) / len(v) for m, v in by_month.items()}
    peak = max(means.values())
    return {m: round(means[m] / peak, 2) for m in range(1, 13)}


def main():
    rows = load_rows()

    print("Per-year peak weekly count / total cases / week count (why 2019 and 2020-2021 are excluded):")
    for year, (peak, total, n) in yearly_peak_and_total(rows).items():
        flag = "  <- excluded" if year not in TYPICAL_YEARS else ""
        print(f"  {year}: peak week = {peak:>4}   total = {total:>5}   weeks on file = {n:>3}{flag}")

    print(f"\nMonthly climatology, {sorted(TYPICAL_YEARS)} only (peak month = 1.00):")
    index_2016_2018 = monthly_climatology(rows, TYPICAL_YEARS)
    for m in range(1, 13):
        print(f"  {m:>2}: {index_2016_2018[m]:.2f}")

    print("\nFor comparison — what including 2019 (the epidemic year) would do to the same climatology:")
    index_with_2019 = monthly_climatology(rows, TYPICAL_YEARS | {2019})
    for m in range(1, 13):
        marker = "  <-- shifts" if abs(index_with_2019[m] - index_2016_2018[m]) >= 0.05 else ""
        print(f"  {m:>2}: {index_with_2019[m]:.2f}{marker}")


if __name__ == "__main__":
    main()
