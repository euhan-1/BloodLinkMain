from pathlib import Path

from sqlalchemy import text

from database import engine

# Fictional facility names (same ones the original design mockup used), placed at
# real Metro Manila coordinates so haversine distance is genuine, not fabricated.
# "Riverside General Hospital" is a stand-in origin/home facility until real
# auth/session data exists (planned for a later step) — it is excluded from
# search results, it only anchors the distance calculation.
FACILITIES = [
    ("Riverside General Hospital", "hospital", "Manila City Hall Area, Manila", 14.5958, 120.9822),
    ("St. Mary's Regional Blood Center", "bloodbank", "Diliman, Quezon City", 14.6760, 121.0437),
    ("General Hospital Blood Bank", "bloodbank", "Ayala Center, Makati City", 14.5547, 121.0244),
    ("University Health Blood Services", "bloodbank", "Ortigas Center, Pasig City", 14.5866, 121.0630),
    ("Northside Community Blood Center", "bloodbank", "Grace Park, Caloocan City", 14.6499, 120.9822),
    ("Metro Blood Alliance", "bloodbank", "Alabang, Muntinlupa City", 14.4181, 121.0416),
]


def main():
    schema_sql = Path(__file__).parent.joinpath("schema_facilities.sql").read_text()

    with engine.begin() as conn:
        conn.execute(text(schema_sql))

        count = conn.execute(text("SELECT count(*) FROM facilities")).scalar()
        if count > 0:
            print(f"facilities already has {count} rows, skipping seed")
            return

        for name, facility_type, address, lat, lng in FACILITIES:
            conn.execute(
                text(
                    """
                    INSERT INTO facilities (name, facility_type, address, latitude, longitude)
                    VALUES (:name, :facility_type, :address, :lat, :lng)
                    """
                ),
                {"name": name, "facility_type": facility_type, "address": address, "lat": lat, "lng": lng},
            )
        print(f"seeded {len(FACILITIES)} facilities")


if __name__ == "__main__":
    main()
