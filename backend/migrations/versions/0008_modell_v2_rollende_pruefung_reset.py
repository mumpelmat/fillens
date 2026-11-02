"""Modell v2, rollende Prüfung und Lernstand-Wiederherstellung.

Revision ID: 0008
Revises: 0007
"""

from uuid import uuid4

from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = depends_on = None


def upgrade():
    op.add_column(
        "cameras",
        sa.Column(
            "learning_generation",
            sa.String(36),
            nullable=False,
            server_default="legacy",
        ),
    )
    op.add_column(
        "model_versions",
        sa.Column(
            "learning_generation",
            sa.String(36),
            nullable=False,
            server_default="legacy",
        ),
    )
    op.add_column(
        "training_samples",
        sa.Column(
            "queue_state", sa.String(16), nullable=False, server_default="pending"
        ),
    )
    op.add_column(
        "training_samples",
        sa.Column(
            "window_managed", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.add_column(
        "training_samples", sa.Column("source_fingerprint", sa.String(64))
    )
    op.add_column("analysis_results", sa.Column("estimated_error", sa.Float()))

    with op.batch_alter_table("training_fetch_records") as batch:
        for name, kind in (
            ("remote_path", sa.Text()),
            ("remote_size", sa.Integer()),
            ("remote_modified", sa.String(64)),
            ("captured_at", sa.DateTime(timezone=True)),
            ("state", sa.String(16)),
        ):
            batch.add_column(sa.Column(name, kind))
        batch.add_column(
            sa.Column(
                "sample_id",
                sa.String(36),
                sa.ForeignKey(
                    "training_samples.id",
                    ondelete="SET NULL",
                    name="fk_training_fetch_records_sample_id",
                ),
            )
        )
    op.create_index(
        "ix_training_fetch_records_sample_id", "training_fetch_records", ["sample_id"]
    )

    bind = op.get_bind()
    cameras = list(bind.execute(sa.text("SELECT id FROM cameras")))
    for row in cameras:
        generation = str(uuid4())
        bind.execute(
            sa.text(
                "UPDATE cameras SET learning_generation=:generation WHERE id=:camera"
            ),
            {"generation": generation, "camera": row[0]},
        )
        bind.execute(
            sa.text(
                "UPDATE model_versions SET learning_generation=:generation WHERE camera_id=:camera"
            ),
            {"generation": generation, "camera": row[0]},
        )
    bind.execute(
        sa.text(
            "UPDATE training_samples SET queue_state=CASE "
            "WHEN discarded_at IS NOT NULL THEN 'discarded' "
            "WHEN approved=true THEN 'confirmed' ELSE 'pending' END"
        )
    )
    bind.execute(
        sa.text(
            "UPDATE training_samples SET window_managed=true WHERE origin IN ('camera','ftp')"
        )
    )
    bind.execute(
        sa.text(
            "UPDATE training_samples SET source_fingerprint=("
            "SELECT import_records.fingerprint FROM analysis_results "
            "JOIN import_records ON import_records.id=analysis_results.source_import_id "
            "WHERE analysis_results.review_sample_id=training_samples.id LIMIT 1"
            ") WHERE origin='camera'"
        )
    )
    # Die Tabelle war ausschließlich ein Abrufcache. Der rollende Index wird
    # mit den neuen Zuständen aus dem aktuellen 168-Stunden-Fenster aufgebaut.
    bind.execute(sa.text("DELETE FROM training_fetch_records"))
    bind.execute(
        sa.text(
            "UPDATE training_fetch_records SET state='candidate' WHERE state IS NULL"
        )
    )

    op.create_table(
        "ftp_rejections",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("camera_id", sa.String(36), sa.ForeignKey("cameras.id"), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("remote_path", sa.Text(), nullable=False),
        sa.Column("remote_size", sa.Integer(), nullable=False),
        sa.Column("remote_modified", sa.String(64), nullable=False),
        sa.Column("error_code", sa.String(40), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("retry_at", sa.DateTime(timezone=True)),
        sa.Column("blocked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("camera_id", "fingerprint"),
    )
    op.create_index("ix_ftp_rejections_camera_id", "ftp_rejections", ["camera_id"])

    op.create_table(
        "learning_restore_points",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("camera_id", sa.String(36), sa.ForeignKey("cameras.id"), nullable=False),
        sa.Column("reason", sa.String(24), nullable=False),
        sa.Column("config_json", sa.JSON(), nullable=False),
        sa.Column("active_models_json", sa.JSON(), nullable=False),
        sa.Column("artifact_hashes_json", sa.JSON(), nullable=False),
        sa.Column("approved_count", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_learning_restore_points_camera_id",
        "learning_restore_points",
        ["camera_id"],
    )
    op.create_table(
        "learning_restore_samples",
        sa.Column(
            "restore_point_id",
            sa.String(36),
            sa.ForeignKey("learning_restore_points.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "sample_id",
            sa.String(36),
            sa.ForeignKey("training_samples.id"),
            primary_key=True,
        ),
        sa.Column("file_hash", sa.String(64), nullable=False),
        sa.Column("target_fill_level", sa.Float(), nullable=False),
        sa.Column("image_mode", sa.String(16), nullable=False),
        sa.Column("series_name", sa.String(100), nullable=False),
        sa.Column("transform_key", sa.String(64), nullable=False),
        sa.Column("window_managed", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id")),
    )
    op.create_index(
        "ix_learning_restore_samples_sample_id",
        "learning_restore_samples",
        ["sample_id"],
    )


def downgrade():
    bind = op.get_bind()
    if bind.scalar(sa.text("SELECT COUNT(*) FROM learning_restore_points")):
        raise RuntimeError(
            "Downgrade gesperrt: Wiederherstellungspunkte vorhanden. Passende DB-/ML-Sicherung wiederherstellen."
        )
    op.drop_table("learning_restore_samples")
    op.drop_table("learning_restore_points")
    op.drop_table("ftp_rejections")
    op.drop_index(
        "ix_training_fetch_records_sample_id", table_name="training_fetch_records"
    )
    with op.batch_alter_table("training_fetch_records") as batch:
        for column in (
            "sample_id",
            "state",
            "captured_at",
            "remote_modified",
            "remote_size",
            "remote_path",
        ):
            batch.drop_column(column)
    op.drop_column("analysis_results", "estimated_error")
    for column in ("source_fingerprint", "window_managed", "queue_state"):
        op.drop_column("training_samples", column)
    op.drop_column("model_versions", "learning_generation")
    op.drop_column("cameras", "learning_generation")
