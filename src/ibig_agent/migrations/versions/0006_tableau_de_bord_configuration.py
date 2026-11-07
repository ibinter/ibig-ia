"""Configuration depuis le tableau de bord : base de connaissances, boîtes mail, consignes.

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-30 03:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "knowledge_edits",
        sa.Column("path", sa.String(length=300), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("updated_by", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("path"),
    )
    op.create_table(
        "mailbox_accounts",
        sa.Column("adresse", sa.String(length=300), nullable=False),
        sa.Column("hebergeur", sa.String(length=20), nullable=False),
        sa.Column("pole", sa.String(length=40), nullable=False),
        sa.Column("responsable", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("signature", sa.Text(), nullable=False, server_default=""),
        sa.Column("imap_host", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("imap_port", sa.Integer(), nullable=False, server_default="993"),
        sa.Column("smtp_host", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("smtp_port", sa.Integer(), nullable=False, server_default="465"),
        sa.Column("secret_enc", sa.Text(), nullable=False, server_default=""),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("updated_by", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("adresse"),
    )
    op.create_table(
        "directives",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("pole", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by", sa.String(length=300), nullable=False, server_default=""),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("directives")
    op.drop_table("mailbox_accounts")
    op.drop_table("knowledge_edits")
