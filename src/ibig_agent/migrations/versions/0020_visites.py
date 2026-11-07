"""Suivi des visites des sites du groupe (audience centralisée).

Migration ADDITIVE : une nouvelle table, rien d'existant n'est modifié. Réversible.

Revision ID: 0020
Revises: 0019
Create Date: 2026-11-05 15:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "visits",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("site", sa.String(length=120), nullable=False),
        sa.Column("path", sa.String(length=300), nullable=False, server_default="/"),
        sa.Column("event", sa.String(length=20), nullable=False, server_default="pageview"),
        sa.Column("label", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("ref", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("visitor", sa.String(length=16), nullable=False, server_default=""),
        sa.Column("device", sa.String(length=10), nullable=False, server_default=""),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_visits_site_at", "visits", ["site", "at"])


def downgrade() -> None:
    op.drop_index("ix_visits_site_at", table_name="visits")
    op.drop_table("visits")
