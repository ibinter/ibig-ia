"""Résumé du tri des mails traités (questions réelles pour l'agent Contenus web).

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-29 21:04:07
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # server_default : la colonne NOT NULL doit pouvoir être ajoutée à une table non vide.
    with op.batch_alter_table("processed_messages", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("summary", sa.Text(), nullable=False, server_default=sa.text("''"))
        )


def downgrade() -> None:
    with op.batch_alter_table("processed_messages", schema=None) as batch_op:
        batch_op.drop_column("summary")
