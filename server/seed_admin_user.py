"""The only way an admin account (role='admin') ever gets created — there is
no registration endpoint for it, and POST /admin/facilities (which creates
ordinary facility staff accounts) deliberately can't set role='admin'.

Usage:
    .venv\\Scripts\\python.exe seed_admin_user.py <email> <password>

The password is set directly (not a temp password) since there's no
must-change-password step for admins — whoever runs this script is trusted
to pick a real password up front. Idempotent: running it again for an
email that already exists reports that and does nothing.
"""

import sys

from sqlalchemy import text

import auth
from database import engine


def main():
    if len(sys.argv) != 3:
        print("usage: seed_admin_user.py <email> <password>")
        sys.exit(1)

    email, password = sys.argv[1], sys.argv[2]
    if len(password) < 8:
        print("password must be at least 8 characters")
        sys.exit(1)

    with engine.begin() as conn:
        existing = conn.execute(text("SELECT id FROM users WHERE email = :email"), {"email": email}).first()
        if existing is not None:
            print(f"an account with email {email!r} already exists, skipping")
            return

        conn.execute(
            text(
                """
                INSERT INTO users (email, password_hash, facility_id, role, must_change_password)
                VALUES (:email, :password_hash, NULL, 'admin', false)
                """
            ),
            {"email": email, "password_hash": auth.hash_password(password)},
        )
    print(f"created admin account {email!r}")


if __name__ == "__main__":
    main()
