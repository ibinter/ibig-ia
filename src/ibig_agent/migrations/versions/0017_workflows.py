"""Phase 5 (cahier des charges v2, 7.2) : définitions de workflows, versionnées et approuvées.

Migration ADDITIVE : une nouvelle table, aucune table ni colonne existante n'est modifiée.
Le budget par mission réutilise les colonnes déjà présentes (`missions.budget_usd`, `spent_usd`).
Réversible.

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-31 21:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workflow_defs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=60), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("trigger", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("pole", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("steps", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="brouillon"),
        sa.Column("created_by", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_by", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", "version", name="uq_workflow_code_version"),
    )
    op.create_index("ix_workflow_defs_code", "workflow_defs", ["code"])
    op.create_index("ix_workflow_defs_status", "workflow_defs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_workflow_defs_status", table_name="workflow_defs")
    op.drop_index("ix_workflow_defs_code", table_name="workflow_defs")
    op.drop_table("workflow_defs")
