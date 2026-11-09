"""Laufende Sammlung ohne Bildanzahllimit, Aufbewahrung und Trainingssteuerung."""
from alembic import op
import sqlalchemy as sa

revision = "0011"
down_revision = "0010"
branch_labels = depends_on = None


def upgrade():
    op.add_column("cameras", sa.Column("training_mode", sa.String(16), nullable=False, server_default="automatic"))
    op.add_column("cameras", sa.Column("image_retention_days", sa.Integer(), nullable=False, server_default="7"))
    op.add_column("model_versions", sa.Column("trigger", sa.String(16), nullable=False, server_default="manual"))
    op.execute("UPDATE model_versions SET trigger = 'automatic' WHERE managed = true")
    with op.batch_alter_table("image_buffer") as batch:
        batch.add_column(sa.Column("training_sample_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("learning_collected", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.create_foreign_key("fk_image_training_sample", "training_samples", ["training_sample_id"], ["id"], ondelete="SET NULL")
        batch.create_index("ix_image_buffer_training_sample_id", ["training_sample_id"])
    # Alte 24-Stunden-Sperren wegen fehlender Datumsstruktur neu prüfen.
    op.execute("UPDATE training_collection SET context_key = NULL, retry_at = NULL")


def downgrade():
    # Ältere Software würde den behaltenen Bildbestand wieder auf drei reduzieren.
    connection = op.get_bind()
    if connection.execute(sa.text("SELECT camera_id FROM image_buffer GROUP BY camera_id HAVING COUNT(*) > 3 LIMIT 1")).first():
        raise RuntimeError("Rückbau gesperrt: Ältere Software würde Kamerabilder automatisch löschen. Vollständige Sicherung verwenden.")
    with op.batch_alter_table("image_buffer") as batch:
        batch.drop_index("ix_image_buffer_training_sample_id")
        batch.drop_constraint("fk_image_training_sample", type_="foreignkey")
        batch.drop_column("learning_collected")
        batch.drop_column("training_sample_id")
    op.drop_column("model_versions", "trigger")
    op.drop_column("cameras", "image_retention_days")
    op.drop_column("cameras", "training_mode")
