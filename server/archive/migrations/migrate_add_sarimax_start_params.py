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
        if _has_column(conn, "facility_sarimax_order", "start_params"):
            print("facility_sarimax_order.start_params already exists, skipping")
            return

        # Additive ALTER, not schema_facility_sarimax_order.sql's DROP TABLE + CREATE —
        # that file is the fresh-install shape; re-running it against the live DB would
        # drop every facility's existing selections. NULL on every existing row until the
        # next select_orders.py run repopulates them (see main.py's _get_selected_order).
        conn.execute(text("ALTER TABLE facility_sarimax_order ADD COLUMN start_params jsonb"))
        conn.execute(text("ALTER TABLE facility_sarimax_order ADD COLUMN selection_aic_per_obs double precision"))
        print("added facility_sarimax_order.start_params and .selection_aic_per_obs (NULL on existing rows)")


if __name__ == "__main__":
    main()
