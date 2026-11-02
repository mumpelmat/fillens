"""Begrenzte Trainingssammlung und dauerhaftes Verwerfen.

Revision ID: 0007
Revises: 0006
"""

from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = depends_on = None


def upgrade():
    op.add_column(
        "analysis_results", sa.Column("discarded_at", sa.DateTime(timezone=True))
    )
    op.add_column(
        "training_samples",
        sa.Column("origin", sa.String(16), nullable=False, server_default="upload"),
    )
    op.add_column(
        "training_samples", sa.Column("captured_at", sa.DateTime(timezone=True))
    )
    op.add_column(
        "training_samples", sa.Column("discarded_at", sa.DateTime(timezone=True))
    )
    op.execute(
        sa.text(
            "UPDATE training_samples SET origin='camera' WHERE created_by IS NULL OR id IN (SELECT review_sample_id FROM analysis_results WHERE review_sample_id IS NOT NULL)"
        )
    )
    op.execute(
        sa.text(
            "UPDATE training_samples SET captured_at=(SELECT MIN(measured_at) FROM analysis_results WHERE review_sample_id=training_samples.id) WHERE origin='camera'"
        )
    )
    op.create_table(
        "training_collection",
        sa.Column(
            "camera_id", sa.String(36), sa.ForeignKey("cameras.id"), primary_key=True
        ),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("candidates_json", sa.JSON(), nullable=False),
        sa.Column("scan_json", sa.JSON(), nullable=False),
        sa.Column("scanned_at", sa.DateTime(timezone=True)),
        sa.Column("retry_at", sa.DateTime(timezone=True)),
        sa.Column("context_key", sa.String(64)),
        sa.Column("error", sa.Text()),
    )
    op.create_table(
        "training_fetch_records",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "camera_id", sa.String(36), sa.ForeignKey("cameras.id"), nullable=False
        ),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("transform_key", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("camera_id", "fingerprint", "transform_key"),
    )
    op.create_index(
        "ix_training_fetch_records_camera_id", "training_fetch_records", ["camera_id"]
    )


def downgrade():
    if op.get_bind().scalar(
        sa.text("SELECT COUNT(*) FROM analysis_results WHERE discarded_at IS NOT NULL")
    ):
        raise RuntimeError(
            "Downgrade gesperrt: verworfene Referenzen vorhanden. Sicherung wiederherstellen."
        )
    if op.get_bind().scalar(
        sa.text("SELECT COUNT(*) FROM training_samples WHERE discarded_at IS NOT NULL")
    ):
        raise RuntimeError(
            "Downgrade gesperrt: verworfene Bilder würden wieder aufgenommen. Datenbank-/ML-Sicherung wiederherstellen."
        )
    op.drop_table("training_fetch_records")
    op.drop_table("training_collection")
    for column in ("discarded_at", "captured_at", "origin"):
        op.drop_column("training_samples", column)

    op.drop_column("analysis_results", "discarded_at")
