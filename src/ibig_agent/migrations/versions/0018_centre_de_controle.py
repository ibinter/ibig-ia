"""Centre de contrôle de l'écosystème IBIG : actifs surveillés, historique des sondes, incidents.

Migration ADDITIVE : trois nouvelles tables, aucune table ni colonne existante n'est modifiée.
Réversible.

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-01 10:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "eco_assets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("url", sa.String(length=500), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False, server_default="site"),
        sa.Column("hosting", sa.String(length=20), nullable=False, server_default="vps"),
        sa.Column("pole", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("criticality", sa.String(length=10), nullable=False, server_default="moyenne"),
        sa.Column("health_path", sa.String(length=200), nullable=False, server_default="/"),
        sa.Column("ok_codes", sa.String(length=20), nullable=False, server_default="200-399"),
        sa.Column("expect_text", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("source", sa.String(length=30), nullable=False, server_default="manuel"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_eco_assets_url", "eco_assets", ["url"], unique=True)

    op.create_table(
        "eco_checks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("asset_id", sa.Integer(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("status", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cert_days", sa.Integer(), nullable=True),
        sa.Column("detail", sa.String(length=300), nullable=False, server_default=""),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_eco_checks_asset_id", "eco_checks", ["asset_id"])
    op.create_index("ix_eco_checks_at", "eco_checks", ["at"])

    op.create_table(
        "eco_incidents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("asset_id", sa.Integer(), nullable=True),
        sa.Column("key", sa.String(length=200), nullable=False),
        sa.Column("kind", sa.String(length=30), nullable=False, server_default=""),
        sa.Column("severity", sa.String(length=10), nullable=False, server_default="warning"),
        sa.Column("title", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("detail", sa.Text(), nullable=False, server_default=""),
        sa.Column("suggestion", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=10), nullable=False, server_default="open"),
        sa.Column("count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ack_by", sa.String(length=300), nullable=False, server_default=""),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_eco_incidents_asset_id", "eco_incidents", ["asset_id"])
    op.create_index("ix_eco_incidents_key", "eco_incidents", ["key"], unique=True)
    op.create_index("ix_eco_incidents_status", "eco_incidents", ["status"])
    op.create_index("ix_eco_incidents_last_seen", "eco_incidents", ["last_seen"])


def downgrade() -> None:
    for name in ("ix_eco_incidents_last_seen", "ix_eco_incidents_status", "ix_eco_incidents_key",
                 "ix_eco_incidents_asset_id"):
        op.drop_index(name, table_name="eco_incidents")
    op.drop_table("eco_incidents")
    op.drop_index("ix_eco_checks_at", table_name="eco_checks")
    op.drop_index("ix_eco_checks_asset_id", table_name="eco_checks")
    op.drop_table("eco_checks")
    op.drop_index("ix_eco_assets_url", table_name="eco_assets")
    op.drop_table("eco_assets")
