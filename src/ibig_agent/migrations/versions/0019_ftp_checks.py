"""Contrôles de contenu FTP : stockage des identifiants chiffrés et résultats des vérifications.

Migration ADDITIVE : deux nouvelles tables, rien d'existant n'est modifié.
Réversible.

Revision ID: 0019
Revises: 0018
Create Date: 2026-11-01 12:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "eco_ftp_creds",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(length=100), nullable=False),
        sa.Column("host", sa.String(length=200), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False, server_default="21"),
        sa.Column("mode", sa.String(length=10), nullable=False, server_default="ftps"),
        sa.Column("login", sa.String(length=200), nullable=False),
        sa.Column("password_enc", sa.Text(), nullable=False),
        sa.Column("root_path", sa.String(length=300), nullable=False, server_default="/"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sa.String(length=300), nullable=False, server_default=""),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_eco_ftp_creds_label", "eco_ftp_creds", ["label"], unique=True)

    op.create_table(
        "eco_ftp_results",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("cred_id", sa.Integer(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("folders", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("files", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("quota_used_mb", sa.Integer(), nullable=True),
        sa.Column("quota_total_mb", sa.Integer(), nullable=True),
        sa.Column("detail", sa.Text(), nullable=False, server_default=""),
        sa.ForeignKeyConstraint(["cred_id"], ["eco_ftp_creds.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_eco_ftp_results_cred_at", "eco_ftp_results", ["cred_id", "at"])


def downgrade() -> None:
    op.drop_index("ix_eco_ftp_results_cred_at", table_name="eco_ftp_results")
    op.drop_table("eco_ftp_results")
    op.drop_index("ix_eco_ftp_creds_label", table_name="eco_ftp_creds")
    op.drop_table("eco_ftp_creds")
