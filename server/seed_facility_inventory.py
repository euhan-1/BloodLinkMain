from sqlalchemy import text

from database import engine

# Demo/synthetic stock counts per facility per blood type — deterministic (not
# random) so the resulting Available/Unavailable mix is reproducible. Chosen to
# land on both sides of blood_type_thresholds for a believable, mixed demo
# rather than making every facility trivially available or unavailable.
FACILITY_STOCK = {
    "Northside Community Blood Center": {"A+": 85, "A-": 55, "B+": 65, "B-": 35, "O+": 95, "O-": 90, "AB+": 32, "AB-": 20},
    "General Hospital Blood Bank": {"A+": 70, "A-": 45, "B+": 58, "B-": 42, "O+": 105, "O-": 78, "AB+": 28, "AB-": 27},
    "University Health Blood Services": {"A+": 90, "A-": 48, "B+": 70, "B-": 38, "O+": 98, "O-": 85, "AB+": 35, "AB-": 22},
    "St. Mary's Regional Blood Center": {"A+": 75, "A-": 52, "B+": 55, "B-": 45, "O+": 110, "O-": 60, "AB+": 29, "AB-": 26},
    "Metro Blood Alliance": {"A+": 120, "A-": 65, "B+": 90, "B-": 50, "O+": 150, "O-": 200, "AB+": 40, "AB-": 30},
}

# Deliberately expired O- stock at General Hospital Blood Bank, on top of its 78
# valid units above — proves the availability query excludes expired units
# rather than counting them (the TODO flagged back when Expired status was
# added to Inventory). Without correct exclusion this would look like 88 units.
EXPIRED_TEST_BATCH = ("General Hospital Blood Bank", "O-", 10)

BULK_INSERT_SQL = """
    INSERT INTO blood_units
        (din, blood_type, component, location, volume_ml, collected_date, expires_date, facility_id)
    SELECT
        :din_prefix || '-' || gs,
        :blood_type,
        'Packed RBC',
        'Bay 1',
        280,
        CURRENT_DATE - ((gs % 20) || ' days')::interval,
        CURRENT_DATE + ((90 - (gs % 30)) || ' days')::interval,
        :facility_id
    FROM generate_series(1, :count) AS gs
"""

EXPIRED_BATCH_INSERT_SQL = """
    INSERT INTO blood_units
        (din, blood_type, component, location, volume_ml, collected_date, expires_date, facility_id)
    SELECT
        :din_prefix || '-' || gs,
        :blood_type,
        'Packed RBC',
        'Bay 1',
        280,
        CURRENT_DATE - ((60 + gs) || ' days')::interval,
        CURRENT_DATE - ((5 + (gs % 5)) || ' days')::interval,
        :facility_id
    FROM generate_series(1, :count) AS gs
"""


def main():
    with engine.begin() as conn:
        existing = conn.execute(
            text("SELECT count(*) FROM blood_units WHERE din LIKE 'BB-%'")
        ).scalar()
        if existing > 0:
            print(f"facility inventory already seeded ({existing} rows starting with BB-), skipping")
            return

        facility_ids = {
            row["name"]: row["id"]
            for row in conn.execute(text("SELECT id, name FROM facilities")).mappings().all()
        }

        total = 0
        for facility_name, stock in FACILITY_STOCK.items():
            facility_id = facility_ids[facility_name]
            for blood_type, count in stock.items():
                din_prefix = f"BB-{facility_id}-{blood_type}"
                conn.execute(
                    text(BULK_INSERT_SQL),
                    {
                        "din_prefix": din_prefix,
                        "blood_type": blood_type,
                        "facility_id": facility_id,
                        "count": count,
                    },
                )
                total += count
        print(f"seeded {total} units across {len(FACILITY_STOCK)} facilities")

        expired_facility, expired_type, expired_count = EXPIRED_TEST_BATCH
        expired_facility_id = facility_ids[expired_facility]
        conn.execute(
            text(EXPIRED_BATCH_INSERT_SQL),
            {
                "din_prefix": f"BB-{expired_facility_id}-{expired_type}-EXPIRED",
                "blood_type": expired_type,
                "facility_id": expired_facility_id,
                "count": expired_count,
            },
        )
        print(f"seeded {expired_count} deliberately-expired {expired_type} units at {expired_facility} (exclusion test)")


if __name__ == "__main__":
    main()
