"""add unique constraint on diagnostic_centres(name, location)"""
from alembic import op

revision = "0002_centre_name_location_unique"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("diagnostic_centres") as batch_op:
        batch_op.create_unique_constraint(
            "uq_centre_name_location",
            ["name", "location"],
        )


def downgrade() -> None:
    with op.batch_alter_table("diagnostic_centres") as batch_op:
        batch_op.drop_constraint(
            "uq_centre_name_location",
            type_="unique",
        )

