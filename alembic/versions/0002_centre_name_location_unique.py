"""add unique constraint on diagnostic_centres(name, location) for existing databases

This migration is a no-op for databases created fresh from 0001_initial
(the constraint is already part of that migration). It exists solely as the
upgrade path for databases that were created before the constraint was added
to 0001_initial.
"""
from alembic import op


revision = "0002_centre_name_location_unique"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # PostgreSQL-native: create the constraint directly without batch mode.
    # The `if_not_exists` equivalent is handled by catching the error; use
    # `checkfirst`-style guard via raw SQL so this is idempotent for both
    # fresh installs (already have the constraint) and old installs (need it).
    conn = op.get_bind()
    # Check if constraint already exists (fresh installs from 0001_initial have it)
    result = conn.execute(
        __import__("sqlalchemy", fromlist=["text"]).text(
            "SELECT 1 FROM information_schema.table_constraints "
            "WHERE table_name = 'diagnostic_centres' "
            "AND constraint_name = 'uq_centre_name_location'"
        )
    ).fetchone()
    if result is None:
        op.create_unique_constraint(
            "uq_centre_name_location",
            "diagnostic_centres",
            ["name", "location"],
        )


def downgrade() -> None:
    conn = op.get_bind()
    result = conn.execute(
        __import__("sqlalchemy", fromlist=["text"]).text(
            "SELECT 1 FROM information_schema.table_constraints "
            "WHERE table_name = 'diagnostic_centres' "
            "AND constraint_name = 'uq_centre_name_location'"
        )
    ).fetchone()
    if result is not None:
        op.drop_constraint(
            "uq_centre_name_location",
            "diagnostic_centres",
            type_="unique",
        )
