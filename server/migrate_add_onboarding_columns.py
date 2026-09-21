from sqlalchemy import text

from database import engine


def _has_column(conn, table: str, column: str) -> bool:
    return conn.execute(
        text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = :table AND column_name = :column"
        ),
        {"table": table, "column": column},
    ).first() is not None


def main():
    with engine.begin() as conn:
        if not _has_column(conn, "users", "must_change_password"):
            conn.execute(text("ALTER TABLE users ADD COLUMN must_change_password boolean NOT NULL DEFAULT false"))
            print("added users.must_change_password")
        else:
            print("users.must_change_password already exists, skipping")

        if not _has_column(conn, "facilities", "department"):
            conn.execute(text("ALTER TABLE facilities ADD COLUMN department text"))
            print("added facilities.department")
        else:
            print("facilities.department already exists, skipping")

        if not _has_column(conn, "facilities", "doh_license_number"):
            conn.execute(text("ALTER TABLE facilities ADD COLUMN doh_license_number text"))
            print("added facilities.doh_license_number")
        else:
            print("facilities.doh_license_number already exists, skipping")

        if not _has_column(conn, "facilities", "profile_completed"):
            conn.execute(text("ALTER TABLE facilities ADD COLUMN profile_completed boolean NOT NULL DEFAULT false"))
            # Existing seeded facilities already have real address/lat/long from
            # seed_facilities.py — they're not mid-onboarding, so backfill them
            # as complete rather than incorrectly gating them behind the new
            # complete-profile flow.
            result = conn.execute(
                text(
                    """
                    UPDATE facilities SET profile_completed = true
                    WHERE address IS NOT NULL AND latitude IS NOT NULL AND longitude IS NOT NULL
                    """
                )
            )
            print(f"added facilities.profile_completed, backfilled {result.rowcount} existing facilities as complete")
        else:
            print("facilities.profile_completed already exists, skipping")

        # address/latitude/longitude were NOT NULL originally — relax them so
        # admin-onboarded facilities can be created without a location yet.
        conn.execute(text("ALTER TABLE facilities ALTER COLUMN address DROP NOT NULL"))
        conn.execute(text("ALTER TABLE facilities ALTER COLUMN latitude DROP NOT NULL"))
        conn.execute(text("ALTER TABLE facilities ALTER COLUMN longitude DROP NOT NULL"))
        print("facilities.address/latitude/longitude are now nullable")


if __name__ == "__main__":
    main()
