"""Numéros WhatsApp Business et comptes sociaux ajoutés depuis le tableau de bord.

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-30 17:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "whatsapp_accounts",
        sa.Column("phone_number_id", sa.String(length=64), nullable=False),
        sa.Column("nom", sa.String(length=200), nullable=False),
        sa.Column("numero", sa.String(length=32), nullable=False),
        sa.Column("pole", sa.String(length=40), nullable=False),
        sa.Column("token_enc", sa.Text(), nullable=False, server_default=""),
        sa.Column("updated_by", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("phone_number_id"),
    )
    op.create_table(
        "social_account_rows",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("reseau", sa.String(length=40), nullable=False),
        sa.Column("compte", sa.String(length=200), nullable=False),
        sa.Column("pole", sa.String(length=40), nullable=False),
        sa.Column("publication_auto", sa.Boolean(), nullable=False,
                  server_default=sa.false()),
        sa.Column("created_by", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("reseau", "compte"),
    )


def downgrade() -> None:
    op.drop_table("social_account_rows")
    op.drop_table("whatsapp_accounts")
