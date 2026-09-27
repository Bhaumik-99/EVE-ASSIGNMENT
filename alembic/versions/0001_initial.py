"""initial schema"""
from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    user_role = sa.Enum("PATIENT", "ADMIN", name="user_role")
    booking_status = sa.Enum("PENDING", "CONFIRMED", "FAILED", "CANCELLED", name="booking_status")
    payment_status = sa.Enum("SUCCESS", "FAILED", name="payment_status")
    payment_event_status = sa.Enum("SUCCESS", "FAILED", name="payment_event_status")

    op.create_table(
        "users",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("full_name", sa.String(length=120), nullable=False),
        sa.Column("role", user_role, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_index("ix_users_role", "users", ["role"], unique=False)

    op.create_table(
        "diagnostic_centres",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=180), nullable=False),
        sa.Column("location", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", "location", name="uq_centre_name_location"),
    )
    op.create_index("ix_diagnostic_centres_name", "diagnostic_centres", ["name"], unique=False)

    op.create_table(
        "diagnostic_tests",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("centre_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=180), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("price", sa.Numeric(10, 2), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["centre_id"], ["diagnostic_centres.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_diagnostic_tests_centre_id", "diagnostic_tests", ["centre_id"], unique=False)

    op.create_table(
        "bookings",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("test_id", sa.String(length=36), nullable=False),
        sa.Column("centre_id", sa.String(length=36), nullable=False),
        sa.Column("appointment_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("status", booking_status, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["centre_id"], ["diagnostic_centres.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["test_id"], ["diagnostic_tests.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("centre_id", "test_id", "appointment_at", name="uq_booking_slot"),
    )
    op.create_index("ix_bookings_user_id", "bookings", ["user_id"], unique=False)
    op.create_index("ix_bookings_test_id", "bookings", ["test_id"], unique=False)
    op.create_index("ix_bookings_centre_id", "bookings", ["centre_id"], unique=False)
    op.create_index("ix_bookings_appointment_at", "bookings", ["appointment_at"], unique=False)
    op.create_index("ix_bookings_status", "bookings", ["status"], unique=False)

    op.create_table(
        "payments",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("booking_id", sa.String(length=36), nullable=False),
        sa.Column("provider_payment_id", sa.String(length=100), nullable=False),
        sa.Column("status", payment_status, nullable=False),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("idempotency_key", sa.String(length=100), nullable=True),
        sa.Column("idempotency_request_hash", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["booking_id"], ["bookings.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("booking_id"),
        sa.UniqueConstraint("idempotency_key"),
        sa.UniqueConstraint("provider_payment_id"),
    )
    op.create_index("ix_payments_booking_id", "payments", ["booking_id"], unique=True)
    op.create_index("ix_payments_provider_payment_id", "payments", ["provider_payment_id"], unique=True)
    op.create_index("ix_payments_idempotency_key", "payments", ["idempotency_key"], unique=True)

    op.create_table(
        "payment_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("event_id", sa.String(length=100), nullable=False),
        sa.Column("payment_id", sa.String(length=36), nullable=False),
        sa.Column("booking_id", sa.String(length=36), nullable=False),
        sa.Column("provider_payment_id", sa.String(length=100), nullable=False),
        sa.Column("status", payment_event_status, nullable=False),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["booking_id"], ["bookings.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["payment_id"], ["payments.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id"),
    )
    op.create_index("ix_payment_events_event_id", "payment_events", ["event_id"], unique=True)
    op.create_index("ix_payment_events_payment_id", "payment_events", ["payment_id"], unique=False)
    op.create_index("ix_payment_events_booking_id", "payment_events", ["booking_id"], unique=False)
    op.create_index("ix_payment_events_provider_payment_id", "payment_events", ["provider_payment_id"], unique=False)


def downgrade() -> None:
    op.drop_table("payment_events")
    op.drop_table("payments")
    op.drop_table("bookings")
    op.drop_table("diagnostic_tests")
    # uq_centre_name_location is dropped implicitly when the table is dropped
    op.drop_table("diagnostic_centres")
    op.drop_table("users")
    bind = op.get_bind()
    for enum_name in ("payment_event_status", "payment_status", "booking_status", "user_role"):
        sa.Enum(name=enum_name).drop(bind, checkfirst=True)
