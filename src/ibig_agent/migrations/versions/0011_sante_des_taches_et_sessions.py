"""Santé des tâches planifiées ; sessions invalidées après un changement de mot de passe.

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-01 09:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("sessions_valid_after", sa.DateTime(timezone=True),
                                     nullable=True))
    op.create_table(
        "job_status",
        sa.Column("job_id", sa.String(length=60), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("last_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_ok", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=False, server_default=""),
        sa.Column("last_result", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("running", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("runs", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("alerted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.PrimaryKeyConstraint("job_id"),
    )


def downgrade() -> None:
    op.drop_table("job_status")
    op.drop_column("users", "sessions_valid_after")
