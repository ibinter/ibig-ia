"""Publication centrale d'articles : articles et traces de publication par site.

Migration ADDITIVE : deux nouvelles tables, rien d'existant n'est modifié. Réversible.

Revision ID: 0023
Revises: 0022
Create Date: 2026-11-05 20:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "articles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("slug", sa.String(length=200), nullable=False),
        sa.Column("excerpt", sa.Text(), nullable=False, server_default=""),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("category", sa.String(length=100), nullable=False, server_default=""),
        sa.Column("cover_image", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("seo_title", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("seo_description", sa.String(length=400), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=10), nullable=False, server_default="brouillon"),
        sa.Column("created_by", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_articles_slug", "articles", ["slug"])
    op.create_table(
        "article_publications",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("article_id", sa.Integer(), nullable=False),
        sa.Column("target", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False, server_default="ok"),
        sa.Column("external_id", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("url", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("error", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("by", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["article_id"], ["articles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_article_publications_article_id", "article_publications", ["article_id"])


def downgrade() -> None:
    op.drop_index("ix_article_publications_article_id", table_name="article_publications")
    op.drop_table("article_publications")
    op.drop_index("ix_articles_slug", table_name="articles")
    op.drop_table("articles")
