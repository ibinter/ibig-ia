"""Lot 4 : devis et lignes de devis.

Migration ADDITIVE (cahier des charges, 12.1) : deux nouvelles tables, aucune table ni colonne
existante n'est modifiée. Réversible.

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-30 15:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "quotes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("number", sa.String(length=30), nullable=False),
        sa.Column("prospect_id", sa.Integer(), nullable=False),
        sa.Column("pole", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="brouillon"),
        sa.Column("currency", sa.String(length=8), nullable=False, server_default="FCFA"),
        sa.Column("total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_by", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("validated_by", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_quotes_number", "quotes", ["number"], unique=True)
    op.create_index("ix_quotes_prospect_id", "quotes", ["prospect_id"])
    op.create_index("ix_quotes_status", "quotes", ["status"])
    op.create_table(
        "quote_lines",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("quote_id", sa.Integer(), nullable=False),
        sa.Column("offer_code", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("label", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("qty", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("unit_price", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("price_source", sa.String(length=20), nullable=False,
                  server_default="catalogue"),
        sa.Column("total", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_quote_lines_quote_id", "quote_lines", ["quote_id"])


def downgrade() -> None:
    op.drop_index("ix_quote_lines_quote_id", table_name="quote_lines")
    op.drop_table("quote_lines")
    op.drop_index("ix_quotes_status", table_name="quotes")
    op.drop_index("ix_quotes_prospect_id", table_name="quotes")
    op.drop_index("ix_quotes_number", table_name="quotes")
    op.drop_table("quotes")
