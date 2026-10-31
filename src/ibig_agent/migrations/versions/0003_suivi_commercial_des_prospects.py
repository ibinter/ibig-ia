"""Suivi commercial des prospects : qualification, relances, essais et démonstrations.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-29 21:19:12
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

DATES = ("qualified_at", "last_inbound_at", "last_outbound_at", "trial_ends_at",
         "trial_reminded_at", "demo_at", "demo_reminded_at")


def upgrade() -> None:
    # server_default : les colonnes NOT NULL doivent pouvoir être ajoutées à une table remplie.
    with op.batch_alter_table("prospects", schema=None) as batch_op:
        batch_op.add_column(sa.Column("score", sa.Integer(), nullable=False,
                                      server_default=sa.text("0")))
        batch_op.add_column(sa.Column("temperature", sa.String(length=10), nullable=False,
                                      server_default=sa.text("''")))
        batch_op.add_column(sa.Column("solution", sa.String(length=200), nullable=False,
                                      server_default=sa.text("''")))
        batch_op.add_column(sa.Column("next_step", sa.Text(), nullable=False,
                                      server_default=sa.text("''")))
        batch_op.add_column(sa.Column("followups_sent", sa.Integer(), nullable=False,
                                      server_default=sa.text("0")))
        batch_op.add_column(sa.Column("stop_followups", sa.Boolean(), nullable=False,
                                      server_default=sa.false()))
        for name in DATES:
            batch_op.add_column(sa.Column(name, sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("prospects", schema=None) as batch_op:
        for name in (*DATES, "stop_followups", "followups_sent", "next_step", "solution",
                     "temperature", "score"):
            batch_op.drop_column(name)
