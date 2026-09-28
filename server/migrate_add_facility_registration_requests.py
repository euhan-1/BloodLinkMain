from pathlib import Path

from sqlalchemy import text

from database import engine


def _has_constraint(conn, table: str, constraint: str) -> bool:
    return conn.execute(
        text(
            "SELECT 1 FROM information_schema.table_constraints "
            "WHERE table_name = :table AND constraint_name = :constraint"
        ),
        {"table": table, "constraint": constraint},
    ).first() is not None


def main():
    schema_sql = Path(__file__).parent.joinpath("schema_facility_registration_requests.sql").read_text()
    with engine.begin() as conn:
        conn.execute(text(schema_sql))
        print("facility_registration_requests table ready")

        # otp_codes.registration_request_id had no FK when that table was
        # created (this table didn't exist yet) — add it now that it does.
        if not _has_constraint(conn, "otp_codes", "otp_codes_registration_request_id_fkey"):
            conn.execute(
                text(
                    """
                    ALTER TABLE otp_codes
                    ADD CONSTRAINT otp_codes_registration_request_id_fkey
                    FOREIGN KEY (registration_request_id)
                    REFERENCES facility_registration_requests(id)
                    """
                )
            )
            print("added otp_codes.registration_request_id -> facility_registration_requests FK")
        else:
            print("otp_codes registration_request_id FK already exists, skipping")


if __name__ == "__main__":
    main()
