"""Licences et abonnements des clients (administration générale du groupe).

Migration ADDITIVE : une nouvelle table, rien d'existant n'est modifié. Réversible.

Revision ID: 0021
Revises: 0020
Create Date: 2026-11-05 17:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "licenses",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("client", sa.String(length=200), nullable=False),
        sa.Column("contact", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("product", sa.String(length=200), nullable=False),
        sa.Column("plan", sa.String(length=10), nullable=False, server_default="annuel"),
        sa.Column("amount_fcfa", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("starts_on", sa.Date(), nullable=False),
        sa.Column("ends_on", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=12), nullable=False, server_default="active"),
        sa.Column("site", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_licenses_client", "licenses", ["client"])
    op.create_index("ix_licenses_product", "licenses", ["product"])
    op.create_index("ix_licenses_ends_on", "licenses", ["ends_on"])


def downgrade() -> None:
    op.drop_index("ix_licenses_ends_on", table_name="licenses")
    op.drop_index("ix_licenses_product", table_name="licenses")
    op.drop_index("ix_licenses_client", table_name="licenses")
    op.drop_table("licenses")
