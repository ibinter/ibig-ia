"""Lot 1 : catalogue validé (statut, version, historique des prix) et fiche prospect enrichie.

Migration ADDITIVE (cahier des charges, 12.1) : aucune table ni colonne existante n'est
supprimée ou renommée ; les nouvelles colonnes ont une valeur par défaut. Réversible.

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-30 10:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Fiche prospect : origine et affectation
    op.add_column("prospects", sa.Column("source_url", sa.Text(), nullable=False,
                                         server_default=""))
    op.add_column("prospects", sa.Column("collected_at", sa.DateTime(timezone=True),
                                         nullable=True))
    op.add_column("prospects", sa.Column("verification", sa.String(length=20), nullable=False,
                                         server_default="non_verifie"))
    op.add_column("prospects", sa.Column("assigned_agent", sa.String(length=60), nullable=False,
                                         server_default=""))
    op.add_column("prospects", sa.Column("assigned_to", sa.String(length=300), nullable=False,
                                         server_default=""))

    op.create_table(
        "prospect_activities",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("prospect_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False, server_default="note"),
        sa.Column("summary", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("by", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_prospect_activities_prospect_id", "prospect_activities", ["prospect_id"])
    op.create_index("ix_prospect_activities_at", "prospect_activities", ["at"])

    op.create_table(
        "catalog_offers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=200), nullable=False),
        sa.Column("brand", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("pole", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("name", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("kind", sa.String(length=30), nullable=False, server_default=""),
        sa.Column("category", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("price", sa.Integer(), nullable=True),
        sa.Column("prices", sa.JSON(), nullable=False),
        sa.Column("source_prices", sa.JSON(), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False, server_default="FCFA"),
        sa.Column("url", sa.Text(), nullable=False, server_default=""),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("source", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="actif"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("validated_by", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_catalog_offers_code", "catalog_offers", ["code"], unique=True)
    op.create_index("ix_catalog_offers_pole", "catalog_offers", ["pole"])
    op.create_index("ix_catalog_offers_status", "catalog_offers", ["status"])

    op.create_table(
        "catalog_price_history",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("offer_id", sa.Integer(), nullable=False),
        sa.Column("price", sa.Integer(), nullable=True),
        sa.Column("prices", sa.JSON(), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("changed_by", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("reason", sa.String(length=200), nullable=False, server_default=""),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_catalog_price_history_offer_id", "catalog_price_history", ["offer_id"])


def downgrade() -> None:
    op.drop_table("catalog_price_history")
    op.drop_table("catalog_offers")
    op.drop_table("prospect_activities")
    for col in ("assigned_to", "assigned_agent", "verification", "collected_at", "source_url"):
        op.drop_column("prospects", col)
