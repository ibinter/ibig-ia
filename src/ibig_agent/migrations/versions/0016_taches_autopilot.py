"""Phase 1 (cahier des charges v2) : tâches d'une mission, avec dépendances et preuves.

Migration ADDITIVE : une nouvelle table, aucune table ni colonne existante n'est modifiée.
Réversible.

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-31 10:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mission_tasks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("mission_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False, server_default=""),
        sa.Column("agent", sa.String(length=60), nullable=False, server_default=""),
        sa.Column("depends_on", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="a_faire"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("idempotency_key", sa.String(length=120), nullable=False),
        sa.Column("evidence", sa.Text(), nullable=False, server_default=""),
        sa.Column("result", sa.Text(), nullable=False, server_default=""),
        sa.Column("cost_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_by", sa.String(length=300), nullable=False, server_default=""),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_mission_tasks_mission_id", "mission_tasks", ["mission_id"])
    op.create_index("ix_mission_tasks_status", "mission_tasks", ["status"])
    op.create_index("ix_mission_tasks_idempotency_key", "mission_tasks", ["idempotency_key"],
                    unique=True)


def downgrade() -> None:
    op.drop_index("ix_mission_tasks_idempotency_key", table_name="mission_tasks")
    op.drop_index("ix_mission_tasks_status", table_name="mission_tasks")
    op.drop_index("ix_mission_tasks_mission_id", table_name="mission_tasks")
    op.drop_table("mission_tasks")
