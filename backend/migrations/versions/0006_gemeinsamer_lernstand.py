"""Gemeinsamen Lernstand und gewünschten Erkennungsbetrieb ergänzen.

Revision ID: 0006
Revises: 0005
"""

from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "cameras",
        sa.Column(
            "recognition_requested",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    op.add_column(
        "model_versions",
        sa.Column("managed", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    # Bestehende Pausen und aktive Modelle erhalten. Neue gemeinsame Kandidaten
    # werden aus den aktuellen menschlichen Vorgaben aufgebaut.
    if op.get_bind().dialect.name == "sqlite":
        # Ältere SQLite-ADD-COLUMN-Defaults können als Text vorliegen.
        for name in ("analysis_enabled", "learning_enabled", "learning_dirty"):
            op.execute(
                sa.text(
                    f"UPDATE cameras SET {name} = CASE WHEN {name} IN (1,'1','true') THEN 1 ELSE 0 END"
                )
            )
    op.execute(sa.text("UPDATE cameras SET recognition_requested = analysis_enabled"))
    op.execute(
        sa.text(
            "UPDATE cameras SET learning_dirty = true WHERE learning_enabled = true AND roi_json IS NOT NULL"
        )
    )


def downgrade():
    if op.get_bind().scalar(
        sa.text("SELECT COUNT(*) FROM model_versions WHERE managed = true")
    ):
        raise RuntimeError(
            "Downgrade gesperrt: gemeinsame Lernstände vorhanden. Für einen Rückwechsel die Datenbank-/ML-Sicherung wiederherstellen."
        )
    op.drop_column("model_versions", "managed")
    op.drop_column("cameras", "recognition_requested")
