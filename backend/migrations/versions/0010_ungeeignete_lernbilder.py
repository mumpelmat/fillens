"""Ungeeignete Bilder als geprüfte Lernbeispiele erhalten."""
from alembic import op
import sqlalchemy as sa

revision = "0010"
down_revision = "0009"
branch_labels = depends_on = None


def upgrade():
    op.add_column("training_samples", sa.Column("rejection_reason", sa.String(32)))
    with op.batch_alter_table("learning_restore_samples") as batch:
        batch.add_column(sa.Column("rejection_reason", sa.String(32)))
        batch.alter_column("target_fill_level", existing_type=sa.Float(), nullable=True)


def downgrade():
    connection = op.get_bind()
    for table in ("training_samples", "learning_restore_samples"):
        if connection.execute(sa.text(f"SELECT 1 FROM {table} WHERE rejection_reason IS NOT NULL LIMIT 1")).first():
            raise RuntimeError("Rückbau gesperrt: Ungeeignete Lernbeispiele zuerst vollständig sichern. Alte Software unterstützt diese Bewertungen nicht.")
    if connection.execute(sa.text("SELECT 1 FROM model_versions WHERE CAST(dataset_json AS TEXT) LIKE '%rejection_reason%' LIMIT 1")).first():
        raise RuntimeError("Rückbau gesperrt: Es existieren Modellstände mit ungeeigneten Lernbeispielen.")
    with op.batch_alter_table("learning_restore_samples") as batch:
        batch.alter_column("target_fill_level", existing_type=sa.Float(), nullable=False)
        batch.drop_column("rejection_reason")
    op.drop_column("training_samples", "rejection_reason")
