"""Licences importées automatiquement des logiciels : origine et identifiant d'origine.

Migration ADDITIVE : deux colonnes avec valeur par défaut ; les lignes existantes ne changent pas. Réversible.

Revision ID: 0022
Revises: 0021
Create Date: 2026-11-05 19:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("licenses", sa.Column("source", sa.String(length=30), nullable=False, server_default="manuel"))
    op.add_column("licenses", sa.Column("external_id", sa.String(length=120), nullable=False, server_default=""))
    op.create_index("ix_licenses_source_ext", "licenses", ["source", "external_id"])


def downgrade() -> None:
    op.drop_index("ix_licenses_source_ext", table_name="licenses")
    op.drop_column("licenses", "external_id")
    op.drop_column("licenses", "source")
