from pathlib import Path

from sqlalchemy import text

from database import engine

# Minimum/maximum safe-stock levels per blood type. These are a policy/config
# decision a facility would set, not something derived from inventory data —
# starting from the values used in the original design mockup as placeholder
# defaults (maximum is a placeholder 2x the minimum, not a real capacity figure).
THRESHOLDS = [
    ("A+", 80, 160),
    ("A-", 50, 100),
    ("B+", 60, 120),
    ("B-", 40, 80),
    ("O+", 100, 200),
    ("O-", 80, 160),
    ("AB+", 30, 60),
    ("AB-", 25, 50),
]


def main():
    schema_sql = Path(__file__).parent.joinpath("schema_dashboard.sql").read_text()

    with engine.begin() as conn:
        conn.execute(text(schema_sql))

        count = conn.execute(text("SELECT count(*) FROM blood_type_thresholds")).scalar()
        if count > 0:
            print(f"blood_type_thresholds already has {count} rows, skipping seed")
            return

        for blood_type, minimum_units, maximum_units in THRESHOLDS:
            conn.execute(
                text(
                    "INSERT INTO blood_type_thresholds (blood_type, minimum_units, maximum_units) "
                    "VALUES (:blood_type, :minimum_units, :maximum_units)"
                ),
                {"blood_type": blood_type, "minimum_units": minimum_units, "maximum_units": maximum_units},
            )
        print(f"seeded {len(THRESHOLDS)} thresholds")


if __name__ == "__main__":
    main()
