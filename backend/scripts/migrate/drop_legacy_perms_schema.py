"""Phase 4 cleanup — drop the legacy permissions schema.

After this runs:
  - The `group_permissions` table is gone
  - `users.permissions_extra` and `users.permissions_revoked` columns are gone

Idempotent (uses IF EXISTS). Take a Cloud SQL backup before running.

Usage:
    cd backend && python -m scripts.migrate.drop_legacy_perms_schema
"""
from sqlalchemy import text

from app.database import engine


def main() -> None:
    statements = [
        "DROP TABLE IF EXISTS group_permissions",
        "ALTER TABLE users DROP COLUMN IF EXISTS permissions_extra",
        "ALTER TABLE users DROP COLUMN IF EXISTS permissions_revoked",
    ]
    with engine.begin() as conn:
        for stmt in statements:
            print(f"-- {stmt}")
            conn.execute(text(stmt))
    print("Legacy permissions schema dropped.")


if __name__ == "__main__":
    main()
