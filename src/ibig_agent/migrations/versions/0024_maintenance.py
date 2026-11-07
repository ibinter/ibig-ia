"""Console de maintenance : demandes d'actions (redémarrer, sauvegarder…) exécutées sur le serveur.

Migration ADDITIVE : une nouvelle table, rien d'existant n'est modifié. Réversible.

Revision ID: 0024
Revises: 0023
Create Date: 2026-11-05 22:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "maintenance_actions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=30), nullable=False),
        sa.Column("target", sa.String(length=60), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False, server_default="demandee"),
        sa.Column("requested_by", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("confirmed_by", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("output", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("done_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_maintenance_actions_status", "maintenance_actions", ["status"])


def downgrade() -> None:
    op.drop_index("ix_maintenance_actions_status", table_name="maintenance_actions")
    op.drop_table("maintenance_actions")
