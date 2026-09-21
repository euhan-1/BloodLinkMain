from pathlib import Path

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
    schema_sql = Path(__file__).parent.joinpath("schema_notifications.sql").read_text()

    with engine.begin() as conn:
        conn.execute(text(schema_sql))
        print("notifications table + indexes ensured")

        # Tracks the last expiry status (near-expiry/critical) a unit was
        # already notified about, so GET /inventory only fires a notification
        # the first time a unit crosses into a given tier, not on every load.
        if not _has_column(conn, "blood_units", "last_notified_expiry_status"):
            conn.execute(text("ALTER TABLE blood_units ADD COLUMN last_notified_expiry_status text"))
            print("added blood_units.last_notified_expiry_status")
        else:
            print("blood_units.last_notified_expiry_status already exists, skipping")

        # Tracks whether a facility's forecast was already alerting for a
        # given blood type, so GET /forecast only fires a notification the
        # moment it *newly* starts predicting a shortage, not on every load
        # while the shortage is ongoing.
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS forecast_alert_state (
                    facility_id bigint NOT NULL REFERENCES facilities(id),
                    blood_type text NOT NULL,
                    alerting boolean NOT NULL DEFAULT false,
                    updated_at timestamptz NOT NULL DEFAULT now(),
                    PRIMARY KEY (facility_id, blood_type)
                )
                """
            )
        )
        print("forecast_alert_state table ensured")


if __name__ == "__main__":
    main()
