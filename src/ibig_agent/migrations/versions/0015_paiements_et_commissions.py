"""Lot 5 : paiements, taux et commissions.

Migration ADDITIVE (cahier des charges, 12.1) : trois nouvelles tables, aucune table ni colonne
existante n'est modifiée. Réversible.

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-30 16:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "payments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("quote_id", sa.Integer(), nullable=False),
        sa.Column("prospect_id", sa.Integer(), nullable=False),
        sa.Column("pole", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("amount", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(length=8), nullable=False, server_default="FCFA"),
        sa.Column("method", sa.String(length=20), nullable=False, server_default=""),
        sa.Column("reference", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("proof", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="a_confirmer"),
        sa.Column("recorded_by", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_by", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reject_reason", sa.String(length=300), nullable=False, server_default=""),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("method", "reference"),
    )
    op.create_index("ix_payments_quote_id", "payments", ["quote_id"])
    op.create_index("ix_payments_prospect_id", "payments", ["prospect_id"])
    op.create_index("ix_payments_pole", "payments", ["pole"])
    op.create_index("ix_payments_status", "payments", ["status"])
    op.create_table(
        "commission_rates",
        sa.Column("pole", sa.String(length=40), nullable=False),
        sa.Column("rate_bp", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_by", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("pole"),
    )
    op.create_table(
        "commissions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("payment_id", sa.Integer(), nullable=False),
        sa.Column("quote_id", sa.Integer(), nullable=False),
        sa.Column("pole", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("beneficiary", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("base", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rate_bp", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("amount", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="a_valider"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("validated_by", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paid_by", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_commissions_payment_id", "commissions", ["payment_id"], unique=True)
    op.create_index("ix_commissions_quote_id", "commissions", ["quote_id"])
    op.create_index("ix_commissions_status", "commissions", ["status"])


def downgrade() -> None:
    op.drop_index("ix_commissions_status", table_name="commissions")
    op.drop_index("ix_commissions_quote_id", table_name="commissions")
    op.drop_index("ix_commissions_payment_id", table_name="commissions")
    op.drop_table("commissions")
    op.drop_table("commission_rates")
    op.drop_index("ix_payments_status", table_name="payments")
    op.drop_index("ix_payments_pole", table_name="payments")
    op.drop_index("ix_payments_prospect_id", table_name="payments")
    op.drop_index("ix_payments_quote_id", table_name="payments")
    op.drop_table("payments")
