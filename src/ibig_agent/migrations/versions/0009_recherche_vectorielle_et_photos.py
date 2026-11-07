"""Recherche par le sens (pgvector) et photos générées pour les publications.

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-30 16:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def _pgvector_available(bind) -> bool:
    if bind.dialect.name != "postgresql":
        return False
    return bool(bind.execute(sa.text(
        "SELECT 1 FROM pg_available_extensions WHERE name = 'vector'")).scalar())


def upgrade() -> None:
    op.create_table(
        "kb_embeddings",
        sa.Column("id", sa.String(length=300), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=40), nullable=False),
        sa.Column("embedding", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    bind = op.get_bind()
    if _pgvector_available(bind):
        # Image Docker pgvector/pgvector : recherche vectorielle dans la base
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
        op.execute("ALTER TABLE kb_embeddings ADD COLUMN vec vector(1024)")
    op.create_table(
        "media_assets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("pending_id", sa.Integer(), nullable=False),
        sa.Column("mime", sa.String(length=40), nullable=False, server_default="image/jpeg"),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_by", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_media_assets_pending_id", "media_assets", ["pending_id"])


def downgrade() -> None:
    op.drop_index("ix_media_assets_pending_id", "media_assets")
    op.drop_table("media_assets")
    op.drop_table("kb_embeddings")
