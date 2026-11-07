"""Publications programmées, veille des commentaires et numéro WhatsApp des comptes.

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-30 14:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scheduled_posts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("pending_id", sa.Integer(), nullable=False),
        sa.Column("reseau", sa.String(length=40), nullable=False),
        sa.Column("compte", sa.String(length=200), nullable=False),
        sa.Column("pole", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("texte", sa.Text(), nullable=False),
        sa.Column("titre", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("publish_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="programme"),
        sa.Column("result", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_scheduled_posts_pending_id", "scheduled_posts", ["pending_id"])
    op.create_index("ix_scheduled_posts_publish_at", "scheduled_posts", ["publish_at"])
    op.create_table(
        "social_comments",
        sa.Column("id", sa.String(length=120), nullable=False),
        sa.Column("reseau", sa.String(length=40), nullable=False),
        sa.Column("compte", sa.String(length=200), nullable=False),
        sa.Column("pole", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("post_id", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("author", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("text", sa.Text(), nullable=False, server_default=""),
        sa.Column("sentiment", sa.String(length=20), nullable=False, server_default="neutre"),
        sa.Column("summary", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("posted_at", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_social_comments_seen_at", "social_comments", ["seen_at"])
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("phone", sa.String(length=32), nullable=False,
                                   server_default=""))


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_column("phone")
    op.drop_index("ix_social_comments_seen_at", "social_comments")
    op.drop_table("social_comments")
    op.drop_index("ix_scheduled_posts_publish_at", "scheduled_posts")
    op.drop_index("ix_scheduled_posts_pending_id", "scheduled_posts")
    op.drop_table("scheduled_posts")
