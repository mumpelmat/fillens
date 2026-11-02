"""Manuellen Worker-Durchlauf dauerhaft anfordern.

Revision ID: 0005
Revises: 0004
"""

from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    table = op.create_table(
        "worker_control",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("requested", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("claimed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("running", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("requested_at", sa.DateTime(timezone=True)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    op.bulk_insert(
        table,
        [
            {
                "id": "importer",
                "requested": 0,
                "claimed": 0,
                "completed": 0,
                "running": False,
            }
        ],
    )


def downgrade():
    op.drop_table("worker_control")
