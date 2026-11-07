"""Lot 2 : missions, journal des missions, suspension et quotas des agents.

Migration ADDITIVE (cahier des charges, 12.1) : trois nouvelles tables, aucune table ni colonne
existante n'est modifiée. Réversible.

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-30 12:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_controls",
        sa.Column("agent", sa.String(length=60), nullable=False),
        sa.Column("suspended", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("reason", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("updated_by", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("runs_cap_day", sa.Integer(), nullable=True),
        sa.Column("cost_cap_day", sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint("agent"),
    )
    op.create_table(
        "missions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("kind", sa.String(length=30), nullable=False, server_default="prospection"),
        sa.Column("pole", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("product", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("zone", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("target", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("objective", sa.Text(), nullable=False, server_default=""),
        sa.Column("criteria", sa.Text(), nullable=False, server_default=""),
        sa.Column("agent", sa.String(length=60), nullable=False, server_default="prospecteur"),
        sa.Column("channel", sa.String(length=60), nullable=False, server_default=""),
        sa.Column("autonomy", sa.String(length=30), nullable=False, server_default="validation"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="brouillon"),
        sa.Column("budget_usd", sa.Float(), nullable=True),
        sa.Column("max_per_day", sa.Integer(), nullable=True),
        sa.Column("max_total", sa.Integer(), nullable=True),
        sa.Column("error_threshold", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("spent_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("runs", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_result", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("pause_reason", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("created_by", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("simulated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("launched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_missions_status", "missions", ["status"])
    op.create_table(
        "mission_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("mission_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False, server_default="etat"),
        sa.Column("summary", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("by", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_mission_events_mission_id", "mission_events", ["mission_id"])
    op.create_index("ix_mission_events_at", "mission_events", ["at"])


def downgrade() -> None:
    op.drop_table("mission_events")
    op.drop_table("missions")
    op.drop_table("agent_controls")
