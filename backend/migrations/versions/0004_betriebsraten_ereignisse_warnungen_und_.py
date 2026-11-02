"""Betriebsraten Ereignisse Warnungen und SMTP

Revision ID: 0004
Revises: 0003
"""

from alembic import op
import sqlalchemy as sa


revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "cameras",
        sa.Column(
            "learning_enabled", sa.Boolean(), server_default="true", nullable=False
        ),
    )
    op.add_column(
        "cameras",
        sa.Column(
            "learning_dirty", sa.Boolean(), server_default="false", nullable=False
        ),
    )
    with op.batch_alter_table("training_samples") as batch:
        batch.alter_column("created_by", existing_type=sa.String(36), nullable=True)
    op.add_column(
        "analysis_results",
        sa.Column(
            "review_visible", sa.Boolean(), server_default="true", nullable=False
        ),
    )
    with op.batch_alter_table("analysis_results") as batch:
        batch.add_column(sa.Column("review_sample_id", sa.String(36), nullable=True))
        batch.create_foreign_key(
            "fk_analysis_review_sample",
            "training_samples",
            ["review_sample_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_index("ix_analysis_results_review_sample_id", ["review_sample_id"])
    op.create_table(
        "smtp_config",
        sa.Column("id", sa.String(length=16), nullable=False),
        sa.Column("host", sa.String(length=253), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("encryption", sa.String(length=16), nullable=False),
        sa.Column("username", sa.String(length=254), nullable=False),
        sa.Column("encrypted_password", sa.Text(), nullable=True),
        sa.Column("sender", sa.String(length=254), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "alert_states",
        sa.Column("container_id", sa.String(length=36), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("severity", sa.Integer(), nullable=False),
        sa.Column("episode", sa.Integer(), nullable=False),
        sa.Column("emitted_level", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["container_id"],
            ["containers.id"],
        ),
        sa.PrimaryKeyConstraint("container_id"),
    )
    op.create_table(
        "notifications",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("container_id", sa.String(length=36), nullable=False),
        sa.Column("episode", sa.Integer(), nullable=False),
        sa.Column("severity", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("container_name", sa.String(length=120), nullable=False),
        sa.Column("measured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fill_level", sa.Float(), nullable=False),
        sa.Column("threshold", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["container_id"],
            ["containers.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("container_id", "episode", "severity"),
    )
    op.create_index(
        op.f("ix_notifications_container_id"),
        "notifications",
        ["container_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_notifications_created_at"),
        "notifications",
        ["created_at"],
        unique=False,
    )
    op.create_table(
        "email_outbox",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("notification_id", sa.String(length=36), nullable=False),
        sa.Column("recipient", sa.String(length=254), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error", sa.String(length=200), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["notification_id"],
            ["notifications.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("notification_id", "recipient"),
    )
    op.create_index(
        op.f("ix_email_outbox_notification_id"),
        "email_outbox",
        ["notification_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_email_outbox_status"), "email_outbox", ["status"], unique=False
    )
    op.create_table(
        "notification_reads",
        sa.Column("notification_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["notification_id"], ["notifications.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("notification_id", "user_id"),
    )
    op.create_table(
        "container_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("container_id", sa.String(length=36), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("note", sa.String(length=500), nullable=False),
        sa.Column("measurement_id", sa.String(length=36), nullable=True),
        sa.Column("created_by", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["container_id"],
            ["containers.id"],
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["measurement_id"],
            ["measurements.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_event_container_time",
        "container_events",
        ["container_id", "occurred_at"],
        unique=False,
    )
    op.add_column(
        "containers",
        sa.Column(
            "rate_time_unit",
            sa.String(length=16),
            server_default="hour",
            nullable=False,
        ),
    )
    op.add_column(
        "containers",
        sa.Column(
            "smoothing_minutes", sa.Integer(), server_default="60", nullable=False
        ),
    )
    op.add_column(
        "containers",
        sa.Column("event_threshold", sa.Float(), server_default="20", nullable=False),
    )
    op.add_column(
        "containers",
        sa.Column("rate_revision", sa.Integer(), server_default="1", nullable=False),
    )
    op.add_column(
        "containers",
        sa.Column(
            "calculated_revision", sa.Integer(), server_default="0", nullable=False
        ),
    )
    op.add_column(
        "containers",
        sa.Column("fill_notice", sa.Float(), server_default="80", nullable=False),
    )
    op.add_column(
        "containers",
        sa.Column("fill_warning", sa.Float(), server_default="90", nullable=False),
    )
    op.add_column(
        "containers",
        sa.Column(
            "consumption_notice", sa.Float(), server_default="20", nullable=False
        ),
    )
    op.add_column(
        "containers",
        sa.Column(
            "consumption_warning", sa.Float(), server_default="10", nullable=False
        ),
    )
    op.add_column(
        "containers",
        sa.Column(
            "email_enabled", sa.Boolean(), server_default="false", nullable=False
        ),
    )
    op.add_column(
        "containers",
        sa.Column("email_recipients", sa.JSON(), server_default="[]", nullable=False),
    )
    op.add_column(
        "measurements", sa.Column("operating_rate_percent", sa.Float(), nullable=True)
    )
    op.add_column(
        "measurements", sa.Column("operating_rate_absolute", sa.Float(), nullable=True)
    )
    op.add_column(
        "measurements", sa.Column("rate_revision", sa.Integer(), nullable=True)
    )
    op.add_column("measurements", sa.Column("rate_cycle", sa.Integer(), nullable=True))
    op.add_column(
        "measurements",
        sa.Column("cycle_break", sa.Boolean(), server_default="false", nullable=False),
    )


def downgrade() -> None:
    # Automatisch gesammelte Bilder haben bewusst keinen erfundenen Benutzer.
    # Ein Downgrade darf diese Bilddaten nicht still löschen/umhängen.
    bind = op.get_bind()
    if bind.scalar(
        sa.text("SELECT COUNT(*) FROM training_samples WHERE created_by IS NULL")
    ):
        raise RuntimeError(
            "Downgrade gesperrt: automatisch gesammelte Trainingsbilder vorhanden. Backup wiederherstellen oder Daten gezielt migrieren."
        )
    with op.batch_alter_table("analysis_results") as batch:
        batch.drop_index("ix_analysis_results_review_sample_id")
        batch.drop_constraint("fk_analysis_review_sample", type_="foreignkey")
        batch.drop_column("review_sample_id")
        batch.drop_column("review_visible")
    with op.batch_alter_table("training_samples") as batch:
        batch.alter_column("created_by", existing_type=sa.String(36), nullable=False)
    op.drop_column("cameras", "learning_dirty")
    op.drop_column("cameras", "learning_enabled")
    op.drop_column("measurements", "cycle_break")
    op.drop_column("measurements", "rate_cycle")
    op.drop_column("measurements", "rate_revision")
    op.drop_column("measurements", "operating_rate_absolute")
    op.drop_column("measurements", "operating_rate_percent")
    op.drop_column("containers", "email_recipients")
    op.drop_column("containers", "email_enabled")
    op.drop_column("containers", "consumption_warning")
    op.drop_column("containers", "consumption_notice")
    op.drop_column("containers", "fill_warning")
    op.drop_column("containers", "fill_notice")
    op.drop_column("containers", "calculated_revision")
    op.drop_column("containers", "rate_revision")
    op.drop_column("containers", "event_threshold")
    op.drop_column("containers", "smoothing_minutes")
    op.drop_column("containers", "rate_time_unit")
    op.drop_index("ix_event_container_time", table_name="container_events")
    op.drop_table("container_events")
    op.drop_table("notification_reads")
    op.drop_index(op.f("ix_email_outbox_status"), table_name="email_outbox")
    op.drop_index(op.f("ix_email_outbox_notification_id"), table_name="email_outbox")
    op.drop_table("email_outbox")
    op.drop_index(op.f("ix_notifications_created_at"), table_name="notifications")
    op.drop_index(op.f("ix_notifications_container_id"), table_name="notifications")
    op.drop_table("notifications")
    op.drop_table("alert_states")
    op.drop_table("smtp_config")
